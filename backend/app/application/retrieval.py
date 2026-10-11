from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from backend.app.domain.fusion import rrf_fuse
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import NormalizedQuery, normalize_query
from backend.app.ports.retrieval import RetrievalRepository
from backend.app.ports.ranking import CandidateRanker, CandidateDiversitySelector
from backend.app.ports.model_access import model_access
from backend.app.application.retrieval_policy import RetrievalRouter
from backend.app.application.retrieval_merge import ContextPassage, merge_passages, SHORT_CONTEXT
from backend.app.domain.bm25 import score_bm25


@dataclass(frozen=True)
class RetrievalItem:
    chunk: ChunkRecord
    hit: RankedHit
    context_passage: ContextPassage | None = None


@dataclass(frozen=True)
class RetrievalResult:
    query_plan: NormalizedQuery
    items: list[RetrievalItem]
    sources: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    candidate_rankings: dict[str, tuple[RankedHit, ...]] = field(default_factory=dict)
    fused_ranking: tuple[RankedHit, ...] = ()
    effective_config: dict[str, Any] = field(default_factory=dict)
    context_items: tuple[RetrievalItem, ...] = ()
    context_max_per_document: int | None = None
    latency_ms: float | None = None
    degradation_flags: tuple[str, ...] = ()
    stage_latency_ms: dict[str, float | None] = field(default_factory=dict)
    retrieval_mode: str = "hybrid"
    route_reason: tuple[str, ...] = ()
    embedding_cache_hit: bool | None = None
    merge_provenance: dict[str, Any] | None = None
    retrieval_stats: dict[str, Any] = field(default_factory=dict)


def _rank_descending(hits: list[RankedHit], limit: int) -> list[RankedHit]:
    hits.sort(key=lambda hit: (-float(hit.raw_score or 0), hit.chunk_id))
    return [hit.model_copy(update={"rank": index}) for index, hit in enumerate(hits[:limit], start=1)]


