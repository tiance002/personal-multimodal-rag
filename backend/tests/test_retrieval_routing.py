import pytest

from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.ports.providers import EmbeddingResult


def test_semantic_and_identifier_routes_are_deterministic():
    from backend.app.application.retrieval_policy import RetrievalRouter
    router = RetrievalRouter()
    for question in ("为什么 Transformer 需要 Attention？", "解释可分离变量微分方程。"):
        assert router.route(question).mode == "vector"
    for question in ('查找 "ContextBuilder"。', "candidate_k 在哪里定义？", "BGE-M3 dimension 是多少？", "打开 README.md", "ContextBuilder 如何选择证据？"):
        assert router.route(question).mode == "hybrid"
        assert router.route(question).reason
    assert router.route("1919年发生了什么历史变化？").mode == "vector"
    assert router.route("API v2 的编号是多少？").mode == "hybrid"


class Embedding:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def embed(self, texts, timeout_seconds):
        self.calls += 1
        if self.fail:
            raise RuntimeError("offline")
        return EmbeddingResult([[1.0, 0.0]], "fixture", 2, 1.0, "p")


def repository():
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord("semantic", "kb", "doc1", "v1", "注意力能关联远处的内容", embedding=(1.0, 0.0), embedding_profile_id="p"))
    repo.add(ChunkRecord("lexical", "kb", "doc2", "v2", "为什么注意力机制有帮助？", embedding=(0.0, 1.0), embedding_profile_id="p"))
    return repo


def test_vector_route_avoids_keyword_rank_interference():
    result = HybridRetriever(repository(), Embedding(), top_k=1, mode="adaptive").retrieve(Scope.from_ids(["kb"]), "为什么注意力机制有帮助？")
    assert result.retrieval_mode == "vector"
    assert result.sources == ("vector",)
    assert result.items[0].chunk.chunk_id == "semantic"
    assert result.candidate_rankings.get("keyword", ()) == ()


def test_vector_failure_uses_keyword_without_retrying_embedding():
    provider = Embedding(fail=True)
    result = HybridRetriever(repository(), provider, mode="adaptive").retrieve(Scope.from_ids(["kb"]), "为什么注意力机制有帮助？")
    assert result.retrieval_mode == "keyword"
    assert result.items[0].chunk.chunk_id == "lexical"
    assert "VECTOR_UNAVAILABLE_KEYWORD_FALLBACK" in result.route_reason
    assert provider.calls == 1


def test_keyword_mode_makes_no_embedding_call():
    provider = Embedding()
    result = HybridRetriever(repository(), provider, mode="keyword").retrieve(Scope.from_ids(["kb"]), "注意力")
    assert result.items
    assert provider.calls == 0


def test_ranker_cannot_inject_a_chunk_outside_recall():
    class UntrustedRanker:
        def rank(self, question, hits, chunks):
            return [RankedHit(chunk_id="other-kb", rank=1)]
    with pytest.raises(ValueError, match="unauthorized"):
        HybridRetriever(repository(), Embedding(), ranker=UntrustedRanker()).retrieve(Scope.from_ids(["kb"]), "注意力")


def test_evidence_quality_does_not_invent_conflict_or_correctness():
    from backend.app.application.evidence_quality import EvidenceQualityAssessor
    result = HybridRetriever(repository(), Embedding()).retrieve(Scope.from_ids(["kb"]), "注意力")
    quality = EvidenceQualityAssessor().assess(result, result.items)
    assert quality.level == "MEDIUM"
    assert quality.signals.conflicting_evidence is None
    assert quality.signals.retrieval_confidence is None
    assert quality.basis == "RETRIEVAL_HEURISTIC_NOT_ANSWER_CORRECTNESS"


def test_execution_route_does_not_automatically_send_low_evidence_to_cloud():
    from backend.app.application.execution_routing import ExecutionRouter
    router = ExecutionRouter()
    assert router.choose(prefer_cloud=False, cloud_enabled=True, cloud_allowed=True, cloud_provider_available=True).path == "LOCAL"
    denied = router.choose(prefer_cloud=True, cloud_enabled=True, cloud_allowed=False, cloud_provider_available=True)
    assert denied.error_code == "CLOUD_EGRESS_DISABLED"
    unavailable = router.choose(prefer_cloud=True, cloud_enabled=True, cloud_allowed=True, cloud_provider_available=False)
    assert unavailable.error_code == "CLOUD_PROVIDER_UNAVAILABLE"
