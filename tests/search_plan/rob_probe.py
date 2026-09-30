# Purpose: Measure indexing time, query latency and memory in a fresh process for ROB-01/01b.
# What the code does:
#   - Builds the deterministic benchmark corpus (tests/benchmarks/conftest.py::build_corpus),
#     indexes it, runs mixed queries, and prints one JSON line with the measurements.
#   - "gui" mode: shows results in the preview panel (Qt offscreen) 200 times and reports RSS
#     samples every 40 cycles, for the ROB-01c growth check.
# Usage notes, dependencies, or assumptions:
#   - python tests/search_plan/rob_probe.py COUNT   (run by test_robustness.py in a subprocess so
#     the process peak RSS is not inflated by the rest of the test session).
#   - Memory numbers use resource.getrusage (POSIX only); on Windows they are reported as null.

import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

QUERIES = [
    "預算",
    "budget",
    "quarterly AND 風險",
    '"revenue forecast"',
    "合約 OR contract",
    "專案 NOT audit",
    "2024-05",
    "會议记录",
    "预算",
    "policy review",
    "客戶",
    "vendor",
    "strategy AND 策略",
    "invoice",
    "分析",
    "schedule",
    "risk",
    "customer product",
    "報告",
    "plan",
]


def peak_rss_mb():
    try:
        import resource
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def main(count: int) -> dict:
    spec = importlib.util.spec_from_file_location(
        "bench_conftest", ROOT / "tests/benchmarks/conftest.py"
    )
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)

    from doc_searcher.indexing.service import IndexingService, IndexRequest
    from doc_searcher.search.searcher import DocumentSearcher
    from doc_searcher.storage.database import Database

    work = Path(tempfile.mkdtemp(prefix="rob_probe_"))
    corpus = bench.build_corpus(work / "corpus", count)
    db = Database(str(work / "index.db"))
    started = time.perf_counter()
    stats = IndexingService(db).run(IndexRequest(roots=[str(corpus)]))
    index_seconds = time.perf_counter() - started

    searcher = DocumentSearcher(db)
    searcher.search("warmup")
    rss_before = peak_rss_mb()
    slowest = 0.0
    for query in QUERIES:
        begin = time.perf_counter()
        searcher.search(query, limit=100)
        slowest = max(slowest, (time.perf_counter() - begin) * 1000)
    rss_after = peak_rss_mb()
    return {
        "count": count,
        "indexed": stats["indexed"],
        "index_seconds": round(index_seconds, 2),
        "slowest_query_ms": round(slowest, 1),
        "peak_rss_mb": None if rss_after is None else round(rss_after, 1),
        "rss_growth_mb": None if rss_after is None else round(rss_after - rss_before, 1),
    }


def gui_cycles(cycles: int = 200, sample_every: int = 40) -> dict:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    spec = importlib.util.spec_from_file_location(
        "bench_conftest", ROOT / "tests/benchmarks/conftest.py"
    )
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)

    from PySide6.QtWidgets import QApplication

    from doc_searcher.desktop.preview_panel import PreviewPanel
    from doc_searcher.indexing.service import IndexingService, IndexRequest
    from doc_searcher.search.searcher import DocumentSearcher
    from doc_searcher.storage.database import Database

    app = QApplication.instance() or QApplication([])
    work = Path(tempfile.mkdtemp(prefix="rob_gui_"))
    corpus = bench.build_corpus(work / "corpus", 300)
    db = Database(str(work / "index.db"))
    IndexingService(db).run(IndexRequest(roots=[str(corpus)]))
    items = DocumentSearcher(db).search("budget OR 預算", limit=50)
    preview = PreviewPanel()
    for warm in range(20):  # let allocators and caches settle before the baseline
        preview.display_result(items[warm % len(items)])
    app.processEvents()
    baseline = peak_rss_mb()
    samples = []
    for cycle in range(1, cycles + 1):
        preview.display_result(items[cycle % len(items)])
        preview._next_match()
        if cycle % sample_every == 0:
            app.processEvents()
            samples.append(None if baseline is None else round(peak_rss_mb() - baseline, 1))
    return {"cycles": cycles, "growth_samples_mb": samples}


if __name__ == "__main__":
    if sys.argv[1] == "gui":
        print(json.dumps(gui_cycles()))
    else:
        print(json.dumps(main(int(sys.argv[1]))))
