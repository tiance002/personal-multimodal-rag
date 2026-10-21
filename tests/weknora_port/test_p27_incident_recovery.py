"""Pinned incident recovery never settles UNKNOWN or changes the budget."""
from copy import deepcopy
import pytest
from scripts import weknora_port_p27_recover as recovery


@pytest.fixture
def before(monkeypatch):
    row = dict(request_id='real/answer/1/cheap', wire_sha256=recovery.WIRE,
               wire_bytes=195, state='unknown', held_tokens=259, actual_total=None,
               reserved_output=64, known_lower_bound=221)
    value = dict(month=dict(blocked_reason=recovery.REASON, limit_tokens=1_000_000,
                            carry_in_tokens=0, approval_source='unchanged-owner'),
                 calls=[row], slots=[dict(call_id='preserved-logical-slot')])
    monkeypatch.setattr(recovery, 'CALLS', recovery.evidence.digest(value['calls']))
    return value


def test_recovery_changes_only_block_reason_and_retains_reservation(before):
    original = deepcopy(before)
    after = recovery.decision(before)
    assert before == original
    assert after['calls'] == before['calls']
    assert after['slots'] == before['slots']
    assert after['month'] == {**before['month'], 'blocked_reason': ''}


@pytest.mark.parametrize('field,value', [('state', 'settled'), ('held_tokens', 221),
                                      ('actual_total', 221), ('wire_sha256', 'other')])
def test_modified_historical_row_is_ineligible(before, field, value):
    before['calls'][0][field] = value
    with pytest.raises(RuntimeError, match='HISTORICAL_LEDGER_CHANGED'):
        recovery.decision(before)


def test_unrelated_month_block_is_not_cleared(before):
    before['month']['blocked_reason'] = 'OTHER_SECURITY_INCIDENT'
    with pytest.raises(RuntimeError, match='BLOCK_REASON_CHANGED'):
        recovery.decision(before)


def test_repaired_contract_refuses_historical_wrong_cap():
    assert recovery.repaired_contract() == dict(correct_cap='max_tokens', output_upper=64,
                                                legacy_wire='DENIED')
