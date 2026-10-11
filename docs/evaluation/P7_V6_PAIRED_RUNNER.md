# P7 v6 paired cohort runtime

`paired-simulate` and `paired-run` both call
`eval_center.public_runner.run_public_paired_cohort`. They share source
validation, admission, the cohort loop, immutable Run/Attempt identity,
checkpoints, case scoring and macro aggregation. `paired-run` has an admitted
execution branch; an absent runtime returns `BLOCKED_ZERO_SEND` before reading
settings/keys, constructing a database engine or invoking QuickChain.

The offline runtime uses the project `LangChainQuickChain` Runnable and real
router/GenerationRole code with explicit SIMULATED provider, capacity, lifecycle
and evidence fixtures. The cheap role is SiliconFlow Xing; the expensive role
is DeepSeek. This exercises execution contracts, not real model/retrieval
quality. Simulation evidence hardening and finalization are fixtures. Retrieval
and citation readback stay `NOT_RUN`; generated fixture text is never REAL.

```powershell
& '.\.venv\Scripts\python.exe' -B -X utf8 -m eval_center.public_benchmark_cli --phase paired-simulate --run-id <unique-run-id> --state-path <new-E-drive-attempt-directory>/center.sqlite3
```

The default new state location is
`var/reports/v6-cohort-runtime-r1/cli-default/run_ledger.sqlite3`. Explicit paths
are preferred for evidence runs. The existing Evaluation Center SQLite registry
and existing `paired_runs`, `paired_attempts`, `paired_case_results` tables share
that file. No second center or authoritative billing ledger is created.
Simulation ledgers are visibly labelled, per-run fixtures under the new state
directory; they never point at either canonical provider ledger.

Owner composition injects a typed `PairedRuntime` through the exact public CLI
entry `main(argv, runtime=runtime)` (or `_run(args, runtime=runtime)` in tests).
The evaluator does not discover credentials or manufacture authorization.

```python
from eval_center.public_benchmark_cli import main
from eval_center.public_runner import PairedRuntime

# Assemble these dependencies in the separately authorized owner composition.
runtime = PairedRuntime(
    containers=containers_by_dataset,  # ordinary application containers
    scopes=server_scopes_by_dataset,
    cloud_allowed_by_kb=observed_kb_egress,
    dispatch_guard=check_current_account_egress_budget_and_single_writer,
    execution_kind="REAL",
    capacity_probe=measure_with_both_production_counters,
    index_bindings=observed_readonly_index_bindings,
    index_manifest_sha256=frozen_index_manifest_sha256,
    source_id_for_chunk=map_chunk_to_public_source_document,
    database_queries=observed_backend_query_count,
    provider_observations=observed_provider_transport_counts,
    configure_deadline=bind_synchronous_database_and_telemetry_deadline,
    langfuse_client=approved_bounded_client_or_none,
)
exit_code = main([
    "--phase", "paired-run", "--run-id", owner_run_id,
    "--state-path", new_attempt_state_path,
    "--index-manifest", frozen_index_manifest_path,
], runtime=runtime)
```

Each dataset's index binding is `{expected, actual, scope}`. `scope` is the
observed server `Scope`; `expected` and independently observed `actual` are
consumed by the existing `validate_index_identity` read-only verifier. Dataset,
version, corpus/development hashes and scope are checked against the frozen
manifest. The actual manifest file must match its injected SHA256.

`capacity_probe()` returns `{chat_cheap: estimate, chat_expensive: estimate}`
from actual counters, not ready flags. Both estimates must report production
readiness. Role checks still validate exact provider/model/typed bindings;
per-request prepare/reserve/settle remain inside the existing provider adapters.
DeepSeek uses `V6GenerationBinding(grant, activity_gate, capacity_counter)`;
SF uses `V6XingGenerationBinding(grant, capacity_counter, authorized_transport)`.
The envelope wrapper passes the binding's existing grant scope to each role,
without creating an evaluation grant from a product KB Scope.

The injected dispatch guard is synchronous and read-only; it must fail closed
on current egress, budget, account, index freeze or shared-writer failures.
`configure_deadline(absolute_utc_datetime)` must bind bounded synchronous DB
and telemetry I/O. These callbacks are owner integration responsibilities;
callable checks alone are not independent evidence of target readiness.
The evaluator creates no background dispatch/telemetry threads. Each provider
envelope has the same 30s timeout across the three arms, and cancellation and
deadline guards cover generation, scoring and evidence readback. A new QID
requires at least 95s remaining in the round.

Per QID, project retrieval, selected evidence and context are captured once and
copied for all three arms. Scope is asserted again for real retrieval/context
chunks. Only the question enters generation; answers and qrels stay in local
identity/scoring. CHEAP_ONLY, DYNAMIC at 0.35, and EXPENSIVE_ONLY disable every
escalation/fallback option. Memory and query-understanding injection are disabled
in request-local QuickChain copies. The final prompt rejects memory/history
markers. Original containers are not mutated.

