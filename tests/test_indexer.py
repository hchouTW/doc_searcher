# Purpose: Unit tests for FileScanner, Database, and DocumentIndexer.
# What the code does:
#   - Verifies scanning of sample directories and exclusion of temporary files.
#   - Verifies indexing of multiple formats into SQLite FTS5.
#   - Verifies incremental indexing and deletion detection.
# Usage notes, dependencies, or assumptions:
#   - Uses temporary SQLite database.
#   - Run via pytest.

import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import Database
from core.config import AppConfig
from core.scanner import FileScanner
from core.indexer import DocumentIndexer
from ui.worker import IndexWorker

SAMPLE_DIR = Path(__file__).parent / "sample_files"


def test_file_scanner():
    scanner = FileScanner()
    
    # Test exclusions
    assert scanner.is_valid_document_file("~$contract.docx") is False
    assert scanner.is_valid_document_file(".DS_Store") is False
    assert scanner.is_valid_document_file("image.png") is False
    assert scanner.is_valid_document_file("report.pdf") is True
    assert scanner.is_valid_document_file("data.xlsx") is True

    # Test scanning directory
    files = scanner.scan_directories([str(SAMPLE_DIR)])
    assert len(files) >= 5
    file_paths = [f[0] for f in files]
    assert any("sample_report.pdf" in p for p in file_paths)
    assert any("sample_contract.docx" in p for p in file_paths)


def test_file_scanner_can_exclude_subdirectories(tmp_path):
    root_file = tmp_path / "root.txt"
    root_file.write_text("root", encoding="utf-8")
    nested_dir = tmp_path / "nested" / "deeper"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "nested.txt"
    nested_file.write_text("nested", encoding="utf-8")

    scanner = FileScanner()
    recursive_paths = {
        path for path, _, _ in scanner.scan_directories([str(tmp_path)])
    }
    top_level_paths = {
        path
        for path, _, _ in scanner.scan_directories(
            [str(tmp_path)], include_subdirectories=False
        )
    }

    assert recursive_paths == {str(root_file), str(nested_file)}
    assert top_level_paths == {str(root_file)}


def test_scanner_exclusion_patterns_prune_folders_and_files(tmp_path):
    kept = tmp_path / "reports" / "keep.txt"
    ignored_folder = tmp_path / "node_modules" / "ignore.txt"
    ignored_file = tmp_path / "reports" / "draft.tmp.txt"
    kept.parent.mkdir()
    ignored_folder.parent.mkdir()
    for path in (kept, ignored_folder, ignored_file):
        path.write_text("sample", encoding="utf-8")

    found = FileScanner().scan_directories(
        [str(tmp_path)], exclude_patterns=["node_modules", "draft*"]
    )
    assert {path for path, _, _ in found} == {str(kept)}


