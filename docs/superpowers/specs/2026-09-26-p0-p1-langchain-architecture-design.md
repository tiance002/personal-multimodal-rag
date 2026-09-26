# P0/P1 Boundary Fixes and LangChain M4 Architecture

**Date:** 2026-09-26

**Status:** approved by the project owner in chat; implementation pending plan execution

**Scope:** release-gate integrity, Scope/resource authorization, ingestion concurrency, document versioning, run cancellation, Compose frontend proxy, and M4 Agent runtime migration

## Intent

Repair the current `65e5382` implementation so the verification scripts exercise the same dependency graph as production, and replace the project-owned generic Smart Agent loop with a LangChain `create_agent` adapter while keeping RAG business invariants inside this project.

The supplied review is treated as a defect list to verify against the working tree. The current local code is authoritative for signatures, migrations, and tests.

## Non-goals

- No login, RBAC, SaaS, MCP, shell, filesystem, network, approval, sandbox, or third-party Skills runtime.
- No replacement of the existing PostgreSQL/pgvector retrieval algorithm.
- No broad repository decomposition or formatting-only cleanup in this change.
- No silent compatibility aliases for `PostgresKnowledgeRepository.from_url()` or `app.state.store`/`app.state.ollama`.

## Target architecture

```text
FastAPI routes
    -> AnswerService / ingestion use cases
        -> QueryRouter / ModelRouter
        -> Quick LangChain Runnable | Smart LangChain create_agent
                -> shared KnowledgeGateway / project RAG Core
                    -> Scope, hybrid retrieval, evidence, versions, graph
                -> four project-owned read-only StructuredTools (Smart)
        -> PostgreSQL/pgvector, storage, Ollama
```

`build_container(settings, overrides...)` remains the only composition root. `create_app(settings, container=...)` accepts an explicit container for tests; production still builds one container from settings. Scripts and the worker use the same root.

Quick mode calls a fixed LangChain `Runnable` chain. Smart mode calls LangChain `create_agent` through a project port. Both modes call the same `KnowledgeGateway`, `HybridRetriever`, `EvidenceService` and `AnswerValidator`; Smart tools must not call a second retrieval implementation, and its frozen evidence union is consumed by the shared final-answer validation path.

## Dependency decision

Use the locally available LangChain architecture and lock the verified stable packages in `pyproject.toml`:

- `langchain==1.4.2`
- `langchain-ollama==1.1.0`

The adapter constructs `ChatOllama` with the configured local base URL/model and passes it to `langchain.agents.create_agent`. Tests inject a deterministic LangChain-compatible chat model; they do not contact a cloud provider.

## Boundary fixes

### Composition and verification

- Remove every `from_url` call from scripts; use `Settings` plus `build_container(settings)`.
- Remove every direct `app.state.store`/`app.state.ollama` write; test doubles enter through `create_app(..., container=...)` or a fully wired test container.
- Add a static release check that fails on legacy symbols in `scripts/` and `backend/` before milestone smoke tests run.
- Make the worker use the same configured chunking and model/storage construction as the API.

### Scope and evidence

- Add a repository operation that resolves a document's real, non-deleted knowledge-base ownership and active version.
- `read_document` validates the returned ownership and requested optional version against the server-created `Scope`; tool arguments never establish ownership.
- Graph SQL filters knowledge-base scope, optional document scope, non-deleted documents, active version, ready index, and ready graph status. Python checks remain defense-in-depth.
- Vector candidates filter the active embedding profile used by the current retriever.

### Ingestion and versions

- Claim one queued job with one atomic `UPDATE ... RETURNING` statement that writes `status='running'`, `worker_id`, `lease_until`, `attempts`, and stage.
- Processing requires the claiming worker identity; a stale or non-owner worker cannot finalize another worker's job.
- Add `create_version(document_id, ...)` and use it from `/documents/{document_id}/versions`; file-name matching remains only for the new-document upload route.
- Empty text/PDF extraction is a recoverable explicit failure and never a successful indexed version without chunks.

### Run cancellation

- `cancel_run` writes a terminal cancellation only from `created/running`.
- Completion/failure uses conditional updates from `created/running`; it cannot overwrite `cancelled`.
- Answer/Agent execution checks persistent cancellation before evidence freeze and final commit. Model calls already in flight may finish locally, but their result cannot become a successful persisted run.

### Compose frontend

- Add `deploy/nginx/nginx.conf` in the E: project workspace.
- Convert the frontend image to a build stage plus Nginx runtime.
- Proxy `/api/` and `/healthz` to the Compose `api` service, disable buffering for SSE, and fall back to `index.html` for React routes.
- Keep Vite `server.proxy` for development and add `preview.proxy` for local preview consistency; Compose uses Nginx rather than `vite preview`.

## LangChain Smart Agent contract

The adapter exposes only:

- `list_documents`
- `search_knowledge`
- `read_document`
- `query_knowledge_graph`

Each tool has a closed Pydantic input schema and closes over a server-created `Scope`. Tool calls are read-only and return verified summaries/evidence pointers. The adapter records redacted steps, token usage, cost, elapsed time, and errors into the existing trace store. Project limits remain authoritative: step, token, time, cost, and cancellation failures end the run with the existing error codes.

The old project loop is removed from the request path. `AnswerService` depends on a `SmartAgentPort`, and `LangChainAgentAdapter` is the only M4 implementation. No second generic Agent runtime remains.

## Verification contract

Required regression coverage:

1. Static legacy-symbol check fails before fixes and passes after fixes.
2. Smoke scripts build the same container graph as production.
3. Test-container injection controls the actual API route dependencies.
4. Graph, document-read, vector-profile, version, cancellation, and worker-claim isolation tests fail on the old behavior.
5. LangChain import, deterministic `create_agent` tool loop, trace persistence, and limit/cancel behavior pass.
6. Nginx Compose returns frontend HTML, proxies `/api/v1/health`-equivalent routes and `/healthz`, and streams SSE without buffering.
7. Affected milestone gates and the full release gate run with real PostgreSQL/pgvector; every unrun command is explicitly marked `NOT RUN`.

## Risks and explicit limits

- Local Ollama tool-calling support must be verified against the installed model; if unavailable, Smart mode reports a bounded local-model capability error and Quick RAG remains available.
- Docker/Nginx verification requires the local Docker engine; no container success is claimed from a build-only run.
- Existing P2 cleanup and large-repository decomposition remain outside this focused P0/P1 migration.
