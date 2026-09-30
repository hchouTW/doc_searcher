# Search completeness validation — 2026-10-01

Implemented on `feature/search-completeness`, starting at `e625e31`.
Specification: [search-completeness task](../tasks/search-completeness.md).
Plan: [implementation plan](../superpowers/plans/2026-10-01-search-completeness.md).
Measurements: same macOS arm64 machine, Python 3.13.2, SQLite 3.51.1; deterministic seed 20260924. Temporary synthetic indexes only. No dependencies or data assets added.

## Acceptance evidence

| Criteria | Evidence |
| --- | --- |
| A1–A2 | `tests/unit/search/test_completeness.py`: compound and single-character Chinese, contiguous verification, boolean segment scope, mixed OR/NOT, quoted operators and empty phrases. Existing folding/stemming/punctuation/phrase tests pass. |
| A3–A4 | Seven-segment starvation regression, stable document-ID tie breaking, cursor continuation and stale-revision rejection; desktop continuation loads 200 then 202 unique documents in all four language/theme combinations. |
| A5 | Migrations 4/5 rebuild from stored text, deleted originals remain unnecessary, injected failures roll back, legacy quality is unknown. Integration migration tests cover reopening and equivalent retrieval. |
| B1–B3 | Eight occurrences with one snippet; ten occurrences with only three rendered contexts; overlap/repetition/adjacency/NOT/phrase and emoji original offsets. Navigation loads all eight occurrences. XLSX sources survive reopening; cross-cell phrases report both cell sources. |
| B4 | Bounded location/context operations, preview HTML under 10 KB for a 100 KB segment, cancelled/stale callbacks, index revision invalidation, streaming dense and zero-width regex counting with deadlines. |
| C1–C2 | Ordered DOCX XML, shared headers/footers and text boxes, hidden/merged XLSX cells, formula/cache labels and missing-cache warnings. Distinct sources retained, shared source references deduplicated. No formula or macro execution. |
| C3 | Persisted success/no-text/partial/failure/unknown, partial PDF retains readable pages and failed page locations; selected-only reprocessing handles unchanged size/mtime and respects scope/exclusions. UI/CLI/MCP expose diagnostics and reprocessing. |
| V1 | Targeted RED→GREEN evidence for each phase; phase search 142 passed, parser/integration 96 passed/1 xfailed, UI/locations 66 passed. Full suite: 567 passed, 5 skipped, 2 xfailed; coverage 88.31% (82% required). Ruff and mypy pass (54 source files). Benchmarks: 13 passed; explicit 10k budget: 1 passed/4 deselected. |

Initial regressions failed before implementation: phase A 7 failures; B 7 failures; incremental API/UI 3 failures; Office fixtures 3 failures; quality persistence 3 plus missing service/CLI operations. Extra phrase regression corrected the original full phrase interval; quoted operators/empty phrases produced four failures before their fix. Theme-switch preservation failed after stale-preview changes: syntax rehighlighting emits document changes; input now emits query changes only when actual text differs.

## Before/after benchmarks

Full [before](search-completeness-before-benchmark.json) and [after](search-completeness-after-benchmark.json) pytest-benchmark artifacts include timing distributions and environment metadata. Medians:

| Operation | Before | After |
| --- | ---: | ---: |
| Cold index 1k | 1.648 s | 1.952 s |
| Cold index 10k | 19.401 s | 21.056 s |
| No-change rescan | 11.17 ms | 10.36 ms |
| One-file update | 14.53 ms | 15.94 ms |
| Cancellation | 2.36 ms | 1.88 ms |
| 200-page PDF parse | 39.95 ms | 39.55 ms |
| 5 MB text parse | 6.27 ms | 5.97 ms |
| 20k-row XLSX parse | 401.98 ms | 1030.39 ms |
| Chinese `預算`, 1k | 16.36 ms | 36.53 ms |
| English `budget`, 1k | 22.83 ms | 44.97 ms |
| Mixed boolean, 1k | 25.90 ms | 69.23 ms |
| English phrase, 1k | 4.76 ms | 16.35 ms |
| Regex, 1k | 18.43 ms | 16.67 ms |

XLSX now streams both formula and cached-value workbooks and records cell spans, explaining its additional cost. Search verifies candidates and counts true occurrences; earlier timings counted only rendered snippets. These are measurable slowdowns. Common-search relative 1.25× proposals are exceeded; those proposals were never approved. Approved absolute budgets remain unchanged and pass: 1k indexing ≤6 s/query ≤200 ms; 10k ≤90 s/query ≤500 ms; macOS RSS ≤300/400 MB. The dedicated 10k robustness test also passes.

## Recall, index size, counting and context

The [measurement driver](search-completeness-measure.py) adds `教師升等級審查` and `都市計畫` to ten corpus files and compares original folded text with indexed results. [Before 1k](search-completeness-before-1k.json), [after 1k](search-completeness-after-1k.json), [before 10k](search-completeness-before-10k.json), [after 10k](search-completeness-after-10k.json).

