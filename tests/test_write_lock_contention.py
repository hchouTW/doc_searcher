# Purpose: Guards the "database is locked" fixes: short write transactions and a clear message.
# What the code does:
#   - Checks CJK tokenizing runs before the write transaction opens, and CJK search still works.
#   - Checks the busy timeout and that IndexWorker reports a classified lock message.

import sqlite3

from doc_searcher.desktop.worker import IndexWorker
from doc_searcher.storage import database as database_module
from doc_searcher.storage.database import BUSY_TIMEOUT_SECONDS, Database


def _segments(n=3):
    return [
        {"segment_id": i, "segment_type": "paragraph", "content": f"檢索系統 {i}", "sources": []}
        for i in range(n)
    ]


def test_cjk_tokenizing_happens_before_write_transaction(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "index.db"))
    states = []
    real = database_module.cjk_tokens

    def spy(text):
        states.append(db.get_connection().in_transaction)
        return real(text)

    monkeypatch.setattr(database_module, "cjk_tokens", spy)
    db.save_document_index("/x/a.txt", "txt", 1, 1.0, _segments())
    assert states == [False] * 3


def test_cjk_index_rows_match_segments_on_reindex(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    db.save_document_index("/x/a.txt", "txt", 1, 1.0, _segments(3))
    db.save_document_index("/x/a.txt", "txt", 1, 2.0, _segments(2))
    conn = db.get_connection()
    seg_ids = [r[0] for r in conn.execute("SELECT id FROM doc_segments ORDER BY id")]
    cjk_ids = [r[0] for r in conn.execute("SELECT rowid FROM doc_cjk_fts ORDER BY rowid")]
    assert len(seg_ids) == 2 and cjk_ids == seg_ids


def test_busy_timeout_waits_for_other_writers(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    assert db.get_connection().execute("PRAGMA busy_timeout").fetchone()[0] == int(
        BUSY_TIMEOUT_SECONDS * 1000
    )


def test_index_worker_reports_classified_lock_message(tmp_path):
    db = Database(str(tmp_path / "index.db"))
    worker = IndexWorker(db, [str(tmp_path)])

    def boom():
        raise sqlite3.OperationalError("database is locked")

    worker._run_indexing = boom
    messages = []
    worker.status_changed.connect(messages.append)
    worker.run()
    assert messages and "locked by another program" in messages[0]


def test_concurrent_writers_to_one_path_do_not_race(tmp_path):
    """Two processes (modelled by two Database objects) may save the same new path at once."""
    import threading

    path = str(tmp_path / "index.db")
    first, second = Database(path), Database(path)
    errors = []

    def writer(db):
        try:
            for n in range(40):
                db.save_document_index(f"/x/doc{n}.txt", "txt", 1, 1.0, _segments(2))
        except Exception as exc:  # noqa: BLE001 - the test reports whatever escaped
            errors.append(repr(exc))
        finally:
            db.close()

    threads = [threading.Thread(target=writer, args=(db,)) for db in (first, second)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert Database(path).get_stats()["total_docs"] == 40
