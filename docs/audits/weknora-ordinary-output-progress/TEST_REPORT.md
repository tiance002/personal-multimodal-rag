# G0-G4 verification report

Baseline main b4aa3ec5ff82497fdf9f9f00cdacf75e3128edd3. G1 local commit: e90ecebca3f3346ff0e08ca49a8eca4147064b57. G2/G3 local commit: 4d01144. Runtime source/image/config/DB identity are NOT VERIFIED in this batch.

## Gates

| Gate | Result | Boundary |
|---|---|---|
| G0 source identity and fixed WeKnora | PASS | Clean identified baseline and15fixed reference files actually read; manifest ignored locally. |
| G0 current runtime/PG attempts | BLOCKED / NOT RUN | DockerDesktop Linuxpipe absent; UI14188 and API8000readyz unreachable. Historical reports not current identity. |
| G1 budget/local protocol | OFFLINE_PASS |1024local exact wire/counter/reservation; SHORT1024/STANDARD2048/LONG4096 explicit length policy, independent ordinary attempts. |
| G1 hosted output capability | NOT RUN / BLOCKED |1024endpoint evidenceUNKNOWN;2048/4096not enabled. Real >512denied before send, no silentdownshift. |
| G2 progress | OFFLINE_PASS | Real code stage callbacks, existing event store/schema, assistant-left timeline; final transaction unchanged. Actual PG persisted stage delivery NOT RUN. |
| G3 long request recovery | OFFLINE_PASS |7healthysegments/simulated210s; one POST, scopedcommittedGET arbitration,seq,cancel/scope,latePOST. Liveproxy NOT RUN. |
| G4 affected regression | PASS offline | See exact commands below and child notes; no6datasets or fullrelease checks. |
| G4 originalquestion/livecitations | NOT RUN | No new modelRun or realcitationGET for this code version. E2E_PASS not claimed. |
| G5 cleanup | NOT RUN | Separate futurecommit only after functional acceptance. |

## Actual commands, exit codes and evidence

Project root unless noted. No model network calls by tests; SQLite reservations/synthetic IO are SIMULATED, pinned local Xing counting is REAL OFFLINE.

```powershell
$env:XING_TEST_ASSETS = 'E:\RAG quention\var\model-assets\xing4.0\baae3c3e813cad5f888f1f485cfff659c89076c5'
.venv/Scripts/python.exe -m pytest -q backend/tests/test_ordinary_output_budget.py backend/tests/test_answer_hardening.py backend/tests/test_v6_text_capacity.py backend/tests/test_siliconflow_xing_capacity.py backend/tests/test_v6_output_cap.py backend/tests/test_service_usage.py backend/tests/test_service_configuration.py backend/tests/test_service_caption.py backend/tests/test_p5_router_generation.py backend/tests/test_quick_chain_budget.py backend/tests/test_run_progress.py backend/tests/test_answer_service.py backend/tests/test_run_presentation.py backend/tests/test_final_answer_commit.py backend/tests/test_conversation_scope.py backend/tests/test_knowledge_scope_boundaries.py backend/tests/test_p55b21_global_scoped.py --tb=short -p no:cacheprovider
```

Exit0: **289passed**,7warnings,16.09s. Later readiness accessor/test correction validated by `.venv/Scripts/python.exe -m pytest backend/tests/test_service_configuration.py backend/tests/test_run_progress.py -q -p no:cacheprovider`: exit0,14passed. ASCII fixture clarification then test_run_progress.py alone: exit0,4passed. Final legalcross-evidence duration regression: `.venv/Scripts/python.exe -m pytest backend/tests/test_final_answer_commit.py -q -p no:cacheprovider`:41passed; enclosing shell also checked unchanged protection sources and exited0. Existing missingunit counterexample retained, novalidatorcode edits.

