from backend.app.application.quality import QualityGate, QualityReason
from backend.app.application.retrieval import RetrievalResult
from backend.app.domain.text_normalization import normalize_query


def test_quality_gate_reports_no_candidates():
    decision = QualityGate().evaluate(RetrievalResult(query_plan=normalize_query("无结果"), items=[], sources=()))

    assert decision.accepted is False
    assert decision.reason == QualityReason.NO_CANDIDATES
