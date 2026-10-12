# File change manifest

Baseline: b4aa3ec5ff82497fdf9f9f00cdacf75e3128edd3. Code only; no business data/schema change, model call, key/consent edit, budget increase, ledger reset or deployment.

| File | Purpose / invariant |
|---|---|
| `backend/app/adapters/models/cloud.py` | Empty length recognized as truncation; real unverified output rejected before send. |
| `backend/app/adapters/models/runtime_readiness.py` | Report local-reviewed caps and unverified hosted output separately. |
| `backend/app/adapters/models/service_capacity.py` | Ordinary counters and immutable exact-wire local1024 protocol; endpoint guard retained. |
| `backend/app/adapters/models/service_configuration.py` | Select ordinary counters without changing policy IDs/keys/ledger. |
| `backend/app/adapters/models/siliconflow_xing_capacity.py` | Independent ordinary Xing counter; frozen512 stays unchanged. |
| `backend/app/adapters/models/v6_text_capacity.py` | Independent ordinary DeepSeek text counter; frozen V6/vision512 stays unchanged. |
| `backend/app/api/routes.py` | Only ordinary _answer_service budget opt-in; scoped SSE and authoritative finality preserved. |
| `backend/app/application/answer_hardening.py` | Ordinary length policy; legacy generation_budget retained. |
| `backend/app/application/answer_service.py` | Versioned run/understanding and final validation process callbacks; final commit remains unchanged. |
| `backend/app/application/quick_chain.py` | Ordinary immutable budget and attempt envelopes; actual stage callbacks; legacy prompt remains frozen. |
| `backend/app/application/rule_router_v1.py` | Optional attempt identity for ordinary envelopes; absence preserves historical hash; scoring/threshold unchanged. |
| `backend/app/application/run_progress.py` | Safe non-factual stage projection into existing retrieval_events; failure cannot alter generation. |
| `backend/app/ports/run_presentation.py` | Existing diagnosis mapping extended with output-capacity refusal; no factual assistant insertion. |
| `backend/tests/test_answer_service.py` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `backend/tests/test_final_answer_commit.py` | Focused test fixture echoes exact Run identity without weakening scope/terminal assertions. |
| `backend/tests/test_ordinary_output_budget.py` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `backend/tests/test_run_presentation.py` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `backend/tests/test_run_progress.py` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `backend/tests/test_service_configuration.py` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `backend/tests/test_service_usage.py` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `contracts/schema.snapshot.json` | Register run.progress/v1 envelope and safe sample; final event compatibility retained. |
| `contracts/sse/events.sample.jsonl` | Register run.progress/v1 envelope and safe sample; final event compatibility retained. |
| `contracts/sse/events.schema.json` | Register run.progress/v1 envelope and safe sample; final event compatibility retained. |
| `docs/audits/weknora-ordinary-output-progress/CHANGE_MANIFEST.md` | Verification documentation or synthetic browser evidence; no runtime/data behavior. |
| `docs/audits/weknora-ordinary-output-progress/G1_NOTES.md` | Source mapping, verification evidence or rollback/handoff; no runtime behavior. |
| `docs/audits/weknora-ordinary-output-progress/RISK_AND_ROLLBACK.md` | Verification documentation or synthetic browser evidence; no runtime/data behavior. |
| `docs/audits/weknora-ordinary-output-progress/TEST_REPORT.md` | Verification documentation or synthetic browser evidence; no runtime/data behavior. |
| `docs/audits/weknora-ordinary-output-progress/UI_NOTES.md` | Verification documentation or synthetic browser evidence; no runtime/data behavior. |
| `docs/audits/weknora-ordinary-output-progress/WEKNORA_DIFF_MATRIX.md` | Source mapping, verification evidence or rollback/handoff; no runtime behavior. |
| `docs/audits/weknora-ordinary-output-progress/frontend-final-tests.last-run.json` | Verification documentation or synthetic browser evidence; no runtime/data behavior. |
| `docs/audits/weknora-ordinary-output-progress/offline-progress-pending.png` | Verification documentation or synthetic browser evidence; no runtime/data behavior. |
| `docs/audits/weknora-ordinary-output-progress/offline-progress-restored.png` | Verification documentation or synthetic browser evidence; no runtime/data behavior. |
| `frontend/src/api/client.ts` | One POST competes with verified scoped committed terminal/history; no regenerated POST. |
| `frontend/src/api/sse.ts` | Healthy EOF continuation, abnormal retry/backoff, scoped deadline/seq and cancellation. |
| `frontend/src/app/App.tsx` | Per-Run progress/history replay with conversation/KB/document epoch and abort isolation. |
| `frontend/src/app/state.ts` | Safe versioned progress reducer; terminal closes pending; no factual history use. |
| `frontend/src/components/ChatPanel.tsx` | Assistant-side progress adjacent to exact user Run, separate from user content. |
| `frontend/src/components/RunProgress.tsx` | Expandable safe stage timeline; completed runs collapse; no thoughts/percentages. |
| `frontend/src/styles.css` | Scoped timeline presentation and motion accessibility. |
| `frontend/tests/clarification-response.spec.ts` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `frontend/tests/run-progress.spec.ts` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `frontend/tests/scope-sync.spec.ts` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `frontend/tests/stale-message-scope.spec.ts` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
| `frontend/tests/table-header-hint.spec.ts` | Focused offline/browser fixture or regression; SIMULATED provider/PG authority; actual local tokenizer tests identified separately. |
