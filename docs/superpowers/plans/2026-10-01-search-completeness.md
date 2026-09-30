# Search Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Return complete Chinese literal-query results, accurate occurrence counts and navigation, and inspectable extraction quality across desktop, CLI, and MCP.

**Architecture:** Keep the existing porter/jieba index for English matching and ranking. Add a folded Chinese character/bigram candidate index, verify boolean conditions within original-text segments, and paginate verified documents. Enumerate occurrences independently of snippets, load preview context on demand, and persist parser quality and source metadata.

**Tech Stack:** Python 3.10–3.14, SQLite FTS5, existing script folding and stemming helpers, PySide6, python-docx, openpyxl, PyMuPDF, pytest/pytest-benchmark. No new runtime dependencies.

**Spec:** `docs/tasks/search-completeness.md`

## Global Constraints

- Execute A → B → C; all three phases must meet acceptance criteria before declaring the task complete.
- Chinese literal terms are contiguous within one searchable segment. Whitespace-separated terms retain AND semantics; explicit OR and NOT retain boolean semantics.
- Preserve existing English stemming, case sensitivity, whole-word matching, punctuation, phrases, and regex syntax/time budgets.
- Matching offsets are Python string character indices with half-open intervals `[start, end)` in original text.
- Never clear or overwrite `~/.doc_searcher/index.db` for validation. Use temporary fixtures and SQLite backup copies for real-index comparisons.
- Image-only text recognition, semantic search, formula execution, passwords, and unsupported embedded Office objects are outside scope.
- Keep existing search entry points usable. Correcting snippet-based counts is an intentional compatibility change; document it without choosing a release version.
- Use the next available migration versions, existing SQLite backup/transaction mechanism, and stored `doc_segments.content` for search-index rebuilds.
- Keep expensive search/context/reprocessing work off the UI thread; cancel or reject stale work.
- Existing approved platform budgets remain in force. New occurrence-count/index-size thresholds are proposals until agreed.
- Add/update file-level purpose/behavior/usage comments when changing logic, following existing repository conventions.

## Repository Findings and Decisions

- Baseline branch is `main`; only the user-supplied task file is untracked. Do not discard it or include unrelated work.
- Current migrations stop at version 3. Reserve 4 for recall/revision tracking and 5 for quality/source metadata; recheck before implementation.
- `searcher.search()` caps FTS rows at `limit * 3`; LIKE fallback also caps segments. Remove both caps before logical verification/document pagination.
- `extract_keywords_from_query()` includes NOT terms and phrase subcomponents. Replace its use for occurrence counting with parsed positive query terms.
- `tests/unit/search/test_unsegmented_cjk.py::test_segmentable_words_keep_and_semantics` currently expects `記錄會議` to match scattered words. Update that assertion to the specification's contiguous-term contract; `記錄 會議` continues to match.
- `ExtractedDoc` already defines detailed failure statuses. Extend that model with partial/unknown quality and warning/source metadata rather than replacing existing failure categories.
- Preview currently navigates snippets. Keep snippet fields for compatibility, but drive new navigation from original-text occurrences.
- Preserve standalone NOT's existing document-wide absence behavior and zero positive occurrences. Compound boolean expressions remain segment-scoped.
- Regex zero-width matches count as individual occurrences returned by the regex iterator, with a visible caret in context. They do not become empty `<mark>` elements; advance according to the regex engine's finite iterator semantics.

## Review Focus

1. Mixed Chinese/English OR branches and branch-local NOT: a candidate from another branch must not be incorrectly rejected; test in Task 1.
2. An update/deletion between result pages or context requests: reject the old continuation/location revision rather than omit or mis-highlight results; test in Tasks 1 and 3.
3. Repeated text in distinct table cells versus one merged cell: preserve distinct sources and suppress only repeated references to the same source; test in Task 4.
4. A partial parse carrying useful text and a warning: index useful segments and preserve quality instead of routing it through the existing all-or-nothing error branch; test in Task 5.
5. Large segments with dense or zero-width regex hits: bounded memory, deadline and cancellation must hold throughout counting and navigation; test in Tasks 2 and 3.

---

### Task 1: Phase A — Complete Candidate Retrieval and Document Pagination

