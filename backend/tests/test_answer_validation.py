from backend.app.application.answer_validation import AnswerValidator
from backend.app.application.query_router import QueryRouter
from backend.app.domain.evidence import freeze_evidence


def _evidence():
    return (freeze_evidence("E1", "v1", "c1", "A方案成本1000元。", {"start": 0}),)


def test_amount_and_subject_must_match_the_cited_source():
    validator = AnswerValidator()
    plan = QueryRouter().plan("A 的成本是多少？")

    assert validator.validate("A方案成本1000元 [E1]", _evidence(), plan) is None
    assert validator.validate("A方案成本2000元 [E1]", _evidence(), plan) == "UNSUPPORTED_ANSWER"
    assert validator.validate("B方案成本1000元 [E1]", _evidence(), plan) == "UNSUPPORTED_ANSWER"


def test_factual_answer_without_citation_is_rejected():
    assert AnswerValidator().validate("A方案成本1000元。", _evidence(), QueryRouter().plan("A 的成本是多少？")) == "UNSUPPORTED_ANSWER"


def test_extra_unsupported_numeric_claim_is_rejected_even_when_requested_fact_is_correct():
    plan = QueryRouter().plan("A 的成本是多少？")

    assert AnswerValidator().validate(
        "A方案成本1000元 [E1]；B方案成本1000元 [E1]", _evidence(), plan,
    ) == "UNSUPPORTED_ANSWER"
