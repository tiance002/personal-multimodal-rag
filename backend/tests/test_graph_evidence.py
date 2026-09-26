from backend.app.application.graph import GraphService
from backend.app.domain.models import ChunkRecord


def test_graph_edges_require_evidence_from_the_same_version():
    repository = GraphMemoryRepository()
    repository.chunks = [
        ChunkRecord("c1", "kb", "doc", "ver", "第一段", {"start": 0}),
        ChunkRecord("c2", "kb", "doc", "ver", "第二段", {"start": 4}),
    ]

    result = GraphService(repository).build("doc", "ver")

    assert result.status == "ready"
    assert result.edges
    assert all(edge.evidence_chunk_id in {"c1", "c2"} and edge.version_id == "ver" for edge in result.edges)


class GraphMemoryRepository:
    def __init__(self):
        self.chunks = []
        self.graph = None
        self.graph_status = "disabled"

    def list_version_chunks(self, document_id, version_id):
        return [chunk for chunk in self.chunks if chunk.document_id == document_id and chunk.version_id == version_id]

    def save_graph(self, version_id, nodes, edges):
        self.graph = (version_id, nodes, edges)

    def set_graph_status(self, version_id, status):
        self.graph_status = status


__all__ = ["GraphMemoryRepository"]