Each Attempt is committed before QuickChain. Actual prompt/context/envelope
hashes are additionally committed before provider admission and compared to
previous arms; divergence is denied before another send. Same-ID recovery
reuses complete results. Pending/unknown/failed/blocked attempts never resend.
A crash leaving a partial QID is retained as `CARRIED_NO_RETRY`; missing cells
are not automatically generated with newly retrieved context. A failed cell
remains in the matrix and cannot turn into a passing answer score.

Dispatch has a 30-minute deadline; the round has a 40-minute deadline, both
capped by `2026-10-10T04:25:44Z`. Same-ID recovery preserves original deadlines.
After cutoff, recovery may read durable outcomes, but aggregation is marked
`NOT_RUN_DEADLINE_REACHED` rather than performing new scoring/readback.

Only eight LongBench rows have answer Gold. The other sixteen QA rows remain
`NOT_SCORED_NO_ANSWER_GOLD`. All 24 have qrels. Real retrieval/context metrics
use the captured ranking and an explicit public-source-document mapper; they
are `NOT_RUN` without an actual retrieval observation. Macro means count only
defined numeric dimensions and report evaluated/unavailable denominators.

Langfuse is optional and content-free. `retry_public_paired_telemetry(
state_path=..., run_id=..., client=..., work_guard=...)` retries only saved
telemetry with stable IDs. It accepts no runtime/model container. Original case
evidence stays immutable; Attempt telemetry receipt history is preserved.
No real Langfuse calls were made in this task.

Backend meet4 now provides guarded fixed-arm policy admission, native
answer/helper claims and final wire checks. The evaluator passes the configured
DeepSeek server Scope exactly and uses normal composition below. Live hosted
capacity, exact grants/account/monthly-budget authority and observed read-only
indexes still require target evidence; no production-ready boolean is changed.

Historical evidence is preserved: v16 previously recorded 24 QIDs/72 SIMULATED
arms and same-ID recovery. Its result SHA256 was
`3498079ce9fb2f797b4a65aef43bf65baf8045dbdd52adda5529ca83e0c71669`;
source snapshot was
`134ee1b6b9a76efa733b4b0fe4a294a4580041f204f36d81c36008962c54da3d`.
The old JUnit path was overwritten by three invocations. Only its latest
18-pass content is evidence; prior original bytes/hash are UNKNOWN and cannot
be recovered or backfilled. Owner E-combination SHA remains an unverified owner
assertion, not local source proof. No old cohort/Gold/ledger/evidence was written.

The current task's commands, new JUnit files, source hashes and reports are in
`var/reports/v6-cohort-runtime-r1/attempt1/`. This is a
`MANUALLY_SUPERVISED_TRIAL`; offline CHECKS_PASSED does not grant owner acceptance,
live model quality, milestone approval, deployment or publication.

## Provider/helper send-claim addendum

The shared SQLite now has provider-call children under the existing Attempt,
with stable request IDs and purpose/ordinal identities for answer, understand,
rewrite, expansion and calibration. UNKNOWN stays consumed. The normal backend
can inject CurrentProviderSendClaims via its explicit send_claim_callback hook;
SQLiteProviderSendClaims owns all persistence. The runner binds the synchronous
scope and avoids a duplicate upper claim when the native callback is connected.
Calibration uses a separately labelled CALIBRATION Run in the same state file,
never cohort scores. Exact signatures, connection points, tested limits and the
minimum missing backend policy-admission change are recorded in
var/reports/v6-cohort-runtime-r1/attempt1/claim-addendum-1/send-claim-contract.md.
The proposed validate_generation_policy backend method and native transport hook
are not implemented by this backend-read-only task. Counters/target grants/index
conditions are tracked separately from those code interfaces.

## Review fix: deadline checks and answer Gold counts

`claim_provider_send` reads the clock after `BEGIN IMMEDIATE` acquires its lock,
and checks again after COMMIT/connection cleanup. Expiry after a commit leaves
the claim consumed; pending/unknown outcomes never gain automatic retries.
The evaluator wrapper rechecks the dispatch guard and the consumed claim just
before calling the provider role. Native transports must call the additive
`CurrentProviderSendClaims.check_before_send(request_id=...)` (or its bound
SQLite equivalent) at their actual wire send, after reservation/lock/validation
work. It delegates to `check_provider_send_before_send` in the same SQLite;
it checks run/Attempt scope, pending receipt and current dispatch/round/absolute
deadlines and never inserts or releases a claim. Existing __call__/settle
signatures are unchanged. Backend transport changes remain separately owned.
The fixed absolute cutoff stays `2026-10-10T04:25:44Z`.

`answer_gold_denominator` and `answer_gold_frozen_rows` count the frozen Gold
QIDs (8), regardless of arm failures. `answer_scoring_by_arm` reports evaluated,
failed and not_scored Gold QIDs, which sum to that fixed denominator per arm;
the 16 no-Gold rows are separate `not_scored_no_answer_gold` counts. Failed
includes explicit failure/blocked/unknown receipts; pending or absent results
remain not_scored. Each arm's macro still uses its defined numeric dimensions
and its actual evaluated/unavailable counts. The legacy
`answer_gold_scored_rows` remains the Dynamic evaluated count for compatibility;
it is not the frozen Gold denominator. Red/green commands, source hashes and
JUnit are isolated in `var/reports/v6-cohort-runtime-r1/attempt1/review-fix-1/`;
the prior 47 PASS evidence remains unchanged.

