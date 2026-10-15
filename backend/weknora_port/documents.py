"""Scoped, immutable document lifecycle for the isolated WeKnora Python port.

The service owns source bytes and document status. Parsing, chunk construction,
PostgreSQL persistence, embeddings, image work, and post-processing are explicit
dependencies so an absent production adapter leaves the document pending.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from .ingestion.core import DocumentRequest, IngestResult, ParsedAsset, ParserRegistry, ingest_document
from .models.types import CallContext, TrustedScope
from .storage.chunks import DocumentScope, prepare_chunks
from .storage.history import LocalPrincipal
from .storage.indexing import Embedder, HybridIndexer, IndexInfo
from .storage.knowledge import ScopedKnowledgeRepository
from .storage.postgres import PostgresRetriever


_SAFE_FILENAME = re.compile(r"[^\w.()\- ]", re.UNICODE)
_SUPPORTED_MEDIA = {
    ".txt": "text/plain", ".text": "text/plain", ".md": "text/markdown",
    ".markdown": "text/markdown", ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint", ".csv": "text/csv", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel", ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff", ".webp": "image/webp",
}
_IMAGE_BEARING_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".tif", ".tiff", ".webp", ".pdf", ".docx", ".doc", ".pptx", ".ppt"})
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")


class DocumentLifecycleError(RuntimeError):
    def __init__(self, code: str):
        self.code = code if _SAFE_CODE.fullmatch(code) else "DOCUMENT_LIFECYCLE_FAILED"
        super().__init__(self.code)


@dataclass(frozen=True)
class KnowledgeBaseSettings:
    """Resolved, server-side settings. IDs come from configured model bindings."""

    embedding_model_id: str
    summary_model_id: str
    rerank_model_id: str
    chunking_config: Mapping[str, Any]
    indexing_strategy: Mapping[str, Any]
    image_processing_config: Mapping[str, Any]
    description: str = ""


@dataclass(frozen=True)
class DocumentBytes:
    filename: str
    media_type: str
    content: bytes


class ConnectionFactory(Protocol):
    def lease(self, *, writable_knowledge_base_ids=frozenset()): ...


SettingsResolver = Callable[[LocalPrincipal], KnowledgeBaseSettings | Awaitable[KnowledgeBaseSettings]]
EmbedderResolver = Callable[[LocalPrincipal, Mapping[str, Any]], Embedder | Awaitable[Embedder]]
AssetProcessor = Callable[[LocalPrincipal, Mapping[str, Any], DocumentScope,
                            Sequence[ParsedAsset]], Sequence[Any] | Awaitable[Sequence[Any]]]
PostProcessor = Callable[[LocalPrincipal, Mapping[str, Any], Mapping[str, Any], IngestResult,
                           Sequence[Any]], None | Awaitable[None]]


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _filename(value: str) -> str:
    leaf = PurePosixPath(value.replace("\\", "/")).name
    leaf = _SAFE_FILENAME.sub("_", leaf).strip(" .")[:240]
    if leaf in ("", ".", ".."):
        raise DocumentLifecycleError("DOCUMENT_FILENAME_INVALID")
    return leaf


def _safe_error_code(exc: BaseException, fallback: str) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _SAFE_CODE.fullmatch(code):
        return code
    # Do not persist parser/provider exception text: it can contain document or
    # credential data. The type is sufficient diagnostic metadata for this API.
    return fallback


def _under_root(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise DocumentLifecycleError("DOCUMENT_STORAGE_PATH_INVALID")
    return resolved


def _native_io_path(path: Path) -> Path:
    """Windows extended-length IO after ordinary canonical scope validation.

    Does not change ACLs, redirect paths, or change stored logical references.
    """
    if os.name!='nt':return path
    value=str(path.absolute())
    if value.startswith('\\\\?\\'):return path
    if value.startswith('\\\\'):return Path('\\\\?\\UNC\\'+value[2:])
    return Path('\\\\?\\'+value)


class DocumentLifecycleService:
    """Upload, parse, index, and read documents inside one bound local owner.

    A caller passes the server-resolved principal on every operation. The
    constructor binding prevents a same-tenant but different subject from
    borrowing another local owner's document IDs.
    """

    def __init__(self, connection_factory: ConnectionFactory, storage_root: str | Path,
                 *, principal: LocalPrincipal, resolve_kb_settings: SettingsResolver,
                 embedder_for_kb: EmbedderResolver | None = None,
                 parser_registry: ParserRegistry | None = None,
                 asset_processor: AssetProcessor | None = None,
                 post_processor: PostProcessor | None = None,
                 max_upload_bytes: int = 50 * 1024 * 1024, start_image_worker_on_upload: bool = True):
        if not isinstance(principal, LocalPrincipal):
            raise PermissionError("LOCAL_PRINCIPAL_REQUIRED")
        if max_upload_bytes <= 0:
            raise ValueError("MAX_UPLOAD_BYTES_INVALID")
        self.connection_factory = connection_factory
        self.storage_root = Path(storage_root).expanduser().resolve()
        self.principal = principal
        self.image_tasks = None
        self.resolve_kb_settings = resolve_kb_settings
        self.embedder_for_kb = embedder_for_kb
        self.parser_registry = parser_registry or ParserRegistry()
        self.asset_processor = asset_processor
        self.post_processor = post_processor
        self.max_upload_bytes = max_upload_bytes
        self.start_image_worker_on_upload = start_image_worker_on_upload
        self._parse_lock = asyncio.Lock()

    def _authorize(self, principal: LocalPrincipal) -> None:
        if not isinstance(principal, LocalPrincipal) or principal != self.principal:
            raise PermissionError("LOCAL_PRINCIPAL_SCOPE_MISMATCH")

    async def _rows(self, sql: str, params: Sequence[Any] = (), *,
                    writable_knowledge_base_ids=frozenset()) -> list[Mapping[str, Any]]:
        async with self.connection_factory.lease(
                writable_knowledge_base_ids=writable_knowledge_base_ids) as repository:
            if not isinstance(repository, PostgresRetriever):
                # Structural fakes used by tests implement this same verified
                # contract without importing or contacting a real database.
                require_verified = getattr(repository, "require_verified", None)
                if not callable(require_verified):
                    raise PermissionError("VERIFIED_STORAGE_REQUIRED")
                require_verified()
            else:
                repository.require_verified()
            cursor = await repository.connection.execute(sql, tuple(params))
            return await cursor.fetchall()

    async def _kb(self, principal: LocalPrincipal, knowledge_base_id: str) -> Mapping[str, Any]:
        self._authorize(principal)
        if not knowledge_base_id:
            raise DocumentLifecycleError("KNOWLEDGE_BASE_ID_REQUIRED")
        rows = await self._rows(
            "SELECT * FROM knowledge_bases WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL",
            (knowledge_base_id, principal.tenant_id))
        if not rows:
            raise LookupError("KNOWLEDGE_BASE_NOT_FOUND")
        row = rows[0]
        if row.get("tenant_id") != principal.tenant_id or row.get("id") != knowledge_base_id:
            raise PermissionError("KNOWLEDGE_BASE_SCOPE_MISMATCH")
        return row

    async def list_knowledge_bases(self, principal: LocalPrincipal) -> list[dict[str, Any]]:
        self._authorize(principal)
        rows = await self._rows(
            "SELECT kb.id,kb.name,kb.cloud_allowed,(SELECT count(*) FROM knowledges k WHERE k.tenant_id=kb.tenant_id "
            "AND k.knowledge_base_id=kb.id AND k.deleted_at IS NULL) AS document_count "
            "FROM knowledge_bases kb WHERE kb.tenant_id=%s AND kb.deleted_at IS NULL ORDER BY kb.created_at,kb.id",
            (principal.tenant_id,))
        return [{"id": row["id"], "name": row["name"], "cloud_allowed": bool(row.get("cloud_allowed",False)),
                 "document_count": row["document_count"]} for row in rows]

    async def create_knowledge_base(self, principal: LocalPrincipal, name: str) -> dict[str, Any]:
        self._authorize(principal)
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 200:
            raise DocumentLifecycleError("KNOWLEDGE_BASE_NAME_INVALID")
        settings = await _resolve(self.resolve_kb_settings(principal))
        if not isinstance(settings, KnowledgeBaseSettings):
            raise DocumentLifecycleError("KNOWLEDGE_BASE_MODEL_BINDING_REQUIRED")
        strategy = dict(settings.indexing_strategy)
        if not all(isinstance(strategy.get(k), bool) for k in
                   ("vector_enabled", "keyword_enabled", "wiki_enabled", "graph_enabled")):
            raise DocumentLifecycleError("KNOWLEDGE_BASE_INDEXING_CONFIG_INVALID")
        if strategy["vector_enabled"] and not settings.embedding_model_id:
            raise DocumentLifecycleError("EMBEDDING_MODEL_CONFIGURATION_REQUIRED")
        if any(not isinstance(value, str) for value in (
                settings.embedding_model_id, settings.summary_model_id, settings.rerank_model_id)):
            raise DocumentLifecycleError("KNOWLEDGE_BASE_MODEL_BINDING_INVALID")
        kb_id = str(uuid.uuid4())
        values = (kb_id, clean_name, settings.description, principal.tenant_id,
                  json.dumps(dict(settings.chunking_config), ensure_ascii=False),
                  json.dumps(dict(settings.image_processing_config), ensure_ascii=False),
                  settings.embedding_model_id, settings.summary_model_id, settings.rerank_model_id,
                  json.dumps(strategy, ensure_ascii=False))
        rows = await self._rows(
            "INSERT INTO knowledge_bases(id,name,description,tenant_id,chunking_config,image_processing_config,"
            "embedding_model_id,summary_model_id,rerank_model_id,indexing_strategy,type) "
            "VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb,'document') RETURNING id,name",
            values)
        if len(rows) != 1 or rows[0]["id"] != kb_id:
            raise DocumentLifecycleError("KNOWLEDGE_BASE_CREATE_FAILED")
        return {"id": kb_id, "name": clean_name, "cloud_allowed": False, "document_count": 0}

    async def list_documents(self, principal: LocalPrincipal,
                             knowledge_base_id: str) -> list[dict[str, Any]]:
        await self._kb(principal, knowledge_base_id)
        rows = await self._rows(
            "SELECT id,knowledge_base_id,file_name,parse_status,error_message,created_at "
            "FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL "
            "ORDER BY created_at,id", (principal.tenant_id, knowledge_base_id))
        return [{"id": row["id"], "knowledge_base_id": row["knowledge_base_id"],
                 "filename": row.get("file_name") or row.get("title") or "", "status": row["parse_status"],
                 "error_code": row.get("error_message") or ""} for row in rows]

    async def upload_document(self, principal: LocalPrincipal, knowledge_base_id: str,
                              filename: str, content_type: str, source: Any,
                              process_overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
        kb = await self._kb(principal, knowledge_base_id)
        name = _filename(filename)
        extension = Path(name).suffix.lower()
        if extension not in _SUPPORTED_MEDIA:
            raise DocumentLifecycleError("DOCUMENT_TYPE_UNSUPPORTED")
        data = await source.read() if hasattr(source, "read") else bytes(source)
        if not isinstance(data, bytes) or not data:
            raise DocumentLifecycleError("DOCUMENT_CONTENT_EMPTY")
        if len(data) > self.max_upload_bytes:
            raise DocumentLifecycleError("DOCUMENT_TOO_LARGE")

        document_id = str(uuid.uuid4())
        source_version = str(uuid.uuid4())
        digest = hashlib.sha256(data).hexdigest()
        original_path = _under_root(
            self.storage_root,
            self.storage_root / str(principal.tenant_id) / knowledge_base_id / document_id /
            source_version / "original" / name)
        self._write_immutable(original_path, data)
        metadata: dict[str, Any] = {"source_version": source_version, "sha256": digest,
                                    "media_type": _SUPPORTED_MEDIA[extension], "assets": []}
        try:
            rows = await self._rows(
                "INSERT INTO knowledges(id,tenant_id,knowledge_base_id,type,title,description,source,parse_status,"
                "enable_status,embedding_model_id,file_name,file_type,file_size,file_path,file_hash,metadata,custom_metadata) "
                "VALUES(%s,%s,%s,'document',%s,'',%s,'processing','enabled',%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) "
                "RETURNING id",
                (document_id, principal.tenant_id, knowledge_base_id, Path(name).stem, name,
                 kb.get("embedding_model_id") or None, name, extension.lstrip("."), len(data),
                 str(original_path), digest, json.dumps(metadata, ensure_ascii=False),
                 json.dumps({})),
                writable_knowledge_base_ids=frozenset({knowledge_base_id}))
        except Exception:
            original_path.unlink(missing_ok=True)
            raise
        if len(rows) != 1 or rows[0]["id"] != document_id:
            original_path.unlink(missing_ok=True)
            raise DocumentLifecycleError("DOCUMENT_CREATE_FAILED")

        image_config = _json_object(kb.get("image_processing_config"))
        if (self.image_tasks is not None and image_config.get("enable_multimodal", False)
                and extension in _IMAGE_BEARING_EXTENSIONS):
            try:
                await self.image_tasks.create(principal,kb,document_id,source_version,process_overrides)
            except Exception as exc:
                return await self._mark(principal,knowledge_base_id,document_id,"failed",
                    _safe_error_code(exc,"IMAGE_TASK_CREATE_FAILED"),metadata)
            row=await self._document_row(principal,knowledge_base_id,document_id)
            if self.start_image_worker_on_upload:await self.image_tasks.start()
            return row
        return await self._process_document(principal,kb,name,data,document_id,source_version,metadata,
                                             process_overrides=process_overrides)

    async def _process_document(self,principal,kb,name,data,document_id,source_version,metadata,
                                *,process_overrides=None,image_task=None,image_queue=None):
        knowledge_base_id=kb['id']
        extension=Path(name).suffix.lower()

        try:
            request = DocumentRequest(filename=name, data=data, owner_id=principal.subject_id,
                                      knowledge_base_id=knowledge_base_id, document_id=document_id,
                                      source_version=source_version,
                                      kb_chunking_config=_json_object(kb.get("chunking_config")))
            async with self._parse_lock:
                ingest = await asyncio.to_thread(ingest_document, request, self.parser_registry)
            if ingest.parsed.failures or not ingest.parsed.content.strip():
                code = ingest.parsed.failures[0].code if ingest.parsed.failures else "DOCUMENT_CONTENT_UNREADABLE"
                return await self._mark(principal, knowledge_base_id, document_id, "failed",
                                        _safe_error_code(DocumentLifecycleError(code), "DOCUMENT_PARSE_FAILED"), metadata,image_task=image_task)

            if image_task is not None:
                await self._assert_current_image_task(principal,image_task)
            asset_manifest = self._persist_assets(principal, knowledge_base_id, document_id,
                                                  source_version, ingest.parsed.assets,
                                                  task_id=image_task.id if image_task is not None else None)
            metadata.update({"parser_engine": ingest.parser_engine,
                             "parser_metadata": dict(ingest.parsed.metadata), "assets": asset_manifest})
            self._assert_no_cloud_parser(ingest)
            doc_scope = DocumentScope(principal.tenant_id, principal.subject_id,
                                      knowledge_base_id, document_id, source_version)
            plan = prepare_chunks(doc_scope, ingest.chunks, title=Path(name).stem)

            image_config = _json_object(kb.get("image_processing_config"))
            if extension in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}:
                if not image_config.get("enable_multimodal", False):
                    return await self._mark(principal, knowledge_base_id, document_id, "failed",
                                            "MULTIMODAL_DISABLED_FOR_IMAGE", metadata,image_task=image_task)
            if ingest.parsed.assets and image_config.get("enable_multimodal", False):
                if self.asset_processor is None:
                    return await self._mark(principal, knowledge_base_id, document_id, "pending",
                                            "MULTIMODAL_ADAPTER_REQUIRED", metadata,image_task=image_task)
                process_plan=getattr(self.asset_processor,'process_plan',None)
                if callable(process_plan):
                    if image_task is None:
                        plan=await process_plan(principal,kb,doc_scope,ingest.parsed.assets,plan,
                                                override=process_overrides)
                    else:
                        plan=await process_plan(principal,kb,doc_scope,ingest.parsed.assets,plan,
                            override=process_overrides,selection_snapshot=image_task.selection,
                            on_before=lambda i,k:image_queue.before(image_task,i,k),
                            on_result=lambda i,info:image_queue.result(image_task,i,info),
                            prior=image_task.outcomes)
                else:
                    image_chunks = await _resolve(self.asset_processor(principal, kb, doc_scope, ingest.parsed.assets))
                    for chunk in image_chunks:
                        if (getattr(chunk, "owner_id", None), getattr(chunk, "knowledge_base_id", None),
                                getattr(chunk, "document_id", None), getattr(chunk, "source_version", None)) != (
                                principal.subject_id, knowledge_base_id, document_id, source_version):
                            raise PermissionError("IMAGE_CHUNK_SCOPE_MISMATCH")
                    if image_chunks:
                        plan = prepare_chunks(doc_scope, (*ingest.chunks, *image_chunks), title=Path(name).stem)
            strategy = _json_object(kb.get("indexing_strategy"))
            vector_enabled = bool(strategy.get("vector_enabled"))
            keyword_enabled = bool(strategy.get("keyword_enabled"))
            embedder = None
            if vector_enabled:
                if self.embedder_for_kb is None:
                    return await self._mark(principal, knowledge_base_id, document_id, "pending",
                                            "EMBEDDING_ADAPTER_REQUIRED", metadata,image_task=image_task)
                embedder = await _resolve(self.embedder_for_kb(principal, kb))
                if embedder is None:
                    return await self._mark(principal, knowledge_base_id, document_id, "pending",
                                            "EMBEDDING_ADAPTER_REQUIRED", metadata,image_task=image_task)

            if plan.persisted:
                if image_task is not None:
                    await self._assert_current_image_task(principal,image_task)
                async with self.connection_factory.lease(
                        writable_knowledge_base_ids=frozenset({knowledge_base_id})) as retriever:
                    repository = ScopedKnowledgeRepository(retriever,
                        resolve_tenant=lambda owner: self._resolve_tenant(owner, principal))
                    await repository.create_chunks(plan, CallContext(
                        request_id=source_version,
                        scope=TrustedScope(principal.subject_id, (knowledge_base_id,), (document_id,))))

            indexed = vector_enabled or keyword_enabled
            if indexed and plan.indexed:
                if image_task is not None:
                    await self._assert_current_image_task(principal,image_task)
                disabled_indexes = tuple(replace(row, is_enabled=False) for row in plan.indexed)
                async with self.connection_factory.lease(
                        writable_knowledge_base_ids=frozenset({knowledge_base_id})) as retriever:
                    await HybridIndexer(retriever).batch_index(
                        disabled_indexes, vector_enabled=vector_enabled, embedder=embedder)

            if self._requires_postprocessing(kb, plan) and self.post_processor is None:
                metadata["pending_stage"] = "postprocess"
                return await self._mark(principal, knowledge_base_id, document_id, "pending",
                                        "POSTPROCESSOR_ADAPTER_REQUIRED", metadata,image_task=image_task)
            if self.post_processor is not None:
                result = await _resolve(self.post_processor(principal, kb,
                    {"id": document_id, "knowledge_base_id": knowledge_base_id,
                     "source_version": source_version}, ingest, plan.persisted))
                if result is False:
                    return await self._mark(principal, knowledge_base_id, document_id, "pending",
                                            "POSTPROCESSOR_PENDING", metadata,image_task=image_task)

            metadata.update({"chunk_count": len(plan.persisted), "indexed_count": len(plan.indexed),
                             "index_enabled": indexed, "processed_at": datetime.now(timezone.utc).isoformat()})
            await self._finalize(principal, knowledge_base_id, document_id, source_version,
                                 metadata, enabled=indexed and bool(plan.indexed),image_task=image_task)
            return await self._document_row(principal, knowledge_base_id, document_id)
        except asyncio.CancelledError:
            if image_task is None:
                await asyncio.shield(self._mark(principal, knowledge_base_id, document_id, "cancelled",
                                                "DOCUMENT_PROCESS_CANCELLED", metadata,image_task=image_task))
            raise
        except Exception as exc:
            if image_task is not None:raise
            code = _safe_error_code(exc, "DOCUMENT_PROCESSING_FAILED")
            return await self._mark(principal, knowledge_base_id, document_id, "failed", code, metadata,image_task=image_task)

    async def _assert_current_image_task(self,principal,task):
        if principal!=self.principal or (task.tenant_id,task.subject_id)!=(principal.tenant_id,principal.subject_id):
            raise DocumentLifecycleError('IMAGE_TASK_OWNER_MISMATCH')
        rows=await self._rows(
            "SELECT k.metadata,k.parse_status,t.claim_token,t.state FROM knowledges k "
            "JOIN port_image_tasks t ON t.knowledge_id=k.id AND t.tenant_id=k.tenant_id "
            "AND t.knowledge_base_id=k.knowledge_base_id WHERE k.id=%s AND k.tenant_id=%s "
            "AND k.knowledge_base_id=%s AND k.deleted_at IS NULL AND t.id=%s",
            (task.knowledge_id,task.tenant_id,task.knowledge_base_id,task.id))
        if (len(rows)!=1 or rows[0]['metadata'].get('source_version')!=task.source_version
                or rows[0]['parse_status'] not in ('pending','processing')
                or rows[0]['state']!='processing' or rows[0]['claim_token']!=task.claim_token):
            raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')

    async def _lock_current_image_document(self,guard,principal,task,*,states=('pending','processing')):
        """Caller owns transaction; every image transition locks task then document."""
        self._authorize(principal);guard.require_verified()
        if (task.tenant_id,task.subject_id)!=(principal.tenant_id,principal.subject_id):
            raise DocumentLifecycleError('IMAGE_TASK_OWNER_MISMATCH')
        claims=await (await guard.connection.execute(
            'SELECT state,claim_token,source_version FROM port_image_tasks WHERE id=%s '
            'AND tenant_id=%s AND subject_id=%s AND knowledge_base_id=%s AND knowledge_id=%s FOR UPDATE',
            (task.id,task.tenant_id,task.subject_id,task.knowledge_base_id,task.knowledge_id))).fetchall()
        current=await (await guard.connection.execute(
            'SELECT metadata,parse_status FROM knowledges WHERE id=%s AND tenant_id=%s '
            'AND knowledge_base_id=%s AND deleted_at IS NULL FOR UPDATE',
            (task.knowledge_id,task.tenant_id,task.knowledge_base_id))).fetchall()
        if (len(current)!=1 or current[0]['metadata'].get('source_version')!=task.source_version
                or current[0]['parse_status'] not in states):
            raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
        if (len(claims)!=1 or claims[0]['source_version']!=task.source_version
                or claims[0]['state']!='processing' or claims[0]['claim_token']!=task.claim_token):
            raise DocumentLifecycleError('IMAGE_TASK_CLAIM_LOST')
        return dict(current[0]['metadata'])

    async def _prepare_image_task(self,principal,task):
        # Validate inside the same transaction that clears the uncommitted rows.
        async with self.connection_factory.lease(
                writable_knowledge_base_ids=frozenset({task.knowledge_base_id})) as guard:
            async with guard.connection.transaction():
                metadata=await self._lock_current_image_document(guard,principal,task)
                await guard.connection.execute(
                    "UPDATE knowledges SET parse_status='processing',error_message='',updated_at=now() "
                    "WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL "
                    "AND metadata->>'source_version'=%s AND parse_status IN ('pending','processing')",
                    (task.knowledge_id,task.tenant_id,task.knowledge_base_id,task.source_version))
                await guard.connection.execute(
                    "DELETE FROM embeddings e USING chunks c WHERE e.chunk_id=c.id AND "
                    "c.tenant_id=%s AND c.knowledge_base_id=%s AND c.knowledge_id=%s "
                    "AND c.metadata->>'source_version'=%s",
                    (task.tenant_id,task.knowledge_base_id,task.knowledge_id,task.source_version))
                await guard.connection.execute(
                    "DELETE FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s AND knowledge_id=%s "
                    "AND metadata->>'source_version'=%s",
                    (task.tenant_id,task.knowledge_base_id,task.knowledge_id,task.source_version))
        return metadata

    async def process_image_task(self,principal,task,queue):
        await self._assert_current_image_task(principal,task)
        kb=await self._kb(principal,task.knowledge_base_id)
        kb={**kb,'image_processing_config':dict(task.config)}
        source=await self.read_document(principal,task.knowledge_base_id,task.knowledge_id)
        metadata=await self._prepare_image_task(principal,task)
        return await self._process_document(principal,kb,source.filename,source.content,
            task.knowledge_id,task.source_version,metadata,
            process_overrides=task.config,image_task=task,image_queue=queue)

    async def fail_image_task(self,principal,task,code):
        try:
            await self._mark(principal,task.knowledge_base_id,task.knowledge_id,'failed',code,
                             {'source_version':task.source_version},image_task=task)
        except DocumentLifecycleError as exc:
            if exc.code not in ('IMAGE_SOURCE_VERSION_CHANGED','IMAGE_TASK_CLAIM_LOST'):raise
            # A replacement claim, cancelled/new version or committed winner owns it.
            return False
        return True

    @staticmethod
    def _assert_no_cloud_parser(ingest: IngestResult) -> None:
        # Parser callbacks are local by construction; a provider-backed parser
        # must be supplied as an explicit, separately gated application adapter.
        if ingest.parser_engine in {"cloud", "remote", "model"}:
            raise DocumentLifecycleError("CLOUD_PARSER_NOT_ALLOWED")

    @staticmethod
    def _requires_postprocessing(kb: Mapping[str, Any], plan: Any) -> bool:
        # Fixed upstream ResolveProcessConfig defaults summary generation on.
        # Do not silently skip it because this Python app has not injected its
        # post-processing implementation yet.
        return any(row.chunk_type == "text" for row in plan.persisted)

    @staticmethod
    async def _resolve_tenant(owner: str, principal: LocalPrincipal) -> int:
        if owner != principal.subject_id:
            raise PermissionError("DOCUMENT_OWNER_SCOPE_MISMATCH")
        return principal.tenant_id

    def _write_immutable(self, path: Path, data: bytes) -> None:
        path = _native_io_path(_under_root(self.storage_root, path))
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=True) as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _persist_assets(self, principal: LocalPrincipal, knowledge_base_id: str,
                        document_id: str, source_version: str,
                        assets: Sequence[ParsedAsset], *, task_id: str | None = None) -> list[dict[str, Any]]:
        manifest = []
        for index, asset in enumerate(assets):
            original_name = PurePosixPath(asset.path.replace("\\", "/")).name
            name = _filename(original_name or f"asset-{index}")
            relative = Path(str(principal.tenant_id), knowledge_base_id, document_id,
                            source_version, "assets", f"{index:04d}-{task_id or uuid.uuid4()}-{name}")
            path = _under_root(self.storage_root, self.storage_root / relative)
            if task_id and _native_io_path(path).exists():
                if hashlib.sha256(_native_io_path(path).read_bytes()).digest()!=hashlib.sha256(asset.data).digest():
                    raise DocumentLifecycleError('IMAGE_ASSET_REPLAY_MISMATCH')
            else:
                self._write_immutable(path, asset.data)
            manifest.append({"path": str(relative), "logical_path":asset.path,
                             "media_type": asset.media_type,
                             "source_type": asset.source_type, "page_number": asset.page_number,
                             "size": len(asset.data), "sha256": hashlib.sha256(asset.data).hexdigest(),
                             "metadata": dict(asset.metadata)})
        return manifest

    async def _mark(self, principal: LocalPrincipal, knowledge_base_id: str, document_id: str,
                    status: str, code: str, metadata: Mapping[str, Any], *,image_task=None) -> dict[str, Any]:
        self._authorize(principal)
        if image_task is not None:
            if (knowledge_base_id,document_id)!=(image_task.knowledge_base_id,image_task.knowledge_id):
                raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
            async with self.connection_factory.lease(
                    writable_knowledge_base_ids=frozenset({knowledge_base_id})) as guard:
                async with guard.connection.transaction():
                    current=await self._lock_current_image_document(guard,principal,image_task)
                    current.update(dict(metadata))
                    if current.get('source_version')!=image_task.source_version:
                        raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
                    rows=await (await guard.connection.execute(
                        "UPDATE knowledges SET parse_status=%s,error_message=%s,metadata=%s::jsonb,updated_at=now() "
                        "WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL "
                        "AND metadata->>'source_version'=%s AND parse_status IN ('pending','processing') RETURNING id",
                        (status,code,json.dumps(current,ensure_ascii=False),document_id,principal.tenant_id,
                         knowledge_base_id,image_task.source_version))).fetchall()
                    if len(rows)!=1:raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
        else:
            await self._rows(
                "UPDATE knowledges SET parse_status=%s,error_message=%s,metadata=%s::jsonb,updated_at=now() "
                "WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL "
                "AND metadata->>'source_version'=%s AND parse_status IN ('pending','processing') RETURNING id",
                (status, code, json.dumps(dict(metadata), ensure_ascii=False), document_id,
                 principal.tenant_id, knowledge_base_id,metadata.get('source_version')),
                writable_knowledge_base_ids=frozenset({knowledge_base_id}))
        return await self._document_row(principal, knowledge_base_id, document_id)

    async def _finalize(self, principal: LocalPrincipal, knowledge_base_id: str, document_id: str,
                        source_version: str, metadata: Mapping[str, Any], *, enabled: bool,image_task=None) -> None:
        self._authorize(principal)
        async with self.connection_factory.lease(
                writable_knowledge_base_ids=frozenset({knowledge_base_id})) as retriever:
            retriever.require_verified()
            async with retriever.connection.transaction():
                if image_task is not None:
                    if (knowledge_base_id,document_id,source_version)!=(image_task.knowledge_base_id,image_task.knowledge_id,image_task.source_version):
                        raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
                    await self._lock_current_image_document(retriever,principal,image_task,states=('processing',))
                if enabled:
                    await retriever.connection.execute(
                        "UPDATE embeddings SET is_enabled=TRUE FROM knowledges k "
                        "WHERE embeddings.knowledge_id=k.id AND embeddings.knowledge_base_id=k.knowledge_base_id "
                        "AND k.tenant_id=%s AND k.knowledge_base_id=%s AND k.id=%s "
                        "AND k.deleted_at IS NULL AND embeddings.is_enabled=FALSE",
                        (principal.tenant_id, knowledge_base_id, document_id))
                cursor = await retriever.connection.execute(
                    "UPDATE knowledges SET parse_status='completed',error_message='',metadata=%s::jsonb,"
                    "processed_at=now(),updated_at=now() WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s "
                    "AND parse_status='processing' AND deleted_at IS NULL RETURNING id",
                    (json.dumps(dict(metadata), ensure_ascii=False), document_id,
                     principal.tenant_id, knowledge_base_id))
                rows = await cursor.fetchall()
                if len(rows) != 1 or rows[0]["id"] != document_id:
                    raise DocumentLifecycleError("DOCUMENT_FINALIZE_STATE_MISMATCH")
                if image_task is not None:
                    await retriever.connection.execute(
                        "UPDATE port_image_tasks SET state='completed',claim_token=NULL,lease_until=NULL,"
                        "error_code='',updated_at=now() WHERE id=%s AND tenant_id=%s AND subject_id=%s "
                        "AND claim_token=%s AND state='processing'",
                        (image_task.id,principal.tenant_id,principal.subject_id,image_task.claim_token))

    async def _document_row(self, principal: LocalPrincipal, knowledge_base_id: str,
                            document_id: str) -> dict[str, Any]:
        rows = await self._rows(
            "SELECT id,knowledge_base_id,file_name,title,parse_status,error_message,created_at,processed_at "
            "FROM knowledges WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL",
            (document_id, principal.tenant_id, knowledge_base_id))
        if not rows:
            raise LookupError("DOCUMENT_NOT_FOUND")
        row = rows[0]
        if row.get("knowledge_base_id") != knowledge_base_id:
            raise PermissionError("DOCUMENT_SCOPE_MISMATCH")
        return {"id": row["id"], "knowledge_base_id": row["knowledge_base_id"],
                "filename": row.get("file_name") or row.get("title") or "",
                "status": row["parse_status"], "error_code": row.get("error_message") or ""}

    async def read_document(self, principal: LocalPrincipal, knowledge_base_id: str,
                            document_id: str) -> DocumentBytes:
        await self._kb(principal, knowledge_base_id)
        rows = await self._rows(
            "SELECT file_name,file_type,file_path,file_hash,file_size FROM knowledges WHERE id=%s AND tenant_id=%s "
            "AND knowledge_base_id=%s AND deleted_at IS NULL",
            (document_id, principal.tenant_id, knowledge_base_id))
        if not rows:
            raise LookupError("DOCUMENT_NOT_FOUND")
        row = rows[0]
        path = _native_io_path(_under_root(self.storage_root, Path(row["file_path"])))
        if not path.is_file():
            raise LookupError("DOCUMENT_SOURCE_UNAVAILABLE")
        extension = "." + (row.get("file_type") or "").lower().lstrip(".")
        if path.stat().st_size>self.max_upload_bytes:
            raise DocumentLifecycleError('DOCUMENT_SOURCE_SIZE_MISMATCH')
        raw=path.read_bytes()
        if not row.get('file_hash') or hashlib.sha256(raw).hexdigest()!=row['file_hash']:
            raise DocumentLifecycleError('DOCUMENT_SOURCE_HASH_MISMATCH')
        if row.get('file_size') is not None and len(raw)!=row['file_size']:
            raise DocumentLifecycleError('DOCUMENT_SOURCE_SIZE_MISMATCH')
        return DocumentBytes(row.get("file_name") or path.name,
                             _SUPPORTED_MEDIA.get(extension, "application/octet-stream"),raw)

    async def read_source_chunk(self, principal: LocalPrincipal, chunk_id: str) -> dict[str, Any]:
        self._authorize(principal)
        if not chunk_id:
            raise DocumentLifecycleError("CHUNK_ID_REQUIRED")
        rows = await self._rows(
            "SELECT c.id AS chunk_id,c.knowledge_id AS document_id,c.knowledge_base_id,c.content,"
            "c.source_content,c.chunk_index,c.start_at,c.end_at,k.file_name AS filename,k.parse_status "
            "FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id AND k.knowledge_base_id=c.knowledge_base_id "
            "WHERE c.id=%s AND c.tenant_id=%s AND k.tenant_id=%s AND c.deleted_at IS NULL AND k.deleted_at IS NULL "
            "AND (c.chunk_type NOT IN ('image_ocr','image_caption') OR "
            "(k.parse_status='completed' AND c.metadata->>'source_version'=k.metadata->>'source_version'))",
            (chunk_id, principal.tenant_id, principal.tenant_id))
        if not rows:
            raise LookupError("SOURCE_CHUNK_NOT_FOUND")
        row = rows[0]
        if row.get("parse_status") not in ("completed", "processing", "pending"):
            raise LookupError("SOURCE_CHUNK_UNAVAILABLE")
        return {"chunk_id": row["chunk_id"], "document_id": row["document_id"],
                "knowledge_base_id": row["knowledge_base_id"], "filename": row.get("filename") or "",
                "text": row.get("source_content") or row.get("content") or "",
                "chunk_index": row["chunk_index"], "start": row["start_at"], "end": row["end_at"],
                "source_url": f"/api/v1/knowledge-bases/{row['knowledge_base_id']}/documents/{row['document_id']}/source"}
