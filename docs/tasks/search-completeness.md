# Improve Document Search Completeness, Match Counts, and Text Extraction

Created: 2026-10-01  
Updated: 2026-10-01\
Repository: `/Users/hchou/AgenticAI/doc_searcher`  
Status: Phases A–C implemented; acceptance evidence recorded through commit `00cba7b`. [C]\
Purpose: Retain the implementation requirements and link their verification evidence for review and future regression checks.

Evidence labels: **[C] Confirmed**, **[I] Inferred**, **[TBD] Unresolved**. The Technical Approach and Acceptance Criteria preserve the task's requirements; the implementation summary and linked validation record describe the delivered behavior and recorded checks.

## Implementation Status

- **A — Recall and pagination:** Chinese character n-gram candidates are verified against original text. Documents are ranked before pagination, with stable ordering and revision-aware continuation. Migration 4 builds the candidate index from stored text. [C]
- **B — Occurrences and preview:** Match counts represent occurrences, independently of rendered snippets. Paginated locations and bounded context requests support individual occurrence navigation through desktop, CLI, and MCP. [C]
- **C — Extraction and quality:** DOCX headers/footers/text boxes and XLSX formula/cache sources retain location metadata. Migration 5 persists quality, warnings, parser versions, and source spans; selected documents can be explicitly reparsed. [C]

The [validation record](../benchmarks/search-completeness-validation.md) maps A1–V1 to regression and interaction evidence. Its final recorded suite reports **572 passed, 5 skipped, 2 xfailed**, coverage **88.48%**, passing Ruff/mypy checks, and passing approved absolute benchmark budgets. These are recorded implementation results, not a new test run for this document update. [C]

Validation covered macOS arm64/Python 3.13 and offscreen desktop checks in both languages and themes. Windows/Linux and frozen builds remain unverified. New counting/index-size thresholds remain proposals; release version selection remains with the maintainer. [C]

## Background

The original user report described search results lacking detail and omitting content. Before this task, the pipeline was document parsing → original-text segments → Traditional/Simplified folding and jieba tokenization → SQLite FTS5 → document aggregation → snippets and preview. The findings below describe that baseline, not the implemented behavior. [C]

A read-only investigation on 2026-09-30 confirmed:

| Issue | Evidence | Interpretation limits |
| --- | --- | --- |
| Missing Chinese matches | Original indexed text contained `計畫` or `计画` in 94 documents, but FTS returned only 88 candidate documents. The compound `都市計畫` did not supply the `计画` token required by the query. | These counts describe that index snapshot, not a permanent expected document count. |
| Missing snippets | The snippet function examines only the first three matches and then discards overlapping contexts. A fixture containing ten occurrences produced only one snippet. | The full text may still be present in the index. |
| Incorrect match counts | `total_matches` counts snippets. The matching Excel worksheet shown in the screenshot contained eight occurrences of `會議`, while the UI displayed one hit. | Document counts, segment counts, occurrence counts, and snippet counts must be separate. |
| Truncated candidates | SQL limits results to `limit * 3` segments before aggregating documents. A reproduction requesting two documents returned only one. | Multiple matching segments in a long document can crowd out other documents. |
| Incomplete Office extraction | DOCX extraction omits headers and footers. XLSX extraction reads only cached values with `data_only=True`. | Reproductions confirmed a missing DOCX header keyword and an uncached formula being parsed as empty text. |
| Unclear extraction quality | Of 6,459 documents without recorded parse errors, 44 contained no searchable text. Four additional documents had parse errors. | A document without text may be blank or contain only images; this alone does not prove a parser failure. |

The baseline's relevant tests passed (39 tests), while these defects remained reproducible. [C] Targeted regressions and their recorded failing/passing results are linked in the validation record.

## Objective

Search extracted text without omissions caused by Chinese token boundaries. Return the correct documents, all match locations, and accurate occurrence counts. Clearly report incomplete extraction, paginated results, and processing failures so users can distinguish complete results from partial results.

