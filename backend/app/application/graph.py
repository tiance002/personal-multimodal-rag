from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

from backend.app.application.retrieval import ChunkRecord
from backend.app.domain.scope import Scope


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


class GraphService:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def build(self, document_id: str, version_id: str) -> GraphJobResult:
        try:
            chunks: list[ChunkRecord] = self.repository.list_version_chunks(document_id, version_id)
            nodes: list[GraphNodeDraft] = []
            edges: list[GraphEdgeDraft] = []
            for index, chunk in enumerate(chunks):
                label = self._label(chunk.content, index)
                nodes.append(GraphNodeDraft(str(uuid.uuid4()), chunk.version_id, chunk.document_id, chunk.knowledge_base_id, "document_chunk", label, f"{label.casefold()}-{index}"))
                if index:
                    previous = chunks[index - 1]
                    edge_id = str(uuid.uuid4())
                    quote = previous.content[:500]
                    edges.append(GraphEdgeDraft(edge_id, chunk.version_id, chunk.document_id, chunk.knowledge_base_id, nodes[index - 1].node_id, nodes[index].node_id, "follows", previous.chunk_id, quote, hashlib.sha256(quote.encode("utf-8")).hexdigest()))
            self.repository.save_graph(version_id, nodes, edges)
            if hasattr(self.repository, "set_graph_status"):
                self.repository.set_graph_status(version_id, "ready")
            return GraphJobResult("ready", tuple(nodes), tuple(edges))
        except Exception as exc:
            if hasattr(self.repository, "set_graph_status"):
                self.repository.set_graph_status(version_id, "failed")
            return GraphJobResult("failed", error_code=type(exc).__name__)

    def query(self, scope: Scope, entity_name: str, depth: int = 1) -> list[GraphEdgeDraft]:
        if not scope.knowledge_base_ids:
            return []
        return [edge for edge in self.repository.query_graph(scope, entity_name, depth) if scope.contains(edge.knowledge_base_id, edge.document_id)]

    @staticmethod
    def _label(content: str, index: int) -> str:
        heading = re.search(r"^#{1,6}\s+(.+)$", content, flags=re.MULTILINE)
        return heading.group(1).strip() if heading else f"chunk-{index + 1}"