## Final backend meeting and normal CLI composition

DeepSeek receives `V6GenerationBinding.server_scope` only when it exactly equals
the Scope used for retrieval. SF receives the exact `XingChatGrant.scope` Scope.
Neither arm accepts a different KB, wider document scope, union of scopes or a
grant string in place of the configured server Scope. REAL admission compares
both model scopes with the observed index Scope. The frozen question/index/Gold/
prompt identities and Dynamic 0.35/no-fallback policy remain in the common loop.
SF's normal monthly UsageGuard lives on its gateway; the evaluator checks that
actual guard rather than requiring a duplicate guard field on GenerationRole.

Both normal gateways expose `gateway.transport.send_claim_callback`. The
runner binds its existing SQLite Attempt with CurrentProviderSendClaims and
leaves claim/settle to the native sender. Each actual answer/helper retains one
provider child. Final wire `check_before_send` is supplied by the frozen backend;
the old outer fallback remains only for explicitly controlled legacy fixtures.
SQLiteProviderSendClaims/CurrentProviderSendClaims signatures remain unchanged.

The normal executable entry is:

```powershell
& '.\.venv\Scripts\python.exe' -B -X utf8 -m eval_center.public_benchmark_cli --phase paired-run --run-id <owner-run-id> --state-path <existing-center-new-attempt.sqlite3> --index-manifest <frozen-index-manifest.json> --runtime-entry <reviewed-target-composition.py> --runtime-entry-sha256 <its-exact-sha256>
```

`--runtime-entry` is an explicit owner-reviewed operator composition module,
not a product Agent tool. It is optional; without it/in-process injection the
CLI performs zero-send rejection. Exact bytes/hash are checked before executing
the module, and the hash joins the immutable Run runtime identity. It must
define `compose_public_paired_runtime(*, state_path, index_manifest_path)` and
return the exact PairedRuntimeComposition type. Module/factory initialization
must construct inputs without provider dispatch. This is a manually supervised
operator trust boundary, not OS enforcement of arbitrary module behavior.

Each dataset entry is PairedDatasetComposition(settings, scope, v6_binding,
xing_binding, execution_context, model_usage_guards, embedding_provider,
embedding_admission=None). The execution_context callback receives the normal
container, run_id and exact Scope; it must use the authorized native lifecycle
and finish within the deadline. Capacity, current owner/account/egress/monthly/
single-writer guard, verified index expected/actual/Scope, public source mapping,
actual DB/provider observers and bounded I/O callback are explicit composition
inputs. Missing real grants, capacity or frozen identities are rejected before
normal bootstrap, Settings/key lookup or new DB construction. No grant, key,
counter, index or authorization is synthesized by the evaluator.

`build_normal_paired_runtime` builds each admitted dataset via the actual
backend build_container with v6_generation_binding/v6_xing_binding and normal
model UsageGuards/embedding inputs, then enters run_public_paired_cohort.
The offline argv contract exercises this loader/build branch with native
Factory/QuickChain and SIMULATED providers/DB/evidence. Historical meet-adapt-1
full-native verification was BLOCKED: normal CloudAdapter retained max_requests=10 per instance,
while each dataset needs 16 Cheap-arm calls. Observed verification-2 retains
24 QIDs/72 arms, with SF 30 and DeepSeek 24 actual fixture sends and 18 rejected
cells. No request counters are reset, caps raised or gateways replaced to evade
this guard. The subsequent explicit backend request/token window is integrated
below; monthly budget, native claims and actual token limits remain authoritative.
The standard legacy-fixture CLI still provides its separate 72-cell simulation
and zero-send resume. Neither result proves target DB, hosted capacity or model
quality. Existing 54 deadline/Gold/UNKNOWN/telemetry checks passed separately
inside verification-2; its full 99-test suite has 2 retained failures.

The separately owned calibration entry is:

```powershell
& '.\.venv\Scripts\python.exe' -B -X utf8 scripts/verify_v6_xing_calibration_once.py --live
```

This command is NOT RUN by the cohort worker, and its live state/budget is not
opened here. At the observed script snapshot, RUN_ID is
v6-xing-calibration-20261009-r1 while its legacy-default backend store Run is
v6-xing-public-smoke-r1; the script rejects CALIBRATION_RUN_ID_CONTRACT_MISMATCH
before config/DB/network. A subsequent read observed the backend adding a typed
XingCalibrationIdentity/new owner identity while the script still used the
legacy class default. Its owner should use that explicit typed ABI, preserving
old evidence/identities, and establish its declared unavailable verified public
monthly-budget composition. No class/instance shadowing, RAM billing or old
request retry is permitted. Backend cloud.py changed after the meeting baseline;
its original and current hashes are recorded instead of asserting a static freeze.
The calibration must use the same
Evaluation Center provider-child implementation, separately labelled CALIBRATION
Run, fixed request/wire/scope/output32/timeout10/deadline, frozen quote and
positive normal budget reservation. Hosted input/total/thinking and actual
charge/settlement remain unverified; calibration does not open ordinary cohort
production readiness. No second calibration runner is added by the evaluator.

