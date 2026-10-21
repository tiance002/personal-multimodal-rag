import pytest

from scripts.m4_result_contract import (
    aggregate_coverage, audit_bindings, execution_status, row_status, source_coverage,
)


@pytest.mark.parametrize("terminal", ["complete", "completed", "success"])
def test_complete_requires_persisted_assistant(terminal):
    assert execution_status(terminal_status=terminal, assistant={"is_completed": True}) == "COMPLETE"
    assert execution_status(terminal_status=terminal, assistant={"is_completed": False}) == "FAILED"


def test_unknown_and_historical_decisions_cannot_be_normalized_away():
    row = {"terminal_status": "complete", "assistant_message": {"is_completed": True},
           "provider_calls": [{"outcome": "UNKNOWN"}]}
    assert row_status(row) == "UNKNOWN"
    row["status"] = "NOT_RUN_HISTORICAL_UNKNOWN_REPLAY_PROHIBITED"
    assert row_status(row) == row["status"]


def test_failed_terminal_is_not_provider_unknown():
    assert execution_status(terminal_status="failed", assistant={"is_completed": True}) == "FAILED"
    assert execution_status(terminal_status=None, assistant={"is_completed": True}) == "PARTIAL"
    assert execution_status(terminal_status="complete", assistant={"is_completed": True},
                            api_error="disconnect", event_count=2) == "PARTIAL"


def test_any_and_all_sources_have_explicit_denominator():
    rows = [source_coverage(["a", "b"], ["a", "a"]), source_coverage(["c", "d"], ["c", "d"])]
    result = aggregate_coverage(rows)
    assert result["qid_denominator"] == 2
    assert result["any_source_hit_rate"] == 1
    assert result["all_required_sources_hit_rate"] == .5
    assert result["macro_required_source_recall"] == .75
    assert aggregate_coverage([source_coverage([], [])])["any_source_hit_rate"] is None


def test_bindings_are_not_trusted_from_forward_map_alone():
    case = {"qrels": {"a": 1, "b": 1}, "qrel_source_to_document": {"a": "d", "b": "d"},
            "document_to_source": {"d": "a"}, "expected_documents": [{"source_id": "a", "id": "d"}]}
    audit = audit_bindings(case)
    assert not audit["identity_consistent"]
    assert audit["conflicting_sources"] == ["b"]
    assert audit["duplicate_document_targets"] == ["d"]
    assert audit["content_projection_status"] == "NOT_VALIDATED"