def score_keyword_hits(chunks: list[ChunkRecord], query: NormalizedQuery, limit: int) -> list[RankedHit]:
    """Frozen legacy TF scorer retained only for baseline comparisons.

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
            if chunk.is_current and chunk.chunk_role == 'child' and scope.contains(chunk.knowledge_base_id, chunk.document_id)
        ]
        chunks.sort(key=lambda chunk: chunk.chunk_id)
        return chunks[:limit] if limit is not None else chunks

    def keyword_candidates(self, scope: Scope, query: NormalizedQuery, limit: int) -> list[RankedHit]:
        if not scope.knowledge_base_ids or not query.terms:
            return []
        return score_bm25(self.list_active_chunks(scope), query, limit)

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

    def get_retrieval_chunks(self, scope: Scope, chunk_ids) -> dict[str, ChunkRecord]:
        return {c.chunk_id: c for c in self.list_active_chunks(scope) if c.chunk_id in chunk_ids}

    def read_parent_contexts(self, scope: Scope, seeds) -> dict[str, ChunkRecord]:
        from backend.app.application.retrieval_merge import valid_context
        return {seed.chunk_id: parent for seed in seeds
                if (parent := self.records.get(seed.parent_id)) is not None
                and valid_context(seed, parent, scope, parent=True)}


class HybridRetriever:
    """Fuses keyword and vector candidate sets with reciprocal rank fusion."""

    def __init__(
        self,
        repository: RetrievalRepository,
        embedding_provider: Any | None = None,
        top_k: int = 8,
        candidate_k: int | None = None,
        rrf_k: int = 60,
        source_weights: Mapping[str, float] | None = None,
        context_candidate_k: int | None = None,
        context_max_per_document: int | None = None,
        mode: str = "hybrid",
        ranker: CandidateRanker | None = None,
        diversity_selector: CandidateDiversitySelector | None = None,
        *,
        context_max_items: int | None = None,
        query_expansion_enabled: bool = True,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider
        self.top_k = top_k
        self.candidate_k = candidate_k if candidate_k is not None else max(top_k * 4, 32)
        self.rrf_k = rrf_k
        self.source_weights = dict(source_weights) if source_weights is not None else None
        self.context_candidate_k = context_candidate_k
        self.context_max_per_document = context_max_per_document
        self.context_max_items = context_max_items
        self.query_expansion_enabled = query_expansion_enabled
        self.router = RetrievalRouter(mode)
        self.ranker = ranker
        self.diversity_selector = diversity_selector
        self.effective_config()

    def effective_config(self) -> dict[str, Any]:
        config = {'top_k': self.top_k, 'candidate_k': self.candidate_k, 'rrf_k': self.rrf_k}
        if any(type(value) is not int or value <= 0 for value in config.values()):
            raise ValueError('retrieval limits and RRF k must be positive integers')
        if self.source_weights is not None:
            # Validate the opt-in configuration before making any retrieval call.
            rrf_fuse({}, k=self.rrf_k, source_weights=self.source_weights)
            config['source_weights'] = dict(sorted(self.source_weights.items()))
        if self.context_candidate_k is not None:
            context_limit = min(self.candidate_k, self.top_k * 2)
            if (type(self.context_candidate_k) is not int or self.context_candidate_k < self.top_k
                    or self.context_candidate_k > context_limit):
                raise ValueError('context_candidate_k must be between top_k and min(candidate_k, 2*top_k)')
            if (self.context_max_per_document is not None
                    and (type(self.context_max_per_document) is not int or self.context_max_per_document <= 0)):
                raise ValueError('context_max_per_document must be a positive integer')
            config['context_candidate_k'] = self.context_candidate_k
            if self.context_max_per_document is not None:
                config['context_max_per_document'] = self.context_max_per_document
            if self.context_max_items is not None:
                if (type(self.context_max_items) is not int or self.context_max_items <= 0
                        or self.context_max_items > self.context_candidate_k):
                    raise ValueError('context_max_items must be between 1 and context_candidate_k')
                config['context_max_items'] = self.context_max_items
        elif self.context_max_per_document is not None:
            raise ValueError('context_max_per_document requires context_candidate_k')
        elif self.context_max_items is not None:
            raise ValueError('context_max_items requires context_candidate_k')
        if self.router.mode != "hybrid":
            config['mode'] = self.router.mode
        if self.ranker is not None or self.diversity_selector is not None:
            config.update(rerank_enabled=self.ranker is not None, mmr_enabled=self.diversity_selector is not None)
        return config

    def retrieve(self, scope: Scope, question: str, query_plan: NormalizedQuery | None = None, *,
                 understanding: Any = None, allow_expansion: bool = True) -> RetrievalResult:
        started = time.perf_counter()
        effective = self.effective_config()
        plan = query_plan or normalize_query(question)
        route = self.router.route(question)
        mode, route_reason = route.mode, route.reason
        timings={'query_processing_ms':(time.perf_counter()-started)*1000,
                 'keyword_retrieval_ms':None,'vector_retrieval_ms':None,'embedding_ms':None,'fusion_ms':None,
                 'ranking_ms':None,'diversity_ms':None}
        if not scope.knowledge_base_ids:
            return RetrievalResult(query_plan=plan, items=[], sources=(), reason_codes=("NO_CANDIDATES",),
                                   effective_config=effective, latency_ms=(time.perf_counter()-started)*1000,
                                   retrieval_mode=mode, route_reason=route_reason, stage_latency_ms=timings)
        degradation_flags: tuple[str, ...] = ()
        embedding_cache_hit: bool | None = None
        keyword_started=time.perf_counter()
        rankings: dict[str, list[RankedHit]] = {}
        if mode in ("keyword", "hybrid"):
            rankings["keyword"] = self.repository.keyword_candidates(scope, plan, self.candidate_k)
            timings['keyword_retrieval_ms']=(time.perf_counter()-keyword_started)*1000
        if mode in ("vector", "hybrid") and self.embedding_provider is not None:
            try:
                embedding_started=time.perf_counter()
                allowed = True
                if getattr(self.embedding_provider, "provider_kind", None) == "cloud":
                    checker = getattr(self.repository, "embedding_scope_allowed", None)
                    allowed = checker is not None and checker(scope) is True
                with model_access("embedding", allowed=allowed, scope=scope):
                    embedded = self.embedding_provider.embed([question], timeout_seconds=10)
                validator = getattr(self.repository, "validate_embedding_result", None)
                if validator is not None:
                    validator(embedded)
                embedding_cache_hit = bool(getattr(self.embedding_provider, "last_cache_hit", False))
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
        elif mode in ("vector", "hybrid"):
            degradation_flags = ('VECTOR_UNAVAILABLE',)

        # q0 keyword fallback remains available even when the model is absent.
        if mode == "vector" and not rankings.get("vector"):
            keyword_started = time.perf_counter()
            rankings["keyword"] = self.repository.keyword_candidates(scope, plan, self.candidate_k)
            timings['keyword_retrieval_ms'] = (time.perf_counter() - keyword_started) * 1000
            mode = "keyword"
            route_reason += (("VECTOR_UNAVAILABLE_KEYWORD_FALLBACK" if degradation_flags else "VECTOR_EMPTY_KEYWORD_FALLBACK"),)
        elif mode == "hybrid" and "vector" not in rankings:
            mode = "keyword"
            route_reason += ("VECTOR_UNAVAILABLE_KEYWORD_FALLBACK",)

        # Lexical recovery occurs before one global fusion/rerank. Dense still
        # uses the original validated expression once, not each projection.
        from backend.app.application.query_expansion import risk_signals, variants, fingerprint
        read = getattr(self.repository, 'get_retrieval_chunks', None)
        first_ids = list(dict.fromkeys(h.chunk_id for hits in rankings.values() for h in hits))
        first_chunks = (read(scope,first_ids) if read else {key:self.repository.get_chunk(key) for key in first_ids})
        first_chunks = {key:c for key,c in first_chunks.items() if c and c.chunk_id==key and c.is_current
                        and c.chunk_role=='child' and scope.contains(c.knowledge_base_id,c.document_id)}
        keyword_requested='keyword' in rankings
        before = risk_signals(question,list(first_chunks.values()),rankings.get('keyword',()),understanding,keyword_requested=keyword_requested)
        # A semantic vector route deliberately omits lexical voting. Lexical
        # mismatch alone does not prove a gap in semantic paraphrase evidence.
        # Explicit unsupported targets/tasks still permit bounded recovery.
        trigger_reasons=(before['reasons'] if keyword_requested else [reason for reason in before['reasons']
            if reason in {'EMPTY_CANDIDATES','EVIDENCE_TARGET_UNCOVERED','EXPLICIT_TASK_UNCOVERED'}])
        expansion = dict(enabled=self.query_expansion_enabled and allow_expansion,triggered=False,
            before=before,after=before,trigger_reasons=trigger_reasons,queries=[],model_calls=0,cost_microunits=0,cost_basis='DETERMINISTIC_NO_MODEL',
            initial_candidate_rankings={k:[h.model_dump(mode='json') for h in v] for k,v in rankings.items()},
            q0_sha256=fingerprint(understanding.q0 if understanding is not None else plan.q0),
            rewrite_sha256=fingerprint(question),status='NOT_NEEDED',latency_ms=0.0)
        if expansion['enabled'] and trigger_reasons:
            stage=time.perf_counter();expansion['triggered']=True
            initial_rankings=dict(rankings);initial_mode=mode;initial_reason=route_reason
            local={'original':rankings.get('keyword',[])}
            for i,(projection,projected_plan) in enumerate(variants(question,understanding),1):
                row=dict(source=projection.source,query_sha256=fingerprint(projection.text),
                         terms_sha256=fingerprint('\0'.join(projected_plan.terms)),candidates=[],status='FAILED')
                try:
                    hits=self.repository.keyword_candidates(scope,projected_plan,self.candidate_k)
                    local[f'projection_{i}']=hits
                    row.update(status='RETRIEVED',candidates=[h.model_dump(mode='json') for h in hits])
                except Exception:
                    row['reason']='EXPANSION_RETRIEVAL_UNAVAILABLE'
                    degradation_flags+=('EXPANSION_RETRIEVAL_UNAVAILABLE',)
                expansion['queries'].append(row)
            if len(local)>1 and all(r['status']=='RETRIEVED' for r in expansion['queries']):
                merged=rrf_fuse(local,k=self.rrf_k)
                rankings['keyword']=[h.model_copy(update={'rank':i,'raw_score':h.fused_score}) for i,h in enumerate(merged[:self.candidate_k],1)]
                if mode=='vector':mode='hybrid';route_reason+=('LOW_RECALL_LEXICAL_RECOVERY',)
            ids=list(dict.fromkeys(h.chunk_id for hits in rankings.values() for h in hits))
            try:
                after_chunks=(read(scope,ids) if read else {key:self.repository.get_chunk(key) for key in ids})
                after_chunks=[c for key,c in after_chunks.items() if c and c.chunk_id==key and c.is_current
                              and c.chunk_role=='child' and scope.contains(c.knowledge_base_id,c.document_id)]
                expansion['after']=risk_signals(question,after_chunks,rankings.get('keyword',()),understanding,keyword_requested='keyword' in rankings)
            except Exception:
                rankings=initial_rankings;mode=initial_mode;route_reason=initial_reason
                expansion['validation_failure']='EXPANSION_READBACK_UNAVAILABLE'
                degradation_flags+=('EXPANSION_READBACK_UNAVAILABLE',)
            expansion['status']='DEGRADED_KEEP_INITIAL' if any(r['status']=='FAILED' for r in expansion['queries']) else 'COMPLETED' if expansion['queries'] else 'NO_SAFE_VARIANT'
            if expansion.get('validation_failure'):expansion['status']='DEGRADED_KEEP_INITIAL'
            expansion['latency_ms']=(time.perf_counter()-stage)*1000
        timings['query_expansion_ms']=expansion['latency_ms']
        fusion_started=time.perf_counter()
        full_fused = rrf_fuse(rankings, k=self.rrf_k, source_weights=self.source_weights)
        timings['fusion_ms']=(time.perf_counter()-fusion_started)*1000
        # Revalidate active Scope before any cloud input, never use historical
        # citation readback as production authorization.
        reader = getattr(self.repository, 'get_retrieval_chunks', None)
        chunks = (reader(scope, [h.chunk_id for h in full_fused]) if reader else
                  {h.chunk_id: self.repository.get_chunk(h.chunk_id) for h in full_fused})
        chunks = {key: c for key, c in chunks.items() if c is not None and c.chunk_id == key
                  and c.is_current is True and c.chunk_role == 'child'
                  and scope.contains(c.knowledge_base_id, c.document_id)}
        full_fused = [h for h in full_fused if h.chunk_id in chunks]
        fused_before_rank = [h.model_dump(mode='json') for h in full_fused]
        # Fusion recalls candidates; optional second stages own their ordering.
        for adapter, method, timing_key, failure in (
            (self.ranker, "rank", "ranking_ms", "RANKER_UNAVAILABLE"),
            (self.diversity_selector, "select", "diversity_ms", "DIVERSITY_UNAVAILABLE"),
        ):
            if adapter is None or not full_fused:
                continue
            stage_started = time.perf_counter()
            try:
                allowed = True
                if getattr(adapter, 'provider_kind', None) == 'cloud':
                    checker = getattr(self.repository, 'embedding_scope_allowed', None)
                    allowed = checker is not None and checker(scope) is True
                with model_access('rerank', allowed=allowed, scope=scope):
                    ranked = list(getattr(adapter, method)(question, tuple(full_fused), chunks))
                ids = [hit.chunk_id for hit in ranked]
                if (len(ids) != len(full_fused) or len(ids) != len(set(ids))
                        or set(ids) != {h.chunk_id for h in full_fused}):
                    raise ValueError('ranking returned incomplete or unauthorized candidates')
                originals = {h.chunk_id: h for h in full_fused}
                # Only order is accepted from a ranker, never its replacement
                # scores, source channels or arbitrary candidate metadata.
                full_fused = [originals[key].model_copy(update={'rank': rank})
                              for rank, key in enumerate(ids, 1)]
            except Exception:
                degradation_flags += (failure,)
            timings[timing_key] = (time.perf_counter() - stage_started) * 1000
        if self.ranker is not None:
            effective['rerank_status'] = ('FUSION_FALLBACK' if 'RANKER_UNAVAILABLE' in degradation_flags else
                                         'RERANKED' if full_fused else 'EMPTY_SKIP')
            effective['rerank_circuit_open'] = bool(getattr(self.ranker, 'circuit_open', False))
        merge_started = time.perf_counter()
        candidates = [(chunks[h.chunk_id], h) for h in full_fused]
        parents, neighbors = {}, {}
        try:
            parent_reader = getattr(self.repository, 'read_parent_contexts', None)
            if parent_reader:
                parents = parent_reader(scope, [c for c, _ in candidates])
        except Exception:
            degradation_flags += ('PARENT_CONTEXT_UNAVAILABLE',)
        try:
            neighbor_reader = getattr(self.repository, 'read_context_rows', None)
            short = [c for c, _ in candidates if c.chunk_id not in parents and len(c.content) < SHORT_CONTEXT
                     and c.chunk_type == 'text' and c.content_sha256][:10]
            if neighbor_reader and short:
                for row in neighbor_reader(scope, short, max_seeds=10).rows:
                    if row.offset:
                        neighbors.setdefault(row.seed_id, []).append((row.offset, row.metadata.chunk))
        except Exception:
            degradation_flags += ('NEIGHBOR_CONTEXT_UNAVAILABLE',)
        groups = merge_passages(scope, candidates, parents, neighbors)
        timings['merge_ms'] = (time.perf_counter() - merge_started) * 1000

        def materialize(selected_groups):
            return [RetrievalItem(c, h, passage if (passage.parent_ids or passage.neighbor_ids
                        or len(passage.unit_ids) > 1) else None)
                    for passage, units in selected_groups for c, h in units]

        # Top-K counts merged contexts; every retained original citation unit
        # stays available to coverage, validation and snapshot freezing.
        items = materialize(groups[:self.top_k])
        context_items: tuple[RetrievalItem, ...] = ()
        if self.context_candidate_k is not None:
            context_items = tuple(materialize(groups[:self.context_candidate_k]))
        sources = tuple(sorted(rankings.keys()))
        return RetrievalResult(
            query_plan=plan,
            items=items,
            sources=sources,
            reason_codes=() if items else ("NO_CANDIDATES",),
            candidate_rankings={name: tuple(hits) for name, hits in rankings.items()},
            fused_ranking=tuple(full_fused), effective_config=effective,
            context_items=context_items,
            context_max_per_document=self.context_max_per_document,
            latency_ms=(time.perf_counter()-started)*1000, degradation_flags=degradation_flags,
            stage_latency_ms=timings,
            retrieval_mode=mode, route_reason=route_reason,
            embedding_cache_hit=embedding_cache_hit,
            retrieval_stats=dict(candidate_count=len(full_fused), merged_count=len(groups),
                query_expansion=expansion,keyword_scoring='BM25/k1=1.2/b=0.75/mixed-script-compatible/v2',
                fused_before_rank=fused_before_rank,
                final_context_count=min(len(groups), self.top_k), final_evidence_count=len(items),
                document_count=len({i.chunk.document_id for i in items}),
                parent_count=len({p for i in items if i.context_passage for p in i.context_passage.parent_ids}),
                cross_document=len({i.chunk.document_id for i in items}) > 1,
                contains_table=any(i.chunk.locator.get('table_id') is not None for i in items),
                contains_image_caption_or_ocr=any(i.chunk.chunk_type in {'caption','image_caption','ocr','image_ocr'} for i in items),
                answer_correctness='UNKNOWN'),
        )


__all__ = [
    "HybridRetriever",
    "InMemoryRetrievalRepository",
    "RetrievalItem",
    "RetrievalResult",
    "cosine_similarity",
    "score_keyword_hits",
]
