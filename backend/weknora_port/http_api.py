"""FastAPI boundary for the opt-in WeKnora Python port.

The API deliberately knows no database, model provider, environment variable,
or local-owner fallback. Applications inject a RuntimeService and a trusted
principal resolver. The runtime remains responsible for resolving requested
KB/document IDs into an effective server-side scope.
"""
from __future__ import annotations

import inspect
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit,quote

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse,Response
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt

from .streaming.events import StreamEvent, to_sse
from .models.types import ProviderError
from .documents import DocumentLifecycleError


_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
_MODEL_ROLES = ("embedding", "rerank", "cheap", "expensive", "vlm")
_SAFE_EXCEPTION_CODES = frozenset({
    "MODEL_CREDENTIAL_REBINDING_REQUIRED",
    "MODEL_ROLES_REQUIRED",
    "REQUEST_INVALID",
    "RESOURCE_NOT_FOUND",
    "SCOPE_DENIED",
    "TRUSTED_PRINCIPAL_REQUIRED",
})


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryItemCreate(ApiModel):
    kind: str = ""
    content: str = ""
    importance: StrictInt = 0


class MemoryItemUpdate(ApiModel):
    content: str = ""
    importance: StrictInt = 0


class MemorySettingsUpdate(ApiModel):
    enabled: StrictBool | None = None


class KnowledgeBaseExternalProcessing(ApiModel):
    cloud_allowed: StrictBool


class KnowledgeBaseCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)


class ImageProcessingWrite(ApiModel):
    enable_multimodal: StrictBool
    ocr_adapter: str = ''
    ocr_model_id: str = ''
    caption_mode: str = 'follow_ocr'
    caption_model_id: str = ''
    description_language: str = ''
    custom_instructions: str = ''


class VisionModelCreate(ApiModel):
    provider: str
    model: str
    base_url: str
    api_key: SecretStr = SecretStr('')
    vision_user_declared: StrictBool = False


class SessionCreate(ApiModel):
    knowledge_base_scope: list[str] = Field(min_length=1)
    document_scope: list[str] = Field(default_factory=list)
    title: str = Field(default="新对话", max_length=200)


class MessageCreate(ApiModel):
    content: str = Field(min_length=1, max_length=100_000)
    request_id: str = Field(min_length=1, max_length=128)
    mode: str = Field(default="quick")
    knowledge_base_scope: list[str] = Field(min_length=1)
    document_scope: list[str] = Field(default_factory=list)


class StopSessionRequest(ApiModel):
    message_id: str = Field(min_length=1,max_length=36)


class ModelRoleWrite(ApiModel):
    role: str
    provider: str = Field(max_length=200)
    model: str = Field(max_length=300)
    base_url: str = Field(max_length=2048)
    api_key: SecretStr | None = None


class ModelConfigurationWrite(ApiModel):
    revision: str = Field(pattern=r"^(0|[1-9][0-9]*)$", max_length=20)
    roles: list[ModelRoleWrite] = Field(min_length=5, max_length=5)


@dataclass(frozen=True)
class AcceptedTurn:
    """Shape shared with the ordinary runtime's accepted run descriptor."""

    run_id: str
    session_id: str
    user_message_id: str
    assistant_message_id: str
    context: Any = None
    is_new: bool = True
    replay_only: bool = False

    def __post_init__(self) -> None:
        if not all((self.run_id, self.session_id, self.user_message_id, self.assistant_message_id)):
            raise ValueError("TURN_IDENTITY_INVALID")

    @property
    def request_id(self) -> str:
        return self.run_id


from .service import OrdinaryTurnRequest


