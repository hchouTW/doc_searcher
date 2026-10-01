# Full-folder search coverage extension

Date: 2026-10-01
Status: Approved by the user ("do it"); implemented and verified; see coverage validation record.

## Outcome

Extend the existing local document search application so supported text sources are
searchable across configured folders, scanned PDF pages receive automatic OCR,
and changes become searchable without a manual rescan. Add semantic, synonym,
and typo search while preserving exhaustive literal search and occurrence counts.

The request includes three acceptance goals: all valid occurrences of a keyword
such as `會議`; automatic scanned-PDF OCR; newly created or modified files becoming
searchable within five seconds. Five seconds is a proposed measured target for
small native-text files with an idle worker. Large files, OCR, model startup, and
backlogs require pending/progress reporting and separately measured latency;
an unconditional five-second guarantee for arbitrary documents is not feasible.
This measured scope was included in the design approved by the user.

## Repository baseline

Read-only inspection confirms these existing behaviors; this is not new test evidence:

- SQLite FTS5 BM25 ranking and Chinese character/bigram candidates, verified
  against original text, with document pagination and accurate occurrence APIs.
- Complete original page/sheet/section segments rather than fixed-size chunks.
- XLSX traversal of all worksheets, including hidden sheets, formula source and
  cached outputs. Hidden rows are not filtered. Comments are not extracted.
- DOCX body/table XML order, headers, footers, textboxes, source spans, and warnings
  for unsupported embedded objects.
- PPTX text frames, tables, and notes, but no recursive grouped-shape traversal.
- Native PDF page extraction with partial-failure diagnostics; no OCR.
- Incremental scan/index reconciliation, writer serialization, unavailable-root
  preservation, and quality/problem lists with explicit reprocessing.
- No persistent file observer or dense embedding search.

The earlier `docs/tasks/search-completeness.md` explicitly excluded OCR, vectors,
and synonyms. This extension adds that scope without rewriting that task's history.

## Approaches considered

1. **Extend the current local pipeline (recommended).** Preserve SQLite, original
   segments, source locations, and existing desktop/CLI/MCP adapters. Add OCR,
   event-driven updates, and an optional local semantic index. Minimizes migration
   and preserves the application's local-file workflow.
2. **Move all content into overlapping search chunks.** Simplifies sharing chunks
   with embeddings, but requires deduplicating literal occurrences and rewriting
   pagination/location contracts. Offers no demonstrated keyword-recall benefit
   over the existing untruncated original segments.
3. **External search/vector service.** Adds server deployment and document transfer
   requirements absent from this request. Avoid for this desktop application.

## A. Extraction and OCR

### Office documents

Keep streaming XLSX formula/cache reads. Read comment parts through workbook ZIP
relationships and map ordinary cell comments to worksheet/cell source spans,
including comments on empty cells and hidden rows/sheets. Avoid loading an entire
workbook twice in editable mode merely to read comments. Do not evaluate formulas.
Missing cached outputs remain explicit warnings; indexing source formulas cannot
manufacture their calculated results. Unsupported threaded comments or embedded
objects must produce actionable partial-coverage diagnostics.

Recursively visit PPTX groups, textframes, and tables; preserve slide and shape
locations and avoid merged-cell duplicates. Include applicable text-bearing
layout/master content once per rendered slide, filtering placeholder prompts and
nonrendered objects. Retain notes. Keep DOCX extraction and add regressions for
nested tables and textboxes; report unsupported objects rather than claiming
universal embedded-object coverage.

### PDF and images

Use sorted native blocks with page/source locations and test two-column reading
order and multiple pages. Trigger page OCR when native nonwhitespace text is below
a configurable threshold, and for significant image regions on mixed-content pages.
Sparse native-only pages should not automatically become OCR failures.

