"""Neutral pipeline data, ported from WeKnora types/chat_manage/search types."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    id: str
    content: str
    score: float
    knowledge_id: str = ""
    knowledge_base_id: str = ""
    chunk_type: str = "text"
    chunk_index: int = 0
    start_at: int = 0
    end_at: int = 0
    parent_chunk_id: str = ""
    pre_chunk_id: str = ""
    next_chunk_id: str = ""
    match_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    knowledge_source: str = ""
    knowledge_title: str = ""
    knowledge_filename: str = ""
    knowledge_description: str = ""
    knowledge_custom_metadata: str = ""
    image_info: str = ""
    chunk_metadata: dict[str, Any] = field(default_factory=dict)
    sub_chunk_ids: list[str] = field(default_factory=list)
    content_rewritten: bool = False
    content_revision: int = 0
    is_enabled: bool = True
    matched_content: str = ""
    seq: int = 0
    knowledge_channel: str = ""
    # Persisted source breadcrumb; never infer it from matched/indexed text.
    context_header: str = ""


def context_results(state):
    """Original normal order followed by independent, verified supplements."""
    normal = state.merge_results
    rescue = getattr(state, "rescue_evidence", ())
    if not rescue:
        return normal
    if getattr(state, "bridge_rescue_mode", "off") != "cheap_bridge_v1":
        raise ValueError("BRIDGE_EVIDENCE_MODE_MISMATCH")
    rows = [*normal, *rescue]
    ids = [row.id for row in rows if row is not None]
    if len(ids) != len(set(ids)):
        raise ValueError("BRIDGE_EVIDENCE_DUPLICATE")
    return rows


@dataclass
class SearchState:
    needs_retrieval: bool = True
    rerank_top_k: int = 30
    search_results: list[SearchResult | None] = field(default_factory=list)
    rerank_results: list[SearchResult | None] = field(default_factory=list)
    merge_results: list[SearchResult | None] = field(default_factory=list)
