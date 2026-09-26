from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
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
    chain = LangChainQuickChain(
        KnowledgeGateway(HybridRetriever(repository)),
        answer_gateway=cloud,
    )

    result = chain.invoke(
        "secret",
        Scope.from_ids(["kb-private", "kb-public"]),
        settings=QuickSettings(cloud_enabled=True, prefer_cloud=True),
        cloud_allowed_by_kb={"kb-private": False, "kb-public": True},
    )

    assert result.error_code == "CLOUD_EGRESS_DISABLED"
    assert cloud.calls == 0
