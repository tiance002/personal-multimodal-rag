# Personal Multimodal RAG Knowledge Agent V1.0 Design

**Date:** 2026-09-26  
**Status:** implementation baseline  
**Source references:** `AGENTS.md`, `progress.md`, the supplied V4.2 project plan, the supplied V4.2 software design, and `START_PROMPT.txt`.

## Intent and interpretation

The user request is to build V1.0 of a personal, local-first, multi-knowledge-base, multimodal RAG question-answering agent. The supplied plan and design documents define the target contract and constraints; they do not prove that any command, migration, API, model, or feature already exists. The current repository is a new project, so this design defines the initial implementation shape rather than preserving an existing application architecture.

The current project rules in `AGENTS.md` take precedence over copied material from the attachments. The old `E:\codex_workspace\study-plan` repository is a read-only reference only. No database, service, account, learning-domain model, or business API from that repository is a runtime dependency.

## Goals

V1.0 must provide a usable end-to-end local application:

1. Create and switch between personal knowledge bases.
2. Upload text, Markdown, PDF, and image files by drag-and-drop or file selection.
3. Preserve original bytes and immutable document versions; never silently overwrite history.
4. Parse, normalize, chunk, index, and expose processing state with retryable failures.
5. Search the server-selected knowledge-base/document scope using deterministic keyword retrieval plus an independently versioned embedding profile when available.
6. Answer through `AnswerService`: Quick uses a fixed LangChain `Runnable` and Smart uses LangChain `create_agent`; both freeze evidence and validate every citation against the stored source version and locator through one shared RAG Core.
7. Keep L0 original-query retrieval available when the optional local query model is disabled, unavailable, slow, or returns invalid structured output.
8. Provide quick question answering and a bounded smart mode using only four internal read-only knowledge tools.
9. Provide conversation history independently from the knowledge-base document list.
10. Provide a right-side `document | graph` view, with document view as the default and graph failures isolated from ordinary RAG.
11. Enforce local-only and cloud-egress policies, including knowledge-base `cloud_allowed`, global cloud enablement, and a monthly budget gate.
12. Provide backup and restore procedures covering database data, source files, derived assets, and model/index profile metadata.
13. Produce machine-checkable migration, API/SSE, retrieval, milestone, and release evidence.

## Non-goals

V1.0 does not implement login, registration, multi-user SaaS, RBAC, public sharing, Wiki behavior, a multi-agent cluster, external MCP, approval workflows, shell execution, third-party Skills, sandbox execution, artifact downloading, or the V2 database tables and API routes reserved for those capabilities.

## Architecture

The system is a modular monolith with a separate worker process, not a collection of fine-grained microservices.

```text
React/Vite UI
    |
FastAPI HTTP + SSE boundary
    |
Application use cases
    |-- AnswerService / KnowledgeGateway / Ingestion / Conversation
    |-- LangChainQuickChain / LangChainAgentAdapter / KnowledgeToolGateway / Backup
    |
Domain and ports
    |-- NormalizedDocument / Chunk / Evidence / Citation
    |-- keyword + vector retrieval / RRF / QualityGate
    |-- graph evidence and bounded agent policies
    |
Adapters
    |-- PostgreSQL + pgvector repositories
    |-- content-addressed local file storage
    |-- parser registry and OCR/image adapters
    |-- Ollama/local model adapter and optional cloud adapter
    |
Runtime
    |-- API process / worker process / PostgreSQL service / frontend service
    |-- persistent database and storage volumes
```

Dependency direction is `frontend -> API -> application -> domain/ports`; infrastructure implements ports and is wired only in the composition root. Domain code does not import FastAPI, SQLAlchemy, Docker SDK, or provider clients. Ingestion and online RAG communicate through immutable document versions and indexes, not direct calls to one another. Quick and Smart call the shared KnowledgeGateway; LangChain owns execution composition, not retrieval, evidence or persistence rules.

## Repository layout

