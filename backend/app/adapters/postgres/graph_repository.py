from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.domain.graph import GraphEdgeDraft, GraphNodeDraft
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


class PostgresGraphRepository(PostgresKnowledgeRepository):
    def list_version_chunks(self, document_id: str, version_id: str) -> list[ChunkRecord]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT id,knowledge_base_id,document_id,version_id,content,locator,heading_path FROM chunks WHERE document_id=:document_id AND version_id=:version_id ORDER BY chunk_index"), {"document_id": document_id, "version_id": version_id}).mappings()
            return [ChunkRecord(str(row["id"]), str(row["knowledge_base_id"]), str(row["document_id"]), str(row["version_id"]), row["content"], row["locator"] or {}, tuple(row["heading_path"] or ())) for row in rows]

    def set_graph_status(self, version_id: str, status: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE document_versions SET graph_status=:status WHERE id=:id"), {"status": status, "id": version_id})

    def save_graph(self, version_id: str, nodes: list[GraphNodeDraft], edges: list[GraphEdgeDraft]) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM graph_edge_evidence WHERE edge_id IN (SELECT id FROM graph_edges WHERE version_id=:version_id)"), {"version_id": version_id})
            conn.execute(text("DELETE FROM graph_edges WHERE version_id=:version_id"), {"version_id": version_id})
            conn.execute(text("DELETE FROM graph_nodes WHERE version_id=:version_id"), {"version_id": version_id})
            for node in nodes:
                conn.execute(text("INSERT INTO graph_nodes (id,version_id,document_id,knowledge_base_id,node_type,label,canonical_key) VALUES (:id,:version_id,:document_id,:kb,:node_type,:label,:canonical_key)"), {"id": node.node_id, "version_id": node.version_id, "document_id": node.document_id, "kb": node.knowledge_base_id, "node_type": node.node_type, "label": node.label, "canonical_key": node.canonical_key})
            for edge in edges:
                conn.execute(text("INSERT INTO graph_edges (id,version_id,document_id,knowledge_base_id,source_node_id,target_node_id,relation) VALUES (:id,:version_id,:document_id,:kb,:source,:target,:relation)"), {"id": edge.edge_id, "version_id": edge.version_id, "document_id": edge.document_id, "kb": edge.knowledge_base_id, "source": edge.source_node_id, "target": edge.target_node_id, "relation": edge.relation})
                chunk = conn.execute(text("SELECT locator FROM chunks WHERE id=:id AND version_id=:version_id"), {"id": edge.evidence_chunk_id, "version_id": edge.version_id}).scalar()
                conn.execute(text("INSERT INTO graph_edge_evidence (edge_id,version_id,chunk_id,quote,quote_sha256,locator) VALUES (:edge_id,:version_id,:chunk_id,:quote,:sha256,CAST(:locator AS jsonb))"), {"edge_id": edge.edge_id, "version_id": edge.version_id, "chunk_id": edge.evidence_chunk_id, "quote": edge.evidence_quote, "sha256": edge.evidence_sha256, "locator": json.dumps(chunk or {})})

    def get_document_graph(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        with self.engine.connect() as conn:
            if version_id is None:
                version_id = conn.execute(text("SELECT active_version_id FROM documents WHERE id=:id"), {"id": document_id}).scalar()
            if version_id is None:
                return {"status": "disabled", "nodes": [], "edges": []}
            nodes = [dict(row) for row in conn.execute(text("SELECT id,node_type,label,canonical_key,metadata FROM graph_nodes WHERE document_id=:document_id AND version_id=:version_id ORDER BY created_at"), {"document_id": document_id, "version_id": version_id}).mappings()]
            edges = [dict(row) for row in conn.execute(text("""SELECT ge.id,ge.source_node_id,ge.target_node_id,ge.relation,gee.chunk_id,gee.quote,gee.quote_sha256,gee.locator
                FROM graph_edges ge LEFT JOIN graph_edge_evidence gee ON gee.edge_id=ge.id
                WHERE ge.document_id=:document_id AND ge.version_id=:version_id ORDER BY ge.created_at"""), {"document_id": document_id, "version_id": version_id}).mappings()]
            status = conn.execute(text("SELECT graph_status FROM document_versions WHERE id=:id"), {"id": version_id}).scalar() or "disabled"
            return {"version_id": str(version_id), "status": status, "nodes": nodes, "edges": edges}

    def query_graph(self, scope: Scope, entity_name: str, depth: int = 1) -> list[GraphEdgeDraft]:
        if not scope.knowledge_base_ids:
            return []
        kb_names = ",".join(f":kb_{index}" for index, _ in enumerate(scope.knowledge_base_ids))
        params = {f"kb_{index}": value for index, value in enumerate(scope.knowledge_base_ids)}
        document_clause = ""
        if scope.document_ids:
            document_names = ",".join(f":doc_{index}" for index, _ in enumerate(scope.document_ids))
            params.update({f"doc_{index}": value for index, value in enumerate(scope.document_ids)})
            document_clause = f" AND ge.document_id IN ({document_names})"
        params["name"] = f"%{entity_name.casefold()}%"
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"""SELECT ge.id,ge.version_id,ge.document_id,ge.knowledge_base_id,ge.source_node_id,ge.target_node_id,ge.relation,
                gee.chunk_id,gee.quote,gee.quote_sha256
                FROM graph_edges ge
                JOIN graph_edge_evidence gee ON gee.edge_id=ge.id AND gee.version_id=ge.version_id
                JOIN documents d ON d.id=ge.document_id AND d.knowledge_base_id=ge.knowledge_base_id
                JOIN document_versions dv ON dv.id=ge.version_id AND dv.document_id=ge.document_id
                WHERE ge.knowledge_base_id IN ({kb_names})
                  {document_clause}
                  AND d.deleted_at IS NULL
                  AND d.active_version_id = ge.version_id
                  AND dv.index_status = 'ready'
                  AND dv.graph_status = 'ready'
                  AND EXISTS (
                      SELECT 1 FROM graph_nodes gn
                      WHERE gn.id IN (ge.source_node_id, ge.target_node_id)
                        AND (lower(gn.label) LIKE :name OR lower(gn.canonical_key) LIKE :name)
                  )
                ORDER BY ge.created_at"""), params).mappings()
            return [GraphEdgeDraft(str(row["id"]), str(row["version_id"]), str(row["document_id"]), str(row["knowledge_base_id"]), str(row["source_node_id"]), str(row["target_node_id"]), row["relation"], str(row["chunk_id"]), row["quote"], row["quote_sha256"]) for row in rows]
