"""Directory scope and indexing-control cases (DIR-*, IDX-*) from docs/test-plan.md."""

import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest


RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
TREE_MARKERS = {"root": "QATREEroot", "sub": "QATREEsub", "deep": "QATREEdeep"}


def found(scratch, *keys):
    return {key for key in keys if scratch.names(TREE_MARKERS[key])}


def test_dir_01_subfolders_off_indexes_only_the_root_folder(scratch, dataset_root):
    scratch.run([dataset_root / "tree"], include_subdirectories=False)
    assert found(scratch, "root", "sub", "deep") == {"root"}


def test_dir_02_turning_subfolders_on_indexes_everything_below(scratch, dataset_root):
    scratch.run([dataset_root / "tree"], include_subdirectories=False)
    scratch.run([dataset_root / "tree"], include_subdirectories=True)
    assert found(scratch, "root", "sub", "deep") == {"root", "sub", "deep"}


def test_dir_03_turning_subfolders_off_again_removes_their_entries(scratch, dataset_root):
    # Recorded behaviour (test plan OQ-7 is still open): the next scan purges them.
    scratch.run([dataset_root / "tree"], include_subdirectories=True)
    stats = scratch.run([dataset_root / "tree"], include_subdirectories=False)
    assert stats["deleted"] == 2
    assert found(scratch, "root", "sub", "deep") == {"root"}


def test_dir_04_nested_and_duplicate_roots_are_scanned_once(scratch, dataset_root):
    tree = dataset_root / "tree"
    stats = scratch.run([tree, tree / "sub", tree, tree / "sub" / "deep"])
    assert stats["scanned"] == 3 and stats["indexed"] == 3
    rows = scratch.db.get_connection().execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert rows == 3


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need POSIX")
def test_dir_04_symlink_loop_terminates(scratch, dataset_root):
    stats = scratch.run([dataset_root / "bad"])
    assert stats["scanned"] < 50  # would never return (or explode) if the loop were followed
    assert scratch.names("QABADgood") == {"good.txt"}


def test_dir_05_excluded_folders_are_not_indexed(scratch, dataset_root):
    scratch.run([dataset_root / "tree"], exclude_patterns=["sub"])
    assert found(scratch, "root", "sub", "deep") == {"root"}


def test_dir_06_awkward_paths_are_indexed_once_and_searchable(env):
    keys = ["cjk", "emoji", "spaces", "percent", "underscore", "brackets", "nfd"]
    for key in keys:
        hits = env.results(f"QAPATH{key}")
        assert len(hits) == 1, key
        (path,) = hits
        assert path.startswith("path/")
    stored = [row[0] for row in env.db.get_connection().execute("SELECT path FROM documents")]
    assert sum(1 for path in stored if Path(path).parent.name == "path") == len(keys)


# ---- IDX: pause / resume / stop ---------------------------------------------------------------
def _corpus(tmp_path, count=12):
    folder = tmp_path / "corpus"
    folder.mkdir()
    for number in range(count):
        (folder / f"doc{number:02d}.txt").write_text(f"QAIDX{number:02d} body", encoding="utf-8")
    return folder


def _documents(scratch):
    return scratch.db.get_connection().execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def _run_in_thread(scratch, folder, on_progress, roots=None):
    result = {}

    def target():
        result.update(scratch.run(roots or [folder], on_progress=on_progress))

    thread = threading.Thread(target=target, daemon=True)  # a failing test must not hang pytest
    thread.start()
    return thread, result


def _settled_count(scratch, quiet=0.4):
    """Document count once it has stopped changing (the file in flight has finished)."""
    last, since = _documents(scratch), time.monotonic()
    while time.monotonic() - since < quiet:
        time.sleep(0.05)
        current = _documents(scratch)
        if current != last:
            last, since = current, time.monotonic()
    return last


def _disk_integrity(scratch):
    """integrity_check on a fresh connection: a long-lived one can report FTS5 false positives."""
    conn = sqlite3.connect(scratch.db.db_path)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def test_idx_01_pause_holds_progress_and_resume_finishes_the_job(scratch, tmp_path):
    folder = _corpus(tmp_path)
    reached = threading.Event()

    def on_progress(done, total, name):
        if done == 3:
            scratch.service.pause()  # takes effect before the next file
            reached.set()

    thread, result = _run_in_thread(scratch, folder, on_progress)
    try:
        assert reached.wait(10)
        stalled = _settled_count(scratch)
        assert thread.is_alive() and scratch.service.is_paused
        assert 3 <= stalled < 12  # the file in flight finished, then nothing moved
        time.sleep(0.3)
        assert _documents(scratch) == stalled
    finally:
        scratch.service.resume()
    thread.join(20)
    assert not thread.is_alive()
    assert result["indexed"] == 12 and not result["cancelled"]
    assert _documents(scratch) == 12


