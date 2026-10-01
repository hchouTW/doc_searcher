# Purpose: SQLite database connection and FTS5 full-text search schema management.
# What the code does:
#   - Opens index.db, checks FTS5, and applies versioned migrations (storage.migrations).
#   - Manages per-thread connections, transactions, index updates, deletions, and querying.
#   - Clears all documents and both search indexes atomically with bulk deletions.
#   - Stores and looks up documents by canonical path (platform.paths).
# Usage notes, dependencies, or assumptions:
#   - Requires sqlite3 with FTS5; environmental failures raise storage.errors.StorageError.
#   - Enables foreign keys and WAL mode; waits up to BUSY_TIMEOUT_SECONDS for another writer.

import json
import os
import sqlite3
import threading
import time
from typing import Optional, Dict, Any, List, Tuple

from doc_searcher.search.cjk_index import cjk_tokens
from doc_searcher.storage.errors import classify
from doc_searcher.platform.paths import canonical_path
from doc_searcher.storage.migrations import migrate


def _path_spellings(file_path: str) -> Tuple[str, str]:
    """The path as given (how v1.2.0 stored it) and its canonical form (how it is stored now)."""
    return os.path.abspath(file_path), canonical_path(file_path)


BUSY_TIMEOUT_SECONDS = 30.0  # a large document's write transaction can outlast a few seconds


def _enable_wal(conn: sqlite3.Connection, timeout: float = BUSY_TIMEOUT_SECONDS) -> None:
    """Switch to WAL (persistent in the file) unless it is already on.

    The switch needs an exclusive lock and SQLite reports "database is locked" at once instead
    of honouring busy_timeout, so two processes opening a new index together would fail; retry
    for up to the busy timeout. Files already in WAL mode skip the switch entirely.
    """
    if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


