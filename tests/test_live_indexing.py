"""Real filesystem tests for targeted updates, watcher latency, and stale extraction."""

import time
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.storage.database import Database
from doc_searcher.search.searcher import DocumentSearcher


def wait_until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    assert predicate(), "index change was not visible within five seconds"


def test_targeted_updates_respect_scope_exclusions_and_unavailable_roots(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "a.txt"
    path.write_text("會議")
    outside = tmp_path / "outside.txt"
    outside.write_text("會議")
    excluded = root / "ignored.txt"
    excluded.write_text("會議")
    db = Database(str(tmp_path / "index.db"))
    service = IndexingService(db)
    request = IndexRequest([str(root)], exclude_patterns=["ignored.txt"])
    result = service.update_paths(request, [str(path), str(outside), str(excluded)])
    assert result["indexed"] == 1
    assert len(DocumentSearcher(db).search("會議")) == 1
    unavailable = tmp_path / "offline"
    root.rename(unavailable)
    service.update_paths(request, [str(path)])
    assert db.get_document_by_path(str(path)) is not None
    unavailable.rename(root)
    path.unlink()
    assert service.update_paths(request, [str(path)])["deleted"] == 1
    db.close()


def test_watcher_indexes_create_replace_move_and_delete_within_five_seconds(tmp_path):
    from doc_searcher.indexing.watcher import FolderWatcher

    root = tmp_path / "docs"
    root.mkdir()
    db = Database(str(tmp_path / "index.db"))
    watcher = FolderWatcher(db, lambda: IndexRequest([str(root)]), debounce=0.1)
    watcher.start()
    try:
        path = root / "first.txt"
        started = time.monotonic()
        path.write_text("會議 initial")
        wait_until(lambda: bool(DocumentSearcher(db).search("會議")))
        assert time.monotonic() - started < 5
        replacement = root / "replacement.tmp"
        replacement.write_text("updated keyword")
        replacement.replace(path)
        wait_until(lambda: bool(DocumentSearcher(db).search("updated")))
        assert not DocumentSearcher(db).search("會議")
        moved = root / "moved.txt"
        path.rename(moved)
        wait_until(
            lambda: (
                db.get_document_by_path(str(moved)) is not None
                and db.get_document_by_path(str(path)) is None
            )
        )
        moved.unlink()
        wait_until(lambda: not DocumentSearcher(db).search("updated"))
        assert watcher.status()["errors"] == []
    finally:
        watcher.stop()
        db.close()


def test_file_changed_during_parse_does_not_commit_stale_text(tmp_path, monkeypatch):
    import doc_searcher.indexing.indexer as module
    from doc_searcher.parsers.base import ExtractedDoc, PageSegment

    path = tmp_path / "race.txt"
    path.write_text("before")
    db = Database(str(tmp_path / "index.db"))

    def changed(file_path):
        path.write_text("after much longer")
        return ExtractedDoc(str(path), "txt", 1, [PageSegment("1", "section", "before")])

    monkeypatch.setattr(module, "parse_file", changed)
    result = IndexingService(db).update_paths(IndexRequest([str(tmp_path)]), [str(path)])
    assert result["retry_paths"] == [str(path)]
    assert db.get_document_by_path(str(path)) is None
    db.close()


def test_obsolete_folder_request_cannot_repopulate_cleared_index(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    path = root / "document.txt"
    path.write_text("obsolete")
    db = Database(str(tmp_path / "index.db"))
    service = IndexingService(db)
    service.indexer.request_is_current = lambda: False
    result = service.update_paths(IndexRequest([str(root)]), [str(path)])
    assert result["indexed"] == 0 and db.get_document_by_path(str(path)) is None
    db.close()


def test_targeted_updates_do_not_index_hidden_parent_directories(tmp_path):
    root = tmp_path / "docs"
    hidden = root / ".private"
    hidden.mkdir(parents=True)
    path = hidden / "secret.txt"
    path.write_text("secret marker")
    db = Database(str(tmp_path / "index.db"))
    result = IndexingService(db).update_paths(IndexRequest([str(root)]), [str(path)])
    assert result["indexed"] == 0 and not DocumentSearcher(db).search("secret")
    db.close()


def test_replacing_owned_roots_prunes_old_documents_but_preserves_other_owners(tmp_path):
    from doc_searcher.indexing.watcher import FolderWatcher

    roots = [tmp_path / name for name in ("a", "b", "other")]
    for root in roots:
        root.mkdir()
        (root / "file.txt").write_text(root.name + "marker")
    db = Database(str(tmp_path / "index.db"))
    IndexingService(db).run(IndexRequest([str(roots[2])], scope=[str(roots[2])]))
    request = IndexRequest([str(roots[0])])
    watcher = FolderWatcher(db, lambda: request, debounce=0.05, reconcile_seconds=0.1)
    watcher.start()
    try:
        wait_until(lambda: bool(DocumentSearcher(db).search("amarker")))
        request = IndexRequest([str(roots[1])])
        wait_until(lambda: bool(DocumentSearcher(db).search("bmarker")))
        wait_until(lambda: not DocumentSearcher(db).search("amarker"))
        assert DocumentSearcher(db).search("othermarker")
    finally:
        watcher.stop()
        db.close()
