"""Synthetic counter contracts only; no cloud, service, or database."""
import copy
import json
from types import SimpleNamespace

import pytest

from scripts import s8_overnight_ingestion as base
from scripts.s8_execution_counter import ExecutionCounter, install
from tests.weknora_port.test_s8_overnight_ingestion import ledger_fixture


def fixture(tmp_path):
    plan, row, filename, ledger, auth, parent = ledger_fixture(tmp_path)
    # Do not monkeypatch the shared imported runner module in tests.
    module = SimpleNamespace(**{n: getattr(base, n) for n in
        ('parent_state', 'verify_execution', 'read', 'MODEL_ID', 'sha', 'source_key')})
    return ExecutionCounter(module), plan, row, filename, ledger, auth, parent


def settle(filename, ledger, tokens=123):
    model = ledger['allowed_models'][0]
    request = 'synthetic-only-operation'
    ledger['entries'] = [{**model, 'id': base.sha(json.dumps([request, 'embedding']).encode()),
        'request_id': request, 'wire_sha256': 'b' * 64, 'reserved_tokens': 2048,
        'held_tokens': tokens, 'status': 'SETTLED', 'outcome': 'RESPONSE_RECEIVED',
        'usage': {'status': 'REPORTED', 'total_tokens': tokens,
                  'prompt_tokens': tokens, 'completion_tokens': 0}}]
    base.write(filename, ledger)


def test_normal_settlement_accumulates_and_full_final_scan(tmp_path):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    p, calls, tokens = c.parent_state(auth, plan)
    assert c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), calls, tokens) == path
    settle(path, ledger)
    assert c.parent_state(auth, plan)[1:] == (1, 123)
    assert c.finish() == (1, 123)


def test_unknown_is_not_released_or_hidden(tmp_path):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    p, a, b = c.parent_state(auth, plan)
    c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), a, b)
    settle(path, ledger)
    ledger['entries'][0].update(status='UNKNOWN', held_tokens=2048, usage=None)
    ledger['status'] = 'STOP'
    base.write(path, ledger); before = path.read_bytes()
    with pytest.raises(ValueError, match='UNKNOWN_OR_INFLIGHT'):
        c.parent_state(auth, plan)
    assert before == path.read_bytes()


@pytest.mark.parametrize('where', ['parent', 'ledger', 'row'])
def test_changed_budgets_are_rejected(tmp_path, where):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    p, a, b = c.parent_state(auth, plan)
    if where == 'parent':
        parent['max_tokens'] += 1; base.write(auth, parent)
        with pytest.raises(ValueError, match='PARENT_CONTRACT'):
            c.parent_state(auth, plan)
    else:
        if where == 'ledger':
            ledger['max_tokens'] += 1; base.write(path, ledger)
        else:
            row = copy.deepcopy(row); row['token_reservation_upper'] -= 1
        with pytest.raises(ValueError, match='FROZEN'):
            c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), a, b)


def test_duplicate_active_refuses_even_without_send(tmp_path):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    p, a, b = c.parent_state(auth, plan)
    c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), a, b)
    with pytest.raises(ValueError, match='DUPLICATE_ACTIVE'):
        c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), a, b)
    c.parent_state(auth, plan)
    with pytest.raises(ValueError, match='DUPLICATE_ACTIVE'):
        c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), a, b)


def test_preallocation_must_fit_parent_and_exact_row(tmp_path):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    parent['max_tokens'] = 4999; base.write(auth, parent)
    with pytest.raises(ValueError, match='PREALLOCATION'):
        c.parent_state(auth, plan)


def test_final_scan_detects_untouched_file_change(tmp_path):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    c.parent_state(auth, plan)
    settle(path, ledger)
    with pytest.raises(ValueError, match='FINAL_COUNTER'):
        c.finish()


def test_completed_original_runner_closure_is_exactly_reconciled(tmp_path):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    p, a, b = c.parent_state(auth, plan)
    c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), a, b)
    settle(path, ledger)
    assert c.parent_state(auth, plan)[1:] == (1, 123)
    ledger['status'] = 'CLOSED'; base.write(path, ledger)
    parent.update(status='CLOSED', actual_calls=1, actual_reported_tokens=123,
        unused_calls_withdrawn=0, unused_token_space_withdrawn=4877)
    base.write(auth, parent)
    assert c.finish() == (1, 123)


def test_middle_document_does_not_read_all_ledgers_again(tmp_path):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    for number in range(1, 6):
        extra = copy.deepcopy(row); extra['source_sha256'] = str(number) * 64
        other = tmp_path / f'file-{number}.json'
        base.write(other, ledger)
        parent['executions'][base.source_key(extra)] = str(other)
        plan['sources'].append(extra)
    parent.update(max_calls=6, max_tokens=30000, plan_sha256=base.sha(base.canonical(plan).encode()))
    base.write(auth, parent)
    seen = []; original = c.module.read
    c.module.read = lambda p: (seen.append(str(p)), original(p))[1]
    p, a, b = c.parent_state(auth, plan)
    c.verify_execution(p, plan, row, SimpleNamespace(subject_id='synthetic-owner'), a, b)
    settle(path, ledger); seen.clear()
    assert c.parent_state(auth, plan)[1:] == (1, 123)
    assert seen == [str(auth), str(path)]


def test_install_is_idempotent_and_keeps_gate_untouched(tmp_path):
    import asyncio
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    module = c.module
    async def original(args):
        module.parent_state(auth, plan)
        return {'synthetic': True}
    module.run_ingestion = original
    counter = install(module)
    assert install(module) is counter
    assert asyncio.run(module.run_ingestion(None)) == {'synthetic': True}
    assert counter.finished


@pytest.mark.parametrize('field,value', [('allowed_kbs', ['other-kb']), ('subject_id', 'wrong-subject')])
def test_initial_scope_mismatch_cannot_use_preallocated_budget(tmp_path, field, value):
    c, plan, row, path, ledger, auth, parent = fixture(tmp_path)
    ledger[field] = value; base.write(path, ledger)
    with pytest.raises(ValueError, match='SCOPE_DENIED'):
        c.parent_state(auth, plan)
