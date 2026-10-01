# Full-folder Coverage Implementation Plan

**Status:** Implementation and local validation complete. Delivery branch: `main`; developed on `feature/full-folder-coverage`.

**Updated:** 2026-10-01. **Base:** `abd0a3c`.

This document records the completed implementation. Remaining platform/build checks are listed separately below.

**Goal:** Deliver the approved extraction, OCR, live-indexing, and expanded-retrieval extension.

**Architecture:** Keep original segments and literal search authoritative. Add source-aware Office extraction, an isolated OCR subprocess, a serialized observer queue, and optional local dense retrieval over offset-preserving chunks.

**Tech Stack:** Python, SQLite FTS5, PyMuPDF, Open XML, watchdog, optional Sentence Transformers.

**Spec:** [Approved design](../specs/2026-10-01-full-folder-coverage-design.md)

**Delivery:** [Setup guide](../../full-folder-search.md) · [Validation report](../../benchmarks/full-folder-coverage-validation.md)

## Verified results

| Check | Result |
| --- | --- |
| Full regression suite | 614 passed, 6 skipped, 2 expected failures |
| Branch-aware coverage | 86.05%; required minimum 82% |
| Ruff lint and mypy | Passed; mypy checked 62 source files |
| Formatting | All 43 modified/new Python files passed; repository-wide check still flags 11 pre-existing untouched files |
| Live small-file updates | Six measurements, 0.396–0.909 seconds; all below five seconds |
| Real Chinese scanned-PDF OCR | `會議` retrieved; extraction/indexing took 0.423 seconds |
| Real bilingual dense retrieval | English and Chinese conceptual queries both ranked the expected HR document first |
| Dependency availability | All nine Linux/Windows/macOS ARM64 × Python 3.10/3.12/3.14 target checks passed |

Measurements were made on macOS 26.5 ARM64 with Python 3.13.2. Runtime and keyword benchmark artifacts are linked in the validation report. Semantic timing used an already initialized model; it does not establish cold-start latency.

## Global constraints

- Preserve existing exact search, occurrence, revision, and filter contracts.
- Local OCR/model processing; missing optional runtimes must be diagnosed.
- Five-second measured target for small native-text files; report slower pending work.
- Chunks target 800 characters, max 1,000, approximately 15% overlap, original offsets.
- Python >=3.10 and existing migration backup/rollback conventions.

## Review focus

- Comments on otherwise empty hidden cells must appear with source locations.
- Mixed PDF image/native pages must retain native text on failed OCR.
- Atomic saves and unavailable roots must not remove retained documents.
- Writes during extraction must be retried rather than committing stale content.
- Semantic passages must never invent literal highlights or occurrence counts.

## Task 1: Office sources

Files: `parsers/xlsx_parser.py`, new `parsers/xlsx_comments.py`, `parsers/pptx_parser.py`, parser-version registry, `tests/unit/parsers/test_extended_coverage.py`.
Interface: `read_comments(path) -> (comments, warnings)`; comments are keyed by worksheet with `(cell, text)` entries. PPTX recursive shape traversal retains source spans.

- [x] Add real XLSX hidden empty-cell comments and grouped PPTX regression fixtures.
- [x] Run those tests and confirm missed text before implementation.
- [x] Implement relationship-based streaming comment reads and recursive source-aware PPTX traversal; warn for unsupported content.
- [x] Run all parser tests and extraction integration tests.

## Task 2: OCR and images

Files: new `parsers/ocr.py`, `parsers/image_parser.py`, `parsers/pdf_parser.py`, parser registry, `tests/unit/parsers/test_ocr_coverage.py`.
Interface: `recognize(pixmap) -> OCRText`, a string subclass carrying word geometry; subprocess timeout, language selection, explicit failure exception. PDF retains native text and records OCR provenance. Images use the same adapter.

- [x] Add scanned/mixed/native PDF fixtures, engine-failure tests, and an optional real OCR integration fixture.
- [x] Confirm failures against the current parser.
- [x] Implement block sorting, image-aware sparse-page OCR trigger, subprocess limits, image parsing, and diagnostic warnings.
- [x] Run parser regressions and a real installed-engine English/Chinese smoke check when runtime data is available.

Review fixes: OCR mixed pages from original raster pixels; suppress only fully represented OCR words overlapping native text. Preserve partial native-layer coverage and repeated words at distinct positions. Cancellation terminates OCR work without committing a failed partial result.

