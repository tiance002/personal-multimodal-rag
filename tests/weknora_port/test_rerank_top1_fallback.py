"""Top1 policy experiment: synthetic provider doubles, no real sends or DB."""
from types import SimpleNamespace as NS

import pytest

from backend.weknora_port.models.types import CallCancelled, ProviderError, RerankResult
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.rerank import rerank
from backend.weknora_port.retrieval.search import SearchNothing
from tests.weknora_port.test_pipeline import CTX, Model, Registry, Repo, row, run, state


@pytest.mark.parametrize("score", [.149, .001, 0., -.1])
def test_valid_top_one_has_no_absolute_score_floor(score):
    s = state(rerank_model_id="existing-rerank", search_results=[row()])
    model = Model([[RerankResult(0, score)]])
    run(rerank(s, CTX, Registry(model)))
    assert [r.id for r in s.rerank_results] == ["a"]
    assert s.rerank_results[0].metadata["model_score"] == f"{score:.4f}"
    assert s.rerank_results[0].score == pytest.approx(max(0., .6 * score + .3 * .8 + .1))
    assert s.rerank_threshold == .3 and len(model.calls) == 1


def test_unsorted_response_uses_highest_legal_score_and_only_one_fallback():
    s = state(rerank_model_id="r", search_results=[row("first"), row("best"), row("last")])
    model = Model([[RerankResult(0, .01), RerankResult(1, .1), RerankResult(2, .03)]])
    run(rerank(s, CTX, Registry(model)))
    assert [r.id for r in s.rerank_results] == ["best"]


def test_threshold_hits_do_not_also_add_a_low_score_fallback():
    s = state(rerank_model_id="r", search_results=[row("above"), row("below")])
    run(rerank(s, CTX, Registry(Model([[RerankResult(0, .31), RerankResult(1, .29)]]))))
    assert [r.id for r in s.rerank_results] == ["above"]


@pytest.mark.parametrize("result", [
    NS(index=-1, score=.001), NS(index=2, score=.001), NS(index=True, score=.001),
    NS(index="0", score=.001), NS(index=0, score=float("nan")),
    NS(index=0, score=float("inf")), NS(index=0, score=-float("inf")),
    NS(index=0, score=True), NS(index=0, score=".1"), NS(index=0, score=None),
    NS(index=0), NS(score=.1), NS(index=0, score=10 ** 1000), None,
])
def test_malformed_provider_results_are_not_fallback_candidates(result):
    s = state(rerank_model_id="r", search_results=[row()])
    with pytest.raises(ValueError, match="RERANK_RESPONSE_INVALID"):
        run(rerank(s, CTX, Registry(Model([[result]]))))
    assert not s.rerank_results


def test_duplicate_provider_indices_fail_closed_instead_of_becoming_top_one():
    s = state(rerank_model_id="r", search_results=[row()])
    with pytest.raises(ValueError, match="RERANK_RESPONSE_INVALID"):
        run(rerank(s, CTX, Registry(Model([[RerankResult(0, .01), RerankResult(0, .02)]]))))
    assert not s.rerank_results


def test_empty_real_response_still_raises_search_nothing():
    s = state(rerank_model_id="r", search_results=[row()])
    with pytest.raises(SearchNothing):
        run(rerank(s, CTX, Registry(Model([[]]))))


def test_no_search_candidates_do_not_invoke_rerank_or_create_a_fallback():
    s = state(rerank_model_id="r", search_results=[])
    model = Model([])
    run(rerank(s, CTX, Registry(model)))
    assert not model.calls and not s.rerank_results
    with pytest.raises(SearchNothing):
        run(RetrievalPipeline(Repo(), Registry(model)).run(state(), CTX))


def test_blank_body_cannot_become_evidence_from_a_document_title():
    s = state(rerank_model_id="r", search_results=[row(content="  ", knowledge_title="A title")],
              rerank_context_enabled=True)
    model = Model([])
    run(rerank(s, CTX, Registry(model)))
    assert not model.calls and not s.rerank_results


def test_unconfigured_rerank_keeps_its_existing_skip_contract():
    s = state(search_results=[row()])
    model = Model([])
    run(rerank(s, CTX, Registry(model)))
    assert not model.calls and not s.rerank_results


def test_cancellation_is_not_converted_into_a_top_one_result():
    s = state(rerank_model_id="r", search_results=[row()])
    with pytest.raises(CallCancelled):
        run(rerank(s, CTX, Registry(Model([CallCancelled()]))))
    assert not s.rerank_results


def test_sent_unknown_error_does_not_get_a_synthetic_rerank_score_or_retry():
    s = state(rerank_model_id="r", search_results=[row()])
    model = Model([ProviderError("PROVIDER_RESPONSE_UNKNOWN", sent=True)])
    # Preserve the existing native API-error contract, independently from the
    # new successful-response Top1 policy. No result or score is fabricated.
    run(rerank(s, CTX, Registry(model)))
    assert not s.rerank_results and len(model.calls) == 1
