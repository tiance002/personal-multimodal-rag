"""SIMULATED sends/leases; real independent E-drive SQLite/QuickChain checks."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import subprocess
import sys
import threading

import pytest
import eval_center.paired_state as state
from eval_center.tests.test_public_paired_runtime import _batch_setup, _batch_claim, _args
from eval_center.paired_simulation import build_simulated_runtime
from eval_center.public_benchmark_cli import _run
from backend.app.ports.providers import ProviderRequestNotSent


def setup(path, monkeypatch):
    callback, clock = _batch_setup(path, monkeypatch)
    return replace(callback, plan=replace(callback.plan, provider='siliconflow',
                    model='SIMULATED-window-model')), clock


def overrun(callback, marker='native_error'):
    first = _batch_claim(callback)
    state.finish_provider_send(callback.path, request_id=first.request_id, status='unknown',
        error_code='SF_FREE_TOKEN_BOUND_EXCEEDED' if marker == 'native_error' else None,
        result={'provider_dispatch': {'request_window_blocked': True}} if marker == 'legacy_receipt' else None)
    return state.provider_send_claim_record(callback.path, request_id=first.request_id)


@pytest.mark.parametrize('marker', ['native_error', 'legacy_receipt'])
def test_persisted_overrun_rejects_before_insert_and_preserves_unknown(tmp_path, monkeypatch, marker):
    callback, _ = setup(tmp_path/'center.sqlite3', monkeypatch)
    old = overrun(callback, marker)
    before = state.run_record(callback.path, callback.run_id)
    fresh = replace(callback)
    with pytest.raises(state.PairedContractError, match='window_blocked'):
        _batch_claim(fresh, 2)
    assert state.provider_send_claim_record(callback.path,
        request_id=fresh.request_id(purpose='index_embedding', call_ordinal=2)) is None
    assert state.provider_send_claim_record(callback.path, request_id=old['request_id']) == old
    assert state.run_record(callback.path, callback.run_id) == before
    assert fresh.reusable_result(request_id=old['request_id']) is None
    with pytest.raises(state.PairedContractError):
        _batch_claim(fresh)
    with sqlite3.connect(callback.path) as db:
        assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0] == 1


def test_preclaim_window_query_shares_insert_write_transaction(tmp_path, monkeypatch):
    callback, _ = setup(tmp_path/'center.sqlite3', monkeypatch)
    checker = state._check_persisted_provider_window
    phases = []
    def check(db, **scope):
        phases.append((db.in_transaction, db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0]))
        checker(db, **scope)
    monkeypatch.setattr(state, '_check_persisted_provider_window', check)
    claim = _batch_claim(callback)
    assert phases == [(True, 0), (False, 1)]
    callback.check_before_send(request_id=claim.request_id)
    assert phases[-1] == (False, 1)  # actual final wire boundary still checks


def test_postcommit_overrun_race_keeps_new_pending_consumed(tmp_path, monkeypatch):
    callback, _ = setup(tmp_path/'center.sqlite3', monkeypatch)
    first = _batch_claim(callback)
    second_id = callback.request_id(purpose='index_embedding', call_ordinal=2)
    def settle_after_commit(lease):
        assert state.provider_send_claim_record(callback.path, request_id=second_id)['status'] == 'pending'
        callback.settle(request_id=first.request_id, status='unknown', error_code='SF_FREE_TOKEN_BOUND_EXCEEDED')
    racing = replace(callback, lease_validator=settle_after_commit)
    with pytest.raises(state.PairedContractError, match='window_blocked'):
        _batch_claim(racing, 2)
    assert state.provider_send_claim_record(callback.path, request_id=second_id)['status'] == 'pending'
    with pytest.raises(state.PairedContractError):
        _batch_claim(callback, 2)
    with pytest.raises(state.PairedContractError, match='window_blocked'):
        callback.check_before_send(request_id=second_id)


def test_persisted_block_denies_eight_concurrent_callbacks_without_new_child(tmp_path, monkeypatch):
    callback, _ = setup(tmp_path/'center.sqlite3', monkeypatch)
    old = overrun(callback)
    barrier = threading.Barrier(8)
    def worker(_):
        barrier.wait(timeout=10)
        try:
            _batch_claim(replace(callback), 2)
        except state.PairedContractError as exc:
            return str(exc)
        return 'UNEXPECTED_SEND_ADMISSION'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(8)))
    assert results == ['provider_send_window_blocked_by_token_overrun'] * 8
    with sqlite3.connect(callback.path) as db:
        assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0] == 1
    assert state.provider_send_claim_record(callback.path, request_id=old['request_id']) == old
    (tmp_path/'concurrency.json').write_text(json.dumps({'callbacks': 8, 'denials': results,
        'durable_children': 1, 'simulated_wires': 0, 'real_model_calls': 0}), encoding='utf-8')


def test_restarted_process_rejects_persisted_overrun_without_replay(tmp_path, monkeypatch):
    callback, _ = setup(tmp_path/'center.sqlite3', monkeypatch)
    old = overrun(callback)
    script = '''import sys,json
from pathlib import Path
from dataclasses import replace
from eval_center.tests.test_public_paired_runtime import _batch_plan,_batch_claim
from eval_center.paired_state import SQLiteProviderBatchSendClaims,PairedContractError,provider_send_claim_record
plan=replace(_batch_plan(),provider='siliconflow',model='SIMULATED-window-model')
c=SQLiteProviderBatchSendClaims(Path(sys.argv[1]),'batch-run','unit-1','INDEXING','d'*64,plan,'e'*64,lambda lease:None)
try:_batch_claim(c,2)
except PairedContractError as e:
 assert str(e)=='provider_send_window_blocked_by_token_overrun'
 assert provider_send_claim_record(c.path,request_id=c.request_id(purpose='index_embedding',call_ordinal=2)) is None
 print(json.dumps({'recovery':'DENIED_BEFORE_INSERT','simulated_wires':0,'real_model_calls':0}))
else:raise AssertionError('unexpected replay')
'''
    process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, str(callback.path)], capture_output=True)
    assert process.returncode == 0, process.stderr.decode(errors='replace')
    assert state.provider_send_claim_record(callback.path, request_id=old['request_id']) == old
    (tmp_path/'restart.json').write_text(json.dumps({'argv': process.args, 'exit_code': process.returncode,
        'stdout': process.stdout.decode(), 'execution_kind': 'SIMULATED; REAL_LOCAL_PROCESS_SQLITE'}), encoding='utf-8')


def test_unknown_timeout_consumes_own_slot_but_does_not_poison_next(tmp_path, monkeypatch):
    callback, _ = setup(tmp_path/'center.sqlite3', monkeypatch)
    first = _batch_claim(callback)
    callback.settle(request_id=first.request_id, status='unknown', error_code='REQUEST_TIMEOUT')
    old = state.provider_send_claim_record(callback.path, request_id=first.request_id)
    second = _batch_claim(callback, 2)
    callback.check_before_send(request_id=second.request_id)
    with pytest.raises(state.PairedContractError, match='already_claimed_no_retry'):
        _batch_claim(callback)
    assert state.provider_send_claim_record(callback.path, request_id=first.request_id) == old


@pytest.mark.parametrize('dimension', ['provider', 'model', 'role', 'run'])
def test_persistent_window_scope_preserves_default_chat_contract(tmp_path, monkeypatch, dimension):
    # Use the native chat claim API without batch metadata or altered assertions.
    path = tmp_path/'chat.sqlite3'
    _, _ = _batch_setup(path, monkeypatch)
    for run in ['chat-one', 'chat-two']:
        state.register_run(path, run_id=run, cohort_sha256='a'*64, source_manifest_sha256='b'*64,
            code_sha='c'*64, deadline_utc='2026-10-10T07:44:02Z', expected_qids=['q1','q2'])
        for qid, arm in [('q1','CHEAP_ONLY'), ('q2','DYNAMIC')]:
            state.begin_attempt(path, run_id=run, qid=qid, arm=arm, input_identity_sha256='d'*64)
    args = dict(run_id='chat-one', qid='q1', arm='CHEAP_ONLY', request_id='chat-1',
        input_identity_sha256='d'*64, request_identity_sha256='e'*64,
        provider='siliconflow', model='SIMULATED-chat', role='chat_cheap')
    state.claim_provider_send(path, **args)
    state.finish_provider_send(path, request_id='chat-1', status='unknown', error_code='SF_FREE_TOKEN_BOUND_EXCEEDED')
    old = state.provider_send_claim_record(path, request_id='chat-1')
    second = dict(args, qid='q2', arm='DYNAMIC', request_id='chat-2')
    second[dimension if dimension != 'run' else 'run_id'] = {
        'provider':'SIMULATED-other', 'model':'SIMULATED-other', 'role':'chat_expensive', 'run':'chat-two'}[dimension]
    claim = state.claim_provider_send(path, **second)
    state.check_provider_send_before_send(path, run_id=claim.run_id, qid='q2', arm='DYNAMIC',
        input_identity_sha256='d'*64, request_id='chat-2')
    assert state.provider_send_claim_record(path, request_id='chat-1') == old
    assert state.provider_send_claim_record(path, request_id='chat-2')['batch_plan_sha256'] is None


@pytest.mark.parametrize('failure', ['malformed_receipt', 'db_error'])
def test_window_read_errors_fail_closed_before_insert(tmp_path, monkeypatch, failure):
    callback, _ = setup(tmp_path/'center.sqlite3', monkeypatch)
    first = _batch_claim(callback)
    if failure == 'malformed_receipt':
        with sqlite3.connect(callback.path) as db:
            db.execute("UPDATE paired_provider_calls SET result_json='{' WHERE request_id=?", (first.request_id,))
        error = json.JSONDecodeError
    else:
        def broken_query(db, **scope):
            assert db.in_transaction
            db.execute('SELECT * FROM missing_window_table')
        monkeypatch.setattr(state, '_check_persisted_provider_window', broken_query)
        error = sqlite3.OperationalError
    with pytest.raises(error):
        _batch_claim(callback, 2)
    assert state.provider_send_claim_record(callback.path,
        request_id=callback.request_id(purpose='index_embedding', call_ordinal=2)) is None
    with sqlite3.connect(callback.path) as db:
        assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0] == 1


def test_resume_reconstructs_top_level_failed_cell_without_model_or_db_retry(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    # Freeze only this offline test's two admission clocks; leave production
    # time profiles, grants and all historical Run deadlines unchanged.
    class SimulatedRecoveryClock(datetime):
        current = datetime(2026,10,10,3,0,tzinfo=timezone.utc)
        @classmethod
        def now(cls,tz=None):return cls.current
    monkeypatch.setattr(state,'datetime',SimulatedRecoveryClock)
    monkeypatch.setattr('eval_center.public_runner.datetime',SimulatedRecoveryClock)
    path = tmp_path/'center.sqlite3'
    runtime = build_simulated_runtime(state_path=path, run_id='resume-failed-SIMULATED')
    gate = runtime.containers['scifact'].simulated_gate
    grant_before = gate.grant
    assert grant_before.execution_kind == 'SIMULATED'
    assert all(c.simulated_gate is gate for c in runtime.containers.values())
    assert grant_before.deadline_utc == '2026-10-10T04:25:44+00:00'
    # Synchronize this fixture's existing injectable gate clock, not its grant.
    monkeypatch.setattr(gate,'clock',lambda:SimulatedRecoveryClock.now(timezone.utc))
    gateway = runtime.containers['scifact'].quick_chain.generation_roles['chat_cheap'].gateway
    original, calls = gateway.generate, []
    def generate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ProviderRequestNotSent('SIMULATED_FIXTURE_DENIAL')
        return original(*args, **kwargs)
    monkeypatch.setattr(gateway, 'generate', generate)
    first = _run(_args(path, run_id='resume-failed-SIMULATED'), runtime=runtime)
    assert len(first['failures']) == 1 and first['status'] == 'SIMULATED_PAIRED_COHORT_PARTIAL'
    rows_before = state.load_case_results(path, first['run_id'])
    run_before = state.run_record(path,first['run_id'])
    assert run_before['deadline_utc'] == '2026-10-10T04:25:44+00:00'
    before = len(calls)
    SimulatedRecoveryClock.current = datetime(2026,10,10,3,5,tzinfo=timezone.utc)
    second = _run(_args(path, phase='paired-simulate', run_id=first['run_id']), runtime=runtime)
    assert second['failures'] == first['failures']
    assert second['new_quickchain_invocations'] == 0 and len(calls) == before
    assert second['results_sha256'] == first['results_sha256']
    assert state.load_case_results(path, first['run_id']) == rows_before
    assert second['provider_calls'] == second['database_queries'] == 0
    assert state.run_record(path,first['run_id']) == run_before
    assert gate.grant is grant_before
    # At the unchanged absolute cutoff, recovery may read immutable results
    # but cannot rescore, dispatch or create a new expired Run.
    SimulatedRecoveryClock.current = datetime(2026,10,10,4,25,44,tzinfo=timezone.utc)
    expired = _run(_args(path,phase='paired-simulate',run_id=first['run_id']),runtime=runtime)
    assert expired['cohort_scoring_status'] == 'NOT_RUN_DEADLINE_REACHED'
    assert expired['new_quickchain_invocations'] == 0 and len(calls) == before
    assert expired['provider_calls'] == expired['database_queries'] == 0
    assert expired['failures'] == first['failures']
    assert expired['results_sha256'] == first['results_sha256']
    assert state.load_case_results(path,first['run_id']) == rows_before
    assert state.run_record(path,first['run_id']) == run_before
    from backend.app.ports.session_attempts import AttemptDenied
    with pytest.raises(AttemptDenied,match='V6_REQUEST_BOUND_INVALID'):
        gate.validate_request('SIMULATED deadline probe',10,512,model=grant_before.model)
    assert gate.grant is grant_before
    expired_path = tmp_path/'expired-new-run.sqlite3'
    with pytest.raises(state.PairedContractError,match='run_deadline_expired'):
        _run(_args(expired_path,run_id='expired-new-SIMULATED'),runtime=runtime)
    assert state.run_record(expired_path,'expired-new-SIMULATED') is None
    assert len(calls) == before
    (tmp_path/'recovered-failures.json').write_text(json.dumps({'first': first, 'resumed': second,
        'expired_recovery':expired,'execution_kind':'SIMULATED',
        'frozen_test_clock_utc':['2026-10-10T03:00:00Z','2026-10-10T03:05:00Z','2026-10-10T04:25:44Z'],
        'unchanged_run_identity':run_before,'new_expired_run':'REJECTED_ZERO_SEND'},
        ensure_ascii=False, indent=2), encoding='utf-8')