**Files:**
- Create: `src/doc_searcher/search/query.py`, `src/doc_searcher/search/cjk_index.py`, `tests/unit/search/test_completeness.py`.
- Modify: `src/doc_searcher/search/searcher.py`, `src/doc_searcher/storage/database.py`, `src/doc_searcher/storage/migrations.py`, `tests/unit/search/test_unsegmented_cjk.py`, `tests/integration/test_database_migrations.py`.
- Extend benchmark coverage: `tests/benchmarks/test_benchmarks.py`.

**Interfaces:**
- Consumes: `fold(text)`, `script_regex(term)`, existing stemming helpers, `Database.get_connection()`.
- Produces: parsed query terms and boolean tree from `parse_query(query: str) -> ParsedQuery`; validated syntax preserves existing `SearchQueryError.code` values.
- Produces: `cjk_tokens(text: str) -> str`, encoding unique folded CJK unigrams/bigrams as ASCII FTS tokens; include supplementary CJK ranges, not just U+4E00–U+9FFF.
- Produces: `DocumentSearcher.search_page(query_str: str, *, cursor: Optional[str] = None, limit: int = 200, **options) -> SearchPage`; page fields are `items`, `next_cursor`, `has_more`, `total_documents` (nullable), `complete`, and `revision`.
- Preserves: `search(query_str, ...) -> List[SearchResultItem]` delegates to the first page with its original arguments/defaults.
- Produces: database index revision, incremented atomically by successful save/delete/clear operations. Cursors bind revision, query/filter fingerprint, and last `(rank_score, doc_id)`; mismatches raise `SearchQueryError(code="stale_cursor")`.

- [x] **Step 1: Write failing regressions and baseline measurement cases.** Assert `都市計畫`/`都市计划` match `計畫`, `教師升等級審查` matches `升等`, scattered/reversed characters do not, and single-character terms work. Add a folded literal oracle for Chinese terms, phrases, implicit/explicit AND, OR, and branch-local NOT. Assert `甲 OR 乙 NOT 丙` honors FTS boolean precedence. Assert one document with seven matching segments plus a second document returns both at limit=2, retaining all seven segments. Traverse tied-ranked documents across pages without duplication and reject stale cursors after save/delete. Record pre-change 1k/10k benchmark JSON and database sizes before product changes.
- [x] **Step 2: Run red tests.** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/search/test_completeness.py -q`. Expected: failures for missed compounds, segment truncation, and missing page contract. Preserve actual output in the execution ledger.
- [x] **Step 3: Implement candidate index and migration 4.** Add `doc_cjk_fts` with rowid equal to stored segment row ID and `unicode61` tokenization of encoded grams. Populate from stored content in bounded batches. Maintain it transactionally during save/delete/clear. Add revision storage. Candidate leaves use existing English FTS or intersect CJK gram postings; mixed/punctuation leaves must use a safe superset and original-text verification. Compute AND/OR set operations at segment scope; subtract only verified NOT matches. Full LIKE scanning is the correctness oracle and fallback for leaves without a safe indexed source. Never cap intermediate sets. Benchmark indexed versus scan candidates on 1k/10k before accepting the production path; if approved budgets fail, optimize without trading away recall.
- [x] **Step 4: Implement verified document ranking and paging.** Stream candidates and verify conditions. Use best existing BM25 segment score for each document; documents found only through grams receive a fixed neutral score. Sort by score then integer document ID; select page documents before retrieving their matching segments. Fetch one extra verified document to determine `has_more`; leave `total_documents=None` unless computed. Filename and standalone-NOT modes use stable ordering and apply options before paging. Preserve regex exceptions rather than publish a partial page as complete.
- [x] **Step 5: Verify migration behavior and existing contracts.** Fresh/migrated indexes must agree without source files; injected migration/index-write failure must roll back content, postings, version, and revision. Replace hardcoded migration-version assertions with explicit expected current versions where appropriate. Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/search tests/test_searcher.py tests/integration/test_database_migrations.py -q`. Expected: all pass. Record measurements and any intentional changed Chinese expectations.
- [x] **Step 6: Commit phase A.** Commit only Task 1 files and validation notes with message `fix: complete Chinese literal recall and paginate documents`.

