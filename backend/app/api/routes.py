from __future__ import annotations

import json
import asyncio
import time
import mimetypes
from pathlib import Path
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from backend.app.ports.office_preview import OfficePreviewUnavailable
from backend.app.application.answer_service import AnswerService
from backend.app.application.quick_chain import QuickSettings
from backend.app.bootstrap import Container

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
    request_id: uuid.UUID | None = None
    content: str = Field(min_length=1, max_length=100_000)
    mode: str = Field(default="quick", pattern="^(quick|smart)$")
    expected_knowledge_base_scope: list[str] | None = None
    expected_document_scope: list[str] | None = None


def _container(request: Request) -> Container:
    return request.app.state.container


def _meta(request: Request) -> dict[str, str]:
    return {"request_id": getattr(request.state, "request_id", str(uuid.uuid4()))}


def _ok(request: Request, data: Any, status_code: int = 200) -> JSONResponse:
    from fastapi.encoders import jsonable_encoder

    return JSONResponse({"data": jsonable_encoder(data), "meta": _meta(request)}, status_code=status_code)


def _not_found(request: Request, detail: str = "resource not found") -> JSONResponse:
    return JSONResponse({"error": {"code": "NOT_FOUND", "message": detail, "details": {}}, "meta": _meta(request)}, status_code=404)


def _error(request: Request, code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}, "meta": _meta(request)}, status_code=status_code)


def _validate_conversation_scope(
    request: Request,
    store: Any,
    knowledge_base_scope: list[str],
    document_scope: list[str],
) -> JSONResponse | None:
    if not knowledge_base_scope:
        return _error(request, "INVALID_KNOWLEDGE_BASE_SCOPE", "at least one knowledge base is required")
    missing_kb = [kb_id for kb_id in knowledge_base_scope if not store.get_knowledge_base(kb_id)]
    if missing_kb:
        return _not_found(request, "knowledge base scope contains an unknown id")
    for document_id in document_scope:
        document = store.get_document_access(document_id)
        if not document or document.get("deleted_at") is not None:
            return _error(request, "INVALID_DOCUMENT_SCOPE", "document scope contains an unknown document", 400, {"document_id": document_id})
        document_kb_id = str(document.get("knowledge_base_id", ""))
        if document_kb_id not in {str(kb_id) for kb_id in knowledge_base_scope}:
            return _error(
                request,
                "INVALID_DOCUMENT_SCOPE",
                "document scope must belong to the selected knowledge base scope",
                400,
                {"document_id": document_id, "knowledge_base_id": document_kb_id},
            )
    return None


def _media_type(file: UploadFile) -> str:
    return file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"


def _submit_upload(
    kb_id: str | None,
    request: Request,
    file: UploadFile,
    duplicate_policy: str,
    background_tasks: BackgroundTasks,
    *,
    document_id: str | None = None,
):
    settings = request.app.state.settings
    if not file.filename:
        return _error(request, "INVALID_FILE_NAME", "file name is required")
    normalized = file.filename.replace("\\", "/").split("/")[-1]
    if normalized != file.filename or normalized in {"", ".", ".."}:
        return _error(request, "INVALID_FILE_NAME", "path components are not allowed")
    try:
        store = _container(request).store
        stored = store.storage.put_stream(file.file, max_bytes=settings.max_upload_bytes)
        if document_id is not None:
            receipt = store.create_version(document_id, normalized, _media_type(file), stored, duplicate_policy)
        else:
            if kb_id is None:
                return _error(request, "KNOWLEDGE_BASE_NOT_FOUND", "knowledge base is required")
            receipt = store.create_upload(kb_id, normalized, _media_type(file), stored, duplicate_policy)
        if receipt.get("job_id") and settings.inline_ingestion_enabled:
            background_tasks.add_task(store.process_job, receipt["job_id"])
        return _ok(request, receipt, 202)
    except ValueError as exc:
        return _error(request, str(exc), str(exc))
    except LookupError:
        return _not_found(request, "document not found" if document_id is not None else "knowledge base not found")
    except Exception as exc:
        return _error(request, "UPLOAD_FAILED", type(exc).__name__, 400)


@router.get("/knowledge-bases")
def list_knowledge_bases(request: Request):
    try:
        return _ok(request, _container(request).store.list_knowledge_bases())
    except Exception as exc:
        return _error(request, "DATABASE_UNAVAILABLE", type(exc).__name__, 503)


