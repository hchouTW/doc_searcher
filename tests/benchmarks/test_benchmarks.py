"""Indexing and search benchmarks (Task 5.1). Methodology and baseline: docs/benchmarks.md.

Run: python -m pytest tests/benchmarks --benchmark-only --benchmark-json=bench.json
extra_info records peak_tracemalloc_mb (Python allocations of one extra run) and
max_rss_mb (process peak RSS so far, monotonic, includes C libraries) where memory matters.
"""

import shutil
import sys
import threading
import time
import tracemalloc

import pytest

from doc_searcher.indexing.service import IndexingService, IndexRequest
from doc_searcher.parsers import parse_file
from doc_searcher.search.searcher import DocumentSearcher
from doc_searcher.storage.database import Database


def _max_rss_mb() -> float:
    """Process peak resident memory so far (monotonic; includes C allocations such as MuPDF)."""
    try:
        import resource
    except ImportError:  # Windows
        return float("nan")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 2**10  # bytes vs KiB


def _peak_mb(function) -> float:
    tracemalloc.start()
    try:
        function()
        return tracemalloc.get_traced_memory()[1] / 2**20
    finally:
        tracemalloc.stop()


def _cold_index(corpus, db_path):
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    db = Database(str(db_path))
    try:
        return IndexingService(db).run(IndexRequest(roots=[str(corpus)]))
    finally:
        db.close()


# ---------------------------------------------------------------- indexing
def test_cold_index_1k(benchmark, corpus_1k, tmp_path):
    db_path = tmp_path / "cold.db"
    stats = benchmark.pedantic(_cold_index, args=(corpus_1k, db_path), rounds=3, iterations=1)
    assert stats["indexed"] == 1_000
    benchmark.extra_info["peak_tracemalloc_mb"] = round(
        _peak_mb(lambda: _cold_index(corpus_1k, db_path)), 1
    )
    benchmark.extra_info["max_rss_mb"] = round(_max_rss_mb(), 1)


def test_cold_index_10k(benchmark, corpus_10k, tmp_path):
    db_path = tmp_path / "cold.db"
    stats = benchmark.pedantic(_cold_index, args=(corpus_10k, db_path), rounds=1, iterations=1)
    assert stats["indexed"] == 10_000
    benchmark.extra_info["max_rss_mb"] = round(_max_rss_mb(), 1)


def test_no_change_rescan_1k(benchmark, corpus_1k, indexed_1k):
    db = Database(str(indexed_1k))
    try:
        stats = benchmark.pedantic(
            lambda: IndexingService(db).run(IndexRequest(roots=[str(corpus_1k)])),
            rounds=5,
            iterations=1,
        )
    finally:
        db.close()
    assert stats["indexed"] == 0 and stats["deleted"] == 0


def test_single_file_update_1k(benchmark, corpus_1k, indexed_1k):
    target = next(corpus_1k.rglob("doc_00007.*"))
    original = target.read_text(encoding="utf-8")
    db = Database(str(indexed_1k))
    counter = iter(range(10**6))

    def touch():
        target.write_text(original + f"\nrevision {next(counter)}", encoding="utf-8")
        return (), {}

    try:
        stats = benchmark.pedantic(
            lambda: IndexingService(db).run(IndexRequest(roots=[str(corpus_1k)])),
            setup=touch,
            rounds=5,
            iterations=1,
        )
    finally:
        target.write_text(original, encoding="utf-8")
        IndexingService(db).run(IndexRequest(roots=[str(corpus_1k)]))
        db.close()
    assert stats["indexed"] == 1 and stats["deleted"] == 0


def test_cancellation_latency_1k(benchmark, corpus_1k, tmp_path):
    """Time from cancel() to run() returning, measured mid-way through a cold index."""
    state = {}

    def start_run():
        db_path = tmp_path / f"cancel_{time.monotonic_ns()}.db"
        db = Database(str(db_path))
        service = IndexingService(db)
        progressed = threading.Event()

        def on_progress(current, total, name):
            if current >= 100:
                progressed.set()

        thread = threading.Thread(
            target=lambda: state.update(
                stats=service.run(IndexRequest(roots=[str(corpus_1k)]), on_progress=on_progress)
            )
        )
        thread.start()
        assert progressed.wait(120)
        return (service, thread, db), {}

    def cancel(service, thread, db):
        service.cancel()
        thread.join()
        db.close()

    benchmark.pedantic(cancel, setup=start_run, rounds=3, iterations=1)
    assert state["stats"]["cancelled"] is True


@pytest.mark.parametrize("name", ["large.pdf", "large.txt", "large.xlsx"])
def test_parse_large_file(benchmark, large_files, name):
    result = benchmark.pedantic(parse_file, args=(str(large_files / name),), rounds=3, iterations=1)
    assert result.segments
    benchmark.extra_info["peak_tracemalloc_mb"] = round(
        _peak_mb(lambda: parse_file(str(large_files / name))), 1
    )
    benchmark.extra_info["max_rss_mb"] = round(_max_rss_mb(), 1)


# ------------------------------------------------------------------ search
@pytest.fixture(scope="module")
def searcher_1k(indexed_1k, tmp_path_factory):
    copy = tmp_path_factory.mktemp("search") / "index.db"
    shutil.copy(indexed_1k, copy)
    db = Database(str(copy))
    yield DocumentSearcher(db)
    db.close()


@pytest.mark.parametrize("query", ["預算", "budget", "quarterly AND 風險", '"revenue forecast"'])
def test_fts_search_1k(benchmark, searcher_1k, query):
    results = benchmark(searcher_1k.search, query)
    assert results


def test_regex_search_1k(benchmark, searcher_1k):
    pattern = r"20(19|2[0-6])-0[1-6]-\d{2}"
    results = benchmark.pedantic(
        searcher_1k.search, args=(pattern,), kwargs={"regex": True}, rounds=5
    )
    assert results
    benchmark.extra_info["peak_tracemalloc_mb"] = round(
        _peak_mb(lambda: searcher_1k.search(pattern, regex=True)), 1
    )
    benchmark.extra_info["max_rss_mb"] = round(_max_rss_mb(), 1)
