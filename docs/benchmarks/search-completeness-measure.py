# Synthetic measurement driver, deliberately isolated from the user's live index.
import importlib.util
import json
import platform
import resource
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path


def main():
    root, label, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    sys.path.insert(0, str(root / "src"))
    spec = importlib.util.spec_from_file_location(
        "bench_data", root / "tests/benchmarks/conftest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from doc_searcher.storage.database import Database
    from doc_searcher.indexing.service import IndexingService, IndexRequest
    from doc_searcher.search.searcher import DocumentSearcher
    from doc_searcher.search.script_fold import fold

    work = Path(tempfile.mkdtemp(prefix=f"completeness_{label}_{count}_", dir="/private/tmp"))
    corpus = module.build_corpus(work / "corpus", count)
    for i, path in enumerate(sorted(corpus.rglob("*.*"))):
        if i < 10:
            path.write_text(path.read_text() + "\n教師升等級審查\n都市計畫")
    db = Database(str(work / "index.db"))
    start = time.perf_counter()
    stats = IndexingService(db).run(IndexRequest([str(corpus)]))
    cold = time.perf_counter() - start
    searcher = DocumentSearcher(db)
    searcher.search("warmup")

    def timed(fn, rounds=3):
        values = []
        for _ in range(rounds):
            start = time.perf_counter()
            result = fn()
            values.append((time.perf_counter() - start) * 1000)
        return round(statistics.median(values), 3), result

    queries = {}
    for query in ("計畫", "budget", "quarterly AND 風險", "計", "升等"):
        median, results = timed(lambda q=query: searcher.search(q, limit=200))
        queries[query] = dict(
            median_ms=median,
            loaded_documents=len(results),
            returned_matches=sum(r.total_matches for r in results),
        )
    start = time.perf_counter()
    oracle = {
        r[0]
        for r in db.get_connection().execute("SELECT doc_id, content FROM doc_segments")
        if "升等" in fold(r[1])
    }
    scan_ms = (time.perf_counter() - start) * 1000
    median, results = timed(lambda: searcher.search("升等", limit=count))
    recall = len({r.doc_id for r in results} & oracle) / len(oracle)
    tracemalloc.start()
    full_ms, full = timed(lambda: searcher.search("計畫", limit=count), 1)
    peak = tracemalloc.get_traced_memory()[1] / 2**20
    tracemalloc.stop()
    extra = {}
    if hasattr(searcher, "match_locations"):
        page_ms, page = timed(lambda: searcher.search_page("計畫", limit=100))
        location_ms, locations = timed(
            lambda: searcher.match_locations(
                "計畫", page.items[0].doc_id, revision=page.revision, limit=100
            )
        )
        context_ms, context = timed(lambda: searcher.match_context(locations.locations[0]))
        extra = dict(
            page_ms=page_ms,
            total_documents=page.total_documents,
            location_ms=location_ms,
            context_ms=context_ms,
            context_bytes=len(context["html"]),
        )
    conn = db.get_connection()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size = Path(db.db_path).stat().st_size
    index_sizes = {}
    try:
        for name, size_bytes in conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
            if name.startswith("doc_cjk_fts"):
                index_sizes[name] = size_bytes
    except sqlite3.OperationalError:
        pass
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
    report = dict(
        label=label,
        count=count,
        seed=module.SEED,
        python=platform.python_version(),
        sqlite=sqlite3.sqlite_version,
        machine=platform.machine(),
        cold_seconds=round(cold, 3),
        indexed=stats["indexed"],
        db_bytes=size,
        cjk_index_bytes=sum(index_sizes.values()),
        queries=queries,
        compound_recall=recall,
        compound_documents_expected=len(oracle),
        compound_documents_found=len(results),
        compound_index_ms=median,
        compound_scan_ms=round(scan_ms, 3),
        full_count_ms=full_ms,
        full_count_matches=sum(r.total_matches for r in full),
        full_count_peak_python_mb=round(peak, 3),
        process_peak_rss_mb=round(rss, 3),
        **extra,
    )
    print(json.dumps(report, ensure_ascii=False))
    db.close()


if __name__ == "__main__":
    main()
