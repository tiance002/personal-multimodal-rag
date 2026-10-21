"""WeKnora chat_pipeline/filter_top_k.go semantic port (MIT, 3e8b0bfc).

Attribution: docs/weknora-port/UPSTREAM_LICENSE.md.
"""
from functools import cmp_to_key

from .types import SearchResult, SearchState


def _compare(left: SearchResult | None, right: SearchResult | None) -> int:
    if left is None or right is None:
        return (right is not None) - (left is not None)
    if left.score != right.score:
        # Match the source comparator even for non-finite scores; normalization
        # and filtering belong to earlier stages, not a new TopK validator.
        return -1 if left.score > right.score else 1
    for a, b in ((left.knowledge_id, right.knowledge_id),
                 (left.chunk_type, right.chunk_type),
                 (left.chunk_index, right.chunk_index), (left.id, right.id)):
        if a != b:
            return -1 if a < b else 1
    return 0


def filter_top_k(state: SearchState) -> None:
    if not state.needs_retrieval:
        return
    for name in ("merge_results", "rerank_results", "search_results"):
        rows = getattr(state, name)
        if rows:
            rows.sort(key=cmp_to_key(_compare))
            if state.rerank_top_k > 0 and len(rows) > state.rerank_top_k:
                setattr(state, name, rows[:state.rerank_top_k])
            return
