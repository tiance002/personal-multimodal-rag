from backend.app.domain.text_normalization import normalize_query, term_frequencies


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


def test_term_frequencies_count_occurrences_not_just_presence():
    counts = term_frequencies("事务回滚 事务回滚 事务")

    assert counts["事务回滚"] == 2
    assert counts["事务"] == 3
    assert counts["回滚"] == 2


def test_normalize_query_deduplicates_but_term_frequencies_do_not():
    text = "回滚 回滚"

    assert normalize_query(text).terms.count("回滚") == 1
    assert term_frequencies(text)["回滚"] == 2
