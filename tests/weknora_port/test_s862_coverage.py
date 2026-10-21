"""Finite frozen closed-rule contracts; no Provider, database, retrieval or network.

Gold slots were selected before registry integration. Agent review is not Owner
human approval, and this fixture says nothing about real retrieval/answer quality.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.weknora_port.retrieval.rule_registry import parse_rule
from backend.weknora_port.retrieval.strategy import plan_query


FIXTURE = Path(__file__).parent / "fixtures/s862_rule_cases.json"
FROZEN_SHA256 = "b2a1e8a299f78c692c87c40833317c56503c1049e5d48c6eaa124e8a69039d0a"
DOCUMENT = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = DOCUMENT["cases"]


def expected_queries(case):
    """Render the independently fixed gold slots using the declared rule format."""
    dimension = case["expected_dimension"]
    chinese = any("\u4e00" <= character <= "\u9fff" for character in case["q0"])
    if chinese:
        return [f"{name}的{dimension}是什么？" for name in case["expected_objects"]]
    return [f"What is known about {name} regarding {dimension}?" for name in case["expected_objects"]]


def _rule_record(match):
    return {
        "status": match.status, "matched": match.matched,
        "reject_reason": match.reject_reason, "rule_id": match.rule_id,
        "rule_version": match.rule_version, "objects": list(match.objects),
        "dimension": match.dimension, "object_spans": [list(span) for span in match.object_spans],
        "dimension_span": list(match.dimension_span) if match.dimension_span is not None else None,
        "full_match": match.full_match, "rendered_queries": list(match.render_queries()),
    }


def observe_case(case):
    """Capture real API outputs for the offline denominator report, without mocks."""
    q0 = case["q0"]
    local = parse_rule(q0)
    gated = parse_rule(q0, query_type=case["query_type"])
    plan = plan_query(q0, q0, case["query_type"])
    subqueries = [{"id": row.id, "query_text": row.query_text, "covers": list(row.covers),
                   "object_span": list(row.object_span) if row.object_span is not None else None,
                   "dimension_span": list(row.dimension_span) if row.dimension_span is not None else None}
                  for row in plan.subqueries]
    expected_slots = [[name, case["expected_dimension"]] for name in case["expected_objects"]]
    positive_exact = bool(case["expected_match"] and len(subqueries) == len(expected_slots)
        and [row["covers"] for row in subqueries] == expected_slots
        and [row["object_span"] for row in subqueries] == case["expected_object_spans"]
        and all(row["dimension_span"] == case["expected_dimension_span"] for row in subqueries)
        and [row["query_text"] for row in subqueries] == expected_queries(case))
    return {
        "id": case["id"], "q0": q0, "query_type": case["query_type"],
        "category": case["category"], "provenance": case["provenance"],
        "expected_match": case["expected_match"], "expected_local_match": case["expected_local_match"],
        "expected_rule": case["expected_rule"], "expected_objects": case["expected_objects"],
        "expected_dimension": case["expected_dimension"],
        "expected_object_spans": case["expected_object_spans"],
        "expected_dimension_span": case["expected_dimension_span"],
        "local_registry": _rule_record(local), "gated_registry": _rule_record(gated),
        "plan": {"q0": plan.q0, "effective_query": plan.effective_query,
                 "query_type": plan.query_type, "strategy": plan.strategy,
                 "reason": plan.reason, "subqueries": subqueries},
        "positive_exact_slots_and_spans": positive_exact,
        "negative_false_split": not case["expected_local_match"] and len(subqueries) > 1,
        "classification_mismatch": case["category"] == "classification_mismatch",
        "no_extra_queries": len(subqueries) == 1,
    }


def test_fixed_fixture_provenance_and_source_spans():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FROZEN_SHA256
    assert DOCUMENT["schema_version"] == "s862-closed-rule-cases/v1"
    assert DOCUMENT["owner_review"] == "NOT_RUN"
    assert len(CASES) == 66
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert sum(case["expected_match"] for case in CASES) == 24
    assert sum(not case["expected_local_match"] for case in CASES) == 31
    assert sum(case["category"] == "classification_mismatch" for case in CASES) == 11
    for case in CASES:
        assert case["reason"] and case["category"]
        assert case["owner_review"] == "NOT_RUN"
        assert case["provenance"] in {
            "OWNER_SPEC_LITERAL", "OWNER_SPEC_INSTANTIATION", "AGENT_CURATED_NOT_OWNER_REVIEWED"}
        if case["expected_local_match"]:
            assert len(case["expected_objects"]) in (2, 3)
            assert [case["q0"][slice(*span)] for span in case["expected_object_spans"]] == case["expected_objects"]
            assert case["q0"][slice(*case["expected_dimension_span"])] == case["expected_dimension"]
            assert all(left[1] <= right[0] for left, right in zip(
                case["expected_object_spans"], case["expected_object_spans"][1:]))
            assert case["expected_object_spans"][-1][1] <= case["expected_dimension_span"][0]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_frozen_case_registry_and_production_plan(case):
    record = observe_case(case)
    local, gated, plan = record["local_registry"], record["gated_registry"], record["plan"]
    assert local["matched"] is case["expected_local_match"], record
    assert local["status"] == ("MATCHED" if case["expected_local_match"] else "NOT_APPLICABLE"), record
    # None is the explicitly documented offline syntax diagnostic; it is not a
    # production classifier and may never promote the plan by itself.
    expected_gated = case["expected_match"] or (case["query_type"] is None and case["expected_local_match"])
    assert gated["matched"] is expected_gated, record
    assert plan["q0"] == case["q0"] and plan["effective_query"] == case["q0"], record
    assert 1 <= len(plan["subqueries"]) <= 3, record
    if case["expected_local_match"]:
        assert local["rule_id"] == case["expected_rule"], record
        assert local["rule_version"] == "1", record
        assert local["objects"] == case["expected_objects"], record
        assert local["dimension"] == case["expected_dimension"], record
        assert local["object_spans"] == case["expected_object_spans"], record
        assert local["dimension_span"] == case["expected_dimension_span"], record
        assert local["full_match"] == case["q0"], record
        assert local["rendered_queries"] == expected_queries(case), record
    else:
        assert local["reject_reason"], record
        assert not local["objects"] and not local["dimension"], record
        assert local["rendered_queries"] == [], record
    if case["expected_match"]:
        expected_strategy = "RULE_COMPARE" if case["expected_rule"] == "COMPARE_SHARED" else "RULE_PARALLEL"
        assert plan["strategy"] == expected_strategy, record
        assert record["positive_exact_slots_and_spans"], record
    else:
        assert plan["strategy"] == "PLAIN", record
        assert record["no_extra_queries"], record
        assert plan["subqueries"] == [{"id": "sq-1", "query_text": case["q0"],
            "covers": [case["q0"]], "object_span": None, "dimension_span": None}], record
