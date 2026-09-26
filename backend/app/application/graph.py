from __future__ import annotations

import hashlib
import uuid
from typing import Any

from backend.app.domain.graph import GraphEdgeDraft, GraphJobResult, GraphNodeDraft
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


class GraphService:
    """Deterministic document-structure graph, isolated from indexing status.

    A graph failure must never change the chunk index status, so every failure
    is converted into a `GraphJobResult` instead of propagating.
    """

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def build(self, document_id: str, version_id: str) -> GraphJobResult:
        try:
            chunks: list[ChunkRecord] = self.repository.list_version_chunks(document_id, version_id)
            nodes: list[GraphNodeDraft] = []
            edges: list[GraphEdgeDraft] = []
            for index, chunk in enumerate(chunks):
                label = self._label(chunk, index)
                nodes.append(GraphNodeDraft(str(uuid.uuid4()), chunk.version_id, chunk.document_id, chunk.knowledge_base_id, "document_chunk", label, f"{label.casefold()}-{index}"))
                if index:
                    previous = chunks[index - 1]
                    edge_id = str(uuid.uuid4())
                    quote = previous.content[:500]
                    edges.append(GraphEdgeDraft(edge_id, chunk.version_id, chunk.document_id, chunk.knowledge_base_id, nodes[index - 1].node_id, nodes[index].node_id, "follows", previous.chunk_id, quote, hashlib.sha256(quote.encode("utf-8")).hexdigest()))
            self.repository.save_graph(version_id, nodes, edges)
            self.repository.set_graph_status(version_id, "ready")
            return GraphJobResult("ready", tuple(nodes), tuple(edges))
        except Exception as exc:
            try:
                self.repository.set_graph_status(version_id, "failed")
            except Exception:
                pass
            return GraphJobResult("failed", error_code=type(exc).__name__)

    def query(self, scope: Scope, entity_name: str, depth: int = 1) -> list[GraphEdgeDraft]:
        if not scope.knowledge_base_ids:
            return []
        return [edge for edge in self.repository.query_graph(scope, entity_name, depth) if scope.contains(edge.knowledge_base_id, edge.document_id)]

    @staticmethod
    def _label(chunk: ChunkRecord, index: int) -> str:
        """Use the heading parsed once at ingestion time; no second markdown parse here (R-06)."""
        if chunk.heading_path:
            return chunk.heading_path[-1]
        return f"chunk-{index + 1}"
