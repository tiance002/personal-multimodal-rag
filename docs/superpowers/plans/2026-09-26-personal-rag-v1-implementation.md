# Personal Multimodal RAG Knowledge Agent V1.0 Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task. Every production change follows the TDD red-green-refactor loop.

**Goal:** Build and verify a local-first personal multi-knowledge-base multimodal RAG question-answering agent with immutable source versions, scope-safe citations, bounded read-only Agent tools, and a three-column UI.

**Architecture:** Create a new modular-monolith repository in the current root. FastAPI exposes JSON/multipart/SSE, application services orchestrate ingestion, retrieval, conversation, and Agent runs, pure domain code owns normalization/retrieval/evidence rules, and adapters connect PostgreSQL/pgvector, controlled file storage, local model providers, and parsers. A separate polling worker executes leased ingestion and graph jobs.

**Tech Stack:** Python 3.11+ with FastAPI, Pydantic v2, SQLAlchemy/Alembic, psycopg, PostgreSQL + pgvector, PyMuPDF/Pillow and a verified local OCR adapter; React + Vite + TypeScript; Docker Compose; PowerShell verification scripts; Ollama as an optional local provider.

**Spec:** `docs/superpowers/specs/2026-09-26-personal-rag-v1-design.md`

## Global Constraints

- `RAGOrchestrator` is the only quick-answer application orchestrator; do not create `QuickQAService`.
- L0 original query `q0` retrieval is always available; L1 local query understanding is optional and cannot block q0.
- Server-side knowledge-base/document scope and current active-version filtering are mandatory on every read and retrieval path.
- Original files and ready document versions are immutable; a replacement creates a new version and activates it only after indexes are ready.
- Evidence labels resolve only to frozen, readable source snapshots; HyDE and other derived text are never evidence.
- `cloud_allowed=false` or global cloud disablement forbids sending questions, chunks, images, embeddings, or derived content to cloud providers.
- V1.0 does not implement or migrate MCP, approvals, sandboxes, third-party Skills, artifacts, or shell execution.
- Do not report a command as passed unless the command exists and was executed with a recorded exit code.
- Test production behavior before refactoring; mocks are allowed only at provider/DB boundaries where the boundary is the behavior under test.
- The current root is not a Git worktree; do not invent branch names, commit SHAs, or tags.

## Review Focus

- A duplicate upload with different bytes must create or explicitly select a new version and must never replace the current version silently. Test in Task 4.
- A query selecting two knowledge bases must not retrieve a chunk from an unselected or deleted knowledge base, including through graph edges or historical versions. Test in Tasks 3, 5, and 8.
- A citation must resolve to the exact frozen quote/version/locator captured at answer time even after a newer document version becomes active. Test in Tasks 3 and 5.
- Any selected knowledge base that disallows cloud egress must prevent every provider call for that run, not merely omit that knowledge base. Test in Task 6.
- A timeout, invalid JSON, missing model, missing OCR adapter, or graph failure must surface an explicit state while preserving the available local retrieval path. Test in Tasks 2, 4, 5, and 7.

---

### Task 1: Establish M0 project scaffold and executable verification

**Files:**
- Create: `pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/main.py`
- Create: `backend/app/bootstrap.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`
- Create: `docs/adr/README.md`
- Create: `docs/adr/ADR-001-model-capabilities.md`
- Create: `contracts/README.md`
- Create: `evaluations/core.jsonl`
- Create: `scripts/verify-m0.ps1`
- Create: `scripts/verify-m1.ps1`
- Create: `scripts/verify-m2.ps1`
- Create: `scripts/verify-m3.ps1`
- Create: `scripts/verify-m4.ps1`
- Create: `scripts/verify-release.ps1`
- Create: `scripts/eval.ps1`
- Create: `deploy/compose.yml`
- Create: `.env.example`
- Create: `README.md`
- Modify: `progress.md`

**Interfaces:**
- `create_app(settings: Settings) -> FastAPI` returns an app with `GET /healthz` and no provider calls.
- `Settings` reads loopback host, storage root, database URL, cloud switches, local model switches, and bounded limits from environment with safe local defaults.
- Each verification script exits non-zero on failure and writes a JSON report under `var/reports/`.

