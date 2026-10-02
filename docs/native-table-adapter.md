# Native HTML/DOCX table adapter (M3)

The registry now uses BeautifulSoup and python-docx through one local stdin/JSON
worker. It reuses M2 DocumentTable/TableCell, table-row chunks, persisted locator
JSON, ContextBuilder and CitationService. It does not add another retrieval stack.

## Explicit runtime selection

No runtime path is built into business defaults. Select an absolute Python
executable using `ParserRegistry(native_python=Path(...))`,
`HtmlParser(python=Path(...))`, `DocxParser(python=Path(...))`, or the process-local
`RAG_NATIVE_TABLE_PYTHON` environment variable. Existing user configuration was
not changed. Installed dependencies alone do not enable this path.
`NATIVE_RUNTIME_NOT_CONFIGURED`, `NATIVE_RUNTIME_UNAVAILABLE` and sanitized worker
errors fail closed. The source file is read once into bounded bytes and unchanged;
no fallback claims complete flat-text parsing. The application retains the original
upload via its existing ingestion error path; live storage/DB behavior is NOT_RUN.

The selected runtime needs beautifulsoup4 4.15.0 (MIT), python-docx 1.2.0 (MIT),
and defusedxml 0.7.1 (PSF license); python-docx depends on lxml (BSD) and
BeautifulSoup on soupsieve (MIT). This round installed nothing, copied no library
source, and did not change the project's LICENSE or primary environment. Existing
PoC packages.json retains direct parser license-file hashes. Distributors must
retain each dependency's own required notices in the environment they distribute.
Docling aggregation/PDF dependencies remain blocked and are not invoked.

## Wire and admission contract

`native_worker.py html|docx` accepts bytes on stdin only, returns UTF-8 JSON on
stdout, and uses exit 2 with a fixed error code on rejection. No input pathname,
source URL, temporary source file or shell command is accepted. The host uses an
argument list with shell=False, -I/-B, a small environment allowlist (Windows
system paths and TEMP/TMP only), a default 20 second timeout (maximum 60), at most
4 MiB stdout and 8 KiB stderr. Both streams are read concurrently; overflow or
timeout kills/reaps the worker. No environment values or dependency tracebacks
are returned to callers. The explicitly configured interpreter and local worker
code are trusted executable configuration; this is not an OS sandbox for arbitrary
executables. There is no background service or dependency auto-installation.

Version 1 neutral JSON has exactly schema_version, format, source_sha256, blocks,
tables and warnings. Text blocks carry kind/text; table blocks carry kind/table_index.
Tables carry dimensions, cells and optional caption. Each cell carries raw text,
zero-based half-open row/column spans, nullable column_header/row_header, original
header_evidence and a document-relative native locator. The host rejects extra
schema keys, wrong version/format/hash, noncontiguous table references, overlapping,
out-of-range or incomplete grids. No page key is accepted. Metadata dictionaries
are bounded by the total transport budget and remain inert provenance.

Admission budgets: input <=8 MiB; DOCX ZIP <=2000 members, <=32 MiB expanded,
<=8 MiB/member, ratio <=100; no encrypted/duplicate/path-traversal members.
All XML/rels is defused with DTD, entities and external access forbidden before
python-docx reads the same in-memory bytes. <=100000 XML nodes/member. Macros,
ActiveX, embeddings, fields/altChunk/object and external relationships are rejected.
No ZIP extraction, relationship fetch, formula evaluation or macro execution occurs.
HTML is UTF-8 local bytes parsed by html.parser; script/style/template and external
image/iframe/object/link content is ignored without fetching or executing it,
with explicit partial warnings. HTML nesting depth <=200, nodes <=100000.
Tables <=64, rows <=1000, columns <=200, total grid slots <=50000, unique cells
<=20000, text <=4096/cell and <=1 MiB total. Nested and ragged tables fail closed
with explicit unsupported diagnostics rather than silently flattened output.
These are conservative admission limits, not measured peak-memory guarantees.

## Semantics and existing chain

Native cell text is preserved separately from rendered evidence, including blank,
literal zero, percentages, negative values and whitespace. Native coordinates are
one-based RnCm; spans preserve original merge origins and DOCX continuations.
HTML header roles use explicit scope or the documented leading-all-th policy.
DOCX uses w:tblHeader only; absent/false markers remain UNKNOWN, including the
frozen fixture. There is no layout engine and DOCX page stays null.

Ordered text/table blocks cover the entire normalized content. Native row chunks
carry column-header context and raw cell provenance into citation JSON; surrounding
paragraph chunks remain in source order. parse_status/parse_warnings survive in
chunk locators so partial parsing is not represented as complete evidence.
The existing XLSX table-only coverage check and rendering remain unchanged.
TXT/Markdown and existing PDF/image parsers remain in the registry.
Native row evidence is grouped by unique cell origin row; a vertical merge retains
its full span and continuation positions rather than creating duplicate raw cells.
Oversized evidence rows fail with TABLE_ROW_TOO_LARGE rather than splitting them.

## Offline verification and limits

Synthetic frozen fixture directory and interpreter are explicitly injected via
RAG_NATIVE_TEST_FIXTURES and RAG_NATIVE_TEST_PYTHON. Tests skip native execution
when those test-only inputs are absent; an installed dependency does not count as
verification. RAG_NATIVE_EVIDENCE_DIR optionally writes actual chain artifacts.
All actual tests/logs/backups in this round reside in the current D evidence folder.
The final harness disables plugin autoload before collection and audits D-only
writes/no external network. Windows TestClient internal socketpair is permitted;
real API services, DB, browser and model/answer-quality QA are NOT_RUN.

Legacy .doc, native PDF table extraction, scans/OCR, full visual parsing, DOCX
pagination, nested/ragged tables and external/active DOCX content are not covered.
No mock answer is presented as real QA. Header acceptance for unmarked DOCX is
UNKNOWN; HTML/DOCX structure acceptance remains an owner decision.

Precise rollback: restore only the five modified preexisting files from this round's
D before/ directory, reconcile later writes first, and remove only this round's two
new adapters, one test module and this document. Do not reset/clean the worktree,
restore XLSX backups over reviewer changes, or touch report/archive/credentials.
