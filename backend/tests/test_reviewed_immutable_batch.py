"""SIMULATED isolated accounting and mock transport; never real egress or keys."""
import dataclasses
import hashlib
import importlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.app.application.validation_usd_budget import POLICY, ValidationAttemptGate, SYNTHETIC_PROMPT
from backend.app.application import static_pair_validation as old_pair
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.ports.session_attempts import AttemptDenied
from backend.app.adapters.models import deepseek
from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer

PLAN = Path(r'D:\codex_workspace\2026-10-01\task-16\planning-cloud12-rev2')
TASK = 'RAG-CLOUD12-CONTRAST-PREP-01-rev1'

def read(p): return json.loads(p.read_text(encoding='utf-8'))
def write(p,d): p.write_text(json.dumps(d), encoding='utf-8')
def enable(p):
    d=read(p);d['calls_allowed_in_this_task']=True;write(p,d)

def test_reviewed_immutable_gate_exists():
    assert importlib.util.find_spec('backend.app.application.reviewed_immutable_batch') is not None

@pytest.fixture
def setup(tmp_path,monkeypatch):
    module=importlib.import_module('backend.app.application.reviewed_immutable_batch')
    p=tmp_path/'SIMULATED-ledger.json'
    monkeypatch.setattr(module,'CANONICAL_LEDGER',p)
    old_manifest=read(Path(r'D:\codex_workspace\2026-10-01\task-17\pair-manifest.json'))
    usage=[121]*6+[129,127,116,213,225]
    attempts=[]
    for i,t in enumerate(usage):
        attempts.append({'attempt_id':f'history-{i}','request_id':f'old-{i}' if i<7 else old_manifest['cases'][i-7]['request_id'],
            'status':'ok','validation_tokens':{'reserved_input':3000,'reserved_output':512,'state':'settled','input':t,'output':0},
            'validation_cost':{'currency':'USD','scale':1000000}})
    old_scope={'sha256':old_pair.APPROVED_SHA256,'manifest':old_manifest,'enabled':False}
    write(p,{'schema_version':1,'provider':'deepseek','authorized_limit':50,'consumed_attempts':11,'reserved_attempts':0,
        'calls_allowed_in_this_task':False,'validation_policy':POLICY,'attempts':attempts,
        'static_pair_scopes':{old_pair.BATCH_ID:old_scope}})
    policy=module.reviewed_task_policy(TASK)
    def make(cid='Q01'):
        n=(int(cid[1:])-1)//4+1
        scope=PLAN/f'batch-{n:02}-manifest.draft.json';m=read(scope)
        return module.ReviewedImmutableBatchGate(p,task_policy=policy,batch_id=m['batch_id'],scope_path=scope,case_id=cid)
    def register_all():
        for cid in ['Q01','Q05','Q09']:make(cid).register_disabled_scope()
    g=make(); assert g._used(read(p))==1536
    return p,g,make,register_all,module

def reserve(g): return g.reserve(request_id=g.case['request_id'])
def settle(g):
    a=reserve(g);g.finish_with_usage(a,'ok',{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2});return a

def test_registration_disabled_idempotent_and_history_untouched(setup):
    p,g,make,register_all,_=setup;old=read(p)
    register_all();before=p.read_bytes();register_all();assert p.read_bytes()==before
    d=read(p);assert d['attempts']==old['attempts'] and d['static_pair_scopes']==old['static_pair_scopes']
    assert d['consumed_attempts']==11 and g._used(d)==1536
    assert all(not b['enabled'] for b in d['reviewed_immutable_tasks'][TASK]['batches'].values())
    with pytest.raises(AttemptDenied):reserve(g)
    enable(p)
    with pytest.raises(AttemptDenied):reserve(g)
    with pytest.raises(AttemptDenied):g.register_disabled_scope()

@pytest.mark.parametrize('field,value',[('provider','other'),('model','deepseek-v4-pro'),('output_cap',256),
    ('token_limit',999999),('batch_token_limit',999999),('attempt_limit',13)])
def test_manifest_cannot_authorize_forged_policy(setup,field,value):
    p,g,_,_,m=setup
    forged=dataclasses.replace(g.task_policy,**{field:value})
    with pytest.raises(AttemptDenied,match='TRUST'):
        m.ReviewedImmutableBatchGate(p,task_policy=forged,batch_id=g.batch_id,scope_path=g.scope_path,case_id='Q01')
    with pytest.raises(AttemptDenied):m.reviewed_task_policy('unreviewed')

