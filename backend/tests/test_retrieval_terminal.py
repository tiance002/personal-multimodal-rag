"""SIMULATED transactional PG/Redis state; actual normal classes, no DB/model calls."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import Event
from pathlib import Path
import hashlib
import json
import pytest

from backend.tests.test_run_construction import build, request, RUN, SESSION, SCOPE
from backend.app.adapters.postgres.run_lifecycle import request_hash
from backend.app.application.run_lifecycle import RunLifecycle, read_committed_events
from backend.app.ports.run_lifecycle import (
    RunClaim, LifecycleDenied, current_execution, current_attempt, execution_owner,
    checked_retrieval_completion,
)

QIDS = ('SIMULATED-q1', 'SIMULATED-q2')
DIGEST = 'a' * 64
OWNER = 'SIMULATED-owner'


class Result:
    def __init__(self, value=None, rows=(), rowcount=1): self.value, self.rows, self.rowcount = value, rows, rowcount
    def mappings(self): return self
    def first(self): return self.value
    def all(self): return self.rows
    def scalar(self): return self.value
    def scalar_one(self): return self.value


class SQL:
    def __init__(self, harness, data): self.h, self.data = harness, data
    def execute(self, statement, values=None):
        query = ' '.join(str(statement).split())
        values = values or {}
        self.h.calls.append((query, values))
        if self.h.hook: self.h.hook(query)
        row, lease = self.data['run'], self.data['lease']
        if query.startswith('SELECT owner,lease_until'): return Result(lease)
        if query.startswith('SELECT request_hash'): return Result(lease['request_hash'] if lease else None)
        if query.startswith('SELECT owner,state'): return Result(lease)
        if query.startswith('SELECT knowledge_base_scope'): return Result(self.data['conversation'])
        if query.startswith('SELECT * FROM rag_runs') or query.startswith('SELECT conversation_id,'):
            return Result(row)
        if query.startswith('SELECT EXISTS'): return Result(self.data['prohibited'])
        if query.startswith('SELECT content FROM conversation_messages'): return Result(self.data['answer'])
        if query.startswith('SELECT payload FROM retrieval_events'):
            if "event_type='answer.completed'" in query: return Result(self.data['answer_event'])
            events = [e for e in self.data['events'] if e['event'] == 'retrieval.completed']
            return Result(events[-1]['data'] if events else None)
        if query.startswith('SELECT ae.label'): return Result(rows=self.data['evidence'])
        if query.startswith('SELECT owner,request_hash'): return Result(lease)
        if query.startswith('UPDATE rag_runs'):
            seq = row['next_event_seq']; row.update(status='completed', error_code=None, next_event_seq=seq+1)
            return Result(seq)
        if query.startswith('INSERT INTO retrieval_events'):
            if self.h.fail_insert: raise RuntimeError('SIMULATED event insert failed')
            self.data['events'].append({'seq': values['seq'], 'event': 'retrieval.completed',
                                        'data': json.loads(values['payload'])})
            return Result()
        if query.startswith('UPDATE rag_run_leases'):
            if self.h.expire_at_commit: return Result(rowcount=0)
            lease['state'] = 'COMPLETED'; return Result()
        raise AssertionError('Unexpected SQL / forbidden table write: ' + query)


class Harness:
    def __init__(self, monkeypatch):
        self.bundle = build()
        self.calls, self.commits, self.rollbacks = [], 0, 0
        self.fail_insert = self.fail_commit = self.expire_at_commit = False
        self.hook = None
        self.lost = Event()
        self.claim = RunClaim(RUN, OWNER, True, 'IN_PROGRESS')
        self.data = {'run': {'id': RUN, 'conversation_id': SESSION,
            'knowledge_base_scope': request().kbs, 'document_scope': request().docs,
            'q0': request().content, 'status': 'running', 'error_code': None, 'next_event_seq': 1},
            'lease': {'owner': OWNER, 'valid': True, 'state': 'IN_PROGRESS',
                'request_hash': request_hash(SESSION, request().kbs, request().docs, request().content, 'quick')},
            'conversation': {'knowledge_base_scope': request().kbs, 'document_scope': request().docs, 'deleted_at': None},
            'events': [], 'prohibited': False, 'answer': None, 'answer_event': None, 'evidence': []}
        monkeypatch.setattr(self.bundle.write_engine, 'begin', self.begin)
        monkeypatch.setattr(self.bundle.write_engine, 'connect', self.connect)

    @contextmanager
    def begin(self):
        data = deepcopy(self.data)
        try:
            yield SQL(self, data)
            if self.fail_commit: raise RuntimeError('SIMULATED commit failed')
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.data = data; self.commits += 1

    @contextmanager
    def connect(self): yield SQL(self, self.data)

    @contextmanager
    def current(self, *, lifecycle=None, claim=None, session=SESSION, attempt=None, owner=None):
        claim = claim or self.claim
        a = current_execution.set((claim, lifecycle or self.bundle.lifecycle, self.lost, session))
        b = execution_owner.set(owner or (claim.run_id, claim.owner))
        c = current_attempt.set(attempt)
        try: yield
        finally: current_execution.reset(a); execution_owner.reset(b); current_attempt.reset(c)

    def finish(self, req=None, qids=QIDS, digest=DIGEST):
        return self.bundle.finalize_retrieval(req or request(), completed_qids=qids, native_receipts_sha256=digest)


def test_formal_retrieval_terminal_commits_truthful_metadata_without_answer(monkeypatch):
    h = Harness(monkeypatch)
    with h.current(): assert h.finish() is True
    assert h.commits == 1 and h.rollbacks == 0
    assert h.data['run']['status'] == 'completed' and h.data['lease']['state'] == 'COMPLETED'
    assert len(h.data['events']) == 1
    payload = h.data['events'][0]['data']
    assert payload['terminal_kind'] == 'retrieval_only' and payload['completed_qids'] == list(QIDS)
    assert payload['native_receipts_sha256'] == DIGEST
    assert payload['knowledge_base_ids'] == request().kbs and payload['document_ids'] == request().docs
    assert h.calls[0][0].endswith('FOR UPDATE') and 'FROM rag_runs' in h.calls[0][0]
    assert h.calls[1][0].endswith('FOR UPDATE') and 'FROM rag_run_leases' in h.calls[1][0]
    writes = [q for q, _ in h.calls if q.startswith(('UPDATE', 'INSERT'))]
    assert len(writes) == 3 and not any('conversation_messages' in q or 'answer_evidence' in q or 'answer.completed' in q for q in writes)
    saved = h.bundle.lifecycle.repository.read_run(RUN, SESSION, request().kbs, request().docs)
    assert saved['status'] == 'completed' and saved['answer'] == '' and saved['citations'] == ()
    assert saved['retrieval']['completed_qids'] == list(QIDS)


@pytest.mark.parametrize('kind', ['no_context', 'other_lifecycle', 'other_run', 'other_session', 'foreign_owner',
                                  'lost', 'active_attempt', 'duplicate_claim', 'terminal_claim'])
def test_finalizer_requires_exact_live_normal_context_before_sql(monkeypatch, kind):
    h = Harness(monkeypatch)
    kwargs = {}
    if kind == 'lost': h.lost.set()
    if kind == 'other_lifecycle': kwargs['lifecycle'] = RunLifecycle(h.bundle.lifecycle.repository, h.bundle.lifecycle.streams)
    if kind == 'other_run': kwargs['claim'] = replace(h.claim, run_id='00000000-0000-0000-0000-000000000099')
    if kind == 'other_session': kwargs['session'] = 'other-session'
    if kind == 'foreign_owner': kwargs['owner'] = (RUN, 'foreign')
    if kind == 'active_attempt': kwargs['attempt'] = 'unfinished-model-attempt'
    if kind == 'duplicate_claim': kwargs['claim'] = replace(h.claim, created=False)
    if kind == 'terminal_claim': kwargs['claim'] = replace(h.claim, state='COMPLETED')
    if kind == 'no_context':
        with pytest.raises(LifecycleDenied): h.finish()
    else:
        with h.current(**kwargs), pytest.raises(LifecycleDenied): h.finish()
    assert h.calls == [] and h.commits == 0


@pytest.mark.parametrize('qids,digest', [((), DIGEST), (list(QIDS), DIGEST), (('same','same'), DIGEST),
                                      (('',), DIGEST), (('q\n1',), DIGEST), (QIDS, 'invalid')])
def test_invalid_completion_receipt_is_rejected_before_sql(monkeypatch, qids, digest):
    h = Harness(monkeypatch)
    with h.current(), pytest.raises(LifecycleDenied): h.finish(qids=qids, digest=digest)
    assert h.calls == [] and h.data['events'] == []


@pytest.mark.parametrize('kind', ['cancelled', 'completed', 'foreign_lease', 'expired', 'missing_lease',
                                  'wrong_scope', 'wrong_session', 'wrong_request', 'wrong_mode',
                                  'deleted_conversation', 'conversation_scope', 'generation_present'])
def test_actual_transaction_state_cannot_be_bypassed(monkeypatch, kind):
    h = Harness(monkeypatch); req = request()
    if kind in ('cancelled','completed'): h.data['run']['status'] = kind
    elif kind == 'foreign_lease': h.data['lease']['owner'] = 'foreign'
    elif kind == 'expired': h.data['lease']['valid'] = False
    elif kind == 'missing_lease': h.data['lease'] = None
    elif kind == 'wrong_scope': h.data['run']['document_scope'] = ['foreign']
    elif kind == 'wrong_session': h.data['run']['conversation_id'] = 'foreign'
    elif kind == 'wrong_request': req = replace(req, content='different request')
    elif kind == 'wrong_mode': req = replace(req, mode='smart')
    elif kind == 'deleted_conversation': h.data['conversation']['deleted_at'] = 'SIMULATED deleted'
    elif kind == 'conversation_scope': h.data['conversation']['knowledge_base_scope'] = ['foreign']
    elif kind == 'generation_present': h.data['prohibited'] = True
    with h.current(), pytest.raises(LifecycleDenied): h.finish(req)
    assert not any(q.startswith(('UPDATE','INSERT')) for q,_ in h.calls)
    assert h.data['events'] == [] and h.commits == 0


@pytest.mark.parametrize('kind', ['event_insert', 'commit_failure', 'expired_at_commit', 'cancel_during_lock', 'cancel_during_write'])
def test_terminal_event_and_run_transition_rollback_together(monkeypatch, kind):
    h = Harness(monkeypatch)
    h.fail_insert = kind == 'event_insert'; h.fail_commit = kind == 'commit_failure'; h.expire_at_commit = kind == 'expired_at_commit'
    if kind == 'cancel_during_lock': h.hook = lambda q: h.lost.set() if 'FROM rag_runs' in q else None
    if kind == 'cancel_during_write': h.hook = lambda q: h.lost.set() if q.startswith('UPDATE rag_run_leases') else None
    with h.current(), pytest.raises((LifecycleDenied, RuntimeError)): h.finish()
    assert h.data['run']['status'] == 'running' and h.data['lease']['state'] == 'IN_PROGRESS'
    assert h.data['events'] == [] and h.commits == 0 and h.rollbacks == 1


def test_duplicate_terminal_cannot_overwrite_or_add_event(monkeypatch):
    h = Harness(monkeypatch)
    with h.current():
        assert h.finish() is True
        before = deepcopy(h.data)
        with pytest.raises(LifecycleDenied): h.finish(digest='b'*64)
    assert h.data == before and h.commits == 1


def test_retrieval_terminal_exits_existing_lifecycle_and_caches_committed_event(monkeypatch):
    h = Harness(monkeypatch); repo, streams = h.bundle.lifecycle.repository, h.bundle.lifecycle.streams
    observed = {'finished': [], 'cached': [], 'cleanup': []}
    monkeypatch.setattr(repo, 'claim_run', lambda *args: h.claim)
    monkeypatch.setattr(repo, 'finish_run', lambda *args, **kwargs: observed['finished'].append(args))
    monkeypatch.setattr(repo, 'cleanup_done', lambda *args: observed['cleanup'].append(args))
    monkeypatch.setattr(streams, 'set_live_run', lambda *args: True)
    monkeypatch.setattr(streams, 'clear_live_run', lambda *args: True)
    monkeypatch.setattr(streams, 'append_event', lambda *args: observed['cached'].append(args))
    class Store:
        def list_events(self, *args): return deepcopy(h.data['events'])
    h.bundle.lifecycle.event_store = Store()
    with h.bundle.executing(request()): assert h.finish() is True
    assert current_execution.get() is None and observed['finished'] and observed['cleanup']
    assert observed['cached'][0][2]['event'] == 'retrieval.completed'
    assert h.data['events'][0]['data']['terminal_kind'] == 'retrieval_only'


def test_terminal_read_preserves_real_answer_citation_validation(monkeypatch):
    h = Harness(monkeypatch)
    h.data['run']['status'] = 'completed'; h.data['answer'] = 'real answer'
    h.data['answer_event'] = {'citations': ['S1']}
    h.data['evidence'] = [{'label':'S1', 'quote':'real', 'quote_sha256':hashlib.sha256(b'real').hexdigest(), 'content':'a real source'}]
    repo = h.bundle.lifecycle.repository
    assert repo.read_run(RUN, SESSION, request().kbs, request().docs)['citations'] == ('S1',)
    h.data['evidence'][0]['quote'] = 'forged'
    with pytest.raises(LifecycleDenied, match='CITATION_INTEGRITY'): repo.read_run(RUN, SESSION, request().kbs, request().docs)


@pytest.mark.parametrize('field', ['native_receipts_sha256', 'completed_qids', 'owner', 'document_ids'])
def test_retrieval_terminal_read_rejects_tampered_metadata(monkeypatch, field):
    h = Harness(monkeypatch)
    with h.current(): assert h.finish() is True
    h.data['events'][0]['data'][field] = [] if field.endswith('qids') or field.endswith('ids') else 'bad'
    with pytest.raises(LifecycleDenied): h.bundle.lifecycle.repository.read_run(RUN, SESSION, request().kbs, request().docs)


def test_retrieval_sse_terminal_rechecks_commit_without_fabricating_answer():
    calls = []
    def read(*args):
        calls.append(args)
        return {'status':'running' if len(calls)==1 else 'completed', 'answer':'', 'citations':()}
    class Store:
        def list_events(self, *args): return [{'seq':1,'event':'retrieval.completed','data':{'terminal_kind':'retrieval_only'}}]
    events, saved = read_committed_events(read, None, Store(), RUN, SESSION, request().kbs, request().docs, 0)
    assert len(calls) == 2 and saved['status'] == 'completed'
    assert events[0]['data'] == {'terminal_kind':'retrieval_only'}
    def cancelled(*args): return {'status':'cancelled','answer':'','citations':()}
    assert read_committed_events(cancelled, None, Store(), RUN, SESSION, request().kbs, request().docs, 0)[0] == []


def frozen_dev24_qids():
    source = Path(__file__).resolve().parents[2] / 'var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl'
    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'cc67e423a370cfd67437ebf70976caef140b2b9f71f5731307a528b9720bf97d'
    rows = [json.loads(line) for line in raw.splitlines()]
    qids = tuple(row['qid'] for row in rows)
    assert len(qids) == len(set(qids)) == 24
    assert {dataset: sum(row['dataset'] == dataset for row in rows)
            for dataset in ('scifact', 'miracl-zh', 'longbench-zh')} == {
                'scifact': 8, 'miracl-zh': 8, 'longbench-zh': 8}
    return qids


@pytest.mark.parametrize('qid', frozen_dev24_qids())
def test_actual_frozen_qid_is_accepted_without_rewriting(qid):
    metadata = checked_retrieval_completion((qid,), DIGEST)
    assert metadata['completed_qids'] == [qid]
    assert metadata['completed_qids'][0].encode('utf-8') == qid.encode('utf-8')


def test_actual_frozen_qids_commit_and_read_back_exactly(monkeypatch):
    h = Harness(monkeypatch)
    qids = frozen_dev24_qids()
    with h.current(): assert h.finish(qids=qids) is True
    saved = h.bundle.lifecycle.repository.read_run(RUN, SESSION, request().kbs, request().docs)
    assert saved['retrieval']['completed_qids'] == list(qids)
    assert saved['retrieval']['count'] == 24
    assert saved['answer'] == '' and saved['citations'] == ()
    assert h.commits == 1 and len(h.data['events']) == 1


class QidAlias(str): pass


@pytest.mark.parametrize('qid', [None, True, 1, b'277616#0', QidAlias('277616#0'), '', ' ',
    '277616#', '#0', 'x#0', '277616#-1', '277616#0#1', '277616#../x',
    '../277616#0', '277616%230', '277616#0?x=1', '277616#0\x00',
    '277616#0\n', '277616#0\r', '277616#0\t', '277616#0\x7f',
    '277616#0\x85', '２７７#0', "277616#0'); DROP TABLE rag_runs;--",
    'a' * 129, '0' * 64 + '#' + '1' * 64])
def test_qid_compatibility_preserves_invalid_input_rejection_before_sql(monkeypatch, qid):
    h = Harness(monkeypatch)
    with h.current(), pytest.raises(LifecycleDenied, match='RETRIEVAL_COMPLETION_INPUT_INVALID'):
        h.finish(qids=(qid,))
    assert h.calls == [] and h.commits == 0 and h.data['events'] == []


@pytest.mark.parametrize('qid', ['a' * 128, '0' * 63 + '#' + '1' * 64])
def test_qid_compatibility_keeps_existing_terminal_length_limit(qid):
    assert checked_retrieval_completion((qid,), DIGEST)['completed_qids'] == [qid]
