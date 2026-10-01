# Changelog

## Unreleased

- Automatically index folder changes in desktop/MCP, with CLI `--watch`, pending/error diagnostics and safe retries for files modified during extraction.
- Local Tesseract OCR covers scanned/mixed PDFs and PNG/JPEG/TIFF; existing PDF text layers are geometrically deduplicated. Missing runtimes/languages and failed pages remain visible in diagnostics.
- Tesseract OCR is off by default: enable the desktop **Tesseract OCR** checkbox (saved as `ocr_enabled` in `config.json`, also used by MCP) or pass CLI `--ocr`. Skipped pages/images are reported as `ocr_disabled` warnings and are re-extracted automatically once OCR is enabled.
- XLSX ordinary comments include empty hidden cells; PPTX groups, notes, tables and visible inherited decorations retain source locations. Unsupported embedded content produces warnings.
- Explicit expanded/hybrid modes add scoped synonyms, English typo matching and optional local multilingual E5 dense retrieval with source-aware overlapping chunks. Literal occurrence counts remain authoritative. Schema 6 stores revision-checked vectors.
- Setup and measured acceptance evidence: [full-folder search](docs/full-folder-search.md), [validation](docs/benchmarks/full-folder-coverage-validation.md).

- Faster “Clear all folders”: clear stored text and both search indexes in one transaction, with bounded progress updates and atomic rollback on failure.
- Chinese literal terms match contiguous original text independently of jieba token boundaries, including single characters and Traditional/Simplified equivalents.
- Search limits apply to documents, with stable continuation and stale-index detection. All matching segments of selected documents remain accessible.
- Hit counts now represent original-text occurrences, rather than snippets. Overlapping positive matches merge; adjacent matches remain separate; NOT terms do not add hits.
- Desktop previews load occurrence context in background workers. Previous/next reaches individual occurrences; larger original segments load in bounded pages. Regex zero-width hits have a visible caret.
- DOCX extraction includes body/table order, shared headers/footers and text boxes. XLSX extraction preserves formulas, cached results, worksheet/cell sources and missing-cache warnings without executing formulas.
- PDF page failures retain readable pages and report partial extraction. Extraction quality, warnings, parser versions and source locations persist in schema 5; older records have unknown quality until reprocessed.
- Desktop, CLI and MCP expose quality lists and explicit selected-document reprocessing. Existing search calls remain usable; clients relying on the old snippet-based `match_count` must update.

Release version selection remains with the maintainer. Validation and measured limitations: [search completeness validation](docs/benchmarks/search-completeness-validation.md).