```text
AGENTS.md
README.md
pyproject.toml
backend/
  app/
    api/                 HTTP, multipart, SSE, error mapping
    application/         use-case orchestration
    domain/               pure models, policies, algorithms
    ports/                repository and provider protocols
    adapters/             PostgreSQL, storage, parser, model adapters
    bootstrap.py          composition root
    main.py               FastAPI application factory
  worker.py              lease-based ingestion/graph worker entrypoint
  tests/
frontend/
  package.json
  src/
    api/                 typed HTTP/SSE client
    components/           three-column UI pieces
    pages/                knowledge base and conversation screens
    state/                selected scope, document, view mode, history
alembic/
  versions/              staged M0 through M4 migrations
contracts/
  openapi.json
  sse/
docs/
  adr/README.md
  adr/ADR-001-model-capabilities.md
  glossary.md
  superpowers/specs/
  superpowers/plans/
evaluations/
  core.jsonl
  incremental.jsonl
scripts/
  verify-m0.ps1 ... verify-release.ps1
  backup.ps1
  restore.ps1
deploy/
  compose.yml
  Dockerfile.api
  Dockerfile.frontend
var/                    local runtime data, ignored by source control
```

## Core domain contracts

### Scope and version

`Scope` is created from explicit user-selected knowledge-base IDs and optional document IDs. The server validates every ID, applies soft-delete and active-version predicates, and stores a scope snapshot on every run. Model output cannot expand scope. A mixed scope containing any knowledge base with `cloud_allowed=false` is treated as local-only for the whole operation; the server does not silently remove that knowledge base.

`DocumentVersion` is immutable after it becomes ready. A replacement upload creates a new version. The worker writes all normalized content, chunks, terms, embeddings, and required validation state for the candidate version before a single transaction switches `documents.active_version_id`. Failed candidates never replace a ready active version. Historical runs retain the exact version and quote snapshot used at answer time.

### Normalized document and source locations

```python
class SourceLocator(BaseModel):
    kind: Literal["text", "markdown", "pdf", "image"]
    page: int | None = None
    start: int | None = None
    end: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    quote: str | None = None


class NormalizedDocument(BaseModel):
    document_id: UUID
    version_id: UUID
    title: str
    media_type: str
    markdown_content: str
    sections: list[DocumentSection]
    assets: list[DocumentAsset]
    source_locators: list[SourceLocator]
    content_sha256: str
    parser_version: str
```

Original bytes are stored separately from the normalized manifest. OCR and captions are derived assets and must point to their source image or page. Derived text is never presented as original text without its derived status.

### Retrieval and evidence

The canonical answer path is:

```text
validate server scope
  -> keep original q0
  -> deterministic query plan
  -> optional bounded local query understanding
  -> keyword + vector candidates
  -> RRF and dedupe
  -> current active-version filter
  -> optional rerank
  -> QualityGate
  -> at most one targeted retry
  -> freeze evidence
  -> ContextBuilder
  -> selected provider after egress/budget gate
  -> citation validation
  -> persist response, evidence and trace
```

Keyword retrieval uses NFKC normalization, lower-casing, special-token preservation, and a deterministic Chinese bigram/term-frequency baseline named `keyword/v1`. Vector retrieval uses a dedicated embedding profile containing provider, model name, model revision, dimension, distance, and fingerprint. Initial PostgreSQL vector retrieval uses exact comparison; ANN indexes and BM25 are not added until an ADR is backed by real evaluation evidence.

HyDE text, query rewrites, model summaries, and graph edges are not original evidence. HyDE is query-only and cannot be stored as a citation or knowledge chunk. A `QualityGate` returns explicit reason codes such as `NO_CANDIDATES`, `LOW_COVERAGE`, `SECTION_TRUNCATED`, `SEMANTIC_MISMATCH`, `VERSION_CONFLICT`, `INDEX_ERROR`, and `NO_EVIDENCE_AFTER_RETRY`. The retry is bounded to one pass and cannot broaden the server scope.

Evidence labels are short opaque labels such as `E1`. The server freezes `E1 -> version_id, chunk_id, locator, quote, quote_sha256` before generation. Final answer labels are resolved server-side; unknown, expired, cross-scope, or unsupported labels are rejected or removed and the run is marked non-successful rather than silently accepted.

## Model routing and privacy