@router.post("/knowledge-bases", status_code=201)
def create_knowledge_base(request: Request, payload: KnowledgeBaseIn):
    try:
        created = _container(request).store.create_knowledge_base(payload.name, payload.description, graph_enabled=payload.graph_enabled, cloud_allowed=payload.cloud_allowed)
        return _ok(request, created, 201)
    except Exception as exc:
        return _error(request, "KNOWLEDGE_BASE_CREATE_FAILED", type(exc).__name__, 400)


@router.patch("/knowledge-bases/{kb_id}")
def update_knowledge_base(kb_id: str, request: Request, payload: KnowledgeBasePatch):
    result = _container(request).store.update_knowledge_base(kb_id, **payload.model_dump(exclude_none=True))
    return _ok(request, result) if result else _not_found(request)


@router.delete("/knowledge-bases/{kb_id}")
def delete_knowledge_base(kb_id: str, request: Request):
    return _ok(request, {"deleted": True}) if _container(request).store.delete_knowledge_base(kb_id) else _not_found(request)


@router.get("/knowledge-bases/{kb_id}/documents")
def list_documents(kb_id: str, request: Request):
    store = _container(request).store
    if not store.get_knowledge_base(kb_id):
        return _not_found(request)
    return _ok(request, store.list_documents(kb_id))


@router.post("/knowledge-bases/{kb_id}/documents", status_code=202)
def upload_document(kb_id: str, request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), duplicate_policy: str = "new_version"):
    return _submit_upload(kb_id, request, file, duplicate_policy, background_tasks)


@router.get("/documents/{document_id}")
def get_document(document_id: str, request: Request):
    result = _container(request).store.get_document(document_id)
    return _ok(request, result) if result else _not_found(request)


@router.get("/documents/{document_id}/content")
def get_document_content(document_id: str, request: Request):
    try:
        store = _container(request).store
        return _ok(request, {"document_id": document_id, "content": store.get_document_content(document_id), "assets": store.list_assets(document_id)})
    except LookupError:
        return _not_found(request)


