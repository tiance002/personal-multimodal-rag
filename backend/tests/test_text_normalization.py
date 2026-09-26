from backend.app.domain.text_normalization import normalize_query


def test_normalization_preserves_q0_and_adds_chinese_bigrams():
    plan = normalize_query("  事务回滚  ")

    assert plan.q0 == "  事务回滚  "
    assert "事务" in plan.terms
    assert "回滚" in plan.terms
    assert "务回" in plan.terms


def test_normalization_keeps_special_tokens_and_is_deterministic():
    first = normalize_query("API_v2 / p95 >= 8s")
    second = normalize_query("API_v2 / p95 >= 8s")

    assert first == second
    assert "api_v2" in first.terms
    assert "p95" in first.terms
    assert ">=" in first.terms