def test_scanner_exclusions_only_apply_below_scanned_root(tmp_path):
    root = tmp_path / "temp" / "docs"
    files = [root / "keep.txt", root / "temp" / "skip.txt", root / "private" / "skip.txt"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sample", encoding="utf-8")

    absolute = (root / "private").as_posix() + "/*"
    found = FileScanner().scan_directories([str(root)], exclude_patterns=["temp", absolute])
    assert {path for path, _, _ in found} == {str(root / "keep.txt")}


def test_legacy_database_migration_populates_creation_time(tmp_path):
    document = tmp_path / "existing.txt"
    document.write_text("existing", encoding="utf-8")
    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.execute("""
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL, file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL, mtime REAL NOT NULL,
            indexed_at REAL NOT NULL, total_segments INTEGER DEFAULT 0,
            error TEXT
        )
    """)
    connection.execute(
        "INSERT INTO documents (path, filename, file_type, file_size, mtime, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(document), document.name, "txt", 8, 1.0, 1.0),
    )
    connection.commit()
    connection.close()

    db = Database(str(db_path))
    assert db.get_document_by_path(str(document))["ctime"] > 1.0
    db.close()


def test_file_scanner_deduplicates_overlapping_roots(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()

    scanner = FileScanner()
    normalized = scanner.normalize_directories([
        str(nested),
        str(tmp_path),
        str(nested),
    ])

    assert normalized == [str(tmp_path)]


def test_unavailable_directory_indexes_are_preserved(tmp_path):
    available = tmp_path / "available"
    unavailable = tmp_path / "offline"
    removed = tmp_path / "removed"
    available.mkdir()
    current_file = available / "current.txt"
    current_file.write_text("current", encoding="utf-8")

    scanner = FileScanner()
    current_files = scanner.scan_directories([str(available)])
    indexed_files = {
        str(current_file): (current_file.stat().st_mtime, current_file.stat().st_size),
        str(unavailable / "keep.txt"): (1.0, 1),
        str(removed / "delete.txt"): (1.0, 1),
    }

    to_index, to_delete = scanner.calculate_changes(
        current_files,
        indexed_files,
        preserved_directories=[str(unavailable)],
    )

    assert to_index == []
    assert to_delete == [str(removed / "delete.txt")]


def test_scan_reports_inaccessible_subdirectories(monkeypatch, tmp_path):
    inaccessible = tmp_path / "restricted"

    def fake_walk(root, followlinks, onerror):
        error = PermissionError("denied")
        error.filename = str(inaccessible)
        onerror(error)
        return iter([(str(tmp_path), [], [])])

    monkeypatch.setattr(os, "walk", fake_walk)
    scan_errors = []

    FileScanner().scan_directories(
        [str(tmp_path)],
        error_callback=scan_errors.append,
    )

    assert scan_errors == [str(inaccessible)]


def test_recursive_scan_avoids_symbolic_link_cycles(tmp_path):
    root_file = tmp_path / "root.txt"
    root_file.write_text("root", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    try:
        (nested / "back-to-root").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        import pytest
        pytest.skip("Symbolic links are not available on this platform")

    files = FileScanner().scan_directories([str(tmp_path)])

    assert [path for path, _, _ in files] == [str(root_file)]


def test_batch_progress_includes_deletions_and_additions(tmp_path):
    db = Database(str(tmp_path / "progress.db"))
    indexer = DocumentIndexer(db)
    progress = []
    indexer.index_single_file = lambda path: True

    stats = indexer.run_batch_indexing(
        ["new.txt"],
        ["old.txt"],
        progress_callback=lambda current, total, name: progress.append(
            (current, total, name)
        ),
    )

    assert progress == [
        (1, 2, "移除索引：old.txt"),
        (2, 2, "new.txt"),
    ]
    assert stats["deleted"] == 1
    assert stats["indexed"] == 1
    db.close()


def test_index_worker_preserves_indexes_when_all_roots_are_unavailable(tmp_path):
    db = Database(str(tmp_path / "offline_worker.db"))
    indexed_path = str(tmp_path / "offline" / "preserved.txt")
    db.save_document_index(
        file_path=indexed_path,
        file_type="txt",
        file_size=1,
        mtime=1.0,
        segments=[],
    )

    worker = IndexWorker(db, [str(tmp_path / "offline")])
    worker._run_indexing()

    assert db.get_document_by_path(indexed_path) is not None
    db.close()


def test_include_subdirectories_config_defaults_to_enabled_and_persists(tmp_path):
    config_path = tmp_path / "config.json"
    config = AppConfig(config_path)

    assert config.include_subdirectories is True

    config.include_subdirectories = False
    reloaded_config = AppConfig(config_path)

    assert reloaded_config.include_subdirectories is False


def test_indexer_can_pause_and_resume_at_safe_checkpoint(tmp_path):
    db = Database(str(tmp_path / "pause_test.db"))
    indexer = DocumentIndexer(db)
    checkpoint_reached = threading.Event()
    checkpoint_finished = threading.Event()

    indexer.pause()

    def wait_at_checkpoint():
        checkpoint_reached.set()
        indexer.wait_until_ready()
        checkpoint_finished.set()

    waiter = threading.Thread(target=wait_at_checkpoint)
    waiter.start()
    assert checkpoint_reached.wait(timeout=1)
    assert checkpoint_finished.wait(timeout=0.05) is False

    indexer.resume()
    assert checkpoint_finished.wait(timeout=1)
    waiter.join(timeout=1)

    assert indexer.is_paused is False
    db.close()


def test_cancelling_indexer_releases_paused_checkpoint(tmp_path):
    db = Database(str(tmp_path / "cancel_test.db"))
    indexer = DocumentIndexer(db)
    checkpoint_result = []

    indexer.pause()
    waiter = threading.Thread(
        target=lambda: checkpoint_result.append(indexer.wait_until_ready())
    )
    waiter.start()
    indexer.cancel()
    waiter.join(timeout=1)

    assert checkpoint_result == [False]
    assert waiter.is_alive() is False
    db.close()


def test_cancelled_worker_does_not_restart_during_batch_handoff(tmp_path):
    db = Database(str(tmp_path / "handoff_test.db"))
    indexer = DocumentIndexer(db)
    processed_files = []
    indexer.index_single_file = lambda path: processed_files.append(path) or True

    indexer.cancel()
    stats = indexer.run_batch_indexing(
        ["should-not-run.txt"],
        [],
        reset_cancellation=False,
    )

    assert processed_files == []
    assert stats["indexed"] == 0
    assert stats["cancelled"] is True
    db.close()


def test_database_uses_a_separate_connection_per_thread(tmp_path):
    db = Database(str(tmp_path / "thread_connections.db"))
    main_connection = db.get_connection()
    worker_connections = []

    def get_worker_connection():
        worker_connections.append(db.get_connection())
        db.close()

    worker = threading.Thread(target=get_worker_connection)
    worker.start()
    worker.join(timeout=2)

    assert worker.is_alive() is False
    assert len(worker_connections) == 1
    assert worker_connections[0] is not main_connection
    db.close()


def test_database_and_indexer():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_index.db")
        db = Database(db_path)
        indexer = DocumentIndexer(db)

        scanner = FileScanner()
        files = scanner.scan_directories([str(SAMPLE_DIR)])
        file_paths = [f[0] for f in files]

        # Initial indexing
        stats = indexer.run_batch_indexing(file_paths, [])
        assert stats["indexed"] >= 5
        assert stats["failed"] == 0

        # Verify database stats
        db_stats = db.get_stats()
        assert db_stats["total_docs"] >= 5
        assert db_stats["total_segments"] >= 5

        # Verify incremental change calculation
        current_indexed = db.get_all_indexed_paths()
        to_index, to_delete = scanner.calculate_changes(files, current_indexed)
        # Nothing changed, so to_index and to_delete should both be empty
        assert len(to_index) == 0
        assert len(to_delete) == 0

        # Simulate a deletion
        del_target = file_paths[0]
        indexer.run_batch_indexing([], [del_target])
        assert db.get_document_by_path(del_target) is None
        assert db.get_stats()["total_docs"] == db_stats["total_docs"] - 1

        db.close()