@pytest.mark.parametrize('mutation',['prompt','messages','context','identity','model','cap','budget','digest'])
def test_unreviewed_manifest_cannot_self_authorize(setup,tmp_path,mutation):
    p,g,_,_,mod=setup;m=read(g.scope_path)
    if mutation=='prompt':m['cases'][0]['prompt']+='private'
    elif mutation=='messages':m['cases'][0]['messages'][0]['role']='system'
    elif mutation=='context':m['cases'][0]['context_sha256']='0'*64
    elif mutation=='identity':m['cases'][0]['request_id']='foreign'
    elif mutation=='model':m['model']='other'
    elif mutation=='cap':m['output_cap']=256
    elif mutation=='budget':m['token_bound_planned']=999999
    else:m['sha256']='0'*64
    f=tmp_path/'self-authorizing.json';write(f,m)
    with pytest.raises(AttemptDenied,match='MANIFEST'):
        mod.ReviewedImmutableBatchGate(p,task_policy=g.task_policy,batch_id=g.batch_id,scope_path=f,case_id='Q01')

@pytest.mark.parametrize('mutation',['batch','case','ledger','policy_object'])
def test_unreviewed_entry_and_alternate_ledger_denied(setup,tmp_path,mutation):
    p,g,_,_,m=setup
    kw=dict(task_policy=g.task_policy,batch_id=g.batch_id,scope_path=g.scope_path,case_id='Q01')
    target=p
    if mutation=='batch':kw['batch_id']='fourth-unreviewed'
    elif mutation=='case':kw['case_id']='Q05'
    elif mutation=='ledger':target=tmp_path/'other.json'
    else:kw['task_policy']=None
    with pytest.raises(AttemptDenied):m.ReviewedImmutableBatchGate(target,**kw)

@pytest.mark.parametrize('field',['manifest','sha256','policy','limits'])
def test_registry_tamper_denies_and_finally_closes(setup,field):
    p,g,_,reg,_=setup;reg();enable(p)
    with g.execution_scope():
        d=read(p);t=d['reviewed_immutable_tasks'][TASK]
        if field=='manifest':t['batches'][g.batch_id]['manifest']['cases'][0]['prompt']='private'
        elif field=='sha256':t['batches'][g.batch_id]['sha256']='0'*64
        elif field=='policy':t['policy_sha256']='0'*64
        else:t['policy']['token_limit']=999999
        write(p,d)
        with pytest.raises(AttemptDenied):reserve(g)
    assert not read(p)['calls_allowed_in_this_task']
    assert not read(p)['reviewed_immutable_tasks'][TASK]['batches'][g.batch_id]['enabled']

@pytest.mark.parametrize('where',['attempt','old_scope','other_registered_task'])
def test_global_identity_overlap_registration_denied(setup,where):
    p,g,_,_,_=setup;d=read(p)
    if where=='attempt':d['attempts'][0]['request_id']=g.case['request_id']
    elif where=='old_scope':d['static_pair_scopes'][old_pair.BATCH_ID]['manifest']['cases'][0]['request_id']=g.case['request_id']
    else:d['reviewed_immutable_tasks']={'SIMULATED-other':{'batches':{'b':{'manifest':{'cases':[{'request_id':g.case['request_id']} ]}}}}}
    write(p,d)
    with pytest.raises(AttemptDenied,match='ALREADY'):g.register_disabled_scope()

@pytest.mark.parametrize('reason',['unknown','truncated'])
def test_unknown_retains_bound_blocks_continuation_and_no_replay(setup,reason):
    p,g,make,reg,_=setup;reg();enable(p)
    with pytest.raises(RuntimeError):
        with g.execution_scope():
            a=reserve(g);g.finish(a,reason)
            assert g._used(read(p))==1536+g.input_cap+512
            with pytest.raises(AttemptDenied):reserve(make('Q02'))
            raise RuntimeError('SIMULATED failure')
    assert not read(p)['calls_allowed_in_this_task']
    enable(p)
    with pytest.raises(AttemptDenied):
        with make('Q05').execution_scope():pass
    assert read(p)['consumed_attempts']==12

@pytest.mark.parametrize('mutation',['prompt','model','cap','timeout','identity'])
def test_exact_request_guard_before_send(setup,monkeypatch,mutation):
    p,g,_,reg,_=setup;reg();enable(p)
    monkeypatch.setattr(deepseek,'build_opener',lambda *a:pytest.fail('must not open mocked transport'))
    kw=dict(prompt=g.case['prompt'],timeout_seconds=30,max_tokens=512,cloud_authorized=True,request_id=g.case['request_id'])
    model=g.model
    if mutation=='model':model='deepseek-v4-pro'
    else:kw[{'prompt':'prompt','cap':'max_tokens','timeout':'timeout_seconds','identity':'request_id'}[mutation]]={
        'prompt':'private','cap':256,'timeout':31,'identity':'foreign'}.get(mutation)
    with g.execution_scope():
        client=deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED-NOT-REAL',model=model,cloud_enabled=True,attempt_gate=g)
        with pytest.raises(ProviderRequestNotSent):client.answer_with_budget(**kw)
    assert read(p)['consumed_attempts']==11