- [ ] **Step 1: Write the failing health test**

```python
def test_healthz_reports_local_service_without_external_calls(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["data"]["service"] == "personal-rag"
    assert response.json()["data"]["cloud_enabled"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_health.py -q`

Expected: collection or assertion failure because `backend.app.main` and `/healthz` do not yet exist.

- [ ] **Step 3: Verify dependencies before pinning**

Run the bundled Python import probe for `fastapi`, `pydantic`, `sqlalchemy`, `alembic`, `psycopg`, `pgvector`, `fitz`, and `PIL`. Record each import/version result. If a package is absent, add the verified package name to `pyproject.toml`, install it in the project environment, and rerun the same probe; do not substitute an unverified package name.

- [ ] **Step 4: Implement the minimal app/config**

Implement `Settings` with `cloud_enabled=False`, `local_query_enabled=True`, loopback binding, bounded upload/worker limits, and no API-key logging. Implement `create_app` with the standard response envelope and `/healthz` only.

- [ ] **Step 5: Run the focused test and then the complete available suite**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_health.py -q`  
Expected: PASS.

Run: `& $env:PYTHON_EXE -m pytest -q`  
Expected: PASS with the current test count; an empty or unavailable suite is recorded as NOT IMPLEMENTED, not PASS.

- [ ] **Step 6: Add M0 artifacts and model probe**

Run the actual `ollama list`, `ollama show <actual-tag>`, local structured-output chat probe, and embedding probe. Store redacted JSON under `var/reports/m0-model-probe.json` and write actual tags, revisions, dimensions, latency, failures, and the L1 switch decision to `docs/adr/ADR-001-model-capabilities.md`. Add six to ten machine-readable questions to `evaluations/core.jsonl` with the required fields `question`, `expected_chunk_ids`, `answer_points`, and `kb_scope`.

- [ ] **Step 7: Implement and run `scripts/verify-m0.ps1`**

The script runs dependency/import checks, the M0 migration check from Task 2, deterministic domain tests, the local provider probe, and evaluation-schema validation. Run: `& .\scripts\verify-m0.ps1`. Record its exit code and report path in `progress.md`.

### Task 2: Add domain models, deterministic policies, and staged database foundation

**Files:**
- Create: `backend/app/domain/models.py`
- Create: `backend/app/domain/errors.py`
- Create: `backend/app/domain/scope.py`
- Create: `backend/app/domain/text_normalization.py`
- Create: `backend/app/domain/chunking.py`
- Create: `backend/app/domain/fusion.py`
- Create: `backend/app/domain/evidence.py`
- Create: `backend/app/ports/repositories.py`
- Create: `backend/app/ports/providers.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_m0_core.py`
- Create: `scripts/schema_check.ps1`
- Create: `backend/tests/test_scope.py`
- Create: `backend/tests/test_text_normalization.py`
- Create: `backend/tests/test_chunking.py`
- Create: `backend/tests/test_fusion.py`
- Create: `backend/tests/test_evidence.py`
- Create: `backend/tests/test_schema_check.py`

**Interfaces:**
- `normalize_query(text: str) -> NormalizedQuery` preserves q0 and returns deterministic NFKC terms/bigrams.
- `chunk_document(document: NormalizedDocument, max_chars: int, overlap: int) -> list[ChunkDraft]` returns contiguous source spans and heading paths.
- `rrf_fuse(rankings: Mapping[str, Sequence[RankedHit]], k: int = 60) -> list[RankedHit]` returns deterministic fused order with stable tie-breaks.
- `Scope` contains explicit KB IDs and optional document IDs; `Scope.assert_contains(kb_id, document_id)` raises a stable domain error.
- `EvidenceSnapshot` contains label, version ID, chunk ID, quote, quote hash, and locator; `EvidenceResolver.resolve(label)` re-reads and verifies the frozen mapping.

- [ ] **Step 1: Write failing tests for normalization, chunks, RRF, scope, and evidence**

```python
def test_normalization_preserves_q0_and_adds_chinese_bigrams():
    plan = normalize_query("  事务回滚  ")
    assert plan.q0 == "  事务回滚  "
    assert "事务" in plan.terms
    assert "回滚" in plan.terms