### Task 2: Phase B — Occurrences Independent of Snippets

**Files:**
- Create: `src/doc_searcher/search/matches.py`, `tests/unit/search/test_match_locations.py`.
- Modify: `src/doc_searcher/search/searcher.py`, `src/doc_searcher/search/text_helper.py`, `tests/unit/search/test_snippets.py`, `tests/test_searcher.py`.

**Interfaces:**
- Consumes: `ParsedQuery` and matched positive branches from Task 1; original stored text and segment identity.
- Produces: `MatchLocation(doc_id, segment_row_id, segment_id, segment_type, start, end, matched_terms, source, revision)`; `source` is an optional format-location mapping.
- Produces: `iter_locations(content, positive_terms, *, regex_pattern=None, deadline=None, cancel_check=None)` yielding deduplicated, ordered original-text locations; merge overlapping intervals, keep adjacent ones separate.
- Extends: result fields `segment_count`, `snippet_count`, `count_complete`; `total_matches` becomes the true occurrence count. Keep existing segment/snippet fields and constructor compatibility through defaults.
- Produces: bounded context rendering from original segment text and location intervals; counts never depend on rendered snippets.

- [x] **Step 1: Write failing count/offset regressions.** Eight worksheet occurrences must count eight. Three close initial occurrences plus seven separated ones must count ten and produce three snippets when requested. Test repeated terms, overlapping terms, adjacent terms, a quoted phrase counted once, excluded terms counted zero, overlapping branch matches, emoji before folded Chinese, HTML escaping, and zero-width regex locations. Dense-regex counting must check cancellation/deadline without accumulating every hit in memory.
- [x] **Step 2: Run red tests.** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/search/test_match_locations.py tests/unit/search/test_snippets.py -q`. Expected: incorrect snippet-based totals and missing location APIs fail.
- [x] **Step 3: Implement shared occurrence enumeration.** Enumerate each original positive term without adding jieba subcomponents; retain existing English stem matching. Merge overlap with a streaming sweep and preserve contributing terms. Count selected-document occurrences without retaining all locations; regex must share one deadline across verification/counting/rendering. A timeout/cancellation raises the existing error and returns no falsely complete count. For filename queries, count actual filename occurrences; standalone NOT has zero.
- [x] **Step 4: Correct snippet selection.** Continue past overlapping contexts until the requested number of nonoverlapping snippets or exhaustion; bound rendered match length using existing `MAX_MATCH_CHARS`. Render every positive occurrence in a selected context, including a separate active occurrence. Handle zero-width positions with caret markup.
- [x] **Step 5: Verify phase B core.** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/search tests/test_searcher.py tests/search_plan -q`. Expected: all pass after intentionally updating tests that encoded snippet-based counts; budgets unchanged. Record full-count latency and peak memory.
- [x] **Step 6: Commit occurrence contract.** Commit Task 2 files with message `fix: count original-text occurrences independently of snippets`.

### Task 3: Phase B — Incremental Preview and Shared Interfaces

**Files:**
- Modify: `src/doc_searcher/search/searcher.py`, `src/doc_searcher/search/search_service.py`, `src/doc_searcher/integrations/mcp_server.py`, `src/doc_searcher/cli.py`, `src/doc_searcher/desktop/worker.py`, `src/doc_searcher/desktop/preview_panel.py`, `src/doc_searcher/desktop/main_window.py`, `src/doc_searcher/desktop/result_table.py`, `src/doc_searcher/desktop/i18n.py`.
- Test: `tests/test_search_service.py`, `tests/test_mcp_server.py`, `tests/test_cli.py`, `tests/test_ui_features.py`, `tests/search_plan/test_filters_preview.py`.

