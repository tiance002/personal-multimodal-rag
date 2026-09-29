from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import RetrievalItem
from backend.app.domain.models import ChunkRecord, RankedHit


def item(number: int, content: str, *, document: str = "doc", version: str = "v1") -> RetrievalItem:
    chunk = ChunkRecord(f"c{number}", "kb", document, version, content)
    return RetrievalItem(chunk, RankedHit(chunk_id=chunk.chunk_id, rank=number))


def test_selection_trace_records_budget_rejection_and_continues_to_later_items():
    selected, trace = ContextBuilder(max_chars=20).select_with_trace(
        [item(1, "a"), item(2, "x" * 20), item(3, "b")]
    )

    assert [entry.chunk.chunk_id for entry in selected] == ["c1", "c3"]
    assert [step["reason"] for step in trace] == [None, "CHAR_BUDGET", None]
    assert [step["selected"] for step in trace] == [True, False, True]
    assert [step["used_chars_before"] for step in trace] == [0, 6, 6]
    assert trace[-1]["used_chars_after"] == len("[E1] a\n\n[E2] b")


def test_selection_trace_preserves_distinct_chunks_from_same_document_and_parent():
    candidates = [item(1, "a", document="parent#1"),
                  item(2, "b", document="parent#1"),
                  item(3, "c", document="parent#2")]
    selected, trace = ContextBuilder(max_chars=80).select_with_trace(candidates)

    assert len(selected) == 3
    assert all(step["selected"] and step["reason"] is None for step in trace)


def test_selection_trace_handles_empty_and_underfilled_candidates():
    builder = ContextBuilder(max_chars=80)
    assert builder.select_with_trace([]) == ([], [])
    selected, trace = builder.select_with_trace([item(1, "a")])
    assert len(selected) == len(trace) == 1
    assert trace[0]["used_chars_after"] == len("[E1] a")
