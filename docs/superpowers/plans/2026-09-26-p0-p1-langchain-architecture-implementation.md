# P0/P1 Boundary Fixes and LangChain M4 Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (or `superpowers:subagent-driven-development` when independent work can be isolated) to execute this plan task by task.

**Goal:** Repair the P0/P1 release-gate and data-boundary defects in the current working tree, then migrate Smart mode to LangChain `create_agent` while preserving the project-owned RAG Core, Scope, evidence, privacy, cancellation and persistence invariants.

**Architecture:** FastAPI routes and workers obtain one composition-root `Container`. `AnswerService` owns the shared run/cancel/commit boundary. Quick mode calls a fixed LangChain Runnable chain; Smart mode calls a `SmartAgentPort` implemented by a thin LangChain adapter. Both modes use one `KnowledgeGateway`/RAG Core for retrieval, scope, evidence and validation; Smart additionally supplies four project-owned read-only tools and an immutable server execution context while LangChain owns only the Agent loop.

**Tech Stack:** Python 3.13 virtual environment, FastAPI, PostgreSQL/pgvector, SQLAlchemy/psycopg, React/Vite, Nginx, LangChain `1.4.2`, `langchain-ollama` `1.1.0`, Ollama, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-p0-p1-langchain-architecture-design.md` plus `docs/superpowers/specs/2026-09-26-p0-p1-langchain-architecture-review-addendum.md`.

## Global constraints

- Read `AGENTS.md`, `progress.md`, the ADR index and the current design before each implementation batch.
- Use the existing checkout; do not overwrite unrelated user changes and do not create a worktree without explicit approval.
- Use TDD: add a focused failing test first, run it and record the failure, then implement the smallest fix and rerun the focused test.
- Do not restore `PostgresKnowledgeRepository.from_url()`, `app.state.store` or `app.state.ollama` compatibility shims.
- Scope, run identity, cloud policy, embedding profile and authorized versions are server-owned, never model-owned tool parameters.
- No V2 features: external MCP, shell, filesystem tools, approval workflows, independent sandbox or multi-agent orchestration.
- Every command in the delivery report must be copied exactly with its exit code; unrun PostgreSQL/Ollama/Docker checks remain `NOT RUN`.

## Task 1: Establish the current baseline and repair composition-root P0 call sites

**Files:** `scripts/smoke_m1.py`, `scripts/smoke_m2.py`, `scripts/smoke_m3.py`, `scripts/smoke_m4.py`, `scripts/smoke_api.py`, `backend/app/main.py`, `backend/app/bootstrap.py`, `backend/app/workers/ingestion.py`, `tests/`, new release-preflight test/script.

1. Add a failing static test that scans executable Python call sites and reports `from_url`, `app.state.store` and `app.state.ollama` usages while ignoring the checker’s own rule data and documentation.
2. Run the test and capture its non-zero failure against the current tree.
3. Add explicit `create_app(settings=None, container=None)` injection and ensure routes read only the injected container. Keep production assembly in `build_container`.
4. Migrate M1–M4 smoke scripts to construct a `Settings` object and call `build_container`; make M4 inject a fully wired test container rather than mutating old app state. Migrate API cleanup through the injected repository.
5. Make the worker use the same composition root and configured chunking/model/storage values as the API.
6. Run the focused static and application-injection tests; only then continue to database behavior.

## Task 2: Make ingestion claiming leased, atomic and fenced

**Files:** `backend/app/workers/ingestion.py`, `backend/app/adapters/postgres/knowledge_repository.py`, `backend/app/ports/`, `backend/app/domain/`, Alembic migration, `tests/unit/`, `tests/integration/`.

1. Add failing tests for two-worker claim exclusivity, queued-to-running transition, `attempts` incrementing only at claim, expired-lease recovery, heartbeat ownership and stale-token finalization.
2. Run those tests to document the pre-fix race.
3. Add a migration for a unique claim token and required indexes/constraints without deleting historical migrations.
4. Implement one atomic `UPDATE ... RETURNING` claim for queued or expired-running jobs. Commit before long-running work. Return `(job_id, worker_id, claim_token, attempts)`.
5. Add lease renewal, token-fenced progress/complete/fail operations and bounded recovery. Ensure `process_job` does not increment attempts again and cannot write after reclamation.
6. Run unit tests and, when the local PostgreSQL service is available, real concurrent integration tests. Mark database tests `NOT RUN` if the service is unavailable.

## Task 3: Enforce Scope, graph, document ownership and embedding-profile isolation

**Files:** `backend/app/application/knowledge_tools.py`, `backend/app/application/retrieval.py`, `backend/app/domain/scope.py`, `backend/app/ports/retrieval.py`, `backend/app/adapters/postgres/graph_repository.py`, `backend/app/adapters/postgres/knowledge_repository.py`, tests.

1. Add failing tests for forged KB/document pairs, document-scope filtering, graph old/deleted/not-ready records, and two embedding profiles with the same dimension.
2. Run the focused tests against the old behavior and retain the failure evidence.
3. Add a repository lookup returning the document’s real KB, active version and readiness/deletion state. Make `read_document` authorize that returned identity, not a tool argument; bound returned content.
4. Extend graph SQL with KB scope, optional document IDs, non-deleted document, active version, index-ready and graph-ready predicates. Keep Python checks as defense in depth.
5. Add exact `profile_id` through the retrieval port, query path and vector SQL; fail explicitly on profile mismatch rather than mixing vectors.
6. Implement a per-run evidence accumulator that deduplicates stable chunk/version identity and validates chunk/version/locator/hash before freezing.
7. Rerun the focused unit tests and, if available, PostgreSQL integration tests.

## Task 4: Fix target-document versioning and ingestion success semantics

**Files:** `backend/app/api/routes.py`, `backend/app/application/ingestion.py`, `backend/app/adapters/postgres/knowledge_repository.py`, parser/chunking modules, migration, tests.

1. Add failing tests for a version upload with a different file name, concurrent version creation, duplicate-name upload policy, scanned/empty PDF extraction and no-chunk indexing.
2. Run the tests to prove the old route can create a second document or mark empty content successful.
3. Introduce an explicit `create_version(document_id, ...)` repository/use-case operation. Lock the document row, allocate the next version under a unique `(document_id, version_no)` constraint and use the route path ID as the target.
4. Keep file-name matching only for the new-document route. Make empty text, OCR unavailable and parser failure explicit non-success states; never activate a version with no usable chunks.
5. Rerun focused tests and migration checks.

## Task 5: Close cancellation/terminal-state races

**Files:** `backend/app/adapters/postgres/knowledge_repository.py`, `backend/app/adapters/postgres/agent_repository.py`, `backend/app/application/answer_service.py`, `backend/app/application/agent_runtime.py` or replacement port, event/trace adapters, tests.

1. Add failing tests for cancel-versus-complete races on both `rag_runs` and `agent_runs`, including no late success event/message and no completed/failed-to-cancel transition.
2. Run the tests against the current unconditional completion behavior.
3. Make cancellation, completion and failure conditional on nonterminal states with one consistent terminal-state rule.
4. Add persistent cancellation checks before tools, between Agent turns, before evidence freeze and before final commit. Discard in-flight model results after cancellation.
5. Ensure both stores and the answer service describe the same terminal outcome and keep the original `q0`.
6. Rerun focused cancellation tests.

## Task 6: Introduce the thin LangChain Smart Agent adapter

**Files:** `pyproject.toml`, lock/requirements files if present, `backend/app/application/agent_ports.py`, `backend/app/application/langchain_agent.py`, tool schemas/gateway, `backend/app/bootstrap.py`, tests.

1. Verify the local package/import state before changing dependency declarations. Add a failing adapter test with a deterministic LangChain-compatible fake model that must select a declared tool, pass valid JSON and terminate.
2. Run the test and capture the missing-import or old-runtime failure.
3. Add and lock the verified LangChain dependencies (`langchain==1.4.2`, `langchain-ollama==1.1.0`) only after package-name/version/install verification.
4. Implement `SmartAgentPort` and `LangChainAgentAdapter` using `langchain.agents.create_agent`. Close the four tools over the immutable server execution context; do not expose Scope/run/cloud/profile/version controls in schemas.
5. Let LangChain own the loop and tool messages. Map callbacks/events to the existing trace store, enforce step/time/token/cost/cancel limits, redact private payloads and feed all retrieval results into the evidence accumulator.
6. Replace `AnswerService` Smart wiring with the port. Remove the old generic loop from the request path and do not call `answer_query()` after Smart retrieval.
7. Run deterministic adapter, evidence, invalid-tool, limit and cancellation tests.

## Task 7: Verify real Ollama capability and complete M4 wiring

**Files:** `scripts/smoke_langchain_ollama.py`, config/ADR/release docs, `backend/app/application/langchain_agent.py`, tests.

1. Add a capability test contract for the configured local model: tool selection, JSON arguments, multi-turn search/read sequence and termination.
2. Run it against the actual local Ollama endpoint/model. Record the exact command, model tag and exit code. A fake-model pass is not a real-model pass.
3. If capability is absent, return a bounded local-model error or configured Quick fallback without cloud calls; do not weaken tool security or claim Smart support.
4. If capability passes, run the full Smart API/SSE path with the LangChain adapter and evidence validation.

## Task 8: Make Compose frontend/API routing same-origin

**Files:** `frontend/vite.config.ts`, `deploy/Dockerfile.frontend`, `deploy/compose.yml`, new `deploy/nginx/nginx.conf`, frontend tests/browser smoke.

1. Add a failing integration/browser assertion that a Compose frontend request to `/api/v1` reaches the API and that SSE is not buffered.
2. Add Vite `preview.proxy` for local preview parity, but use Nginx in Compose.
3. Build a frontend image with a Node build stage and Nginx runtime. Proxy `/api/` and `/healthz` to the `api` service, set SPA fallback and disable proxy buffering for SSE.
4. Run config syntax/build checks, then real Compose/browser checks if Docker is available.

## Task 9: Release gates, evidence and handoff

**Files:** `scripts/verify_release.py` or existing gate entry points, `progress.md`, `docs/`, `.planning/2026-09-26-personal-rag-v1/`.

1. Add preflight ordering: static legacy-symbol check, migrations, focused tests, milestone smoke tests, real PostgreSQL/Compose/Ollama checks.
2. Run every available gate and save reports under the existing reports policy; label missing services/tests `NOT RUN` rather than inferring success.
3. Update progress/findings with exact changes, commands, exit codes, current commit SHA, migration revision, model/index configuration and residual P2/P3 risks.
4. Do not create a release tag until all required P0/P1 checks pass and the project owner confirms acceptance.

## Completion criteria

- No executable legacy `from_url` or old `app.state` dependency remains.
- API, worker and smoke paths share one composition root with explicit test injection.
- Scope/resource ownership, graph SQL filters, embedding profile, versioning, leases and terminal cancellation are covered by tests.
- Quick RAG remains independently usable.
- Smart uses LangChain `create_agent` with exactly four project-owned read-only tools and a frozen, validated evidence union.
- Compose serves frontend and same-origin API/SSE through Nginx.
- The final report distinguishes simulated tests, real services, `NOT RUN`, `NOT IMPLEMENTED`, `FAIL` and `BLOCKED` with exact evidence.
