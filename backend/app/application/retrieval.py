from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.app.domain.fusion import rrf_fuse
from backend.app.domain.models import RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import NormalizedQuery, normalize_query


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    knowledge_base_id: str
    document_id: str
    version_id: str
    content: str
    locator: dict[str, Any]
    is_current: bool = True
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class RetrievalItem:
    chunk: ChunkRecord
    hit: RankedHit


@dataclass(frozen=True)
class RetrievalResult:
    query_plan: NormalizedQuery
    items: list[RetrievalItem]
    sources: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()


class InMemoryRetrievalRepository:
    def __init__(self) -> None:
        self.records: dict[str, ChunkRecord] = {}

    def add(self, chunk: ChunkRecord) -> None:
        self.records[chunk.chunk_id] = chunk

    def list_active_chunks(self, scope: Scope) -> list[ChunkRecord]:
        return [
            chunk
            for chunk in self.records.values()
            if chunk.is_current and scope.contains(chunk.knowledge_base_id, chunk.document_id)
        ]

    def get(self, chunk_id: str) -> ChunkRecord | None:
        return self.records.get(chunk_id)


def _cosine(left: tuple[float, ...], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


class HybridRetriever:
    def __init__(self, repository: InMemoryRetrievalRepository, embedding_provider: Any | None = None, top_k: int = 8) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.top_k = top_k

    def retrieve(self, scope: Scope, question: str, query_plan: NormalizedQuery | None = None) -> RetrievalResult:
        plan = query_plan or normalize_query(question)
        chunks = self.repository.list_active_chunks(scope)
        keyword_hits: list[RankedHit] = []
        for chunk in chunks:
            lowered = chunk.content.lower()
            score = sum(lowered.count(term) for term in plan.terms if term)
            if plan.normalized and plan.normalized in lowered:
                score += 2
            if score > 0:
                keyword_hits.append(RankedHit(chunk_id=chunk.chunk_id, rank=1, raw_score=float(score)))
        keyword_hits.sort(key=lambda hit: (-float(hit.raw_score or 0), hit.chunk_id))
        keyword_hits = [hit.model_copy(update={"rank": index}) for index, hit in enumerate(keyword_hits[: self.top_k], start=1)]
        rankings: dict[str, list[RankedHit]] = {"keyword": keyword_hits}

        if self.embedding_provider is not None and chunks:
            try:
                embedded = self.embedding_provider.embed([question], timeout_seconds=10)
                vector = embedded.vectors[0]
                vector_hits = [
                    RankedHit(chunk_id=chunk.chunk_id, rank=1, raw_score=_cosine(chunk.embedding or (), vector))
                    for chunk in chunks
                    if chunk.embedding is not None
                ]
                vector_hits.sort(key=lambda hit: (-float(hit.raw_score or 0), hit.chunk_id))
                rankings["vector"] = [hit.model_copy(update={"rank": index}) for index, hit in enumerate(vector_hits[: self.top_k], start=1)]
            except Exception:
                pass

        fused = rrf_fuse(rankings)[: self.top_k]
        items = [RetrievalItem(self.repository.get(hit.chunk_id), hit) for hit in fused]
        items = [item for item in items if item.chunk is not None]
        sources = tuple(sorted(rankings.keys()))
        return RetrievalResult(query_plan=plan, items=items, sources=sources, reason_codes=() if items else ("NO_CANDIDATES",))
