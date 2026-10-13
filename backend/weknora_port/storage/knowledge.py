"""Scoped source-chunk adapter for the fixed WeKnora merge/context stages.

Ports chunk repository ListChunksByID and knowledge metadata assembly. Only the
server principal resolver supplies tenant identity; no old repository is used.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..models.types import CallContext
from ..retrieval.types import SearchResult
from .postgres import PostgresRetriever


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value.copy()
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    return {}


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
        self.retriever.require_verified()
        tenant = await self._tenant(context)
        if not ids:
            return {}
        scope = context.scope
        where = ["c.tenant_id = %s", "k.tenant_id = %s", "kb.tenant_id = %s",
                 "c.deleted_at IS NULL", "k.deleted_at IS NULL", "kb.deleted_at IS NULL",
                 "c.id IN (" + ",".join(["%s"] * len(ids)) + ")",
                 "c.knowledge_base_id IN (" + ",".join(["%s"] * len(scope.knowledge_base_ids)) + ")"]
        values = [tenant,tenant,tenant,*ids,*scope.knowledge_base_ids]
        if scope.document_ids:
            where.append("c.knowledge_id IN (" + ",".join(["%s"] * len(scope.document_ids)) + ")")
            values.extend(scope.document_ids)
        # Parent and neighbor source bodies are read as source rows, not text
        # copied from an index or a prior model answer. Enabled/index selection
        # remains the responsibility of its upstream stage, not this loader.
        query = ("SELECT c.*, k.source AS knowledge_source, k.title AS knowledge_title, "
                 "k.file_name AS knowledge_filename, k.description AS knowledge_description, "
                 "k.custom_metadata AS knowledge_custom_metadata FROM chunks c "
                 "JOIN knowledges k ON k.id = c.knowledge_id AND k.knowledge_base_id = c.knowledge_base_id "
                 "JOIN knowledge_bases kb ON kb.id = c.knowledge_base_id WHERE " + " AND ".join(where))
        cursor = await self.retriever.connection.execute(query, values)
        rows = await cursor.fetchall()
        result = {}
        for row in rows:
            # Defend the boundary even with an incorrectly implemented injected
            # repository/driver; out-of-scope source data is never returned.
            if (row["id"] not in ids or row["tenant_id"] != tenant
                    or row["knowledge_base_id"] not in scope.knowledge_base_ids
                    or scope.document_ids and row["knowledge_id"] not in scope.document_ids):
                raise PermissionError("RETURNED_CHUNK_SCOPE_MISMATCH")
            metadata = _json_object(row.get("metadata"))
            custom_metadata = _json_object(row.get("knowledge_custom_metadata"))
            result[row["id"]] = SearchResult(
                id=row["id"],content=row["content"],score=0,
                knowledge_id=row["knowledge_id"],knowledge_base_id=row["knowledge_base_id"],
                chunk_type=row["chunk_type"],chunk_index=row["chunk_index"],
                start_at=row["start_at"],end_at=row["end_at"],
                parent_chunk_id=row.get("parent_chunk_id") or "",
                pre_chunk_id=row.get("pre_chunk_id") or "",next_chunk_id=row.get("next_chunk_id") or "",
                metadata=metadata,chunk_metadata=metadata.copy(),
                knowledge_source=row.get("knowledge_source") or "",
                knowledge_title=row.get("knowledge_title") or "",
                knowledge_filename=row.get("knowledge_filename") or "",
                knowledge_description=row.get("knowledge_description") or "",
                knowledge_custom_metadata=json.dumps(custom_metadata,ensure_ascii=False) if custom_metadata else "",
                image_info=row.get("image_info") or "",
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
