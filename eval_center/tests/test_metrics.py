import importlib
import importlib.util
import math

import pytest


def subject():
    assert importlib.util.find_spec('eval_center.metrics') is not None, 'independent rank metrics are not implemented'
    return importlib.import_module('eval_center.metrics')


def test_rank_formulas_match_hand_calculated_multiple_evidence():
    m = subject()
    result = m.ranking_metrics(['x', 'a', 'y', 'b'], {'a': 2, 'b': 1}, k=5)
    assert result['recall_at_k'] == 1
    assert result['precision_at_k'] == pytest.approx(2/5)
    assert result['f1_at_k'] == pytest.approx(4/7)
    assert result['hit_at_k'] == 1
    assert result['mrr_at_k'] == 0.5
    assert result['map_at_k'] == 0.5
    expected = (3/math.log2(3) + 1/math.log2(5)) / (3 + 1/math.log2(3))
    assert result['ndcg_at_k'] == pytest.approx(expected)


def test_duplicate_evidence_consumes_rank_without_extra_credit():
    m = subject()
    result = m.ranking_metrics(['a', 'a', 'a', 'b'], {'a': 1, 'b': 1}, k=3)
    assert result['recall_at_k'] == 0.5
    assert result['precision_at_k'] == pytest.approx(1/3)
    assert result['map_at_k'] == 0.5
    assert result['ndcg_at_k'] <= 1


def test_fewer_than_k_and_empty_results_use_fixed_k_denominator():
    m = subject()
    assert m.ranking_metrics(['a'], {'a': 1}, k=5)['precision_at_k'] == 0.2
    result = m.ranking_metrics([], {'a': 1}, k=5)
    for name in m.RANK_METRICS:
        assert result[name] == 0


def test_no_answer_cases_do_not_pollute_rank_averages():
    m = subject()
    answerable = m.ranking_metrics(['a'], {'a': 1}, k=5)
    unanswerable = m.ranking_metrics([], {}, k=5)
    assert all(unanswerable[name] is None for name in m.RANK_METRICS)
    assert unanswerable['no_answer_retrieval_empty'] == 1
    summary = m.aggregate_metrics([answerable, unanswerable])
    assert summary['metrics']['recall_at_k'] == 1
    assert summary['counts']['recall_at_k'] == {'evaluated': 1, 'unavailable': 1}
    assert summary['metrics']['no_answer_retrieval_empty'] == 1


def test_grades_and_k_are_validated_not_coerced():
    m = subject()
    for k in (0, -1, True):
        with pytest.raises(ValueError):
            m.ranking_metrics([], {'a': 1}, k=k)
    for grade in (-1, 4, 0.5, True):
        with pytest.raises(ValueError):
            m.ranking_metrics(['a'], {'a': grade}, k=5)


def test_missing_usage_stays_unavailable_and_quantiles_are_explicit():
    m = subject()
    summary = m.aggregate_metrics([{'latency_ms': 10, 'tokens_in': None}, {'latency_ms': 30, 'tokens_in': None}])
    assert summary['metrics']['tokens_in'] is None
    assert summary['counts']['tokens_in']['evaluated'] == 0
    assert summary['metrics']['latency_ms_p50'] == 20
    assert summary['metrics']['latency_ms_p95'] == 29
    assert summary['metrics']['latency_ms_p99'] == pytest.approx(29.8)


def test_nonfinite_metrics_are_rejected():
    m = subject()
    with pytest.raises(ValueError):
        m.aggregate_metrics([{'recall_at_k': float('nan')}])
