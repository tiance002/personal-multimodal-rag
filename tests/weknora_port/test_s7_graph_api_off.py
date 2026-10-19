"""Offline API-off reconstruction proof guards; synthetic saved snapshots."""
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from scripts.s7_graph_api_off import ordinary_after_top_k, sha
from tests.weknora_port.test_graph_retrieval import chain, source_row


def capture():
    rows = [asdict(source_row(batch.source)) for batch in chain()]
    graph = deepcopy(rows[1])
    graph['match_type'] = 'graph'
    return {'stages': [
        {'stage': 'merge', 'status': 'completed', 'state': {'merge_results': rows}},
        {'stage': 'graph_fusion', 'status': 'completed', 'state': {'merge_results': [rows[0], graph]}}]}


def test_actual_fusion_snapshot_recovers_original_post_top_k_not_all_merge_candidates():
    data = capture()
    ordinary, _ = ordinary_after_top_k(data)
    assert len(ordinary) == 1 and ordinary[0]['id'] == chain()[0].source.chunk_id
    assert len(data['stages'][0]['state']['merge_results']) == 2


def test_changed_ordinary_source_is_not_silently_treated_as_exact_replay():
    data = capture()
    data['stages'][1]['state']['merge_results'][0] = deepcopy(data['stages'][1]['state']['merge_results'][0])
    data['stages'][1]['state']['merge_results'][0]['content'] += ' invented text'
    with pytest.raises(ValueError, match='ORDINARY_POST_TOP_K_NOT_PROVABLE'):
        ordinary_after_top_k(data)


def test_empty_ordinary_result_requires_saved_native_fallback_instead_of_fabricating_one():
    data = capture()
    data['stages'][1]['state']['merge_results'] = data['stages'][1]['state']['merge_results'][1:]
    with pytest.raises(ValueError, match='FALLBACK_PREPARATION_NOT_CAPTURED'):
        ordinary_after_top_k(data)


def test_wire_sha_uses_the_actual_transport_serialization():
    import hashlib
    body = {'messages': [{'role': 'user', 'content': '原始问题'}], 'model': 'configured'}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    assert sha(body) == hashlib.sha256(encoded).hexdigest()
