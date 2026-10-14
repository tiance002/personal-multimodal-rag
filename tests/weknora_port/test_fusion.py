"""Critical branching invariants extracted from the fixed source."""
import math

import pytest

from backend.weknora_port.retrieval.fusion import (
    Hit, RetrievalConfig, effective_config, fuse_or_deduplicate,
    rescale_unbounded_scores,
)


def test_nil_config_and_explicit_zero_threshold_are_distinct():
    defaults = effective_config(None)
    assert (defaults.embedding_top_k, defaults.rerank_top_k) == (50,10)
    assert (defaults.vector_threshold, defaults.keyword_threshold, defaults.rerank_threshold) == (.15,.3,.2)
    assert effective_config(RetrievalConfig()).rerank_threshold == 0
    configured = effective_config(RetrievalConfig(rrf_vector_weight=-1, rrf_keyword_weight=.8))
    assert (configured.rrf_vector_weight, configured.rrf_keyword_weight) == (.7,.8)


def test_vector_only_preserves_scores_keyword_only_rescales():
    vectors = [Hit("a", .3), Hit("a", .9), Hit("b", .7)]
    assert [(hit.chunk_id,hit.score) for hit in fuse_or_deduplicate(vectors,[])] == [("a",.9),("b",.7)]
    keywords = [Hit("a", 24), Hit("b", 12), Hit("a", 2)]
    assert [(hit.chunk_id,hit.score) for hit in fuse_or_deduplicate([],keywords)] == [("a",1),("b",.5)]


def test_hybrid_first_rank_highest_vector_metadata_keyword_union():
    vectors = [Hit("a", .6, "first"), Hit("b", .5), Hit("a", .9, "highest")]
    keywords = [Hit("c",100), Hit("a",90,"keyword")]
    rows = {hit.chunk_id:hit for hit in fuse_or_deduplicate(vectors,keywords)}
    assert rows["a"].content == "highest"
    assert rows["a"].score == pytest.approx(.7/61 + .3/62)
    assert rows["b"].score == pytest.approx(.7/62)
    assert rows["c"].score == pytest.approx(.3/61)


def test_finite_maximum_rescale_edges_and_already_normalized_values():
    rows = [Hit("a",4), Hit("b",float("inf")), Hit("c",float("-inf")), Hit("d",float("nan")),Hit("e",-2)]
    rescale_unbounded_scores(rows)
    assert [hit.score for hit in rows] == [1,1,0,0,0]
    normalized = [Hit("x",.8), Hit("y",float("nan"))]
    rescale_unbounded_scores(normalized)
    assert normalized[0].score == .8 and math.isnan(normalized[1].score)
