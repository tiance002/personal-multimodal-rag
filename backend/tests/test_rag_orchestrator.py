from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.rag_orchestrator import RAGOrchestrator, RagSettings
from backend.app.application.retrieval import ChunkRecord, HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.scope import Scope


class CloudGateway:
    def __init__(self):
        self.calls = []

    def answer(self, prompt: str, timeout_seconds: float):
        self.calls.append(prompt)
        return "cloud answer [E1]"


class TimeoutAnswerGateway:
    def answer(self, prompt: str, timeout_seconds: float):
        raise TimeoutError("model timeout")


def test_cloud_is_not_called_when_any_selected_kb_disallows_egress():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c1", "kb-private", "doc", "ver", "private evidence", {"start": 0}))
    cloud = CloudGateway()
    orchestrator = RAGOrchestrator(
        HybridRetriever(repository),
        CitationService(InMemoryCitationStore()),
        answer_gateway=cloud,
        cloud_allowed_by_kb={"kb-private": False},
    )

    result = orchestrator.answer_query(
        "private",
        Scope.from_ids(["kb-private"]),
        RagSettings(cloud_enabled=True, prefer_cloud=True),
    )

    assert result.error_code == "CLOUD_EGRESS_DISABLED"
    assert cloud.calls == []


def test_local_answer_timeout_returns_readable_retrieval_fallback():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c1", "kb-local", "doc", "ver", "local evidence", {"start": 0}))
    orchestrator = RAGOrchestrator(
        HybridRetriever(repository),
        CitationService(InMemoryCitationStore()),
        answer_gateway=TimeoutAnswerGateway(),
    )

    result = orchestrator.answer_query("evidence", Scope.from_ids(["kb-local"]), RagSettings())

    assert result.error_code is None
    assert "MODEL_UNAVAILABLE" in (result.trace.degradation_code or "")
    assert "local evidence" in result.answer
