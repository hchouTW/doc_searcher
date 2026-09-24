# Spike 0001 — Bounded parallel parsing (Task 5.3)

- **Date:** 2026-09-24 · **Timebox:** 1 day (used ~1 hour) · **Code:** `scripts/spikes/parallel_parsing.py`
- **Question:** does parallel extraction materially speed up representative cold indexing without
  breaking parser or SQLite safety?

## Where the time goes

Profiling a sequential cold index of the 1k text corpus: **jieba tokenization 92%**, SQLite
writes 5%, parsing 2%, scanning < 1%. Tokenization is pure Python, so it holds the GIL.

## Method

Each corpus is indexed sequentially (production `IndexingService`) and with bounded pools in
which workers run parse + tokenize while the main thread is the **single SQLite writer**
(at most 2×workers results in flight). Every parallel database is compared with the sequential
one by a digest over documents, segments, and FTS rows. Machine: Apple M3 (8 cores: 4 performance
+ 4 efficiency), macOS 26.5, Python 3.13.2. One run per configuration.

## Results

| Corpus | Sequential | Threads ×4 | Processes ×2 | Processes ×4 | Processes ×8 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1k text files | 2.04 s | 1.03× | 1.26× | **1.96×** | 1.47× |
| 400 mixed (PDF/DOCX/XLSX/TXT) | 2.08 s | 1.21× | 1.29× | **1.73×** | 1.35× |
| 10k text files | 20.05 s | 1.14× | 1.99× | **3.66×** | 3.06× |

- **Determinism:** every parallel database was identical to the sequential one.
- **Cancellation latency:** 1–26 ms (pending tasks are dropped; in-flight documents finish).
- **Memory:** each worker process loads its own jieba dictionary: peak worker RSS 106 MB (text)
  to 193 MB (mixed). Four workers add roughly **0.4–0.8 GB** on top of the ~0.2–0.4 GB app.
- More workers than performance cores is slower (×8 < ×4): efficiency cores and the single
  writer become the bottleneck.

## Recommendation

**Process-based parsing is worth implementing, with conditions; threads are not.** Do not
implement it until the memory budget is agreed, because it is the main cost.

If approved, implement:
1. `ProcessPoolExecutor` with `min(4, performance cores, os.cpu_count() - 1)` workers and a
   bounded in-flight queue (2× workers); the service thread remains the only SQLite writer.
2. Only for batches of ≥ ~200 files to index. Worker start-up (about 0.3 s each to load jieba)
   outweighs the gain for incremental updates, which stay sequential.
3. A worker entry module that imports only parsers + jieba (never Qt), `multiprocessing.freeze_support()`
   in the frozen entry point, and the `spawn` start method on every OS.
4. Pause handled at the writer (stop submitting); cancel drops pending futures; a worker
   crash marks only that document failed.
5. Stress tests (`tests/stress/test_parallel_indexing.py`): identical DB to sequential, bounded
   worker count, cancellation, error accounting, and a frozen-app smoke test on each OS.

**Decision needed:** accept ~0.5 GB of extra peak memory during large cold indexes in exchange for
~2–3.7× faster first-time indexing? If not, keep sequential indexing (the status quo).
