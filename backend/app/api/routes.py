from __future__ import annotations

import json
import mimetypes
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from starlette.concurrency import run_in_threadpool

from backend.app.adapters.models.ollama import OllamaGateway
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.postgres.graph_repository import PostgresGraphRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.budget import PostgresBudgetGate
from backend.app.application.rag_orchestrator import RAGOrchestrator, RagSettings
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.scope import Scope


router = APIRouter(prefix="/api/v1")


class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    graph_enabled: bool = False
    cloud_allowed: bool = False


class KnowledgeBasePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    graph_enabled: bool | None = None
    cloud_allowed: bool | None = None


class ConversationIn(BaseModel):
    knowledge_base_scope: list[str] = Field(min_length=1)
    document_scope: list[str] = Field(default_factory=list)
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    knowledge_base_scope: list[str] | None = None
    document_scope: list[str] | None = None


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    mode: str = Field(default="quick", pattern="^(quick|smart)$")


def _meta(request: Request) -> dict[str, str]:
    return {"request_id": getattr(request.state, "request_id", str(uuid.uuid4()))}


def _ok(request: Request, data: Any, status_code: int = 200) -> JSONResponse:
    from fastapi.encoders import jsonable_encoder

    return JSONResponse({"data": jsonable_encoder(data), "meta": _meta(request)}, status_code=status_code)


def _not_found(request: Request, detail: str = "resource not found") -> JSONResponse:
    return JSONResponse({"error": {"code": "NOT_FOUND", "message": detail, "details": {}}, "meta": _meta(request)}, status_code=404)


def _error(request: Request, code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}, "meta": _meta(request)}, status_code=status_code)


def _store(request: Request) -> PostgresKnowledgeRepository:
    settings = request.app.state.settings
    current = getattr(request.app.state, "store", None)
    if current is None:
        ollama = OllamaGateway(settings.ollama_base_url, settings.ollama_chat_model, settings.ollama_embedding_model)
        current = PostgresKnowledgeRepository(
            create_engine(settings.database_url, pool_pre_ping=True),
            ContentAddressedStorage(settings.storage_root),
            embedding_provider=ollama,
        )
        request.app.state.store = current
        request.app.state.ollama = ollama
        request.app.state.budget_gate = PostgresBudgetGate(current.engine, settings.monthly_cloud_budget_microunits)
    return current


def _media_type(file: UploadFile) -> str:
    return file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"


@router.get("/knowledge-bases")
def list_knowledge_bases(request: Request):
    try:
        return _ok(request, _store(request).list_knowledge_bases())
    except Exception as exc:
        return _error(request, "DATABASE_UNAVAILABLE", type(exc).__name__, 503)


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(request: Request, payload: KnowledgeBaseIn):
    try:
        return _ok(request, _store(request).create_knowledge_base(payload.name, payload.description, graph_enabled=payload.graph_enabled, cloud_allowed=payload.cloud_allowed), 201)
    except Exception as exc:
        return _error(request, "KNOWLEDGE_BASE_CREATE_FAILED", type(exc).__name__, 400)


@router.patch("/knowledge-bases/{kb_id}")
def update_knowledge_base(kb_id: str, request: Request, payload: KnowledgeBasePatch):
    result = _store(request).update_knowledge_base(kb_id, **payload.model_dump(exclude_none=True))
    return _ok(request, result) if result else _not_found(request)


@router.delete("/knowledge-bases/{kb_id}")
def delete_knowledge_base(kb_id: str, request: Request):
    return _ok(request, {"deleted": True}) if _store(request).delete_knowledge_base(kb_id) else _not_found(request)


@router.get("/knowledge-bases/{kb_id}/documents")
def list_documents(kb_id: str, request: Request):
    if not _store(request).get_knowledge_base(kb_id):
        return _not_found(request)
    return _ok(request, _store(request).list_documents(kb_id))


