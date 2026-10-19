"""Pure inventory contracts, no infrastructure or Provider."""
from scripts.s8_text_contracts import historical_status, positive_sources


def test_qrel_zero_is_never_positive_and_relevance_is_not_a_veracity_label():
    assert positive_sources({'unrelated': 0, 'relevant': 1, 'graded': 2}) == ['graded', 'relevant']
    assert positive_sources({'negative': -1, 'zero': '0'}) == []


def test_old_failed_status_does_not_override_actual_sse_and_persisted_completion():
    row = {'status': 'FAILED', 'terminal_status': 'complete', 'unknown_call_count': 0,
           'assistant_message': {'is_completed': True, 'turn_status': 'completed', 'turn_error_code': ''}}
    assert historical_status(row) == 'COMPLETE_DERIVED_FROM_SSE_AND_PERSISTENCE'
    assert row['status'] == 'FAILED'
    row['unknown_call_count'] = 1
    assert historical_status(row) == 'UNKNOWN'


def test_not_run_or_error_cannot_become_completed_from_terminal_marker():
    assert historical_status({'status': 'NOT_RUN_HISTORICAL_UNKNOWN_REPLAY_PROHIBITED'}) == 'NOT_RUN_HISTORICAL_UNKNOWN_REPLAY_PROHIBITED'
    assert historical_status({'terminal_status': 'complete', 'assistant_message': {'is_completed': False}}) == 'UNVERIFIED'
    assert historical_status({'terminal_status': 'complete', 'assistant_message': {'is_completed': True,
                              'turn_error_code': 'FAILED'}}) == 'FAILED_OR_CANCELLED'