@pytest.mark.parametrize('cid',[f'Q{i:02}' for i in range(1,13)])
def test_all_twelve_exact_actual_wire_and_receipts(setup,monkeypatch,cid):
    p,_,make,reg,_=setup;reg();g=make(cid);enable(p);receipts=[]
    usage={'prompt_tokens':30,'completion_tokens':5,'total_tokens':35,'prompt_cache_hit_tokens':0}
    raw='  SIMULATED raw answer [E1]  '
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return json.dumps({'model':'actual-SIMULATED','choices':[{'message':{'content':raw},'finish_reason':'stop'}],'usage':usage}).encode()
    class Opener:
        def open(self,request,timeout):
            d=read(p);assert d['consumed_attempts']==12 and d['reserved_attempts']==1
            body=json.loads(request.data)
            assert body=={'model':'deepseek-flash','messages':g.case['messages'],'thinking':{'type':'disabled'},'max_tokens':512,'temperature':0,'stream':False}
            assert hashlib.sha256(body['messages'][0]['content'].encode()).hexdigest()==g.case['prompt_sha256']
            assert request.full_url==deepseek.ENDPOINT and timeout==30
            return Response()
    def opener(*handlers):
        assert any(isinstance(h,deepseek._NoRedirect) for h in handlers)
        assert any(isinstance(h,deepseek.ProxyHandler) and h.proxies=={} for h in handlers)
        return Opener()
    monkeypatch.setattr(deepseek,'build_opener',opener)
    with g.execution_scope():
        client=deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED',model=g.model,cloud_enabled=True,attempt_gate=g,receipt_sink=receipts.append)
        assert client.answer_with_budget(g.case['prompt'],30,512,cloud_authorized=True,request_id=g.case['request_id'])==raw.strip()
        with pytest.raises(ProviderRequestNotSent):client.answer_with_budget(g.case['prompt'],30,512,cloud_authorized=True,request_id=g.case['request_id'])
    assert len(receipts)==1 and receipts[0]['raw_answer']==raw and receipts[0]['provider_usage']==usage
    assert receipts[0]['response_actual_model']=='actual-SIMULATED' and receipts[0]['ttft_ms'] is None
    assert receipts[0]['finish_reason']=='stop' and g._used(read(p))==1536+35
    assert not read(p)['calls_allowed_in_this_task']

