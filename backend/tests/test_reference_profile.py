from backend.app.application.retrieval_profile import reference_profile
from backend.app.application.retrieval_policy import RetrievalRouter


def test_reference_profile_has_provenance_for_every_parameter():
    profile = reference_profile()
    assert set(profile["parameters"]) == set(profile["parameter_sources"])
    assert profile["parameters"]["rrf_k"] == 60
    assert profile["parameters"]["source_weights"] == {"vector": 0.7, "keyword": 0.3}
    assert profile["thresholds"]["copied_from_reference"] is False
    assert profile["parameters"]["rerank_enabled"] is False


def test_adaptive_offline_arm_selection_uses_same_product_router():
    assert RetrievalRouter("adaptive").route("Explain semantic similarity").mode == "vector"
    assert RetrievalRouter("adaptive").route('定位 "chunk_id" 定义').mode == "hybrid"