L0 deterministic rules always run. L1 local query understanding is optional and must be schema-validated, timeout-bounded, and switchable. M0 runs `ollama list`, `ollama show <actual-tag>`, structured-output, and latency probes. `ornith-1.5:9b` is only a candidate label until the local probe verifies it; the actual model tag, revision, response shape, and latency are written to ADR-001.

The embedding model is independent from the chat model. If the local chat model is unavailable, the system returns `MODEL_UNAVAILABLE` or a retrieval-only insufficient result; it never silently switches to cloud. Cloud generation, embedding, OCR/VLM, reranking, and query rewriting are only allowed when the global switch is enabled, every selected knowledge base permits cloud egress, and the monthly budget reservation succeeds. A budget reservation is created before the provider call and is settled using actual usage; an unconfirmed sent call keeps a conservative reservation.

Logs contain request/run IDs, stage, error codes, provider metadata, and token/cost counters, but not full private source text by default. API and database services bind to loopback/local Compose networks. Browser code never receives provider credentials.

## Ingestion and worker

The upload endpoint accepts multipart files and a duplicate policy. It validates extension, MIME, magic bytes, byte size, path normalization, and resource limits before streaming to a temporary file while hashing. The file is atomically moved into controlled content-addressed storage. A document/version/job record is created transactionally; upload success means `stored`, not `ready`.

The worker claims jobs with a lease and finite attempts. Stages are `queued -> processing -> indexing -> ready`, with `failed` and `cancelled` terminal states. It parses by MIME and magic number, persists a normalized manifest, creates sections and parent/child chunks, writes terms and embeddings, validates the embedding profile dimension, and atomically activates the version. Graph extraction is a separate optional job with an independent `graph_status`; graph failure leaves `index_status=ready`.

Text and Markdown are required in M1. PDF and image support is required in M2. Normal PDFs use page-aware extraction; scanned PDFs and images use a locally verified OCR/VLM adapter. If an adapter is unavailable, the original asset remains preserved and the job exposes a recoverable explicit failure; no fake OCR text is generated.

## AgentRuntime

Smart mode is a bounded single-agent loop. The only V1 tools are:

| Tool | Input | Output |
|---|---|---|
| `list_documents` | `knowledge_base_ids`, `limit` | scoped document summaries |
| `search_knowledge` | `query`, `knowledge_base_ids`, optional `document_ids`, `top_k` | verified hits and evidence pointers |
| `read_document` | `document_id`, optional `version_id`, `section_id` | exact authorized source content |
| `query_knowledge_graph` | `knowledge_base_id`, `entity_name`, `depth=1` | graph relations with supporting chunks |

Arguments are parsed against closed Pydantic schemas, checked against server scope, and executed read-only. Every step records a redacted input summary, structured output summary, status, sequence number, elapsed time, and evidence references. Limits cover steps, tokens, time, and cost; cancellation produces a terminal `run.cancelled` state. The Agent never exposes hidden chain-of-thought and never imports V2 tool, approval, MCP, sandbox, or skill modules.

## Frontend contract

The UI has three stable regions:

- Left: new conversation, knowledge bases, agent entry, and historical conversations. History is not a document directory.
- Center: selected knowledge base, drag/drop upload, document list, status, retry, and filtering.
- Right: `document` and `graph` tabs. Document is the default; selected tab is green. Switching documents preserves the view mode; switching knowledge bases resets to document view.

The selected state is explicit:

```typescript
type KnowledgeViewState = {
  selectedKnowledgeBaseId: string | null;
  selectedDocumentId: string | null;
  viewMode: "document" | "graph";
};
```

Clicking a citation opens the controlled content endpoint for its frozen version and locator. The UI displays upload, parse, index, and graph states independently and never infers readiness from upload completion.

## HTTP and SSE boundary

All public routes use `/api/v1`. JSON success is `{data, meta:{request_id}}`; errors are `{error:{code,message,details}, meta:{request_id}}`. Missing and unauthorized resources use the same `404 NOT_FOUND` surface. File uploads are multipart; runs are asynchronous and expose a read-only SSE event URL.

The first implementation must expose these route groups:

