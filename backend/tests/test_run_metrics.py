from backend.app.application.model_usage import call_stage, record_call
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


def test_run_metrics_records_real_usage_and_ids_without_source_text():
    from backend.app.application.run_metrics import collect_metrics
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord("c", "kb", "doc", "ver", "private evidence"))
    result = HybridRetriever(repo).retrieve(Scope.from_ids(["kb"]), "private")
    with collect_metrics("run") as metrics:
        metrics.record_retrieval(result)
        metrics.record_context(result.items, latency_ms=2.0)
        with call_stage("answer"):
            record_call(model="qwen", status="ok", response={"prompt_eval_count": 12, "eval_count": 3}, latency_ms=4)
        row = metrics.snapshot(citations=("E1",), error=None)
    assert row["query_id"] == "run"
    assert row["input_tokens"] == 12
    assert row["output_tokens"] == 3
    assert row["total_tokens"] == 15
    assert row["context_tokens"] == "NOT_AVAILABLE"
    assert row["selected_context_chunk_ids"] == ["c"]
    assert row["cloud_called"] is False
    assert row["local_model_latency_ms"] == 4
    assert "private evidence" not in str(row)


def test_missing_provider_usage_remains_unavailable():
    from backend.app.application.run_metrics import collect_metrics
    with collect_metrics("unknown") as metrics:
        with call_stage("answer"):
            record_call(model="qwen", status="error", response=None, latency_ms=7)
        row = metrics.snapshot(citations=(), error="MODEL_UNAVAILABLE")
    assert row["input_tokens"] == "NOT_AVAILABLE"
    assert row["output_tokens"] == "NOT_AVAILABLE"
    assert row["total_tokens"] == "NOT_AVAILABLE"
    assert row["retry_count"] == 0


def test_nested_runs_do_not_share_model_consumption():
    from backend.app.application.run_metrics import collect_metrics
    with collect_metrics("outer") as outer:
        with collect_metrics("inner") as inner:
            with call_stage("answer"):
                record_call(model="qwen", status="ok", response={"prompt_eval_count": 2, "eval_count": 1}, latency_ms=1)
            assert inner.snapshot(citations=(), error=None)["total_tokens"] == 3
        assert outer.snapshot(citations=(), error=None)["total_tokens"] == 0