## Explicit finite request/token window meeting

`window-adapt-1` integrates the independently owned backend window interface.
Normal CloudAdapter defaults stay 10 requests and 20,000 planned tokens. The
owner supplies each dataset's typed V6XingRequestWindow before bootstrap:

```python
plan = V6XingRequestWindow(
    run_id, grant_id, exact_server_scope, "2026-10-10T04:25:44Z",
    cohort_sha256, source_manifest_sha256, explicit_calls, "0" * 64,
)
plan = replace(plan, frozen_plan_sha256=plan.fingerprint)
xing_binding = replace(xing_binding, request_window=plan)
```

Each V6XingPlannedCall has exact request_id, qid, arm, purpose, positive input
bound and output bound. Main SF plans cover Cheap Only and Dynamic for every
frozen QID: 48 possible answers across the three eight-QID datasets. The native
request ID is `request_identity(str(uuid5(NAMESPACE_URL,
f"{run_id}:{qid}:{arm}")), "quick.router.chat_cheap", 1)`. Declared auxiliary
understand/rewrite/expansion calls need their actual caller IDs and bounds;
calibration and unimplemented purposes cannot enter this window. This first
cohort explicitly has zero helpers. Nothing authorizes implicit extra calls.

PairedDatasetComposition now additionally takes
`deepseek_calls: tuple[PairedProviderCallBudget, ...] = ()`. Each frozen entry
has request_id, qid, arm, purpose, input_token_bound, output_token_bound and
safety_tokens (default 0). The required answer plan covers Expensive Only plus
the possible Dynamic expensive choice: 24 + at most 24, not merely 24 calls.
The corresponding stage is `quick.router.chat_expensive`; output is 512.
The current normal backend supports helpers only through Cheap, so this
DeepSeek plan declares no auxiliary purposes. A missing Dynamic slot is rejected.
All dataset bindings must share the same grant object and activity-gate object.
The sum of planned input/output/safety tokens plus the grant's existing baseline
must fit its unchanged limit, at most 1,000,000. This forecast creates no
reservation: current ledger occupancy and every dispatch still pass through the
original native shared gate and monthly UsageGuard. It does not claim remaining
live quota or let a SIMULATED ledger substitute for the canonical ledger.

The normal builder signature is:

```python
build_normal_paired_runtime(
    composition, *, source_manifest_path, index_manifest_path, cohort_path, run_id
)
```

The CLI supplies the frozen cohort and requested Run identity. The builder
checks the exact window Run/grant/deadline/Scope/cohort/source hashes, complete
main arm/request identities, native window validation and shared DeepSeek worst
budget before the normal Factory/bootstrap. Frozen window fingerprints and
DeepSeek call budgets join composition identity even when the operator entry
has a fixed hash. Direct injected runtimes also include native window hashes in
the shared Run identity. Existing Attempt/claim signatures are unchanged.

Offline verification captures the official QuickChain's equal base envelope
and actual final-wire counter bounds under a separately named SIMULATED
zero-send capture Run. SF and DeepSeek capture send nothing; captured bounds
are frozen before the new native adapters are used. This is a test fixture,
not a real-context planning or hosted-readiness proof. No used adapter is reset
or replaced to evade a limit. Actual SF/DeepSeek counts are compared against
observed native wires and durable child claims; Dynamic counts are not fixed
to a presumed 48/24 split.

New evidence is isolated in
`var/reports/v6-cohort-runtime-r1/attempt1/window-adapt-1/`. Historical
meet-adapt-1 97 PASS / 2 FAIL and 54 successful / 18 failed cells remain preserved.
Only new offline results can close that code integration blocker. Real SF
credentials remain owner-reported MISSING, the PG target is not started/verified,
ECS SSH is externally blocked and production counters remain unready. Real calls
stay zero; real retrieval/context/quality and calibration remain NOT RUN.
The separately owned current calibration interface/evidence is in
`var/reports/v6-xing-live-calibration-r1/window-meet1/REPORT.md`; prior script
identity observations above are historical and were not refreshed by executing
the script. No live state, budget, credential, PG or SSH probe is performed here.

### Provider batch claims (RAG-V6-PROVIDER-BATCH-CLAIMS rev1 attempt1)

`paired_state.py` now supports `index_embedding`, `query_embedding`, and
`rerank` in the existing Evaluation Center provider-child table. Existing chat
purposes `answer`, `understand`, `rewrite`, `expansion`, and `calibration` retain
their ABI and identity algorithm. This adds no billing counter or provider gate.
The owner-authorized new absolute cutoff is `2026-10-10T07:44:02Z`; existing Run
identities and their earlier deadlines are immutable. The caller must still
set the 30-minute dispatch target and the applicable full-round cutoff, both
capped by the applicable absolute cutoff. Owner's 2026-10-10T02:38:39Z revision
allows 60 minutes specifically for a 40-question plus 24-page full round,
including all three arms, scoring and report closure. Other sample rounds retain
their existing 40-minute cap. This is recorded in the new attempt time-profile
manifest; it does not mutate an existing Run or consumed calibration request.
The current read-only cohort scheduler still has its old fixed 04:25:44Z/40-minute
profile; the main line must wire an explicit new authorized profile before a
40-question/24-page run. All three deadlines are checked after the
write lock, after claim commit, and at the final send check.

