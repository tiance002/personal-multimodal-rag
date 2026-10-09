"""SIMULATED full transport/persistence and PG-only checks; no real HTTP/DB."""
from contextlib import contextmanager
from io import BytesIO
import hashlib
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from backend.app.adapters.models import cloud
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.models import RankedHit
from backend.app.ports.model_access import model_access
from backend.tests.test_p4_rag_pipeline import chunk, repository
from backend.tests.test_p4_rerank_once import approval, factory, history, record
from scripts import verify_p4_rerank_once as once
from scripts import verify_p4_rag as joint

RAW = 'SIMULATED-INTEGRATED-RAW-SECRET'
PRIVATE = 'SIMULATED-INTEGRATED-PRIVATE-TEXT'


def install_http(monkeypatch, ranker, outcome='success', documents=once.DOCUMENTS):
    calls, body_reads = [], []
    payload = dict(results=[dict(index=i, relevance_score=score, document={"text": documents[i]})
                           for i, score in enumerate((.9, .1))])
    if outcome == 'missing': payload = dict(error={'message': PRIVATE})
    if outcome == 'count': payload['results'].pop()
    if outcome == 'document_null': payload['results'][1]['document'] = None
    if outcome == 'row': payload['results'][1] = PRIVATE
    if outcome == 'score': payload['results'][1]['relevance_score'] = 'bad'
    if outcome == 'top': payload = [PRIVATE]
    raw = ('{' + PRIVATE).encode() if outcome == 'json' else json.dumps(payload).encode()
    class Response(BytesIO):
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        def getcode(self): return 200
        def read(self, n=-1):
            body_reads.append(n)
            return super().read(n)
    class ErrorBody(BytesIO):
        def read(self, *a):
            raise AssertionError('ERROR_BODY_MUST_NOT_BE_READ')
    class Opener:
        def open(self, request, timeout):
            calls.append((hashlib.sha256(request.data).hexdigest(), len(request.data), timeout))
            if outcome.startswith('http'):
                raise HTTPError(request.full_url, int(outcome[4:]), RAW,
                                {'Content-Type': 'text/html; secret=' + RAW}, ErrorBody(PRIVATE.encode()))
            return Response(raw)
    monkeypatch.setattr(cloud, 'build_opener', lambda *handlers: Opener())
    ranker.transport = cloud._send
    return calls, body_reads, len(raw)


def full_factory(monkeypatch, outcome='success', budget=10):
    open_factory, gate, previous, ignored, opens, closes = factory(monkeypatch, budget=budget)
    # All three historical UNKNOWN reservations remain in this same ledger.
    old2, old3 = [key for key in gate.reservations if key != previous]
    if outcome == 'settlement':
        def fail_settlement(_): raise RuntimeError(RAW)
        gate.mark_unknown = fail_settlement
    @contextmanager
    def wrapped():
        with open_factory() as (ranker, identity):
            calls, reads, length = install_http(monkeypatch, ranker, outcome)
            state.update(ranker=ranker, calls=calls, reads=reads, length=length)
            yield ranker, identity
    state = dict(gate=gate, old=previous, old2=old2, old3=old3)
    return wrapped, state


