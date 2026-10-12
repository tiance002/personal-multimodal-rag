# 2026-10-12 continuation: output-capacity proof and runtime

This supersedes the earlier runtime-blocked statements in TEST_REPORT.md. Functional acceptance remains incomplete. No Push, Tag, release, or G5 cleanup was performed.

## Actual changes

| File | Change |
|---|---|
| `backend/app/ports/output_capability.py` | Versioned public synthetic receipt validation, strict reached-cap proof, exact one-use probe admission tied to lifecycle identity, explicit lower-cap inference. |
| `backend/app/adapters/models/service_capacity.py` | Bind exact pinned receipts, remeasure wire, expose observed/supported/inferred output caps separately. |
| `backend/app/adapters/models/service_configuration.py` | Bind proofs only to matching configured provider/model/endpoint. |
| `backend/app/adapters/models/runtime_readiness.py` | Keep unverified required ordinary tiers NOT_READY; expose capacity evidence. |
| `backend/app/adapters/models/siliconflow_xing_capacity.py` | Ordinary local output cap checks; frozen counter unchanged. |
| `backend/app/adapters/models/v6_text_capacity.py` | Ordinary local output cap checks; frozen V6 limits unchanged. |
| `backend/app/config.py` | Internal proof file/SHA settings; no user-facing fields. |
| `deploy/compose.yml` | Pass internal proof file/SHA to API and Worker. |
| `scripts/probe_ordinary_output.py` | One owner-authorized public probe per identity; durable versioned receipt; fixed batch root, 7-attempt/75,000-token fence; settled NOT_PROVEN differs from UNKNOWN; no retry. |
| `scripts/smoke_ordinary_output_capacity.py` | Zero-provider native Linux capacity smoke. |
| `backend/tests/test_output_capability.py` | Strict response, protocol, wire, cap, one-use identity and inference regressions. |
| `backend/tests/test_output_probe_failure.py` | Known settled failures may continue; new UNKNOWN always stops; pretransport denial remains distinct. |
| `backend/tests/test_ordinary_output_budget.py` | Ordinary capacity guard/inference regressions. |
| `backend/tests/test_service_configuration.py` | Configuration/proof binding and readiness regressions. |
| `backend/tests/test_siliconflow_xing_capacity.py` | Pinned local Xing counting/proof binding regressions. |
| `backend/tests/test_v6_text_capacity.py` | Frozen and ordinary output cap regressions. |
| `docs/decisions/ordinary-output-capability-proof-20261012.md` | Proof contract, vendor basis, limitations and exact targeted test commands. |
| This report, TEST_REPORT.md, progress.md | Current runtime, actual results and resume boundary. |

No changes to route scoring, threshold 0.35, cheap/expensive attempt limits, final_answer_commit, knowledge_repository, service_usage ledger or lifecycle implementation. Existing UNKNOWN and historical Runs remain retained.

## Actual probe outcomes

Owner approved at most **7 probes + 3 webpage questions**, aggregate **75,000 input+output Token**, including old UNKNOWN. Four probes have been attempted; zero webpage questions. The fourth returned UNKNOWN and the batch stopped immediately; remaining numerical allowance does not authorize ignoring the stop condition.

| Run | Role/cap | Result | Usage / retained occupancy |
|---|---|---|---|
| `046c3327-5dd9-4d44-a6e6-876897da41ab` | Xing 1024, v1 | UNKNOWN | 39+1024 = 1063 retained |
| `74b2ed85-3c22-470a-b5ed-506f2403b6b5` | Xing 1024, v2 | NOT_PROVEN, early stop | Known 45+131 = 176, settled |
| `1d5374d0-4601-4be5-adeb-0bb54728ef84` | Xing 2048, v3 | VERIFIED cap reach | Known 128+2048 = 2176, settled; finish `length` |
| `72917fff-bd37-4607-b3db-7c7e402d3fbf` | DeepSeek 4096, v3 | UNKNOWN, TRANSPORT_FAILED | 708+4096 = 4804 retained |

Current conservative actual/UNKNOWN occupancy is **8219 Token**; all original full reservation bounds total **10030 Token**. These are distinct accounting views, not additional requests. UNKNOWN total **5867 Token** remains occupied. Public probe output is not a successful RAG answer. Probe lifecycle Runs are failed deliberately without assistant answer/evidence rows.

Xing **2048** is actually observed. Xing **1024** is inferred supported from the provider's maximum-output contract and observed ceiling; it was not individually reached. Xing **4096** and all ordinary DeepSeek tiers remain unverified. Hosted tokenizer/model-weight equivalence and invoice settlement are not claimed.

## Actual runtime