class Database:
    """SQLite3 database manager with FTS5 full-text indexing."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._thread_state = threading.local()
        self.applied_migrations: List[int] = []
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Get or create connection with WAL mode enabled."""
        conn = getattr(self._thread_state, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=BUSY_TIMEOUT_SECONDS)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)};")
                _enable_wal(conn)
                conn.execute("PRAGMA foreign_keys=ON;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except BaseException:
                conn.close()  # e.g. a corrupt or locked file; otherwise the handle leaks
                raise
            self._thread_state.connection = conn
        return conn

    def close(self):
        """Close database connection."""
        conn = getattr(self._thread_state, "connection", None)
        if conn is not None:
            conn.close()
            self._thread_state.connection = None

    def init_db(self):
        """Open the database, verify FTS5, and apply pending schema migrations.

        Raises a StorageError subclass for locked, read-only, corrupt, too-new, or
        FTS5-less databases; programming errors propagate unchanged.
        """
        try:
            conn = self.get_connection()
            conn.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(x)")
            conn.execute("DROP TABLE temp._fts5_probe")
            self.applied_migrations = migrate(conn, self.db_path)
        except Exception as exc:
            self.close()
            storage_error = classify(exc, self.db_path)
            if storage_error is None or storage_error is exc:
                raise
            raise storage_error from exc

    def get_document_by_path(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Retrieve document metadata by path, in canonical or legacy (as-stored) spelling."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM documents WHERE path IN (?, ?) ORDER BY path = ? DESC",
            (*_path_spellings(file_path), canonical_path(file_path)),
        )
        row = cursor.fetchone()
        return self._quality_record(dict(row)) if row else None

    def get_document_segments(self, doc_id: int) -> List[Dict[str, Any]]:
        """Return a document's extracted segments in their original order."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT id, segment_id, segment_type, content, sources FROM doc_segments WHERE doc_id = ? ORDER BY id",
            (doc_id,),
        )
        return [{**dict(row), "sources": json.loads(row["sources"])} for row in cursor.fetchall()]

    def get_all_indexed_paths(self) -> Dict[str, Tuple[float, int]]:
        """Return dict of {path: (mtime, file_size)} for incremental scanning."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT path, mtime, file_size FROM documents")
        return {row["path"]: (row["mtime"], row["file_size"]) for row in cursor.fetchall()}

    def paths_with_warning(self, code: str) -> List[str]:
        """Paths whose stored extraction warnings include the given warning code."""
        rows = self.get_connection().execute(
            "SELECT path, warnings FROM documents WHERE warnings LIKE ?", (f'%"{code}"%',)
        )
        return [
            row["path"]
            for row in rows
            if any(warning.get("code") == code for warning in json.loads(row["warnings"]))
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Return overall database index statistics."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT COUNT(*), SUM(file_size) FROM documents WHERE error IS NULL")
        doc_count, total_size = cursor.fetchone()
        cursor = conn.execute("SELECT COUNT(*) FROM doc_segments")
        seg_count = cursor.fetchone()[0]
        cursor = conn.execute("SELECT MAX(indexed_at) FROM documents")
        last_indexed_at = cursor.fetchone()[0]
        discovered = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        searchable = conn.execute(
            "SELECT COUNT(*) FROM documents d WHERE EXISTS (SELECT 1 FROM doc_segments s WHERE s.doc_id=d.id AND LENGTH(TRIM(s.content))>0)"
        ).fetchone()[0]
        quality = dict(
            conn.execute(
                "SELECT parse_status, COUNT(*) FROM documents GROUP BY parse_status"
            ).fetchall()
        )
        return {
            "discovered_documents": discovered,
            "searchable_documents": searchable,
            "quality_counts": quality,
            "total_docs": doc_count or 0,
            "total_size": total_size or 0,
            "total_segments": seg_count or 0,
            "db_size": os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
            "last_indexed_at": last_indexed_at,
        }

    def delete_document(self, file_path: str):
        """Remove a document, its segments, and its FTS entries.

        The path must be spelled exactly as stored (reconciliation passes stored paths): a legacy
        spelling and the canonical one can both exist, and only the stale one may be removed.
        """
        conn = self.get_connection()
        with conn:
            cursor = conn.execute(
                "SELECT id FROM documents WHERE path = ?", (os.path.abspath(file_path),)
            )
            for row in cursor.fetchall():
                doc_id = row["id"]
                conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (str(doc_id),))
                conn.execute(
                    "DELETE FROM doc_cjk_fts WHERE rowid IN (SELECT id FROM doc_segments WHERE doc_id = ?)",
                    (doc_id,),
                )
                conn.execute("DELETE FROM doc_segments WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                conn.execute("UPDATE index_state SET revision = revision + 1 WHERE id = 1")

    def clear_index(self) -> int:
        """Atomically reset stored text and search indexes; return removed document count.

        Keep schema and ID sequences intact, and invalidate cursors/locations once.
        Unfiltered deletes avoid scanning FTS content separately for every document.
        """
        conn = self.get_connection()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if count:
                conn.execute("DELETE FROM doc_fts")
                conn.execute("DELETE FROM doc_cjk_fts")
                conn.execute("DELETE FROM doc_segments")
                conn.execute("DELETE FROM documents")
                conn.execute("UPDATE index_state SET revision = revision + 1 WHERE id = 1")
        return count

    def save_document_index(
        self,
        file_path: str,
        file_type: str,
        file_size: int,
        mtime: float,
        segments: List[Dict[str, Any]],
        error: Optional[str] = None,
        ctime: Optional[float] = None,
        parse_status: Optional[str] = None,
        warnings=None,
        omitted_locations=None,
        parser_version=None,
    ) -> int:
        """Insert or replace document and index its segments."""
        abs_path = canonical_path(file_path)
        filename = os.path.basename(abs_path)
        now = time.time()
        creation_time = mtime if ctime is None else ctime
        conn = self.get_connection()
        keeps_segments = bool(segments) and (not error or parse_status == "partial")
        # CJK tokenizing is CPU-heavy; do it before the write lock is taken.
        cjk_rows = [cjk_tokens(seg["content"]) for seg in segments] if keeps_segments else []

        with conn:
            # Check existing
            cursor = conn.execute("SELECT id FROM documents WHERE path = ?", (abs_path,))
            existing = cursor.fetchone()
            if existing:
                doc_id = existing["id"]
                # Clean up previous segments and FTS entries
                conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (str(doc_id),))
                conn.execute(
                    "DELETE FROM doc_cjk_fts WHERE rowid IN (SELECT id FROM doc_segments WHERE doc_id = ?)",
                    (doc_id,),
                )
                conn.execute("DELETE FROM doc_segments WHERE doc_id = ?", (doc_id,))
                conn.execute(
                    """
                    UPDATE documents
                    SET filename = ?, file_type = ?, file_size = ?, mtime = ?, ctime = ?, indexed_at = ?, total_segments = ?, error = ?
                    WHERE id = ?
                """,
                    (
                        filename,
                        file_type,
                        file_size,
                        mtime,
                        creation_time,
                        now,
                        len(segments),
                        error,
                        doc_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO documents (path, filename, file_type, file_size, mtime, ctime, indexed_at, total_segments, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        abs_path,
                        filename,
                        file_type,
                        file_size,
                        mtime,
                        creation_time,
                        now,
                        len(segments),
                        error,
                    ),
                )
                doc_id = cursor.lastrowid

            conn.execute(
                "UPDATE documents SET parse_status=?, warnings=?, omitted_locations=?, parser_version=? WHERE id=?",
                (
                    parse_status or "unknown",
                    json.dumps(warnings or [], ensure_ascii=False, default=str),
                    json.dumps(omitted_locations or [], ensure_ascii=False),
                    parser_version or "",
                    doc_id,
                ),
            )
            # Useful partial text remains searchable alongside quality diagnostics.
            if keeps_segments:
                seg_rows = []
                fts_rows = []
                for seg in segments:
                    seg_id = str(seg["segment_id"])
                    seg_type = str(seg["segment_type"])
                    content = seg["content"]
                    tokenized = seg.get("tokenized_content", "")

                    seg_rows.append(
                        (
                            doc_id,
                            seg_id,
                            seg_type,
                            content,
                            json.dumps(seg.get("sources", []), ensure_ascii=False, default=str),
                        )
                    )
                    fts_rows.append((str(doc_id), seg_id, seg_type, content, tokenized))

                conn.executemany(
                    """
                    INSERT INTO doc_segments (doc_id, segment_id, segment_type, content, sources)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    seg_rows,
                )

                conn.executemany(
                    """
                    INSERT INTO doc_fts (doc_id, segment_id, segment_type, content, tokenized_content)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    fts_rows,
                )

            # Segment rows were inserted in order, so ascending ids line up with cjk_rows.
            segment_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM doc_segments WHERE doc_id = ? ORDER BY id", (doc_id,)
                )
            ]
            conn.executemany(
                "INSERT INTO doc_cjk_fts(rowid, tokens) VALUES (?, ?)",
                zip(segment_ids, cjk_rows, strict=True),
            )
            conn.execute("UPDATE index_state SET revision = revision + 1 WHERE id = 1")
        return doc_id

    def revision(self) -> int:
        """Revision used to invalidate continuation and original-text locations."""
        return (
            self.get_connection()
            .execute("SELECT revision FROM index_state WHERE id = 1")
            .fetchone()[0]
        )

    @staticmethod
    def _quality_record(row):
        from doc_searcher.parsers.base import parser_version_for

        for key in ("warnings", "omitted_locations"):
            row[key] = json.loads(row[key])
        row["reprocess_eligible"] = row["parse_status"] == "unknown" or row[
            "parser_version"
        ] != parser_version_for(row["file_type"])
        return row

    def problem_documents(self, *, offset=0, limit=100):
        """Paginated unknown/no-text/partial/failed extraction records, including warnings."""
        if offset < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid problem-document page")
        rows = (
            self.get_connection()
            .execute(
                "SELECT * FROM documents WHERE parse_status != 'success' OR warnings != '[]' ORDER BY id LIMIT ? OFFSET ?",
                (limit + 1, offset),
            )
            .fetchall()
        )
        more = len(rows) > limit
        return dict(
            documents=[self._quality_record(dict(r)) for r in rows[:limit]],
            next_offset=offset + limit if more else None,
            has_more=more,
        )
