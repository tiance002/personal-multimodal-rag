# P0/P1 Architecture Review Addendum

**Date:** 2026-09-26

**Applies to:** `docs/superpowers/specs/2026-09-26-p0-p1-langchain-architecture-design.md`

**Status:** accepted implementation constraints from the project owner


## Smart evidence and final-answer ownership

Every `search_knowledge` invocation contributes only server-scoped results to a per-run evidence accumulator. The accumulator deduplicates by stable chunk/version identity, preserves the original query for tracing, and freezes the accumulated union only after the LangChain Agent stops. Final answer validation operates on that frozen union. Smart mode must not call `RAGOrchestrator.answer_query()` after an Agent search; a future project answer generator may consume frozen evidence only and may not perform retrieval.

The server creates an immutable execution context containing `run_id`, `Scope`, cancellation checks, evidence accumulation, limits, cloud policy, embedding profile and authorized version information. These are not model-controlled tool arguments. Model input schemas contain only the minimum business parameters needed by each read-only tool.

## Worker lease and fencing semantics

Claiming a job is one atomic `UPDATE ... RETURNING` transaction for queued or lease-expired work. It sets `status`, `worker_id`, a unique `claim_token`, `lease_until`, stage and `attempts = attempts + 1`. The transaction commits before parsing, embedding or other long-running work begins. Heartbeats, progress updates, recovery and terminal writes are all fenced by the same claim token. A stale worker may finish local computation, but it cannot write a result after its lease has been reclaimed.

`attempts` is incremented only during claim, never inside `process_job()`. Lease expiry recovery and bounded renewal must be covered by real PostgreSQL concurrency tests.

## Cancellation and version concurrency

`rag_runs` and `agent_runs` share the same terminal-state rule: cancellation is conditional on `created/running`; completion and failure are conditional on `created/running`; terminal rows cannot transition again. Execution checks persistent cancellation before every tool call, between Agent turns, before evidence freeze and before the final answer commit. An in-flight model response may be discarded locally, but it cannot write a success state, success event or success message after cancellation.

`read_document` defaults to the server-selected active, index-ready version. Historical versions require an explicitly server-authorized evidence read path; the model cannot select arbitrary historical versions. The document version endpoint must lock the target document row, allocate the next version under a unique constraint, and use the URL `document_id` as the target even when the uploaded file name changes.

## Model compatibility and release gates

The installed LangChain packages and the configured local Ollama model require separate checks. A deterministic fake model verifies adapter behavior; a real Ollama smoke verifies tool-name selection, JSON arguments, multi-turn calls and termination. `ChatOllama.bind_tools()` behavior, including any ignored `tool_choice`, is not a security mechanism. If the local model cannot call tools, Smart reports a bounded capability error or the configured Quick fallback and never silently calls a cloud model.

The release preflight scans executable call sites for forbidden legacy symbols; it must not flag its own allowlist or explanatory documentation. Static checks do not replace real PostgreSQL/pgvector concurrency tests, migration tests, Compose browser/API/SSE tests or the local-model capability smoke.

## Minimum acceptance matrix

| Scenario | Required result |
| --- | --- |
| Two workers claim one job | Exactly one valid claim token is returned. |
| Worker crash and lease expiry | A new worker reclaims the job; the old token cannot write progress or success. |
| Same-name concurrent upload / same-document concurrent version | No duplicate document or duplicate version number is created. |
| Forged KB ID with another document ID | Read is rejected using database-resolved ownership. |
| Out-of-scope or old graph data | Excluded in SQL, not only by Python post-filtering. |
| Different embedding profiles | Never mixed in vector ranking. |
| Unknown tool or invalid arguments | Rejected before business execution and traced as an error. |
| Agent has no valid evidence | No fact claim with a fabricated citation is persisted. |
| Cancellation races with completion | Exactly one terminal state remains; no late success is persisted. |
| Compose browser requests `/api/v1` | Same-origin frontend entry reaches the API. |
| Real local model cannot call tools | Explicit capability failure or Quick fallback; no silent cloud call. |
