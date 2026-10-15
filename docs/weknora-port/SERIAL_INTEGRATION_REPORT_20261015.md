# Accepted Local / MQ / QA serial integration — 2026-10-15

## Goal and changes

Integrated only the nine independently accepted source commits into `E:\RAG-Worktrees\weknora-python-port-20261013`, branch `codex/weknora-python-port-integrate-image-20261014`, from actual base `2bc2a063a11d1cf3a9e42a9ecfcd55d4124ce3df`. Every cherry-pick uses `-x`; receipt.json contains the exact source→integration SHA map. Pre-receipt integrated source HEAD is `9d0b43c19881e03c992cebbf5ad57a346db9cde3`.

Order: Local 8012cfa→9d9f628→e81c17e; MQ e282b94→be5b109; QA 1530af3→6efc138→f6232e6→c0051c2. MQ contributes only its two commits, not the differences between its older 143ddb74 base and main. No squash or whole-file ours/theirs resolution.

## Shared-file resolution

- `runtime.py` auto-merged. AST equality checks prove Local BoundOrdinaryService.run_turn, ApplicationRuntime.__init__, _make_model and registry_for_context are retained, plus QA _fallback_prompt and _fallback_documents. Both runtime registry factories still call Local _make_model. QA conversation settings and TurnConfiguration fallback remain present. Runtime success hook still reads persisted completed/error-free port_turns before memory scheduling.
- `native_fixture.py` had three textual conflict blocks. Merged the union signature, expected Redis identity+DB15 checks, Local's exactly-one old URL assertion, QA's optional response_handler and its exactly-one wire marker, and the combined namespace. New fixture keys use `serial-integration:20261015:<uuid>`. No historical prefix or evidence was edited. Conflict text is preserved separately.
- Additional test-only integration adaptation: MQ tests retain their legacy expected Redis ID by default but may take explicit WEKNORA_PORT_TEST_REDIS_RUN_ID after strict 40-hex validation. This is an expected identity input, not reading and trusting the current server. Actual INFO must still match it. Old constants/history are preserved. No production MQ timing, cutoff, retry, ACK or UNKNOWN code changed during integration.
- `scripts/weknora_port_serial_verify.py` changes only test evidence destinations through a pytest collection plugin. Local OUT gets a copied identity receipt verified against the explicit expected ID; selected QA test Path constructors redirect evidence paths only. Assertions/provider responses/production storage paths are untouched. Each redirect is recorded. No old report is overwritten.

## Actual commands and exits

CWD is the integration worktree. Python executable is `E:\RAG quention\.venv\Scripts\python.exe -B`; PYTHONDONTWRITEBYTECODE=1, PYTHONIOENCODING=utf-8, existing reader/runtime PYTHONPATH. Redis expected ID is fa7bde76dcd020d396902fa924d0ea3e7b26ceb2. HTTP phase uses the explicit approved isolated PG DSN, not repeated here with credentials.

- Nine `git cherry-pick -x <source-SHA>` commands: all completed. Initial f6232e6 application returned 1 for the three fixture conflicts; after manual resolution `git cherry-pick --continue` returned 0. Raw command outputs/exits retained.
- `python -B scripts/weknora_port_serial_verify.py offline`: exit 0, **253 passed in 3.62s**. Selected Local/controlled lifecycle, generation/P5/protocol/provider stream, service, fallback, profile/configuration and send-gate tests; `offline.inputs.json` lists the exact files and pre-test hashes.
- `python -B scripts/weknora_port_serial_verify.py mq`: exit 0, **27 passed in 1.62s**. Real dedicated Redis DB15, new unique scoped keys, integer-second reclaim/grace, late ACK/stale token, retry/archive and stop behavior. Only these tests' freshly created keys are cleaned; no global flush or old data removal.
- `python -B scripts/weknora_port_serial_verify.py http`: exit 0, **8 passed in 48.62s**. Three Local ASGI/actual PG+Redis/mock wire cases, three ordinary loopback HTTP history/cancel/privacy/budget/scope cases, one nine-subcase fallback wire matrix, one old/new byte-exact Caption module comparison. Local remains MockTransport, not an actual Ollama success. No provider network.
- Final test-file hashes match every phase's pre-test hash; runner hash unchanged. Scope source checks and git diff checks pass. JUnit, raw stdout, per-phase exit codes, redirect records, source receipts and exact commit map are under `evidence/SERIAL_INTEGRATION_20261015/`.

## Protection and boundaries

Before integration, the actual working tree had exactly four unrelated untracked files: Owner DeepSeek handoff and P6 Go-build stdout/stderr/exitcode. Their SHA256/length and local ignored backup locations were recorded before mutation, local copies were byte-checked, and final originals match those hashes. They remain untracked, at their original locations. No backup contents or secrets are embedded in the report.

Previously tracked evidence is unchanged. New evidence uses -text to preserve bytes. Main primary checkout, accepted source branches, old physical DBs/volumes, historical grants and UNKNOWN occupancy are not reset or altered. All PG writes are fresh isolated synthetic owners; Redis tests use DB15 and bounded unique prefixes. Local failed/non-none result receipts still cause zero memory writes/jobs, successful local turn schedules once, replay adds no send. Formal ordinary references read back, second request_id uses persisted history, fallback preserves original FAILED attempts/UNKNOWN and does not add sends. Safety denials remain failures.

Status is OFFLINE_PASS and ISOLATED_INTEGRATION_PASS for these selected integration checks. No whole-engine SOURCE_PARITY_PASS, REAL_PROVIDER_PASS, CUTOVER_PASS or milestone acceptance. Go public KnowledgeQA still awaits the separately managed authorized Linux toolchain/oracle work; no duplicate install here. Real Ollama verification is NOT RUN, with no model pull. Default Docker/worker/React entry and packaging config were not switched. P8 remains preparation only: no deletion, cutover, push, tag, release or deployment.

Next minimum work: independent review of this integration receipt/fixture adaptation; continue public Go same-fixture entry comparison when its separate environment is available, then separately authorized actual Local/provider/cutover gates and bounded P8 entry/package changes. Do not repeat the frozen corpus or change P5 threshold/features/escalation switches.
