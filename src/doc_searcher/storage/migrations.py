# Purpose: Ordered, versioned schema migrations for index.db.
# What the code does:
#   - MIGRATIONS lists (version, name, function) in ascending order; PRAGMA user_version stores
#     the last applied version (v1.2.0 databases have user_version 0).
#   - migrate() applies each pending migration in its own BEGIN IMMEDIATE transaction together
#     with the user_version bump, so a failure rolls back to the previous, usable version.
#   - Before upgrading an existing database it writes a one-generation backup
#     (<index.db>.pre-migration.bak) with SQLite's online backup API.
#   - Refuses databases whose version is newer than this build knows (no silent downgrade).
# Usage notes, dependencies, or assumptions:
#   - Called by doc_searcher.storage.database.Database.init_db on every open.
#   - Every migration must be idempotent (CREATE ... IF NOT EXISTS, column checks) because
#     version-0 databases may already contain part of the schema.

import os
import sqlite3
from typing import Callable, List, Tuple

from doc_searcher.storage.errors import MigrationError, SchemaTooNewError, classify


def _v1_baseline(conn: sqlite3.Connection) -> None:
    """The v1.2.0 schema, including the ctime column that older builds added in place."""
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
        for doc_id, path, mtime in conn.execute("SELECT id, path, mtime FROM documents").fetchall():
            try:
                stat = os.stat(path)
                creation_time = getattr(stat, "st_birthtime", stat.st_ctime)
            except OSError:
                creation_time = mtime
            conn.execute("UPDATE documents SET ctime = ? WHERE id = ?", (creation_time, doc_id))

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


Migration = Tuple[int, str, Callable[[sqlite3.Connection], None]]

MIGRATIONS: List[Migration] = [
    (1, "v1.2.0 baseline schema with ctime", _v1_baseline),
]


def latest_version() -> int:
    return MIGRATIONS[-1][0]


def backup_path(db_path: str) -> str:
    return db_path + ".pre-migration.bak"


def _has_user_tables(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
    ).fetchone() is not None


def migrate(conn: sqlite3.Connection, db_path: str) -> List[int]:
    """Apply pending migrations; returns the versions applied (empty when up to date)."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    newest = latest_version()
    if current > newest:
        raise SchemaTooNewError(
            f"{db_path} was created by a newer DocSearcher (schema {current}, this build "
            f"supports {newest}). Update DocSearcher, or point it at a different data folder."
        )
    pending = [m for m in MIGRATIONS if m[0] > current]
    if not pending:
        return []

    if _has_user_tables(conn):
        backup = sqlite3.connect(backup_path(db_path))
        try:
            conn.backup(backup)
        finally:
            backup.close()

    applied: List[int] = []
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # explicit transaction control, including DDL
    try:
        for version, name, apply in pending:
            conn.execute("BEGIN IMMEDIATE")  # lock errors here are classified by the caller
            try:
                apply(conn)
                conn.execute(f"PRAGMA user_version = {int(version)}")
                conn.execute("COMMIT")
            except Exception as exc:
                conn.execute("ROLLBACK")
                environmental = classify(exc, db_path)
                if environmental is not None:
                    raise environmental from exc
                kept = applied[-1] if applied else current
                raise MigrationError(
                    f"Upgrading {db_path} to schema {version} ({name}) failed and was rolled "
                    f"back; the index is still usable at schema {kept}. Cause: {exc}"
                ) from exc
            applied.append(version)
    finally:
        conn.isolation_level = previous_isolation
    return applied
