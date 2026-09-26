from backend.app.application.retrieval import ChunkRecord, HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.scope import Scope


def test_hybrid_retrieval_filters_scope_and_current_versions():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("old", "kb-a", "doc-a", "ver-old", "事务回滚旧版本", {"start": 0}, is_current=False))
    repository.add(ChunkRecord("new", "kb-a", "doc-a", "ver-new", "事务回滚新版本", {"start": 0}, is_current=True))
    repository.add(ChunkRecord("other", "kb-b", "doc-b", "ver-other", "事务回滚其他库", {"start": 0}, is_current=True))

    result = HybridRetriever(repository).retrieve(Scope.from_ids(["kb-a"]), "事务回滚")

    assert [item.chunk.chunk_id for item in result.items] == ["new"]
    assert "keyword" in result.sources