Frozen types and callback are:

```python
ProviderBatchCall(
    batch_ordinal, document_version_ids, chunk_ids, input_sha256,
    wire_request_sha256, request_identity_sha256, candidate_order=(),
)
ProviderBatchPlan(
    purpose, provider, model, embedding_identity_sha256,
    index_profile_sha256, scope_sha256, calls, query_sha256=None,
)
SQLiteProviderBatchSendClaims(
    path, run_id, qid, arm, input_identity_sha256, plan,
    lease_identity_sha256, lease_validator,
)
```

Calls are a finite tuple in ordinal order 1..N. Index calls align document
versions, chunks, and input digests. Query embedding binds the query digest;
rerank binds that digest and the exact ordered candidate chunk list. Provider,
model, EmbeddingIdentity digest, index-profile digest, scope, wire digest and
the native request-identity digest are part of the canonical plan fingerprint.
Only digests and bounded identifiers are stored; no input text, vectors, key,
lease token or remote response body is stored in the batch claim.
The native sender must derive both request digests from its actual serialized
wire envelope and verify those against this frozen plan, not trust caller
labels as evidence of an executed request.

Indexing registers an explicit `execution_purpose="INDEXING"` Run with
`expected_qids` identifying ingestion units. A fixed shared retrieval registers
`execution_purpose="RETRIEVAL"` with its frozen QIDs. Call
`begin_provider_batch_scope(path, run_id=..., qid=...,
input_identity_sha256=...)` once before dispatch; it returns `INDEXING` or
`SHARED_RETRIEVAL` respectively. These namespaces are not answer arms. Query
embedding/rerank may instead use an existing pending COHORT QID/answer-arm
Attempt. Index embedding cannot use that arm. Recovery inspects the existing
Attempt; calling begin again does not create a retry.

The callback implements the existing `ProviderSendClaims` protocol:
`__call__(request_id, purpose, provider, model, role,
request_identity_sha256, call_ordinal=1)`, `check_before_send(request_id=...)`,
and `settle(request_id=..., status=..., result=..., error_code=...)`, with the
same keyword-only ABI. `CurrentProviderSendClaims` delegates to a batch callback
inside `provider_send_claim_scope(callback)`. The callback also supplies
`request_id(purpose=..., call_ordinal=...)` and
`reusable_result(request_id=...)` for digest/reference recovery.

Request identity remains `p7v6-` plus SHA256 of canonical
`[run_id,qid,arm,purpose,call_ordinal]`. The request PK and the existing unique
slot prevent renaming a consumed operation to admit another send. Provider or
model changes do not create a new slot. Once any batch of a purpose is claimed,
the full ordered plan is immutable for that parent/purpose, including later
unclaimed ordinals. A pending, UNKNOWN, failed, blocked or completed slot is
never released or retried. A crash after COMMIT leaves the claim consumed.

Native call sequence: construct and freeze the whole plan; persist the parent;
call the callback for the exact batch before its first wire; validate the
returned identity including the actual ordinal; run the original native
authorization, capacity, reservation and billing checks; call
`check_before_send` again immediately before wire; perform one send; settle
that child. The injected `lease_validator(lease_identity_sha256)` must verify
the actual current owner lease and return None or raise. Claim COMMIT happens
before this lease check; failure does not release the slot. Final time is read
after lease I/O. Tests use a simulated lease and prove no live lease readiness.
`backend/app/ports/provider_send_claims.py:claim_matches` currently fixes
ordinal=1; the separately owned native batch sender must validate its actual
ordinal, including 2..N, before integration can be claimed complete.

Successful/simulated settlement accepts only
`{"result_sha256": digest, "result_ref": "sha256:" + digest,
"item_count": len(call.input_sha256)}`. Recovery returns that summary only
for a successful/simulated slot and never grants another send. The artifact
reader must read and hash-check the referenced output separately. UNKNOWN
has no reusable result. A completed non-answer Run requires its registered
units, terminal successful/simulated parents and all planned child ordinals.

The SQLite migration is atomic under BEGIN IMMEDIATE and WAL. It expands the
two existing CHECK constraints, copies all old columns, checks exact value
preservation and foreign keys, retains custom indices/triggers and records
paired migration 3 in `paired_schema_migrations` in the same database. Global
`PRAGMA user_version` stays 2 for the existing Center registry. Unrecognized
schema versions, conflicting migration artifacts or external FK dependents
fail closed. Only independent test databases have been migrated here.