## Scope

### In Scope

- Chinese literal-query recall, Traditional/Simplified matching, and original-text highlighting.
- Document ranking, pagination, and retrieval of all matching segments.
- Match-location counts, incremental snippet loading, and occurrence-by-occurrence navigation.
- PDF native-text extraction quality reporting, DOCX headers/footers/text boxes, and XLSX formulas/cell locations.
- Persistent parsing status, explicit document reprocessing, index upgrades, and compatibility.
- Shared search contracts and documentation for the desktop UI, CLI, and MCP.
- Regression tests, performance measurements, and reproducible validation records.

### Out of Scope

- Image-to-text recognition and extraction of text visible only inside images.
- Semantic vector search, LLM summaries, synonym expansion, or cloud document uploads.
- Document decryption or bypassing passwords or permissions.
- Replacing existing English stemming or regular-expression syntax.
- Guaranteeing extraction from unsupported formats or every embedded Office object; document unsupported content explicitly.
- Automatically merging documents with identical filenames at different paths.

## Repository Context

The following paths are relative to the repository root and have been verified to exist. [C]

| Area | Files and responsibilities |
| --- | --- |
| Search | `src/doc_searcher/search/searcher.py`: candidate verification, document ranking/pagination, occurrence counts, and location/context requests; `src/doc_searcher/search/query.py`: boolean query parsing; `src/doc_searcher/search/cjk_index.py`: character n-gram candidates |
| Tokenization and snippets | `src/doc_searcher/search/text_helper.py`: `tokenize_for_fts`, highlighting, and snippet truncation |
| Script folding | `src/doc_searcher/search/script_fold.py`: preserve existing folding semantics |
| Search interfaces | `src/doc_searcher/search/search_service.py`, `src/doc_searcher/cli.py`, `src/doc_searcher/integrations/mcp_server.py` |
| UI | `src/doc_searcher/desktop/main_window.py`, `src/doc_searcher/desktop/preview_panel.py`, `src/doc_searcher/desktop/result_table.py`, `src/doc_searcher/desktop/worker.py`, `src/doc_searcher/desktop/i18n.py` |
| Storage and migrations | `src/doc_searcher/storage/database.py`, `src/doc_searcher/storage/migrations.py`: schema 5; migration 4 adds CJK candidates/revision tracking, migration 5 adds extraction quality/source metadata |
| Parsers | `src/doc_searcher/parsers/base.py`, `src/doc_searcher/parsers/pdf_parser.py`, `src/doc_searcher/parsers/docx_parser.py`, `src/doc_searcher/parsers/xlsx_parser.py` |
| Indexing | `src/doc_searcher/indexing/indexer.py`, `src/doc_searcher/indexing/scanner.py`, `src/doc_searcher/indexing/service.py`: normal modification-time/file-size checks plus explicit selected-path reprocessing |
| Tests | `tests/unit/search/`, `tests/unit/parsers/`, `tests/test_ui_features.py`, `tests/test_search_service.py`, `tests/test_mcp_server.py`, `tests/integration/`, `tests/search_plan/` |
| Performance | `tests/benchmarks/`, `docs/benchmarks.md`, `docs/test-plan.md` |
| Runtime and packaging | `pyproject.toml`, `packaging/`, `.github/workflows/tests.yml`, `.github/workflows/build.yml` |

The older task `docs/tasks/simplified-traditional-search.md` describes an earlier project state. Script folding and English stemming now exist. Do not copy its outdated statements about missing features or schema versions. [C]

## Technical Approach

The implementation followed A → B → C in separately reviewable increments, with failing reproductions before fixes. The requirements below remain the contract for future changes; execution details are in the [implementation plan](../superpowers/plans/2026-10-01-search-completeness.md) and validation record. [C]

### A. Fix Chinese Recall and Candidate Truncation