def test_rrf_fusion_has_stable_tie_break_and_retains_source_labels():
    hits = rrf_fuse({"keyword": [hit("b", 1), hit("a", 2)], "vector": [hit("a", 1)]})
    assert [h.chunk_id for h in hits[:2]] == ["a", "b"]
    assert set(h.sources for h in hits) >= {"keyword", "vector"}


def test_evidence_rejects_cross_scope_chunk():
    with pytest.raises(ScopeViolation):
        scope.assert_contains(kb_id="kb-b", document_id="doc-b")
```

- [ ] **Step 2: Run the focused tests to verify the expected missing-symbol failures**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_text_normalization.py backend/tests/test_fusion.py backend/tests/test_scope.py backend/tests/test_evidence.py -q`  
Expected: FAIL because the domain modules do not yet expose the specified functions.

- [ ] **Step 3: Implement pure domain behavior**

Implement the typed Pydantic/domain dataclasses, Unicode normalization, special-token extraction, Chinese bigrams, contiguous chunk spans, heading paths, stable RRF, scope checks, and hash-verified evidence snapshots without importing database or web frameworks.

- [ ] **Step 4: Run focused and complete tests**

Run the four focused test files and then `& $env:PYTHON_EXE -m pytest -q`. Expected: all current tests PASS.

- [ ] **Step 5: Write the failing migration/schema test**

The test must inspect an empty PostgreSQL database when Compose is available and assert exactly four M0 tables, required columns/constraints, and no V2 table names. When PostgreSQL is unavailable, it must emit `NOT RUN` with the dependency reason rather than pass through a fake SQLite schema.

- [ ] **Step 6: Implement M0 Alembic migration and schema checker**

Create only `knowledge_bases`, `documents`, `document_versions`, and `ingestion_jobs`. Add soft-delete, active-version, immutable source hash, graph/index status, job lease, attempt, and progress constraints. Implement `scripts/schema_check.ps1` to run the real migration and compare a normalized schema snapshot.

- [ ] **Step 7: Run schema verification**

Run: `& .\scripts\schema_check.ps1 -Stage M0` and `& $env:PYTHON_EXE -m pytest backend/tests/test_schema_check.py -q`. Record real database availability and exit codes.

### Task 3: Implement content-addressed storage, parsers, and immutable ingestion

**Files:**
- Create: `backend/app/domain/parsers.py`
- Create: `backend/app/adapters/storage.py`
- Create: `backend/app/adapters/parsers/text.py`
- Create: `backend/app/adapters/parsers/markdown.py`
- Create: `backend/app/adapters/parsers/pdf.py`
- Create: `backend/app/adapters/parsers/image.py`
- Create: `backend/app/application/ingestion.py`
- Create: `backend/app/application/knowledge.py`
- Create: `backend/app/adapters/postgres/knowledge_repository.py`
- Create: `backend/app/adapters/postgres/ingestion_repository.py`
- Create: `backend/app/workers/ingestion.py`
- Create: `backend/tests/test_storage.py`
- Create: `backend/tests/test_parsers.py`
- Create: `backend/tests/test_ingestion_state_machine.py`
- Create: `backend/tests/test_version_activation.py`

**Interfaces:**
- `ContentAddressedStorage.put_stream(stream) -> StoredObject(storage_key, sha256, size)` writes a temporary file and atomically moves it into controlled storage.
- `ParserRegistry.parse(path, media_type, document_id, version_id) -> NormalizedDocument` selects by validated media type and file signature.
- `IngestionService.submit_upload(scope_kb_id, file, duplicate_policy) -> UploadReceipt` stores bytes and registers a version/job.
- `IngestionWorker.process(job_id) -> JobState` claims a lease, progresses stages, and activates only ready versions.

- [ ] **Step 1: Write failing tests for storage/parser/state behavior**

```python
def test_same_bytes_are_content_addressed_and_different_bytes_do_not_overwrite(storage):
    first = storage.put_stream(io.BytesIO(b"one"))
    second = storage.put_stream(io.BytesIO(b"two"))
    assert first.storage_key != second.storage_key
    assert storage.read(first.storage_key) == b"one"


def test_failed_candidate_does_not_replace_active_version(service, active_document):
    failed = service.process_version(active_document.id, b"broken", force_failure=True)
    assert failed.index_status == "failed"
    assert service.active_version(active_document.id).id == active_document.active_version_id
```

