from backend.app.application.graph import GraphService
from backend.app.application.retrieval import ChunkRecord


class FailingGraphRepository:
    index_status = "ready"

    def list_version_chunks(self, document_id, version_id):
        return [ChunkRecord("c", "kb", document_id, version_id, "source", {})]

    def save_graph(self, version_id, nodes, edges):
        raise RuntimeError("graph provider failed")

    def set_graph_status(self, version_id, status):
        self.graph_status = status


def test_graph_failure_does_not_change_index_status():
    repository = FailingGraphRepository()

    result = GraphService(repository).build("doc", "ver")

    assert result.status == "failed"
    assert repository.index_status == "ready"
    assert repository.graph_status == "failed"