def test_idx_02_stop_ends_gracefully_and_leaves_a_valid_database(scratch, tmp_path):
    folder = _corpus(tmp_path)

    def on_progress(done, total, name):
        if done == 4:
            scratch.service.cancel()

    thread, result = _run_in_thread(scratch, folder, on_progress)
    thread.join(20)
    assert not thread.is_alive()
    assert result["cancelled"] and 4 <= _documents(scratch) < 12  # the file in flight finished
    assert _disk_integrity(scratch) == "ok"


def test_idx_03_scan_after_stop_completes_without_duplicates(scratch, tmp_path):
    folder = _corpus(tmp_path)

    def on_progress(done, total, name):
        if done == 4:
            scratch.service.cancel()

    thread, _ = _run_in_thread(scratch, folder, on_progress)
    thread.join(20)
    scratch.service.indexer.reset_cancellation()  # what starting a new scan does
    stats = scratch.run([folder])
    assert not stats["cancelled"] and _documents(scratch) == 12
    conn = scratch.db.get_connection()
    assert conn.execute("SELECT COUNT(DISTINCT path) FROM documents").fetchone()[0] == 12
    assert conn.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0] == 12
    assert scratch.names("QAIDX11") == {"doc11.txt"}


def test_idx_04_search_works_while_indexing(scratch, tmp_path):
    folder = _corpus(tmp_path, 30)
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "seed.txt").write_text("QASEED present", encoding="utf-8")
    scratch.run([tmp_path / "seed"])
    midway = threading.Event()

    def on_progress(done, total, name):
        if done == 5:
            midway.set()

    thread, _ = _run_in_thread(scratch, folder, on_progress, roots=[tmp_path / "seed", folder])
    assert midway.wait(10)
    assert scratch.names("QASEED") == {"seed.txt"}  # answered while the scan is still running
    thread.join(30)
    assert not thread.is_alive()


def test_idx_05_broken_files_are_recorded_and_good_ones_still_indexed(scratch, dataset_root):
    stats = scratch.run([dataset_root / "bad"])
    assert stats["failed"] >= 4
    assert scratch.names("QABADgood") == {"good.txt"}
    errors = dict(
        scratch.db.get_connection().execute("SELECT filename, error FROM documents").fetchall()
    )
    for name in (
        "truncated.docx",
        "truncated.xlsx",
        "empty.pdf",
        "garbage.pdf",
        "user_password.pdf",
    ):
        assert errors.get(name), name  # a reason is recorded for every unreadable file
    assert "~$temp.docx" not in errors  # Office lock files are ignored outright
    if not RUNNING_AS_ROOT and sys.platform != "win32":
        assert errors.get("locked.txt")


def test_idx_06_killing_the_process_mid_scan_leaves_a_recoverable_index(tmp_path):
    """The whole process dies (SIGKILL / TerminateProcess) while indexing, as on a crash or power cut."""
    import subprocess

    folder = tmp_path / "corpus"
    folder.mkdir()
    total = 1500
    for number in range(total):
        (folder / f"doc{number:04d}.txt").write_text(
            f"QAKILL{number:04d} body text", encoding="utf-8"
        )
    data_dir = tmp_path / "data"
    db_path = data_dir / "index.db"
    script = (
        "import sys; from doc_searcher.storage.database import Database; "
        "from doc_searcher.indexing.service import IndexingService, IndexRequest; "
        "Database(sys.argv[1]).close(); "
        "IndexingService(Database(sys.argv[1])).run(IndexRequest([sys.argv[2]]))"
    )
    src = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src), "DOC_SEARCHER_DATA_DIR": str(data_dir)}
    data_dir.mkdir()
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(db_path), str(folder)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        indexed = 0
        while time.monotonic() < deadline and process.poll() is None:
            try:
                probe = sqlite3.connect(str(db_path), timeout=1)
                try:
                    indexed = probe.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                finally:
                    probe.close()
            except sqlite3.Error:
                indexed = 0  # the database is being created or is busy; look again
            if indexed >= 100:
                break
            time.sleep(0.02)
        assert indexed >= 100, "the child never reached the mid-scan point"
        killed_mid_scan = process.poll() is None
        process.kill()
    finally:
        process.wait(30)
    if not killed_mid_scan:
        pytest.skip("indexing finished before the process could be killed on this machine")

    conn = sqlite3.connect(str(db_path))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        partial = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    finally:
        conn.close()
    assert 100 <= partial < total

    from doc_searcher.indexing.service import IndexingService, IndexRequest
    from doc_searcher.search.searcher import DocumentSearcher
    from doc_searcher.storage.database import Database

    db = Database(str(db_path))  # opens without an error dialog-worthy failure
    try:
        stats = IndexingService(db).run(IndexRequest([str(folder)]))
        assert not stats["cancelled"] and stats["failed"] == 0
        conn = db.get_connection()
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == total
        assert conn.execute("SELECT COUNT(DISTINCT path) FROM documents").fetchone()[0] == total
        assert conn.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0] == total
        assert {r.filename for r in DocumentSearcher(db).search("QAKILL0007")} == {"doc0007.txt"}
    finally:
        db.close()
    check = sqlite3.connect(str(db_path))
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        check.close()
