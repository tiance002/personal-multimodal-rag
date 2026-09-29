from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from backend.app.domain.models import RankedHit


def rrf_fuse(
    rankings: Mapping[str, Sequence[RankedHit]],
    k: int = 60,
    weights: Mapping[str, float] | None = None,
) -> list[RankedHit]:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[str, float] = defaultdict(float)
    raw_scores: dict[str, float] = {}
    sources: dict[str, set[str]] = defaultdict(set)
    for source, hits in rankings.items():
        weight = 1.0 if weights is None else float(weights.get(source, 1.0))
        if weight < 0:
            raise ValueError("source weights must be non-negative")
        for position, hit in enumerate(hits, start=1):
            rank = hit.rank if hit.rank > 0 else position
            scores[hit.chunk_id] += weight / (k + rank)
            if hit.raw_score is not None:
                raw_scores[hit.chunk_id] = max(raw_scores.get(hit.chunk_id, float("-inf")), hit.raw_score)
            sources[hit.chunk_id].add(source)
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return [
        RankedHit(
            chunk_id=chunk_id,
            rank=index,
            raw_score=raw_scores.get(chunk_id),
            fused_score=scores[chunk_id],
            sources=tuple(sorted(sources[chunk_id])),
        )
        for index, chunk_id in enumerate(ordered, start=1)
    ]
