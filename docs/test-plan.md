# Search System Test Plan (Multi-Language & Multi-Format)

Evidence tags: **[C]** Confirmed (README / source / tests read), **[I]** Inferred, **[TBD]** Unresolved, needs owner confirmation.

## Background

DocSearcher is a local PySide6 desktop app that indexes documents into SQLite FTS5 with jieba tokenisation and searches them with BM25 ranking, phrase and boolean syntax, filename search, and a highlighted preview [C: `README.md`]. The requester supplied a QA plan draft (10 test cases). Checked against the repository, several of its assumptions do not hold:

| Draft assumption | Repository reality | Handling in this plan |
| --- | --- | --- |
| `.json`, `.log` supported | Registered parsers are `.pdf .docx .doc .pptx .ppt .xlsx .xls .txt .md .csv` [C: `src/doc_searcher/parsers/__init__.py:43-54`] | Decided 2026-09-30: **not supported**. Negative test **FMT-08**: must be ignored, not crash |
| Scanned-PDF OCR | README states "no OCR burden"; no OCR code found [C] | Decided 2026-09-30: **no OCR**. **PDF-03** expects zero hits, no crash, and a recorded reason |
| Simplified query matches Traditional text (cross-script) | No OpenCC or script conversion found in `src/` (grep) [C]; jieba only tokenises [C] | Decided 2026-09-30: **must match** (requirement). **LANG-06** is a P0 acceptance test that will fail until the feature exists (see Prerequisite Feature) |
| "Case Sensitive mode" | Toggle exists: 區分大小寫 / Match case [C: `i18n.py:84`]. Default state [I: off] | LANG-03/04 |
| Bottom quick-filter tag `格式: Word X` | Format filter now lives in the Advanced Filters panel; active filters show as removable badges `格式：{value} ✕` [C: `i18n.py:90`, changelog `i18n.py:138`] | FLT-01 targets panel and badge |
| Page navigation `1 / 2` | README documents previous/next *hit* navigation plus a hit counter, not paging [C] | PRV-03 tests hit navigation |
| Stemming/plurals | No stemmer found [C: jieba only] | LANG-07 characterisation (OQ-2) |

## Objective

When complete, every behaviour in the draft is either verified by an executed test with recorded Pass/Fail, or documented as unsupported with a test proving the app degrades gracefully. Every automatable case has a pytest test, and the manual GUI cases have a runnable script.

## Scope

### In Scope
- Search: Traditional/Simplified Chinese, English, mixed and special-character queries, boolean/phrase/filename syntax, case/whole-word/regex modes.
- Parsing of all 10 registered formats plus negative cases.
- Directory scope, indexing controls (scan/pause/resume/stop), filters, preview panel, corrupted/locked files.
- Test dataset and execution log (see Deliverables).

### Out of Scope
- Adding OCR (decided: not wanted), `.json`/`.log` parsing (decided: not wanted), or stemming. Product changes, not tests.
- *Implementing* Simplified↔Traditional matching. It is a separate task; this plan only specifies its tests (see Prerequisite Feature).
- Performance benchmarking (already in `tests/benchmarks/`, `docs/benchmarks.md`) beyond a smoke check.
- Packaging, signing, installer tests.

## Repository Context [C, all paths verified]

- Existing tests (pytest, CI runs Python 3.12 on ubuntu/windows/macos with `QT_QPA_PLATFORM=offscreen`, per `docs/baseline.md`): `tests/test_searcher.py`, `test_parsers.py`, `test_indexer.py`, `test_ui_features.py`, `tests/integration/test_indexing_failures.py`, `tests/unit/parsers/test_parser_outcomes.py`, `tests/unit/indexing/test_indexing_service.py`, `tests/unit/desktop/test_desktop_decisions.py`.
- Fixtures: `tests/sample_files/` (docx, xlsx, xls, pptx, txt, pdf), `tests/fixtures/files/legacy_word97.doc`, generator `tests/sample_generator.py`.
- Extend these; do not duplicate cases they already cover. First task is a coverage mapping (see Technical Approach step 1).
- UI labels: `src/doc_searcher/desktop/i18n.py` (zh-TW and en).

## Test Environment

