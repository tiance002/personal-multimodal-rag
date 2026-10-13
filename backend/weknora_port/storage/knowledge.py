"""Scoped source-chunk adapter for the fixed WeKnora merge/context stages.

Ports chunk repository ListChunksByID and knowledge metadata assembly. Only the
server principal resolver supplies tenant identity; no old repository is used.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..models.types import CallContext
from ..retrieval.types import SearchResult
from .postgres import PostgresRetriever
from .chunks import ChunkPlan


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value.copy()
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    return {}


def _go_sprint(value: Any) -> str:
    """JSON-value fmt.Sprint formatting used by fixed Knowledge metadata."""
    if value is None:
        return "<nil>"
    if isinstance(value,bool):
        return "true" if value else "false"
    if isinstance(value,float):
        if math.isnan(value): return "NaN"
        if math.isinf(value): return "+Inf" if value > 0 else "-Inf"
        number = Decimal(repr(value))
        exponent = number.adjusted()
        if value and (exponent < -4 or exponent >= 6):
            mantissa, exp = format(number.normalize(),"e").split("e")
            n = int(exp)
            return mantissa + "e" + ("+" if n >= 0 else "-") + str(abs(n)).zfill(2)
        return format(number.normalize(),"f")
    if isinstance(value,dict):
        return "map[" + " ".join(f"{key}:{_go_sprint(value[key])}" for key in sorted(value)) + "]"
    if isinstance(value,(list,tuple)):
        return "[" + " ".join(_go_sprint(item) for item in value) + "]"
    return str(value)


def custom_metadata_text(value: Any) -> str:
    values = _json_object(value)
    lines = []
    for key in sorted(values):
        if values[key] is None:
            continue
        text = _go_sprint(values[key]).strip()
        if key.strip() and text:
            lines.append(key.strip() + ": " + text)
    return "\n".join(lines)


@dataclass(frozen=True)
class FAQAnswer:
    question: str
    answers: tuple[str, ...]


class ScopedKnowledgeRepository:
    def __init__(self, retriever: PostgresRetriever,
                 resolve_tenant: Callable[[str], Awaitable[int]]):
        self.retriever, self.resolve_tenant = retriever, resolve_tenant

    async def _tenant(self, context: CallContext) -> int:
        if not context.scope.owner_id or not context.scope.knowledge_base_ids:
            raise PermissionError("TRUSTED_KNOWLEDGE_SCOPE_REQUIRED")
        tenant = await self.resolve_tenant(context.scope.owner_id)
        if type(tenant) is not int or tenant <= 0:
            raise PermissionError("TRUSTED_PRINCIPAL_UNAVAILABLE")
        return tenant

    async def get_chunks(self, ids: Sequence[str], context: CallContext) -> Mapping[str, SearchResult]:
        return await self._read_chunks(ids,context)

    async def get_chunk_children(self, parent_ids: Sequence[str], context: CallContext) -> Sequence[SearchResult]:
        return list((await self._read_chunks(parent_ids,context,children=True)).values())

    async def _read_chunks(self, ids: Sequence[str], context: CallContext, *,
                           children: bool = False, searchable: bool = False) -> Mapping[str, SearchResult]:
        self.retriever.require_verified()
        tenant = await self._tenant(context)
        if not ids:
            return {}
        scope = context.scope
        identity_column = "parent_chunk_id" if children else "id"
        where = ["c.tenant_id = %s", "k.tenant_id = %s", "kb.tenant_id = %s",
                 "c.deleted_at IS NULL", "k.deleted_at IS NULL", "kb.deleted_at IS NULL",
                 "c." + identity_column + " IN (" + ",".join(["%s"] * len(ids)) + ")",
                 "c.knowledge_base_id IN (" + ",".join(["%s"] * len(scope.knowledge_base_ids)) + ")"]
        values = [tenant,tenant,tenant,*ids,*scope.knowledge_base_ids]
        if scope.document_ids:
            where.append("c.knowledge_id IN (" + ",".join(["%s"] * len(scope.document_ids)) + ")")
            values.extend(scope.document_ids)
        if children or searchable:
            where.append("c.is_enabled = TRUE")
        if searchable:
            where += ["(c.index_status IS NULL OR c.index_status NOT IN ('processing','failed'))",
                      "c.chunk_type IN ('text','summary','table_column','table_summary','faq','image_ocr','image_caption')"]
        # Parent and neighbor source bodies are read as source rows, not text
        # copied from an index or a prior model answer. Enabled/index selection
        # remains the responsibility of its upstream stage, not this loader.
        query = ("SELECT c.*, k.source AS knowledge_source, k.title AS knowledge_title, "
                 "k.file_name AS knowledge_filename, k.description AS knowledge_description, "
                 "k.custom_metadata AS knowledge_custom_metadata, k.metadata AS knowledge_metadata FROM chunks c "
                 "JOIN knowledges k ON k.id = c.knowledge_id AND k.knowledge_base_id = c.knowledge_base_id "
                 "JOIN knowledge_bases kb ON kb.id = c.knowledge_base_id WHERE " + " AND ".join(where))
        cursor = await self.retriever.connection.execute(query, values)
        rows = await cursor.fetchall()
        result = {}
        for row in rows:
            # Defend the boundary even with an incorrectly implemented injected
            # repository/driver; out-of-scope source data is never returned.
            if (row.get(identity_column) not in ids or row["tenant_id"] != tenant
                    or row["knowledge_base_id"] not in scope.knowledge_base_ids
                    or scope.document_ids and row["knowledge_id"] not in scope.document_ids):
                raise PermissionError("RETURNED_CHUNK_SCOPE_MISMATCH")
            if (children or searchable) and not row.get("is_enabled",True):
                # Defense against a driver/test repository ignoring SQL predicates.
                continue
            if searchable and (row.get("index_status") in ("processing","failed") or row["chunk_type"] not in
                    ("text","summary","table_column","table_summary","faq","image_ocr","image_caption")):
                continue
            chunk_metadata = _json_object(row.get("metadata"))
            metadata = {key:_go_sprint(value) for key,value in _json_object(row.get("knowledge_metadata")).items()}
            result[row["id"]] = SearchResult(
                id=row["id"],content=row["content"],score=0,
                knowledge_id=row["knowledge_id"],knowledge_base_id=row["knowledge_base_id"],
                chunk_type=row["chunk_type"],chunk_index=row["chunk_index"],
                start_at=row["start_at"],end_at=row["end_at"],
                parent_chunk_id=row.get("parent_chunk_id") or "",
                pre_chunk_id=row.get("pre_chunk_id") or "",next_chunk_id=row.get("next_chunk_id") or "",
                metadata=metadata,chunk_metadata=chunk_metadata,
                knowledge_source=row.get("knowledge_source") or "",
                knowledge_title=row.get("knowledge_title") or "",
                knowledge_filename=row.get("knowledge_filename") or "",
                knowledge_description=row.get("knowledge_description") or "",
                knowledge_custom_metadata=custom_metadata_text(row.get("knowledge_custom_metadata")),
                image_info=row.get("image_info") or "",
                is_enabled=bool(row.get("is_enabled",True)),
                content_revision=row.get("content_revision") or 0)
        return result

    async def get_faq_answers(self, ids: Sequence[str], context: CallContext) -> Mapping[str, FAQAnswer]:
        rows = await self.get_chunks(ids, context)
        result = {}
        for id, row in rows.items():
            if row.chunk_type != "faq":
                continue
            metadata = row.chunk_metadata
            question, answers = metadata.get("standard_question", ""), metadata.get("answers", ())
            if isinstance(question,str) and isinstance(answers,(list,tuple)) and all(isinstance(a,str) for a in answers):
                result[id] = FAQAnswer(question,tuple(answers))
        return result

    async def create_chunks(self, plan: ChunkPlan, context: CallContext) -> None:
        self.retriever.require_verified()
        tenant = await self._tenant(context)
        rows = plan.persisted
        for row in rows:
            scope = row.scope
            if (scope.owner_id != context.scope.owner_id or scope.tenant_id != tenant
                    or scope.knowledge_base_id not in context.scope.knowledge_base_ids
                    or scope.knowledge_base_id not in self.retriever.writable_knowledge_base_ids
                    or context.scope.document_ids and scope.knowledge_id not in context.scope.document_ids):
                raise PermissionError("CHUNK_WRITE_SCOPE_MISMATCH")
        if not rows:
            return
        columns = ("id,tenant_id,knowledge_base_id,knowledge_id,content,source_content,chunk_index,"
                   "start_at,end_at,chunk_type,context_header,parent_chunk_id,pre_chunk_id,next_chunk_id,metadata,index_status")
        def clean(value: str) -> str:
            return value.encode("utf-8",errors="ignore").decode("utf-8").replace("\x00", "")
        async with self.retriever.connection.transaction():
            for start in range(0,len(rows),500):
                batch = rows[start:start+500]
                values = []
                for row in batch:
                    values.extend((row.id,tenant,row.scope.knowledge_base_id,row.scope.knowledge_id,
                        clean(row.content),clean(row.source_content),row.chunk_index,row.start_at,row.end_at,
                        row.chunk_type,clean(row.context_header),row.parent_chunk_id or None,
                        row.pre_chunk_id or None,row.next_chunk_id or None,json.dumps(row.metadata,ensure_ascii=False),"ready"))
                placeholder = "(" + ",".join(["%s"] * 14 + ["%s::json", "%s"]) + ")"
                query = f"INSERT INTO chunks ({columns}) VALUES " + ",".join([placeholder] * len(batch))
                # Source CreateChunks uses fresh UUIDs, not an upsert. A duplicate
                # identity fails and rolls back all batches, preserving old rows.
                await self.retriever.connection.execute(query,values)
