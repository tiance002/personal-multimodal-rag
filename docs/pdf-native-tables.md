# Native text PDF tables (M5 scoped adapter)

PdfParser calls PyMuPDF 1.26.4 `Page.find_tables(strategy="lines")` while each
native text page is alive, detaches its rows/cell rectangles/header evidence, and
maps admitted tables to the existing DocumentTable/TableCell and origin-aware
table-row chunker. No new table-detection, OCR, VLM or embedding algorithm is used.
Registry dispatch stays the same. Existing OCR/image assets retain their failure
codes and derivation links. Corrupt open/password failures have explicit parser
codes. Scans never gain table structure from OCR text alone.

## Provenance and retrieval projection

One-based table/page identifiers, table bbox, cell bbox and explicit origin spans
are retained in PDF points, top-left, unrotated coordinates. The adapter accepts
spans only when the API's non-null rectangles align with its own row/column grid,
cover every slot exactly once, and explain null merge placeholders. It does not
invent empty cells for unconfirmed holes. Shared TableCell.bbox and DocumentTable
page/bbox/raw_evidence fields are optional; existing XLSX/HTML/DOCX/DOC lineage
and resource rejection paths keep their behavior.

Each page SourceLocator.raw_text preserves `page.get_text("text")` verbatim.
SourceLocator.quote/start/end describe the derived retrieval projection, rather
than claiming that its offsets index raw page text. Raw find_tables rows (including
empty rows/nulls) and header evidence remain in raw_evidence; cell.native_locator
stores separate get_textbox raw_text and API extracted_text. PyMuPDF folds double
spaces in table extraction. The parser does not rewrite either text or gold.

Retrieval replaces only entire native text blocks wholly contained in an admitted
table bbox. It records every suppressed block's original text, bbox, number and
table ID. A block intersecting a table bbox while extending outside it prevents
that table's admission, preserving prose and marking partial status. Overlapping
table candidates are likewise not admitted. Raw page facts remain accessible in
source locators; they are not added again as full-page retrieval chunks alongside
the table. This geometric policy does not establish semantic table correctness.

Header names come from PyMuPDF heuristics, are explicitly prefixed `INFERRED:` in
rendered evidence, and have semantic status UNKNOWN. column_header/row_header
remain null. A first-row header is used for citation provenance only when API
header cell geometry equals that row; it is not a semantic header confirmation.
Table documents report partial with PDF_HEADERS_INFERRED_UNKNOWN. Numeric-looking
values remain display strings, without guessed spreadsheet typing or formatting.

Existing origin-aware row projection supplies merged origin cells to continuation
chunks without copying raw cells. Table chunk locators carry page, table_bbox and
bbox (union of actual projected origin rectangles; a vertical merge can extend
above the current row). Header origin cells also remain in locator.cells. Mixed
PDF text/OCR chunks retain their page and asset IDs. Retrieval, context and frozen
citation readback use the existing chain with no application/gate changes.

## Support boundaries

| Input | Table retrieval | Status / gap |
|---|---|---|
| Fixed native bordered 5x3 | Admitted cells, empty cell, zero, merge origin and bboxes | Geometry checked; headers UNKNOWN; strict whitespace differs |
| Small native vertical merge | Origin projected into continuation chunk | Verified on SIMULATED fixture |
| Two pages with prose and bordered table | Separate page IDs and evidence, bbox dedup | Verified; no cross-page table stitching |
| Native borderless | Raw text retained; raw text-strategy candidate kept | Partial; extra rows / missing merged header; not admitted |
| Unconfirmed spans / overlapping text/table rectangles | Raw text and candidate retained | Partial; no fabricated row/column cells |
| Empty/image-only scan | Hashed source and failed/successful OCR derivative | Table unsupported regardless of OCR result |
| Embedded image / chart | Image preserved; existing OCR result visible | Table/semantic unsupported; semantic NOT_RUN |
| Rotated page | Raw text retained | Table rotation unsupported |
| CJK/complex layout/cropped or real corpus PDFs | Not evaluated this round | NOT_RUN; no full-PDF support claim |

If lines finds no table, text strategy is diagnostic only. Borderless rows are not
filtered, merged headers are not guessed, and candidates are not promoted to
complete table results. API failures produce explicit partial warnings. The shared
native grid/rendering limits apply before projection; no stress or peak-memory
claim is made about PyMuPDF's internal allocation. PDF scan table extraction,
image/chart semantics and answer generation remain NOT_RUN.

## Validation and rollback

The round uses the unchanged SIMULATED PDF PoC gold plus small generated mixed,
two-page, vertical-merge, empty, scan and corrupt PDFs. Test fixtures and evidence
locations are explicitly injected; plugin autoload/reportplugin and bytecode are
disabled. Outputs/cache/TEMP stay in the round's D directory. Tests verify full
parse -> table -> row chunk -> scoped in-memory retrieval -> context -> frozen
citation page/bbox, and shared XLSX loader_calls=0 plus native origin/hMerge and
SIMULATED DOC conversion lineage. No DB, real services or model/API calls run.

Strict text checks keep the known PyMuPDF double-space mismatch as FAIL. A separate
evaluation-only rule uses `' '.join(value.split())` per non-null cell; it does not
alter extraction, raw text, gold or the parser. Borderless strict row/merge/bbox
failures remain failures, rather than being hidden by removal of empty rows.

Evidence, exact before/after hashes, commands/counts, failure history and rollback
are under D:/RAG-M5-PDF-TABLE-01-rev1attempt1. Restore only this round's four
preexisting target files from before/, after checking current hashes for later
writers, and remove only its helper/test/two new docs. Never git reset/clean the
global dirty worktree. Project LICENSE is unchanged; see NOTICE-PyMuPDF.md.
