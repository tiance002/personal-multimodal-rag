from backend.app.application.context_builder import ContextBuilder
from backend.app.domain.models import RankedHit
from eval_center.gold import SourceSpan
from eval_center.public_trace import build_stage_context_trace, read_stage_context_trace


def index():
    return {
        "spans": {
            "c1": SourceSpan("parent#1", "a" * 64, 0, 1, kind="markdown"),
            "c2": SourceSpan("parent#1", "a" * 64, 2, 3, kind="markdown"),
            "c3": SourceSpan("parent#2", "b" * 64, 0, 1, kind="markdown"),
            "c4": SourceSpan("other#1", "c" * 64, 0, 1, kind="markdown"),
        },
        "chunks": [
            {"chunk_id": "c1", "document_id": "db-1", "version_id": "v1", "content": "a", "locator": {"kind": "markdown"}},
            {"chunk_id": "c2", "document_id": "db-1", "version_id": "v1", "content": "x" * 20, "locator": {"kind": "markdown"}},
            {"chunk_id": "c3", "document_id": "db-2", "version_id": "v2", "content": "b", "locator": {"kind": "markdown"}},
            {"chunk_id": "c4", "document_id": "db-3", "version_id": "v3", "content": "c", "locator": {"kind": "markdown"}},
        ],
    }


def test_trace_separates_budget_top_k_and_same_parent_occupancy():
    ranking = [RankedHit(chunk_id=f"c{number}", rank=number) for number in range(1, 5)]
    context, trace = build_stage_context_trace(
        "qid", "hybrid", ranking, index(), ContextBuilder(20), top_k=3,
        effective_config={"candidate_k": 4, "rrf_k": 60, "top_k": 3,
                          "context_budget_chars": 20, "chunk_size": 1200, "chunk_overlap": 120},
    )
    steps = trace["candidates"]
    assert [step["reason"] for step in steps] == [None, "CHAR_BUDGET", None, "TOP_K_LIMIT"]
    assert [step["source_document_id"] for step in steps] == ["parent#1", "parent#1", "parent#2", "other#1"]
    assert trace["selected_chunk_ids"] == ["c1", "c3"]
    assert trace["selected_source_ids"] == ["parent#1", "parent#2"]
    assert trace["selected_parent_ids"] == ["parent"]
    assert trace["rendered_chars"] == len(context["text"])
    assert trace["stage"] == "hybrid"
    assert trace["effective_config"]["top_k"] == 3
    assert steps[0]["source_version"] == "a" * 64
    assert steps[0]["version_id"] == "v1"
    assert steps[0]["locator"]["kind"] == "markdown"
    assert steps[0]["locator"]["start"] == 0


def test_trace_handles_empty_and_fewer_than_top_k():
    config = {"top_k": 5, "context_budget_chars": 80}
    empty, trace = build_stage_context_trace("q", "vector", [], index(), ContextBuilder(80),
                                             top_k=5, effective_config=config)
    assert empty["selected_chunk_ids"] == []
    assert trace["candidates"] == []
    one, trace = build_stage_context_trace("q", "vector", [RankedHit(chunk_id="c1", rank=1)],
                                           index(), ContextBuilder(80), top_k=5, effective_config=config)
    assert one["selected_chunk_ids"] == ["c1"]
    assert len(trace["candidates"]) == 1


def test_legacy_variant_without_trace_is_explicitly_insufficient():
    assert read_stage_context_trace({"context_document_rankings": {"hybrid": ["doc"]}}, "hybrid") == {
        "status": "INSUFFICIENT_TRACE"
    }
