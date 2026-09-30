import pytest

from backend.app.application.context_builder import ContextBuilder
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.retrieval import ChunkRecord, HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.fusion import rrf_fuse
from backend.app.domain.models import RankedHit
from backend.app.domain.scope import Scope


def test_hybrid_retrieval_filters_scope_and_current_versions():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("old", "kb-a", "doc-a", "ver-old", "事务回滚旧版本", {"start": 0}, is_current=False))
    repository.add(ChunkRecord("new", "kb-a", "doc-a", "ver-new", "事务回滚新版本", {"start": 0}, is_current=True))
    repository.add(ChunkRecord("other", "kb-b", "doc-b", "ver-other", "事务回滚其他库", {"start": 0}, is_current=True))

    result = HybridRetriever(repository).retrieve(Scope.from_ids(["kb-a"]), "事务回滚")

    assert [item.chunk.chunk_id for item in result.items] == ["new"]
    assert "keyword" in result.sources


def test_equal_weight_rrf_is_unchanged_when_weights_are_explicit():
    rankings = {
        "vector": [RankedHit(chunk_id="z", rank=1), RankedHit(chunk_id="a", rank=2)],
        "keyword": [RankedHit(chunk_id="a", rank=1), RankedHit(chunk_id="z", rank=2)],
    }

    implicit = rrf_fuse(rankings, k=60)
    explicit = rrf_fuse(rankings, k=60, source_weights={"vector": 1.0, "keyword": 1.0})

    assert implicit == explicit


def test_weighted_rrf_can_reduce_a_weaker_keyword_rank_vote():
    rankings = {
        "vector": [RankedHit(chunk_id="z-relevant", rank=1), RankedHit(chunk_id="a-other", rank=2)],
        "keyword": [RankedHit(chunk_id="a-other", rank=1), RankedHit(chunk_id="z-relevant", rank=2)],
    }

    equal_weight = rrf_fuse(rankings)
    weighted = rrf_fuse(rankings, source_weights={"vector": 1.0, "keyword": 0.25})

    assert equal_weight[0].chunk_id == "a-other"  # deterministic lexical tie-break
    assert weighted[0].chunk_id == "z-relevant"
    assert weighted[0].fused_score > weighted[1].fused_score


def test_rrf_ties_and_missing_source_weights_are_deterministic():
    first = rrf_fuse({"keyword": [RankedHit(chunk_id="z", rank=1)],
                      "vector": [RankedHit(chunk_id="a", rank=1)]})
    second = rrf_fuse({"vector": [RankedHit(chunk_id="a", rank=1)],
                       "keyword": [RankedHit(chunk_id="z", rank=1)]},
                      source_weights={"vector": 1.0})

    assert [hit.chunk_id for hit in first] == ["a", "z"]
    assert [hit.chunk_id for hit in second] == ["a", "z"]


@pytest.mark.parametrize("weight", [0, -1, float("nan"), float("inf"), True])
def test_rrf_rejects_invalid_source_weights(weight):
    with pytest.raises(ValueError, match="source weights"):
        rrf_fuse({"vector": [RankedHit(chunk_id="c", rank=1)]}, source_weights={"vector": weight})


def test_context_candidate_pool_does_not_increase_answer_top_k():
    repository = InMemoryRetrievalRepository()
    for index in range(1, 6):
        document_id = "doc-a" if index <= 3 else f"doc-{index}"
        repository.add(ChunkRecord(f"chunk-{index}", "kb-a", document_id, "ver", "alpha evidence"))
    retriever = HybridRetriever(
        repository,
        top_k=2,
        candidate_k=4,
        context_candidate_k=4,
        context_max_per_document=1,
    )

    result = retriever.retrieve(Scope.from_ids(["kb-a"]), "alpha")

    assert len(result.items) == 2
    assert len(result.context_items) == 4
    assert result.effective_config["top_k"] == 2
    assert result.effective_config["context_candidate_k"] == 4
    assert result.context_max_per_document == 1

    evidence = EvidenceService(context_builder=ContextBuilder())
    bundle = evidence.bundle(evidence.plan("alpha"), result)
    assert [item.chunk.chunk_id for item in bundle.selected] == ["chunk-1", "chunk-4"]


def test_context_candidate_pool_must_fit_fused_candidate_bound():
    repository = InMemoryRetrievalRepository()

    with pytest.raises(ValueError, match="context_candidate_k"):
        HybridRetriever(repository, top_k=5, candidate_k=2, context_candidate_k=5)

    with pytest.raises(ValueError, match="requires context_candidate_k"):
        HybridRetriever(repository, top_k=2, candidate_k=4, context_max_per_document=1)
