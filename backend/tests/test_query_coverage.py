from backend.app.application.query_router import QueryRouter
from backend.app.application.quality import QualityGate, QualityReason
from backend.app.application.retrieval import RetrievalItem, RetrievalResult
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.text_normalization import normalize_query


def _result(*contents: str) -> RetrievalResult:
    items = [
        RetrievalItem(
            ChunkRecord(f"c{index}", "kb", f"doc{index}", "version", content, {"start": 0}),
            RankedHit(chunk_id=f"c{index}", rank=index),
        )
        for index, content in enumerate(contents, start=1)
    ]
    return RetrievalResult(normalize_query("A与B各自的成本是多少"), items, ("keyword",))


def test_explicit_parallel_question_creates_distinct_evidence_targets():
    plan = QueryRouter().plan("A 与 B 两种方案各自的准确成本是多少？")

    assert plan.original_query == "A 与 B 两种方案各自的准确成本是多少？"
    assert [(target.subject, target.attribute) for target in plan.targets] == [("A", "成本"), ("B", "成本")]


def test_relation_question_is_not_split_into_two_answers():
    plan = QueryRouter().plan("A 与 B 有什么关系？")

    assert plan.targets == ()
    assert plan.kind == "relation"
    assert plan.relation_subjects == ("A", "B")


def test_relation_requires_an_explicit_relationship_not_two_unrelated_values():
    plan = QueryRouter().plan("A 与 B 有什么关系？")

    decision = QualityGate().evaluate(_result("A方案成本1000元。B方案成本1200元。"), plan)

    assert decision.accepted is False
    assert decision.reason == QualityReason.INSUFFICIENT_EVIDENCE


def test_cost_difference_requires_both_values_as_one_comparison():
    plan = QueryRouter().plan("A 与 B 的成本差额是多少？")

    assert plan.kind == "comparison"
    assert [(target.subject, target.attribute) for target in plan.targets] == [("A", "成本"), ("B", "成本")]


def test_single_exact_fact_has_one_target():
    plan = QueryRouter().plan("A 的成本是多少？")

    assert [(target.subject, target.attribute) for target in plan.targets] == [("A", "成本")]


def test_textual_attributes_do_not_enter_numeric_only_coverage_rule():
    assert QueryRouter().plan("A 的目标是什么？").targets == ()
    assert QueryRouter().plan("A 与 B 两种方案各自的优势是什么？").targets == ()


def test_quality_gate_requires_subject_attribute_value_in_same_fact():
    plan = QueryRouter().plan("A 与 B 两种方案各自的成本是多少？")

    decision = QualityGate().evaluate(_result("A方案成本1000元。B方案维护费300元。"), plan)

    assert decision.accepted is False
    assert decision.reason == QualityReason.INSUFFICIENT_EVIDENCE
    assert [target.subject for target in decision.missing_targets] == ["B"]


def test_quality_gate_accepts_evidence_spread_across_documents():
    plan = QueryRouter().plan("A 与 B 两种方案各自的成本是多少？")

    decision = QualityGate().evaluate(_result("A方案成本1000元。", "B方案成本1200元。"), plan)

    assert decision.accepted is True
    assert decision.missing_targets == ()