| Item | Value |
| --- | --- |
| OS | macOS arm64 (primary), Windows 10/11, Linux (CI) |
| Python | 3.12 (CI), 3.13 (local baseline) [C: `docs/baseline.md`] |
| GUI runs | Qt offscreen for automation; a real display for manual cases marked **M** |
| Data isolation | Temp config and database only; never the real index (prior incident recorded in project memory) |

## Test Dataset (TD)

Implemented in `tests/search_plan/dataset.py`. Build it with `python tests/search_plan/dataset.py OUTDIR` (OUTDIR must be new or empty), or call `build_dataset(path)` / `load_manifest(path)` from tests. Output is synthetic, deterministic (byte-identical across runs, except encrypted PDFs and the optional `.xls`), and described by `manifest.json`: each file's group, expected outcome (`indexed`, `no_text`, `skipped_unsupported`, `skipped_unreadable`, `skipped_temp`), and search markers. Markers are letters and digits only (e.g. `QAMARKpdf`) so format and scope tests do not depend on punctuation handling (see Known defects). Formats this machine cannot author are listed under `unavailable` with the reason: legacy `.doc` and `.ppt` always (need real Office files; **TBD** OQ-6), `.xls` unless `xlwt` is installed. **TBD**: whether the requester has real sample files to add.

| ID | Content |
| --- | --- |
| TD-ZH-T | Traditional text: 升等, 會議記錄, 升等審查辦法, plus 升 and 等 in unrelated words |
| TD-ZH-S | Simplified: 升等, 会议记录, 计算机, 软件 |
| TD-EN | outstanding, Outstanding, OUTSTANDING, outstandings, outstand, Section 1 (Paragraphs) |
| TD-MIX | 1111223pi retreat, 2023-01-03, A-, A+, snake_case, a/b, C++, 100%, and `1111223pi retreat` as a file name |
| TD-FMT | A unique marker `QAMARK<ext>` in one file per extension; pdf page 2, pptx slide 2 and xlsx sheet `Summary` carry it so location labels can be checked (8 of 10 generated, see `unavailable`) |
| TD-TREE | `tree/a.txt`, `tree/sub/b.txt`, `tree/sub/deep/c.txt` with markers `QATREEroot`, `QATREEsub`, `QATREEdeep`; scan `tree/` as the root |
| TD-BAD | Truncated .docx/.xlsx, zero-byte .pdf, password-protected .pdf, owner-password-only .pdf, locked/unreadable file (chmod 000), `~$temp.docx`, symlink loop |
| TD-NEG | `.json`, `.log`, `.png`, `.exe` containing the marker token |
| TD-SCAN | Image-only PDF (no text layer) containing a rendered keyword |
| TD-PATH | Paths with CJK, emoji, spaces, `%`, `_`, `[ ]`; NFC vs NFD names (macOS) |

## Known defects found while building the dataset

- **DEF-01 (P0, existed on `main`): queries containing punctuation returned nothing.** `QATREE-deep`, `A-`, `2023-01-03`, `snake_case` and `Section 1 (Paragraphs)` returned zero results although the text was indexed. Cause: `_build_fts5_query` ANDed every jieba token, including standalone `-`, `_`, `(`, `)`, which SQLite's `unicode61` tokenizer never indexes. **Fixed** on branch `fix/punctuation-queries`: punctuation-only tokens are dropped from the FTS expression, queries with punctuation are confirmed by a literal check on the stored text, and punctuation-only queries (`_`, `/`) use the LIKE search. Tests: `tests/unit/search/test_punctuation_queries.py`, and LANG-08/09/10 in `tests/search_plan/test_lang.py`. Limitation: FTS returns at most `limit * 3` candidate segments before the literal check, so a punctuation query whose plain words match a very large number of other segments (for example `A-` in a corpus full of the letter A) can miss true hits; the same limit already applies to match-case and whole-word searches.

- **DEF-02 (open):** an unquoted Chinese word that jieba splits into single characters (升等 becomes 升 + 等) also matches documents holding those characters as separate words. Quoted phrases are exact. See `docs/test-execution-log.md`.
- **DEF-03 (open, minor):** opening a file that no longer exists fails silently in the preview panel.

Results of the last run, the manual checklist and the findings are in `docs/test-execution-log.md`.

## Test Cases

Priority: P0 blocks release, P1 should pass, P2 nice to have. Type: **A** automated pytest, **M** manual GUI, **C** characterisation (record actual behaviour, then confirm with owner).

### 1. Language and query behaviour

