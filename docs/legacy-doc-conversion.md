# Legacy DOC conversion adapter: M4 DOC-01 rev1attempt1

Status: rev2 CHECKS_PASSED / NEEDS_OWNER_REVIEW. Production conversion remains disabled by default.
Rev1 FAIL and its initial/unique-repair history remain intact in the D evidence packet.
The existing registry now recognizes application/msword and .doc only when MIME
is absent/generic. No changes to PDF/image/XLSX or other RAG business behavior.

## Admission and conversion

DocParser reads bounded immutable bytes (8 MiB), rejects symlink inputs and checks
OLE magic plus CFB header byte order/version/sector sizes and sector alignment.
This is container admission, not full CFB validation or a new Word parser. Word
recognition is delegated to LibreOffice's forced MS Word 97 importer; RTF or ZIP
renamed .doc is rejected. Original bytes are never replaced.

A converter must be explicitly injected into ParserRegistry(doc_converter=...).
No environment switch or application bootstrap enables it. Without one:
DOC_CONVERSION_UNSUPPORTED. LibreOfficeDocConverter additionally defaults to
isolation_verified=False and raises DOC_CONVERSION_SAFETY_NOT_VERIFIED before
launch. This trusted operator assertion is not a sandbox or security attestation;
only the known synthetic fixture was tested with it true in an isolated container.
Do not enable it for user documents on the basis of this packet.

The adapter follows office_preview's list-argument subprocess/profile pattern,
but does not change office_preview. It uses fixed temporary source.doc/source.docx,
shell=False, DEVNULL stdin, a sanitized child environment, independent temporary
profile and POSIX session/process-group cleanup on every outcome. Other OSes fail
with DOC_PROCESS_ISOLATION_UNSUPPORTED. Timeout defaults to 30s, maximum 60s per
process (version query and conversion are separate, so <=2 process budgets).
stdout/stderr combined <=16 KiB, converted DOCX <=8 MiB, monitored work directory
<=32 MiB. Disk polling is admission monitoring, not a hard disk sandbox; the real
test also imposed container tmpfs/memory/pid/CPU caps. No raw diagnostics enter
ParserError. Missing/empty/oversized output fails explicitly; DOCX admission errors
retain their fixed code under DOC_CONVERSION_FAILED, including HMERGE_UNSUPPORTED.

Profile settings are explicit/finalized: Common/Security/Scripting
DisableMacrosExecution=true, MacroSecurityLevel=3, DisableActiveContent=true,
BlockUntrustedRefererLinks=true, DisableOLEAutomation=true; Writer/Content/Update
Link=2 (Never), Field=false, Chart=false. Headless is not macro disabling proof.
The settings are documented in pinned upstream 25.2.3.2 schemas, but their hostile
macro/external-file behavior has NOT_RUN; successful no-macro fixture conversion
does not establish that boundary. No arbitrary user DOC was opened.

Official sources (read during this task):
- https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html
- https://raw.githubusercontent.com/LibreOffice/core/libreoffice-25.2.3.2/officecfg/registry/schema/org/openoffice/Office/Common.xcs
- https://raw.githubusercontent.com/LibreOffice/core/libreoffice-25.2.3.2/officecfg/registry/schema/org/openoffice/Office/Writer.xcs

## Provenance and remaining failure

Conversion lineage records original DOC hash, converted DOCX hash, actual
converter version, DOCX parser version, adapter version and converted-coordinate
basis. Added model fields propagate lineage through chunk locators/citation JSON.
Converted results are always partial with DOC_CONVERTED_LAYOUT_UNVERIFIED and
retain original DOCX warnings. No original DOC page number is manufactured.

The rev1 wrapper changed table.source_format from docx to doc after DOCX
normalization. table_row_texts includes that format in its rendered prefix, so
chunk_document raises TABLE_SOURCE_SPAN_MISMATCH. This was the sole rev1 failed test. Rev1 stopped after its one directed repair.
The parent explicitly authorized rev2 as one provenance contract correction,
without resetting that history: table and locator formats now remain docx,
rendered content/block offsets/cell origins are byte/structure equal to the mature
DOCX parse, and original DOC identity/hash/converter version live in lineage.
The existing span validator is unchanged. The corresponding counterexample now
checks the original DOC -> converted DOCX -> native cell + normalized fragment ->
citation chain; no original DOC-native cell coordinate or page is claimed.
DOCX HMERGE remains explicitly unsupported and prior M3 counterexamples remain
in the 90-case regression selection.

## Actual verification and limits

D evidence: D:/codex-rag-tools/native-table-m3-rev1attempt1/m4-doc-01/
All pytest plugin autoload disabled; cacheprovider disabled; log/XML/basetemp D;
audit hooks deny external network and writes outside this D task. Windows
TestClient internal socketpair remains the prior approved exception.