```text
POST/GET/PATCH/DELETE /knowledge-bases
POST/GET              /knowledge-bases/{kb_id}/documents
GET                   /documents/{document_id}
GET                   /documents/{document_id}/content
GET                   /documents/{document_id}/chunks
GET                   /documents/{document_id}/graph
POST                  /documents/{document_id}/versions
POST                  /documents/{document_id}/graph/rebuild
GET/POST              /ingestion-jobs/{job_id}[/retry]
POST/GET              /conversations
GET                   /conversations/{id}/messages
PATCH/DELETE          /conversations/{id}
POST                  /conversations/{id}/messages
GET                   /runs/{run_id}/events
POST                  /runs/{run_id}/cancel
GET                   /runs/{run_id}/citations/{citation_id}
GET/PATCH             /settings[/models]
```

SSE events include `run.created`, `upload.progress`, `retrieval.started`, `retrieval.completed`, `evidence.frozen`, `tool.started`, `tool.completed`, `tool.failed`, `answer.delta`, `answer.completed`, `run.completed`, `run.failed`, and `run.cancelled`. Events carry monotonic `seq`; replay uses `Last-Event-ID` and the client deduplicates by sequence. V2 approval and artifact events are not registered.

## Database migration stages

Migrations are applied incrementally and verified against a normalized schema snapshot:

- M0: `knowledge_bases`, `documents`, `document_versions`, `ingestion_jobs`.
- M1: sections, chunks, embedding profiles, chunk embeddings, terms, conversations, conversation scope, messages, RAG runs, retrieval events, retrieval hits, answer evidence, and model calls.
- M2: document assets and version-bound asset references.
- M3: graph nodes, graph edges, and graph-edge evidence.
- M4: agent runs, agent steps, and memory items.

The V2-only tables for MCP, tool policies, approvals, skills, sandboxes, and artifacts are not created or referenced. Composite version and knowledge-base constraints prevent cross-version derived assets, graph evidence, and citations. Database migrations use Alembic; UUIDs are application-generated; timestamps are `TIMESTAMPTZ`; large files stay in controlled storage keys rather than database byte columns.

## Verification and release gates

Every named command is implemented before it is reported. The Windows-compatible scripts are the executable source of truth; a Makefile façade is provided when possible. Because `make` is not currently on PATH, a `make verify-*` result is `NOT RUN` until a real make executable is available; the equivalent PowerShell command must still be run and reported separately.

| Gate | Evidence |
|---|---|
| M0 | environment probe, actual local model/embedding probe or explicit L1-unavailable result, four-table migration, deterministic L0 tests, 6–10 sample questions |
| M1 | real text/Markdown upload → index → hybrid search → answer → resolvable citation; L1 on/off/timeout paths |
| M2 | PDF/image/OCR sample, source locator, immutable replacement, retry and cloud-deny tests |
| M3 | fixed evaluation JSONL, Recall@5/Hit@5/MRR where labels exist, quality-gate/retry limit, graph independent status and evidence edges |
| M4 | closed tool registry, scope isolation, step/token/time/cost limits, cancellation, persisted agent trace and final citations |
| M4.5 | backup/restore, API/OpenAPI and SSE contracts, frontend E2E, core regression, and zero unresolved P0/P1 |

The frozen evaluation schema requires `question`, `expected_chunk_ids`, `answer_points`, and `kb_scope`; optional stable fields include `expected_sources` and `dataset_version`. Reports include corpus, chunker, embedding profile, prompt/model fingerprints, latency, tokens, costs, and degradation reasons. Tests distinguish deterministic simulators from real provider and database evidence.

## Risks and explicit current limitations

The empty current root has no existing dependency lock, database, or test runner, so M0 must establish them before later gates can be meaningful. `soffice.exe` is unavailable for rendering the supplied DOCX; that affects reference visual inspection only, not the software runtime. `psql` and `make` are absent from PATH; Docker Compose and PowerShell verification remain the executable route unless the user installs those tools. Actual Ollama model availability, embedding dimensions, OCR capability, and local latency are unknown until M0 probes complete; no model name or capability is treated as verified before that evidence exists.

No V1.0 release claim is valid until the release gate has fresh command output and the requirement-by-requirement evidence audit confirms data integrity, scope isolation, citation authenticity, privacy, budget behavior, and recoverability.
