"""Versioned migrations and failure classification for index.db (Task 2.2)."""

import os
import shutil
import sqlite3
import sys

import pytest

from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage import migrations
from doc_searcher.storage.database import Database
from doc_searcher.storage.errors import (
    DatabaseCorruptError,
    DatabaseLockedError,
    DatabaseReadOnlyError,
    FTS5UnavailableError,
    MigrationError,
    SchemaTooNewError,
    StorageError,
)
from fixtures.legacy_data import LEGACY_DOCUMENTS, write_legacy_db


def snapshot(path):
    conn = sqlite3.connect(str(path))
    try:
        return {
            "documents": conn.execute(
                "SELECT path, filename, file_type, file_size, mtime FROM documents ORDER BY path"
            ).fetchall(),
            "segments": conn.execute(
                "SELECT d.path, s.segment_id, s.content FROM doc_segments s "
                "JOIN documents d ON d.id = s.doc_id ORDER BY d.path, s.id"
            ).fetchall(),
            "fts": conn.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0],
            "version": conn.execute("PRAGMA user_version").fetchone()[0],
        }
    finally:
        conn.close()


@pytest.mark.parametrize("with_ctime", [True, False], ids=["v1.2.0", "pre-ctime"])
def test_legacy_database_upgrades_once_without_losing_data(tmp_path, with_ctime):
    path = tmp_path / "index.db"
    write_legacy_db(path, with_ctime=with_ctime)
    before = snapshot(path)
    assert before["version"] == 0

    db = Database(str(path))
    assert db.applied_migrations == [1]
    results = DocumentSearcher(db).search("legacy")
    db.close()

    after = snapshot(path)
    assert after["version"] == migrations.latest_version()
    assert after["documents"] == before["documents"]
    assert after["segments"] == before["segments"]
    assert after["fts"] == before["fts"] == sum(len(doc[4]) for doc in LEGACY_DOCUMENTS)
    assert {r.path for r in results} == {doc[0] for doc in LEGACY_DOCUMENTS}

    conn = sqlite3.connect(str(path))
    ctimes = dict(conn.execute("SELECT path, ctime FROM documents").fetchall())
    conn.close()
    for doc_path, _, _, mtime, _ in LEGACY_DOCUMENTS:
        assert ctimes[doc_path] == mtime  # synthetic paths do not exist, so mtime is the fallback

    assert snapshot(migrations.backup_path(str(path))) == before

    reopened = Database(str(path))
    assert reopened.applied_migrations == []
    reopened.close()


def test_new_database_is_created_at_latest_version_without_backup(tmp_path):
    path = tmp_path / "index.db"
    db = Database(str(path))
    db.close()
    assert snapshot(path)["version"] == migrations.latest_version()
    assert not os.path.exists(migrations.backup_path(str(path)))


def test_failed_migration_rolls_back_and_leaves_database_usable(tmp_path, monkeypatch):
    path = tmp_path / "index.db"
    Database(str(path)).close()
    before = snapshot(path)

    def broken(conn):
        conn.execute("ALTER TABLE documents ADD COLUMN extra TEXT")
        conn.execute("UPDATE documents SET extra = 'x'")
        raise RuntimeError("simulated bug in migration 2")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS, (2, "broken", broken)])
    with pytest.raises(MigrationError, match="rolled back") as exc:
        Database(str(path))
    assert "schema 1" in str(exc.value)

    assert snapshot(path) == before
    conn = sqlite3.connect(str(path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    conn.close()
    assert "extra" not in columns

    monkeypatch.undo()
    db = Database(str(path))
    assert db.applied_migrations == []
    db.close()


def test_database_from_newer_build_is_refused_unchanged(tmp_path):
    path = tmp_path / "index.db"
    Database(str(path)).close()
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA user_version = 99")
    conn.close()

    with pytest.raises(SchemaTooNewError, match="newer DocSearcher"):
        Database(str(path))
    assert snapshot(path)["version"] == 99


def test_corrupt_file_is_reported_and_left_in_place(tmp_path):
    path = tmp_path / "index.db"
    garbage = b"this is not an sqlite database" * 100
    path.write_bytes(garbage)

    with pytest.raises(DatabaseCorruptError, match="damaged"):
        Database(str(path))
    assert path.read_bytes() == garbage


def test_locked_database_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "index.db"
    write_legacy_db(path)  # needs a migration, which requires the write lock
    holder = sqlite3.connect(str(path), isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(sqlite3, "connect", _fast_timeout(sqlite3.connect))
    try:
        with pytest.raises(DatabaseLockedError, match="locked"):
            Database(str(path))
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert snapshot(path)["version"] == 0


def _fast_timeout(connect):
    def wrapper(database, timeout=5.0, **kwargs):
        conn = connect(database, timeout=0.1, **kwargs)
        return _NoBusyWait(conn)

    return wrapper


class _NoBusyWait:
    """Proxy that keeps Database's busy_timeout PRAGMA from re-extending the wait."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args):
        if sql.lower().startswith("pragma busy_timeout"):
            sql = "PRAGMA busy_timeout=100;"
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name == "_conn":
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as non-root"
)
def test_read_only_database_is_reported(tmp_path):
    folder = tmp_path / "ro"
    folder.mkdir()
    path = folder / "index.db"
    write_legacy_db(path)
    os.chmod(path, 0o444)
    os.chmod(folder, 0o555)
    try:
        with pytest.raises(DatabaseReadOnlyError, match="not writable"):
            Database(str(path))
    finally:
        os.chmod(folder, 0o755)
        os.chmod(path, 0o644)
    assert snapshot(path)["version"] == 0


def test_missing_fts5_is_reported(tmp_path, monkeypatch):
    real_connect = sqlite3.connect

    class NoFts5:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def execute(self, sql, *args):
            if "using fts5" in sql.lower():
                raise sqlite3.OperationalError("no such module: fts5")
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            setattr(self._conn, name, value)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: NoFts5(real_connect(*a, **k)))
    with pytest.raises(FTS5UnavailableError, match="FTS5"):
        Database(str(tmp_path / "index.db"))


def test_error_categories_are_distinct():
    kinds = [
        DatabaseCorruptError,
        DatabaseLockedError,
        DatabaseReadOnlyError,
        FTS5UnavailableError,
        MigrationError,
        SchemaTooNewError,
    ]
    assert all(issubclass(kind, StorageError) for kind in kinds)
    assert len(set(kinds)) == len(kinds)


def test_programming_errors_are_not_disguised(tmp_path, monkeypatch):
    monkeypatch.setattr("doc_searcher.storage.database.migrate", lambda conn, path: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        Database(str(tmp_path / "index.db"))


def test_backup_is_a_consistent_copy(tmp_path):
    path = tmp_path / "index.db"
    write_legacy_db(path)
    Database(str(path)).close()
    copy = tmp_path / "copy.db"
    shutil.copy(migrations.backup_path(str(path)), copy)
    conn = sqlite3.connect(str(copy))
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
