# Full-folder search

The default literal search uses SQLite FTS5/BM25 and verifies candidates against original text. Original page, sheet and section text stays intact: chunk boundaries never constrain literal matches. Chinese character/bigram candidates and raw substring fallback for one- or two-character queries avoid tokenizer omissions. Existing boolean, phrase, regex and occurrence navigation remain available in literal mode.

## Extraction

XLSX reads every worksheet, including hidden sheets and rows, formulas, saved formula results, and ordinary cell comments—even on otherwise empty cells. Formula results require a cache saved by Excel or another spreadsheet application; formulas and macros are never executed. Comments and values retain worksheet/cell provenance. DOCX includes headers, footers, nested tables and textboxes. PPTX includes grouped shapes, tables, notes and visible layout/master decorations, excluding inherited placeholder prompts and hidden master shapes.

PDF native text is sorted by position. Scanned pages, outlined text and significant image regions on mixed pages receive local OCR. Mixed pages use original raster pixels; geometrically overlapping native/OCR words are deduplicated so existing OCR layers do not inflate counts. Readable native text survives an OCR failure. PNG, JPEG and multi-frame TIFF also use OCR. OCR text has page/image provenance but recognition accuracy depends on image quality and layout.

Install Tesseract and its `eng`, `chi_tra` and `chi_sim` language data. On macOS:

```bash
brew install tesseract tesseract-lang
```

For other platforms install the Tesseract executable and language data through the platform's package manager/installer. Optional environment settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOC_SEARCHER_TESSERACT` | discovered executable | Explicit Tesseract path |
| `TESSDATA_PREFIX` | Tesseract default | Language-data location |
| `DOC_SEARCHER_OCR_LANGUAGES` | `chi_tra+chi_sim+eng` | Languages, joined with `+` |
| `DOC_SEARCHER_OCR_TIMEOUT` | `30` seconds | Per-image timeout, clamped to 1–120 seconds |
| `DOC_SEARCHER_OCR_MIN_CHARS` | `40` | Sparse-page threshold; mixed image regions are also checked |

Rendering is capped at 12 million pixels. OCR subprocesses are interrupted during cancellation/shutdown. Missing engines/languages, unreadable/encrypted files, failed pages, unsupported embedded Office objects, missing formula caches and threaded comments appear in extraction diagnostics. The application does not decrypt documents or guarantee extraction of every embedded object. Blank images can produce no recognized text.

## Automatic updates

Desktop and MCP monitor configured folders while running. Desktop's auto-update checkbox pauses/resumes monitoring. CLI monitoring runs until Ctrl-C:

```bash
doc-searcher --watch --dir /path/to/docs --search '會議'
```

File events are debounced for 350 ms; create/update/move/delete use the serialized index writer. A file changed during parsing is retried. Configured exclusions, hidden directories and folder ownership apply to event updates. Unavailable folders retain their records. Folder events reconcile the owned folder scope; this can cost a full scan. Periodic reconciliation recovers missed events. macOS uses watchdog polling at 500 ms to avoid a native FSEvents shutdown crash; platforms without watchdog reconcile approximately every second (including macOS Python 3.14, where the wheel is unavailable).

The measured five-second target applies to small native-text changes with an idle worker. Large files, OCR, initial scans and queues can take longer. Diagnostics show pending work, watcher errors and semantic indexing state. Application shutdown ends background monitoring; it is not an operating-system service.

## Expanded and hybrid retrieval

Select **Literal**, **Expanded**, or **Hybrid** in the desktop search controls. Expanded mode adds configured synonyms and one-edit English typo candidates for words of at least four letters. Numeric/code identifiers and short Chinese queries are excluded from fuzzy expansion. Hybrid adds local dense passage retrieval. These modes accept simple queries; use literal mode for boolean, quoted phrase, filename or regex syntax.

Literal results keep their original occurrence counts and rank before related passages. Related results report `matched_by` and original-text `passages`; they do not fabricate a literal occurrence. Semantic candidates are bounded to 1,000 passages and similarity ≥0.65, so conceptual retrieval is ranked and bounded rather than exhaustive. Exact matches remain unbounded except for document pagination. Index/model changes invalidate pagination cursors.

Domain dictionaries can be passed with `--synonyms /path/to/synonyms.json` or `DOC_SEARCHER_SYNONYMS`. Example:

```json
{
  "terms": {"會議": ["會談", "meeting"]},
  "scopes": [
    {"path": "/path/to/hr", "file_type": "docx", "terms": {"休假": ["假期", "leave"]}}
  ]
}
```

Keys and values form equivalence groups. Scoped entries apply only to matching file types and folders. Dictionary files refresh when their modification time changes.

```bash
doc-searcher --dir /path/to/docs --search '會談' --mode expanded --synonyms /path/to/synonyms.json --json
```

For dense retrieval, install the optional provider and download `intfloat/multilingual-e5-small` once. Model downloads are a separate setup operation; searches load local files only and run on CPU.

```bash
pip install -e '.[semantic]'
python -c "from huggingface_hub import snapshot_download; snapshot_download('intfloat/multilingual-e5-small', local_dir='/path/to/models/multilingual-e5-small')"
export DOC_SEARCHER_EMBEDDING_MODEL=/path/to/models/multilingual-e5-small
doc-searcher --build-vectors
doc-searcher --dir /path/to/docs --search 'annual paid leave policy' --mode hybrid --json
```

CLI also accepts `--embedding-model DIR`. The local E5 adapter uses `query:`/`passage:` prefixes and normalized embeddings. Passage chunks use natural paragraph/sentence/row boundaries, target 800 characters, maximum 1,000, approximately 15% overlap, and the model's ≤512-token limit. Chunks retain original offsets. Vector publication checks the document revision; background maintenance reuses unchanged segments. First hybrid use can rebuild vectors synchronously; native indexing has a separate worker. Missing model/provider settings produce an actionable error.

Frozen builds include OCR adapters and watcher/image modules, but require an external Tesseract installation and language data. Semantic dependencies are optional at build time: set `DOC_SEARCHER_PACKAGE_SEMANTIC=1` when building with the semantic extra installed; model files remain external. These packaging changes have not been validated by producing installers in this change.

Validation and measured performance: [coverage validation](benchmarks/full-folder-coverage-validation.md).