| ID | Draft | Steps | Expected | Pri | Type |
| --- | --- | --- | --- | --- | --- |
| LANG-01 | TC-01 | Search `升等` and `"升等"` on TD-ZH-T | Both return the same documents containing 升等; hits highlighted (yellow, bold) in preview at correct offsets | P0 | A+M |
| LANG-02 | TC-01 | Search `會議記錄`; search `記錄會議` (reordered) | First hits all docs with the phrase; second follows documented FTS behaviour (jieba tokens ANDed); record it | P1 | A |
| LANG-03 | TC-02 | With Match case OFF search `outstanding`, `OUTSTANDING`, `Outstanding` | Identical result sets and hit counts | P1 | A |
| LANG-04 | TC-02 | Match case ON, same three queries | Only exact-case matches returned; counts differ as per TD-EN | P1 | A |
| LANG-05 | – | Whole word ON, search `outstand` | Does not match `outstanding`; OFF matches per tokenizer (record) | P2 | A |
| LANG-06a | Simplified | Search `会议记录` on TD-ZH-T | Finds the Traditional docs containing 會議記錄; highlight covers the Traditional characters in the preview | P0 | A |
| LANG-06b | Simplified | Search `會議記錄` on TD-ZH-S | Finds the Simplified docs containing 会议记录 | P0 | A |
| LANG-06c | Simplified | Same-script queries (`升等` on TD-ZH-T, `计算机` on TD-ZH-S) and phrase `"会议记录"`, boolean `会议记录 AND 升等`, `filename:` with a Simplified name | Still hit; phrase, boolean and filename modes also work across scripts | P0 | A |
| LANG-06d | Simplified | Regex mode with `会议` | Documented behaviour: regex runs on stored text and is not script-folded (or is, if the feature covers it). Record and decide | P2 | C |
| LANG-06e | Simplified | One-to-many characters: `发` (發/髮), `后` (后/後), `干` (幹/乾/干) | No crash; document over-matching. Owner accepts precision loss (OQ-8) | P1 | C |
| LANG-06f | Simplified | Index built before the feature shipped | Old index is migrated or re-scan prompt appears; no stale misses | P1 | A |
| LANG-07 | Stemming | Search `outstand` and `outstandings` on TD-EN | **C**: record; no stemming is expected [I] | P2 | C |
| LANG-08 | TC-03 | Search `1111223pi retreat` (unquoted and quoted) | Hits in file *name* and body; both highlighted; consistent with `filename:` syntax | P0 | A |
| LANG-09 | TC-03 | Search `Section 1 (Paragraphs)` | No syntax error from parentheses; hit found; highlighted | P0 | A |
| LANG-10 | Special chars | Search `2023-01-03`, `A-`, `A+`, `_`, `/`, `C++`, `100%` individually | No crash or spurious syntax error; each yields hits or a clear message. Record per-symbol behaviour, because FTS5 tokenisation drops punctuation | P0 | A+C |
| LANG-11 | – | Boolean: `升等 AND 會議記錄`, `OR`, `NOT`; lowercase `and`; unbalanced quote | Correct set logic; invalid syntax shows the friendly error [C: `test_invalid_search_syntax_has_user_friendly_error`] | P1 | A |
| LANG-12 | – | Regex mode: valid pattern, invalid pattern, `(a+)+` catastrophic | Valid hits; invalid gives an error; catastrophic stops with `error_regex_timeout` message, UI stays responsive | P1 | A |
| LANG-13 | – | Empty query, whitespace-only, 10k-char query | No crash; defined empty-state message | P2 | A |
| LANG-14 | – | Traditional/English mix `升等 outstanding`, full-width punctuation `，。「」` | Tokenised sensibly; hits correct | P1 | A |

### 2. File parsing