def _submit_upload(kb_id: str, request: Request, file: UploadFile, duplicate_policy: str, background_tasks: BackgroundTasks):
    settings = request.app.state.settings
    if not file.filename:
        return _error(request, "INVALID_FILE_NAME", "file name is required")
    normalized = file.filename.replace("\\", "/").split("/")[-1]
    if normalized != file.filename or normalized in {"", ".", ".."}:
        return _error(request, "INVALID_FILE_NAME", "path components are not allowed")
    try:
        store = _store(request)
        stored = store.storage.put_stream(file.file, max_bytes=settings.max_upload_bytes)
        receipt = store.create_upload(kb_id, normalized, _media_type(file), stored, duplicate_policy)
        if receipt.get("job_id") and settings.inline_ingestion_enabled:
            background_tasks.add_task(store.process_job, receipt["job_id"])
        return _ok(request, receipt, 202)
    except ValueError as exc:
        return _error(request, str(exc), str(exc))
    except LookupError:
        return _not_found(request, "knowledge base not found")
    except Exception as exc:
        return _error(request, "UPLOAD_FAILED", type(exc).__name__, 400)


@router.post("/knowledge-bases/{kb_id}/documents", status_code=202)
def upload_document(kb_id: str, request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), duplicate_policy: str = "new_version"):
    return _submit_upload(kb_id, request, file, duplicate_policy, background_tasks)


@router.get("/documents/{document_id}")
def get_document(document_id: str, request: Request):
    result = _store(request).get_document(document_id)
    return _ok(request, result) if result else _not_found(request)


@router.get("/documents/{document_id}/content")
def get_document_content(document_id: str, request: Request):
    try:
        store = _store(request)
        return _ok(request, {"document_id": document_id, "content": store.get_document_content(document_id), "assets": store.list_assets(document_id)})
    except LookupError:
        return _not_found(request)


@router.get("/documents/{document_id}/assets/{asset_id}")
def get_document_asset(document_id: str, asset_id: str, request: Request):
    store = _store(request)
    asset = store.get_asset(document_id, asset_id)
    if not asset:
        return _not_found(request)
    return _ok(request, {key: value for key, value in asset.items() if key != "storage_key"})


@router.get("/documents/{document_id}/chunks")
def get_document_chunks(document_id: str, request: Request):
    if not _store(request).get_document(document_id):
        return _not_found(request)
    return _ok(request, _store(request).list_chunks(document_id))


@router.get("/documents/{document_id}/graph")
def get_document_graph(document_id: str, request: Request):
    store = _store(request)
    if not store.get_document(document_id):
        return _not_found(request)
    return _ok(request, PostgresGraphRepository(store.engine, store.storage).get_document_graph(document_id))


@router.post("/documents/{document_id}/versions", status_code=202)
def upload_document_version(document_id: str, request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), duplicate_policy: str = "new_version"):
    store = _store(request)
    document = store.get_document(document_id)
    if not document:
        return _not_found(request)
    return _submit_upload(str(document["knowledge_base_id"]), request, file, duplicate_policy, background_tasks)


@router.post("/documents/{document_id}/graph/rebuild", status_code=202)
def rebuild_graph(document_id: str, request: Request):
    store = _store(request)
    document = store.get_document(document_id)
    if not document:
        return _not_found(request)
    if not document.get("active_version_id"):
        return _error(request, "DOCUMENT_NOT_READY", "document has no active indexed version", 409)
    graph_repository = PostgresGraphRepository(store.engine, store.storage)
    from backend.app.application.graph import GraphService
    result = GraphService(graph_repository).build(document_id, str(document["active_version_id"]))
    return _ok(request, {"status": result.status, "error_code": result.error_code}, 202 if result.status == "ready" else 409)


@router.get("/ingestion-jobs/{job_id}")
def get_ingestion_job(job_id: str, request: Request):
    result = _store(request).get_job(job_id)
    return _ok(request, result) if result else _not_found(request)


