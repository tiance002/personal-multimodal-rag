from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.app.ports.providers import EmbeddingResult


class Provider:
    def embed(self, texts, timeout_seconds):
        return EmbeddingResult([[1.0, 0.0]], "test-model", 2, 1.0, "profile")


class CountingRepository(InMemoryRetrievalRepository):
    def __init__(self):
        super().__init__()
        self.keyword_calls = 0

    def keyword_candidates(self, scope, query, limit):
        self.keyword_calls += 1
        return super().keyword_candidates(scope, query, limit)


def test_opt_in_vector_only_never_calls_keyword_branch():
    repository = CountingRepository()
    repository.add(ChunkRecord("c1", "kb", "doc", "v1", "term", embedding=(1.0, 0.0),
                               embedding_profile_id="profile"))
    result = HybridRetriever(repository, embedding_provider=Provider(), top_k=1,
                             enabled_sources=("vector",)).retrieve(Scope.from_ids(["kb"]), "term")
    assert repository.keyword_calls == 0
    assert result.sources == ("vector",)
    assert [item.chunk.chunk_id for item in result.items] == ["c1"]


def test_opt_in_weighted_fusion_changes_order_without_changing_default():
    repository = CountingRepository()
    repository.add(ChunkRecord("a", "kb", "a", "v1", "term", embedding=(0.0, 1.0),
                               embedding_profile_id="profile"))
    repository.add(ChunkRecord("b", "kb", "b", "v1", "other", embedding=(1.0, 0.0),
                               embedding_profile_id="profile"))
    scope = Scope.from_ids(["kb"])
    default = HybridRetriever(repository, embedding_provider=Provider(), top_k=2, candidate_k=1).retrieve(scope, "term")
    weighted = HybridRetriever(repository, embedding_provider=Provider(), top_k=2, candidate_k=1,
                               source_weights={"keyword": 0.2, "vector": 0.8}).retrieve(scope, "term")
    assert default.fused_ranking[0].chunk_id == "a"
    assert weighted.fused_ranking[0].chunk_id == "b"
