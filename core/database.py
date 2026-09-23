# Purpose: SQLite database connection and FTS5 full-text search schema management.
# What the code does:
#   - Initializes and migrates document metadata (including creation time), segments, and FTS.
#   - Manages per-thread connections, transactions, index updates, deletions, and querying.
# Usage notes, dependencies, or assumptions:
#   - Requires sqlite3 with FTS5 enabled (standard in Python 3.8+).
#   - Enables foreign keys and WAL mode for high concurrency.

import os
import sqlite3
import threading
import time
from typing import Optional, Dict, Any, List, Tuple


class Database:
    """SQLite3 database manager with FTS5 full-text indexing."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._thread_state = threading.local()
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Get or create connection with WAL mode enabled."""
        conn = getattr(self._thread_state, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000;")
            # Enable WAL mode for smooth concurrent reads and writes
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._thread_state.connection = conn
        return conn

    def close(self):
        """Close database connection."""
        conn = getattr(self._thread_state, "connection", None)
        if conn is not None:
            conn.close()
            self._thread_state.connection = None

    def init_db(self):
        """Initialize database tables and FTS5 virtual table."""
        conn = self.get_connection()
        with conn:
            # 1. Documents metadata table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    ctime REAL NOT NULL DEFAULT 0,
                    indexed_at REAL NOT NULL,
                    total_segments INTEGER DEFAULT 0,
                    error TEXT
                );
            """)

            columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
            if "ctime" not in columns:
                conn.execute("ALTER TABLE documents ADD COLUMN ctime REAL NOT NULL DEFAULT 0")
                for row in conn.execute("SELECT id, path, mtime FROM documents").fetchall():
                    try:
                        stat = os.stat(row["path"])
                        creation_time = getattr(stat, "st_birthtime", stat.st_ctime)
                    except OSError:
                        creation_time = row["mtime"]
                    conn.execute(
                        "UPDATE documents SET ctime = ? WHERE id = ?",
                        (creation_time, row["id"]),
                    )

            # 2. Document segments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS doc_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id INTEGER NOT NULL,
                    segment_id TEXT NOT NULL,
                    segment_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
                );
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_segments_doc_id ON doc_segments(doc_id);")

            # 3. FTS5 Virtual Table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
                    doc_id UNINDEXED,
                    segment_id UNINDEXED,
                    segment_type UNINDEXED,
                    content,
                    tokenized_content,
                    tokenize='unicode61'
                );
            """)

    def get_document_by_path(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Retrieve document metadata by absolute path."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT id, path, filename, file_type, file_size, mtime, ctime, total_segments, error FROM documents WHERE path = ?",
            (os.path.abspath(file_path),)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

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
                conn.execute("""
                    UPDATE documents
                    SET filename = ?, file_type = ?, file_size = ?, mtime = ?, ctime = ?, indexed_at = ?, total_segments = ?, error = ?
                    WHERE id = ?
                """, (filename, file_type, file_size, mtime, creation_time, now, len(segments), error, doc_id))
            else:
                cursor = conn.execute("""
                    INSERT INTO documents (path, filename, file_type, file_size, mtime, ctime, indexed_at, total_segments, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (abs_path, filename, file_type, file_size, mtime, creation_time, now, len(segments), error))
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

                conn.executemany("""
                    INSERT INTO doc_segments (doc_id, segment_id, segment_type, content)
                    VALUES (?, ?, ?, ?)
                """, seg_rows)

                conn.executemany("""
                    INSERT INTO doc_fts (doc_id, segment_id, segment_type, content, tokenized_content)
                    VALUES (?, ?, ?, ?, ?)
                """, fts_rows)

        return doc_id