@router.post("/ingestion-jobs/{job_id}/retry")
def retry_ingestion_job(job_id: str, request: Request, background_tasks: BackgroundTasks):
    store = _store(request)
    result = store.retry_job(job_id)
    if not result:
        return _not_found(request)
    if result.get("status") == "queued":
        background_tasks.add_task(store.process_job, job_id)
    return _ok(request, result, 202)


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, payload: ConversationIn):
    store = _store(request)
    if any(store.get_knowledge_base(kb_id) is None for kb_id in payload.knowledge_base_scope):
        return _not_found(request, "knowledge base scope contains an unknown id")
    return _ok(request, store.create_conversation(payload.knowledge_base_scope, payload.document_scope, payload.title), 201)


@router.get("/conversations")
def list_conversations(request: Request):
    return _ok(request, _store(request).list_conversations())


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str, request: Request):
    if not _store(request).get_conversation(conversation_id):
        return _not_found(request)
    return _ok(request, _store(request).list_messages(conversation_id))


@router.patch("/conversations/{conversation_id}")
def update_conversation(conversation_id: str, request: Request, payload: ConversationPatch):
    result = _store(request).update_conversation(conversation_id, **payload.model_dump(exclude_none=True))
    return _ok(request, result) if result else _not_found(request)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request):
    return _ok(request, {"deleted": True}) if _store(request).delete_conversation(conversation_id) else _not_found(request)


def _answer_message(store: PostgresKnowledgeRepository, conversation: dict[str, Any], content: str, request: Request, mode: str = "quick") -> dict[str, Any]:
    kb_scope = list(conversation["knowledge_base_scope"])
    document_scope = list(conversation["document_scope"] or [])
    run_id = store.create_run(conversation["id"], kb_scope, document_scope, content)
    store.append_event(run_id, "run.created", {"run_id": run_id})
    store.append_message(conversation["id"], "user", content)
    store.append_event(run_id, "retrieval.started", {"scope": kb_scope, "document_scope": document_scope})
    citation_service = CitationService(InMemoryCitationStore())
    cloud_map = {kb_id: bool((store.get_knowledge_base(kb_id) or {}).get("cloud_allowed", False)) for kb_id in kb_scope}
    ollama = getattr(request.app.state, "ollama", None)
    retriever = HybridRetriever(store, embedding_provider=ollama)
    orchestrator = RAGOrchestrator(
        retriever,
        citation_service,
        local_query_gateway=ollama,
        answer_gateway=ollama,
        cloud_allowed_by_kb=cloud_map,
        budget_gate=getattr(request.app.state, "budget_gate", None),
    )
    scope = Scope.from_ids(kb_scope, document_scope)
    if mode == "smart":
        from backend.app.adapters.postgres.agent_repository import PostgresAgentRepository
        from backend.app.application.agent_runtime import AgentRuntime
        from backend.app.application.knowledge_tools import KnowledgeToolGateway

        graph_repository = PostgresGraphRepository(store.engine, store.storage)
        gateway = KnowledgeToolGateway(
            retriever=retriever,
            content_reader=store.get_document_content,
            document_lister=store.list_documents,
            graph_query=graph_repository.query_graph,
        )
        store.append_event(run_id, "tool.started", {"tool": "search_knowledge"})
        agent_result = AgentRuntime(orchestrator, gateway, PostgresAgentRepository(store.engine)).run(
            str(conversation["id"]), content, scope, run_id=run_id
        )
        store.append_event(run_id, "tool.completed" if agent_result.error_code is None else "tool.failed", {"tool": "search_knowledge", "status": agent_result.status})
        answer = agent_result.answer
        citations = tuple(agent_result.citations)
        error_code = agent_result.error_code
        trace = {"mode": "smart", "steps": [step.__dict__ for step in agent_result.steps], "cost_microunits": agent_result.cost_microunits}
    else:
        result = orchestrator.answer_query(content, scope, RagSettings(local_query_enabled=request.app.state.settings.local_query_enabled), run_id=run_id)
        answer = result.answer
        citations = result.citations
        error_code = result.error_code
        trace = result.trace.__dict__
    store.append_event(run_id, "retrieval.completed", {"count": len(citations), "error_code": error_code, "mode": mode})
    if citation_service.snapshots:
        store.persist_evidence(run_id, list(citation_service.snapshots.values()))
        store.append_event(run_id, "evidence.frozen", {"labels": [snapshot.label for snapshot in citation_service.snapshots.values()]})
    status = "completed" if error_code is None else "failed"
    store.append_event(run_id, "answer.completed" if error_code is None else "run.failed", {"error_code": error_code, "citations": list(citations)})
    store.complete_run(run_id, status, error_code)
    if error_code is None:
        store.append_message(conversation["id"], "assistant", answer)
    return {"run_id": run_id, "answer": answer, "citations": list(citations), "error_code": error_code, "trace": trace}


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: str, request: Request, payload: MessageIn):
    store = _store(request)
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return _not_found(request)
    try:
        return _ok(request, _answer_message(store, conversation, payload.content, request, payload.mode), 201)
    except Exception as exc:
        return _error(request, "RAG_RUN_FAILED", type(exc).__name__, 500)


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, request: Request, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    store = _store(request)
    after = int(last_event_id or 0)
    events = store.list_events(run_id, after)
    if not events:
        return _not_found(request, "run or events not found")

    def stream():
        for event in events:
            yield f"id: {event['seq']}\nevent: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request):
    return _ok(request, {"cancelled": True}) if _store(request).cancel_run(run_id) else _not_found(request)


