import json

from eval_center.retrieval_replay import BASELINE_ARM, _aggregate_retrieval
import pytest
from eval_center.gold import SourceSpan
from eval_center import retrieval_replay
from eval_center.retrieval_replay import _retrieval_arm, _strip_verified_index_diagnostics, _verify_postflight, _write_sanitized_result
from eval_center.verified_index import IndexReuseRejected
from types import SimpleNamespace


def _row(*, rank10, top5_hit, context_hit, missing_docs):
    return {
        BASELINE_ARM: {
            "document_metrics_at_5": {"recall_at_k": 0.5, "mrr_at_k": 0.5, "ndcg_at_k": 0.4, "hit_at_k": top5_hit},
            "context_document_metrics_at_5": {"recall_at_k": 0.5, "mrr_at_k": 0.5, "ndcg_at_k": 0.3, "hit_at_k": context_hit},
            "document_ndcg_at_10": rank10,
            "retrieval_latency_ms": 5.0,
            "context_building_ms": 1.0,
            "positive_documents_missing_from_context": missing_docs,
            "full_fused_positive_documents": 2,
        },
    }


def test_replay_aggregates_ndcg_at_10_and_context_hit_loss_separately():
    summary = _aggregate_retrieval([
        _row(rank10=0.8, top5_hit=1, context_hit=0, missing_docs=2),
        _row(rank10=0.4, top5_hit=1, context_hit=1, missing_docs=1),
    ], BASELINE_ARM)

    assert summary["document_ndcg_at_10"] == pytest.approx(0.6)
    assert summary["hybrid_top5_hit_lost_in_context_qids"] == 1
    assert summary["full_fused_positive_document_misses_from_context"] == 3


def test_verified_index_discards_diagnostic_chunks_and_accepts_source_span_values():
    index = {
        "spans": {"chunk-1": SourceSpan("doc-1", "a" * 64, 0, 1)},
        "chunks": [{"content": "private source text", "embedding": "private vector"}],
    }

    retained = _strip_verified_index_diagnostics(index)

    assert retained == {"spans": {"chunk-1": SourceSpan("doc-1", "a" * 64, 0, 1)}}


@pytest.mark.parametrize(
    "degradation_flags,candidate_rankings",
    [
        (("VECTOR_UNAVAILABLE",), {}),
        ((), {"vector": ()}),
    ],
)
def test_replay_refuses_to_score_when_vector_ranking_degraded_or_empty(degradation_flags, candidate_rankings):
    result = SimpleNamespace(degradation_flags=degradation_flags, candidate_rankings=candidate_rankings)

    class Retriever:
        def retrieve(self, _scope, _question):
            return result

    with pytest.raises(IndexReuseRejected, match="vector_retrieval_degraded"):
        _retrieval_arm(Retriever(), SimpleNamespace(question="frozen development question"), "kb-id")


def test_sanitized_result_reports_query_call_count_and_omits_ephemeral_text(tmp_path):
    output_root = tmp_path / "run"
    output_root.mkdir()
    preregistration = {
        "git_sha": "a" * 40,
        "qid_fixture_sha256": "b" * 64,
        "qid_selection_sha256": "c" * 64,
        "candidate_config": {"fusion": "weighted_rrf"},
        "index_reuse": {"scifact": {"status": "VERIFIED_READ_ONLY_REUSE"}},
    }
    datasets = {"scifact": {
        "qid_count": 1,
        "query_embedding_cache": {"provider_calls": 2, "provider_input_items": 2},
        "qa_contexts": {"private": "raw question and answer"},
    }}

    _write_sanitized_result(output_root, preregistration, datasets, "NOT_RUN", status="IN_PROGRESS")

    result_bytes = (output_root / "retrieval-results.json").read_bytes()
    result = json.loads(result_bytes)
    assert result["query_embedding_calls"] == 2
    assert result["corpus_embedding_calls"] == 0
    assert result["corpus_index_builds"] == 0
    assert result["qa"]["reason"] == "QA prompt text was not included in the frozen fixture"
    assert b"raw question and answer" not in result_bytes
    assert "qa_contexts" not in result["datasets"]["scifact"]


def test_postflight_index_read_errors_are_reported_as_index_reuse_rejected(monkeypatch):
    class ConnectionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    item = SimpleNamespace(
        engine=SimpleNamespace(connect=lambda: ConnectionContext()),
        database="rag_eval_trust_6f0c61007b_scifact",
        repository=object(),
        kb_id="kb-id",
        source_bindings={},
        identity={},
    )
    models = {"chat": {"digest": "a" * 64}}
    runtime = {"clone_container_id": "b" * 64}
    monkeypatch.setattr(retrieval_replay, "inspect_known_containers", lambda: {})
    monkeypatch.setattr(retrieval_replay, "validate_clone_runtime", lambda _runtime: runtime)
    monkeypatch.setattr(retrieval_replay, "model_identities", lambda _ollama: models)
    monkeypatch.setattr(retrieval_replay, "require_read_only_connection", lambda *_args: None)
    monkeypatch.setattr(retrieval_replay, "read_index_snapshot", lambda *_args: (_ for _ in ()).throw(RuntimeError("snapshot failed")))

    with pytest.raises(IndexReuseRejected, match="postflight_index_validation"):
        _verify_postflight({"scifact": item}, SimpleNamespace(embedding_model="bge"), models, runtime)
