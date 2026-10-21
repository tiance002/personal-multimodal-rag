# P4 document affinity / normal KB model selection — 2026-10-14

Bounded result: SOURCE_READ / IMPLEMENTED / OFFLINE_PASS / ISOLATED_INTEGRATION_PASS. Full P4, whole ordinary Go engine, REAL_PROVIDER_PASS and CUTOVER_PASS are NOT established. Baseline reviewed143ddb74; pause checkpoint303a124 retained. Owner resumed at2026-10-14 13:22:54 UTC via parent transcript; this supersedes PAUSE_AFFINITY_CHECKPOINT for this batch only.

## Source and actual changes

Pinned Tencent/WeKnora3e8b0bfc80b845b2d4b2ed683994748741450a97: memory_affinity.go OnEvent/affinityFactor; session_knowledge_qa.go selectChatModelID; session_qa_helpers.go resolveChatModelID; MemorySettings.vue handleOpenDocument/handleStopTrackingDocument. Source hashes in fixtures/affinity_kb_go_oracle.json and evidence/P4_AFFINITY_KB_RECEIPT_20261014.json.

Python retrieval/memory_affinity.py reproduces unique ordered lookup<=200, minhits2, logarithmic factor capped1.15 at8hits, stable descending boosted rerank results. Pipeline applies it after source rerank and before merge using runtime trusted MemoryContext; rerank-disabled/no-results is noop. Source memory-service conditioning/settings gates remain authoritative. Exact source5 affinity fixtures and7 normal-model fixtures executed using Go1.26 extracted unmodified functions with DTO/IO seams; this is not full Go engine execution.

model_selection.py/runtime.py priority: configured profile must validate first; valid request KnowledgeQA override wins; then profile; literal SourceRemote across requested KB order; firstKB summaryID; available KnowledgeQA from source-order ListModels. No URL/provider-name cloud heuristic. Server canonical scope remains permission ceiling; only request order is separately snapshotted. Auxiliary model roles and P5 policy untouched.

Frontend MemoryPanel/api/WeKnoraPythonApp consumes existing scoped GET/DELETE documents routes, lists hits/date and paginated total, disables opening absentKB, opens scoped original-document detail without changing chat KB scope, confirms before stoptracking. It preserves original/history and propagates runtime identity refusal.

## Preserved counterexample and fix

P4_AFFINITY_KB_ENTRY_20261014:1failed, exit1, actualHTTP200 instead of409 at reversedKB replay. Implementation bug: a newly admitted sorted multiKB request stored empty options/v1 hash; reverse order could enter the legacy compatibility replay branch. Fix: all NEW explicit multiKB requests store knowledge_base_order, including sorted order, so new order changes conflict. Existing v1 rows/hashes remain untouched, equivalent old replay never sends again. SingleKB empty-option hashing remains historical v1. Added deterministic sorted-order regression; original409 assertion unchanged. No schema migration needed beyond existing011JSON column.

## Commands and evidence

`E:/RAG quention/.venv/Scripts/python.exe -B -m pytest --noconftest tests/weknora_port/test_affinity_pipeline.py tests/weknora_port/test_kb_model_selection.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_turn_storage.py tests/weknora_port/test_affinity_kb_http_pg.py -q -p no:cacheprovider --tb=short --junitxml=docs/weknora-port/evidence/P4_AFFINITY_KB_RESUME_20261014.junit.xml`
Exit0:82passed6.94s. Logs/exit/JUnit P4_AFFINITY_KB_RESUME_20261014.*. Prior80offline evidence reused and reexecuted only this affected set. Actual ASGI/isolated PG7696088403504697382/Redis native assembly with gated MockTransport: four source-selection scenarios, preserved order, no-send repeat,409 reordered request, legacy-v1 synthetic replay, actual affinity consumer before/after delete and disabled settings, original/history readback. Receipt P4_AFFINITY_KB_HTTP_20261014.json. NOT real provider quality.

`npm run build` in frontend. Exit0:tsc --noEmit and Vite build. Evidence P4_AFFINITY_REACT_BUILD_20261014.*. Existing bundle-size warning is retained, no unrelated optimization.

`python -B scripts/weknora_port_affinity_browser_fixture.py` and `npm run dev -- --host 127.0.0.1 --port 56436 --strictPort` with API56435. Only fresh owner125 and new E:/WK/run/21bf512a7fd9, explicit MockTransport consent. First fixture failed exit1 because fresh KB consent disabled; privacy gate correctly refused model-origin egress. Failure log P4_AFFINITY_BROWSER_API_20261014.* retained. Corrected test setup grants only fresh syntheticKB; retry API exits0 after owned stop-file shutdown. Vite exits1 on deliberate Ctrl-C, not acceptance failure. Both ports confirmed not listening afterward; existing PG/Redis retained.

Actual `agent-browser --session weknora-affinity-20261014` Chrome: open56436; findrole button长期记忆; findrole tab常用文档; inspect2+ visible/1hit hidden/missingKB open disabled; open familiar → originallink HTTP200 exactbytes and unchanged chat selection; cancel stoptracking →0DELETE; confirm →1DELETE200 and refreshremoved; exact original readback preserved; fixture identity mismatch →RUNTIME_CONTRACT_INVALID, noadditionalDELETE and remainingrowpreserved. Evidence P4_AFFINITY_BROWSER_CANCEL/SOURCE/DELETE/IDENTITY_20261014.json, screenshot P4_AFFINITY_BROWSER_20261014.png visually inspected. Own session closed; API/nativeworkers/pool stopped. No existing profile/service changes.

## Boundaries / next minimum work

Existing Python model source column stores role.provider vendor identity and physical/config hash depends on it. Fixed Go ConfigFromModel distinguishes Source(local/remote) and Parameters.Provider; NewChat switches only local/remote. This remains a structural SOURCE_PARITY gap; literalremote selector fixture passing is not full source factory equivalence. Next propose an explicit versioned representation/read adapter preserving old immutable rows; no silent vendor/URL inference or old DB migration. Then execute real fixed Go ordinary assembly over same isolated corpus/provider mocks, including post-turn automatic memory scheduling; do not repeat explicit MQ3delivery tests.

Asynq now-30 lease recovery is separately assigned by Owner from stable143ddb74; this batch does not edit memory_queue.py. P8 candidate cleanup/package must follow actual ordinary-entry integration and pinned new migrations/native resources, preserve privacy/ledger/history. Production cutover/old DB/real providers/UNKNOWN release/push/tag/release remain NOT RUN and unauthorized.

未实现的命令不得报告通过。