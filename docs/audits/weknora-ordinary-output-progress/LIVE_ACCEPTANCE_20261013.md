# Current live acceptance checkpoint

This supplements, rather than rewrites, the historical reports. Full Goal remains incomplete. Latest owner approval authorized the repaired TLS service, seventh capacity probe and original three fenced webpage questions; it did not authorize additional probes or questions, a higher task limit, or deployment of the subsequent unit-prompt repair.

## Actual runtime and capacity

UI: `http://127.0.0.1:14188/`; API readiness: HTTP200 `READY_CONFIG`. API and worker run image `personal-rag:weknora-tls-state-20261012`, manifest `sha256:cfd161ba200916708eb4d0f33a9d0d7b46b829bb8cf84fc3d2cbbfd339f05668`, OCI source revision `47f34459d115db0f1fd72531b257cde9ba718418`. API uses the explicit temporary task-fence factory; worker uses its normal ingestion entry point. DB container remains `5c9a6eacb99420983bc1727e0427f6cc8b2f2adf242f6ae12b8efaa531724a43`, migration `0024_input_route_attempts`, storage volumes `raglocalfirst0930_rag-db` and `raglocalfirst0930_rag-storage`; configuration revision `bcd271c7-e820-45d4-9910-039384d268cd` remains unchanged. Actual selected-field Docker/readiness evidence: ignored `var/reports/weknora-ordinary-20261012/runtime-live-after-q2.json` (no keys or environment dump).

Capability proof `/opt/model-assets/ordinary-output-capability/receipts-v3-complete.json` SHA256 `66d9b5c0d08bc4debadc2166a36b9739f63563a34b07ed878827f601ad20122f`. Seventh probe Run `0da376b4-e06f-4740-9cec-780668f52f6b`: DeepSeek4096, finish `length`, actual input121/output4096, VERIFIED. Xing observed2048/4096;1024 inferred. DeepSeek observed4096;1024/2048 inferred. Lower-tier support is explicitly inferred from the output limit contract, not claimed as individually observed or as proof of hosted model weights.

Seven probes consumed actual known10793 plus unchanged UNKNOWN10671 =21464 Token occupancy; all full probe bounds24559. All seven Run identities and reservations are in ignored `probe-batch-seven-readback.json`. No additional probe is allowed. UNKNOWN1063+4804+4804 is retained; no historical failure cause is retroactively asserted.

## Current webpage results

Scope is exactly KB `d78f3434-0e8f-40bf-afcb-f1c2c311ff51`, document `1fd5a928-d3d7-4bae-aa00-fc74701f61e5`, original session `313909f3-34d9-4f26-8778-3d5bd27f2c60`.

| Sample | Run | Result |
|---|---|---|
| Original `我应该怎么学习`,2048 | `e7f82931-0b2f-4d1d-a12f-263633db4741` | FAIL `UNSUPPORTED_ANSWER`; one POST; four candidates/four evidence items; Xing then DeepSeek both received normal stop responses, but validation rejected the generated answer. No committed assistant answer or citation. |
| Short,1024 | `7b90d6f9-0a15-4380-a119-e248c59b0d52` | FAIL `MODEL_BUDGET_DENIED`; one POST; Xing received normal stop response but candidate failed validation; DeepSeek upgrade was NOT_SENT by the task budget fence. No committed assistant answer or citation. |
| Long,4096 | `31c5266c-55a7-4377-9338-01da73bec415` | NOT RUN. Only11042 conservative task Token remain, insufficient for the expected generation bound with current evidence. No POST/attempt is fabricated. |

Original Xing actual2746+971; DeepSeek2617+882; Embedding6. Short Xing2754+400; Embedding18. All five actual reservation rows are settled, not Token UNKNOWN. Cost provenance remains NO_BILLING_RECEIPT/UNKNOWN; it is not fabricated billing evidence. Total actual known+old UNKNOWN after these questions31858. Temporary conservative task journal total63958/75000, remaining11042; its full admissions are not released or silently changed to actual usage. Preserved grant SHA `5abdb7a002942f0f8490ec84f367451bb8fd8c76ba981fab5cfbbf621af36184`.

