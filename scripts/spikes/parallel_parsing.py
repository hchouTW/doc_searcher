#!/usr/bin/env python3
# Purpose: Task 5.3 spike — does bounded parallel parse+tokenize speed up cold indexing safely?
# What the code does:
#   - Builds corpora (text-only via the benchmark generator; mixed PDF/DOCX/XLSX/TXT) in a temp dir.
#   - Indexes each corpus sequentially (production IndexingService) and with bounded thread and
#     process pools that parse+tokenize in workers while the main thread is the single SQLite
#     writer (at most 2x workers results in flight).
#   - Reports wall time, peak RSS (self + children), cancellation latency, and whether the
#     resulting database content is identical to the sequential run.
# Usage notes, dependencies, or assumptions:
#   - Spike code, not production: PYTHONPATH=src python scripts/spikes/parallel_parsing.py [--10k]
#   - Findings: docs/spikes/0001-parallel-parsing.md

import argparse
import concurrent.futures as cf
import hashlib
import os
import random
import resource
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "benchmarks"))  # spike-only: reuse the corpus generator

from conftest import _paragraph, build_corpus  # noqa: E402

from doc_searcher.indexing.service import IndexingService, IndexRequest  # noqa: E402
from doc_searcher.parsers import parse_file  # noqa: E402
from doc_searcher.search.text_helper import tokenize_for_fts  # noqa: E402
from doc_searcher.storage.database import Database  # noqa: E402


def build_mixed(root: Path, count: int) -> Path:
    import docx
    import openpyxl
    import pymupdf

    rng = random.Random(5)
    root.mkdir(parents=True)
    for index in range(count):
        kind = index % 4
        path = root / f"doc_{index:04d}"
        if kind == 0:
            pdf = pymupdf.open()
            for _ in range(5):
                pdf.new_page().insert_text((50, 60), _paragraph(rng, 120), fontsize=8)
            pdf.save(str(path.with_suffix(".pdf")))
            pdf.close()
        elif kind == 1:
            document = docx.Document()
            for _ in range(6):
                document.add_paragraph(_paragraph(rng, 80))
            document.save(str(path.with_suffix(".docx")))
        elif kind == 2:
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            for row in range(60):
                sheet.append([row, _paragraph(rng, 6), rng.randint(1, 10**6)])
            workbook.save(str(path.with_suffix(".xlsx")))
        else:
            path.with_suffix(".txt").write_text(_paragraph(rng, 300), encoding="utf-8")
    return root


# ------------------------------------------------------------------ workers
def parse_and_tokenize(path: str):
    """Worker: the CPU-heavy part of DocumentIndexer.index_single_file."""
    stat = os.stat(path)
    extracted = parse_file(path)
    segments = [
        {
            "segment_id": seg.segment_id,
            "segment_type": seg.segment_type,
            "content": seg.text.strip(),
            "tokenized_content": tokenize_for_fts(seg.text.strip()),
        }
        for seg in extracted.segments
        if seg.text.strip()
    ]
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return path, ext, stat.st_size, stat.st_mtime, extracted.error, segments


def _warm_worker():
    tokenize_for_fts("預熱")  # load jieba's dictionary once per process


def parallel_index(files, db_path, kind, workers, cancel_after=None):
    """Bounded pool + single writer. Returns (seconds, cancel_latency_or_None)."""
    db = Database(db_path)
    pool_cls = cf.ProcessPoolExecutor if kind == "process" else cf.ThreadPoolExecutor
    extra = {"initializer": _warm_worker} if kind == "process" else {}
    started = time.perf_counter()
    cancel_latency = None
    with pool_cls(max_workers=workers, **extra) as pool:
        pending = iter(files)
        in_flight = set()
        written = 0
        cancel_at = None
        while True:
            while len(in_flight) < workers * 2 and cancel_at is None:
                path = next(pending, None)
                if path is None:
                    break
                in_flight.add(pool.submit(parse_and_tokenize, path))
            if not in_flight:
                break
            done, in_flight = cf.wait(in_flight, return_when=cf.FIRST_COMPLETED)
            for future in done:
                if cancel_at is not None:
                    continue
                path, ext, size, mtime, error, segments = future.result()
                db.save_document_index(path, ext, size, mtime, segments, error=error)
                written += 1
                if cancel_after is not None and written >= cancel_after:
                    cancel_at = time.perf_counter()
                    for other in in_flight:
                        other.cancel()
            if cancel_at is not None and all(f.done() or f.cancelled() for f in in_flight):
                cancel_latency = time.perf_counter() - cancel_at
                break
    db.close()
    return time.perf_counter() - started, cancel_latency