- API and Worker image config: `sha256:5de88de8dd9720e03b83ab4ee69f09d61ea43031dee73807b7c7354d31814c3a`; image manifest list: `sha256:fe204758d47c62d3751b09b71e763288cffc49e49aa07da53f920cb578750ebb`.
- Image built from functional working-tree changes on source HEAD `415d607410a276e603b89a4f5488586b889ec9e3`; actual source hashes captured, not inferred from a route name.
- API command is direct uvicorn, no migration. Same DB container ID beginning `5c9a6eacb994`, same DB/storage volumes, schema `0024_input_route_attempts`.
- Configuration revision `bcd271c7-e820-45d4-9910-039384d268cd`; exact authorized KB/document unchanged.
- Public proof file `/opt/model-assets/ordinary-output-capability/receipts-v3.json`, SHA256 `acfa33c926759ffb0aac3412db4db0cab983ec54c43d9bdf3708c140cd0c943e`.
- Actual `/readyz` HTTP **503**, correctly retaining unverified tiers as NOT_READY. It reports observed Xing `[2048]`, supported `[512,1024,2048]`, inferred `[1024]`; DeepSeek observed `[]`.
- Read-only identity, source hashes and actual PG reservation readback: local ignored `var/reports/weknora-ordinary-20261012/runtime-capability-final.json`. Raw public receipts remain in `/data/storage/weknora-probes-20261012/` and copied local ignored reports. No key/environment dump; Langfuse remains disabled.

## Commands and exit results

- Full exact targeted capacity commands and results are in the decision document: v3 tests **exit 0, 38 passed / 100 deselected** (offline synthetic receipts plus real local Xing counting).
- `.\.venv\Scripts\python.exe -m pytest backend/tests/test_output_probe_failure.py -q`: **exit 0, 5 passed**. Initial bare `python` attempt used Anaconda without pytest: **exit 1**, not counted as verification.
- `docker exec -e PYTHONPATH=/app raglocalfirst0930-api-1 python /tmp/probe_ordinary_output.py --role chat_cheap --cap 2048 --root /data/storage/weknora-probes-20261012 --acknowledge-unknown 046c3327-5dd9-4d44-a6e6-876897da41ab`: **exit 0**, actual cap VERIFIED, usage2176.
- Same probe command with `--role chat_expensive --cap 4096`: **exit 1**, actual UNKNOWN4804, immediate batch stop.
- `docker build -f deploy/Dockerfile.api -t personal-rag:weknora-ordinary-20261012 .`: **exit 0**. Earlier incorrect `deploy/Dockerfile` path failed **exit 1**; corrected to existing file before deployment.
- `powershell -NoProfile -File var/reports/weknora-ordinary-20261012/deploy-approved.ps1`: **exit 0**, only API/Worker recreated, no migration/DB recreation. Helper privately loads `.env.local`; contents never inspected or printed.
- `.\.venv\Scripts\python.exe var/reports/weknora-ordinary-20261012/collect-runtime-readonly.py`: **exit 0**, provider_calls0, readyz503, current DB/proof/source identity captured.
- `.\.venv\Scripts\python.exe scripts/contract_test.py --report var/reports/weknora-ordinary-20261012/contracts-final-capability.json`: **exit 1 / FAIL**. OpenAPI/SSE PASS, existing layering violations retained; no new contract drift.
- `git diff --check`: **exit 0**. Protected-core comparison retained below before commit.

Linux actual zero-provider smoke was executed as `docker exec -e PYTHONPATH=/app raglocalfirst0930-api-1 python /tmp/smoke_ordinary_output_capacity.py --tokenizer /opt/model-assets/deepseek-v41/tokenizer.json --report /data/storage/weknora-probes-20261012/ordinary-output-local.json`: **exit0, REAL LOCAL**, native pinned recipe/tokenizer, all ordinary local caps and frozen512 regression checked. Local copied report: `var/reports/weknora-ordinary-20261012/ordinary-output-local.json`. This is not hosted proof.

## Gate status / next boundary

| Gate | Current result |
|---|---|
| G0 actual source/runtime identity | PASS, current image/DB/config/source/proof recorded |
| G1 offline protocol/budget | PASS offline; actual Xing2048 PASS; hosted DeepSeek FAIL/UNKNOWN and higher Xing NOT RUN |
| G2/G3 implementation and directed regression | Prior offline PASS reused; actual failed-Run SSE scope/cursor readback PASS only, not successful-answer recovery proof |
| G4 original study question, short/long variants, successful citations and reload | **NOT RUN** for this code version; 0/3 questions; blocked by authorized stop condition |
| Full functional acceptance / E2E | **NOT PASSED** |
| G5 redundant cleanup | **NOT RUN**, separate commit only after functional verification |

Do not retry either UNKNOWN identity, release their occupancy, rotate policy IDs, reset counters, alter route thresholds, suppress validation or broaden Scope to pass. Resume requires an explicit owner decision authorizing new calls after the latest UNKNOWN plus investigation of actual transport. Before the three webpage calls, enforce the remaining task aggregate Token fence across their embedding and generation requests while retaining the existing authoritative ledger and policies. No webpage requests have been dispatched under an unimplemented task fence.

Original question remains `我应该怎么学习`. No new factual answer or citation readback exists; historical answers/citations are not relabelled as current evidence. No milestone acceptance or owner-approved P1 deferral is claimed. **未实现的命令不得报告通过。**