Each browser harness exit0 means it collected a terminal result, not that the answer passed. Evidence: ignored `web-q1-result.json`, `web-q2-result.json`, generation/terminal PNGs, `web-q1-ledger-readback.json`, `web-ledger-after-q2.json`, and `web-task-journal-after-q2.json`. Failed diagnostics were observed in `.message.assistant.diagnostic[data-run-id]`; user bubbles contain the submitted question. Progress pending ended as failed and collapsed. The screenshot viewport includes historical answers; they are not current answer evidence. Current citation count is0; citation readback/committed-answer refresh acceptance remains NOT RUN. Do not substitute an old successful Run.

G3's120–210second/at least four healthy SSE connections requirement is explicitly simulated in the source Goal. Existing210second/seven-segment/one-POST offline proof remains valid. Current live failed samples do not establish successful committed terminal recovery or long proxy behavior. No extra costly three-minute generation is required solely to repeat the offline clock test.

## Minimal source repair and actual commands

The original rejected candidate adds `天` to the source's unitless `1，7，16，35`. The ordinary prompt condition removed both the old length restriction and the existing instruction forbidding invented numeric units. Restore only the unit-witness instruction after evidence for ordinary budgets; keep the frozen concise branch unchanged. No validator, router scoring/threshold, evidence snapshot, final commit, service ledger or historical identity change.

- `backend/app/application/quick_chain.py`: ordinary prompt retains source-unit constraint, without the180-character/100-word cap.
- `backend/tests/test_ordinary_output_budget.py`: six cases cover the constraint at1024/2048/4096 with concise mode both enabled and disabled.

Local repair commit `ce3398a65a9acc4aa11c6a0a22228fead348832d`.

