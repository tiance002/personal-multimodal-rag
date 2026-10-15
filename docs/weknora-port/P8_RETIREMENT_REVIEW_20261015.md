# P8 exact retirement candidates (review only)

Frozen source before inventory: `b5f396dc5d48d64dd6f4825524e872f0967d3402`. 45 exact files. No deletion authorization or deletion performed. Historical records are retained.

AST import and explicit text callers are listed per file in evidence/P8_RETIREMENT_REVIEW_20261015/inventory.json, with line numbers and hashes. This is not a dynamic reachability proof; an empty caller list is not deletion approval.

| Exact file | Current caller locations | Outside candidate set |
|---|---:|---:|
| `backend/app/adapters/models/cloud.py` | 105 | 103 |
| `backend/app/adapters/models/factory.py` | 46 | 46 |
| `backend/app/adapters/redis_stream.py` | 7 | 7 |
| `backend/app/application/agent_ports.py` | 4 | 2 |
| `backend/app/application/answer_hardening.py` | 20 | 12 |
| `backend/app/application/answer_service.py` | 16 | 16 |
| `backend/app/application/answer_validation.py` | 9 | 5 |
| `backend/app/application/citations.py` | 35 | 30 |
| `backend/app/application/context_builder.py` | 19 | 18 |
| `backend/app/application/context_expansion.py` | 3 | 3 |
| `backend/app/application/context_window.py` | 14 | 10 |
| `backend/app/application/evidence_accumulator.py` | 16 | 12 |
| `backend/app/application/evidence_quality.py` | 2 | 1 |
| `backend/app/application/execution_routing.py` | 5 | 4 |
| `backend/app/application/follow_up.py` | 10 | 6 |
| `backend/app/application/knowledge_gateway.py` | 58 | 49 |
| `backend/app/application/knowledge_tools.py` | 20 | 17 |
| `backend/app/application/langchain_agent.py` | 16 | 16 |
| `backend/app/application/llm_rerank.py` | 1 | 1 |
| `backend/app/application/quality.py` | 14 | 10 |
| `backend/app/application/query_expansion.py` | 3 | 2 |
| `backend/app/application/query_router.py` | 20 | 16 |
| `backend/app/application/query_understand.py` | 4 | 4 |
| `backend/app/application/question_checklist.py` | 3 | 2 |
| `backend/app/application/quick_chain.py` | 52 | 49 |
| `backend/app/application/rag_validation_scope.py` | 3 | 3 |
| `backend/app/application/retrieval.py` | 91 | 81 |
| `backend/app/application/retrieval_merge.py` | 3 | 1 |
| `backend/app/application/retrieval_policy.py` | 5 | 4 |
| `backend/app/application/retrieval_profile.py` | 4 | 4 |
| `backend/app/application/retrieval_provenance.py` | 2 | 1 |
| `backend/app/application/router_feature_extraction.py` | 3 | 2 |
| `backend/app/application/rule_router_v1.py` | 59 | 55 |
| `backend/app/application/static_pair_validation.py` | 5 | 5 |
| `backend/app/main.py` | 33 | 31 |
| `backend/app/workers/ingestion.py` | 13 | 13 |
| `frontend/src/app/App.tsx` | 1 | 1 |
| `scripts/evaluate_rag_quality.py` | 0 | 0 |
| `scripts/model_probe.py` | 0 | 0 |
| `scripts/ordinary_verification_fence.py` | 2 | 2 |
| `scripts/replay_answer_hardening.py` | 0 | 0 |
| `scripts/replay_p5_router.py` | 2 | 2 |
| `scripts/smoke_ordinary_deepseek_tls.py` | 0 | 0 |
| `scripts/smoke_ordinary_output_capacity.py` | 0 | 0 |
| `scripts/smoke_service_capacity_linux.py` | 0 | 0 |

## Required caller changes before any later deletion

- `frontend/src/main.tsx` still explicitly lazy-imports legacy App for VITE_RAG_IMPLEMENTATION=legacy. Remove that branch only with Owner approval; build/typecheck must verify new UI remains.
- `deploy/legacy` intentionally names old API/worker/probe paths. Keep historical configs as marked artifacts or explicitly retire them with the approved later change; do not treat their current references as default-new-chain dependencies.
- Existing backend/tests and eval_center/scripts still import old modules. Each outside-candidate caller must be ported, explicitly retired or retained with its dependency; do not mass-delete tests to obtain a passing suite.
- Ledger/cost/Run/Attempt/grant/schema support code listed under protected_support_code_review_hold is excluded from candidates. Retain audit/readback and migration capability until a separately reviewed replacement exists.

## Validation required after a separately approved deletion

1. Resolve every external caller in the JSON inventory, check affected import/API/SSE contracts and test collection; do not run paid legacy probes.
2. Rebuild from exact final Git source; wheel excludes old backend.app and loads all production modules from installed paths. Import new API/worker/init console entries.
3. Run affected offline invariants plus isolated upload/parser/index/QA/reference/history/memory/image-worker and cancellation/replay/idempotency checks, using synthetic model transport.
4. React typecheck/build and actual new-chain UI/HTTP route contract checks; verify legacy selector removal explicitly if approved.
5. Preserve hashes of all historical receipts/Gold/protected files and confirm no schema/volume/UNKNOWN/data deletions. Existing service/cutover and real-provider checks remain separately approved.

Python parse errors: 0; absent requested paths: []. No blanket directory deletion proposed.