@router.get("/documents/{document_id}/source", response_class=FileResponse, responses={200: {"description": "Original source bytes or a local PDF preview; media type follows the returned file.", "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}, 404: {"description": "Document source is unavailable"}})
def get_document_source(document_id: str, request: Request):
    """Stream the immutable source bytes for the selected document version.

    The normalized `/content` endpoint is intended for retrieval and text
    inspection. The library preview needs the original bytes so PDF, HTML and
    image files keep their pre-indexing appearance in the browser.
    """
    try:
        source = _container(request).store.get_document_source(document_id)
        return FileResponse(
            source["path"],
            media_type=source["media_type"],
            filename=source["file_name"],
            content_disposition_type="inline",
        )
    except (LookupError, FileNotFoundError):
        return _not_found(request, "document source not found")


@router.get("/documents/{document_id}/preview", response_class=FileResponse, responses={200: {"description": "Original source bytes or a local PDF preview; media type follows the returned file.", "content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}, 404: {"description": "Document source is unavailable"}, 503: {"description": "Local Office preview renderer is unavailable"}})
def get_document_preview(document_id: str, request: Request):
    """Return a browser-readable preview while keeping the source immutable.

    PDF/HTML/image/text files are streamed directly. Office files are rendered
    to a temporary PDF by the local LibreOffice process so Writer/Calc layout
    and embedded images remain visible in the same preview surface.
    """
    try:
        source = _container(request).store.get_document_source(document_id)
        office = _container(request).office_preview
        if office is None:
            raise OfficePreviewUnavailable("Local preview renderer is unavailable")
        if not office.is_office_document(source["file_name"], source["media_type"]):
            return FileResponse(
                source["path"],
                media_type=source["media_type"],
                filename=source["file_name"],
                content_disposition_type="inline",
            )
        preview = office.convert_office_to_pdf(
            Path(source["path"]),
            source["file_name"],
            source["media_type"],
        )
        return FileResponse(
            preview.path,
            media_type="application/pdf",
            filename=preview.file_name,
            content_disposition_type="inline",
            background=BackgroundTask(office.cleanup_office_preview, preview),
        )
    except (LookupError, FileNotFoundError):
        return _not_found(request, "document preview not found")
    except OfficePreviewUnavailable as exc:
        return _error(request, "DOCUMENT_PREVIEW_UNAVAILABLE", str(exc), 503)


@router.get("/documents/{document_id}/assets/{asset_id}")
def get_document_asset(document_id: str, asset_id: str, request: Request):
    asset = _container(request).store.get_asset(document_id, asset_id)
    if not asset:
        return _not_found(request)
    return _ok(request, {key: value for key, value in asset.items() if key != "storage_key"})


@router.get("/documents/{document_id}/chunks")
def get_document_chunks(document_id: str, request: Request):
    store = _container(request).store
    if not store.get_document(document_id):
        return _not_found(request)
    return _ok(request, store.list_chunks(document_id))


@router.get("/documents/{document_id}/graph")
def get_document_graph(document_id: str, request: Request):
    container = _container(request)
    document = container.store.get_document(document_id)
    if not document:
        return _not_found(request)
    knowledge_base_id = str(document.get("knowledge_base_id", ""))
    knowledge_base = container.store.get_knowledge_base(knowledge_base_id)
    if not knowledge_base or not knowledge_base.get("graph_enabled", False):
        return _error(request, "GRAPH_DISABLED", "graph feature is disabled for this knowledge base", 409, {"knowledge_base_id": knowledge_base_id})
    return _ok(request, container.graph.get_document_graph(document_id))


@router.post("/documents/{document_id}/versions", status_code=202)
def upload_document_version(document_id: str, request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), duplicate_policy: str = "new_version"):
    document = _container(request).store.get_document(document_id)
    if not document:
        return _not_found(request)
    return _submit_upload(None, request, file, duplicate_policy, background_tasks, document_id=document_id)


@router.post("/documents/{document_id}/graph/rebuild", status_code=202)
def rebuild_graph(document_id: str, request: Request):
    from backend.app.application.graph import GraphService

    container = _container(request)
    document = container.store.get_document(document_id)
    if not document:
        return _not_found(request)
    knowledge_base_id = str(document.get("knowledge_base_id", ""))
    knowledge_base = container.store.get_knowledge_base(knowledge_base_id)
    if not knowledge_base or not knowledge_base.get("graph_enabled", False):
        return _error(request, "GRAPH_DISABLED", "graph feature is disabled for this knowledge base", 409, {"knowledge_base_id": knowledge_base_id})
    if not document.get("active_version_id"):
        return _error(request, "DOCUMENT_NOT_READY", "document has no active indexed version", 409)
    result = GraphService(container.graph).build(document_id, str(document["active_version_id"]))
    if result.status != "ready":
        return _error(request, result.error_code or "GRAPH_BUILD_FAILED", "graph build failed", 409)
    return _ok(request, {"status": result.status, "error_code": result.error_code}, 202)


@router.get("/ingestion-jobs/{job_id}")
def get_ingestion_job(job_id: str, request: Request):
    result = _container(request).store.get_job(job_id)
    return _ok(request, result) if result else _not_found(request)


@router.post("/ingestion-jobs/{job_id}/retry")
def retry_ingestion_job(job_id: str, request: Request, background_tasks: BackgroundTasks):
    store = _container(request).store
    result = store.retry_job(job_id)
    if not result:
        return _not_found(request)
    if result.get("status") == "queued" and request.app.state.settings.inline_ingestion_enabled:
        background_tasks.add_task(store.process_job, job_id)
    return _ok(request, result, 202)


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, payload: ConversationIn):
    store = _container(request).store
    invalid_scope = _validate_conversation_scope(request, store, payload.knowledge_base_scope, payload.document_scope)
    if invalid_scope is not None:
        return invalid_scope
    return _ok(request, store.create_conversation(payload.knowledge_base_scope, payload.document_scope, payload.title), 201)


@router.get("/conversations")
def list_conversations(request: Request):
    return _ok(request, _container(request).store.list_conversations())


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str, request: Request):
    store = _container(request).store
    if not store.get_conversation(conversation_id):
        return _not_found(request)
    return _ok(request, store.list_messages(conversation_id))


@router.patch("/conversations/{conversation_id}")
def update_conversation(conversation_id: str, request: Request, payload: ConversationPatch):
    store = _container(request).store
    current = store.get_conversation(conversation_id)
    if not current:
        return _not_found(request)
    fields = payload.model_dump(exclude_none=True)
    knowledge_base_scope = fields.get("knowledge_base_scope", current["knowledge_base_scope"])
    document_scope = fields.get("document_scope", current["document_scope"])
    invalid_scope = _validate_conversation_scope(request, store, knowledge_base_scope, document_scope)
    if invalid_scope is not None:
        return invalid_scope
    result = store.update_conversation(conversation_id, **fields)
    return _ok(request, result) if result else _not_found(request)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request):
    return _ok(request, {"deleted": True}) if _container(request).store.delete_conversation(conversation_id) else _not_found(request)


def _answer_service(request: Request) -> AnswerService:
    container = _container(request)
    settings = request.app.state.settings
    return AnswerService(
        knowledge_gateway=container.knowledge_gateway,
        quick_chain=container.quick_chain,
        runs=container.store,
        lifecycle=getattr(container,'lifecycle',None),
        memory_service=getattr(container,'memory_service',None),
        graph_query=container.graph.query_graph,
        agent_trace_store=container.agent,
        content_reader=container.store.get_document_content,
        document_lister=container.store.list_documents,
        document_resolver=container.store.get_document_access,
        smart_agent=container.smart_agent,
        local_query_enabled=settings.local_query_enabled,
        local_query_gateway=container.ollama,
        follow_up_provider=container.ollama,
        follow_up_enabled=container.follow_up_enabled,
        observability=container.langfuse,
        quick_settings=QuickSettings(cloud_enabled=settings.cloud_enabled,
            prefer_cloud=settings.prefer_cloud, cloud_fallback_enabled=settings.cloud_fallback_enabled,
            cloud_provider="deepseek", cloud_model=settings.cloud_model,
            cloud_cost_estimate_microunits=settings.cloud_cost_estimate_microunits),
    )


@router.post("/conversations/{conversation_id}/messages", status_code=201)
def send_message(conversation_id: str, request: Request, payload: MessageIn):
    store = _container(request).store
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        return _not_found(request)
    if (payload.expected_knowledge_base_scope is None) != (payload.expected_document_scope is None):
        return _error(request, "INVALID_EXPECTED_SCOPE", "both expected scope fields are required together")
    if payload.expected_knowledge_base_scope is not None and (
        payload.expected_knowledge_base_scope != conversation["knowledge_base_scope"]
        or payload.expected_document_scope != conversation["document_scope"]
    ):
        return _error(request, "CONVERSATION_SCOPE_CHANGED", "conversation scope changed; refresh before asking", 409)
    try:
        outcome = _answer_service(request).answer(conversation, payload.content, payload.mode,
            request_id=str(payload.request_id) if payload.request_id is not None else None)
    except Exception as exc:
        return _error(request, "RAG_RUN_FAILED", type(exc).__name__, 500)
    if outcome.error_code in {"DUPLICATE_REQUEST", "REQUEST_ID_CONFLICT", "IDEMPOTENCY_UNAVAILABLE", "IDEMPOTENCY_REQUIRED"}:
        return _error(request, outcome.error_code, "request not regenerated; use the original run or refresh", 409)
    return _ok(
        request,
        {
            "run_id": outcome.run_id,
            "answer": outcome.answer,
            "citations": list(outcome.citations),
            "error_code": outcome.error_code,
            "trace": outcome.trace,
        },
        201,
    )


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, conversation_id: str, request: Request, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    container = _container(request)
    store, lifecycle = container.store, getattr(container,'lifecycle',None)
    try:
        uuid.UUID(run_id); uuid.UUID(conversation_id)
        after = int(last_event_id or 0)
        if after < 0 or after > 2**53-1:
            raise ValueError()
    except ValueError:
        return _error(request,'INVALID_STREAM_POSITION','invalid stream identity or position',400)
    conversation = store.get_conversation(conversation_id)
    read_run=lifecycle.repository.read_run if lifecycle else getattr(store,'read_committed_run',None)
    if conversation is None or read_run is None:
        return _not_found(request,'scoped stream unavailable')
    kbs, docs = conversation['knowledge_base_scope'], conversation['document_scope'] or []
    try:
        await asyncio.to_thread(read_run,run_id,conversation_id,kbs,docs)
    except Exception:
        return _not_found(request,'scoped stream unavailable')

    async def stream():
        from backend.app.application.run_lifecycle import read_committed_events
        position, started = after, time.monotonic()
        while time.monotonic()-started < 30 and not await request.is_disconnected():
            try:
                events, saved = await asyncio.to_thread(read_committed_events,read_run,
                    lifecycle.streams if lifecycle else None,store,run_id,conversation_id,kbs,docs,position)
            except Exception:
                return
            for event in events:
                if event['seq'] > position:
                    position = event['seq']
                    yield f"id: {position}\nevent: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
            if saved['status'] in {'completed','failed','cancelled'}:
                return
            yield ': waiting\n\n'
            await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request):
    container = _container(request)
    return _ok(request, {"cancelled": True}) if container.store.cancel_run(run_id) else _not_found(request)


@router.get("/runs/{run_id}/citations/{citation_id}")
def get_citation(run_id: str, citation_id: str, request: Request):
    try:
        result = _container(request).store.get_citation(run_id, citation_id)
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
        return _error(request, "INVALID_SETTING", "only local_query_enabled and monthly_cloud_budget_microunits can be changed at runtime", 400, {"unknown": sorted(unknown)})
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
        settings = request.app.state.settings
        request.app.state.settings = settings.__class__(**{**settings.__dict__, **payload})
        _container(request).budget_gate.monthly_budget_microunits = request.app.state.settings.monthly_cloud_budget_microunits
    return get_settings(request)


class MemoryScopeIn(BaseModel):
    model_config = {'extra':'forbid'}
    knowledge_base_scope: list[str] = Field(min_length=1,max_length=64)
    document_scope: list[str] = Field(default_factory=list,max_length=64)

class MemoryItemIn(MemoryScopeIn):
    kind: str = Field(pattern='^(profile|preference|fact|task|interest)$')
    fact_key: str = Field(min_length=1,max_length=120)
    content: str = Field(min_length=1,max_length=300)

class MemorySettingsIn(BaseModel):
    model_config = {'extra':'forbid','strict':True}
    read_enabled: bool
    write_mode: str = Field(pattern='^(explicit_only|auto)$')

def _memory(request, action, status_code=200):
    """Single local principal, bound by server composition; no user ID input.

    This is not authentication for a network/multi-user deployment. Such a
    deployment must supply a reviewed principal boundary before enabling memory.
    """
    from urllib.parse import urlparse
    from backend.app.ports.memory import MemoryDenied
    service=getattr(_container(request),'memory_service',None)
    if service is None:return _error(request,'MEMORY_PRINCIPAL_REQUIRED','local memory is not configured',403)
    if request.client is None or request.client.host not in {'127.0.0.1','::1'} or request.url.hostname not in {'127.0.0.1','localhost','::1'}:
        return _error(request,'MEMORY_LOCAL_ACCESS_REQUIRED','memory requires the local owner boundary',403)
    origin=request.headers.get('origin')
    if origin and urlparse(origin).netloc!=request.url.netloc:
        return _error(request,'MEMORY_ORIGIN_DENIED','memory requires same-origin access',403)
    try:return _ok(request,action(service),status_code=status_code)
    except MemoryDenied as exc:return _error(request,str(exc),'memory request denied',409)
    except Exception:return _error(request,'MEMORY_STORAGE_UNAVAILABLE','memory storage is unavailable',503)

def _memory_scope(request,payload):
    from backend.app.domain.scope import Scope
    error=_validate_conversation_scope(request,_container(request).store,payload.knowledge_base_scope,payload.document_scope)
    return error or Scope.from_ids(payload.knowledge_base_scope,payload.document_scope)

@router.get('/memory/settings')
def memory_settings(request: Request):
    return _memory(request,lambda s:s.repository.settings(s.principal))

@router.patch('/memory/settings')
def memory_patch_settings(request: Request,payload: MemorySettingsIn):
    return _memory(request,lambda s:s.repository.settings(s.principal,payload.model_dump()))

@router.post('/memory/list')
def memory_list(request: Request,payload: MemoryScopeIn):
    scope=_memory_scope(request,payload)
    if isinstance(scope,JSONResponse):return scope
    return _memory(request,lambda s:s.repository.list_items(s.principal,scope))

@router.post('/memory/items',status_code=201)
def memory_save(request: Request,payload: MemoryItemIn):
    scope=_memory_scope(request,payload)
    if isinstance(scope,JSONResponse):return scope
    return _memory(request,lambda s:{'id':s.repository.save(s.principal,scope,payload.kind,payload.fact_key,payload.content,source_request_id=_meta(request)['request_id'])},status_code=201)

@router.patch('/memory/items/{item_id}')
def memory_edit(item_id: uuid.UUID,request: Request,payload: MemoryItemIn):
    scope=_memory_scope(request,payload)
    if isinstance(scope,JSONResponse):return scope
    return _memory(request,lambda s:{'id':s.repository.save(s.principal,scope,payload.kind,payload.fact_key,payload.content,target=str(item_id),source_request_id=_meta(request)['request_id'])})

@router.post('/memory/items/{item_id}/{operation}')
def memory_transition(item_id: uuid.UUID,operation: str,request: Request,payload: MemoryScopeIn):
    scope=_memory_scope(request,payload)
    if isinstance(scope,JSONResponse):return scope
    from backend.app.ports.memory import MemoryDenied
    def action(s):
        if operation not in {'confirm','reject','delete'}:raise MemoryDenied('MEMORY_TRANSITION_INVALID')
        s.repository.transition(s.principal,scope,str(item_id),{'confirm':'active','reject':'rejected','delete':'deleted'}[operation])
        return {'status':operation}
    return _memory(request,action)