Use a local Tesseract subprocess behind a dedicated OCR adapter. Default
languages are Traditional Chinese, Simplified Chinese, and English when available;
surface missing language data or engine setup in diagnostics. Preserve native text
when OCR fails. Use original raster pixels on mixed pages and word geometry to avoid indexing two copies
of native text. Record OCR provenance, failed page locations, and parser version.
Render at a bounded resolution and enforce per-page time/resource limits using
an interruptible worker boundary. Cancellation and shutdown must terminate OCR
work cleanly. Add supported standalone PNG/JPEG/TIFF image parsing using the same
adapter and explicitly define multi-frame TIFF handling.

OCR operates locally. Runtime setup and frozen-build support must be documented
and verified separately; merely mocking OCR is insufficient to accept scanned-PDF
coverage. Password-required PDFs remain encrypted diagnostics.

## B. Structure-aware overlapping chunks

Keep authoritative original segments for keyword search and occurrence navigation.
Generate embedding chunks from paragraphs, sentences, and table rows with a target
of 800 characters, a maximum of 1,000, and about 15% overlap. Split oversized units
with a sliding window; cap at the embedding model's token limit as well.

Each chunk references its original segment and half-open character offsets.
Never combine unrelated pages/sheets merely to fill a window. Deduplicate retrieved
chunks by source interval/document. Chunk boundaries cannot remove literal keyword
matches because literal retrieval continues to inspect original segments.

## C. Hybrid retrieval and query expansion

Preserve literal mode as the default and retain its boolean, phrase, regex,
case-sensitive, whole-word, pagination, and occurrence contracts. Existing Chinese
unigram/bigram verification already covers one- and two-character queries. Measure
before adding trigrams; they are an optimization rather than a recall prerequisite.
Add code-ID/numeric/punctuation regressions and a narrowly scoped stored-text
fallback if the current candidate index misses these literals.

Provide an explicit expanded/hybrid mode, with shared desktop/CLI/MCP options.
Use real local multilingual dense embeddings through an optional provider with a
configured local model path. Select and pin a Chinese-capable model after evaluating
a small judged bilingual query corpus and measuring startup, size, and latency.
Do not use hashing or synonym lookup as a substitute for dense embeddings.
Model setup/download is explicit, with model identity and embedding dimensions
persisted; model changes invalidate old vectors.

Store chunk text/offsets and vector metadata in a transactional schema migration.
Compute embeddings outside the database writer lock, and attach them only if the
source revision still matches. Keyword indexing becomes available before optional
embedding work finishes. Initial vector retrieval may use bounded batched cosine
scoring; corpus benchmarks determine when an approximate index is needed.

Union exact and semantic document candidates, preserve exact hits, and fuse ranks
with stable document-ID tie-breaking. Apply all filters before final pagination.
Report semantic readiness and bounded semantic candidate counts separately from
literal completeness. A conceptual hit without the literal query must not claim a
literal occurrence count or invent highlights; provide its matched passage instead.

Domain synonym dictionaries are explicit equivalence mappings scoped by file type
or document path (the current index has no field schema). Explain which expansion
matched. Levenshtein distance-one expansion applies to bounded dictionary candidates
for sufficiently long words, with candidate caps. Short Chinese queries, numerical
identifiers, quoted phrases, NOT clauses, and regex are not silently fuzzy-expanded.

## D. Automatic incremental updates

Use watchdog observers plus a background queue shared through the existing indexing
service. Watch configured roots recursively according to settings. Coalesce bursts
with a short debounce and handle create, modify, delete, atomic-save replacement,
and move events. Process affected paths directly rather than rescanning every root
for each event. Directory changes schedule scoped reconciliation.

Enforce scanner exclusions, temporary-file rules, canonical paths, supported types,
and the current writer lock. Detect a file changing during extraction and requeue
it; stale text or embeddings must not overwrite a newer revision. Keep unavailable
roots intact and reconcile when they reconnect. Use periodic reconciliation to
recover dropped events and unsupported observer behavior.

