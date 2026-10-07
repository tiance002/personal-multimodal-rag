"""Text-free retrieval attribution; stable merging is never a new rank fusion."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


def pass_metadata(result: Any) -> dict[str, Any]:
    config = deepcopy(result.effective_config)
    fingerprint = (hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'),
                                            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
                   if config else None)
    query = getattr(result.query_plan, 'normalized', None)
    return {
        'query_fingerprint': hashlib.sha256(query.encode()).hexdigest() if isinstance(query, str) else None,
        'effective_config': config, 'configuration_fingerprint': fingerprint,
        'candidate_rankings': {name: [hit.model_dump(mode='json') for hit in hits]
                               for name, hits in result.candidate_rankings.items()},
        'fused_ranking': [hit.model_dump(mode='json') for hit in result.fused_ranking],
        'returned_items': [item.hit.model_dump(mode='json') for item in result.items],
        'context_items': [item.hit.model_dump(mode='json') for item in result.context_items],
        'context_max_per_document': result.context_max_per_document,
        'sources': list(result.sources), 'reason_codes': list(result.reason_codes),
        'retrieval_mode': result.retrieval_mode, 'route_reason': list(result.route_reason),
        'latency_ms': result.latency_ms, 'stage_latency_ms': deepcopy(result.stage_latency_ms),
        'degradation_flags': list(result.degradation_flags), 'embedding_cache_hit': result.embedding_cache_hit,
        'metadata_availability': {
            'configuration': 'PRESENT' if config else 'NOT_AVAILABLE',
            'candidate_rankings': 'PRESENT' if result.candidate_rankings else 'NOT_AVAILABLE',
            'fused_ranking': 'PRESENT' if result.fused_ranking else 'NOT_AVAILABLE',
            'latency': 'PRESENT' if result.latency_ms is not None else 'NOT_AVAILABLE',
        },
    }


def merge_provenance(first: Any, second: Any, combined: Any, *, max_items: int) -> dict[str, Any]:
    passes = [dict(pass_metadata(result), pass_index=index, role=role)
              for index, (role, result) in enumerate((('original', first), ('targeted', second)), 1)]
    origins: dict[str, list[dict[str, Any]]] = {}
    for row in passes:
        rankings = [('returned_items', row['returned_items']), ('fused_ranking', row['fused_ranking']),
                    ('context_items', row['context_items'])]
        rankings += [('candidate:' + name, hits) for name, hits in row['candidate_rankings'].items()]
        for name, hits in rankings:
            for position, hit in enumerate(hits, 1):
                origins.setdefault(hit['chunk_id'], []).append({
                    'pass_index': row['pass_index'], 'ranking': name,
                    'position': position, 'hit': deepcopy(hit),
                })
    first_ids = {item.chunk.chunk_id for item in first.items}
    fingerprints = [row['configuration_fingerprint'] for row in passes]
    return {
        'schema_version': 'retrieval-merge-provenance-v1',
        'ordering_semantics': 'stable_original_then_targeted_unique_bounded_not_reranked',
        'max_items': max_items, 'passes': passes, 'candidate_origins': origins,
        'configuration_consistent': fingerprints[0] == fingerprints[1] if all(fingerprints) else None,
        'merged_order': [{'chunk_id': item.chunk.chunk_id, 'merge_position': position,
                          'selected_from_pass': 1 if item.chunk.chunk_id in first_ids else 2}
                         for position, item in enumerate(combined, 1)],
    }
