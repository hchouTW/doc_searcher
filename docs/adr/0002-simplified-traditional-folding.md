# ADR 0002 — Simplified/Traditional Chinese matching by character folding

- **Status:** Accepted (2026-09-30), implements `docs/tasks/simplified-traditional-search.md`
- **Scope:** `src/doc_searcher/search/script_fold.py`, `text_helper.py`, `searcher.py`, migration 2

## Context

A Simplified query must find Traditional text and the reverse. Until v1.3.0 the index held jieba
tokens of the original text, so 会议 and 會議 never matched.

## Decision

1. **Character-level fold to Simplified**, one character to one character, using OpenCC's
   `TSCharacters` table (Apache-2.0), vendored unmodified as `assets/opencc_TSCharacters.txt` with
   `assets/opencc_NOTICE.txt`. Folding keeps string length, so no offset mapping is needed.
2. **No new dependency.** The table is package data already covered by `assets/*` and the
   PyInstaller specs (`(assets, 'assets')`), so it works on Python 3.10-3.14 on every platform.
   Alternatives not chosen: `opencc` (C extension, wheel matrix risk) and
   `opencc-python-reimplemented` (pure Python, ships a 1 MB phrase table we do not use, and installs
   a top-level `opencc` package that clashes with the C package).
3. **Index and query are folded before jieba**, so `tokenized_content` is script-neutral. Stored
   `content` is untouched.
4. **Highlighting and literal re-checks run on the original text** with per-character classes
   (`发` becomes `[发發髮]`), so the preview shows the characters as written in the document.
   Match case, whole word, `NOT`-only search, and the LIKE fallback use the same rule. Filename
   search compares `fold(filename)` through a SQLite function, with no new column.
5. **Regex mode is not folded**; it matches stored text as written (documented in README).
6. **Migration 2** rebuilds `doc_fts` from `doc_segments` (no source files are read), in the
   existing backup-first transaction.

## Consequences

- One-to-many characters over-match by design: 干 covers 幹 and 乾, 发 covers 發 and 髮. Words
  still differ (`干部` does not match 乾淨). Recall is preferred over precision.
- Regional vocabulary (軟體 vs 软件) is not converted; that would be a phrase-level follow-up.
- Chinese whole-word matching is unchanged: CJK characters are word characters, so a term inside
  a longer run of Chinese text is not a whole word in either script.
- Migration cost measured on the baseline machine (Apple M3): 15.2 s for 10,000 documents of about
  1.5 KB, blocking on first launch after upgrade. Cold index of 10k documents: 16.2 s (baseline
  20.7 s, same-machine noise about 10%).