| Command | Exit | Outcome |
|---|---:|---|
| `.\.venv\Scripts\python.exe var/reports/weknora-ordinary-20261012/replay-approved-question.py --index 0` |0| REAL collection complete; functional FAIL |
| `.\.venv\Scripts\python.exe var/reports/weknora-ordinary-20261012/replay-approved-question.py --index 1` |0| REAL collection complete; functional FAIL |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_ordinary_output_budget.py backend/tests/test_final_answer_commit.py backend/tests/test_quick_chain_budget.py -q` |0| OFFLINE70 passed,8 warnings (including local pytest cache permission warning) |
| `git diff --check` |0| PASS |
| `git commit -m "fix: retain evidence unit constraints for ordinary answers"` |0| local commit only |
| `& 'E:\Docker\DockerDesktop\resources\bin\docker.exe' build -f deploy/Dockerfile.api -t personal-rag:weknora-unit-guard-20261013 --label org.opencontainers.image.revision=ce3398a65a9acc4aa11c6a0a22228fead348832d .` |0| candidate built, NOT DEPLOYED |
| `.\.venv\Scripts\python.exe var/reports/weknora-ordinary-20261012/collect-seven-readonly.py` |0| seven probe ledger/readback; zero model calls |

Candidate manifest `sha256:a94a1696a6e41bd7b15b502607d4d9e605428b29ee26fb8914b2f9fdf3dcb82c`, config `sha256:d604225f1605da0977d4680ad5cc691c013ca13348fbb9314c3bc06fa03e0dc3`; not the live image. Linux `--network none` import/render smoke is recorded separately in ignored `unit-guard-linux-smoke.txt`; only its actual exit may be reported.

Earlier scoped offline and frontend evidence is reused for unchanged inputs; global contract remains FAIL31 baseline/0new, not all-green. 未实现的命令不得报告通过。

## Remaining owner decision and delivery boundary

Functionality is not accepted, G5 NOT RUN. Next proposal: deploy the unit-constraint candidate; retain all old journals/reservations and7probe ceiling; use two additional fresh Run identities for original/short questions plus the unsent long sample (five webpage attempts total). Raising the task-only conservative ceiling from75000 to150000 would allow full per-call bounds while preserving the original actual ledger, monthly gates and cumulative DeepSeek limit. This change, additional questions and candidate replacement are NOT AUTHORIZED/NOT RUN at this checkpoint. Any new Token UNKNOWN still stops all subsequent calls; old Runs are never resent and occupancy never reset. Alternatively owner may keep the current cap, leaving real acceptance incomplete.

Temporary fence remains active while that decision is pending, so other mutation endpoints are blocked by its test-only middleware. Do not silently remove the fence, expand its grant, switch the service or restart generation. After approved functional validation, remove the temporary entry point without modifying durable evidence; perform G5 in its own commit. No Push, Tag or publication.

## Additional read-only failed-run recovery evidence

`.\.venv\Scripts\python.exe var/reports/weknora-ordinary-20261012/read-failed-recovery.py`: exit0. Actual current Chrome UI was reloaded and the original conversation reselected; for both current failed Runs the assistant diagnostic and failed progress each occur exactly once, progress initially has no expanded stage list, and expanding shows the persisted stages. Scoped terminal SSE GET returned HTTP200 with `run.failed` for each Run. Browser requests included zero mutations and zero model calls. Actual reloaded screenshots were visually inspected; original/short question bubbles are on the user side and their failure progress is on the assistant side. Evidence: ignored `failed-recovery-readonly.json`, `web-q1-reloaded-failure.png`, `web-q2-reloaded-failure.png`, and the two `web-q*-terminal-recovery.sse` files.

This is REAL_PASS for failed-run reload/terminal recovery only. It does not establish a successfully committed answer, current citation readback, or successful long-request proxy recovery. The two sample outcomes remain FAIL; the long question and G5 remain NOT RUN. A first local SSE parsing command failed exit1 because Windows default GBK was used to read UTF8; the files are retained and the parsing command was corrected to explicit UTF8, without replaying any request.

## Subsequent owner-approved execution: current authoritative results

Owner explicitly approved the unit-prompt candidate, five total webpage attempts and150000 conservative Token. V2 authority pinned SHA `a0cd8b01a93dd071d6fc61b235931dd8b1bdbcb66ed4861e1a11a464af56369e` carried all five previous admissions39399 unchanged; old grant/journal files were not overwritten. Schema/ceiling/concurrency/retained-occupancy Linux12tests exit0. Actual candidate API/worker image `sha256:a94a1696a6e41bd7b15b502607d4d9e605428b29ee26fb8914b2f9fdf3dcb82c`, backend source revision `ce3398a65a9acc4aa11c6a0a22228fead348832d`, readiness HTTP200 READY_CONFIG. DB/config/storage unchanged.

| Sample | Run | REAL result |
|---|---|---|
| Fresh original2048 | `171ca042-996e-4e7e-8edb-9516ebd142b2` | PASS: one POST, completed28.56s, persisted assistant1469characters,4citationsHTTP200, reload persistence exact. Xing candidate rejected; normal DeepSeek upgrade PASS. |
| Fresh short1024 | `cc79fb74-4721-4910-8e52-5ed614d5ba35` | PASS: one POST, completed14.47s, persisted assistant473characters,4citationsHTTP200, reload persistence exact. Xing candidate rejected, normal DeepSeek upgrade PASS. |
| Original planned long4096 | `31c5266c-55a7-4377-9338-01da73bec415` | FAIL MODEL_BUDGET_DENIED: Xing normal stop2082output but validator rejected; expensive upgrade NOT_SENT. Full task bound146080/150000. |

Original actual: Embedding6, Xing2778+986, DeepSeek2649+887. Short actual: Embedding18, Xing3842+429, DeepSeek3662+305. Planned long actual: Embedding42, Xing4364+2082, no DeepSeek actual call. Actual usage is distinct from UNKNOWN billing provenance; token rows settled for these calls.

Read-only PostgreSQL evidence joins verified eight HTTP citation quotes/locators/version IDs/chunk IDs against `answer_evidence` and original KB/document Scope, with exact SHA256 of quote. Ignored `successful-citation-pg-readback.json` records real rows. Native browser clicked each sample's first citation and checked the drawer quote, then checked identical rendered answer text after reload. Both actual restored timelines contain retrieval/evidence counts4, real cheap/expensive stages and validation stages. Scoped SSE has22 events each, monotonic1..22, actual terminal `answer.completed`, exact persisted answer, and Last-Event-ID21 resumes the identical terminal. Proxy headers observed `text/event-stream; charset=utf-8`, `cache-control:no-cache`, `x-accel-buffering:no`. Collector `read-successful-recovery.py` exit0, zero mutations/model calls, evidence `successful-recovery-readonly.json` and screenshot/SSE artifacts. Initial collector assertions failed exit1 due an async hydration window and incorrectly expecting `run.completed`; corrected to wait for hydration and actual frozen `answer.completed` contract. These failures are not claimed as product failures. This proves current successful recovery; it does not turn the offline210second simulation into a live210second model test.

Owner then requested a larger task fence to avoid repeated mid-run budget failures and permitted further enlargement if needed. Operational V3 ceiling250000 and six total attempts, Linux12tests exit0; all13admissions121521 were carried, not reset. Grant SHA `4fa8fa405ce34553594fcf5eee16ab2b3692209db67014ede105d00ed87cd4ab`. Backend image remained the same; task script source changed outside image. Before fresh long POST, authoritative preflight confirmed146080 occupancy and103920 remaining, satisfying a100000 headroom check.

Fresh long Run `85eb1ecd-6600-4916-ac0b-7372afa99a6d`: one POST, FAIL MODEL_UNAVAILABLE after92.72s, no committed assistant/citation. Actual provider diagnostic REQUEST_TIMEOUT after about90.3s, current service timeout90s. Xing attempt `rag-request-v1:f5e3eba23655304b26ca635d5105d1abdd12b6c2141b83b503988b6e682dfdde` is token UNKNOWN17526+4096=21622. Embedding42 settled. No expensive upgrade was called. This failure is not caused by the task budget; precise server-side generation/usage remains UNKNOWN. No new model calls after it.

Current totals: task full bound167744/250000; known web32486 plus known probes10793=43279; old+new UNKNOWN32293 (old10671+new21622); actual known+UNKNOWN occupancy75572. Evidence `task-v3-in-flight-readback.json` reports one new UNKNOWN and collector exits1 intentionally to enforce stop. Old failed samples remain preserved, not resent.

Owner's budget-enlargement authority permits preparation of350000 so next full-round headroom exceeds100000; it does not waive the independently agreed new-UNKNOWN stop. V4 code is prepared for seven total attempt identities with exact acknowledged prior web-request IDs in a SHA-pinned owner grant, and retention of all old admissions; Linux14tests exit0, including acknowledgment of one old UNKNOWN without releasing it and immediate blocking of a further unknown. No V4 grant/journal exists or is activated at this checkpoint; continuing after the latest UNKNOWN still requires a fresh explicit decision. Candidate ordinary timeout120 is within existing Settings/transport maximum120; Settings construction120 verified exit0, no frozen limits or global defaults changed. G3 pending-history interruption correction and300second client default are being validated independently; no G5 cleanup yet. Full Goal incomplete.

## G3 interruption repair completed offline

Independent commit `02190463662932d32ee161390b0d1c3370a8c5e7` changes only four frontend files:

- `frontend/src/app/App.tsx`: before a new send invalidates historical SSE callbacks, close pending historical timelines only from this Scope/epoch's persisted assistant or validated definite diagnostic; unknown historical results remain unknown. Scope/cancel/epoch/singlePOST fences remain.
- `frontend/src/api/client.ts`, `frontend/src/api/sse.ts`: default wait300000ms, explicit window and minimum210000ms unchanged. This covers proposed two120second calls plus local processing; it is not proof of provider availability.
- `frontend/tests/run-progress.spec.ts`: history-interruption regression, simulated270second recovery, default/explicit/minimum wait contract.

Actual commands from `frontend`, using the already installed Chrome (no dependency install):

```powershell
$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
.\node_modules\.bin\playwright.cmd test tests/run-progress.spec.ts --grep 'new send closes only confirmed history' --reporter=list
```

RED exit1: old persisted assistant timeline remained pending after a new send.

```powershell
$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
.\node_modules\.bin\playwright.cmd test tests/run-progress.spec.ts tests/sse.spec.ts --reporter=list
npm.cmd run build
git diff --check
```

GREEN Playwright exit0,19passed25.7s, SIMULATED API/model; build exit0 (`tsc --noEmit`, Vite3346modules, existing chunk-size warning); diff-check exit0. No actual model call or database mutation. The current Vite source changes are local/reversible; API remains the90second candidate and liveV3 fence. Server timeout120/V4 grant still NOT DEPLOYED. G4 long remains FAIL; G5 NOT RUN. No Push/Tag/publication.

## Latest approved120second long replay: failed, stopped

Owner explicitly approved keeping the new UNKNOWN and one fresh long replay at120seconds. V4 grant SHA `be59f3e2d59c156a909af7951063997adbc4fe1370c6f73ee313677f889da997` carries all15previous admissions143185 unchanged, acknowledges only the exact prior web request `rag-request-v1:f5e3eba23655304b26ca635d5105d1abdd12b6c2141b83b503988b6e682dfdde`, and leaves old V1/V2/V3 grant/journal files unchanged. New authority and journal were initialized exclusively with fsync. API/worker candidate was restarted without migrations or DB recreation; actual API public `RAG_SERVICE_CHAT_TIMEOUT_SECONDS=120`, readinessHTTP200. Source image remains `a94a1696...`/ce3398a, task script is V4, frontend source0219046. No core routing/validator/counter/ledger relaxation.

Before its single POST, authoritative task readback proved167744/350000 full bound and182256 remaining, exceeding the100000 full-round headroom precheck; no unapproved resend occurred. Run `9043e892-2f0a-423c-a54a-488f20c43d06` collected terminal failure after122.63s, browser collector exit0 but functional FAIL. Error MODEL_UNAVAILABLE; provider diagnostic REQUEST_TIMEOUT, latency120322ms. Xing attempt `rag-request-v1:ee45ee43a559660340b55bdfd2ebb930495117ae7edf8e0f257b39192721839e` is new token UNKNOWN17526+4096=21622; no effective response/usage/finish reason/answer/citation and no expensive upgrade. Embedding42 is settled. No model calls after this failure. Increasing the task ceiling did not fix this service timeout. The exact server-side cause is UNKNOWN; do not infer queueing, billing, reasoning or successful generation from timeout alone.

Current seven webpage attempts are originalFAIL, shortFAIL, approved fresh originalPASS, approved fresh shortPASS, planned long budgetFAIL, fresh long90second timeoutFAIL, fresh long120second timeoutFAIL. Seven public probes unchanged. All approved identities preserved. Current full task bound189408/350000, remaining160592. Known web32528 + known probe10793 =43321; total UNKNOWN53915 = old probe10671 + two web21622; known+UNKNOWN occupancy97236. Actual costs remain NO_BILLING_RECEIPT/UNKNOWN, not estimated billing facts. Evidence `web-q7-result.json` and `task-v4-final-long-readback.json`; final readback intentionally exits1 NEW_UNKNOWN_STOP with one **unacknowledged new** UNKNOWN. Prior acknowledged UNKNOWN is retained and excluded only from the new-stop comparison, never from occupancy.

All subsequent real generation is stopped by the current V4 gate. No further model identity, service-timeout extension, role reassignment or long replay is authorized. Two successful tiers and real failure/success recovery do not satisfy the full three-tier G4 acceptance. G5 code cleanup remains NOT RUN; its read-only inventory exists, and no unilateral deferral or acceptance is claimed. Repeating the same long request is not a useful default next step; owner/model-service decision is required while keeping the requested routing invariant. No Push/Tag/publication.