Earlier217+58G1checks and75+16browserchecks are distinct runs, not summed as unique coverage. Exact workercommands, assets, first failures and browser/build evidence in [G1_NOTES.md](G1_NOTES.md) and [UI_NOTES.md](UI_NOTES.md). Final frontendbuild exit0 includes TypeScript+Vite; existing bundlewarning preserved. Both screenshots are synthetic and visually checked by frontendworker.

`.venv/Scripts/python.exe scripts/contract_test.py --report var/reports/weknora-ordinary-20261012/contracts.json`: exit1, overallFAIL. OpenAPIpaths/snapshot/forbiddenterms andSSEsample checks PASS.31baseline layeringviolations; normalizeddiff against existing contracts-study-answer-20261012.json:31vs31,added0,removed0, savedlayering-baseline-diff.json. Checks notdisabled.

`git diff --check`: exit0. `git diff --quiet HEAD -- backend/app/application/final_answer_commit.py backend/app/adapters/postgres/knowledge_repository.py backend/app/adapters/postgres/service_usage.py backend/app/application/service_usage.py`: exit0, nochanges to those protection/transaction/ledger sources. Routerdiff only optional ordinaryattemptidentity; decide/threshold untouched.

Read-only runtime commands: `git status -sb`, `git rev-parse HEAD`: exit0. `docker ps --format '{{.Names}} {{.Image}} {{.ID}}'`: exit1 missing enginepipe. Pythonurllib read-only UI/API2secprobes reportedUNAVAILABLE URLError for14188/8000. No engine/start/deploy/DBoperation/providerrequest executed.

## Failed development checks retained

Root initialmistyped test_run_lifecycle.py path: pytestexit1 filemissing/no testsrun. Newtest initiallyused missingjsonschema: exit1 collectionerror; removedunrequesteddependency. PowerShellUnicode fixture corruption then1failure68pass; correctedsafeASCII. Callbackstatus prepared didnotmatchversionedcontract:1failure75pass; fixedstatuscompleted, final289pass. G1missingassets/Unicode failures and firstbrowsermissingChromium are separately documented, not relabelledPASS.

## Real evidence boundary and next gate

Originalq0 is the user's studyquestion; preserved KB d78f3434-0e8f-40bf-afcb-f1c2c311ff51,doc1fd5a928-d3d7-4bae-aa00-fc74701f61e5. HistoricalRun d1eee158-93d3-4bac-b78a-5aab154d4221/oldfailure56f2e64b-fdda-4387-81c2-57a854b54ff4 retained. NewRunID NONE,providerusageNONE,citationHTTPevidenceNOT RUN. Oldresult is not newimplementation acceptance.

Before approved realverification: restoreDocker, check actualUI/API/Workerimage digest/configrevision/DBhead and exactScope/remainingledger; obtain currentprovideroutputproof through authorized boundedprobes. No old V6grant, quotareset, KBconsentchange, DBschema/data mutation, push/tag/release. Goal remains awaiting this gate; OFFLINE_PASS is not E2E_PASS.


## Current continuation status (2026-10-12)

The earlier Docker/runtime BLOCKED section is historical. See [CONTINUATION_REPORT.md](CONTINUATION_REPORT.md) for the current per-file changes, real Run identities, immutable image/config/schema, commands and results. Four probes: UNKNOWN1063 retained; known STOP176 NOT_PROVEN; Xing2048 reached/settled2176; DeepSeekUNKNOWN4804 retained. Batch stopped under owner condition; 0/3 webpage questions, successful current citations/reload NOT RUN. Readyz503 is intentional for unverified tiers. G5 NOT RUN. Full Goal is not accepted.


## Current live checkpoint

Historical results above are retained. Current seven-probe/two-question outcomes, actual running identity, local unit-prompt repair and pending limits are in [LIVE_ACCEPTANCE_20261013.md](LIVE_ACCEPTANCE_20261013.md). Full Goal remains incomplete; G4 current samples FAIL/FAIL/NOT RUN and G5 NOT RUN.