1. Define the Chinese query contract: each Chinese literal term must occur contiguously within one searchable segment. Whitespace-separated terms retain AND semantics; explicit OR and NOT retain boolean semantics. Preserve segment-level scope rather than introducing cross-page AND matching.
2. Create fixtures containing `都市計畫` and `教師升等級審查`. Queries for `計畫` and `升等` must match regardless of token boundaries. Noncontiguous occurrences of the constituent characters must not match the literal term.
3. Combine the existing token-index candidates with character n-gram candidates or an equivalent index, then verify against original text. Single-character queries also need a candidate source; support must not be limited to terms of two or more characters.
4. A full-text LIKE scan may serve as a correctness oracle and transitional implementation. Before selecting the production approach, measure recall, latency, and index size on the 1k and 10k datasets. Partial FTS recall must not be reported as a complete search.
5. Preserve original text and highlighting offsets; folding is for matching only. Keep existing contracts for English stemming, case sensitivity, whole-word matching, punctuation, phrases, and regex mode.
6. Handle AND, OR, and NOT correctly in compound queries. Negated terms must not contribute positive occurrences. Do not truncate candidate unions, intersections, or differences before evaluating the query conditions.
7. Rank and paginate documents first, then retrieve all matching segments for selected documents. Increasing the `limit * 3` multiplier is not an acceptable fix.
8. Add a stable document-ID tie-breaker and continuation/completeness information. If the total has not been computed, display a loaded-result count rather than presenting it as the total number of matching documents.
9. If a new index is introduced, use the next available migration version and rebuild from `doc_segments.content` through the existing backup/transaction mechanism. Fixing token recall must not require rereading all original files.

### B. Separate Matches, Snippets, and Preview Navigation

1. Define match-location records containing document ID, segment ID/type, original-text `start`/`end`, matched term, and format-specific location information. Use Python string character indices with half-open intervals `[start, end)`.
2. Deduplicate matches at the same location and merge overlapping positive highlight intervals into one occurrence. Adjacent, nonoverlapping matches remain separate occurrences. Encode this contract in tests.
3. Make `total_matches` the actual occurrence count. Report segment and snippet counts separately. Negated terms and tokenized components of a quoted phrase must not inflate the occurrence count.
4. Separate match enumeration from snippet rendering. Initial previews may show only a few snippets, but snippet limits must not affect counts or document inclusion.
5. Continue scanning matches until the requested number of nonoverlapping snippets is produced or matches are exhausted. Do not stop after examining the first three matches.
6. Load match locations and original-text segments incrementally after document selection. Avoid rendering a large document as one HTML payload. Previous/next controls must navigate individual occurrences and highlight the active occurrence.
7. If one snippet contains multiple matches, navigation must address each occurrence separately, and counts must agree with the result list. Provide access to additional context or the full segment text.
8. Cancel or ignore stale requests when switching queries/documents. Report or reload invalidated locations after a document update/deletion. Keep expensive work off the UI thread.
9. Preserve regex time budgets and cancellation. If counting is incomplete, report incomplete/timed-out status instead of presenting a partial count as complete. Explicitly document and test zero-width match counting/navigation behavior.
10. Apply the same match contract to CLI and MCP. Keep existing calls usable when introducing pagination or location operations. Document the compatibility implications of correcting `match_count` from snippet count to occurrence count.

### C. Improve Office Extraction and Quality Reporting

