"""Deterministic rank formula primitives shared by local evaluation and ECS.

Qrels must describe the *same explicit result unit* as ranked IDs. Source Gold
coverage is separate (gold.coverage); it must not be silently presented as
chunk recall. Overlapping source evidence is converted to stable units before
calling these primitives. Repeated IDs consume rank slots and receive no gain.
"""
from __future__ import annotations

import math
import statistics
from typing import Any

RANK_METRICS = ('recall_at_k', 'precision_at_k', 'f1_at_k', 'hit_at_k',
                'mrr_at_k', 'map_at_k', 'ndcg_at_k')


def ranking_metrics(ranked_ids: list[str], qrels: dict[str, int], *, k: int) -> dict[str, float | int | None]:
    if type(k) is not int or not 1 <= k <= 10000:
        raise ValueError('k must be an integer in [1,10000]')
    if not isinstance(qrels, dict) or any(not isinstance(key, str) or not key for key in qrels):
        raise ValueError('invalid relevance identifiers')
    if any(type(grade) is not int or not 0 <= grade <= 3 for grade in qrels.values()):
        raise ValueError('relevance grades must be integers in [0,3]')
    if not isinstance(ranked_ids, list) or any(not isinstance(key, str) or not key for key in ranked_ids):
        raise ValueError('invalid ranked identifiers')
    relevant = {key: grade for key, grade in qrels.items() if grade > 0}
    ranked = ranked_ids[:k]
    if not relevant:
        return {**dict.fromkeys(RANK_METRICS), 'retrieved_count_at_k': len(ranked),
                'no_answer_retrieval_empty': int(not ranked)}
    seen = set()
    grades = []
    for key in ranked:
        grades.append(relevant.get(key, 0) if key not in seen else 0)
        seen.add(key)
    hit_ranks = [rank for rank, grade in enumerate(grades, 1) if grade]
    recall = len(hit_ranks) / len(relevant)
    precision = len(hit_ranks) / k
    ap = sum(number/rank for number, rank in enumerate(hit_ranks, 1)) / min(k, len(relevant))
    dcg = sum((2**grade-1)/math.log2(rank+1) for rank, grade in enumerate(grades, 1))
    ideal = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum((2**grade-1)/math.log2(rank+1) for rank, grade in enumerate(ideal, 1))
    return {'recall_at_k': recall, 'precision_at_k': precision,
            'f1_at_k': 2*precision*recall/(precision+recall) if precision+recall else 0.0,
            'hit_at_k': int(bool(hit_ranks)), 'mrr_at_k': 1/hit_ranks[0] if hit_ranks else 0.0,
            'map_at_k': ap, 'ndcg_at_k': dcg/idcg,
            'retrieved_count_at_k': len(ranked), 'no_answer_retrieval_empty': None}


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered)-1)*quantile
    left = math.floor(position)
    right = math.ceil(position)
    return ordered[left] + (ordered[right]-ordered[left])*(position-left)


def aggregate_metrics(cases: list[dict[str, float | int | None]]) -> dict[str, Any]:
    """Macro means with explicit evaluated/unavailable counts; no imputed zeros.

    Latency percentiles use linear interpolation (type 7) over actual timings,
    retaining counts so a tiny sample cannot masquerade as a robust benchmark.
    """
    if not isinstance(cases, list) or any(not isinstance(row, dict) for row in cases):
        raise ValueError('case metrics must be objects')
    names = sorted({key for row in cases for key in row})
    metrics: dict[str, float | None] = {}
    counts: dict[str, dict[str, int]] = {}
    for name in names:
        if not isinstance(name, str):
            raise ValueError('metric names must be strings')
        values = []
        for row in cases:
            value = row.get(name)
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('metrics must be finite numbers or unavailable')
            values.append(float(value))
        metrics[name] = statistics.mean(values) if values else None
        counts[name] = {'evaluated': len(values), 'unavailable': len(cases)-len(values)}
        if name.endswith('_ms'):
            for suffix, quantile in [('p50', 0.5), ('p95', 0.95), ('p99', 0.99)]:
                key = name + '_' + suffix
                metrics[key] = percentile(values, quantile)
                counts[key] = dict(counts[name])
    return {'metrics': metrics, 'counts': counts}
