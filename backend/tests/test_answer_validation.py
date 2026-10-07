from backend.app.application.answer_validation import AnswerValidator
import pytest

from backend.app.application.query_router import EvidencePlan, EvidenceTarget, QueryRouter
from backend.app.application.quality import QualityGate
from backend.app.domain.evidence import freeze_evidence
from backend.app.domain.models import ChunkRecord


@pytest.mark.parametrize('separator',['\u3002','\uff1b','\n'],ids=['period','semicolon','newline'])
def test_pure_native_numbered_two_target_answer_keeps_sentence_citations(separator):
    """SIMULATED complete native quotes; actual gate, snapshots and validator."""
    left='\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73'
    right='2. \u8bbe\u5907\u4e59\u5bbd\u5ea620\u5398\u7c73'
    plan=EvidencePlan('SIMULATED two native numeric targets',targets=(
        EvidenceTarget('\u8bbe\u5907\u7532','\u957f\u5ea6'),
        EvidenceTarget('\u8bbe\u5907\u4e59','\u5bbd\u5ea6')),kind='parallel')
    locators=[{'kind':'text','parse_status':'complete','quote':text} for text in (left,right)]
    chunks=[ChunkRecord(f'c{i}','SIMULATED-kb',f'doc{i}','v1',text,locators[i])
        for i,text in enumerate((left,right))]
    assert QualityGate().evaluate_chunks(chunks,plan).accepted
    snapshots=tuple(freeze_evidence(f'E{i+1}','v1',f'c{i}',text,locators[i])
        for i,text in enumerate((left,right)))
    answer=left+'[E1]'+separator+right+'[E2]'
    validator=AnswerValidator()
    assert validator.validate(answer,snapshots,plan) is None
    assert validator.validate(answer.replace('20','21'),snapshots,plan)=='UNSUPPORTED_ANSWER'
    assert validator.validate(answer.replace('[E1]','[E2]'),snapshots,plan)=='UNSUPPORTED_ANSWER'
    assert validator.validate(answer.replace('[E2]','[E99]'),snapshots,plan)=='INVALID_CITATION'


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