## Task 3: Live updates

Files: new `indexing/watcher.py`, `indexing/service.py`, `indexing/indexer.py`, desktop lifecycle, CLI watch adapter, SearchService/MCP lifecycle, `tests/test_live_indexing.py`.
Interface: `IndexingService.update_paths(request, paths) -> dict`; `FolderWatcher.start()/stop()/pause()/resume()/status()`, watchdog event queue and reconciliation fallback.

- [x] Add create/update/move/delete, exclusion, inaccessible-root, atomic-save, extraction-race, and latency regressions.
- [x] Run and confirm missing observer/targeted-update behavior.
- [x] Implement targeted indexing under existing writer lock, changed-during-parse retry, bounded debounce, periodic reconciliation, lifecycle and status adapters.
- [x] Verify small native file changes become searchable under five seconds and safely stop workers.

Implementation details: 350 ms debounce; macOS uses 500 ms watchdog polling after a reproducible native FSEvents shutdown crash. Without watchdog, reconciliation runs approximately every second. File events use targeted updates; directory events reconcile the owner's folder scope. Root changes preserve other owners' documents, and hidden-directory exclusions also apply to event updates. Shutdown cancels paused/manual indexing before joining workers.

## Task 4: Expanded retrieval

Files: new `search/chunks.py`, `search/semantic.py`, `search/expansion.py`, storage migration, searcher/facade, desktop controls/preview, CLI/MCP options, `tests/unit/search/test_expanded_search.py`.
Interfaces: `chunk_text(text) -> list[Chunk]` returns records with offsets; `SemanticIndex.rebuild()/search()` uses a local dense model; explicit `search_mode='literal'|'expanded'|'hybrid'`, local JSON synonym dictionaries with folder/file-type scopes, fuzzy distance-one candidates.

- [x] Add boundary/offset, semantic revision, filtered retrieval, synonyms, typos, identifiers, and literal-count regressions.
- [x] Confirm missing behavior.
- [x] Implement transactional vector metadata, stale-revision checks, real optional multilingual provider, exact-preserving rank fusion and deterministic pagination.
- [x] Expose options consistently; verify missing model is actionable and literal search remains usable.

Verified provider: local `intfloat/multilingual-e5-small`, revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, with query/passage prefixes and a maximum 512-token limit. Vectors publish only for an unchanged document revision; background maintenance reuses unchanged segments. Semantic retrieval returns at most 1,000 candidate passages. Related passages retain original offsets and do not fabricate literal occurrence counts; synonym previews use actual stem-aware match locations.

## Task 5: Validation and delivery

Files: README, changelog, benchmark/validation record, dependency/packaging configuration.

- [x] Run full pytest, Ruff format/lint, mypy and coverage; inspect the final diff and record the pre-existing repository-wide formatting failures.
- [x] Measure keyword benchmark budgets and watcher latencies; record real OCR/model checks and limitations honestly.
- [x] Obtain independent final review and fix material findings with regressions.
- [x] Leave completed changes reviewable in the shared workspace; no release or deployment requested.

Independent review findings were fixed with regressions: paused-index shutdown, mixed/native OCR duplicates, partial-native-token omissions, hidden-directory event scope, folder ownership changes, hidden master shapes and synonym passage offsets. Final review reported no blocking findings.

## Acceptance scope and remaining verification

Literal search checks intact original segments, so embedding chunk boundaries do not truncate keyword matches. The fixtures verify supported sources; they do not prove perfect OCR or universal extraction of embedded Office objects. Threaded comments, unsupported objects and missing saved formula results remain diagnosed omissions.

The five-second acceptance target is verified for small native-text updates with a running, available worker. Large documents, OCR, initial scans, model startup and queues can take longer; diagnostics expose pending work and failures. Tesseract/language data and a local semantic model require separate setup.

The following checks were not executed and are not claimed complete:

- [ ] Interactive desktop acceptance session; current GUI evidence comes from Qt offscreen tests.
- [ ] Windows/Linux runtime OCR and watcher validation; dependency wheel checks do not replace runtime tests.
- [ ] Produce and test frozen installers, including the optional semantic build and external OCR/model setup.
- [ ] Large-corpus semantic throughput and cold-model startup benchmarks.

These are follow-up validation items. The user requested committing the completed implementation to local `main`. No remote push or release was requested.