- [ ] **Step 2: Run focused tests and observe missing implementation failures**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_storage.py backend/tests/test_parsers.py backend/tests/test_ingestion_state_machine.py backend/tests/test_version_activation.py -q`  
Expected: FAIL because storage, parser, and ingestion services are absent.

- [ ] **Step 3: Implement storage, parser registry, text/Markdown/PDF/image parsing, and worker lease**

Preserve original bytes separately from normalized content. Text/Markdown parsers must return exact spans. PDF parsing must keep page numbers. Image parsing must preserve the source asset and return a derived-task state when no verified OCR adapter is available. Worker retries are finite and stale jobs cannot activate a newer candidate.

- [ ] **Step 4: Add M1 migration for sections/chunks/terms/embeddings and repository writes**

Create the M1 tables in a separate Alembic revision. The repository must write candidate version data first, verify all required indexes, and then activate in one transaction. Cross-version composite foreign keys are required for section/chunk/asset relationships.

- [ ] **Step 5: Run focused ingestion tests and the full available suite**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_storage.py backend/tests/test_parsers.py backend/tests/test_ingestion_state_machine.py backend/tests/test_version_activation.py -q` and then `& $env:PYTHON_EXE -m pytest -q`. Expected: PASS, or a real PostgreSQL/PDF/OCR capability is recorded as NOT RUN/BLOCKED with its command output.

### Task 4: Implement model adapters, hybrid retrieval, quality gate, and RAGOrchestrator

**Files:**
- Create: `backend/app/adapters/models/ollama.py`
- Create: `backend/app/adapters/models/cloud.py`
- Create: `backend/app/application/model_policy.py`
- Create: `backend/app/application/rag_orchestrator.py`
- Create: `backend/app/application/context_builder.py`
- Create: `backend/app/application/citations.py`
- Create: `backend/app/adapters/postgres/retrieval_repository.py`
- Create: `alembic/versions/0003_m1_rag.py`
- Create: `backend/tests/test_model_policy.py`
- Create: `backend/tests/test_hybrid_retrieval.py`
- Create: `backend/tests/test_quality_gate.py`
- Create: `backend/tests/test_rag_orchestrator.py`
- Create: `backend/tests/test_citation_resolution.py`

**Interfaces:**
- `ModelGateway.classify/query_expand/embed/chat` exposes typed provider calls and returns `ProviderUnavailable`, `SchemaInvalid`, or `BudgetDenied` without hiding the cause.
- `HybridRetriever.retrieve(scope, question, policy) -> RetrievalResult` filters scope/current versions before scoring and returns keyword/vector sources.
- `RAGOrchestrator.answer_query(question, scope, settings) -> AnswerResult` performs at most one targeted retry, freezes evidence, and validates citations.
- `CitationService.resolve(run_id, citation_id) -> CitationDetail` verifies quote hash and version-bound locator before returning content.

- [ ] **Step 1: Write failing tests for L1 fallback, scope filtering, budget/egress, and citation integrity**

```python
def test_local_query_timeout_keeps_q0_and_retrieves(retriever, timeout_gateway):
    result = RAGOrchestrator(...).answer_query("原始问题", scope, settings=local_enabled())
    assert result.query_plan.q0 == "原始问题"
    assert result.trace.degradation_code == "LOCAL_QUERY_FALLBACK"


def test_cloud_is_not_called_when_any_selected_kb_disallows_egress(cloud_gateway):
    result = orchestrator.answer_query("secret", mixed_scope, settings=cloud_enabled())
    assert result.error_code == "CLOUD_EGRESS_DISABLED"
    assert cloud_gateway.calls == []


def test_old_citation_resolves_after_new_active_version(citation_service):
    citation = citation_service.freeze(run_id, old_chunk)
    activate_new_version()
    resolved = citation_service.resolve(run_id, citation.id)
    assert resolved.version_id == old_chunk.version_id
    assert resolved.current_status == "superseded"
```

