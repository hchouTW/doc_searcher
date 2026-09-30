# Test Execution Log

Run 2026-09-30 on macOS arm64 (Apple M3), Python 3.13.2, branch `test/search-plan-cases`
(stack: #10 Simplified/Traditional, #11 dataset, #12 punctuation fix). Automated cases live in
`tests/search_plan/` (plus `tests/unit/search/` where noted); the dataset is built by
`tests/search_plan/dataset.py`. Full suite: 470 passed, 5 skipped, 2 xfailed.

Status key: **Pass** automated and green. **Characterised** actual behaviour recorded and
asserted, awaiting a product decision. **Manual** needs a person, checklist below. **Blocked**
cannot run here, reason given. **Covered** already tested elsewhere in the suite.

## Results

| ID | Status | Where / note |
| --- | --- | --- |
| LANG-01 | Pass | `test_lang.py::test_lang_01_*`. Quoted and unquoted `升等` both return exactly the 5 documents (DEF-02 fixed) |
| LANG-02 | Pass | Word order is not significant: `記錄會議` returns the same documents as `會議記錄` |
| LANG-03 / 04 / 05 | Pass | case-insensitive equality; match case; whole word |
| LANG-06a-c, e, f | Pass | `tests/unit/search/test_script_fold.py` |
| LANG-06d | Characterised | regex mode matches stored text as written (`test_regex_mode_stays_literal`) |
| LANG-07 | Characterised | no stemming or plurals (OQ-2 open) |
| LANG-08 / 09 / 10 | Pass | after the DEF-01 fix (#12) |
| LANG-11 | Pass | operators, lower-case operators, three malformed-query errors |
| LANG-12 | Pass | valid/invalid regex; catastrophic patterns are covered by `tests/performance/test_regex_limits.py` |
| LANG-13 / 14 | Pass | empty, whitespace, 3,000-term query under 10 s; mixed Chinese and English |
| FMT-01..07b | Pass | pdf page 2, pptx slide 2, xlsx sheet `Summary` reported correctly; docx, txt, md, csv found |
| FMT `.xls` | Skipped here | `xlwt` not installed; runs when it is |
| FMT `.doc`, `.ppt` | Blocked | no Office-authored files (OQ-6). Legacy `.doc` parsing is covered by `tests/unit/parsers/test_parser_outcomes.py`; `.ppt` has no fixture anywhere |
| FMT-08 | Pass | `.json`, `.log`, `.png`, `.exe` never reach the index |
| FMT-09 | Pass, 1 xfail | UTF-8 BOM, UTF-16, UTF-32, Big5 decode. BOM-less GBK is read as Big5 (garbled, README limitation), strict xfail `test_fmt_09_gbk_without_bom_body_decodes` |
| FMT-11 / 12 | Pass | every sheet indexed; modified, deleted and unplugged-folder rescans |
| PDF-01..04 | Pass | page numbers; owner-password text extracted; scanned PDF gives no hits and no error; user-password PDF recorded as unreadable |
| DIR-01 / 02 / 04 / 05 / 06 | Pass | subfolders off/on, nested and duplicate roots, symlink loop, exclusions, awkward paths |
| DIR-03 | Characterised | turning subfolders off purges the deeper entries on the next scan (OQ-7 open) |
| IDX-01..05 | Pass | pause holds after the in-flight file, resume completes, stop leaves a valid DB, rescan has no duplicates, search works during a scan, bad files recorded |
| IDX-06 | Manual | kill the app mid-scan |
| FLT-01 / 02 / 03 | Pass | engine filters; GUI badge and chips are **Covered** by `tests/test_ui_features.py` |
| FLT-04 | Manual + Covered | column sorting and visibility persistence (PR #5 tests) |
| PRV-01, 02, 03, 04, 06 | Pass | metadata, zoom clamping, wrap-around, highlights equal stored text, clipboard path |
| PRV-05 | Pass + Manual | missing file returns False without crashing (DEF-03); real launch needs a person |
| PRV-07 / 08 | Manual + Covered | Finder/Explorer reveal; theme tests exist in `test_ui_features.py` |
| ROB-01 | Pass | 1k: index 1.8 s (budget 6), slowest query 14.6 ms (200), peak RSS 115 MB (300), growth 6.1 MB (20) |
| ROB-01b | Pass | 10k (`DOC_SEARCHER_BENCH_10K=1`): index 17.0 s (90), slowest query 119 ms (500), peak RSS 122 MB (400) |
| ROB-01c | Pass | 200 preview cycles: RSS growth 0 MB in all 5 samples (limit 50) |
| ROB-02 | Pass, gap | no crash, but no "file missing" state exists (DEF-03) |
| ROB-03 | Pass | CLI and `SearchService` return the same paths for 5 queries |

## Findings

- **DEF-01** punctuation queries returned nothing. Fixed in #12.
- **DEF-02 (fixed on `fix/unsegmented-cjk-terms`, precision)** an unquoted Chinese word that
  jieba does not know as one word (升等 is cut into 升 and 等) matched any document holding
  those characters as separate words, e.g. `zh/unrelated.txt`; `NOT 升等` also wrongly excluded
  such documents. Now such terms are confirmed against the stored text like punctuation terms,
  and `NOT` on them is decided by that check instead of FTS. Words jieba does segment keep
  their AND, word-order-free behaviour (`記錄會議` still finds `會議記錄`).
- **DEF-03 (open, minor)** "開啟檔案" on a result whose file was deleted does nothing and shows no
  message. `open_file_with_default_app` returns False and the panel ignores it.
- **Observation** `PRAGMA integrity_check` on a long-lived connection that saw the table before
  another thread wrote to it can report `fts5: checksum mismatch` while a fresh connection says
  `ok`. The file is fine; the tests check integrity on a fresh connection.

## Manual checklist (macOS and Windows)

Use a copy of the dataset (`python tests/search_plan/dataset.py ~/qa-dataset`) and a temporary
data folder (`DOC_SEARCHER_DATA_DIR=$(mktemp -d)`); never the real index.

- [ ] **LANG-01** search `升等`: hits are highlighted yellow and bold in the preview.
- [ ] **IDX-01** scan a folder of a few thousand files, click ⏸ 暫停 then ▶ 繼續: status shows 已暫停, progress holds, then finishes.
- [ ] **IDX-02** click ⏹ 停止 mid-scan, then 🔄 掃描索引: stops after the current file, next scan completes.
- [ ] **IDX-06** kill the app during a scan, relaunch, scan again: no error dialog, counts correct.
- [ ] **FLT-01 / FLT-04** format chip filters the list and shows a removable badge; sort by each column; hide and restore columns.
- [ ] **PRV-05** 開啟檔案 (and Enter / double-click) opens the file in its default app.
- [ ] **PRV-07** right-click, reveal in Finder/Explorer selects the file.
- [ ] **PRV-08** switch light/dark and 中文/English: highlight stays legible, selection and scroll position kept.
- [ ] **FMT `.doc` / `.ppt` / `.xls`** with real Office-authored files: keyword found.
