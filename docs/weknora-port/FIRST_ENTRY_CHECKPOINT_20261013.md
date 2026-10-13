# First actual entry checkpoint — 2026-10-13

Worktree E:/RAG-Worktrees/weknora-python-port-20261013; branch codex/weknora-python-port-resume-20261013. Previous local checkpoints082c2d5/d9c5cbc/31884a8. Original E checkout/C worktree/old databases, keys, volumes, UNKNOWN and historical reports not modified.

## Exact boundary

Actual: native loopback FastAPI/HTTP on56425 (test-only, stopped afterward), verified PostgreSQL56423(system7696088403504697382), Redis56424(runIDc7d5aaff50b2743adea96a426718c5df3483c80f), immutable source file upload, source parser/chunks, real vector and ParadeDB keyword indexes/ready status, server-scope durable admission, P3 understand/retrieval/rerank/merge/context, stored rendered context, P4 SQL history reads, P5 decisions/attempt persistence, P1 monthly ledger, selected answer/reference final commit, Redis SSE and actual citation GET/replay.

Simulated: every supplier uses HTTPX MockTransport, synthetic3D vectors/reported usage/prices, synthetic source corpus and closed fixture permission grants. Document profile postprocessor is explicitly MOCK_NOT_SOURCE_PARITY, not a production summary worker. No real supplier or OCR call, real fee, production readiness or cutover claim.

Current successful witness: KB677bd788-a7bf-48a3-b64c-8048ff9568be; document5e123a4f-cd37-4a41-9543-1c7fb187fc16; session581441c2-c823-4382-91dc-f443ff327004. Client nonce0290f213-eecc-419f-9378-5b9c2df7cb1f maps to server rundb3399f0-fe8b-46fa-86f9-bb07eac47397. Actual3attempts: CheapTRUNCATED, ExpensiveCOMPLETED; subsequent fresh CheapFAILED MODEL_CALL_TIMEOUT/UNKNOWN, no Expensive.14mock wire calls include auxiliary calls. Same-body POST and cached GET replay send no new model request. Citation HTTP200 opens current source chunk. Foreign KB HTTP403; cross-tenant and wrong-KB direct finalizers leave both physical embedding witnesses disabled. Details: evidence/RESUME_HTTP_CHAIN_20261013.json.

## Finite cross-check and corrections

| Contract | Actual inspected mapping / correction |
|---|---|
| Model DTO | PostgresModelStore.resolve returns Source ModelParameters including provider/base_url/api_key. SourceCatalogue verifies then separates identity/secret slots from protocol overrides, avoiding duplicate base_url. Configured roles remain independently pinned; no routing substitution for auxiliary calls. |
| Schema/index |001_retrieval.embeddings has no tenant_id; activation joins knowledges by document+KB and checks tenant. Positive upload and physical cross-tenant/wrong-KB rollback witnesses execute against real migration. No column was invented and migration checksums stay unchanged. |
| Admission DTO/identity | HTTP now imports service.OrdinaryTurnRequest. port_turns distinguishes client_request_id from generated server run_id; accepted descriptor exposes both. POST checks nonce, while SSE emits actual server run. No raw JSON supplies trusted principal/scope. |
| Query callback | PostgresKnowledgeRepository invokes embed_query(KB ID,query,context). Leased binder looks up KB.embedding_model_id under actual tenant/KB scope before invoking neutral registry. |
| History/context | common.go:loadAndProcessHistory intentionally uses raw historical query and separate KnowledgeReferences. Full HistoryRound reaches source merge_history filtering; only plain History enters final Chat. into_chat_message persist_sink stores rendered context but it is not blindly replayed. |
| Generation/ledger | PostgresGenerationLifecycle uses004_generation run/attempt relation;24-test readback joins generation_runs for session scope. New durable gate verifies a fresh connection per authorize/finish. UNKNOWN rows remain occupied. |
| Reference/HTTP | Selected source registry references persist with answer; actual chunks endpoint and quote readback use tenant/KB/doc source records. Typed application Scope denials now have explicit403 status rather than generic500. |
| SSE | Redis raw events reuse stable assistant/run IDs; complete occurs after final SQL commit. Failed buffered Cheap content absent from user stream. Actual framing is event:message with response_type fields, not invented event:complete. |

Actual files: backend/weknora_port/{runtime,documents,http_api,model_registry,service}.py; storage/turns.py; tests/weknora_port/test_runtime_pg.py; evidence path routing only in test_send_gate_pg.py; current STATUS/PARITY_MATRIX and evidence.

## Actual verification (no repeated broad baseline)

