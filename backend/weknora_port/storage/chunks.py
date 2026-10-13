"""Prepare source-backed parent/child rows exactly before persistence.

Semantic port: knowledge_process.go:512-604, types/chunk.go:197-210,
knowledge_index_content.go, Tencent/WeKnora 3e8b0bfc (MIT).
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from .indexing import IndexInfo


class ParsedChunk(Protocol):
    owner_id: str
    knowledge_base_id: str
    document_id: str
    source_version: str
    source_id: str
    role: str
    content: str
    seq: int
    start: int
    end: int
    parent_source_id: str | None
    context_header: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class DocumentScope:
    tenant_id: int
    owner_id: str
    knowledge_base_id: str
    knowledge_id: str
    source_version: str


@dataclass
class StoredChunk:
    id: str
    scope: DocumentScope
    content: str
    chunk_index: int
    start_at: int
    end_at: int
    chunk_type: str
    source_content: str
    context_header: str = ""
    parent_chunk_id: str = ""
    pre_chunk_id: str = ""
    next_chunk_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def embedding_content(self) -> str:
        body = self.content.strip()
        return self.context_header + "\n\n" + body if self.context_header else body


@dataclass(frozen=True)
class ChunkPlan:
    persisted: tuple[StoredChunk, ...]
    indexed: tuple[IndexInfo, ...]


def prepare_chunks(scope: DocumentScope, parsed: Sequence[ParsedChunk], *,
                   title: str = "", new_id: Callable[[], str] = lambda: str(uuid4())) -> ChunkPlan:
    if not scope.owner_id or scope.tenant_id <= 0 or not scope.source_version:
        raise PermissionError("DOCUMENT_SOURCE_IDENTITY_REQUIRED")
    for row in parsed:
        if (row.owner_id, row.knowledge_base_id, row.document_id, row.source_version) != (
                scope.owner_id, scope.knowledge_base_id, scope.knowledge_id, scope.source_version):
            raise PermissionError("DOCUMENT_SOURCE_SCOPE_MISMATCH")
    if len({row.source_id for row in parsed}) != len(parsed):
        raise ValueError("DUPLICATE_PARSED_CHUNK_IDENTITY")
    parents = [row for row in parsed if row.role == "parent"]
    children = [row for row in parsed if row.role == "child" and row.content.strip()]
    if len(parents) + sum(row.role == "child" for row in parsed) != len(parsed):
        raise ValueError("UNSUPPORTED_PARSED_CHUNK_ROLE")
    ids = {row.source_id:new_id() for row in [*parents,*children]}
    parent_ids = {row.source_id:ids[row.source_id] for row in parents}
    output: list[StoredChunk] = []
    for rows, kind in ((parents,"parent_text"),(children,"text")):
        current: list[StoredChunk] = []
        for row in rows:
            parent_id = parent_ids.get(row.parent_source_id or "", "") if kind == "text" else ""
            current.append(StoredChunk(ids[row.source_id],scope,row.content,row.seq,row.start,row.end,
                kind,row.content,row.context_header,parent_id,metadata={**row.metadata,
                "source_version":scope.source_version,"parsed_source_id":row.source_id}))
        # Parent rows always link sequentially; children only do so outside
        # parent-child mode, including otherwise-standalone children in that mode.
        if kind == "text":
            current.sort(key=lambda row: row.chunk_index)
        if kind == "parent_text" or not parents:
            for index, chunk in enumerate(current):
                chunk.pre_chunk_id = current[index-1].id if index else ""
                chunk.next_chunk_id = current[index+1].id if index+1 < len(current) else ""
        output.extend(current)
    output.sort(key=lambda row: row.chunk_index)
    title = title.strip()
    indexes = []
    for row in output:
        if row.chunk_type != "text":
            continue
        content = row.embedding_content()
        if title:
            content = title + "\n" + content
        indexes.append(IndexInfo(row.id,0,row.id,scope.knowledge_id,scope.knowledge_base_id,content))
    return ChunkPlan(tuple(output),tuple(indexes))
