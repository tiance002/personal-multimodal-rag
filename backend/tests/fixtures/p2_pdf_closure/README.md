# P2 PDF offline closure fixtures

These are **SIMULATED synthetic documents**, decoded by real PyMuPDF and the
production PDF parser. They are not a real-corpus/OCR/VLM accuracy benchmark.

`tables.pdf`, `gold.json`, and `scan_table.png` are unchanged frozen fixtures
from the historical project round documented in `docs/pdf-native-tables.md`:
`D:/RAG-M5-PDF-TABLE-01-rev1attempt1/fixture`. Its `harness/prepare.py` records
the original source as `D:/RAG-PDF-TABLE-POC-01-rev1attempt1/fixture`. Both copies
and the historical artifact manifest were checked before copying. Per-file
byte counts, SHA-256, and source manifest identity are in `provenance.json`.
Tests now use this repository directory by default; no D: path is needed.

The five PDF pages contain a bordered table, a horizontally merged bordered
table, two borderless candidates, and a raster-only scan. Designed table/cell
geometry and original strings are frozen in gold; they are not regenerated from
the extractor output. PyMuPDF folds double spaces in table extraction. Tests
retain exact source/raw spaces and check the separate normalized projection;
they do not claim API extraction preserves those spaces.

`backend/tests/pdf_closure_fixtures.py` generates additional in-memory PDFs.
`complex_span_pdf()` removes two internal line segments from a fixed 3x3 design,
creating an overlapping nonrectangular merge candidate. It uses no extraction
results or random data to generate the input, and `no_new_id=True` makes repeated
generation byte-identical in the installed PyMuPDF version. `page_failure_pdf()`
creates a blank or raster-only page, optionally followed by native numeric text.
The tests write these bytes only to pytest temporary paths and decode them again.

OCR isolation is explicit: an absent tessdata directory exercises the production
`OCR_UNAVAILABLE` branch before OCR execution. SQLRecorder in the no-content
tests is **SIMULATED DB**; parser, CAS storage, production `process_job` failure
branch, chunks, scoped retrieval, context, and citation logic execute offline.
This does not prove actual DB transaction/concurrency or real OCR behavior.