- [ ] **Step 2: Run the focused tests and verify expected failures**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_model_policy.py backend/tests/test_hybrid_retrieval.py backend/tests/test_quality_gate.py backend/tests/test_rag_orchestrator.py backend/tests/test_citation_resolution.py -q`  
Expected: FAIL because adapters, repositories, orchestrator, and citation service are absent.

- [ ] **Step 3: Implement deterministic retrieval and model policy**

Implement q0 retention, schema-validated local query understanding, keyword/vectors, RRF, current-version filtering, quality reason codes, one retry, ContextBuilder budgets, provider selection, cloud egress gate, and budget reservation interface. Use a deterministic fake provider for unit tests and the actual Ollama adapter only in M0/M1 smoke tests.

- [ ] **Step 4: Implement citation freeze and validation**

Store evidence labels, quote hash, version ID, chunk ID, and locator snapshot. Reject unknown or cross-scope citations and retain historical validity badges.

- [ ] **Step 5: Add M1 API/application integration tests**

Use a real test database when Docker PostgreSQL is available. Exercise text upload, worker completion, retrieval, answer persistence, evidence freeze, and citation readback. Keep provider simulator and real-provider evidence in separate report sections.

- [ ] **Step 6: Run `scripts/verify-m1.ps1` and record results**

The script runs the M1 migration, domain/repository integration checks, real text end-to-end if PostgreSQL is available, L1 on/off/timeout tests, and citation readback. A missing runtime capability is explicitly reported as NOT RUN/BLOCKED.

### Task 5: Expose the public API and SSE contracts

**Files:**
- Create: `backend/app/api/envelope.py`
- Create: `backend/app/api/errors.py`
- Create: `backend/app/api/knowledge_routes.py`
- Create: `backend/app/api/ingestion_routes.py`
- Create: `backend/app/api/conversation_routes.py`
- Create: `backend/app/api/run_routes.py`
- Create: `backend/app/api/settings_routes.py`
- Create: `backend/app/api/sse.py`
- Create: `contracts/openapi.json`
- Create: `contracts/sse/events.schema.json`
- Create: `contracts/sse/samples.jsonl`
- Create: `backend/tests/test_api_contracts.py`
- Create: `backend/tests/test_sse_contract.py`

**Interfaces:**
- All JSON responses use `{data, meta:{request_id}}` or `{error:{code,message,details}, meta:{request_id}}`.
- Uploads return `202` with `UploadReceipt`; message posts return `202` with `RunReceipt` and `event_url`.
- SSE events carry monotonic `seq`, event type, run ID, and structured payload; replay accepts `Last-Event-ID`.

- [ ] **Step 1: Write failing contract tests**

Assert route status codes, error codes, no absolute storage paths, no V2 routes, required OpenAPI path names, monotonic SSE sequence, and deduplicated replay behavior.

- [ ] **Step 2: Run contract tests and observe failure**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_api_contracts.py backend/tests/test_sse_contract.py -q`  
Expected: FAIL because the route modules and snapshots do not yet exist.

- [ ] **Step 3: Implement typed DTOs, routes, error mapping, and persistent event replay**

Keep scope validation in application services; API routes only validate DTOs, invoke use cases, and translate errors. Never expose a host filesystem path. Emit upload stages independently and terminal run events explicitly.

- [ ] **Step 4: Generate and compare OpenAPI/SSE snapshots**

Run the snapshot generator, inspect the diff, and store the approved JSON in `contracts/`. Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_api_contracts.py backend/tests/test_sse_contract.py -q`.

### Task 6: Add M2 multimodal assets, OCR capability handling, and document UI backend

**Files:**
- Create: `alembic/versions/0004_m2_assets.py`
- Create: `backend/app/application/assets.py`
- Create: `backend/app/adapters/ocr/local.py`
- Modify: `backend/app/application/ingestion.py`
- Modify: `backend/app/api/knowledge_routes.py`
- Create: `backend/tests/test_multimodal_ingestion.py`
- Create: `backend/tests/test_egress_matrix.py`
- Create: `backend/tests/fixtures/sample.pdf`
- Create: `backend/tests/fixtures/sample-image.png`
- Create: `scripts/verify-m2.ps1`

**Interfaces:**
- `AssetService.read_asset(version_id, asset_id) -> AssetDetail` returns controlled content and source locator.
- `OcrProvider.probe() -> CapabilityReport` is explicit; an unavailable provider creates a recoverable failed derived stage without corrupting the original.
- `DocumentContent` returns page/section/image locators without absolute host paths.

- [ ] **Step 1: Write failing tests for PDF/image locators, asset lineage, retries, and egress denial**

Assert page numbers for PDF content, `derived_from_asset_id` for OCR/caption data, preserved original bytes after OCR failure, retryable job state, and zero provider calls for a cloud-denied KB.

- [ ] **Step 2: Run focused tests to see missing adapter/table failures**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_multimodal_ingestion.py backend/tests/test_egress_matrix.py -q`.

