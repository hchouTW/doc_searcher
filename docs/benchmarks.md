# Benchmarks (Task 5.1)

## How to run

```bash
pip install -e '.[dev]'                                   # pytest-benchmark is in the dev extra
python -m pytest tests/benchmarks --benchmark-only        # ~35 s: 1k corpus + large files
DOC_SEARCHER_BENCH_10K=1 python -m pytest tests/benchmarks --benchmark-only   # adds 10k cold index (~20 s more)
python -m pytest tests/benchmarks --benchmark-only --benchmark-json=bench.json # machine-readable
pytest-benchmark compare docs/benchmarks/baseline-macos-arm64-py313.json bench.json \
    --columns=median --group-by=name                                          # compare to baseline
```

Benchmarks live outside the normal test run (`norecursedirs`), so `pytest` alone never runs them.

## Methodology

- **Datasets** are generated deterministically (fixed seed) into pytest temp directories and are
  never committed (`tests/benchmarks/conftest.py`):
  - `corpus_1k` / `corpus_10k`: 1,000 / 10,000 `.txt`/`.md`/`.csv` files of ~1.5 KB mixed
    Traditional Chinese/English text with dates and numbers, in 20 folders.
  - `large_files`: a 200-page PDF, a ~5 MB text file, and a 20,000-row `.xlsx`.
- **Timing**: `pytest-benchmark` wall-clock medians. State-changing operations use
  `benchmark.pedantic` with fixed rounds and untimed setup (a fresh database per cold index; a
  modified file before each single-file update; cancellation starts timing at `cancel()` after
  100 files have been processed, and stops when `run()` returns).
- **Memory**: `peak_tracemalloc_mb` is the Python-heap peak of one extra run (it does not see
  MuPDF/SQLite C allocations); `max_rss_mb` is the process peak RSS so far, which includes C
  libraries but is monotonic across the session, so it is only an upper bound per benchmark.
- **Repeatability**: two consecutive full runs on the baseline machine agreed within ~10% on
  every median.

## Baseline — 2026-09-24, commit `c8ac4fa`

Apple M3 (8 cores), macOS 26.5 (Darwin 25.5.0) arm64, Python 3.13.2, SQLite 3.51.1.
Full data: [`benchmarks/baseline-macos-arm64-py313.json`](benchmarks/baseline-macos-arm64-py313.json).

| Benchmark | Median | Memory |
| --- | ---: | --- |
| Cold index, 1k files | 1.75 s | tracemalloc 0.3 MB · RSS ≤ 119 MB |
| Cold index, 10k files | 20.7 s | RSS ≤ 119 MB |
| No-change rescan, 1k | 10.2 ms | |
| Single-file update, 1k index | 14.3 ms | |
| Cancellation latency (cold 1k) | 2.1 ms | |
| Parse 200-page PDF | 39.8 ms | tracemalloc 0.1 MB (C heap not traced) |
| Parse 5 MB text | 6.0 ms | tracemalloc 20.1 MB |
| Parse 20k-row xlsx | 406 ms | tracemalloc 5.9 MB |
| FTS search `預算` | 15.4 ms | |
| FTS search `budget` | 19.6 ms | |
| FTS search `quarterly AND 風險` | 28.4 ms | |
| FTS phrase `"revenue forecast"` | 6.1 ms | |
| Regex search `20(19\|2[0-6])-0[1-6]-\d{2}` | 12.0 ms | tracemalloc 2.1 MB |

Cold indexing scales linearly (~1.75 ms/file → ~2.1 ms/file at 10k); it is dominated by jieba
tokenization, not I/O.

## Proposed performance budgets (to be agreed)

Budgets are **proposals** pending maintainer agreement; tasks 5.2/5.3 must not be marked done
until they are agreed. Measured on the baseline machine; a regression is a median more than
the stated margin above baseline.

| Area | Proposed budget |
| --- | --- |
| Common FTS searches (1k index) | ≤ 1.25× baseline median |
| Regex search (1k index) | ≤ 1.25× baseline median; peak Python memory must not grow with the number of matches in a segment |
| No-change rescan / single-file update | ≤ 1.25× baseline |
| Cancellation latency | ≤ 250 ms from `cancel()` to return |
| Cold indexing | no regression > 1.25×; a parallel-parsing change (Task 5.3) must show ≥ 1.5× improvement on the 10k corpus to be worth its complexity |
