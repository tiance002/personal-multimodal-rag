import pytest

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
