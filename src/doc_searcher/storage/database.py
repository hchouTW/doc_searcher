# Purpose: SQLite database connection and FTS5 full-text search schema management.
# What the code does:
#   - Opens index.db, checks FTS5, and applies versioned migrations (storage.migrations).
#   - Manages per-thread connections, transactions, index updates, deletions, and querying.
# Usage notes, dependencies, or assumptions:
#   - Requires sqlite3 with FTS5; environmental failures raise storage.errors.StorageError.
#   - Enables foreign keys and WAL mode for high concurrency.

import os
import sqlite3
import threading
import time
from typing import Optional, Dict, Any, List, Tuple

from doc_searcher.storage.errors import classify
from doc_searcher.storage.migrations import migrate


def _enable_wal(conn: sqlite3.Connection, timeout: float = 5.0) -> None:
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
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=5000;")
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
        """Retrieve document metadata by absolute path."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT id, path, filename, file_type, file_size, mtime, ctime, total_segments, error FROM documents WHERE path = ?",
            (os.path.abspath(file_path),),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_document_segments(self, doc_id: int) -> List[Dict[str, Any]]:
        """Return a document's extracted segments in their original order."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT segment_id, segment_type, content FROM doc_segments WHERE doc_id = ? ORDER BY id",
            (doc_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_all_indexed_paths(self) -> Dict[str, Tuple[float, int]]:
        """Return dict of {path: (mtime, file_size)} for incremental scanning."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT path, mtime, file_size FROM documents")
        return {row["path"]: (row["mtime"], row["file_size"]) for row in cursor.fetchall()}

    def get_stats(self) -> Dict[str, Any]:
        """Return overall database index statistics."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT COUNT(*), SUM(file_size) FROM documents WHERE error IS NULL")
        doc_count, total_size = cursor.fetchone()
        cursor = conn.execute("SELECT COUNT(*) FROM doc_segments")
        seg_count = cursor.fetchone()[0]
        cursor = conn.execute("SELECT MAX(indexed_at) FROM documents")
        last_indexed_at = cursor.fetchone()[0]
        return {
            "total_docs": doc_count or 0,
            "total_size": total_size or 0,
            "total_segments": seg_count or 0,
            "db_size": os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
            "last_indexed_at": last_indexed_at,
        }

    def delete_document(self, file_path: str):
        """Remove a document, its segments, and its FTS entries."""
        abs_path = os.path.abspath(file_path)
        conn = self.get_connection()
        with conn:
            cursor = conn.execute("SELECT id FROM documents WHERE path = ?", (abs_path,))
            row = cursor.fetchone()
            if row:
                doc_id = row["id"]
                conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (str(doc_id),))
                conn.execute("DELETE FROM doc_segments WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def save_document_index(
        self,
        file_path: str,
        file_type: str,
        file_size: int,
        mtime: float,
        segments: List[Dict[str, str]],
        error: Optional[str] = None,
        ctime: Optional[float] = None,
    ) -> int:
        """Insert or replace document and index its segments."""
        abs_path = os.path.abspath(file_path)
        filename = os.path.basename(abs_path)
        now = time.time()
        creation_time = mtime if ctime is None else ctime
        conn = self.get_connection()

        with conn:
            # Check existing
            cursor = conn.execute("SELECT id FROM documents WHERE path = ?", (abs_path,))
            existing = cursor.fetchone()
            if existing:
                doc_id = existing["id"]
                # Clean up previous segments and FTS entries
                conn.execute("DELETE FROM doc_fts WHERE doc_id = ?", (str(doc_id),))
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

            # Insert segments and FTS entries
            if segments and not error:
                seg_rows = []
                fts_rows = []
                for seg in segments:
                    seg_id = str(seg["segment_id"])
                    seg_type = str(seg["segment_type"])
                    content = seg["content"]
                    tokenized = seg.get("tokenized_content", "")

                    seg_rows.append((doc_id, seg_id, seg_type, content))
                    fts_rows.append((str(doc_id), seg_id, seg_type, content, tokenized))

                conn.executemany(
                    """
                    INSERT INTO doc_segments (doc_id, segment_id, segment_type, content)
                    VALUES (?, ?, ?, ?)
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

        return doc_id