| ID | Draft | Steps | Expected | Pri | Type |
| --- | --- | --- | --- | --- | --- |
| FMT-01..07 | TC-04 | For each of `.docx .pdf .pptx .xlsx .xls .doc .ppt`: index TD-FMT, search `QAMARK<ext>` | One hit each, correct location label (page N / sheet name / slide N); no parser error | P0 | A |
| FMT-07b | TC-04 | Same for `.txt .md .csv` | One hit each | P0 | A |
| FMT-08 | – | Index TD-NEG (`.json`, `.log`, `.png`, `.exe`) | Files are not indexed; no error dialogs; the scan summary counts them as unsupported/skipped [I]. Search for the marker returns nothing from these files | P1 | A |
| FMT-09 | – | `.txt` in UTF-8 BOM, UTF-16 LE/BE, UTF-32, Big5, GBK (no BOM) | Content decoded and searchable; known limitation: GBK without BOM may be read as Big5 [C: README]. Record the outcome | P1 | A |
| FMT-10 | – | Legacy `.doc` / `.ppt` with Chinese | Best-effort extraction; keyword found; noise tolerated [C: README] | P2 | A |
| FMT-11 | – | Multi-sheet xlsx, merged cells, formulas | Every sheet is searchable; hit labelled with sheet name | P1 | A |
| FMT-12 | – | Update a file and re-scan; delete a file and re-scan | Modified content re-indexed; deleted file removed, unless the volume is unavailable, in which case the index is preserved [C: README] | P0 | A |
| PDF-01 | TC-05 | Text PDF, Chinese and English | Hits with page numbers | P0 | A |
| PDF-02 | – | Owner-password-only PDF | Text extracted [C: README] | P1 | A |
| PDF-03 | TC-05 | Scanned image-only PDF (TD-SCAN) | **No hits, no crash**; file recorded as empty/no-text with a reason. OCR is not supported [C] | P1 | A |
| PDF-04 | TC-10 | User-password-protected PDF | Skipped with recorded reason | P2 | A |

### 3. Indexing and directories

| ID | Draft | Steps | Expected | Pri | Type |
| --- | --- | --- | --- | --- | --- |
| DIR-01 | TC-06 | TD-TREE, 包含所有下級資料夾 OFF, scan, search the three tokens | Only `root/a.txt` token found | P0 | A |
| DIR-02 | TC-06 | Toggle ON, re-scan | All three found. Toggling prompts or triggers re-scan per actual behaviour; record it | P0 | A |
| DIR-03 | – | Toggle ON then OFF: previously indexed sub-folder docs | Are removed from results (or documented as retained); record | P1 | A |
| DIR-04 | – | Add nested and duplicate directories; symlink loop | Merged/deduplicated; no infinite scan [C: README] | P1 | A |
| DIR-05 | – | Exclusion rules, subfolder scope limit | Excluded folders never appear in results [C: `test_exclusions_ignore_folders_above_search_roots`] | P1 | A |
| DIR-06 | – | Paths from TD-PATH, including NFC/NFD on macOS | Indexed once, searchable, openable | P1 | A |
| IDX-01 | TC-07 | During scan click ⏸ 暫停, then ▶ 繼續 | State shows 已暫停 then resumes; final doc count equals an uninterrupted run | P1 | M+A |
| IDX-02 | TC-07 | During scan click ⏹ 停止 | Stops after the in-flight file completes; state `index_stopped`; DB passes `PRAGMA integrity_check` | P1 | M+A |
| IDX-03 | TC-07 | After stop, click 🔄 掃描索引 | Clean incremental re-scan; no duplicate rows (count of documents = count of unique paths) | P1 | A |
| IDX-04 | – | Search while indexing | Search runs or queues and completes; UI does not freeze [C: `test_search_can_run_while_index_is_being_updated`] | P1 | A |
| IDX-05 | TC-10 | Scan TD-BAD | App survives; each bad file logged with a reason; good files still indexed; `~$` temp file skipped | P2 | A |
| IDX-06 | – | Kill app mid-scan, relaunch | DB opens; re-scan completes; no corruption | P2 | M |

### 4. Filters and results