1. Preserve DOCX body/table order and extract headers, footers, and text boxes. Restrict XML extraction to explicit text nodes to avoid duplicate body extraction or treating hidden markup as document text.
2. Deduplicate header/footer parts shared by multiple sections. Preserve source type and part/paragraph location. Do not invent Word layout page numbers that python-docx cannot provide.
3. Read XLSX formula source and cached values separately. If a cached result is missing, preserve the formula and report a missing-cached-result warning. Do not silently treat it as blank or claim to have evaluated it.
4. Do not introduce arbitrary formula or macro execution. Label searchable formula source and displayed values clearly, and avoid duplicate counts for the same source.
5. Preserve worksheet, cell, or range locations such as `Sheet1!B12`. Extract merged-cell values from their actual source cell only. Include hidden worksheets consistently with the existing worksheet traversal behavior.
6. Persist extraction-quality information distinguishing success, no text, partial extraction, and parse failure, with warnings and omitted locations. Naming may vary, but these outcomes must not all be classified as success.
7. For PDF page-level exceptions, preserve readable pages and record failed page numbers rather than silently continuing. Mark the document as partially extracted.
8. Show quality summaries and problem-document lists in UI/CLI/MCP. Distinguish discovered files from files containing searchable text. For older indexes without sufficient quality metadata, report unknown quality rather than assuming completeness.
9. Add an explicit path for forcing selected documents to be reparsed even when modification time/file size are unchanged. Record parser versions or equivalent reprocessing eligibility so extraction improvements can be applied to existing documents.

## Deliverables

- [x] A: Chinese recall and document-pagination fixes, migration if needed, and regression tests.
- [x] B: Accurate match-location model, incremental preview/navigation, and interface compatibility documentation.
- [x] C: Office extraction improvements, extraction-quality status, explicit reprocessing, and tests.
- [x] README, Traditional Chinese/English help, and changelog updates; the maintainer selects the release version.
- [x] Validation record covering reproductions, before/after results, test output, performance/index size, and known limitations.

## Acceptance Criteria

| ID | Verifiable completion condition |
| --- | --- |
| A1 | An index containing `都市計畫` and `教師升等級審查` returns matches for `計畫` and `升等`, respectively; `升 … 等` does not match `升等`. |
| A2 | Traditional/Simplified, single-character Chinese, phrase, AND/OR/NOT, punctuation, and existing English tests pass. NOT terms do not produce positive occurrences. |
| A3 | With seven highly ranked matching segments in one document and one lower-ranked segment in another, document limit=2 returns both documents. All seven segments of the first document are returned or can be fully loaded. |
| A4 | Results spanning multiple pages support continuation without duplicates or omissions, with stable ordering and a visible indication of remaining results. |
| A5 | Fresh and migrated indexes return equivalent results. Search indexes can be rebuilt from stored text without original files, and injected migration failures roll back cleanly. |
| B1 | A worksheet containing eight occurrences of `會議` reports eight matches, and navigation reaches all eight original-text locations. |
| B2 | A fixture with three nearby initial occurrences and seven separated later occurrences reports ten matches; later matches can be loaded and navigated. |
| B3 | Phrase, overlapping-term, repeated-term, and NOT counts follow the phase B contract. Original offsets remain correct around emoji and Traditional/Simplified text. |
| B4 | Large documents load incrementally; switching queries does not show stale results. Regex cancellation/timeouts do not report falsely complete counts. |
| C1 | Keywords appearing only in DOCX headers, footers, or text boxes are searchable with identifiable sources; shared parts are not extracted repeatedly. |
| C2 | XLSX formulas with and without cached values have identifiable sources. Missing caches produce warnings, results identify cell locations, and no formula execution is required. |
| C3 | Blank/no-text, partial, and failed extraction statuses can be inspected. Explicit reprocessing works when modification time/file size are unchanged. |
| V1 | Phase-specific and full regression tests pass, with before/after measurements for added indexes and actual occurrence counting. |