@pytest.mark.parametrize('outcome,stage,code', [
    ('success', 'complete', 'NONE'),
    ('json', 'response_decode', 'JSON_RESPONSE_INVALID'),
    ('top', 'response_validation', 'RESPONSE_STRUCTURE_INVALID'),
    ('missing', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('count', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('document_null', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('row', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('score', 'rerank_validation', 'RERANK_SCORE_INVALID'),
    ('http401', 'http_response', 'HTTP_401'),
    ('http403', 'http_response', 'HTTP_403'),
    ('http429', 'http_response', 'HTTP_429'),
    ('http503', 'http_response', 'HTTP_5XX'),
    ('settlement', 'usage_settlement', 'USAGE_SETTLEMENT_FAILED'),
])
def test_send_wrappers_adapter_and_persistent_receipt(monkeypatch, history, tmp_path, outcome, stage, code):
    open_factory, state = full_factory(monkeypatch, outcome)
    output = tmp_path/'full-chain'
    before = once.read_history(history)
    assert once.run_simulated_once(approval(), output=output, history=history, factory=open_factory) == (
        0 if outcome == 'success' else 1)
    saved = record(output)
    diag = saved['receipts'][0]['response_diagnostics']
    assert saved['receipts'][0]['diagnostic_stage'] == stage
    assert saved['receipts'][0]['error_code'] == code
    assert diag['http_status_code'] == (int(outcome[4:]) if outcome.startswith('http') else 200)
    assert diag['content_type'] == ('text/html' if outcome.startswith('http') else 'application/json')
    assert diag['response_bytes'] == (None if outcome.startswith('http') else state['length'])
    assert diag['expected_results'] == 2
    assert saved['cumulative_attempts'] == 4 and saved['cumulative_body_bytes'] == 907
    assert saved['history'] == before
    assert len(state['calls']) == state['ranker'].requests == len(saved['attempts']) == 1
    assert state['reads'] == ([] if outcome.startswith('http') else [2_000_001])
    assert saved['settlement'] == saved['actual_cost'] == 'UNKNOWN'
    assert len(state['gate'].reservations) == 4
    assert state['gate'].reservations[state['old']]['state'] == 'unknown'
    if outcome == 'success':
        assert saved['scores'] == [.9, .1] and saved['returned_count'] == 2
    if outcome == 'document_null':
        assert diag['first_failure_row'] == 1 and diag['document_type'] == 'null'
    assert RAW not in json.dumps(saved) and PRIVATE not in json.dumps(saved)
    original = (output/'live-receipt.json').read_bytes()
    with pytest.raises(FileExistsError):
        once.run_simulated_once(approval(), output=output, history=history, factory=open_factory)
    assert len(state['calls']) == 1 and (output/'live-receipt.json').read_bytes() == original


def test_telemetry_failure_keeps_success_and_settlement(monkeypatch, history, tmp_path):
    open_factory, state = full_factory(monkeypatch)
    monkeypatch.setattr(cloud, 'record_provider_call', lambda **k: (_ for _ in ()).throw(RuntimeError(RAW)))
    out = tmp_path/'telemetry'
    assert once.run_simulated_once(approval(), output=out, history=history, factory=open_factory) == 0
    saved = record(out)
    assert saved['receipts'][0]['telemetry_error_code'] == 'USAGE_CAPTURE_FAILED'
    assert saved['receipts'][0]['response_diagnostics']['http_status_code'] == 200
    assert len(state['calls']) == 1
    assert all(item['state'] == 'unknown' for item in state['gate'].reservations.values())
    assert RAW not in json.dumps(saved)


@pytest.mark.parametrize('operation', ['fsync', 'replace'])
@pytest.mark.parametrize('fail_at,expected_sends', [(1, 0), (2, 1)])
@pytest.mark.parametrize('outcome', ['success', 'http503'])
def test_persistence_failure_never_retries_or_masks_first_error(
        monkeypatch, history, tmp_path, operation, fail_at, expected_sends, outcome):
    open_factory, state = full_factory(monkeypatch, outcome)
    old_rows = {key: dict(value) for key, value in state['gate'].reservations.items()}
    real_operation, operations = getattr(once.os, operation), []
    first_error = OSError('SIMULATED_INTEGRATED_PERSISTENCE_FAILURE')
    def fail(*args):
        operations.append(True)
        if len(operations) >= fail_at:
            raise first_error
        return real_operation(*args)
    monkeypatch.setattr(once.os, operation, fail)
    out = tmp_path/'persist'
    with pytest.raises(once.ReceiptPersistenceFailure, match='^RECEIPT_PERSISTENCE_FAILED$') as caught:
        once.run_simulated_once(approval(), output=out, history=history, factory=open_factory)
    failure = caught.value
    assert failure.__cause__ is first_error and failure.original_error_type == 'OSError'
    assert failure.operation == operation
    assert failure.stage == ('before_transport' if fail_at == 1 else 'final_receipt')
    assert len(operations) == fail_at  # No final save retry or FileExistsError replacement.
    assert failure.record['error_code'] == 'RECEIPT_PERSISTENCE_FAILED'
    assert failure.record['status'] == 'BLOCKED'
    assert failure.record['settlement'] == failure.record['actual_cost'] == 'UNKNOWN'
    assert len(failure.record['attempts']) == 1
    assert failure.record['attempts'][0]['state'] == 'consumed_before_transport'
    assert failure.record['cumulative_attempts'] == 4
    assert len(state['calls']) == expected_sends
    assert len(state['gate'].reservations) == 4
    assert all(item['state'] == 'unknown' for item in state['gate'].reservations.values())
    assert all(state['gate'].reservations[key] == value for key, value in old_rows.items())
    snapshot = (out/'live-receipt.json').read_bytes()
    pending = (out/'live-receipt.tmp').read_bytes()
    assert out.is_dir() and json.loads(pending)['attempts'][0]['state'] == 'consumed_before_transport'
    if expected_sends:
        assert len(json.loads(snapshot)['attempts']) == 1
        original_codes = failure.record['outcome_before_persistence_failure']['receipt_error_codes']
        assert original_codes == (['HTTP_5XX'] if outcome == 'http503' else ['NONE'])
    else:
        assert json.loads(snapshot)['status'] == 'STARTED' and not json.loads(snapshot)['attempts']
    assert 'SIMULATED_INTEGRATED_PERSISTENCE_FAILURE' not in json.dumps(failure.record)
    assert RAW not in json.dumps(failure.record) and PRIVATE not in json.dumps(failure.record)
    with pytest.raises(FileExistsError):
        once.run_simulated_once(approval(), output=out, history=history, factory=open_factory)
    assert (out/'live-receipt.json').read_bytes() == snapshot
    assert (out/'live-receipt.tmp').read_bytes() == pending
    assert len(state['calls']) == expected_sends and len(operations) == fail_at


@pytest.mark.parametrize('id', ['p4-rerank-continuation-r1', 'p4-rerank-diagnostic-r2', 'p4-rerank-diagnostic-pg-r1', 'p4-r1', '../escape', 'arbitrary-new-id'])
def test_consumed_or_unregistered_identity_denied_before_factory(id, history, tmp_path):
    approved = approval()
    approved['request_id'] = id
    entered = []
    with pytest.raises(ValueError):
        once.run_simulated_once(approved, output=tmp_path/'no-output', history=history, factory=lambda: entered.append(True))
    assert not entered and not (tmp_path/'no-output').exists()


def test_second_historical_receipt_cannot_be_changed_or_omitted(history, tmp_path):
    continuation = tmp_path/'old-continuation.json'
    continuation.write_bytes(once.CONTINUATION.read_bytes() + b' ')
    with pytest.raises(ValueError, match='HISTORICAL_CONTINUATION_CHANGED'):
        once.read_history(history, continuation)
    with pytest.raises(FileNotFoundError):
        once.read_history(history, tmp_path/'missing.json')


def test_third_historical_receipt_cannot_be_changed_or_omitted(history, tmp_path):
    diagnostic = tmp_path/'old-diagnostic.json'
    diagnostic.write_bytes(once.DIAGNOSTIC.read_bytes() + b' ')
    with pytest.raises(ValueError, match='HISTORICAL_DIAGNOSTIC_CHANGED'):
        once.read_history(history, diagnostic=diagnostic)
    with pytest.raises(FileNotFoundError):
        once.read_history(history, diagnostic=tmp_path/'missing.json')


def pg_approval(probe):
    value = approval()
    value.pop('request_body_bytes')
    value.pop('request_body_sha256')
    value.update(request_id=once.PG_REQUEST_ID, validation_stage='pg_only',
                 synthetic_corpus_sha256=once.PG_CORPUS_SHA256,
                 cumulative_body_bytes_limit=once.TOTAL_BODY_LIMIT,
                 successful_probe_sha256=hashlib.sha256(probe.read_bytes()).hexdigest())
    return value


def successful_probe(monkeypatch, history, tmp_path):
    return successful_probe_with_state(monkeypatch, history, tmp_path)[0]


def successful_probe_with_state(monkeypatch, history, tmp_path, budget=10):
    out = tmp_path/'successful-probe'
    f, state = full_factory(monkeypatch, budget=budget)
    state['old_rows'] = {key: dict(value) for key, value in state['gate'].reservations.items()}
    assert once.run_simulated_once(approval(), output=out, history=history, factory=f) == 0
    assert len(state['gate'].reservations) == 4
    return out/'live-receipt.json', state


@pytest.mark.parametrize('change', ['hash', 'failed', 'count', 'identity', 'history', 'settlement'])
def test_pg_requires_exact_successful_probe_before_factory(monkeypatch, history, tmp_path, change):
    probe = successful_probe(monkeypatch, history, tmp_path)
    value = pg_approval(probe)
    body = json.loads(probe.read_bytes())
    if change == 'hash': value['successful_probe_sha256'] = '0'*64
    if change == 'failed': body['status'] = 'BLOCKED'
    if change == 'count': body['cumulative_attempts'] = 1
    if change == 'identity': body['request_id'] = 'p4-rerank-continuation-r1'
    if change == 'history': body['history']['attempts'] = 0
    if change == 'settlement': body['settlement'] = 'FREE'
    if change != 'hash':
        probe.write_text(json.dumps(body), encoding='utf-8')
        value['successful_probe_sha256'] = hashlib.sha256(probe.read_bytes()).hexdigest()
    entered = []
    with pytest.raises(ValueError):
        once.run_simulated_once(value, output=tmp_path/'pg-denied', history=history, pg_only=True,
                      probe_receipt=probe, factory=lambda: entered.append(True))
    assert not entered and not (tmp_path/'pg-denied').exists()


@pytest.mark.parametrize('budget', [4, 5])
def test_pg_only_runs_normal_pipeline_once_and_rolls_back(monkeypatch, history, tmp_path, budget):
    probe, probe_state = successful_probe_with_state(monkeypatch, history, tmp_path, budget)
    gate = probe_state['gate']
    rows_after_probe = {key: dict(value) for key, value in gate.reservations.items()}
    open_factory, shared_gate, old, unused, opens, closes = factory(
        monkeypatch, gate=gate, previous=probe_state['old'])
    assert shared_gate is gate and len(gate.reservations) == 4
    events = []
    body = once.PG_CORPUS
    line = body.splitlines(keepends=True)[0]
    seeds = [chunk('a', line, locator={'kind':'text','start':0,'end':len(line)},
                   parent_id='parent', chunk_index=0),
             chunk('b', body[len(line):2*len(line)],
                   locator={'kind':'text','start':len(line),'end':2*len(line)},
                   parent_id='parent', chunk_index=1)]
    repo = repository(chunk('parent', body, chunk_role='parent'), *seeds)
    repo.embedding_scope_allowed = lambda scope: True  # SIMULATED KB permission only.
    class Transaction:
        def rollback(self): events.append('rollback')
    class Connection:
        def begin(self): events.append('begin'); return Transaction()
        @contextmanager
        def begin_nested(self): yield self
    class Engine:
        @contextmanager
        def connect(self): yield Connection()
    engine = Engine()
    repo.engine = engine
    state = {}
    @contextmanager
    def container_factory(*args):
        with open_factory() as (ranker, identity):
            calls, reads, length = install_http(monkeypatch, ranker, documents=[seed.content for seed in seeds])
            state.update(ranker=ranker, calls=calls)
            container = SimpleNamespace(engine=engine, store=repo,
                knowledge_gateway=SimpleNamespace(retriever=HybridRetriever(repo, mode='keyword', ranker=ranker),
                                                   evidence=EvidenceService()))
            yield container, identity
    monkeypatch.setattr(once, 'live_container', container_factory)
    import backend.tests.test_p4_rag_postgres as pg_fixture
    def seed(store, connection):
        events.append('seed')
        return {'id':'kb'}, {}, body, ['a','b'], ['parent']
    monkeypatch.setattr(pg_fixture, 'seed_p3_fixture', seed)
    monkeypatch.setattr(joint, 'table_counts',
                        lambda c: {'business':0, 'model_calls':5+len(state.get('ranker', SimpleNamespace(receipts=[])).receipts)})
    output = tmp_path/'pg-only'
    result = once.run_simulated_once(pg_approval(probe), output=output, history=history, pg_only=True,
                                    probe_receipt=probe, factory=lambda: joint.pg_only_factory(output))
    saved = record(output)
    assert len(probe_state['calls']) == 1
    assert all(gate.reservations[key] == value for key, value in rows_after_probe.items())
    assert all(item['state'] == 'unknown' for item in gate.reservations.values())
    assert events == ['begin', 'seed', 'rollback'] and repo.engine is engine
    if budget == 4:
        assert result == 1 and saved['status'] == 'BLOCKED'
        assert not state['calls'] and not saved['attempts']
        assert saved['cumulative_attempts'] == 4 and len(gate.reservations) == 4
        assert gate.reservations == rows_after_probe
        return
    assert result == 0
    assert saved['status'] == 'PG_ONLY_PASS' and saved['pipeline']['status'] == 'PASS'
    assert len(state['calls']) == state['ranker'].requests == len(saved['attempts']) == 1
    assert saved['cumulative_attempts'] == 5
    assert saved['cumulative_body_bytes'] == 907 + saved['attempts'][0]['request_body_bytes']
    assert saved['scores'] == [.9, .1]
    assert saved['receipts'][0]['response_diagnostics']['http_status_code'] == 200
    assert events == ['begin', 'seed', 'rollback'] and repo.engine is engine
    assert len(saved['history']['consumed_request_ids']) == 4
    assert len(gate.reservations) == 5 and all(item['state'] == 'unknown' for item in gate.reservations.values())
    assert saved['actual_cost'] == saved['settlement'] == 'UNKNOWN'


def test_pg_only_default_and_old_combined_entry_do_not_probe(monkeypatch, capsys):
    monkeypatch.setattr(joint, 'run', lambda: pytest.fail('IMPLICIT_PROBE'))
    assert joint.main(['--pg-only']) == 0
    assert json.loads(capsys.readouterr().out)['request_id'] == once.PG_REQUEST_ID
    assert joint.main(['--execute-owner-authorized']) == 1


def test_missing_historical_receipt_never_reopens_combined_entry(monkeypatch, tmp_path, capsys):
    old_out = tmp_path/'historical-missing'
    monkeypatch.setattr(joint, 'OUT', old_out)
    assert not (old_out/'live-receipt.json').exists()
    original_run = joint.run
    with pytest.raises(ValueError, match='^HISTORICAL_COMBINED_ENTRY_DISABLED$'):
        original_run()
    entered = []
    monkeypatch.setattr(joint, 'run', lambda: entered.append(True))
    assert joint.main(['--execute-owner-authorized']) == 1
    assert 'HISTORICAL_COMBINED_ENTRY_DISABLED' in capsys.readouterr().out
    assert not entered and not old_out.exists()


@pytest.mark.parametrize('pg_only', [False, True])
def test_real_core_rejects_alternate_directory_before_any_factory(monkeypatch, tmp_path, pg_only):
    entered = []
    monkeypatch.setattr(once, 'live_ranker', lambda: entered.append('probe'))
    monkeypatch.setattr(joint, 'pg_only_factory', lambda *a: entered.append('pg'))
    value = approval()
    if pg_only:
        value.pop('request_body_bytes')
        value.pop('request_body_sha256')
        value.update(request_id=once.PG_REQUEST_ID, validation_stage='pg_only',
                     synthetic_corpus_sha256=once.PG_CORPUS_SHA256,
                     cumulative_body_bytes_limit=once.TOTAL_BODY_LIMIT,
                     successful_probe_sha256='0'*64)
    for name in ['alternate-a', 'alternate-b']:
        output = tmp_path/name
        with pytest.raises(ValueError, match='^REGISTERED_REQUEST_OUTPUT_REQUIRED$'):
            once.run_once(value, output=output, pg_only=pg_only)
        assert not output.exists()
    assert not entered


def test_real_core_rejects_identity_and_removed_factory_seam(monkeypatch, tmp_path):
    entered = []
    monkeypatch.setattr(once, 'live_ranker', lambda: entered.append(True))
    with pytest.raises(ValueError, match='^REGISTERED_REQUEST_OUTPUT_REQUIRED$'):
        once.run_once(approval(), request_id='arbitrary-new-id')
    with pytest.raises(TypeError):
        once.run_once(approval(), factory=lambda: entered.append(True))
    with pytest.raises(TypeError):
        once.run_once(approval(), history=tmp_path/'alternate-history')
    assert not entered


def test_real_core_consumed_directory_cannot_be_relocated(monkeypatch, history, tmp_path):
    canonical = tmp_path/'registered-real'
    canonical.mkdir()
    marker = canonical/'live-receipt.json'
    marker.write_text('{"status":"STARTED","settlement":"UNKNOWN"}', encoding='utf-8')
    before = marker.read_bytes()
    entered = []
    monkeypatch.setattr(once, 'OUT', canonical)
    monkeypatch.setattr(once, 'HISTORY', history)
    monkeypatch.setattr(once, 'live_ranker', lambda: entered.append(True))
    with pytest.raises(FileExistsError):
        once.run_once(approval())
    alternate = tmp_path/'relocated-real'
    with pytest.raises(ValueError, match='^REGISTERED_REQUEST_OUTPUT_REQUIRED$'):
        once.run_once(approval(), output=alternate)
    assert not entered and not alternate.exists() and marker.read_bytes() == before


def test_simulated_entry_has_no_live_default_or_real_output(monkeypatch, history, tmp_path):
    entered = []
    monkeypatch.setattr(once, 'live_ranker', lambda: entered.append(True))
    for fake in [None, once.live_ranker]:
        with pytest.raises(ValueError, match='^SIMULATED_FACTORY_REQUIRED$'):
            once.run_simulated_once(approval(), output=tmp_path/'fake', history=history, factory=fake)
    with pytest.raises(TypeError):
        once.run_simulated_once(approval(), output=tmp_path/'fake', history=history)
    for output in [once.OUT, once.PG_OUT]:
        with pytest.raises(ValueError, match='^SIMULATED_OUTPUT_MUST_NOT_CONSUME_REAL_IDENTITY$'):
            once.run_simulated_once(approval(), output=output, history=history,
                                    factory=lambda: entered.append(True))
    assert not entered and not (tmp_path/'fake').exists()


def test_simulated_probe_is_not_a_real_pg_prerequisite(monkeypatch, history, tmp_path):
    probe = successful_probe(monkeypatch, history, tmp_path)
    value = pg_approval(probe)
    assert record(probe.parent)['evidence_kind'] == 'SIMULATED'
    with pytest.raises(ValueError, match='^SUCCESSFUL_PROBE_REQUIRED$'):
        once.read_successful_probe(probe, value, once.read_history(history))


def test_persistence_cli_exports_only_fixed_error(monkeypatch, tmp_path, capsys):
    auth = tmp_path/'simulated-contract.json'
    auth.write_text(json.dumps(approval()), encoding='utf-8')
    def fail(*a, **k):
        raise once.ReceiptPersistenceFailure('final_receipt', 'replace', 'OSError')
    monkeypatch.setattr(once, 'run_once', fail)
    assert once.main(['--execute-owner-authorized-once', '--authorization-file', str(auth)]) == 1
    assert json.loads(capsys.readouterr().out) == dict(status='BLOCKED',
        error_type='ReceiptPersistenceFailure', error_code='RECEIPT_PERSISTENCE_FAILED',
        persistence_stage='final_receipt', persistence_operation='replace')


def test_pg_cumulative_body_limit_denies_before_transport():
    content = 'Synthetic ' + 'x'*20_000
    payload = dict(model=once.MODEL, query=once.PG_QUERY, documents=[content], top_n=1, return_documents=True)
    sends, saves = [], []
    transport = once.OnceTransport({'attempts':[], 'history':{'body_bytes':681}},
        lambda: saves.append(True), lambda *a: sends.append(True), [content], pg_only=True)
    from backend.app.ports.providers import ProviderRequestNotSent
    with pytest.raises(ProviderRequestNotSent, match='CUMULATIVE_BODY_LIMIT_EXCEEDED'):
        transport(Request('https://api.siliconflow.cn/v1/rerank', data=json.dumps(payload).encode()), 30)
    assert not sends and not saves


@pytest.mark.parametrize('mime,expected', [('application/json; charset=utf-8','application/json'),
                                          ('text/html; private='+RAW,'text/html'),
                                          (RAW,'other'), (None,None)])
def test_content_type_summary_never_exports_unknown_header(mime, expected):
    diag = cloud._response_diagnostics(http_status_code=True, response_bytes=RAW, content_type=mime)
    assert diag['http_status_code'] is None and diag['response_bytes'] is None
    assert diag['content_type'] == expected and RAW not in json.dumps(diag)