| ID | Draft | Steps | Expected | Pri | Type |
| --- | --- | --- | --- | --- | --- |
| FLT-01 | TC-08 | Advanced Filters → format Word | Only `.docx`/`.doc` results; badge `格式：Word ✕` shown; ✕ removes it and restores the list | P1 | A+M |
| FLT-02 | – | Each format filter (pdf/word/excel/ppt/text) against TD-FMT | Result set matches extension group; sum across groups = unfiltered total | P1 | A |
| FLT-03 | – | Date and size filters with boundaries; reset all (重設所有篩選) | Boundary inclusive/exclusive per spec; reset clears badges; filters persist after restart [C: README] | P2 | A |
| FLT-04 | – | Result sorting on each column; column visibility menu | Ordering correct; hidden columns persist (PR #5) | P2 | M |

### 5. Preview panel

| ID | Draft | Steps | Expected | Pri | Type |
| --- | --- | --- | --- | --- | --- |
| PRV-01 | TC-09 | Select a result | Metadata (format, size, modified date, hit count) matches `os.stat` values | P0 | A+M |
| PRV-02 | TC-09 | A− / A+ repeatedly to both limits | Font changes monotonically; clamped at limits; highlight preserved | P1 | M |
| PRV-03 | TC-09 | Previous/next hit at first and last hit | Wraps or stops per spec (record); counter updates; view scrolls to hit | P1 | M |
| PRV-04 | TC-01 | Compare highlighted ranges with match offsets in zh, en, mixed | Highlight covers exactly the matched term, no offset drift with CJK/emoji | P0 | A |
| PRV-05 | TC-09 | 開啟檔案 (or Enter/double-click) | Opens in default app; missing file gives a clear error, not a crash | P0 | M |
| PRV-06 | TC-09 | 複製路徑 | Clipboard equals the full absolute path, including CJK/space paths | P0 | M+A |
| PRV-07 | – | Context menu → reveal in Finder/Explorer | File selected in file manager | P2 | M |
| PRV-08 | – | Switch light/dark and zh/en | Highlight legible in both; selection and scroll position retained | P2 | M |

### 6. Robustness

| ID | Steps | Expected | Pri | Type |
| --- | --- | --- | --- | --- |
| ROB-01 | Index the 1k corpus (`tests/benchmarks/conftest.py::corpus_1k`), then run 20 mixed queries (zh, en, phrase, boolean, regex) | No crash. Cold index ≤ 6 s; each query ≤ 200 ms; peak RSS ≤ 300 MB; RSS growth across the 20 queries ≤ 20 MB (budgets: see Performance Budgets) | P2 | A |
| ROB-01b | Same with the 10k corpus (`DOC_SEARCHER_BENCH_10K=1`) | Cold index ≤ 90 s; peak RSS ≤ 400 MB; queries ≤ 500 ms | P2 | A |
| ROB-01c | Repeat 200 search→select→preview cycles in the offscreen GUI | RSS growth ≤ 50 MB; no unbounded growth trend (slope check over 5 samples) | P2 | A |
| ROB-02 | Index file deleted between scan and preview | Preview shows a clear "file missing" state | P2 | A |
| ROB-03 | CLI parity: `--dir` + `--search` for LANG-01/08/09 | CLI results equal GUI service results | P2 | A |

## Prerequisite Feature: Simplified↔Traditional matching

Decided 2026-09-30 that Simplified queries must match Traditional text. **Implemented** on branch `feature/simplified-traditional-search` (task: `docs/tasks/simplified-traditional-search.md`, design: `docs/adr/0002-simplified-traditional-folding.md`). Automated coverage of LANG-06a, b, c, e, f is in `tests/unit/search/test_script_fold.py`; LANG-06d (regex) intentionally stays literal. Design choice is **TBD** and the tests are written to be implementation-neutral: e.g. fold both index text and query to one script (OpenCC `t2s`) before jieba tokenising, and keep original text for display and highlighting. Highlight mapping back to the original characters is the main risk (PRV-04, LANG-06a).

## Performance Budgets (ROB-01)

Derived 2026-09-30 from the recorded baseline (`docs/benchmarks.md`: cold 1k 1.75 s, cold 10k 20.7 s, FTS search 6-28 ms, RSS ≤ 119 MB on Apple M3). Rule used: about 3x baseline for slower CI runners, rounded up, so the tests catch real regressions and not machine noise. Budgets **approved 2026-09-30**; loosen on CI if a runner proves slower, and re-derive after the Simplified/Traditional feature lands, since script folding adds indexing cost.

| Metric | Baseline | Budget |
| --- | --- | --- |
| Cold index 1k | 1.75 s | ≤ 6 s |
| Cold index 10k | 20.7 s | ≤ 90 s |
| Query median, 1k | 6-28 ms | ≤ 200 ms each |
| Query, 10k | not measured | ≤ 500 ms each |
| Peak RSS, 1k | ≤ 119 MB | ≤ 300 MB |
| Peak RSS, 10k | ≤ 119 MB | ≤ 400 MB |
| RSS growth over 20 queries | not measured | ≤ 20 MB |
| RSS growth over 200 preview cycles | not measured | ≤ 50 MB |

Platform figures measured on the CI runners (2026-09-30): Linux peaks at about 306 MB for the 1,000-file run (macOS: 115 MB) and Windows indexes it in about 8.7 s (macOS: 1.8 s). `tests/search_plan/test_robustness.py` therefore uses a 400 MB peak-RSS budget on Linux/Windows and a 15 s index budget on Windows for that case; macOS keeps 300 MB and 6 s. The other budgets are unchanged.

## Technical Approach

1. Map the cases above against existing tests. Mark each *covered / partial / new* in the execution log, and extend existing modules instead of duplicating.
2. Build TD-* with `tests/search_plan/dataset.py` (done; generated files are not committed). Its own tests are `tests/search_plan/test_dataset.py`. Case modules go beside them in `tests/search_plan/`.
3. Add automated cases as pytest modules: `tests/search_plan/test_lang.py`, `test_formats.py`, `test_directories.py`, `test_preview.py`, each using temp config and DB fixtures from `tests/conftest.py`.
4. Add a manual checklist for **M** cases in the execution log; run on macOS and Windows.
5. For **C** cases, run first, record actual behaviour, then get the owner's decision (Open Questions) and convert to assertions.
6. File a bug per real defect; do not mark characterisation results as failures without a decision.

## Deliverables

1. `docs/test-plan.md` (this file).
2. Deterministic dataset generator and resulting TD-* set.
3. New/extended pytest modules for all **A** cases.
4. `docs/test-execution-log.md`: table of Test ID, Pass/Fail/Blocked/Characterised, environment, evidence, bug link.
5. Bug tickets for parser failures, highlight mismatches, memory or hang issues.

## Acceptance Criteria

- Every test ID above has a row in the execution log with a status; none silently omitted.
- All P0 cases pass on macOS arm64 and one of Windows/Linux, or have a linked open bug.
- `python -m pytest -q` passes in CI on the three OS runners with the new tests included.
- No test reads or writes the user's real config or index (verified by running with `HOME`/data dir redirected).
- Each **C** case records observed behaviour and is either converted to an assertion or listed in Open Questions.
- `.json`, `.log` and scanned-PDF cases assert graceful non-indexing, not success.
- LANG-06a-c (Simplified↔Traditional) pass once the prerequisite feature is implemented; until then they are recorded as *Blocked by feature* in the log, not deleted or weakened.
- ROB-01 budgets are met on macOS arm64; CI runner overruns are documented with measured values before any budget is loosened.
- Any assumption that cannot be verified is documented as such, not guessed.

## Validation

- `python -m pytest --collect-only -q` shows the new tests (baseline was 101 [C: `docs/baseline.md`]).
- `QT_QPA_PLATFORM=offscreen python -m pytest -q` passes locally.
- Re-run the generator twice and confirm identical file hashes.
- For **M** cases, attach a screenshot or note per ID in the log.
- Run the suite with `HOME` pointed at an empty temp dir, and confirm the real data dir is untouched.

## Open Questions

- ~~OQ-1~~ **Resolved 2026-09-30:** Simplified queries must match Traditional text (and the reverse). Requires the prerequisite feature.
- **OQ-2** Is the lack of stemming/plural matching acceptable? Still open (LANG-07 records behaviour).
- ~~OQ-3~~ **Resolved:** `.json`/`.log` not supported.
- ~~OQ-4~~ **Resolved 2026-09-30:** budgets in Performance Budgets approved.
- ~~OQ-5~~ **Resolved:** no OCR.
- **OQ-6** Are real (non-synthetic) sample documents available, and are they safe to store in the repo?
- **OQ-7** Should turning subfolders off after indexing purge or retain entries (DIR-03)?
- **OQ-8** For one-to-many characters (發/髮, 干/幹/乾), is over-matching acceptable? Suggested: yes, prefer recall.
- **OQ-9** Should highlights in the preview show the document's original characters (recommended) even when the query was typed in the other script?

## References

- Requester's draft: "Search System Test Plan (Multi-Language & Multi-Format)" (pasted in conversation).
- `README.md`, `docs/baseline.md`, `docs/benchmarks.md`.
- `src/doc_searcher/parsers/__init__.py`, `src/doc_searcher/desktop/i18n.py`, `src/doc_searcher/search/text_helper.py`.
- Authoring: task-authoring skill template and acceptance-criteria guide.