@pytest.mark.parametrize('reason',['length','content_filter','timeout','invalid_usage'])
def test_failure_receipt_unknown_budget_no_retry(setup,monkeypatch,reason):
    p,g,make,reg,_=setup;reg();enable(p);receipts=[];opened=[]
    class R:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return json.dumps({'model':'actual-SIMULATED','choices':[{'message':{'content':' raw refusal '},'finish_reason':'stop' if reason=='invalid_usage' else reason}],
            'usage':{} if reason=='invalid_usage' else {'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}).encode()
    class O:
        def open(self,*a,**kw):
            opened.append(1)
            if reason=='timeout':raise TimeoutError('SIMULATED')
            return R()
    monkeypatch.setattr(deepseek,'build_opener',lambda *a:O())
    with g.execution_scope():
        client=deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED',cloud_enabled=True,attempt_gate=g,receipt_sink=receipts.append)
        with pytest.raises((ProviderUnavailable,TruncatedAnswer)):
            client.answer_with_budget(g.case['prompt'],30,512,cloud_authorized=True,request_id=g.case['request_id'])
        with pytest.raises(ProviderRequestNotSent):
            client.answer_with_budget(g.case['prompt'],30,512,cloud_authorized=True,request_id=g.case['request_id'])
    assert len(opened)==1 and len(receipts)==1 and receipts[0]['ttft_ms'] is None
    if reason!='timeout':assert receipts[0]['raw_answer']==' raw refusal '
    assert g._used(read(p))==1536+g.input_cap+512 and not read(p)['calls_allowed_in_this_task']


def test_all_three_batches_share_count_bounds_and_preserve_old_scope(setup):
    p,g,make,reg,_=setup;old=read(p);reg()
    for start in [1,5,9]:
        enable(p)
        with make(f'Q{start:02}').execution_scope():
            for n in range(start,start+4):settle(make(f'Q{n:02}'))
            with pytest.raises(AttemptDenied):reserve(make(f'Q{start:02}'))
    d=read(p);assert d['consumed_attempts']==23 and g._used(d)==1536+24
    rows=[r for r in d['attempts'] if r.get('reviewed_comparison')==TASK]
    assert len(rows)==12 and sum(r['validation_tokens']['reserved_input']+512 for r in rows)==25615
    assert d['attempts'][:11]==old['attempts'] and d['static_pair_scopes']==old['static_pair_scopes']
    enable(p)
    with pytest.raises(AttemptDenied):
        with g.execution_scope():pass

@pytest.mark.parametrize('which',['batch','group'])
@pytest.mark.parametrize('extra',[0,1])
def test_conservative_batch_group_bound_exact_and_over(setup,which,extra):
    p,g,make,reg,_=setup;reg()
    previous=['Q01'] if which=='batch' else ['Q01','Q05','Q09']
    for cid in previous:
        enable(p)
        with make(cid).execution_scope():settle(make(cid))
    enable(p)
    with g.execution_scope():
        d=read(p);rows=d['attempts'][11:];incoming=make('Q02').input_cap+512
        if which=='batch':bounds=[12288-incoming+extra]
        else:bounds=[32768-incoming-2*12288+extra,12288,12288]
        for row,bound in zip(rows,bounds):row['validation_tokens']['reserved_input']=bound-512
        write(p,d)
        if extra:
            with pytest.raises(AttemptDenied,match='TOKEN_BOUND'):reserve(make('Q02'))
        else:reserve(make('Q02'))

@pytest.mark.parametrize('extra',[0,1])
def test_global_token_exact_boundary_after_activation(setup,extra):
    p,g,_,reg,_=setup;reg();enable(p)
    with g.execution_scope():
        d=read(p);t=d['attempts'][0]['validation_tokens'];delta=1000000-g.input_cap-512-g._used(d)+extra
        t['reserved_input']+=delta;t['input']+=delta;write(p,d)
        if extra:
            with pytest.raises(AttemptDenied,match='TOKEN_CAP'):reserve(g)
        else:reserve(g);assert g._used(read(p))==1000000


def fill_to(p,count):
    d=read(p)
    for i in range(count-d['consumed_attempts']):
        row=json.loads(json.dumps(d['attempts'][0]));row['attempt_id']=f'fill-{i}';row['request_id']=f'fill-{i}';d['attempts'].append(row)
    d['consumed_attempts']=count;write(p,d)

def test_concurrent_distinct_requests_last_global_slot_one_winner(setup):
    p,g,make,reg,_=setup;reg();enable(p)
    with g.execution_scope():
        fill_to(p,49)
        def one(cid):
            try:return reserve(make(cid))
            except AttemptDenied:return None
        with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(one,['Q01','Q02']))
        assert sum(x is not None for x in r)==1 and read(p)['consumed_attempts']==50


def test_concurrent_duplicate_and_registration_are_idempotent(setup):
    p,g,make,reg,_=setup
    with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(lambda _:make().register_disabled_scope(),range(8)))
    reg();enable(p)
    with g.execution_scope():
        def one(_):
            try:return reserve(make())
            except AttemptDenied:return None
        with ThreadPoolExecutor(max_workers=6) as pool:r=list(pool.map(one,range(8)))
        assert sum(x is not None for x in r)==1
    assert read(p)['consumed_attempts']==12


def test_only_one_batch_active_and_all_registered_required(setup):
    p,g,make,reg,_=setup;g.register_disabled_scope();enable(p)
    with pytest.raises(AttemptDenied):
        with g.execution_scope():pass
    d=read(p);d['calls_allowed_in_this_task']=False;write(p,d);reg();enable(p)
    with g.execution_scope():
        with pytest.raises(AttemptDenied):
            with make('Q05').execution_scope():pass


def test_generic_gate_and_old_caps_unchanged(setup):
    p,g,_,_,_=setup
    for gate in [ValidationAttemptGate(p),SessionAttemptGate(p)]:
        client=deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED',cloud_enabled=True,attempt_gate=gate)
        with pytest.raises(ProviderRequestNotSent):client.answer_with_budget('private',30,256,cloud_authorized=True,request_id='foreign')
    with pytest.raises(AttemptDenied):g.validate_request(g.case['prompt'],30,256,model=g.model)

@pytest.mark.parametrize('limit',['count','tokens'])
def test_old_new_gate_compete_under_one_global_lock(setup,limit):
    p,g,_,reg,_=setup;reg();enable(p)
    with g.execution_scope():
        if limit=='count':fill_to(p,49)
        else:
            d=read(p);t=d['attempts'][0]['validation_tokens'];delta=1000000-g.input_cap-512-g._used(d)
            t['reserved_input']+=delta;t['input']+=delta;write(p,d)
        old=ValidationAttemptGate(p)
        def one(which):
            try:return reserve(g) if which=='new' else old.reserve(request_id='SIMULATED-old-competing')
            except AttemptDenied:return None
        with ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(one,['new','old']))
        assert sum(x is not None for x in r)==1
        if limit=='count':assert read(p)['consumed_attempts']==50
        else:assert g._used(read(p))<=1000000