Evidence: `var/reports/v6-provider-batch-claims-r1/attempt1/`. Final targeted
batch tests: 27 PASS; final associated suite: 135 PASS, including those 27.
Tests use real local SQLite/processes with SIMULATED providers, lease and
controlled clocks. Raw failed harness/JUnit runs and the original test-layout
restoration are retained. Original test bytes and old report/cohort hashes are
preserved. Real embedding/rerank/model/index/cost/Trace verification is NOT RUN.
The read-only six-dataset acceptance audit is in
`ECS_ACCEPTANCE_CONTRACT_AUDIT.md` in that evidence directory. This does not
turn the three-dataset 24-QID development fixture into six-dataset acceptance.
Parameter tuning or necessary code repair must use development cases, preserve
the frozen test set/Gold/failed cells/guards, run affected regressions, and then
compare all arms under the same frozen conditions. No tuning is performed here.

### Forty-question report and special-source meeting (rev1 attempt1)

Evidence is isolated in `var/reports/v6-cohort-report-specials-r1/attempt1/`.
The legacy DATASETS tuple remains three datasets/24 questions. FULL_QA_DATASETS
adds `hotpotqa` and `mmlongbench-doc`, eight frozen cases each: 40 questions,
120 arm cells. Omni's 24 pages are separate, with zero official QA questions.

CLI `--phase paired-prepare-special` requires `--special-freeze`, its exact
`--special-freeze-sha256`, and a new `--special-output`. Optional
`--special-data-root` resolves frozen relative source paths on ECS; otherwise
the manifest's exact local paths and adjacent selected8 files are used.
Preparation writes new cohort_dev40.jsonl, source_manifest_40.json, source-only
payloads, cases and omni-page-input-contract.json. Existing output is accepted
only if identical; original cohort/Gold/files are not overwritten.

Hotpot retains all 73 full native contexts, title/sentence mapping and eight
native answers. Supporting-fact document projection qrels are SOURCE_DERIVED,
not official chunk qrels. Answer EM/token F1 use Hotpot answer normalization;
source-sentence retrieval diagnostics are separate from official joint QA.
MML retains three full PDFs/all 134 pages and eight original typed answers,
including Not answerable and empty evidence-page lists. Its source payload is
PDF identities/paths/hashes, without questions/answers/Gold. Normal PDF ingestion
and verified real indexes are required. Project EM/character-F1 diagnostics
use official typed Gold; official GPT-normalized accuracy/judge is NOT RUN.
Original page-index convention is UNKNOWN, so page scoring is NOT RUN.
The full cohort has 24 answer-Gold questions and 16 without answer Gold.
All source-derived/official/independent origins and per-metric counts stay distinct.

PairedRuntime and PairedRuntimeComposition add optional read-only
`settlement_observer(*, run_id, qid, arm, provider_request_ids)` returning a list
of native facts. Facts must bind request_id/provider/request_identity_sha256
to a durable child and contain execution_kind, settlement=COMPLETED,
actual_charge_verified=True, currency=USD|CNY, actual_charge_microunits=int>=0,
and source_sha256. Quotes, duplicates, mismatches, pending/UNKNOWN, absent or
unverified facts stay UNKNOWN; fees are never inferred from tokens or converted
between currencies. Per-arm cost preserves availability, known components,
unknown-call count and source. SIMULATED never becomes actual real expenditure.
The normal builder forwards the observer; the owner must bind bounded reads
from the original native settlement mechanism. Verified USD alone may populate
existing cost_microusd telemetry. No billing counter/ledger is created or mutated.

Latency measures the actual QuickChain invoke and delegates its existing
_generate/_validate Runnable methods. Fields separately record
quickchain_end_to_end_ms, generation_stage_ms, validation_stage_ms, actual
retrieval/frozen-copy time, and evaluation scoring/readback/telemetry time.
Provider latency remains usage.calls; missing historical measurements remain
nullable. The final paired_arm_report joins the latest Attempt Trace receipt
by Run/QID/arm while preserving initial_telemetry from the immutable case.
join_latest_attempt_report is a read-only view. Trace recovery still retries
only telemetry, never model generation or retrieval.

Omni page_input_contract verifies 24 image hashes/dimensions/page identities
and original Gold digests. Only images are ingestion inputs; annotation JSON
is evaluation-only. score_page_parsing defines PROJECT bbox/category F1@IoU.5,
matched text/table/formula normalized edit distance and matched reading-order
diagnostics with eligible/matched/unavailable counts. Official full Omni score
is NOT RUN. Missing predictions are NOT RUN; unavailable targets stay nullable.
The separate omni-independent-qa-v1 overlay requires explicit hash, independent
premodel origin, review receipt, aware freeze time before first model results,
source page digests and authored questions/answers. It produces PublicCase inputs
for the same normal generation-case path, passing only question into retrieval
and generation. No real QA overlay is authored here. Its absence leaves Omni
RAG QA NOT RUN and six-dataset full RAG unaccepted; it does not block the 40-QID run.

