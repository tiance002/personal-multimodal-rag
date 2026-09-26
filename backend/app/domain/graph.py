from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GraphNodeDraft:
    node_id: str
    version_id: str
    document_id: str
    knowledge_base_id: str
    node_type: str
    label: str
    canonical_key: str


@dataclass(frozen=True)
class GraphEdgeDraft:
    edge_id: str
    version_id: str
    document_id: str
    knowledge_base_id: str
    source_node_id: str
    target_node_id: str
    relation: str
    evidence_chunk_id: str
    evidence_quote: str
    evidence_sha256: str


@dataclass(frozen=True)
class GraphJobResult:
    status: str
    nodes: tuple[GraphNodeDraft, ...] = ()
    edges: tuple[GraphEdgeDraft, ...] = ()
    error_code: str | None = None
