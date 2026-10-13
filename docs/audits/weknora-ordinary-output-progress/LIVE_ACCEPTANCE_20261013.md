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
