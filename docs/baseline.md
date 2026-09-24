# Executable Baseline (v1.2.0, commit `78e99a5`)

Recorded 2026-09-24 as Task 0.1 of the refactoring plan (`task.md`). Later tasks compare
against this snapshot; update it with evidence whenever a count or contract changes on purpose.

## Entry points

| Entry point | Mode | Notes |
| --- | --- | --- |
| `python main.py` | Desktop GUI (PySide6) | Default when `--dir`/`--search` are absent. |
| `python main.py --dir <folder> --search <query> [--type all\|pdf\|word\|excel\|ppt\|text]` | Headless CLI | Indexes `<folder>` incrementally, then prints results. |
| `python mcp_server.py` | MCP stdio server | Requires `requirements-mcp.txt` (Python ≥ 3.10). |
| `packaging/build_mac.sh` | macOS `.app` + zip | PyInstaller, `packaging/doc_searcher_mac.spec`. |
| `packaging/build_win.ps1` | Windows one-file `.exe` | PyInstaller, `packaging/doc_searcher_win.spec`. |

## Tests

- `python -m pytest --collect-only -q` → **101 tests collected**.
- `python -m pytest -q` → **101 passed**, 5 warnings (below), ~3.5 s on macOS arm64, Python 3.13.2.
- CI (`.github/workflows/tests.yml`): Python 3.12 on ubuntu/windows/macos-latest, `QT_QPA_PLATFORM=offscreen`.

### Known third-party warnings (not suppressed)

PyMuPDF's SWIG bindings emit, on first import:

```
DeprecationWarning: builtin type SwigPyPacked has no __module__ attribute
DeprecationWarning: builtin type SwigPyObject has no __module__ attribute
DeprecationWarning: builtin type swigvarlink has no __module__ attribute
```

These come from SWIG-generated code inside `pymupdf`, not from DocSearcher; they are left visible
so an upstream fix (or a new warning) is noticed.

## Release artifacts (`.github/workflows/release.yml`)

| Runner | Artifact |
| --- | --- |
| `macos-latest` (arm64) | `DocSearcher-macOS-arm64.zip` (zipped `DocSearcher.app`) |
| `macos-15-intel` | `DocSearcher-macOS-x86_64.zip` (historical v1.2.0 artifact; no longer produced from v1.3.0) |
| `windows-latest` | `DocSearcher-Windows-x64.exe` |

Branch builds upload `DocSearcher-macOS-<arch>` and `DocSearcher-Win11-x64` workflow artifacts.

## Persistent data

Location: `$DOC_SEARCHER_DATA_DIR` if set, else `~/.doc_searcher/`, else (unwritable home)
`<source checkout>/.data/`.

### `config.json` keys (defaults)

| Key | Default |
| --- | --- |
| `directories` | `[]` (resolved absolute paths) |
| `include_subdirectories` | `true` |
| `enabled_extensions` | `.pdf .docx .doc .pptx .ppt .xlsx .xls .txt .md .csv` |
| `max_snippet_chars` | `80` |
| `db_path` | `<data dir>/index.db` |
| `theme` | `"light"` |
| `language` | `"zh-TW"` (`"en-US"` also valid) |
| `search_debounce_ms` | `300` (clamped 0–2000) |
| `last_updated_at` | `null` |
| `exclude_patterns` | `.git node_modules temp __pycache__` |
| `search_filters` | `file_type, date_field, date_mode, date_from, date_to, size_mode, min_size, min_unit, max_size, max_unit, include_paths, match_case, whole_word, regex` |

### `index.db` schema (no `user_version`; `PRAGMA user_version` = 0)

```sql
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    ctime REAL NOT NULL DEFAULT 0,      -- added in-place by an ad-hoc ALTER for older DBs
    indexed_at REAL NOT NULL,
    total_segments INTEGER DEFAULT 0,
    error TEXT
);
CREATE TABLE doc_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    segment_id TEXT NOT NULL,
    segment_type TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX idx_doc_segments_doc_id ON doc_segments(doc_id);
CREATE VIRTUAL TABLE doc_fts USING fts5(
    doc_id UNINDEXED, segment_id UNINDEXED, segment_type UNINDEXED,
    content, tokenized_content, tokenize='unicode61'
);
```

Connections use WAL, `foreign_keys=ON`, `synchronous=NORMAL`, `busy_timeout=5000`.
Legacy fixtures for migration tests are built by `tests/fixtures/legacy_data.py` (synthetic
paths and text only).

## Development dependency versions observed

jieba 0.42.1, mcp 2.2.0, olefile 0.47, openpyxl 3.1.5, PyInstaller 6.22.3, PyMuPDF 1.28.2,
PySide6 6.11.2, pytest 9.1.1, python-docx 1.2.0, python-pptx 1.0.2, xlrd 2.0.2.

## Known defects found by the audit

- CLI `--dir A` reconciles against every indexed path, deleting entries owned by other roots
  (fixed by Task 0.2).
- CLI exits 0 when the root is unavailable or only one of `--dir`/`--search` is given
  (the latter silently launches the GUI).
