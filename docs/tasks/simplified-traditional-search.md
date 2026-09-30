# Match Simplified and Traditional Chinese in Search

Evidence tags: **[C]** Confirmed (user input or source read), **[I]** Inferred, **[TBD]** Unresolved.

## Background

Decided 2026-09-30: a Simplified Chinese query must find Traditional Chinese text, and the reverse (`docs/test-plan.md`, LANG-06). Today it does not.

- The index stores original text in `doc_segments.content` and `doc_fts.content`, and a jieba token string in `doc_fts.tokenized_content` [C: `storage/migrations.py:_v1_baseline`, `indexing/indexer.py:96-108`].
- Content queries are turned into FTS5 expressions over `tokenized_content` using `jieba.cut` on the raw query [C: `search/searcher.py:_build_fts5_query`]. No script folding exists (grep for opencc/繁簡 finds only a README claim) [C].
- README already advertises "繁簡中文" support [C: `README.md:23`]. It is inaccurate until this task lands; see Deliverables.
- Snippets and preview highlights are produced by a regex over the *original* `content` built from the query keywords [C: `search/text_helper.py:generate_highlighted_snippets`], so a folded match would be found but not highlighted unless highlighting is also made script-aware.

## Objective

When complete, searching `会议记录` finds documents containing `會議記錄` and vice versa, in plain, phrase, boolean, filename and case/whole-word modes. The preview highlights the characters *as they appear in the document*. Existing indexes are upgraded without re-parsing files, and Simplified→Simplified, Traditional→Traditional and English searches behave as before.

## Scope

### In Scope
- Script folding at index time and query time for content search (`tokenized_content` path).
- Script-aware literal matching wherever the code re-checks content: `_literal_matches`, `_matches_query_options` (case/whole-word), `_fallback_like_search`, negative-only search.
- Filename search (`filename:` / `檔名:`), which uses `LIKE` on `documents.filename` [C: `searcher.py:_search_filenames`].
- Highlight/snippet generation over original text for both scripts.
- Schema migration (new version in `storage/migrations.py`) that re-tokenises existing segments from stored `doc_segments.content`.
- Tests for LANG-06a, 06b, 06c, 06e, 06f from `docs/test-plan.md`, plus unit tests for the folding helper.
- Packaging and dependency updates if a new library is chosen.
- README, in-app changelog (`version.py`), and en/zh-TW help text updates.

### Out of Scope
- Regex mode script folding (LANG-06d): regex runs on stored text as written. Default is documented, not changed (see Open Question 3).
- Regional vocabulary conversion (軟體↔软件, 記憶體↔内存), i.e. term-level Taiwan/Mainland/HK phrase mapping. Character-level folding only, unless Open Question 2 says otherwise.
- Converting or rewriting stored document text. Original text stays as-is.
- OCR, stemming, `.json`/`.log` support.
- Interface language switching (already exists).

## Repository Context [C, paths verified]

| Area | File | Relevance |
| --- | --- | --- |
| Tokenising | `src/doc_searcher/search/text_helper.py` | `tokenize_for_fts`, `extract_keywords_from_query`, highlight regex |
| Index write | `src/doc_searcher/indexing/indexer.py`, `storage/database.py` | Builds `tokenized_content`, writes `doc_fts` |
| Query | `src/doc_searcher/search/searcher.py` | `_build_fts5_query`, filename search, fallback LIKE, option checks, negative-only |
| Facade | `src/doc_searcher/search/search_service.py`; CLI `cli.py`; MCP `integrations/mcp_server.py` | MCP delegates to `SearchService` [C]; CLI and MCP therefore share the searcher [I] |
| Schema | `storage/migrations.py` (only version 1 exists), backup `<index.db>.pre-migration.bak` | New migration must be idempotent, transactional, and back up first [C] |
| Health | `selfcheck.py` uses `tokenize_for_fts` | Extend with a folding probe |
| Packaging | `packaging/doc_searcher_{mac,win}.spec` (collect jieba data), `constraints/ci.txt`, `constraints/README.md`, `scripts/update_constraints.sh` | A new dependency must be bundled and pinned |
| Tests | `tests/test_searcher.py`, `tests/unit/search/test_snippets.py`, `tests/integration/test_database_migrations.py`, `tests/benchmarks/` | Extend, do not duplicate |
| Python range | 3.10 to 3.14 [C: `pyproject.toml`] | Any new library must support all of them |
| Perf baseline | `docs/benchmarks.md`; budgets in `docs/test-plan.md` | Cold 1k index ≤ 6 s, queries ≤ 200 ms |

