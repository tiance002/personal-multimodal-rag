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


@dataclass
class SearchState:
    needs_retrieval: bool = True
    rerank_top_k: int = 30
    search_results: list[SearchResult | None] = field(default_factory=list)
    rerank_results: list[SearchResult | None] = field(default_factory=list)
    merge_results: list[SearchResult | None] = field(default_factory=list)
