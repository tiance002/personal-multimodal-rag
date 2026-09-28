from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from backend.app.domain.fusion import rrf_fuse
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import NormalizedQuery, normalize_query
from backend.app.ports.retrieval import RetrievalRepository


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
    candidate_rankings: dict[str, tuple[RankedHit, ...]] = field(default_factory=dict)
    fused_ranking: tuple[RankedHit, ...] = ()
    effective_config: dict[str, int] = field(default_factory=dict)
    latency_ms: float | None = None
    degradation_flags: tuple[str, ...] = ()
    stage_latency_ms: dict[str, float | None] = field(default_factory=dict)


def _rank_descending(hits: list[RankedHit], limit: int) -> list[RankedHit]:
    hits.sort(key=lambda hit: (-float(hit.raw_score or 0), hit.chunk_id))
    return [hit.model_copy(update={"rank": index}) for index, hit in enumerate(hits[:limit], start=1)]


def score_keyword_hits(chunks: list[ChunkRecord], query: NormalizedQuery, limit: int) -> list[RankedHit]:
    """Deterministic keyword scoring shared by in-process repositories.

    Mirrors the PostgreSQL `chunk_terms` ranking so both repositories order
    candidates the same way: summed term occurrences, plus a bonus when the
    whole normalised query appears verbatim.
    """
    hits: list[RankedHit] = []
    for chunk in chunks:
        lowered = chunk.content.lower()
        score = sum(lowered.count(term) for term in query.terms if term)
        if query.normalized and query.normalized in lowered:
            score += 2
        if score > 0:
            hits.append(RankedHit(chunk_id=chunk.chunk_id, rank=1, raw_score=float(score)))
    return _rank_descending(hits, limit)


def cosine_similarity(left: tuple[float, ...], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


class InMemoryRetrievalRepository:
    """Deterministic in-process repository used by tests and smoke scripts.

    The PostgreSQL repository resolves the same two candidate sets in SQL; this
    double keeps the identical semantics so unit tests exercise the real ranking
    contract rather than a simplified one.
    """

    def __init__(self) -> None:
        self.records: dict[str, ChunkRecord] = {}

    def add(self, chunk: ChunkRecord) -> None:
        self.records[chunk.chunk_id] = chunk

    def list_active_chunks(self, scope: Scope, limit: int | None = None) -> list[ChunkRecord]:
        chunks = [
            chunk
            for chunk in self.records.values()
            if chunk.is_current and scope.contains(chunk.knowledge_base_id, chunk.document_id)
        ]
        chunks.sort(key=lambda chunk: chunk.chunk_id)
        return chunks[:limit] if limit is not None else chunks

    def keyword_candidates(self, scope: Scope, query: NormalizedQuery, limit: int) -> list[RankedHit]:
        if not scope.knowledge_base_ids or not query.terms:
            return []
        return score_keyword_hits(self.list_active_chunks(scope), query, limit)

    def vector_candidates(self, scope: Scope, vector: Any, limit: int, *, profile_id: str | None = None) -> list[RankedHit]:
        if not scope.knowledge_base_ids:
            return []
        hits = [
            RankedHit(chunk_id=chunk.chunk_id, rank=1, raw_score=cosine_similarity(chunk.embedding or (), list(vector)))
            for chunk in self.list_active_chunks(scope)
            if chunk.embedding is not None and chunk.embedding_profile_id == profile_id
        ]
        return _rank_descending(hits, limit)

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        return self.records.get(chunk_id)


class HybridRetriever:
    """Fuses keyword and vector candidate sets with reciprocal rank fusion."""

    def __init__(
        self,
        repository: RetrievalRepository,
        embedding_provider: Any | None = None,
        top_k: int = 8,
        candidate_k: int | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.top_k = top_k
        self.candidate_k = candidate_k if candidate_k is not None else max(top_k * 4, 32)
        self.rrf_k = rrf_k
        self.effective_config()

    def effective_config(self) -> dict[str, int]:
        config = {'top_k': self.top_k, 'candidate_k': self.candidate_k, 'rrf_k': self.rrf_k}
        if any(type(value) is not int or value <= 0 for value in config.values()):
            raise ValueError('retrieval limits and RRF k must be positive integers')
        return config

    def retrieve(self, scope: Scope, question: str, query_plan: NormalizedQuery | None = None) -> RetrievalResult:
        started = time.perf_counter()
        effective = self.effective_config()
        plan = query_plan or normalize_query(question)
        timings={'query_processing_ms':(time.perf_counter()-started)*1000,
                 'keyword_retrieval_ms':None,'vector_retrieval_ms':None,'embedding_ms':None,'fusion_ms':None}
        if not scope.knowledge_base_ids:
            return RetrievalResult(query_plan=plan, items=[], sources=(), reason_codes=("NO_CANDIDATES",),
                                   effective_config=effective, latency_ms=(time.perf_counter()-started)*1000)
        degradation_flags: tuple[str, ...] = ()
        keyword_started=time.perf_counter()
        rankings: dict[str, list[RankedHit]] = {
            "keyword": self.repository.keyword_candidates(scope, plan, self.candidate_k)
        }
        timings['keyword_retrieval_ms']=(time.perf_counter()-keyword_started)*1000
        if self.embedding_provider is not None:
            try:
                embedding_started=time.perf_counter()
                embedded = self.embedding_provider.embed([question], timeout_seconds=10)
                timings['embedding_ms']=(time.perf_counter()-embedding_started)*1000
                profile_id = getattr(embedded, "profile_id", None)
                if profile_id is None:
                    resolver = getattr(self.repository, "get_embedding_profile_id", None)
                    if resolver is not None:
                        profile_id = resolver(getattr(embedded, "model", ""), getattr(embedded, "dimensions", 0))
                vector_started=time.perf_counter()
                rankings["vector"] = self.repository.vector_candidates(
                    scope,
                    embedded.vectors[0],
                    self.candidate_k,
                    profile_id=profile_id,
                )
                timings['vector_retrieval_ms']=(time.perf_counter()-vector_started)*1000
            except Exception:
                degradation_flags = ('VECTOR_UNAVAILABLE',)

        fusion_started=time.perf_counter()
        full_fused = rrf_fuse(rankings, k=self.rrf_k)
        timings['fusion_ms']=(time.perf_counter()-fusion_started)*1000
        fused = full_fused[: self.top_k]
        items = [RetrievalItem(self.repository.get_chunk(hit.chunk_id), hit) for hit in fused]
        items = [item for item in items if item.chunk is not None]
        sources = tuple(sorted(rankings.keys()))
        return RetrievalResult(
            query_plan=plan,
            items=items,
            sources=sources,
            reason_codes=() if items else ("NO_CANDIDATES",),
            candidate_rankings={name: tuple(hits) for name, hits in rankings.items()},
            fused_ranking=tuple(full_fused), effective_config=effective,
            latency_ms=(time.perf_counter()-started)*1000, degradation_flags=degradation_flags,
            stage_latency_ms=timings,
        )


__all__ = [
    "HybridRetriever",
    "InMemoryRetrievalRepository",
    "RetrievalItem",
    "RetrievalResult",
    "cosine_similarity",
    "score_keyword_hits",
]