**Interfaces:**
- Consumes: `SearchPage`, `MatchLocation`, result occurrence/segment/snippet counts, index revision.
- Produces: `DocumentSearcher.match_locations(query_str, doc_id, *, offset=0, limit=100, revision, **options) -> MatchPage` with `locations`, `next_offset`, `total_matches`, `complete`, `revision`; limit bounds response payload, not counting accuracy.
- Produces: `DocumentSearcher.match_context(location, *, context_chars=120, full_segment=False) -> MatchContext`; verify identity/revision before returning text. Full-segment access is explicit and paginated by character range when large.
- Extends: `SearchService.search(query, formats=None, limit=20, *, cursor=None)` with continuation/completeness fields; deduplicate overlapping format groups and rank/page the union globally.
- Produces: service/MCP location and context operations; CLI `--limit`, `--cursor`, `--locations`, and `--context` optional operations while retaining existing `--dir ... --search ...` usage.
- Produces: Qt background preview requests with query/document/revision/request-generation identity and cancellation. UI renders only loaded context windows; previous/next navigates individual occurrence ordinals.

- [x] **Step 1: Write failing interface/UI tests.** Validate pagination and location/context serialization, global paging of multiple formats, eight-occurrence navigation within one snippet, later matches beyond the first three contexts, bounded preview HTML size for a large segment, zero-width caret navigation, stale response rejection after query/document switching, and invalidation after update/delete. Test timeout/cancellation through service and worker. Existing first-page calls retain their keys and usable snippets.
- [x] **Step 2: Run red tests.** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_search_service.py tests/test_mcp_server.py tests/test_cli.py tests/test_ui_features.py -q`. Expected: newly added operations/count/navigation assertions fail.
- [x] **Step 3: Implement incremental APIs and adapters.** Return bounded location pages and original context with revision checks; preserve MCP resource URI behavior and never load arbitrary disk paths. Avoid materializing full document HTML. Add desktop Load more, loaded-document/remaining indicators, active occurrence marking, context expansion, and translated stale-location/error messages. Cancel or discard prior context workers and close them safely on shutdown. Prevent a background index change from appending a stale result page.
- [x] **Step 4: Verify interfaces and navigation.** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/test_search_service.py tests/test_mcp_server.py tests/test_cli.py tests/test_ui_features.py tests/search_plan/test_filters_preview.py -q`. Expected: all pass. Verify Chinese/English and light/dark eight-occurrence navigation and pagination with a temporary configured data directory.
- [x] **Step 5: Commit phase B interfaces.** Commit Task 3 files with message `feat: load occurrence context and search pages incrementally`.

### Task 4: Phase C — Office Sources and Partial PDF Extraction

**Files:**
- Modify: `src/doc_searcher/parsers/base.py`, `src/doc_searcher/parsers/docx_parser.py`, `src/doc_searcher/parsers/xlsx_parser.py`, `src/doc_searcher/parsers/pdf_parser.py`.
- Create: `tests/unit/parsers/test_office_completeness.py`.
- Test: `tests/unit/parsers/test_parser_outcomes.py`, `tests/test_parsers.py`.

**Interfaces:**
- Extends: `PageSegment` with optional source spans mapping original text intervals to format locations; existing three positional arguments stay valid.
- Extends: `ExtractedDoc` with `warnings`, `omitted_locations`, `parser_version`; add `ParseStatus.PARTIAL` and `UNKNOWN`. Expected failures retain existing detailed categories.
- Produces: DOCX sources identify part/paragraph/table-cell/text-box without invented layout pages; XLSX sources identify worksheet/cell and `value` or `formula`; PDF warnings identify failed and no-native-text pages.

- [x] **Step 1: Write failing parser fixtures.** Generate DOCX with paragraph/table/paragraph ordering, linked shared headers/footers, distinct equal-valued cells, merged cells, and body/header text boxes. Assert each explicit `w:t` source appears once and metadata identifies it. Generate XLSX with eight worksheet occurrences, formula source plus a cached result (synthetic XML fixture), missing cache, merged cells, and a hidden worksheet. Assert source/cached-value distinction, warnings, source coordinates, and no formula execution. Inject a PDF page failure and assert remaining readable pages survive with partial status and failed page numbers; blank/image-only native text is reported honestly.
- [x] **Step 2: Run red tests.** Run `venv/bin/python -m pytest tests/unit/parsers/test_office_completeness.py -q`. Expected: missing Office sources and PDF partial-quality assertions fail.
- [x] **Step 3: Implement source-aware extraction.** Traverse DOCX body blocks in XML order and explicit text nodes, including text boxes without extracting them again through surrounding paragraphs. Deduplicate parts by part identity and merged cells by XML/source identity, never by equal text. Stream XLSX source and cached workbooks in parallel; build one canonical source stream with mapped spans for formula/value fields and no repeated copies. Keep worksheet segmentation so whitespace AND stays worksheet-scoped. Avoid changing original-text offsets by stripping after span generation. Record missing cache warnings and unsupported embedded objects. PDF retains readable text and page-level warnings.
- [x] **Step 4: Verify parser compatibility/performance.** Run `venv/bin/python -m pytest tests/unit/parsers tests/test_parsers.py -q`. Expected: all pass; format locations are correct for all eight occurrences. Run the existing large-file parser benchmarks, documenting the extra XLSX stream cost.
- [x] **Step 5: Commit parsers.** Commit Task 4 files with message `feat: extract Office sources and report partial PDF text`.