def sequential_index(root, db_path, cancel_after=None):
    db = Database(db_path)
    service = IndexingService(db)
    started = time.perf_counter()
    marks = {}

    def on_progress(current, total, name):
        if cancel_after is not None and current == cancel_after + 1:
            marks["cancel"] = time.perf_counter()
            service.cancel()

    service.run(IndexRequest(roots=[str(root)]), on_progress=on_progress)
    db.close()
    latency = time.perf_counter() - marks["cancel"] if "cancel" in marks else None
    return time.perf_counter() - started, latency


def content_digest(db_path) -> str:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT d.path, d.error, s.segment_id, s.content FROM documents d "
        "LEFT JOIN doc_segments s ON s.doc_id = d.id ORDER BY d.path, s.segment_id"
    ).fetchall()
    fts = conn.execute(
        "SELECT d.path, f.segment_id, f.tokenized_content FROM doc_fts f "
        "JOIN documents d ON d.id = CAST(f.doc_id AS INTEGER) ORDER BY d.path, f.segment_id"
    ).fetchall()
    conn.close()
    return hashlib.sha256(repr((rows, fts)).encode()).hexdigest()[:16]


def peak_rss_mb() -> float:
    scale = 2**20 if sys.platform == "darwin" else 2**10
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(own / scale, 1), round(children / scale, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--10k", dest="large", action="store_true")
    args = parser.parse_args()
    tokenize_for_fts("預熱")
    work = Path(tempfile.mkdtemp())
    corpora = {
        "text-1k": build_corpus(work / "text1k", 1_000),
        "mixed-400": build_mixed(work / "mixed", 400),
    }
    if args.large:
        corpora["text-10k"] = build_corpus(work / "text10k", 10_000)
    cpu = os.cpu_count() or 1
    print(f"cpu_count={cpu} python={sys.version.split()[0]} platform={sys.platform}")
    for name, root in corpora.items():
        files = sorted(str(p) for p in root.rglob("*") if p.is_file())
        seq_db = str(work / f"{name}-seq.db")
        seq_time, _ = sequential_index(root, seq_db)
        reference = content_digest(seq_db)
        print(f"\n[{name}] {len(files)} files  sequential {seq_time:.2f}s  digest {reference}")
        for kind, workers in [("thread", 4), ("process", 2), ("process", 4), ("process", cpu)]:
            db_path = str(work / f"{name}-{kind}{workers}.db")
            seconds, _ = parallel_index(files, db_path, kind, workers)
            same = content_digest(db_path) == reference
            print(
                f"  {kind:7s} x{workers:<2d} {seconds:6.2f}s  speedup {seq_time / seconds:4.2f}x  "
                f"identical_db={same}  peak_rss(self, children)={peak_rss_mb()}"
            )
        _, seq_cancel = sequential_index(
            root, str(work / f"{name}-seqc.db"), cancel_after=len(files) // 4
        )
        _, par_cancel = parallel_index(
            files, str(work / f"{name}-pc.db"), "process", cpu, len(files) // 4
        )
        print(
            f"  cancel latency: sequential {seq_cancel * 1000:.0f} ms, process x{cpu} {par_cancel * 1000:.0f} ms"
        )


if __name__ == "__main__":
    main()
