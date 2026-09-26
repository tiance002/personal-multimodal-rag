from __future__ import annotations

from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.app.ports.providers import EmbeddingResult


class ProfileEmbeddingProvider:
    def embed(self, texts, timeout_seconds):
        return EmbeddingResult([[1.0, 0.0]], "model-a", 2, 0.1, profile_id="profile-a")


class RecordingProfileRepository(InMemoryRetrievalRepository):
    def __init__(self) -> None:
        super().__init__()
        self.profile_ids: list[str | None] = []

    def keyword_candidates(self, scope, query, limit):
        return []

    def vector_candidates(self, scope, vector, limit, *, profile_id=None):
        self.profile_ids.append(profile_id)
        return super().vector_candidates(scope, vector, limit, profile_id=profile_id)


def test_vector_candidates_are_filtered_by_exact_embedding_profile() -> None:
    repository = RecordingProfileRepository()
    repository.add(ChunkRecord("a", "kb", "doc-a", "ver-a", "alpha", {}, embedding_profile_id="profile-a", embedding=(1.0, 0.0)))
    repository.add(ChunkRecord("b", "kb", "doc-b", "ver-b", "beta", {}, embedding_profile_id="profile-b", embedding=(1.0, 0.0)))

    result = HybridRetriever(repository, embedding_provider=ProfileEmbeddingProvider()).retrieve(Scope.from_ids(["kb"]), "question")

    assert repository.profile_ids == ["profile-a"]
    assert [item.chunk.chunk_id for item in result.items] == ["a"]