## Technical Approach

Recommended design (implementation may deviate if acceptance criteria hold):

1. **Choose the conversion library** (Open Question 1). Evaluate against: supports Python 3.10-3.14 on Windows, macOS arm64/x86_64 and Linux; PyInstaller-friendly; pure-Python fallback; licence; speed on 10k documents. Record the comparison in an ADR under `docs/adr/` (`0002-...`), matching ADR 0001's format.
2. **Add a single helper module**, e.g. `search/script_fold.py`, exposing `fold(text) -> str` (map to one canonical script, recommended Simplified) and `variants(char) -> set[str]` (all characters that fold to the same form). It must be deterministic, idempotent, and cheap (cache or precomputed table).
3. **Index time:** in `tokenize_for_fts`, fold the text before jieba, so `tokenized_content` is script-neutral. Keep `content` untouched. Note that jieba's dictionary is mostly Simplified-biased [I], so folding first also improves Traditional segmentation; verify with tests instead of assuming.
4. **Query time:** fold query terms before `jieba.cut` in `_build_fts5_query` and in `extract_keywords_from_query`.
5. **Highlighting:** build the highlight regex from *original-script variants* of each query character (per-character alternation, using `variants()`), so the match is on original content. Offsets never shift, because folding is applied to the pattern, not the content. If folding can change string length, this is the reason not to search folded text.
6. **Re-checks:** make `_literal_matches`, fallback LIKE, negative-only search, and filename search script-aware using the same char-class pattern (or fold both sides). For filename `LIKE`, choose between a folded filename column (new migration column) and Python-side filtering [TBD, keep the cheaper one that meets the budgets].
7. **Migration:** add version 2. Recompute `tokenized_content` for every row from `doc_segments.content` in batches, updating `doc_fts`. Idempotent; backup first (existing mechanism). Do not require re-scanning files. If a full migration is too slow on large indexes (measure at 10k documents), run it in the background with a progress state and a "reindexing" search fallback [TBD, only if measured > acceptable].
8. **Startup guard:** searching an unmigrated database must not silently miss; migration runs on open [C: `Database.init_db` applies migrations].
9. Update `selfcheck.py` with a fold round trip (`会议` finds `會議`).
10. Docs: README claim corrected/expanded, `version.py` CHANGELOG entry (bump per the project's release convention), en and zh-TW strings in `desktop/i18n.py` if help text mentions script matching.

## Deliverables

1. `search/script_fold.py` (or equivalent) with unit tests.
2. Changes in `text_helper.py`, `searcher.py`, and, if needed, `indexer.py`/`database.py`.
3. Migration version 2 with a test on a v1 database fixture containing Traditional and Simplified rows.
4. ADR `docs/adr/0002-*.md` recording the library and folding direction.
5. Updated packaging specs and `constraints/ci.txt` (regenerated with `scripts/update_constraints.sh`), if a dependency is added.
6. Tests: automated versions of LANG-06a, 06b, 06c, 06e, 06f, PRV-04-style highlight offset tests for both scripts.
7. README, `version.py` changelog, and i18n updates.
8. Benchmark comparison against `docs/benchmarks/baseline-macos-arm64-py313.json`.

## Acceptance Criteria

1. `会议记录` finds a document containing `會議記錄`, and `會議記錄` finds one containing `会议记录` (LANG-06a/b), in an index built from scratch and in an index migrated from v1.
2. Plain, `"quoted phrase"`, `AND`/`OR`/`NOT`, and `filename:`/`檔名:` queries all work across scripts (LANG-06c). A negative query (`NOT 会议`) excludes Traditional documents containing 會議.
3. Match case and whole word options give the same results for Simplified and Traditional forms of the same word. English matching is unchanged: the full existing suite (`python -m pytest -q`; baseline 101 tests [C: `docs/baseline.md`]) still passes.
4. Preview and snippet highlights cover exactly the characters written in the document, for a Simplified query on Traditional text and the reverse, with correct offsets when the text also contains emoji and English (test asserts the `<mark>` content equals the original substring).
5. One-to-many characters (`发`→發/髮, `后`→后/後, `干`→干/幹/乾) do not crash and over-match only where the mapping is genuinely ambiguous; observed behaviour is recorded in the ADR (LANG-06e).
6. The migration from schema 1 to 2 keeps `documents` and `doc_segments` row counts, creates the `.pre-migration.bak` backup, is idempotent when re-run, and rolls back cleanly on injected failure (extend `tests/integration/test_database_migrations.py`).
7. No source file is re-read during migration (test asserts the migration works after the original files are deleted).
8. Performance stays within the approved budgets in `docs/test-plan.md`: cold 1k index ≤ 6 s, 10k ≤ 90 s, each query ≤ 200 ms on 1k, migration time for 10k documents is measured and reported. If a budget is exceeded, the number and cause are documented rather than the budget silently changed.
9. Frozen builds bundle the conversion data: `verify_build.py`/`--self-check` passes with the fold probe on macOS and Windows builds [C: `scripts/verify_build.py` exists].
10. If a dependency is added, it installs on Python 3.10 through 3.14 in CI, and `constraints/ci.txt` contains its hashed pin.
11. README and changelog describe the feature and its limits (no regional vocabulary mapping, regex mode not folded).

## Validation

- `QT_QPA_PLATFORM=offscreen python -m pytest -q` on macOS, plus CI on ubuntu/windows/macos.
- `python -m pytest tests/benchmarks --benchmark-only --benchmark-json=bench.json` then `pytest-benchmark compare docs/benchmarks/baseline-macos-arm64-py313.json bench.json --columns=median --group-by=name` [C: `docs/benchmarks.md`].
- Migration check with a real-shaped index: copy a generated v1 database, run the app or `Database.init_db`, and search both scripts. Use a temp data directory; **never the real `~/.doc_searcher`** (prior incident in project memory).
- Manual GUI check (M): Simplified query on Traditional file, verify yellow highlight, hit navigation, light and dark theme.
- Run `docs/test-plan.md` LANG-06a-f; LANG-06a-c move from "Blocked by feature" to Pass.
- `python -m doc_searcher --self-check` on the built app.

## Open Questions

1. **Library choice [TBD]:** OpenCC bindings (C extension, best data), a pure-Python reimplementation, or a smaller table library. Decide after the wheel and PyInstaller check in step 1; must support Python 3.10-3.14.
2. **Character-level only, or phrase-level too?** Recommended: character-level, since it keeps offsets stable and covers the requirement; phrase conversion (軟體↔软件) is a separate enhancement.
3. **Regex mode:** stays literal on stored text (recommended, documented) or gets folding? Affects LANG-06d.
4. **Canonical direction:** fold to Simplified (recommended, fewer collisions in jieba's dictionary [I]) or to Traditional.
5. **Filename folding:** extra column vs on-the-fly filter (measure first).
6. **Migration UX:** blocking on first launch, or background? Decide from the measured 10k migration time (approve if ≤ about 30 s blocking [TBD, needs owner]).
7. **Over-matching:** confirm that favouring recall for one-to-many characters is acceptable (test plan OQ-8) and that highlights show original characters (test plan OQ-9, recommended yes).
8. **Version number** for the release containing this change [TBD, maintainer].

## References

- `docs/test-plan.md` (LANG-06a-f, Performance Budgets, OQ-1/8/9), `docs/benchmarks.md`, `docs/baseline.md`, `docs/adr/0001-data-directory.md`.
- Source files listed in Repository Context.
- Authoring: task-authoring skill template and acceptance-criteria guide.