Initial pytest: exit 1, 104 passed, 1 failed, 1 skipped (missing test-only renderer
comparison path). Directed UTF-8 write completion/lineage propagation repair:
final pytest exit 1, 105 passed, 1 failed, zero skipped, 6 dependency warnings.
A repeated basetemp attempt separately failed 69 setup checks due to Windows
extended-path cleanup being rejected by the audit hook; retained environment-error
log, then used a fresh D basetemp without changing the audit policy or source.

Official approval allowed only scoped Docker actions. Existing image f16fe86da0ae
reported LibreOffice 25.2.3.2 520(Build:2). A temporary --rm, --pull=never,
--network none, --read-only container with no ports, cleared process environment,
nonroot uid, dropped capabilities, no-new-privileges, 512MiB memory/1CPU/64PID,
128MiB tmpfs, and only the D synthetic evidence bind mount ran successfully.
No production container mount, API/DB interaction, restart, image pull or host
permission/network change occurred. An unapproved image-inspect attempt was
sandbox-denied; no alternate socket/account/ACL was attempted.

Real test exit 0: 7 checks comprising known synthetic DOCX -> OLE DOC -> DOCX,
timeout, log limit, DOCX output limit, work disk limit, nonzero exit, and descendant
process-group cleanup. Frozen truth preceded both conversions. Both generation
and conversion can lose layout/structure. Host downstream diagnostic verifies
real artifact hashes and all four table cells plus surrounding paragraphs through
the actual mature DOCX worker/chunk chain; replaying the legacy wrapper explicitly
reproduces the same failure. This is synthetic-source real conversion, not private
material QA, and the replay is not an in-container end-to-end production run.

NOT_RUN: hostile macro/external-link/external-file boundary, arbitrary user DOC,
live ingestion/storage/DB/API, original DOC pagination, generated-answer quality,
full suite/milestone/release checks, production enablement/deployment.

No software/models installed or downloaded. LibreOffice is used as an installed
external process; no LibreOffice source redistributed. Its MPL-2.0 and bundled
third-party notices must be retained by distributors (official notices:
https://www.libreoffice.org/about-us/licenses/). Existing python-docx/BS4 runtime
license notes remain in native-table-adapter.md. Project LICENSE is unchanged.
No commit/push/deployment/paid model calls; 7 paid calls/855 tokens preserved.

Rollback: use the hash-guarded D rollback.py --apply only after reviewing its
printed exact scope. It restores the three preexisting files from this attempt's
before/ snapshot and removes only this attempt's adapter/test/document. Never
git reset/clean or overwrite unrelated dirty work.


## Rev2 provenance correction and retained evidence

Rev2 changes only the two source-format overrides in DocParser, the corresponding
existing test, and this document. No registry/model/chunking/security changes.
The strengthened test asserts converted rendering/offsets/cells are unchanged,
both artifact hashes and converter/parser lineage reach citation JSON, native
cell origins describe the converted DOCX, and cited fragment offsets select the
exact quote. Test count remains 106.

Actual results: red counterexample exit 1 (1 failed, 15 deselected); final selected
regression exit 0 (106 passed, 0 failed/skipped, 6 existing dependency warnings).
The standalone offline replay of previously stored REAL conversion artifacts also
passes frozen truth, immutable hashes, table/chunk/citation lineage. It invokes no
Docker and does not rerun conversion. Evidence lives under the prior D folder's
rev2/ directory. Rev1 105PASS/1FAIL and its 7 real checks remain unchanged; this is
an explicitly authorized contract revision, not a reset of attempt/repair counts.

Saved Docker facts only: the image-list, version-container, and real synthetic
container commands were formally approved through require_escalated and exited 0.
Both containers used --rm, --pull=never, --network none, --read-only, no ports,
nonroot uid, dropped capabilities and no-new-privileges. Version container:
512MiB/1CPU/32PID/64MiB tmpfs/no bind mounts. Conversion test container:
512MiB/1CPU/64PID/128MiB tmpfs, only D synthetic evidence mounted, child process
environment cleared. Both run commands completed normally with automatic removal
requested. No separate post-run docker ps/removal observation was saved, so current
container inventory is NOT_RUN rather than independently verified empty. Rev2 made
zero Docker calls and performed zero cleanup. No production-container operation.

Security readiness remains NOT_RUN for hostile macros and external-file/link
behavior. The default registry injection and isolation_verified=False gates are
unchanged. No arbitrary user DOC, API/DB, service/configuration/install/commit or
paid call was accessed or changed. This result is CHECKS_PASSED for the scoped
provenance correction, not production enablement or milestone acceptance.

Rev2 rollback: its D rev2/rollback.py restores exactly DocParser, the corresponding
test and this document to the rev1 snapshot after checking current hashes; dry-run
by default. The older rev1 rollback must abort on later rev2 source changes, as
intended. No rollback or cleanup was executed.
