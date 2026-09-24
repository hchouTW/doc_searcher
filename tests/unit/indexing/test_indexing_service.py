"""IndexingService decisions, controls, and write serialization (Task 4.1)."""

import os
import threading
import time

import pytest

from doc_searcher.indexing import service as service_module
from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.storage.database import Database
from fixtures.platform import RUNNING_AS_ROOT


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "index.db"))
    yield database
    database.close()


@pytest.fixture
def roots(tmp_path):
    made = []
    for name in ("a", "b"):
        root = tmp_path / name
        (root / "sub").mkdir(parents=True)
        (root / f"{name}1.txt").write_text(f"{name} one", encoding="utf-8")
        (root / "sub" / f"{name}2.txt").write_text(f"{name} two", encoding="utf-8")
        made.append(root)
    return made


def files_under(root):
    return {str(p) for p in root.rglob("*.txt")}


def indexed(db):
    return set(db.get_all_indexed_paths())


def test_full_scope_prunes_removed_roots(db, roots):
    a, b = roots
    IndexingService(db).run(IndexRequest(roots=[str(a), str(b)]))
    assert indexed(db) == files_under(a) | files_under(b)

    stats = IndexingService(db).run(IndexRequest(roots=[str(a)]))
    assert indexed(db) == files_under(a)
    assert stats["deleted"] == 2 and stats["skipped"] is False


def test_explicit_scope_leaves_other_roots_alone(db, roots):
    a, b = roots
    IndexingService(db).run(IndexRequest(roots=[str(a), str(b)]))
    (a / "a1.txt").unlink()

    stats = IndexingService(db).run(IndexRequest(roots=[str(a)], scope=[str(a)]))

    assert indexed(db) == files_under(a) | files_under(b)
    assert stats["deleted"] == 1


def test_subtree_scope_only_reconciles_the_subtree(db, roots):
    a, _ = roots
    IndexingService(db).run(IndexRequest(roots=[str(a)]))
    (a / "a1.txt").unlink()
    (a / "sub" / "a2.txt").unlink()

    IndexingService(db).run(IndexRequest(roots=[str(a / "sub")], scope=[str(a / "sub")]))

    assert indexed(db) == {str(a / "a1.txt")}  # outside the subtree: kept until a full rescan


def test_unavailable_root_is_preserved(db, roots, tmp_path):
    a, b = roots
    IndexingService(db).run(IndexRequest(roots=[str(a), str(b)]))
    b.rename(tmp_path / "b_offline")

    stats = IndexingService(db).run(IndexRequest(roots=[str(a), str(b)]))

    assert {p for p in indexed(db) if "/b/" in p.replace("\\", "/")} == {
        str(b / "b1.txt"),
        str(b / "sub" / "b2.txt"),
    }
    assert stats["unavailable_directories"] == [str(b)]
    assert stats["skipped"] is False


def test_all_roots_unavailable_keeps_them_but_prunes_removed_roots(db, roots, tmp_path):
    a, b = roots
    IndexingService(db).run(IndexRequest(roots=[str(a), str(b)]))
    a.rename(tmp_path / "a_offline")

    stats = IndexingService(db).run(IndexRequest(roots=[str(a)]))  # b was removed from config

    assert indexed(db) == {str(a / "a1.txt"), str(a / "sub" / "a2.txt")}
    assert stats["skipped"] is True and stats["deleted"] == 2 and stats["indexed"] == 0


@pytest.mark.posix
@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root ignores file permissions")
def test_unreadable_subfolder_is_not_treated_as_deleted(db, roots):
    a, _ = roots
    IndexingService(db).run(IndexRequest(roots=[str(a)]))
    (a / "sub").chmod(0)
    try:
        stats = IndexingService(db).run(IndexRequest(roots=[str(a)]))
    finally:
        (a / "sub").chmod(0o755)
    assert indexed(db) == files_under(a)
    assert stats["scan_error_paths"] == [str(a / "sub")]


def test_phases_are_reported(db, roots):
    a, _ = roots
    phases = []
    IndexingService(db).run(IndexRequest(roots=[str(a)]), on_phase=phases.append)
    assert phases == ["scanning", "indexing", "completed"]

    phases.clear()
    IndexingService(db).run(IndexRequest(roots=[str(a)]), on_phase=phases.append)
    assert phases == ["scanning", "completed"]  # nothing changed


def test_cancel_before_run_changes_nothing(db, roots):
    service = IndexingService(db)
    service.cancel()
    phases = []
    stats = service.run(IndexRequest(roots=[str(r) for r in roots]), on_phase=phases.append)
    assert stats["cancelled"] is True
    assert indexed(db) == set()
    assert phases == ["cancelled"]


def test_cancel_during_indexing_stops_after_current_file(db, roots):
    service = IndexingService(db)
    progress = []

    def on_progress(current, total, name):
        progress.append(current)
        service.cancel()

    stats = service.run(IndexRequest(roots=[str(r) for r in roots]), on_progress=on_progress)
    assert stats["cancelled"] is True
    assert stats["indexed"] == 1 and len(indexed(db)) == 1


def test_pause_blocks_until_resume(db, roots):
    service = IndexingService(db)
    service.pause()
    result = {}
    worker = threading.Thread(
        target=lambda: result.update(service.run(IndexRequest(roots=[str(roots[0])])))
    )
    worker.start()
    time.sleep(0.3)
    assert worker.is_alive() and service.is_paused
    service.resume()
    worker.join(10)
    assert result["indexed"] == 2


def test_clear_removes_everything(db, roots):
    IndexingService(db).run(IndexRequest(roots=[str(r) for r in roots]))
    stats = IndexingService(db).clear()
    assert stats["deleted"] == 4 and indexed(db) == set()


def test_runs_against_one_database_never_write_concurrently(tmp_path, roots, monkeypatch):
    path = str(tmp_path / "shared.db")
    active, overlaps = [], []
    real_batch = DocumentIndexer.run_batch_indexing

    def tracked_batch(self, *args, **kwargs):
        active.append(1)
        if len(active) > 1:
            overlaps.append(True)
        time.sleep(0.2)
        try:
            return real_batch(self, *args, **kwargs)
        finally:
            active.pop()

    monkeypatch.setattr(DocumentIndexer, "run_batch_indexing", tracked_batch)

    errors = []

    def run(root):
        try:
            db = Database(path)  # both threads also race to create the new database
            try:
                IndexingService(db).run(IndexRequest(roots=[str(root)], scope=[str(root)]))
            finally:
                db.close()
        except Exception as exc:  # surfaced below; a thread exception would otherwise be lost
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(root,)) for root in roots]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert overlaps == []
    check = Database(path)
    assert indexed(check) == files_under(roots[0]) | files_under(roots[1])
    check.close()
    lock = service_module._write_lock(os.path.abspath(path))
    assert lock.acquire(blocking=False)  # released after both runs
    lock.release()
