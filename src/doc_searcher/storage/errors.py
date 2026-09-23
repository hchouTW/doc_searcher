# Purpose: Distinct, actionable exception types for index.db problems.
# What the code does:
#   - StorageError is the base; subclasses name the cause (locked, read-only, corrupt, FTS5
#     missing, schema too new, failed migration) and carry a message telling the user what to do.
#   - classify() maps a raw sqlite3 exception to one of these, or None for programming errors
#     that should propagate unchanged.
# Usage notes, dependencies, or assumptions:
#   - Callers catch StorageError at entry points (CLI exit code 3, GUI dialog, MCP tool error).
#   - DocSearcher never deletes or rebuilds an index on its own; the messages say how to.

import sqlite3
from typing import Optional


class StorageError(Exception):
    """index.db cannot be used; the message says why and what the user can do."""


class DatabaseLockedError(StorageError):
    pass


class DatabaseReadOnlyError(StorageError):
    pass


class DatabaseCorruptError(StorageError):
    pass


class FTS5UnavailableError(StorageError):
    pass


class SchemaTooNewError(StorageError):
    pass


class MigrationError(StorageError):
    pass


def classify(exc: BaseException, db_path: str) -> Optional[StorageError]:
    """Translate an environmental sqlite3 failure into a StorageError, else return None."""
    if isinstance(exc, StorageError):
        return exc
    if not isinstance(exc, sqlite3.Error):
        return None
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return DatabaseLockedError(
            f"{db_path} is locked by another program (another DocSearcher window, the MCP "
            f"server, or a backup tool). Close it and try again."
        )
    if "readonly" in message or "read-only" in message or "unable to open database file" in message:
        return DatabaseReadOnlyError(
            f"{db_path} or its folder is not writable. Fix the permissions, or set "
            f"DOC_SEARCHER_DATA_DIR to a writable folder."
        )
    if "not a database" in message or "malformed" in message or "corrupt" in message:
        return DatabaseCorruptError(
            f"{db_path} is damaged and cannot be read. Move it aside (keep it if you want to "
            f"attempt recovery); DocSearcher will build a new index on the next start."
        )
    if "no such module: fts5" in message:
        return FTS5UnavailableError(
            "This Python's SQLite library was built without FTS5 full-text search, which "
            "DocSearcher requires. Use an official python.org, Homebrew, or packaged "
            "DocSearcher build."
        )
    return None