class RuntimeService(Protocol):
    """Application-owned facade. Implementations must use server-resolved scope.

    ``accept_turn`` validates and resolves requested KB/document IDs using the
    server principal, durably creates/reuses one user/assistant pair, appends
    one initial agent_query, and starts/attaches the runtime-owned worker. HTTP
    only reads that accepted run; request disconnect never cancels generation.
    """

    async def runtime_identity(self, principal: Any) -> Mapping[str, Any]: ...
    async def list_knowledge_bases(self, principal: Any) -> Sequence[Mapping[str, Any]]: ...
    async def create_knowledge_base(self, principal: Any, name: str) -> Mapping[str, Any]: ...
    async def update_knowledge_base_consent(self, principal: Any, knowledge_base_id: str, cloud_allowed: bool) -> Mapping[str, Any]: ...
    async def list_documents(self, principal: Any, knowledge_base_id: str) -> Sequence[Mapping[str, Any]]: ...
    async def upload_document(self, principal: Any, knowledge_base_id: str, filename: str,
                              content_type: str, source: UploadFile) -> Mapping[str, Any]: ...
    async def read_model_configuration(self, principal: Any) -> Mapping[str, Any]: ...
    async def update_model_configuration(self, principal: Any, revision: int,
                                         roles: list[dict[str, Any]]) -> Mapping[str, Any]: ...
    async def list_sessions(self, principal: Any) -> Sequence[Mapping[str, Any]]: ...
    async def create_session(self, principal: Any, request: SessionCreate) -> Mapping[str, Any]: ...
    async def list_messages(self, principal: Any, session_id: str) -> Sequence[Mapping[str, Any]]: ...
    async def accept_turn(self, request: OrdinaryTurnRequest, trusted_principal: Any) -> AcceptedTurn: ...
    async def validate_run(self, session_id: str, assistant_message_id: str, trusted_principal: Any) -> AcceptedTurn: ...
    def replay(self, accepted: AcceptedTurn, principal: Any) -> AsyncIterator[StreamEvent]: ...
    async def read_source_chunk(self, principal: Any, chunk_id: str) -> Mapping[str, Any]: ...


PrincipalResolver = Callable[[Request], Any | Awaitable[Any]]


class RuntimeContractError(RuntimeError):
    pass


def _safe_code(exc: BaseException, default: str = "REQUEST_FAILED") -> str:
    candidate = getattr(exc, "code", None)
    if isinstance(candidate, str) and _CODE.fullmatch(candidate):
        return candidate
    # Built-in exception text can contain user input or provider details. Only
    # promote a small set of known contract codes when no structured code exists.
    candidate = str(exc) if isinstance(exc, (ValueError, LookupError, PermissionError)) else ""
    return candidate if candidate in _SAFE_EXCEPTION_CODES else default


def _public_model_configuration(value: Mapping[str, Any]) -> dict[str, Any]:
    roles_value = value.get("roles")
    if not isinstance(roles_value, Sequence) or isinstance(roles_value, (str, bytes)):
        raise RuntimeContractError("MODEL_CONFIGURATION_RESPONSE_INVALID")
    by_role = {row.get("role"): row for row in roles_value if isinstance(row, Mapping)}
    if set(by_role) != set(_MODEL_ROLES):
        raise RuntimeContractError("MODEL_CONFIGURATION_RESPONSE_INVALID")
    roles = []
    for role_name in _MODEL_ROLES:
        row = by_role[role_name]
        roles.append({
            "role": role_name,
            "provider": str(row.get("provider", "")),
            "model": str(row.get("model", "")),
            "base_url": str(row.get("base_url", "")),
            "key_configured": bool(row.get("key_configured", row.get("api_key"))),
        })
    revision = value.get("revision", 0)
    if type(revision) is int and revision >= 0:
        revision = str(revision)
    if not isinstance(revision, str) or not revision.isascii() or not revision.isdecimal():
        raise RuntimeContractError("MODEL_CONFIGURATION_RESPONSE_INVALID")
    return {"revision": revision, "roles": roles}


def _error_response(code: str, status_code: int, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={
        "error": {"code": code, "message": code},
        "meta": {"request_id": request_id},
    })


def _request_id(request: Request) -> str:
    value = request.headers.get("x-request-id", "")
    return value if value and len(value) <= 128 and _CODE.fullmatch(value) else str(uuid.uuid4())


async def _resolved_principal(resolver: PrincipalResolver, request: Request) -> Any:
    principal = resolver(request)
    if inspect.isawaitable(principal):
        principal = await principal
    if principal is None:
        raise PermissionError("TRUSTED_PRINCIPAL_REQUIRED")
    return principal


async def _response_stream(runtime: RuntimeService, principal: Any,
                           accepted: AcceptedTurn) -> AsyncIterator[bytes]:
    async for event in runtime.replay(accepted, principal):
        if event.type in ("answer", "thinking", "reflection") and "event_id" not in event.data:
            event = StreamEvent(event.event_id, event.type, event.content, event.done,
                                event.timestamp, {**event.data, "event_id": event.event_id}, event.usage)
        yield to_sse(event, accepted.run_id)