`--paired-time-profile owner-40q-24page` requires the frozen 40-QID/24-page
contract and selects the owner's 60-minute full round including scoring/report,
30-minute dispatch target and absolute 2026-10-10T07:44:02Z cutoff.
owner-small keeps 40 minutes with the new cutoff; legacy-dev24 keeps the old
04:25:44Z/40-minute profile. The normal builder checks the SF window's exact
selected deadline. Existing Run times, old grants and consumed requests are
never extended. Final report joining is deadline guarded; no background tail runs.

First controlled full run exposed Hotpot QID 5a8911ad554299515336129c: the
official generation_budget chooses 896 for its two intents, while the native
DeepSeek grant accepts 512. V6_REQUEST_BOUND_INVALID denies that wire. All
120 cells remain: 119 simulated successes and one failed Expensive Only cell;
the cohort stays PARTIAL with an explicit failed-cell summary. No Gold/question,
native cap/counter or provider label is changed to force completion. Real larger
contexts may expose more mismatches. Backend/main-line output-budget meeting
is required before claiming a complete real cohort; this worker changes no backend.

### Persisted provider window admission repair

The backend-confirmed SiliconFlow window marker is scoped to the existing
Run/provider/model/role child records. `claim_provider_send` now reads that
marker within its existing BEGIN IMMEDIATE transaction before inserting a new
provider child. A committed SF_FREE_TOKEN_BOUND_EXCEEDED error, or the existing
provider_dispatch.request_window_blocked=true receipt, rejects admission without
creating another pending child. This applies across QID/arm and reconstructed
callbacks; it creates no new window, ledger, schema or grant.

The same read-only helper remains in `check_provider_send_before_send`.
Post-COMMIT deadline checks, callback lease/deadline checks and the transport's
final send check remain required. A window settlement racing after COMMIT can
still leave a consumed pending child; that record must remain and cannot be
released or retried. UNKNOWN timeout alone does not mark a token overrun, and
its own used slot still cannot replay. SQLite/read/receipt errors fail closed.
Existing chat ABI, migration version and historical rows remain unchanged.

The shared cohort's report reconstructs terminal case failures from its durable
case results on resume, deduplicating the same first-run entries. This is a
read-only summary and does not repeat generation/retrieval or change scoring.
Repair evidence is stored independently under
var/reports/v6-provider-window-preclaim-r1/attempt1. All providers/leases there
are SIMULATED; local SQLite/process/QuickChain checks are real executions.
The original backend 289 PASS / 1 FAIL evidence is retained, and historical
overwritten JUnit gaps remain UNKNOWN. No real cohort acceptance is granted.

### Normal paired 512 output meeting

The owner-authorized backend entry is now available as
`QuickSettings(answer_output_token_cap=512)`. Paired arm invocation supplies this
same exact integer to CHEAP_ONLY, DYNAMIC and EXPENSIVE_ONLY through the normal
QuickChain. It applies before context admission, generation metrics, envelope,
counter measurement and provider/monthly/activity reservations. Complexity,
question/Gold/source/index selection and prompt construction are unchanged.

PAIRED_ANSWER_OUTPUT_TOKEN_CAP is part of composition, runtime policy and case
identity and is exposed in the report. The observed GenerationEnvelope must
have that exact integer before checkpoint/claim admission. Its existing identity
also binds max_tokens. Native SF answer call plans and DeepSeek worst-case plans
must match 512; helpers retain their separate explicitly planned bounds.
Changed cap/identity cannot resume a consumed Run. Native final wire/token checks,
UNKNOWN no replay, privacy/lease/cost/deadline guards and memory-off policy remain.

The new full 40-QID/24-page input profile uses the absolute 07:44:02Z cutoff,
30-minute dispatch target and 60-minute round including scoring/readback/report.
New Run IDs/evidence are required. The original uncapped 119-success/one-failure
Run, failed cell, JUnit/source/report hashes remain immutable historical evidence;
they are never upgraded to PASS. New complete SIMULATED rows demonstrate the
normal 512 path with controlled evidence/providers, not real retrieval/model
quality or cloud authorization. Native REAL R2 grant/authority activation,
production counters and verified real indexes remain separately required.

Backend frozen interfaces are in output-authority-closure-r1/interfaces.md;
the evaluator invokes no real ledger activation, marker writes or Key lookup.
The new meeting evidence and final source snapshot are under
var/reports/v6-cohort-runtime-r1/output512-meet-r1-attempt1.

### R2 normal permit and independent ECS database assembly

The evaluator accepts the existing exact `SiliconFlowFreePermit` type through
`V6XingGenerationBinding.request_window`. It must be the same object installed
as `authorized_transport.free_model_permit`, with the same grant object,
run/scope/source/deadline, fingerprint and exact Cheap/Dynamic answer request IDs.
Native permit validation retains the 07:44:02Z hard ceiling and final wire checks.
The legacy `V6XingRequestWindow` branch still validates cohort hash and its native
04:25:44Z deadline. No consumed window or registered Run is extended.