### Task 5: Phase C — Persist Quality and Force Selected Reprocessing

**Files:**
- Modify: `src/doc_searcher/storage/migrations.py`, `src/doc_searcher/storage/database.py`, `src/doc_searcher/indexing/indexer.py`, `src/doc_searcher/indexing/service.py`, `src/doc_searcher/search/search_service.py`, `src/doc_searcher/integrations/mcp_server.py`, `src/doc_searcher/cli.py`, `src/doc_searcher/desktop/worker.py`, `src/doc_searcher/desktop/main_window.py`, `src/doc_searcher/desktop/preview_panel.py`, `src/doc_searcher/desktop/i18n.py`.
- Test: `tests/integration/test_database_migrations.py`, `tests/integration/test_indexing_failures.py`, `tests/unit/indexing/test_indexing_service.py`, `tests/test_indexer.py`, `tests/test_search_service.py`, `tests/test_mcp_server.py`, `tests/test_cli.py`, `tests/test_ui_features.py`.

**Interfaces:**
- Consumes: parser warnings/status/version and source spans from Task 4; revisions and context operations from Tasks 1–3.
- Extends: `Database.save_document_index` with optional parse-status/warnings/omitted-locations/parser-version/source metadata, retaining existing parameters.
- Produces: migration 5 adds persisted quality metadata and segment source spans. Legacy rows receive `unknown`, never inferred success; legacy source spans are absent until reprocessing.
- Produces: `Database.problem_documents(*, offset=0, limit=100) -> dict` with quality/warnings/reprocessing eligibility and continuation; `get_stats()` adds discovered/searchable/status counts without dropping existing keys.
- Extends: `IndexRequest.force_paths: List[str]` defaults empty; `IndexingService.run` adds existing explicitly selected paths to indexing regardless of unchanged mtime/size, restricted to available configured scope and exclusions.
- Produces: `SearchService.start_reprocess(paths: Sequence[str]) -> dict`, asynchronous using existing write locking/reindex status; optional CLI `--reprocess PATH` (repeatable), MCP tool, and desktop selected-document/problem-list actions.

