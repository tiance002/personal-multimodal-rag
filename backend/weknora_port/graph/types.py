"""Source-bound graph records; generated edges are retrieval hints, not facts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


VERIFICATION = "QUOTE_AND_ORDER_CHECKED_NOT_SEMANTIC_JUDGE"


def stable_id(kind: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{kind}-" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class GraphSource:
    tenant_id: int
    owner_id: str
    knowledge_base_id: str
    document_id: str
    source_version: str
    chunk_id: str
    content: str
    start_at: int = 0
    end_at: int = 0
    page_number: int | None = None

    def __post_init__(self) -> None:
        if type(self.tenant_id) is not int or self.tenant_id < 1:
            raise ValueError("Graph source needs a positive tenant ID")
        for name in ("owner_id", "knowledge_base_id", "document_id", "source_version", "chunk_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"Invalid graph source {name}")
        if not isinstance(self.content, str):
            raise ValueError("Graph source content must be text")
        if type(self.start_at) is not int or type(self.end_at) is not int or not 0 <= self.start_at <= self.end_at:
            raise ValueError("Invalid source offsets")
        if self.page_number is not None and (type(self.page_number) is not int or self.page_number < 1):
            raise ValueError("Invalid physical page number")

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GraphNode:
    id: str
    local_key: str
    name: str
    entity_type: str
    aliases: tuple[str, ...]
    is_anchor: bool
    identity_qualifier: str = ""


@dataclass(frozen=True)
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    predicate: str
    quote: str
    quote_start: int
    quote_end: int
    temporal_text: str = ""
    verification: str = VERIFICATION


@dataclass(frozen=True)
class GraphBatch:
    source: GraphSource
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    rejected: tuple[dict, ...]
    extraction_id: str

    @property
    def status(self) -> str:
        if self.rejected:
            return "PARTIAL" if self.edges else "REJECTED"
        return "EXTRACTED" if self.edges else "EMPTY"


Source = GraphSource
Node = GraphNode
Edge = GraphEdge