- [ ] **Step 3: Implement M2 assets and verified local OCR path**

Use the actual available local OCR capability from the probe. If no adapter is available, the test report must show explicit `OCR_UNAVAILABLE`; it must not mark scanned samples ready with invented text.

- [ ] **Step 4: Run `scripts/verify-m2.ps1`**

Verify normal PDF, image, and scanned/unsupported behavior separately; record actual capability and exit code.

### Task 7: Add optional graph extraction and document/graph read APIs

**Files:**
- Create: `alembic/versions/0005_m3_graph.py`
- Create: `backend/app/application/graph.py`
- Create: `backend/app/adapters/graph/extractor.py`
- Create: `backend/app/adapters/postgres/graph_repository.py`
- Modify: `backend/app/workers/ingestion.py`
- Modify: `backend/app/api/knowledge_routes.py`
- Create: `backend/tests/test_graph_evidence.py`
- Create: `backend/tests/test_graph_failure_isolation.py`
- Create: `backend/tests/test_evaluation_schema.py`
- Create: `scripts/eval.ps1`
- Create: `evaluations/incremental.jsonl`

**Interfaces:**
- `GraphService.build(version_id) -> GraphJobResult` validates triples and attaches every edge to evidence chunks.
- `GraphService.get_document_graph(document_id, version_id) -> GraphDTO` returns current authorized subgraph and status.
- `GraphService.query(scope, entity_name, depth=1) -> GraphResult` returns relations only with supporting source chunks.

- [ ] **Step 1: Write failing tests for graph evidence and failure isolation**

Assert a graph edge cannot be persisted without a chunk in the same knowledge base/version, graph failure leaves `index_status=ready`, and graph query cannot return an unselected KB node.

- [ ] **Step 2: Run focused graph tests and observe missing table/service failures**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_graph_evidence.py backend/tests/test_graph_failure_isolation.py -q`.

- [ ] **Step 3: Implement deterministic document structure graph and optional entity extractor**

The document structure graph is deterministic. Entity extraction is optional and provider-gated; graph status is independent. Graph edges are never answer evidence without text readback.

- [ ] **Step 4: Implement the frozen evaluation runner**

Validate the JSONL required fields, compute Hit@5/Recall@5/MRR when expected IDs exist, record profile/model/chunker/prompt versions, and write machine-readable reports. Do not compute or claim Precision@5 without complete relevance labels.

- [ ] **Step 5: Run `scripts/eval.ps1` and `scripts/verify-m3.ps1`**

Run both with the fixed core set and record actual corpus/provider availability, retrieval metrics, timings, costs, and graph state.

### Task 8: Add conversations, bounded read-only AgentRuntime, memory boundaries, and traces

**Files:**
- Create: `alembic/versions/0006_m4_agent.py`
- Create: `backend/app/application/conversations.py`
- Create: `backend/app/application/agent_runtime.py`
- Create: `backend/app/application/knowledge_tools.py`
- Create: `backend/app/domain/agent_policy.py`
- Create: `backend/app/adapters/postgres/conversation_repository.py`
- Create: `backend/app/adapters/postgres/agent_repository.py`
- Modify: `backend/app/api/conversation_routes.py`
- Modify: `backend/app/api/run_routes.py`
- Create: `backend/tests/test_conversation_scope.py`
- Create: `backend/tests/test_agent_tool_allowlist.py`
- Create: `backend/tests/test_agent_limits.py`
- Create: `backend/tests/test_agent_trace_sse.py`
- Create: `scripts/verify-m4.ps1`

**Interfaces:**
- `KnowledgeToolGateway.invoke(tool_name, args, scope) -> ToolResult` exposes exactly four read-only tools and validates all scope arguments server-side.
- `AgentRuntime.run(conversation_id, question, scope, limits) -> AgentRunResult` persists steps and returns final verified citations.
- `AgentRuntime.cancel(run_id) -> RunState` is idempotent and leaves a terminal cancelled trace.

- [ ] **Step 1: Write failing tests for closed tools, scope, limits, cancellation, and history separation**

Assert `shell_exec`, unknown tools, out-of-scope documents, excessive steps/tokens/time/cost, and graph-unavailable calls are rejected with stable codes. Assert conversation history can list messages without appearing in the knowledge-base document list.

- [ ] **Step 2: Run focused Agent tests and observe missing implementation failures**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_conversation_scope.py backend/tests/test_agent_tool_allowlist.py backend/tests/test_agent_limits.py backend/tests/test_agent_trace_sse.py -q`.