- [x] **Step 1: Write failing persistence/reprocessing tests.** Persist and reopen success/no-text/partial/failure metadata. Migrate a legacy no-error row to unknown. Partial documents remain searchable. Force a selected file with unchanged size/mtime to update its parser version, extracted content, sources, and revision; unselected/unavailable/excluded/out-of-scope paths remain protected. Test same-path overwrite, rollback, cancellation, and source invalidation. Problem lists distinguish discovered versus searchable files across UI/CLI/MCP.
- [x] **Step 2: Run red tests.** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/indexing tests/test_indexer.py tests/integration/test_indexing_failures.py tests/integration/test_database_migrations.py -q`. Expected: new metadata/force-path assertions fail.
- [x] **Step 3: Implement migration/storage/indexing.** Save quality and useful partial segments together transactionally; warnings do not become fatal `error` values. Preserve metadata on expected failures and parser-version eligibility for old files. Serialize bounded source metadata per segment and resolve occurrence source spans in context operations. Explicit reprocessing bypasses change detection only for selected paths; never changes reconciliation protections.
- [x] **Step 4: Implement quality/reprocessing presentation.** Show translated quality summary, problem-document list, source labels, warnings, and a selected-document reprocess action. CLI and MCP expose the same paginated metadata and asynchronous progress/error contract. Clearly label unknown quality and formulas with missing cached results.
- [x] **Step 5: Verify phase C end to end.** Run `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/unit/indexing tests/test_indexer.py tests/integration tests/test_search_service.py tests/test_mcp_server.py tests/test_cli.py tests/test_ui_features.py -q`. Expected: all pass. Verify forced reprocessing and quality lists in both languages/themes using synthetic documents only.
- [x] **Step 6: Commit phase C.** Commit Task 5 files with message `feat: persist extraction quality and reprocess selected documents`.

### Task 6: Documentation, Performance, and Completion Evidence

**Files:**
- Modify: `README.md`, `docs/benchmarks.md`, `docs/test-plan.md`, `src/doc_searcher/desktop/main_window.py`, `src/doc_searcher/desktop/i18n.py`, `docs/tasks/search-completeness.md` (checkboxes only when demonstrated).
- Create: `CHANGELOG.md` (no existing changelog found), `docs/benchmarks/search-completeness-validation.md`, before/after benchmark JSON artifacts under `docs/benchmarks/`.
- Modify tests as necessary: `tests/benchmarks/test_benchmarks.py`, `tests/search_plan/test_robustness.py`.

**Interfaces:**
- Consumes: completed A/B/C contracts and execution ledger evidence.
- Produces: reproducible acceptance mapping A1–A5, B1–B4, C1–C3, V1; measured budgets/proposals and documented limitations.

- [x] **Step 1: Update documentation/help.** Document contiguous Chinese term versus whitespace AND, page continuation and stale cursors, true occurrence `match_count`, segment/snippet counts, source offsets, zero-width regex, quality categories, unsupported content, formula/cache labels, and explicit reprocessing. Add an Unreleased changelog entry; leave release version selection to the maintainer.
- [x] **Step 2: Run required verification.** Run both phase-specific command groups and full `QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest -q`, plus `venv/bin/ruff check .` and `venv/bin/mypy` if available in the existing environment. Expected: all required checks pass; do not claim pass from a truncated/incomplete run. Resolve failures before continuing.
- [x] **Step 3: Measure 1k/10k and large files.** Run the spec's benchmark commands and compare same-machine before/after. Record recall against the oracle, latency medians, total DB plus WAL/index size, Python peak memory and process RSS limitations, cold index/rebuild cost, full occurrence count, and context-page latency. Preserve approved budgets; propose new size/count thresholds from measurements rather than treating them as approved.
- [x] **Step 4: Compare real-index queries safely.** Open the live database read-only for baseline `會議`/`計畫`; use SQLite backup to a temporary copy for upgraded comparison. Record dates and current counts, not historical constants. Do not reparse originals or mutate the live index for this check.
- [x] **Step 5: Verify desktop and packaging limits.** Exercise eight-occurrence navigation, late contexts, continuation, quality/problem lists and forced reprocessing in light/dark and Traditional Chinese/English. Keep a bounded preview payload and verify switching during requests. Record untested platforms honestly. No new dependencies/data assets are planned; if that changes, run existing frozen-build validation before completion.
- [x] **Step 6: Review and finish.** Use verification-before-completion and requesting-code-review; review the entire change against the spec, migrations, performance evidence and stale-state behavior. Resolve important findings with failing regressions then passing suites. Commit documentation/evidence with message `docs: document and validate search completeness`. Mark task deliverables only when all acceptance evidence exists; leave any unmet criterion explicitly open.

## Plan Self-Review

- A1–A5: Task 1, shared pagination adapters in Task 3, and measurements in Task 6.
- B1–B3: Tasks 2–4; B4: Tasks 2–3 and dense-hit benchmarks in Task 6.
- C1–C2: Tasks 4–5; C3: Task 5; V1: per-task red/green checks and Task 6.
- Shared interfaces flow forward: query/revision → occurrences → preview/API → source metadata → quality/reprocessing.
- Separate new migration versions preserve independently reviewable search and extraction increments.
- Execution recommendation: native implementation in this session; most tasks share contracts and should execute sequentially. One fresh whole-change review at the end.