def _normalize_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            return None
        port = parsed.port
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = 80 if parsed.scheme == "http" else 443
        return f"{parsed.scheme.lower()}://{host}" + (f":{port}" if port and port != default_port else "")
    except ValueError:
        return None


def create_app(runtime: RuntimeService, *, resolve_principal: PrincipalResolver,
               allowed_origins: Sequence[str] = ()) -> FastAPI:
    """Build the port API using explicit application dependencies.

    The required resolver prevents an implicit user identity, and the required
    runtime dependency prevents a pretend/in-memory production service.
    """
    if runtime is None or resolve_principal is None:
        raise TypeError("RUNTIME_AND_PRINCIPAL_RESOLVER_REQUIRED")
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def lifespan(app):
        start=getattr(runtime,'start_background_workers',None)
        if callable(start):await start()
        try:yield
        finally:
            stop=getattr(runtime,'stop_background_workers',None)
            if callable(stop):await stop()
    app = FastAPI(title="WeKnora Python Port", version="v1", lifespan=lifespan)
    app.state.runtime_service = runtime
    app.state.resolve_principal = resolve_principal
    normalized_allowed = frozenset(origin for raw in allowed_origins if (origin := _normalize_origin(raw)))
    app.state.allowed_origins = normalized_allowed
    if normalized_allowed:
        app.add_middleware(CORSMiddleware, allow_origins=sorted(normalized_allowed),
                           allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"],
                           allow_headers=["Accept", "Content-Type", "X-Request-ID"])

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        if request.url.path.startswith("/api/v1/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = _normalize_origin(request.headers.get("origin", ""))
            host = request.headers.get("host", "")
            same_origin = _normalize_origin(f"{request.url.scheme}://{host}") if host else None
            if origin is None or (origin != same_origin and origin not in normalized_allowed):
                return _error_response("ORIGIN_NOT_ALLOWED", 403, _request_id(request))
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Do not echo the invalid input; it may contain a configured secret.
        return _error_response("REQUEST_INVALID", 422, _request_id(request))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = exc.detail if isinstance(exc.detail, str) and _CODE.fullmatch(exc.detail) else "HTTP_REQUEST_FAILED"
        return _error_response(code, exc.status_code, _request_id(request))

    @app.exception_handler(PermissionError)
    async def permission_error(request: Request, exc: PermissionError):
        return _error_response(_safe_code(exc, "SCOPE_DENIED"), 403, _request_id(request))

    @app.exception_handler(LookupError)
    async def lookup_error(request: Request, exc: LookupError):
        return _error_response(_safe_code(exc, "RESOURCE_NOT_FOUND"), 404, _request_id(request))

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return _error_response(_safe_code(exc, "REQUEST_INVALID"), 409, _request_id(request))

    @app.exception_handler(RuntimeContractError)
    async def contract_error(request: Request, exc: RuntimeContractError):
        return _error_response(_safe_code(exc, "RUNTIME_CONTRACT_INVALID"), 502, _request_id(request))

    @app.exception_handler(ProviderError)
    async def provider_application_error(request: Request, exc: ProviderError):
        code = _safe_code(exc, "REQUEST_FAILED")
        denied = {"TRUSTED_PRINCIPAL_REQUIRED", "TURN_OWNER_MISMATCH",
                  "TURN_KB_SCOPE_DENIED", "TURN_DOCUMENT_SCOPE_DENIED",
                  "TURN_RAW_SCOPE_EXPANSION_DENIED", "KNOWLEDGE_OWNER_MISMATCH", "MEMORY_OWNER_MISMATCH"}
        conflicts = {"TURN_REQUEST_PAYLOAD_CONFLICT", "TURN_FAILURE_CONFLICT",
                     "TURN_ALREADY_FAILED"}
        status = 403 if code in denied else 409 if code in conflicts else 422
        return _error_response(code, status, _request_id(request))

    @app.exception_handler(DocumentLifecycleError)
    async def document_application_error(request: Request, exc: DocumentLifecycleError):
        code = _safe_code(exc, "DOCUMENT_REQUEST_FAILED")
        status = 404 if code.endswith("_NOT_FOUND") else 403 if code.endswith("_SCOPE_DENIED") else 422
        return _error_response(code, status, _request_id(request))

    @app.exception_handler(Exception)
    async def runtime_error(request: Request, exc: Exception):
        return _error_response("INTERNAL_ERROR", 500, _request_id(request))

    async def principal(request: Request) -> Any:
        return await _resolved_principal(resolve_principal, request)

    def json_data(request: Request, data: Any) -> JSONResponse:
        return JSONResponse(content={"data": jsonable_encoder(data), "meta": {"request_id": _request_id(request)}})

    from dataclasses import asdict
    from fastapi.encoders import jsonable_encoder
    from .conversation.management import MemoryWriteError
    from .conversation.types import MemoryScopeError

    @app.exception_handler(MemoryWriteError)
    async def memory_write_error(request: Request, exc: MemoryWriteError):
        code=_safe_code(exc,'MEMORY_WRITE_FAILED')
        return _error_response(code,404 if str(exc)=='MEMORY_ITEM_NOT_FOUND' else 422,_request_id(request))

    @app.exception_handler(MemoryScopeError)
    async def memory_scope_error(request: Request, exc: MemoryScopeError):
        return _error_response('MEMORY_SCOPE_UNAVAILABLE',403,_request_id(request))

    @app.post('/api/v1/sessions/{session_id}/stop')
    async def stop_session(session_id: str,body: StopSessionRequest,request: Request):
        return JSONResponse(await runtime.stop_session(await principal(request),session_id,body.message_id))

    @app.get('/api/v1/memory/settings')
    async def memory_settings(request: Request):
        return JSONResponse({'success':True,'data':jsonable_encoder(await runtime.memory_settings(await principal(request)))})

    @app.put('/api/v1/memory/settings')
    async def update_memory_settings(body: MemorySettingsUpdate,request: Request):
        if body.enabled is None:raise HTTPException(400,'MEMORY_ENABLED_REQUIRED')
        return JSONResponse({'success':True,'data':jsonable_encoder(await runtime.update_memory_settings(await principal(request),body.enabled))})

    @app.post('/api/v1/memory/items/{item_id}/confirm')
    async def confirm_memory(item_id: str,request: Request):
        return JSONResponse({'success':True,'data':jsonable_encoder(await runtime.confirm_memory(await principal(request),item_id))})

    @app.post('/api/v1/memory/items/{item_id}/reject')
    async def reject_memory(item_id: str,request: Request):
        await runtime.reject_memory(await principal(request),item_id)
        return JSONResponse({'success':True})

    @app.post('/api/v1/memory/items')
    async def create_memory(body: MemoryItemCreate,request: Request):
        item=await runtime.create_memory(await principal(request),body.kind,body.content,body.importance)
        return JSONResponse({'success':True,'data':jsonable_encoder(item)})

    @app.put('/api/v1/memory/items/{item_id}')
    async def update_memory(item_id: str,body: MemoryItemUpdate,request: Request):
        item=await runtime.update_memory(await principal(request),item_id,body.content,body.importance)
        return JSONResponse({'success':True,'data':jsonable_encoder(item)})

    @app.delete('/api/v1/memory/items')
    async def clear_memory(request: Request):
        removed=await runtime.clear_memory(await principal(request))
        return JSONResponse({'success':True,'removed':removed})

    @app.get('/api/v1/memory/export')
    async def export_memory(request: Request):
        who=await principal(request)
        items=[];total=0
        while True:
            page,total=await runtime.list_memory(who,'',500,len(items))
            items.extend(page)
            if len(page)<500 or len(items)>=total or len(items)>=20000:break
        return JSONResponse({'success':True,'total':total,'truncated':len(items)<total,'data':jsonable_encoder(items)},
            headers={'Content-Disposition':'attachment; filename="weknora-memories.json"'})

    @app.get('/api/v1/memory/items')
    async def memory_items(request: Request,status: str='',limit: str='50',offset: str='0'):
        if status not in ('','active','superseded','archived','pending'):
            raise HTTPException(400,'MEMORY_STATUS_INVALID')
        try:page_limit=int(limit)
        except ValueError:page_limit=50
        if page_limit<=0 or page_limit>200:page_limit=50
        try:page_offset=max(0,int(offset))
        except ValueError:page_offset=0
        items,total=await runtime.list_memory(await principal(request),status,page_limit,page_offset)
        return JSONResponse({'success':True,'data':jsonable_encoder(items),'total':total})

    @app.delete('/api/v1/memory/items/{item_id}')
    async def delete_memory(item_id: str,request: Request):
        await runtime.forget_memory(await principal(request),item_id)
        return JSONResponse({'success':True})

    @app.get('/api/v1/memory/documents')
    async def memory_documents(request: Request,limit: str='50',offset: str='0'):
        try:page_limit=int(limit)
        except ValueError:page_limit=50
        if page_limit<=0 or page_limit>200:page_limit=50
        try:page_offset=max(0,int(offset))
        except ValueError:page_offset=0
        rows,total=await runtime.list_memory_documents(await principal(request),page_limit,page_offset)
        return JSONResponse({'success':True,'data':jsonable_encoder(rows),'total':total})

    @app.delete('/api/v1/memory/documents/{affinity_id}')
    async def delete_memory_document(affinity_id: str,request: Request):
        await runtime.delete_memory_document(await principal(request),affinity_id)
        return JSONResponse({'success':True})

    @app.get('/api/v1/memory/topics')
    async def memory_topics(request: Request,limit: str='50',offset: str='0'):
        try:page_limit=int(limit)
        except ValueError:page_limit=50
        if page_limit<=0 or page_limit>200:page_limit=50
        try:page_offset=max(0,int(offset))
        except ValueError:page_offset=0
        topics,total=await runtime.list_memory_topics(await principal(request),page_limit,page_offset)
        return JSONResponse({'success':True,'data':jsonable_encoder(topics),'total':total})

    @app.post('/api/v1/memory/topics/{topic_id}/promote')
    async def promote_memory_topic(topic_id: str,request: Request):
        return JSONResponse({'success':True,'data':jsonable_encoder(await runtime.promote_memory_topic(await principal(request),topic_id))})

    @app.delete('/api/v1/memory/topics/{topic_id}')
    async def delete_memory_topic(topic_id: str,request: Request):
        await runtime.delete_memory_topic(await principal(request),topic_id)
        return JSONResponse({'success':True})

    @app.post('/api/v1/memory/consolidate')
    async def consolidate_memory(request: Request):
        return JSONResponse({'success':True,'data':jsonable_encoder(await runtime.consolidate_memory(await principal(request)))})

    @app.get("/api/v1/runtime")
    async def runtime_endpoint(request: Request):
        who = await principal(request)
        identity = await runtime.runtime_identity(who)
        if identity.get("implementation") != "weknora-python-port":
            raise RuntimeContractError("RUNTIME_IDENTITY_INVALID")
        return json_data(request, {"implementation": "weknora-python-port", "api_version": "v1"})

    @app.get("/api/v1/knowledge-bases")
    async def knowledge_bases(request: Request):
        return json_data(request, await runtime.list_knowledge_bases(await principal(request)))

    @app.post("/api/v1/knowledge-bases")
    async def create_knowledge_base(body: KnowledgeBaseCreate, request: Request):
        row = await runtime.create_knowledge_base(await principal(request), body.name.strip())
        return json_data(request, row)

    @app.patch("/api/v1/knowledge-bases/{knowledge_base_id}/external-processing")
    async def update_external_processing(knowledge_base_id: str, body: KnowledgeBaseExternalProcessing, request: Request):
        row = await runtime.update_knowledge_base_consent(await principal(request), knowledge_base_id, body.cloud_allowed)
        return json_data(request, row)

    @app.get("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
    async def documents(knowledge_base_id: str, request: Request):
        return json_data(request, await runtime.list_documents(await principal(request), knowledge_base_id))

    @app.post("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
    async def upload_document(knowledge_base_id: str, request: Request, file: UploadFile = File(...)):
        who = await principal(request)
        form=await request.form()
        raw=form.get('process_overrides')
        try:
            overrides=json.loads(raw) if isinstance(raw,str) and raw else None
        except ValueError:
            raise HTTPException(status_code=422,detail='IMAGE_PROCESS_OVERRIDES_INVALID') from None
        if overrides is not None and (not isinstance(overrides,dict) or
            set(overrides)-{'ocr_adapter','ocr_model_id','caption_mode','caption_model_id',
                            'description_language','custom_instructions'} or
            any(not isinstance(value,str) for value in overrides.values())):
            raise HTTPException(status_code=422,detail='IMAGE_PROCESS_OVERRIDES_INVALID')
        result = await runtime.upload_document(who, knowledge_base_id, file.filename or "upload",
                                               file.content_type or "application/octet-stream", file,overrides)
        return json_data(request, result)

    @app.patch('/api/v1/knowledge-bases/{knowledge_base_id}/image-processing')
    async def update_image_processing(knowledge_base_id: str,body: ImageProcessingWrite,request: Request):
        return json_data(request,await runtime.update_image_processing(await principal(request),
            knowledge_base_id,body.model_dump()))

    @app.post('/api/v1/settings/vision-models')
    async def register_vision_model(body: VisionModelCreate,request: Request):
        return json_data(request,await runtime.register_vision_model(await principal(request),
            body.provider,body.model,body.base_url,body.api_key.get_secret_value(),
            body.vision_user_declared))

    @app.get('/api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/source')
    async def original_document(knowledge_base_id: str,document_id: str,request: Request):
        source=await runtime.read_document(await principal(request),knowledge_base_id,document_id)
        return Response(source.content,media_type=source.media_type,headers={
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(source.filename,safe=''),
            'X-Content-Type-Options':'nosniff'})

    @app.get('/api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/image-task')
    async def image_task_status(knowledge_base_id: str,document_id: str,request: Request):
        return json_data(request,await runtime.image_task_status(await principal(request),
            knowledge_base_id,document_id))

    @app.post('/api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/image-task/cancel')
    async def cancel_image_task(knowledge_base_id: str,document_id: str,request: Request):
        return json_data(request,await runtime.cancel_image_task(await principal(request),
            knowledge_base_id,document_id))

    @app.get("/api/v1/settings/model-configuration")
    async def read_model_configuration(request: Request):
        value = await runtime.read_model_configuration(await principal(request))
        return json_data(request, _public_model_configuration(value))

    @app.put("/api/v1/settings/model-configuration")
    async def update_model_configuration(body: ModelConfigurationWrite, request: Request):
        role_names = [row.role for row in body.roles]
        if len(set(role_names)) != len(_MODEL_ROLES) or set(role_names) != set(_MODEL_ROLES):
            raise ValueError("MODEL_ROLES_REQUIRED")
        roles = []
        for row in body.roles:
            item = {"role": row.role, "provider": row.provider, "model": row.model, "base_url": row.base_url}
            if row.api_key is not None:
                item["api_key"] = row.api_key.get_secret_value()
            roles.append(item)
        value = await runtime.update_model_configuration(await principal(request), int(body.revision), roles)
        return json_data(request, _public_model_configuration(value))

    @app.get("/api/v1/sessions")
    async def sessions(request: Request):
        return json_data(request, await runtime.list_sessions(await principal(request)))

    @app.post("/api/v1/sessions")
    async def create_session(body: SessionCreate, request: Request):
        return json_data(request, await runtime.create_session(await principal(request), body))

    @app.get("/api/v1/sessions/{session_id}/messages")
    async def messages(session_id: str, request: Request):
        return json_data(request, await runtime.list_messages(await principal(request), session_id))

    @app.post("/api/v1/sessions/{session_id}/messages")
    async def create_message(session_id: str, body: MessageCreate, request: Request):
        if body.mode != "quick":
            raise HTTPException(status_code=501, detail="SMART_MODE_UNAVAILABLE")
        who = await principal(request)
        turn_request = OrdinaryTurnRequest(session_id, body.content, body.request_id, body.mode,
                                           tuple(body.knowledge_base_scope), tuple(body.document_scope))
        accepted = await runtime.accept_turn(turn_request, who)
        if accepted.session_id != session_id or (getattr(accepted, 'client_request_id', '') or accepted.run_id) != body.request_id:
            raise RuntimeContractError("TURN_IDENTITY_MISMATCH")
        return StreamingResponse(_response_stream(runtime, who, accepted), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/sessions/continue-stream/{session_id}")
    async def continue_stream(session_id: str, request: Request, message_id: str):
        who = await principal(request)
        accepted = await runtime.validate_run(session_id, message_id, who)
        if accepted.session_id != session_id or accepted.assistant_message_id != message_id:
            raise RuntimeContractError("TURN_IDENTITY_MISMATCH")
        return StreamingResponse(_response_stream(runtime, who, accepted), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/v1/chunks/{chunk_id}")
    async def source_chunk(chunk_id: str, request: Request):
        return json_data(request, await runtime.read_source_chunk(await principal(request), chunk_id))

    return app