All three phases must meet their acceptance criteria before this task is reported as complete. The [acceptance evidence table](../benchmarks/search-completeness-validation.md#acceptance-evidence) records coverage for every criterion above; its [final independent review](../benchmarks/search-completeness-validation.md#final-independent-review) describes four resolved review findings and the final recorded checks. [C]

## Validation

Recorded results, before/after artifacts, reproduction commands, and measurement caveats are in the [validation record](../benchmarks/search-completeness-validation.md). Retain the commands below for subsequent regression checks.

Run from the repository root using the existing `venv/bin/python`. If the environment is unavailable, follow the README installation instructions. Never clear or overwrite `~/.doc_searcher/index.db` for validation. Automated tests must use temporary directories and synthetic documents. Investigate a real index read-only or through a consistent copy created with the SQLite backup API.

```bash
cd /Users/hchou/AgenticAI/doc_searcher

# Confirm new reproductions fail before fixing, then run relevant tests after fixing.
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/search tests/test_searcher.py tests/test_search_service.py tests/test_mcp_server.py -q
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/parsers tests/test_parsers.py tests/integration tests/test_ui_features.py -q

# Full regression suite; benchmarks are excluded from the normal test run.
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest -q
venv/bin/python -m pytest tests/benchmarks --benchmark-only --benchmark-json=/tmp/doc-searcher-completeness-bench.json
DOC_SEARCHER_BENCH_10K=1 venv/bin/python -m pytest tests/benchmarks --benchmark-only
```

- Compare before/after measurements on the same machine, Python/SQLite versions, and generated dataset. Record database size and peak memory.
- Validate against the existing platform budgets in `docs/test-plan.md`. Some relative budgets in `docs/benchmarks.md` remain proposals; do not represent them all as approved. Budgets for full occurrence counting and added index size are TBD; measure and report first.
- Add a Chinese literal-query oracle that checks original synthetic text according to the query contract and compares it with indexed results. Boolean and phrase cases require their respective semantics, not only single-term comparisons.
- Manually check light/dark themes and Traditional Chinese/English interfaces: eight-occurrence navigation, continuation, quality lists, and explicit reprocessing.
- Compare real-index queries for `會議` and `計畫` read-only. Do not require fixed counts of 44 or 94 documents; recompute baselines when the underlying data changes.
- If dependencies or data assets are added, validate frozen builds through the existing build workflow. Clearly identify platforms that were not tested.

## Open Questions

1. **[TBD] Approval of new performance thresholds:** Recorded proposals are added database size ≤15% on the measured corpus, instrumented full counting of 10k documents ≤7 s and ≤50 MB Python heap, and selected short-document context ≤10 ms. These are pending agreement, not approved budgets. Existing approved absolute budgets pass in the recorded measurements; proposed relative 1.25× search budgets are exceeded. See the validation record for measurement conditions.
2. **[TBD] Release version:** The maintainer selects the release version. Existing entry points are retained; the corrected occurrence-based `match_count` requires updates in clients that relied on snippet counts, as documented in `README.md` and `CHANGELOG.md`. [C]

## Known Limitations

- Character folding supports `計畫` ↔ `计画`; regional vocabulary conversion such as `計畫` ↔ `计划` remains outside scope.
- Stored-text migration restores search recall but cannot recover omitted Office text. Existing documents need explicit reprocessing to populate new sources and extraction-quality metadata; legacy quality remains unknown until then.
- DOCX footnotes/endnotes and some embedded Office objects remain unsupported. OCR, decryption, and formula/macro execution remain excluded.
- Broad queries still evaluate candidates to compute exact document totals. Location pages bound output but rescan segment text for exact counts; paging does not guarantee constant-time retrieval for large segments.
- Platform and packaging verification is limited to the recorded environment above.

These limits are documented in the validation record and do not imply extraction of every visible source or approval of the proposed performance thresholds. [C]

## References

- `docs/test-plan.md`: search, preview, robustness, and platform performance budgets.
- `docs/benchmarks.md`, `docs/benchmarks/baseline-macos-arm64-py313.json`: existing performance measurements.
- `docs/adr/0002-simplified-traditional-folding.md`, `docs/adr/0003-english-stemming.md`: existing matching contracts.
- Source and tests listed in Repository Context.
- [Search completeness validation](../benchmarks/search-completeness-validation.md): acceptance evidence, baseline findings, before/after benchmarks, existing-index comparison, resolved review findings, and limitations.
- [Implementation plan](../superpowers/plans/2026-10-01-search-completeness.md): phase contracts and execution checklist.
- `README.md`, `CHANGELOG.md`: user-facing behavior and compatibility notes.
- Authoring references: task-authoring task-template, acceptance-criteria, and task-quality-checklist.