From worktree, E:/RAG quention/.venv/Scripts/python.exe, PYTHONDONTWRITEBYTECODE=1 and PYTHONPATH pointing to var/weknora-port/runtime-deps. Explicit synthetic new56423 WEKNORA_PORT_TEST_DSN used for PG cases.

- python -B -m pytest --noconftest -q tests/weknora_port/test_runtime_pg.py -p no:cacheprovider --tb=short: exit0,1passed18.12s (first closed HTTP path).
- python -B -m pytest --noconftest -q tests/weknora_port/test_runtime_pg.py tests/weknora_port/test_http_api.py tests/weknora_port/test_http_consent.py -p no:cacheprovider --tb=short: exit0,24passed20.24s; includes actual tenant/KB negative cases. This supersedes preceding path-only result.
- python -B -m pytest --noconftest -q tests/weknora_port/test_http_api.py tests/weknora_port/test_http_consent.py tests/weknora_port/test_service.py tests/weknora_port/test_generation.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_model_store.py tests/weknora_port/test_model_store_pg.py tests/weknora_port/test_catalog.py tests/weknora_port/test_documents.py tests/weknora_port/test_documents_pg.py -p no:cacheprovider --basetemp=E:/RAG-Tests/w13b --tb=short: exit0,497passed11.80s (4actual PG cases; others offline/protocol).
- python -B -m pytest --noconftest -q tests/weknora_port/test_native_go_oracle.py tests/weknora_port/test_crypto.py -p no:cacheprovider --tb=short: exit0,57passed1.68s.
- npm.cmd ci --ignore-scripts --no-audit --fund=false --cache E:/RAG-Tests/npm-cache-weknora-resume: exit0,194packages; new E frontend only.
- npm.cmd run build from frontend: exit0,TypeScript/Vite3347modules. Browser end-to-end NOT RUN.
- git diff --check: exit0.

Earlier one-case integration failures are real FAIL evidence: request import mismatch; incomplete index fixture config; JSON envelope assumption; duplicated base_url on durable resolve; nonexistent embeddings.tenant_id; KB/query callback incorrectly treated as model ID; final witness assumed session_id on attempts instead of joining runs. Neither completion nor answer assertion was disabled to turn green. All synthetic failed rows/UNKNOWN remain in dedicated database.

## Upstream same-input evidence reused and limitations

Fresh57checks compare Python to previously executed fixed-source54request fixtures (kind exact-request-source-with-neutral-type-stubs) plus3crypto tests including the original Go ciphertext vector. Existing5unmodified GoSSEReader cases and P2 source chunk cases remain inherited evidence; no repeated oracle generation. These establish those bounded observable fields only. Full pinned Go RAG engine/session orchestration was NOT EXECUTED against this new HTTP corpus; do not call full SOURCE_PARITY or phase P6 PASS.

## Remaining minimum gaps

- Production source-equivalent document summary/profile/multimodal postprocessing adapter; pending must remain pending.
- Source-parity streaming: Go chat_completion_stream consumes channel and emits decoded chunks directly; current coordinator buffers all roles, including OFF/baseline and Expensive-only. Cheap+DYNAMIC buffering is authorized, universal buffering is not yet justified. Next minimal correction: buffer only potentially upgrading Cheap; timed same-SSE fixture checks first delta before terminal for baseline/Expensive and no failed Cheap leak, preserving final commit/terminal rules. No upstream or threshold/assertion changes.
- Real sourced prompt estimator at actual assembly; current injected estimator defaults UNKNOWN, never fabricated0.
- P4 automatic extraction/maintenance and optional memory vector adapter at actual entry; SQL mechanisms have evidence, full lifecycle binding remains pending.
- Actual React against this HTTP service, cancellation/disconnect/restart and scoped cursor cases; build alone not enough.
- P7 six dataset full-chain paired runner/Gold boundaries and P8 isolated shadow/rollback prep; real suppliers/production modifications remain AWAITING_OWNER_APPROVAL.
- Direct CPPJieba/full retrieval/provider-chain source oracle and index-engine version comparison remain not fully verified. Existing31PREEXISTING contract issues not re-audited or declared fixed.

Result: IMPLEMENTED / OFFLINE_PASS / ISOLATED_INTEGRATION_PASS for the explicitly bounded entry and negative cases above. Full Goal and full P0–P8 SOURCE_PARITY NOT DONE. REAL_PROVIDER_PASS/CUTOVER_PASS NOT RUN. Parent can review a stable local commit before the next independent domain proceeds. 未实现的命令不得报告通过。
