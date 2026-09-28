"""Recompute deterministic claims from sanitized case sufficient statistics.

This proves consistency of statistics/configuration/claims, not the truth of
privately held source annotations. File hashing remains a separate integrity
check. The v2 importer additionally validates privacy/schema and code identity.
"""
from __future__ import annotations

import math
from typing import Any

from eval_center.gold import union_length
from eval_center.metrics import aggregate_metrics, ranking_metrics
from eval_center.quality import quality_metrics
from eval_center.source_metrics import source_case_metrics
from eval_center.telemetry import telemetry_metrics

STAGES = ('keyword', 'vector', 'fused', 'context')


class ExperimentInvalidError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__('experiment is INVALID: ' + code)


def _invalid(code: str) -> None:
    raise ExperimentInvalidError(code)


def compute_case_metrics(statistics: dict[str, Any], effective_config: dict[str, Any]) -> dict[str, float | int | None]:
    try:
        if 'gold_sources' in statistics:
            result=source_case_metrics(statistics,effective_config)
            if 'quality' in statistics: result.update(quality_metrics(statistics['quality']))
            if 'telemetry' in statistics: result.update(telemetry_metrics(statistics['telemetry']))
            return result
        required={'qrels','rankings','coverage'}
        if not required <= set(statistics) or set(statistics)-required-{'quality','telemetry'}:
            _invalid('invalid_statistics')
        rankings = statistics['rankings']
        if not isinstance(rankings, dict) or set(rankings) != set(STAGES):
            _invalid('invalid_rank_stages')
        result = {}
        for stage in STAGES:
            k = effective_config['candidate_k'] if stage in ('keyword', 'vector') else effective_config['top_k']
            if len(rankings[stage]) > k:
                _invalid('ranking_exceeds_effective_limit')
            result.update({f'{stage}.{name}': value for name, value in
                           ranking_metrics(rankings[stage], statistics['qrels'], k=k).items()})
        source = statistics['coverage']
        if not isinstance(source, dict) or set(source) != {'gold_lengths', 'intervals', 'threshold'}:
            _invalid('invalid_coverage_statistics')
        lengths, intervals, threshold = source['gold_lengths'], source['intervals'], source['threshold']
        if not isinstance(lengths, dict) or not isinstance(intervals, dict) or set(lengths) != set(intervals):
            _invalid('invalid_coverage_statistics')
        if type(threshold) not in (int, float) or not 0 < threshold <= 1:
            _invalid('invalid_coverage_statistics')
        fractions = []
        for key, length in lengths.items():
            if not isinstance(key, str) or not key or type(length) is not int or length <= 0:
                _invalid('invalid_coverage_statistics')
            spans = intervals[key]
            if not isinstance(spans, list):
                _invalid('invalid_coverage_statistics')
            for span in spans:
                if not isinstance(span, list) or len(span) != 2:
                    _invalid('invalid_coverage_statistics')
                left, right = span
                if type(left) is not int or type(right) is not int or not 0 <= left < right <= length:
                    _invalid('invalid_coverage_statistics')
            fractions.append(union_length((tuple(span) for span in spans))/length)
        result['context_recall'] = sum(value+1e-12 >= threshold for value in fractions)/len(fractions) if fractions else None
        result['evidence_coverage'] = sum(fractions)/len(fractions) if fractions else None
        if 'quality' in statistics:
            result.update(quality_metrics(statistics['quality']))
        if 'telemetry' in statistics:
            result.update(telemetry_metrics(statistics['telemetry']))
        return result
    except ExperimentInvalidError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        _invalid('invalid_statistics')


def _same_metrics(claimed: Any, calculated: dict[str, Any], code: str) -> None:
    if not isinstance(claimed, dict) or set(claimed) != set(calculated):
        _invalid(code)
    for key, expected in calculated.items():
        actual = claimed[key]
        if expected is None:
            if actual is not None:
                _invalid(code)
        elif type(actual) not in (int, float) or not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
            _invalid(code)


def verify_report(report: dict[str, Any]) -> dict[str, Any]:
    """Validate execution declarations, independently recompute, return official values.

    Invalid reports are rejected, not downgraded into an apparently successful
    experiment. Callers can preserve rejection machine codes in a local
    diagnostic report; no rejected free text needs to be sent to the server.
    """
    try:
        runtime = report['runtime']
        effective = runtime['effective_config']
        if report['config'] != effective:
            _invalid('effective_config_mismatch')
        for field in ('git_sha', 'corpus_hash', 'gold_set_hash', 'index_version'):
            if report['manifest'][field] != runtime[field]:
                _invalid('manifest_runtime_mismatch')
        cases = report['cases']
        sample_count = report['manifest']['sample_count']
        if type(sample_count) is not int or sample_count != len(cases):
            _invalid('sample_count_mismatch')
        if len({case['case_id'] for case in cases}) != len(cases):
            _invalid('duplicate_case_id')
        recomputed = []
        for case in cases:
            calculated = compute_case_metrics(case['statistics'], effective)
            _same_metrics(case['metrics'], calculated, 'case_metrics_mismatch')
            recomputed.append(calculated)
        summary = aggregate_metrics(recomputed)
        _same_metrics(report['metrics'], summary['metrics'], 'summary_mismatch')
        if report['metric_counts'] != summary['counts']:
            _invalid('metric_counts_mismatch')
        return summary
    except ExperimentInvalidError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        _invalid('invalid_experiment')
