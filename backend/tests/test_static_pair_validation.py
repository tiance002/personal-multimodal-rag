"""SIMULATED only: isolated ledgers and mocked HTTP, no credentials/network."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app.application import static_pair_validation as pair
from backend.app.application.validation_usd_budget import ValidationAttemptGate, POLICY, SYNTHETIC_PROMPT
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.ports.session_attempts import AttemptDenied
from backend.app.adapters.models import deepseek
from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer

MANIFEST = Path(r'D:\codex_workspace\2026-10-01\task-17\pair-manifest.json')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    p = tmp_path / 'SIMULATED-ledger.json'
    monkeypatch.setattr(pair, 'CANONICAL_LEDGER', p)
    attempts = []
    for i in range(7):
        attempts.append({'attempt_id': f'history-{i}', 'request_id': f'old-{i}', 'status': 'ok',
            'validation_tokens': {'reserved_input': 337, 'reserved_output': 512, 'state': 'settled',
                                  'input': 121 if i < 6 else 129, 'output': 0},
            'validation_cost': {'currency': 'USD', 'scale': 1000000}})
    d = {'schema_version': 1, 'provider': 'deepseek', 'authorized_limit': 50,
         'consumed_attempts': 7, 'reserved_attempts': 0, 'calls_allowed_in_this_task': False,
         'validation_policy': POLICY, 'attempts': attempts}
    p.write_text(json.dumps(d),encoding='utf-8')
    def gate(case='Q01'):
        return pair.StaticPairValidationGate(p, scope_path=MANIFEST, expected_sha256=pair.APPROVED_SHA256, case_id=case)
    g = gate()
    assert g._used(d) == 855
    return p, g, gate


def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p, d):p.write_text(json.dumps(d),encoding='utf-8')
def enable(p):
    d = read(p);d['calls_allowed_in_this_task'] = True;write(p, d)
def register(g):g.register_disabled_scope(MANIFEST, pair.APPROVED_SHA256)
def reserve(g):return g.reserve(request_id=g.case['request_id'])


def test_disabled_idempotent_registration_and_legacy_unchanged(setup):
    p,g,_ = setup
    before = read(p)
    register(g);raw = p.read_bytes();register(g)
    assert p.read_bytes() == raw
    d=read(p)
    assert d['attempts'] == before['attempts'] and d['consumed_attempts']==7
    assert g._used(d)==855 and not d['static_pair_scopes'][pair.BATCH_ID]['enabled']
    with pytest.raises(AttemptDenied, match='SESSION_CALLS_DISABLED'):reserve(g)
    enable(p)
    with pytest.raises(AttemptDenied, match='PAIR_SCOPE_DISABLED'):reserve(g)
    with pytest.raises(AttemptDenied):register(g)


@pytest.mark.parametrize('field,value', [('model','deepseek-v4-pro'),('output_cap',512),('token_bound',9000),
    ('attempt_limit',5),('batch_id','foreign'),('cases',[])])
def test_manifest_tamper_denied(setup,tmp_path,field,value):
    p,g,_=setup
    m=json.loads(MANIFEST.read_bytes());m[field]=value
    f=tmp_path/'tampered.json';f.write_text(json.dumps(m))
    with pytest.raises(AttemptDenied,match='PAIR_MANIFEST_CHANGED'):
        pair.StaticPairValidationGate(p,scope_path=f,expected_sha256=pair.APPROVED_SHA256,case_id='Q01')


def test_alternate_ledger_and_fifth_case_denied(setup,tmp_path):
    p,g,make=setup
    with pytest.raises(AttemptDenied,match='CANONICAL'):
        pair.StaticPairValidationGate(tmp_path/'other.json',scope_path=MANIFEST,expected_sha256=pair.APPROVED_SHA256,case_id='Q01')
    with pytest.raises(AttemptDenied,match='UNREGISTERED'):make('Q05')
    with pytest.raises(AttemptDenied):g.register_disabled_scope(MANIFEST,'0'*64)


@pytest.mark.parametrize('mutate', ['prompt','model','cap','timeout','identity','registry'])
def test_request_mutations_denied(setup,mutate):
    p,g,_=setup;register(g);enable(p)
    with g.execution_scope():
        if mutate=='identity':
            with pytest.raises(AttemptDenied):g.reserve(request_id='foreign')
        elif mutate=='registry':
            d=read(p);d['static_pair_scopes'][pair.BATCH_ID]['manifest']['token_bound']=9000;write(p,d)
            with pytest.raises(AttemptDenied):reserve(g)
        else:
            args=dict(prompt=g.case['prompt'],timeout_seconds=30,max_tokens=256,model=g.model)
            args[{'prompt':'prompt','model':'model','cap':'max_tokens','timeout':'timeout_seconds'}[mutate]]={
                'prompt':'private','model':'other','cap':512,'timeout':31}[mutate]
            with pytest.raises(AttemptDenied):g.validate_request(**args)
    assert not read(p)['static_pair_scopes'][pair.BATCH_ID]['enabled']


def test_four_attempts_bound_replay_and_fifth_denied(setup):
    p,g,make=setup;register(g);enable(p)
    with g.execution_scope():
        for case in ['Q01','Q02','Q04','Q07']:
            c=make(case);a=reserve(c);c.finish(a,'unknown')
        with pytest.raises(AttemptDenied,match='PAIR_ATTEMPT_LIMIT'):reserve(g)
    d=read(p)
    assert d['consumed_attempts']==11 and d['reserved_attempts']==0 and g._used(d)==855+4205
    assert all(r['retry_count']==0 for r in d['attempts'][7:])


def test_unknown_no_retry_and_exception_closes(setup):
    p,g,_=setup;register(g);enable(p)
    with pytest.raises(RuntimeError):
        with g.execution_scope():
            a=reserve(g);g.finish(a,'unknown')
            with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):reserve(g)
            raise RuntimeError('SIMULATED')
    assert not read(p)['static_pair_scopes'][pair.BATCH_ID]['enabled']
    assert g._used(read(p))==855+869


def test_global_duplicate_cross_scope(setup):
    p,g,_=setup
    d=read(p);d['attempts'][0]['request_id']=g.case['request_id'];write(p,d)
    with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):register(g)


def test_duplicate_inserted_after_registration(setup):
    p,g,_=setup;register(g);enable(p)
    with g.execution_scope():
        d=read(p);d['attempts'][0]['request_id']=g.case['request_id'];write(p,d)
        with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):reserve(g)


def test_batch_8192_is_reserved_not_settled_budget(setup):
    p,g,make=setup;register(g);enable(p)
    with g.execution_scope():
        c=make('Q02');a=reserve(c);c.finish_with_usage(a,'ok',{'prompt_tokens':1,'completion_tokens':1})
        d=read(p);row=d['attempts'][-1];row['validation_tokens']['reserved_input']=7937;write(p,d)
        with pytest.raises(AttemptDenied,match='PAIR_TOKEN_BOUND'):reserve(g)


@pytest.mark.parametrize('which',['calls','tokens','invalid_history'])
def test_global_bound_after_activation_atomic(setup,which):
    p,g,_=setup;register(g);enable(p)
    with g.execution_scope():
        d=read(p)
        if which=='calls':
            for i in range(43):
                row=json.loads(json.dumps(d['attempts'][0]));row['attempt_id']=f'fill-{i}';row['request_id']=f'fill-{i}'
                d['attempts'].append(row)
            d['consumed_attempts']=50
        else:
            t=d['attempts'][0]['validation_tokens']
            t.update(reserved_input=997900 if which=='tokens' else 998400,
                     reserved_output=1024,state='unknown')
        write(p,d);before=p.read_bytes()
        if which=='tokens':
            assert g._used(d)==999658
            assert g._used(d)+g.input_cap+g.output_cap==1000527
        elif which=='invalid_history':
            assert sum(t.get('input',0)+t.get('output',0) if t['state']=='settled'
                       else t['reserved_input']+t['reserved_output']
                       for t in (r['validation_tokens'] for r in d['attempts']))==1000158
        expected={'calls':'SESSION_CALL_LIMIT','tokens':'VALIDATION_TOKEN_CAP',
                  'invalid_history':'VALIDATION_RECORD_INVALID'}[which]
        with pytest.raises(AttemptDenied,match=expected):reserve(g)
        assert p.read_bytes()==before


def test_concurrent_duplicate_reserve_one_winner(setup):
    p,g,make=setup;register(g);enable(p)
    with g.execution_scope():
        def run(_):
            try:return reserve(make())
            except AttemptDenied:return None
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(run,range(16)))
        assert sum(x is not None for x in results)==1
    assert read(p)['consumed_attempts']==8


@pytest.mark.parametrize('case',['Q01','Q02','Q04','Q07'])
@pytest.mark.parametrize('reason',['stop','length','content_filter','timeout'])
def test_wire_and_receipt_with_mock_transport(setup,monkeypatch,reason,case):
    p,g,make=setup;g=make(case);register(g);enable(p);receipts=[]
    raw_answer='  SIMULATED raw refusal/content  '
    usage={'prompt_tokens':30,'completion_tokens':6,'total_tokens':36,'prompt_cache_hit_tokens':5}
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return json.dumps({'model':'actual-SIMULATED-model','choices':[{
            'message':{'content':raw_answer},'finish_reason':reason}], 'usage':usage}).encode()
    class Opener:
        def open(self,request,timeout):
            assert read(p)['consumed_attempts']==8 and read(p)['reserved_attempts']==1
            payload=json.loads(request.data)
            assert request.full_url==deepseek.ENDPOINT and timeout==30
            assert payload=={'model':g.model,'messages':[{'role':'user','content':g.case['prompt']}],
                'thinking':{'type':'disabled'},'max_tokens':256,'temperature':0,'stream':False}
            if reason=='timeout':raise TimeoutError('SIMULATED')
            return Response()
    def opener(*handlers):
        assert any(isinstance(h,deepseek._NoRedirect) for h in handlers)
        assert any(isinstance(h,deepseek.ProxyHandler) and h.proxies=={} for h in handlers)
        return Opener()
    monkeypatch.setattr(deepseek,'build_opener',opener)
    client=deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED-NOT-A-CREDENTIAL',cloud_enabled=True,attempt_gate=g,receipt_sink=receipts.append)
    with g.execution_scope():
        if reason=='stop':assert client.answer_with_budget(g.case['prompt'],30,256,cloud_authorized=True,request_id=g.case['request_id'])==raw_answer.strip()
        else:
            with pytest.raises((ProviderUnavailable,TruncatedAnswer)):
                client.answer_with_budget(g.case['prompt'],30,256,cloud_authorized=True,request_id=g.case['request_id'])
        with pytest.raises(ProviderRequestNotSent):
            client.answer_with_budget(g.case['prompt'],30,256,cloud_authorized=True,request_id=g.case['request_id'])
    assert len(receipts)==1 and receipts[0]['ttft_ms'] is None and receipts[0]['retry_count']==0
    if reason!='timeout':
        assert receipts[0]['raw_answer']==raw_answer and receipts[0]['provider_usage']==usage
        assert receipts[0]['finish_reason']==reason and receipts[0]['response_actual_model']=='actual-SIMULATED-model'
    else:assert receipts[0]['response_actual_model']=='UNKNOWN'
    assert read(p)['consumed_attempts']==8 and read(p)['reserved_attempts']==0
    assert g._used(read(p))==855+(36 if reason=='stop' else g.input_cap+256)
    assert not read(p)['static_pair_scopes'][pair.BATCH_ID]['enabled']


def test_old_and_generic_caps_unchanged(setup,monkeypatch):
    p,g,_=setup
    with pytest.raises(AttemptDenied):ValidationAttemptGate(p,max_output_tokens=256)
    old=ValidationAttemptGate(p)
    with pytest.raises(AttemptDenied):old.validate_request('private',30,512,model=old.model)
    for gate in [old,SessionAttemptGate(p)]:
        client=deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED',cloud_enabled=True,attempt_gate=gate)
        with pytest.raises(ProviderRequestNotSent,match='LIMIT_INVALID'):
            client.answer_with_budget(SYNTHETIC_PROMPT,30,256,cloud_authorized=True,request_id='old')


@pytest.mark.parametrize('extra',[0,1])
def test_exact_global_token_boundary(setup,extra):
    p,g,_=setup;register(g);enable(p)
    with g.execution_scope():
        d=read(p);t=d['attempts'][0]['validation_tokens']
        t.update(reserved_input=998397+extra,input=998397+extra)
        write(p,d)
        if extra:
            with pytest.raises(AttemptDenied,match='VALIDATION_TOKEN_CAP'):reserve(g)
        else:
            reserve(g)
            assert g._used(read(p))==1000000


def test_concurrent_registration_idempotent(setup):
    p,g,make=setup
    def one(_):register(make());return True
    with ThreadPoolExecutor(max_workers=8) as pool:assert all(pool.map(one,range(8)))
    assert len(read(p)['static_pair_scopes'])==1 and read(p)['consumed_attempts']==7


def test_pending_registration_and_model_missing_receipt(setup,monkeypatch):
    p,g,_=setup;register(g);enable(p)
    with g.execution_scope():
        a=reserve(g)
    d=read(p);d['calls_allowed_in_this_task']=False;write(p,d)
    with pytest.raises(AttemptDenied,match='REGISTRATION_REQUIRES_DISABLED'):register(g)
    g.finish(a,'unknown')
    assert not read(p)['static_pair_scopes'][pair.BATCH_ID]['enabled']
