from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query
from backend.app.ports.retrieval import RetrievalRepository


class CountingRepository(InMemoryRetrievalRepository):
    """Counts candidate passes so a retry can never slip back in unnoticed."""

    def __init__(self) -> None:
        super().__init__()
        self.keyword_calls = 0

    def keyword_candidates(self, scope, query, limit):
        self.keyword_calls += 1
        return super().keyword_candidates(scope, query, limit)


def test_in_memory_repository_satisfies_the_retrieval_port():
    assert isinstance(InMemoryRetrievalRepository(), RetrievalRepository)


def test_keyword_candidates_are_bounded_by_the_requested_limit():
    repository = InMemoryRetrievalRepository()
    for index in range(10):
        repository.add(ChunkRecord(f"c{index:02d}", "kb", "doc", "ver", "事务回滚说明", {"start": index}))

    hits = repository.keyword_candidates(Scope.from_ids(["kb"]), normalize_query("事务回滚"), 3)

    assert len(hits) == 3
    assert [hit.rank for hit in hits] == [1, 2, 3]


def test_keyword_candidates_never_leave_the_server_scope():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("in", "kb-a", "doc", "ver", "事务回滚", {}))
    repository.add(ChunkRecord("out", "kb-b", "doc", "ver", "事务回滚", {}))

    hits = repository.keyword_candidates(Scope.from_ids(["kb-a"]), normalize_query("事务回滚"), 10)

    assert [hit.chunk_id for hit in hits] == ["in"]


def test_retrieval_runs_exactly_one_candidate_pass_per_query():
    repository = CountingRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "evidence", {}))

    result = HybridRetriever(repository).retrieve(Scope.from_ids(["kb"]), "evidence")

    assert [item.chunk.chunk_id for item in result.items] == ["c"]
    assert repository.keyword_calls == 1


def test_empty_scope_returns_no_candidates_without_touching_the_repository():
    repository = CountingRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "evidence", {}))

    result = HybridRetriever(repository).retrieve(Scope.from_ids([]), "evidence")

    assert result.items == []
    assert result.reason_codes == ("NO_CANDIDATES",)
    assert repository.keyword_calls == 0
