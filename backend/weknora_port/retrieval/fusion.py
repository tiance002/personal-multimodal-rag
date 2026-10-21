"""Port of retrieval_config.go and knowledgebase_search_fusion.go (MIT).

Tencent/WeKnora@3e8b0bfc; see docs/weknora-port/UPSTREAM_LICENSE.md.
YAML conversation defaults are intentionally separate from tenant getters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RetrievalConfig:
    embedding_top_k: int = 0
    vector_threshold: float = 0
    keyword_threshold: float = 0
    rerank_top_k: int = 0
    rerank_threshold: float = 0
    rerank_model_id: str = ""
    rrf_k: int = 0
    rrf_vector_weight: float = 0
    rrf_keyword_weight: float = 0


@dataclass(frozen=True)
class EffectiveConfig:
    embedding_top_k: int
    vector_threshold: float
    keyword_threshold: float
    rerank_top_k: int
    rerank_threshold: float
    rrf_k: int
    rrf_vector_weight: float
    rrf_keyword_weight: float


def effective_config(config: RetrievalConfig | None) -> EffectiveConfig:
    c = config or RetrievalConfig()
    return EffectiveConfig(
        c.embedding_top_k if c.embedding_top_k > 0 else 50,
        c.vector_threshold if c.vector_threshold > 0 else .15,
        c.keyword_threshold if c.keyword_threshold > 0 else .3,
        c.rerank_top_k if c.rerank_top_k > 0 else 10,
        .2 if config is None else c.rerank_threshold,
        c.rrf_k if c.rrf_k > 0 else 60,
        c.rrf_vector_weight if c.rrf_vector_weight > 0 else .7,
        c.rrf_keyword_weight if c.rrf_keyword_weight > 0 else .3,
    )


@dataclass
class Hit:
    chunk_id: str
    score: float
    content: str = ""
    knowledge_base_id: str = ""
    knowledge_id: str = ""
    source_id: str = ""
    source_type: int = 0
    tag_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


def deduplicate_by_score(results: Sequence[Hit]) -> list[Hit]:
    selected: dict[str, Hit] = {}
    for hit in results:
        if hit.chunk_id not in selected or hit.score > selected[hit.chunk_id].score:
            selected[hit.chunk_id] = hit
    # Go's map traversal does not prescribe tie order. Deterministic tie ordering
    # belongs to the later FilterTopK stage, not an extra fusion policy here.
    return sorted(selected.values(), key=lambda item: item.score, reverse=True)


def rescale_unbounded_scores(results: Sequence[Hit]) -> None:
    maximum = max((hit.score for hit in results if math.isfinite(hit.score)), default=0)
    if maximum <= 1:
        return
    for hit in results:
        if math.isnan(hit.score) or hit.score <= 0:
            hit.score = 0
        elif math.isinf(hit.score):
            hit.score = 1
        else:
            hit.score /= maximum


def fuse_or_deduplicate(vectors: Sequence[Hit], keywords: Sequence[Hit],
                        config: RetrievalConfig | None = None) -> list[Hit]:
    if not keywords:
        return deduplicate_by_score(vectors)
    if not vectors:
        result = deduplicate_by_score(keywords)
        rescale_unbounded_scores(result)
        return result
    cfg = effective_config(config)
    vector_ranks: dict[str, int] = {}
    keyword_ranks: dict[str, int] = {}
    for index, hit in enumerate(vectors, 1):
        vector_ranks.setdefault(hit.chunk_id, index)
    for index, hit in enumerate(keywords, 1):
        keyword_ranks.setdefault(hit.chunk_id, index)
    # Metadata uses the highest scoring vector duplicate, even when its rank
    # remains the first occurrence; keyword metadata never replaces that row.
    selected = {hit.chunk_id: hit for hit in deduplicate_by_score(vectors)}
    for hit in keywords:
        selected.setdefault(hit.chunk_id, hit)
    for chunk_id, hit in selected.items():
        score = 0.0
        if chunk_id in vector_ranks:
            score += cfg.rrf_vector_weight / (cfg.rrf_k + vector_ranks[chunk_id])
        if chunk_id in keyword_ranks:
            score += cfg.rrf_keyword_weight / (cfg.rrf_k + keyword_ranks[chunk_id])
        hit.score = score
    return sorted(selected.values(), key=lambda item: item.score, reverse=True)
