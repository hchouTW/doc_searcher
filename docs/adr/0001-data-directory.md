# ADR 0001 — Where DocSearcher stores config.json and index.db

- **Status:** Accepted (2026-09-24, Task 2.1)
- **Scope:** `src/doc_searcher/config.py` (`get_default_data_dir`)

## Context

`config.json` and `index.db` are the only user data DocSearcher owns. Through v1.2.0 the data
directory was `$DOC_SEARCHER_DATA_DIR`, else `~/.doc_searcher`, else `<module dir>/../.data`.
The last fallback was only meaningful for a source checkout: in a PyInstaller build it points
into the (read-only, possibly temporary) bundle, and in an installed wheel into
`site-packages`. Losing or silently relocating the index forces a full re-index and loses
settings, so the rules must be predictable and must not move existing users.

## Decision

Resolve the first usable candidate, in order:

1. `$DOC_SEARCHER_DATA_DIR` — explicit override. If it cannot be created/written this is an
   error (`ConfigError`), never a silent fallback: the user asked for that location.
2. `~/.doc_searcher` — the legacy location; every existing installation keeps using it.
3. `<checkout>/.data` — only for a **source checkout** (not frozen, `pyproject.toml` beside
   `src/`) that **already contains** `config.json` or `index.db`, so users of the old fallback
   keep their data. New installs never start here.
4. The OS per-user data directory:
   - Windows: `%LOCALAPPDATA%\DocSearcher`
   - macOS: `~/Library/Application Support/DocSearcher`
   - Linux: `$XDG_DATA_HOME/doc_searcher` (default `~/.local/share/doc_searcher`)
5. Otherwise raise `ConfigError` naming the paths tried and `DOC_SEARCHER_DATA_DIR`. The CLI
   exits with code 3; the desktop app shows the message in a dialog.

"Usable" means the directory can be created and a temporary file can be written in it.

## Consequences

- Existing users see no change (1–3 cover every v1.2.0 location that could have held data).
- Nothing is ever written inside an app bundle, PyInstaller extraction directory, or
  `site-packages`.
- A user whose home was unwritable and who later fixes it moves to `~/.doc_searcher`, as in
  v1.2.0. Migrating data between candidates is out of scope; no automatic copy is attempted.
- Tests cover each branch in `tests/integration/test_config_persistence.py`.