@router.get("/runs/{run_id}/citations/{citation_id}")
def get_citation(run_id: str, citation_id: str, request: Request):
    try:
        result = _store(request).get_citation(run_id, citation_id)
    except ValueError:
        return _error(request, "EVIDENCE_INTEGRITY_ERROR", "citation evidence failed hash/readability validation", 409)
    return _ok(request, result) if result else _not_found(request)


@router.get("/settings")
def get_settings(request: Request):
    settings = request.app.state.settings
    return _ok(request, {"cloud_enabled": settings.cloud_enabled, "local_query_enabled": settings.local_query_enabled, "monthly_cloud_budget_microunits": settings.monthly_cloud_budget_microunits, "host": settings.host, "port": settings.port})


@router.get("/settings/models")
def get_models(request: Request):
    settings = request.app.state.settings
    return _ok(request, {"chat": settings.ollama_chat_model, "embedding": settings.ollama_embedding_model, "provider": "ollama"})


@router.patch("/settings")
def patch_settings(request: Request, payload: dict[str, Any]):
    allowed = {"local_query_enabled", "monthly_cloud_budget_microunits"}
    unknown = set(payload) - allowed
    if unknown:
        return _error(request, "INVALID_SETTING", "only local_query_enabled can be changed at runtime", 400, {"unknown": sorted(unknown)})
    if "local_query_enabled" in payload:
        payload["local_query_enabled"] = bool(payload["local_query_enabled"])
    if "monthly_cloud_budget_microunits" in payload:
        try:
            payload["monthly_cloud_budget_microunits"] = int(payload["monthly_cloud_budget_microunits"])
        except (TypeError, ValueError):
            return _error(request, "INVALID_SETTING", "monthly budget must be an integer", 400)
        if payload["monthly_cloud_budget_microunits"] < 0:
            return _error(request, "INVALID_SETTING", "monthly budget must be non-negative", 400)
    if payload:
        request.app.state.settings = request.app.state.settings.__class__(**{**request.app.state.settings.__dict__, **payload})
        if hasattr(request.app.state, "budget_gate"):
            request.app.state.budget_gate.monthly_budget_microunits = request.app.state.settings.monthly_cloud_budget_microunits
    return get_settings(request)
