from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.rag_orchestrator import RAGOrchestrator, RagSettings
from backend.app.application.retrieval import ChunkRecord, HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.scope import Scope


class MustNotCallCloud:
    def __init__(self):
        self.calls = 0

    def answer(self, prompt: str, timeout_seconds: float):
        self.calls += 1
        raise AssertionError("cloud call crossed a denied scope")


def test_cloud_denial_applies_to_the_whole_selected_scope():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c", "kb-private", "doc", "ver", "secret", {}))
    cloud = MustNotCallCloud()
    orchestrator = RAGOrchestrator(HybridRetriever(repository), CitationService(InMemoryCitationStore()), answer_gateway=cloud, cloud_allowed_by_kb={"kb-private": False, "kb-public": True})

    result = orchestrator.answer_query("secret", Scope.from_ids(["kb-private", "kb-public"]), RagSettings(cloud_enabled=True, prefer_cloud=True))

    assert result.error_code == "CLOUD_EGRESS_DISABLED"
    assert cloud.calls == 0