The desktop owns observer lifecycle while open. CLI gets an explicit watch mode;
the MCP server owns its long-running observer while running. No indexing occurs
after all app/service processes exit. Root/filter changes restart observers safely.
Batch completion refreshes active searches and invalidates stale continuations
through existing revision semantics. Pause/resume/stop and shutdown remain usable.

## E. Diagnostics and compatibility

Extend existing quality/problem UI, CLI, and MCP rather than introducing a second
diagnostic system. Report OCR unavailable/failed, unsupported Office content,
missing formula caches, watcher errors, pending work, and embedding readiness.
Make discovered, searchable, partial, failed, and pending counts distinct.
Expose unindexed/scanner failures without treating inaccessible files as deleted.

Bump affected parser versions and offer existing explicit reprocessing. Schema
migrations use current backup/transaction conventions and can rebuild chunk/vector
metadata from stored text; new comment/OCR content requires reading original files.
Document optional dependency/model/OCR installation and packaging implications.

## Delivery sequence

1. Office coverage and exact-query regressions.
2. PDF/image OCR adapter, setup diagnostics, and real-engine fixtures.
3. File observer, targeted indexing, lifecycle integration, and latency measurement.
4. Offset-preserving chunks, model provider/storage, hybrid and expanded query modes.
5. Cross-interface diagnostics, migration/reprocessing checks, benchmarks, and docs.

Each increment needs failing regressions, implementation, focused verification,
and review. The complete request remains open until every increment is delivered
and acceptance evidence records any agreed limitations.

## Acceptance and validation

| Requirement | Evidence required |
| --- | --- |
| Exhaustive `會議` matches | Generated nested-folder corpus across native PDF, XLSX hidden sheets/rows/comments/cached formulas, DOCX headers/footers/tables/textboxes, PPTX grouped/layout text, and text files; assert exact document sets and occurrence locations across every result page. |
| Boundary safety | Place keywords around proposed chunk boundaries and inside long paragraphs/table rows; assert original offsets and no overlap-inflated counts. |
| Native PDF layouts | Two-column/multi-page fixtures with known terms and source pages; preserve readable pages when one fails. |
| Automatic scanned PDF/image OCR | Real installed engine and selected language data recognize known printed fixture terms; search returns page/image provenance. Missing engine/language and timeout tests retain native text and actionable partial status. |
| Automatic file updates | Running app/service indexes small created/modified files within five seconds from final write to searchable commit on declared hardware; moves and deletes update without manual scan. Record raw timings across repeated trials. |
| Observer recovery | Atomic saves, event bursts, writes during parsing, overlapping roots, excluded paths, reconnects, dropped events, pause/cancel, and shutdown do not corrupt or remove protected index entries. |
| Semantic and typo support | Real dense-model integration tests on judged Chinese/English synonym/concept queries; typo/domain fixtures; exact identifiers remain included and semantic hits do not inflate literal counts. |
| Compatibility | Existing search/parser/storage/service/CLI/MCP/UI suites, migration rollback, Ruff, mypy, and coverage checks pass. |
| Performance | Compare keyword latency/index size on existing 1k/10k benchmarks; measure OCR and embedding throughput, observer queue delay, memory, and startup separately. |
| Packaging | Verify OCR discovery and optional model-provider behavior on supported platform builds; list any unverified platforms explicitly. |

Zero omissions means zero missing expected matches in the declared acceptance
corpus and supported source types. OCR recognition accuracy is measured and reported;
it cannot guarantee perfect recognition of arbitrary handwriting or damaged scans.

## Primary implementation references

- PyMuPDF OCR: https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html
- PyMuPDF page APIs: https://pymupdf.readthedocs.io/en/latest/page.html
- openpyxl streaming behavior: https://openpyxl.readthedocs.io/en/stable/optimized.html
- watchdog observer quickstart: https://watchdog-docs.readthedocs.io/en/latest/quickstart.html
- Sentence Transformers: https://sbert.net/