- [ ] **Step 3: Implement M4 persistence and bounded runtime**

Persist redacted step summaries, tool names, status, sequence, budget counters, and evidence references. Use the same RAGOrchestrator/retrieval service for `search_knowledge`; do not duplicate ranking logic.

- [ ] **Step 4: Run focused tests, full suite, and `scripts/verify-m4.ps1`**

Run the focused files, `& $env:PYTHON_EXE -m pytest -q`, and the real verification script. Distinguish simulator results from actual provider results.

### Task 9: Build the React/Vite three-column application

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/state.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/sse.ts`
- Create: `frontend/src/components/Sidebar.tsx`
- Create: `frontend/src/components/KnowledgeBasePanel.tsx`
- Create: `frontend/src/components/DocumentPanel.tsx`
- Create: `frontend/src/components/GraphPanel.tsx`
- Create: `frontend/src/components/ChatPanel.tsx`
- Create: `frontend/src/styles.css`
- Create: `frontend/tests/app.spec.ts`
- Create: `frontend/tests/upload-and-citation.spec.ts`

**Interfaces:**
- `KnowledgeViewState` contains `selectedKnowledgeBaseId`, `selectedDocumentId`, and `viewMode: "document" | "graph"`.
- `ApiClient` calls only `/api/v1` and never holds provider credentials.
- `SseClient` deduplicates events by monotonic `seq` and reconnects with `Last-Event-ID`.

- [ ] **Step 1: Write failing UI tests**

Assert left history is not the document list, changing knowledge base resets to document view, changing documents preserves view mode, drag/drop and file picker share the same upload call, the selected tab is green, and a citation opens the correct version/locator.

- [ ] **Step 2: Run the UI tests and observe missing-app failures**

Run: `npm --prefix frontend test -- --runInBand` or the verified package-manager equivalent. Expected: FAIL because the Vite app and components do not exist.

- [ ] **Step 3: Implement the minimal typed UI and API client**

Build the three-column layout, explicit selection state, upload queue/status, conversation history, quick/smart mode toggle, document/graph tabs, citation links, and SSE rendering. Keep all data access behind `ApiClient`.

- [ ] **Step 4: Run frontend unit/component tests and build**

Run: `npm --prefix frontend test -- --runInBand` and `npm --prefix frontend run build`. Expected: PASS with exit code 0; record the actual installed npm package versions.

### Task 10: Add deployment, backup/restore, and release verification

**Files:**
- Create: `deploy/Dockerfile.api`
- Create: `deploy/Dockerfile.frontend`
- Modify: `deploy/compose.yml`
- Create: `scripts/backup.ps1`
- Create: `scripts/restore.ps1`
- Create: `scripts/contract_test.ps1`
- Create: `scripts/release_report.ps1`
- Create: `contracts/schema.snapshot.json`
- Create: `backend/tests/test_backup_manifest.py`
- Create: `backend/tests/test_restore_invariants.py`
- Create: `frontend/tests/release.e2e.spec.ts`
- Modify: `README.md`

**Interfaces:**
- `backup.ps1 -OutputDir <path>` writes a manifest containing database dump hash, storage file hashes, active profile, migration revision, and model fingerprints.
- `restore.ps1 -InputDir <path>` restores into an isolated target, validates active-version/index consistency, and exits non-zero on mismatch.
- `release_report.ps1` combines migration, contract, backend, frontend, evaluation, restore, and P0/P1 results without inventing missing evidence.

- [ ] **Step 1: Write failing backup/restore and release-report tests**

Assert a backup includes database metadata and source/asset hashes, restore rejects a mismatched manifest, and release reports show `NOT RUN`/`BLOCKED` for missing external capabilities instead of PASS.

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `& $env:PYTHON_EXE -m pytest backend/tests/test_backup_manifest.py backend/tests/test_restore_invariants.py -q`.

- [ ] **Step 3: Implement Compose, backup, restore, contract and release scripts**

Compose starts frontend, API, worker, and PostgreSQL/pgvector on local networks with persistent volumes. It must not expose PostgreSQL publicly. Backups cover database, source storage, derived assets, and profile/config metadata. Restore reconstructs indexes when necessary but labels rebuilt indexes as rebuilt.

- [ ] **Step 4: Run all available milestone commands**

Run the exact implemented commands in this order:

```powershell
& .\scripts\verify-m0.ps1
& .\scripts\verify-m1.ps1
& .\scripts\verify-m2.ps1
& .\scripts\eval.ps1
& .\scripts\verify-m3.ps1
& .\scripts\verify-m4.ps1
& .\scripts\contract_test.ps1
& .\scripts\verify-release.ps1
```

Also run `make verify-m0`, `make verify-m1`, `make verify-m2`, `make eval`, `make verify-m3`, `make verify-m4`, and `make verify-release` only if a real `make` executable is present. Otherwise record each as NOT RUN because `make` is absent.

### Task 11: Requirement audit and delivery handoff

**Files:**
- Modify: `.planning/2026-09-26-personal-rag-v1/task_plan.md`
- Modify: `.planning/2026-09-26-personal-rag-v1/progress.md`
- Modify: `.planning/2026-09-26-personal-rag-v1/findings.md`
- Modify: `progress.md`
- Create: `var/reports/v1-release-report.json`
- Create: `var/reports/v1-release-report.md`

- [ ] **Step 1: Re-read the spec and plan**

Build a checklist mapping every goal, non-goal, named command, schema/API/SSE contract, invariant, and release gate to a file and fresh command output. Treat absent or indirect evidence as incomplete.

- [ ] **Step 2: Inspect all generated reports and current files**

Confirm no V2 route/table/module is registered, no absolute path or secret is returned, and no report claims PASS without exit code and output path.

- [ ] **Step 3: Run the complete release verification fresh**

Run: `& .\scripts\verify-release.ps1 -Fresh` and the backend/frontend complete suites. Read the full output and record exit codes, actual test counts, and blocked capabilities.

- [ ] **Step 4: Update project reports and stop only at the evidence-backed state**

Mark each phase PASS, FAIL, NOT RUN, NOT IMPLEMENTED, or BLOCKED. Do not mark V1.0 accepted or create a tag; the project owner must confirm milestone acceptance and tagging. If any P0/P1 remains, keep the goal active and report the specific blocker and recovery path.

## Plan self-review

- Spec coverage: Tasks 1–2 cover M0 and core policies; Tasks 3–4 cover ingestion and M1 RAG; Tasks 5–7 cover API/SSE, M2, and M3; Task 8 covers M4; Task 9 covers the UI; Task 10 covers M4.5 operations; Task 11 performs requirement-level audit.
- Placeholder scan: the plan contains no unresolved placeholder markers or unowned implementation requirement. Actual provider/model/OCR values are discovered by M0 probes and written as evidence rather than guessed.
- Interface consistency: `Scope`, `NormalizedDocument`, `HybridRetriever`, `RAGOrchestrator`, `CitationService`, `KnowledgeToolGateway`, `AgentRuntime`, `ApiClient`, and `SseClient` signatures are reused consistently across tasks.
- Review focus coverage: duplicate/version safety is covered by Tasks 3 and 4; cross-KB/version scope by Tasks 2, 4, 7, and 8; citation immutability by Task 4; cloud denial by Tasks 4 and 6; capability failures by Tasks 1, 3, 4, 6, and 7.
- Environment limitation: this root is not a Git repository, `make` is not currently available, and LibreOffice is unavailable for DOCX rendering; these are reported as facts and are not converted into false PASS results.