| Measurement | Before 1k | After 1k | Before 10k | After 10k |
| --- | ---: | ---: | ---: | ---: |
| `升等` recall | 0/10 | 10/10 | 0/10 | 10/10 |
| Indexed compound lookup | — | 1.09 ms | — | 1.08 ms |
| Folded stored-text scan | 79.7 ms | 78.8 ms | 771 ms | 777.7 ms |
| Database bytes after WAL checkpoint | 7,671,808 | 8,458,240 | 76,034,048 | 83,353,600 |
| CJK FTS index bytes | 0 | 774,144 | 0 | 7,180,288 |
| Full `計畫` enumeration under tracemalloc | 100 ms | 499 ms | 1088 ms | 4998 ms |
| Reported matches | 2,180 snippets | 5,157 occurrences | 21,813 snippets | 51,413 occurrences |
| Enumeration peak Python heap | 4.84 MB | 3.29 MB | 49.10 MB | 33.02 MB |
| Process peak RSS | 131.8 MB | 109.8 MB | 198.7 MB | 174.2 MB |
| First 100-document page | — | 26.5 ms | — | 168.3 ms |
| 100-location request, selected short document | — | 0.058 ms | — | 0.063 ms |
| One original-text context | — | 0.013 ms | — | 0.014 ms |

The added database size is 10.25% at 1k and 9.63% at 10k. Checkpointing truncates WAL, so recorded database bytes represent total persistent database+WAL for this measurement. RSS is a process-lifetime upper bound including native allocations; tracemalloc observes only Python allocations and slows enumeration. Count timings are one instrumented run, not normal-query medians; ordinary 10k queries with 200 results measure 173/232/331 ms (Chinese/English/mixed). Context measurements use a short corpus document and do not guarantee the same latency for a multi-megabyte segment. Location pages bound output but enumerate the segment again to compute exact totals.

Suggested new thresholds, **pending agreement**: added database size ≤15% on this corpus; instrumented full counting of these 10k documents ≤7 s and ≤50 MB Python heap; one selected short-document context ≤10 ms. No existing budget was relaxed.

Reproduce from the repository root (archive the base revision separately; do not switch this checkout or touch the live index):

```bash
mkdir -p /tmp/doc-searcher-completeness-base
git archive e625e31 src tests/benchmarks | tar -x -C /tmp/doc-searcher-completeness-base
venv/bin/python docs/benchmarks/search-completeness-measure.py /tmp/doc-searcher-completeness-base before 1000
venv/bin/python docs/benchmarks/search-completeness-measure.py "$PWD" after 1000
# Repeat with 10000. Driver leaves synthetic temp directories for inspection.
DOC_SEARCHER_BENCH_10K=1 venv/bin/python -m pytest tests/benchmarks --benchmark-only
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest -q --cov=doc_searcher
venv/bin/ruff check .
venv/bin/mypy
```

The driver is a macOS/POSIX measurement artifact (`resource`, macOS RSS units), not a runtime dependency. Cold indexing includes creation of the gram index. Stored-text schema 3→5 upgrade on consistent synthetic copies took 0.254 s at 1k and 2.558 s at 10k, including the existing backup mechanism; no originals were reparsed. These are single-run measurements; a migration latency budget remains unapproved.

## Existing-index comparison

Opened the live index read-only and made a consistent SQLite backup. Compared baseline schema 3 with migration to schema 5 **only on the temporary copy**, without reparsing originals. The live index remains schema 3. No paths or document contents are retained in these records.

| Query | Before documents / snippet count | After documents / occurrences | Folded original-text oracle |
| --- | --- | --- | --- |
| `會議` | 44 / 127 | 44 / 690 | 44 documents |
| `計畫` | 88 / 606 | 94 / 2874 | 94 documents |

After queries took 0.504/0.567 s for full enumeration on that copy. Counts describe this snapshot, not permanent acceptance constants.

## Desktop and limitations

Offscreen interaction and visual checks cover Traditional Chinese/English and light/dark: individual occurrence 8/8, loaded-results continuation, quality lists, forced reprocessing and stale deselection. Preview HTML remains bounded. Synthetic screenshots were inspected; no private document content was used.

No OCR, decryption, macro/formula evaluation, synonym or regional vocabulary expansion. Character folding matches `計畫`↔`计画`; `计划` is a different vocabulary form. Unsupported embedded Office objects report warnings. DOCX location is part/paragraph, never a fabricated layout page. Existing text-only schema migration cannot recover omitted Office sources; explicit reprocessing is needed. Broad queries compute exact document totals and revisit candidate text; paging saves rendering/counting of unselected documents, not candidate evaluation. English stemming is retained.

Validated macOS arm64/Python 3.13. Windows/Linux and frozen application builds were not run in this session; no added dependency or asset triggered the spec's frozen-build requirement. Release version remains the maintainer's decision.

## Implementation decisions

- Local feature branch in the existing checkout: preserves the authorized native workspace; cost if wrong: move changes to a worktree.
- Character-only script folding: preserves the established contract; cost if wrong: add regional vocabulary separately.
- Exact computed document totals: verified candidates already need traversal for stable ranking; cost if wrong: extra broad-query summary work.
- Contiguous Chinese compounds replace the old scattered-word assertion: required by the specification; cost if wrong: users must insert spaces for AND.
- Formula source and distinct caches are separate labeled text; equivalent constant caches remain metadata: prevents duplicate counts; cost if wrong: consumers must inspect metadata for that cache.
- Selected-only reprocessing preserves unselected documents: scope and reconciliation remain protected; cost if wrong: other changed files wait for normal refresh.
