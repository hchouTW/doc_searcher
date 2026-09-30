# ADR 0003 — English stemming with SQLite's porter tokenizer

- **Status:** Accepted (2026-09-30)
- **Scope:** `storage/migrations.py` (version 3), `search/stemming.py`, `search/text_helper.py`, `search/searcher.py`

## Context

Searching `outstanding` did not find `outstand` or `outstandings`. The owner decided on
2026-09-30 that this is not acceptable (test plan OQ-2).

## Decision

1. **Index with `tokenize='porter unicode61'`.** FTS5 applies the tokenizer to the query text as
   well, so no query rewriting is needed. Porter only touches ASCII letters, so the jieba tokens
   for Chinese are unchanged. Built in to SQLite: no dependency, works on every platform and
   Python version we support.
2. **Migration 3** drops and recreates `doc_fts` with that tokenizer and refills it from
   `doc_segments` (source files are not read). Because that rebuild also re-runs the
   Simplified/Traditional folding, migration 2 became a marker: a database from 1.3.0 is
   rebuilt once. Measured on the baseline machine: 15.4 s for 10,000 documents of about 1.5 KB
   (version 1 to 3), blocking on first launch.
3. **Highlights follow the index.** A hit found through another word form would otherwise have no
   visible mark (and was dropped for lack of a snippet). `search/stemming.py` asks the same
   porter tokenizer (through a private in-memory FTS5 table and `fts5vocab`) for a word's stem,
   so highlighting and the index cannot disagree. Words starting with the term's stem prefix are
   candidates and only those with the same stem are marked (`run` marks `running`, not `runway`).
4. **Exact means exact.** Match case and whole word are asked for the literal word, so they switch
   stemming off in the literal re-checks and in highlighting. Filename search is literal.
   Regex mode is unchanged.

## Consequences

- Porter over-stems some words (`news` finds `new`, `university` finds `universe`). Accepted and
  covered by a test so a change is noticed.
- English only; other languages are not stemmed.
- Queries of a plain English word cost more (1,000-file benchmark, `budget`: 17.6 ms to 22.1 ms,
  +25%) because highlighting checks stems; Chinese, phrase, boolean and regex queries are unchanged.
- Cold indexing is not slower (1,000 files 1.58 s, 10,000 files 16.9 s).
