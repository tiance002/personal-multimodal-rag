"""Offline regression against saved M3 Omni capture and retry evidence."""
from pathlib import Path

import pytest

from scripts.rag_m3_score import load_executor_bundle, read, score_omni


M3_ROOT = Path('E:/WK/run/rag-m3-20261017')
P32_ROOT = Path('E:/WK/run/p32-20261017')


def test_saved_omni_stage_source_identity_and_failed_attempt_are_preserved():
    if not (M3_ROOT / 'cases' / 'omni-page007-A' / 'result.json').exists():
        pytest.skip('saved M3 Omni executor artifact is unavailable')

    plan = read(M3_ROOT / 'plan.json')
    p32_plan = read(P32_ROOT / 'plan.json')
    loaded, *_ = load_executor_bundle(M3_ROOT, plan, p32_plan, P32_ROOT)
    p32_case = next(row for row in p32_plan['cases'] if row['case_id'] == 'omni-page007')

    for variant in ('A', 'B'):
        record = next(row for row in loaded[('omni-page007', variant)]
                      if row.get('attempt_status') == 'COMPLETED')
        diagnostic = score_omni(p32_case, {}, record)
        assert diagnostic['target_source_id'] == 'PPT_lecture1_page_007.png'
        assert diagnostic['target_source_id_raw'].startswith('omnidocbench:')
        assert diagnostic['target_source_identity_in_stages'] == {
            'search': True, 'rerank': True, 'context': True,
        }
        assert diagnostic['target_ocr_chunk_ids'] == ['51316a20-67fd-4732-b520-c049999933d0']
        assert diagnostic['target_ocr_chunk_in_stages'] == {
            'search': True, 'rerank': False, 'context': False,
        }
        assert diagnostic['target_ocr_chunk_observed_in_evidence'] is False
        assert diagnostic['status'] == 'OCR_SOURCE_PRESENCE_DIAGNOSTIC_ONLY'

    attempts = loaded[('scifact-339', 'A')]
    old_gate_failure = next(row for row in attempts if row.get('attempt_number') == 1)
    retry = next(row for row in attempts if row.get('attempt_number') == 2)
    assert old_gate_failure['attempt_status'] == 'PRE_SEND_GATE_REJECTED'
    assert old_gate_failure['error_code'] == 'RUNTIME_DURABLE_GATE_REQUIRED'
    assert old_gate_failure['provider_calls'] == []
    assert retry['attempt_status'] == 'COMPLETED'