`PairedDatasetComposition.free_model_bindings` is an optional dictionary passed
to the ordinary backend `build_container(v6_free_model_bindings=...)`. When it is
provided, the evaluator omits the embedding-provider override so that normal
bootstrap owns construction. REAL composition requires a native embedding
binding with a distinct query-only permit: each call is `query_embedding` /
`SHARED_RETRIEVAL`, binds the selected QID and exact public question hash, stable
native request ID, one item and the backend's ten-second retrieval timeout.
Run/scope/source/deadline/execution kind and `CurrentProviderSendClaims` must
match. An INDEXING-only binding is rejected. The owner execution context still
must install the frozen `ProviderBatchPlan`, native lease callback and matching
`FreeModelRequestContext`; declaring a permit does not supply these at runtime.

For the approved independent `rag_p7_r1` / `rag-eval` target, pass a typed
`ECSReadOnlyDatabaseProfile` in `index_bindings[dataset]['database_profile']`.
The profile requires observed port, PostgreSQL system identifier, database OID,
migration/schema revision, container ID, volume and an evidence SHA256.
Unknown values are rejected. Expected and actual index identities retain all
existing fields, add `system_identifier`, `database_oid` and explicit
`scope={'kb': [...], 'documents': [...]}`, and must match both the profile and
server Scope. Omitting the profile retains the old 25437 clone branch.
`read_only_database_url` and `require_read_only_connection` accept the same
optional profile, retain read-only transactions, and the connection check reads
system identifier/OID/migration. Native index versions, counts, source hashes,
container/volume identity and all original equality checks remain required.

The actual backend Container is frozen. The evaluator attaches its execution
context only to a copy with `object.__setattr__`; it no longer relies on mutable
test replacements. Offline tests exercise the normal bootstrap/Factory/QuickChain
without allowing database connections or live senders. Independent fixture QIDs
and database identities are explicitly SIMULATED, never observations of ECS.

Evidence: `var/reports/v6-cohort-runtime-r1/r2-ecs-assembly-r1-attempt1`.
Original MIRACL QIDs contain `#`; the currently pinned native permit validator
rejects them. This is a backend-author blocker; original cohort/Gold is unchanged.
Actual ECS compose entry and observed database identity were not supplied here.
Focused checks: 81 PASS after one fixture repair, with the initial 62 PASS /
19 ERROR JUnit preserved. Additional boundary checks: 71 PASS / 2 FAIL (missing
simulation result-root environment; expired historical resume-test deadline).
No further repair under this attempt. Complete validation and real assembly stay
BLOCKED pending a separately assigned continuation; no real acceptance is granted.

### Offline test closure and observed ECS host deployment

The separately assigned offline attempt2 closes both test-environment failures.
The native final-send test supplies its own E-drive SIMULATED CAS directory.
Recovery uses a function-local historical clock shared by evaluator/SQLite and
the explicitly SIMULATED activity gate's injectable clock. Original failure
assertions remain. Valid same-ID resume makes zero new calls; advancing to the
unchanged 04:25:44Z cutoff disables scoring/dispatch and rejects a new expired
Run. The grant object and persisted Run/case results remain identical.
Attempt1 failures and attempt2 initial failure are retained. Associated checks
passed 154 tests, including a repeated meeting after the backend author's separate
QID change; the evaluator worker did not write that backend file.

ECS evidence receipt `ecs-db-missing-identity-readonly1.json`, SHA256
`5ff1189a242d4ff6a556d6914f13c07cbc1163218528daf60280c27324fbe662`,
observes HOST_NATIVE_POSTGRESQL: port5432, system identifier7687882228252794550,
OID19805, database rag_p7_r1, migration0022_memory_vector_index,
data directory /var/lib/postgresql/16/main and postgresql@16-main.service.
The current process executable is /usr/lib/postgresql/16/bin/postgres.
These are externally collected read-only metadata, not a real index/Scope proof.

```python
profile = ECSReadOnlyDatabaseProfile.from_host_metadata_receipt(
    Path('var/reports/v6-ecs-resume-r1/ecs-db-missing-identity-readonly1.json'),
    expected_sha256='5ff1189a242d4ff6a556d6914f13c07cbc1163218528daf60280c27324fbe662')
index_bindings[dataset]['database_profile'] = profile
```

The explicit host branch requires `deployment_kind`, `data_directory`,
`service_name`, `postgres_executable`, system identifier/OID/migration/port and
the complete existing dataset/index/source/model/version/read-only/Scope fields.
Docker container_id/container_name/volume_name are absent or null; non-null
placeholders are rejected. The profile's corresponding Docker fields are null.
The Docker branch retains its container/volume requirements; omitting the profile
retains the original clone checks. No old identity fields applicable to the
selected deployment or index are removed.

`require_read_only_connection` rechecks current DB and transaction read-only,
then host system identifier/OID/migration/data_directory/server port. Missing
columns fail closed. The evidence's metadata transaction `on` never sets
index readiness or the business connection's `database_read_only` field.
The receipt loader constructs no Scope, index or ready flag. Real Scope remains
pending actual ingestion; the actual ECS factory is still required separately.
No P4 system ID, fabricated Docker identity, production window extension or
real model/DB/ledger call is introduced by this evaluator patch.

Host profile evidence and 110 passing offline contracts are under
var/reports/v6-cohort-runtime-r1/r2-ecs-host-profile-r1-attempt1.
