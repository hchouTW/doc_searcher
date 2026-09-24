"""Contract tests: CLI, desktop IndexWorker, and MCP SearchService all delegate to
IndexingService and report completion, failure, cancellation, and unavailable roots (Task 4.1)."""

import pytest

from doc_searcher import cli
from doc_searcher.config import AppConfig
from doc_searcher.desktop.worker import IndexWorker
from doc_searcher.indexing.indexer import DocumentIndexer
from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.search.search_service import SearchService
from doc_searcher.storage.database import Database
from doc_searcher.storage.errors import DatabaseLockedError


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_SEARCHER_DATA_DIR", str(tmp_path / "data"))
    roots = {}
    for name in ("a", "b"):
        root = tmp_path / name
        (root / "sub").mkdir(parents=True)
        (root / f"{name}1.txt").write_text(f"{name} keyword", encoding="utf-8")
        (root / "sub" / f"{name}2.txt").write_text(f"{name} keyword two", encoding="utf-8")
        roots[name] = root
    return roots


def db_path():
    return AppConfig().db_path


def indexed():
    db = Database(db_path())
    try:
        return set(db.get_all_indexed_paths())
    finally:
        db.close()


def clear_index():
    db = Database(db_path())
    IndexingService(db).clear()
    db.close()


def seed(*roots):
    db = Database(db_path())
    IndexingService(db).run(IndexRequest(roots=[str(r) for r in roots]))
    db.close()


# ------------------------------------------------------------------ adapters
def run_desktop(roots, cancel=False):
    db = Database(db_path())
    worker = IndexWorker(db, [str(r) for r in roots])
    finished, states = [], []
    worker.indexing_finished.connect(finished.append)
    worker.state_changed.connect(states.append)
    if cancel:
        worker.cancel()
    worker.run()  # synchronous; run() also closes the connection
    return finished[0], states


def run_mcp(roots, subfolder=None):
    config = AppConfig()
    config.directories = [str(r) for r in roots]
    service = SearchService(config)
    service.start_reindex(str(subfolder) if subfolder else None)
    service.wait_for_reindex(60)
    state = service.index_status()["reindex"]
    service.db.close()
    return state


def run_cli(root):
    return cli.main(["--dir", str(root), "--search", "keyword"])


# ------------------------------------------------------------ same decisions
def test_desktop_and_mcp_make_identical_full_rescan_decisions(env):
    a, b = env["a"], env["b"]
    outcomes = []
    for adapter in (run_desktop, run_mcp):
        seed(a, b)
        (a / "a1.txt").unlink()  # deleted inside a configured root
        (a / "new.txt").write_text("keyword new", encoding="utf-8")
        adapter([a])  # b was removed from the configured roots
        outcomes.append(indexed())
        (a / "new.txt").unlink()
        (a / "a1.txt").write_text("a keyword", encoding="utf-8")
        clear_index()
    assert outcomes[0] == outcomes[1] == {str(a / "sub" / "a2.txt"), str(a / "new.txt")}


def test_cli_and_mcp_subfolder_make_identical_scoped_decisions(env):
    a, b = env["a"], env["b"]
    outcomes = []
    for adapter in ("cli", "mcp"):
        seed(a, b)
        (a / "sub" / "a2.txt").unlink()
        if adapter == "cli":
            assert run_cli(a / "sub") == 0
        else:
            assert run_mcp([a, b], subfolder=a / "sub")["state"] == "completed"
        outcomes.append(indexed())
        (a / "sub" / "a2.txt").write_text("a keyword two", encoding="utf-8")
        clear_index()
    expected = {str(a / "a1.txt"), str(b / "b1.txt"), str(b / "sub" / "b2.txt")}
    assert outcomes[0] == outcomes[1] == expected


# ---------------------------------------------------------------- completion
def test_completion_is_reported_by_every_adapter(env):
    a = env["a"]
    stats, states = run_desktop([a])
    assert stats["indexed"] == 2 and states[-1] == "completed"

    (a / "a3.txt").write_text("keyword three", encoding="utf-8")
    state = run_mcp([a])
    assert state["state"] == "completed" and state["result"]["indexed"] == 1

    (a / "a4.txt").write_text("keyword four", encoding="utf-8")
    assert run_cli(a) == 0
    assert str(a / "a4.txt") in indexed()


# ------------------------------------------------------------------- failure
def test_failures_are_reported_by_every_adapter(env, monkeypatch, capsys):
    def boom(self, request, on_progress=None, on_phase=None):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(IndexingService, "run", boom)

    stats, states = run_desktop([env["a"]])
    assert stats["error"] == "disk exploded" and states[-1] == "error"

    state = run_mcp([env["a"]])
    assert state["state"] == "failed" and "disk exploded" in state["result"]["error"]

    with pytest.raises(RuntimeError, match="disk exploded"):
        run_cli(env["a"])  # unexpected errors propagate: non-zero exit with a traceback

    def locked(self, request, on_progress=None, on_phase=None):
        raise DatabaseLockedError("index.db is locked by another program")

    monkeypatch.setattr(IndexingService, "run", locked)
    assert run_cli(env["a"]) == 3
    assert "locked" in capsys.readouterr().err


# -------------------------------------------------------------- cancellation
def test_cancellation_is_reported_and_leaves_a_consistent_index(env, monkeypatch):
    a = env["a"]
    stats, states = run_desktop([a], cancel=True)
    assert stats["cancelled"] is True and states[-1] == "idle"
    assert indexed() == set()

    real_run = IndexingService.run

    def cancel_first(self, *args, **kwargs):
        self.cancel()
        return real_run(self, *args, **kwargs)

    monkeypatch.setattr(IndexingService, "run", cancel_first)
    state = run_mcp([a])
    assert state["state"] == "completed" and state["result"]["cancelled"] is True
    monkeypatch.setattr(
        IndexingService, "run", real_run
    )  # never monkeypatch.undo(): it drops the data-dir isolation

    # The CLI is interrupted with Ctrl+C: finished documents stay, the rerun completes the rest.
    real_index = DocumentIndexer.index_single_file
    calls = []

    def interrupt_second(self, path):
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_index(self, path)

    monkeypatch.setattr(DocumentIndexer, "index_single_file", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        run_cli(a)
    assert len(indexed()) == 1
    monkeypatch.setattr(DocumentIndexer, "index_single_file", real_index)
    assert run_cli(a) == 0
    assert indexed() == {str(a / "a1.txt"), str(a / "sub" / "a2.txt")}


# --------------------------------------------------------- unavailable roots
def test_unavailable_roots_are_preserved_by_every_adapter(env, tmp_path, capsys):
    a = env["a"]
    seed(a)
    before = indexed()
    a.rename(tmp_path / "a_offline")

    stats, _ = run_desktop([a])
    assert stats["skipped"] is True and stats["unavailable_directories"] == [str(a)]

    state = run_mcp([a])
    assert state["result"]["skipped"] is True and state["result"]["unavailable_directories"] == [
        str(a)
    ]

    assert run_cli(a) == 1
    assert "無法存取" in capsys.readouterr().err
    assert indexed() == before
