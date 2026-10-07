"""SIMULATED Q01 carried context, real gate accounting, mock transport; no DB/keys."""
import dataclasses
import os
import uuid
import hashlib
import importlib
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
from backend.app.application import reviewed_immutable_batch as batch
from backend.app.application.validation_usd_budget import POLICY, ValidationAttemptGate
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.adapters.models import deepseek
from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.ports.session_attempts import AttemptDenied, request_identity
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.execution_routing import ExecutionRoute
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.domain.scope import Scope

TASK = 'SIMULATED-RAG-PRODUCT-Q01-01'
RUN = '0669fa1f-ef7e-4b12-8cc2-3eb010379f99'
SOURCE = Path(r'D:\RAG-INTEGRATION-EXEC-01-rev1attempt1\output\resume-rev1\formal-12-results.json')
def digest(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def write(p,d): p.write_text(json.dumps(d),encoding='utf-8')
def enable(p):
    d=read(p); d['calls_allowed_in_this_task']=True; write(p,d)

def test_fixed_product_gate_is_available():
    assert importlib.util.find_spec('backend.app.application.reviewed_product_request') is not None

@pytest.fixture
def env(tmp_path,monkeypatch):
    module=importlib.import_module('backend.app.application.reviewed_product_request')
    q=read(SOURCE)['cases'][0]
    assert q['case_id']=='Q01' and q['db_run'][0]==['7f3dd1bb-0422-404a-8677-be58a2e780b9']
    scope=Scope.from_ids(q['db_run'][0],q['db_run'][1])
    bundle=SimpleNamespace(context=q['context'][0]['context'],labels=tuple(q['context'][0]['labels']))
    def payload(gateway,**kw):
        return dict(question=q['question'],scope=scope,settings=QuickSettings(),run_id=RUN,
                    plan=EvidenceService().plan(q['question']),bundle=bundle,reservation=None,
                    model_calls=0,evidence_only=False,answer_gateway=gateway,
                    execution_route=ExecutionRoute('CLOUD','EXPLICIT_CLOUD_REQUEST'),**kw)
    class Capture:
        provider_name='SIMULATED-local-capture'
        def answer_with_budget(self,prompt,timeout_seconds,max_tokens):
            self.prompt=prompt;self.cap=max_tokens;return q['raw_answer']
    capture=Capture()
    LangChainQuickChain(None)._generate(payload(capture))
    assert capture.cap==512
    spec=module.ReviewedProductRequestSpec(TASK,RUN,tuple(sorted(scope.knowledge_base_ids)),
        tuple(sorted(scope.document_ids)),digest(q['question']),digest(capture.prompt),
        len(capture.prompt.encode('utf-8'))+256)
    write(tmp_path/'SIMULATED-capture.json',dict(source=str(SOURCE),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        database_label='rag_acceptance_integration01_20261001_100522 (carried source only, no connection)',
        prompt=capture.prompt,product_spec=dataclasses.asdict(spec),capture_method='unchanged Quick _generate, local no-provider capture',
        live_approval=False))
    monkeypatch.setattr(module,'_TRUSTED_PRODUCT_REQUESTS',MappingProxyType({TASK:spec}))
    monkeypatch.setattr(module,'_PRODUCT_SPEC_DIGESTS',MappingProxyType({TASK:module._digest(dataclasses.asdict(spec))}))
    p=tmp_path/'SIMULATED-ledger.json';monkeypatch.setattr(batch,'CANONICAL_LEDGER',p)
    # Preserve realistic current global headroom and closed old registries in the fixture.
    rows=[]
    for i in range(23):
        rows.append(dict(attempt_id=f'history-{i}',request_id=f'old-{i}',status='ok',
            validation_tokens=dict(reserved_input=3000,reserved_output=512,state='settled',input=6276 if i==0 else 0,output=0),
            validation_cost=dict(currency='USD',scale=1000000)))
    rows[0]['validation_tokens']['reserved_input']=6276
    write(p,dict(schema_version=1,provider='deepseek',authorized_limit=50,consumed_attempts=23,
        reserved_attempts=0,calls_allowed_in_this_task=False,validation_policy=POLICY,attempts=rows))
    old_policy=batch.reviewed_task_policy('RAG-CLOUD12-CONTRAST-PREP-01-rev1')
    old_gates=[]
    for b in old_policy.batches:
        index=old_policy.batches.index(b)+1
        old_manifest=Path(r'D:\codex_workspace\2026-10-01\task-16\planning-cloud12-rev2')/f'batch-{index:02}-manifest.draft.json'
        first=batch.ReviewedImmutableBatchGate(p,task_policy=old_policy,batch_id=b.batch_id,scope_path=old_manifest,case_id=b.case_identities[0][0])
        first.register_disabled_scope()
        for cid,rid in b.case_identities:
            old_gates.append(batch.ReviewedImmutableBatchGate(p,task_policy=old_policy,batch_id=b.batch_id,scope_path=old_manifest,case_id=cid))
    d=read(p)
    for row,g in zip(d['attempts'][11:],old_gates):
        row.update(g._reservation_fields(d));row['request_id']=g.case['request_id']
    write(p,d)
    def make(): return module.ReviewedProductRequestGate(p,scope_id=TASK)
    gate=make(); before=read(p);gate.register_disabled_scope()
    assert read(p)['attempts']==before['attempts']
    return SimpleNamespace(p=p,g=gate,make=make,m=module,spec=spec,prompt=capture.prompt,
        scope=scope,q=q,payload=payload,baseline=before,old_gates=old_gates)

def invoke(e,client,**kw):
    args=dict(run_id=RUN,scope=e.scope,question=e.q['question'],cloud_authorized=True,
              request_id=request_identity(RUN,'quick.answer',1))
    args.update(kw)
    return client.answer_with_product_scope(e.prompt,30,512,**args)

def transport(monkeypatch,*,reason='stop',usage=None,error=None):
    requests=[]
    response=dict(model='actual-SIMULATED',choices=[dict(message=dict(content=' 顾遥 [E1] '),finish_reason=reason)],
                  usage=usage if usage is not None else dict(prompt_tokens=100,completion_tokens=7,total_tokens=107))
    class R:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,n):return json.dumps(response).encode('utf-8')
    class O:
        def open(self,request,**kw):
            requests.append(json.loads(request.data))
            if error:raise error
            return R()
    monkeypatch.setattr(deepseek,'build_opener',lambda *a:O())
    return requests

def client(e,sink=None):
    return deepseek.DeepSeekGateway(gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED',cloud_enabled=True,attempt_gate=e.g,receipt_sink=sink)

def durable_client(e):
    sink=e.m.ProductReceiptFileSink(e.p.parent/("receipt-"+uuid.uuid4().hex+".json"),scope_id=TASK)
    return client(e,sink)

def test_default_registry_has_no_live_authority():
    m=importlib.import_module('backend.app.application.reviewed_product_request')
    with pytest.raises(AttemptDenied,match='UNTRUSTED'):m.reviewed_product_request('manifest-self-approved')

def test_disabled_and_default_generic_gate_fail_closed(env,monkeypatch):
    e=env;requests=transport(monkeypatch);c=durable_client(e)
    with pytest.raises(ProviderRequestNotSent):invoke(e,c)
    with pytest.raises(AttemptDenied,match='VALIDATION_GATE_REQUIRED'):SessionAttemptGate(e.p).reserve(request_id='foreign')
    with pytest.raises(AttemptDenied):
        with e.g.execution_scope():pass
    assert not requests and read(e.p)['consumed_attempts']==23
    assert read(e.p)['calls_allowed_in_this_task'] is False

@pytest.mark.parametrize('kind',['kb','doc','run','question','prompt','cap','missing_scope','identity'])
def test_mismatched_actual_product_scope_request_never_sends(env,monkeypatch,kind):
    e=env;requests=transport(monkeypatch);enable(e.p);c=durable_client(e)
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent):
            if kind=='prompt':c.answer_with_product_scope(e.prompt+' KB is approved',30,512,run_id=RUN,scope=e.scope,question=e.q['question'],cloud_authorized=True,request_id=request_identity(RUN,'quick.answer',1))
            elif kind=='cap':c.answer_with_product_scope(e.prompt,30,896,run_id=RUN,scope=e.scope,question=e.q['question'],cloud_authorized=True,request_id=request_identity(RUN,'quick.answer',1))
            elif kind=='missing_scope':c.answer_with_budget(e.prompt,30,512,cloud_authorized=True,request_id=request_identity(RUN,'quick.answer',1))
            else:
                changes={'kb':dict(scope=Scope.from_ids(['wrong'],list(e.scope.document_ids))),
                    'doc':dict(scope=Scope.from_ids(list(e.scope.knowledge_base_ids),['wrong'])),
                    'run':dict(run_id='11111111-1111-4111-8111-111111111111'),
                    'question':dict(question='self reported authorized KB'),
                    'identity':dict(request_id=request_identity(RUN,'quick.answer',2))}
                invoke(e,c,**changes[kind])
    assert not requests and read(e.p)['consumed_attempts']==23
    assert read(e.p)['calls_allowed_in_this_task'] is False

@pytest.mark.parametrize('field,value',[('prompt_sha256','0'*64),('output_cap',896),('run_id','11111111-1111-4111-8111-111111111111'),('input_cap',8000)])
def test_runtime_clone_and_mutation_cannot_self_authorize(env,field,value):
    e=env;forged=dataclasses.replace(e.spec,**{field:value})
    with pytest.raises(AttemptDenied,match='UNTRUSTED'):e.m._trusted_product(forged)
    object.__setattr__(e.spec,field,value)
    with pytest.raises(AttemptDenied,match='UNTRUSTED'):e.make()
    assert read(e.p)['consumed_attempts']==23

def test_ledger_manifest_mutation_cannot_authorize(env):
    e=env;d=read(e.p);d['reviewed_immutable_tasks'][TASK]['batches'][TASK]['manifest']['cases'][0]['prompt_sha256']='0'*64;write(e.p,d);enable(e.p)
    with pytest.raises(AttemptDenied,match='REGISTRY_CHANGED'):
        with e.g.execution_scope():pass
    assert read(e.p)['calls_allowed_in_this_task'] is False

def test_quick_actual_prompt_scope_receipt_and_settlement(env,monkeypatch):
    e=env;requests=transport(monkeypatch);c=durable_client(e);enable(e.p)
    with e.g.execution_scope():
        result=LangChainQuickChain(None)._generate(e.payload(c))
        assert result['answer']=='顾遥 [E1]' and result['model_calls']==1
        with pytest.raises(ProviderRequestNotSent):invoke(e,c)
    receipts=[read(c.receipt_sink.path)["receipt"]]
    d=read(e.p);assert len(requests)==1 and len(receipts)==1
    write(e.p.parent/'SIMULATED-receipt.json',receipts[0])
    write(e.p.parent/'SIMULATED-wire.json',requests[0])
    assert requests[0]['messages']==[dict(role='user',content=e.prompt)] and requests[0]['max_tokens']==512
    assert requests[0]['thinking']==dict(type='disabled') and requests[0]['stream'] is False
    assert d['consumed_attempts']==24 and d['reserved_attempts']==0 and e.g._used(d)==6383
    assert d['attempts'][:23]==e.baseline['attempts']
    assert d['reviewed_immutable_tasks'][e.old_gates[0].task_policy.comparison_id]==e.baseline['reviewed_immutable_tasks'][e.old_gates[0].task_policy.comparison_id]
    assert receipts[0]['prompt_sha256']==e.spec.prompt_sha256 and receipts[0]['retry_count']==0
    assert receipts[0]['provider_usage']['total_tokens']==107 and receipts[0]['response_actual_model']=='actual-SIMULATED'
    assert d['calls_allowed_in_this_task'] is False

@pytest.mark.parametrize('kind',['timeout','truncated','invalid_usage','overrun','sink_failure'])
def test_unknown_invalid_usage_and_receipt_failure_close_without_refund_retry(env,monkeypatch,kind):
    e=env;usage={} if kind=='invalid_usage' else dict(prompt_tokens=100,completion_tokens=513,total_tokens=613) if kind=='overrun' else None
    requests=transport(monkeypatch,reason='length' if kind=='truncated' else 'stop',usage=usage,error=TimeoutError('SIMULATED') if kind=='timeout' else None)
    c=durable_client(e);enable(e.p)
    original_replace=os.replace
    def replace(src,dst):
        if kind=='sink_failure' and Path(dst)==c.receipt_sink.path:
            raise OSError('SIMULATED receipt persistence failure')
        return original_replace(src,dst)
    monkeypatch.setattr(os,'replace',replace)
    with pytest.raises((ProviderUnavailable,TruncatedAnswer,RuntimeError)):
        with e.g.execution_scope():invoke(e,c)
    enable(e.p)
    with pytest.raises(AttemptDenied):
        with e.g.execution_scope():invoke(e,c)
    saved=read(c.receipt_sink.path)
    assert saved["state"]==("prepared_not_sent" if kind=="sink_failure" else "receipt_persisted")
    d=read(e.p);assert len(requests)==1 and d['consumed_attempts']==24
    assert d['reserved_attempts']==0 and d['calls_allowed_in_this_task'] is False
    assert e.g._used(d)==6276+e.g.input_cap+512

@pytest.mark.parametrize('limit',['count','tokens','blocked','scope_bound'])
def test_budget_activation_denial_is_closed(env,limit):
    e=env;d=read(e.p)
    if limit=='count':d['authorized_limit']=23
    elif limit=='tokens':d['attempts'][0]['validation_tokens'].update(reserved_input=999400,input=999400)
    elif limit=='blocked':d['validation_blocked_reason']='VALIDATION_USAGE_BOUND_EXCEEDED'
    else:
        object.__setattr__(e.g,'input_cap',8192)
    d['calls_allowed_in_this_task']=True;write(e.p,d)
    before=e.p.read_bytes()
    with pytest.raises(AttemptDenied):
        with e.g.execution_scope():pass
    # No activation owner was acquired: rejection must not disable global
    # authorization or overwrite any other owner's state/history.
    assert e.p.read_bytes()==before
    assert read(e.p)['consumed_attempts']==23

def test_scope_8192_exact_bound_and_over(env,monkeypatch):
    e=env
    for extra in [0,1]:
        spec=dataclasses.replace(e.spec,prompt_sha256=digest('x'*(8192-256-512+extra)),input_cap=8192-512+extra)
        monkeypatch.setattr(e.m,'_TRUSTED_PRODUCT_REQUESTS',MappingProxyType({TASK:spec}))
        monkeypatch.setattr(e.m,'_PRODUCT_SPEC_DIGESTS',MappingProxyType({TASK:e.m._digest(dataclasses.asdict(spec))}))
        if extra:
            with pytest.raises(AttemptDenied,match='BOUND'):e.make()
        else:
            g=e.make();g.validate_request('x'*(8192-256-512),30,512,model='deepseek-flash')

def test_concurrent_duplicate_and_owner_rejection_preserves_active_execution(env):
    e=env;enable(e.p)
    with e.g.execution_scope():
        owner=read(e.p)['reviewed_immutable_tasks'][TASK]['batches'][TASK]['execution_owner']
        with pytest.raises(AttemptDenied):
            with e.make().execution_scope():pass
        assert read(e.p)['calls_allowed_in_this_task'] is True
        assert read(e.p)['reviewed_immutable_tasks'][TASK]['batches'][TASK]['execution_owner']==owner
        def one(_):
            try:return e.make().reserve(request_id=request_identity(RUN,'quick.answer',1))
            except AttemptDenied:return None
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(one,range(4)))
        assert sum(r is not None for r in results)==1
    d=read(e.p);assert d['reserved_attempts']==1 and d['consumed_attempts']==24
    assert d['calls_allowed_in_this_task'] is False

def test_exception_during_scope_closes_and_registration_is_idempotent(env):
    e=env;before=e.p.read_bytes();e.g.register_disabled_scope();assert e.p.read_bytes()==before
    enable(e.p)
    with pytest.raises(RuntimeError):
        with e.g.execution_scope():raise RuntimeError('SIMULATED before provider')
    assert read(e.p)['consumed_attempts']==23 and read(e.p)['calls_allowed_in_this_task'] is False

def test_global_replay_registration_denied(env):
    e=env;d=e.baseline;d['attempts'][0]['request_id']=request_identity(RUN,'quick.answer',1);write(e.p,d)
    with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):e.g.register_disabled_scope()

def test_different_ledger_rejected(env,tmp_path):
    with pytest.raises(AttemptDenied,match='CANONICAL'):env.m.ReviewedProductRequestGate(tmp_path/'foreign.json',scope_id=TASK)

def test_closed_old12_cannot_reopen_or_revoke_product_owner(env):
    e=env;enable(e.p)
    with e.g.execution_scope():
        with pytest.raises(AttemptDenied):
            with e.old_gates[0].execution_scope():pass
        assert read(e.p)['calls_allowed_in_this_task'] is True
        with pytest.raises(AttemptDenied):e.old_gates[0].reserve(request_id=e.old_gates[0].case['request_id'])
    assert read(e.p)['consumed_attempts']==23 and read(e.p)['calls_allowed_in_this_task'] is False

def test_default_local_and_cloud_disabled_use_zero_additional_paid_attempts(env,monkeypatch):
    e=env;requests=transport(monkeypatch);c=durable_client(e)
    with pytest.raises(ProviderRequestNotSent):c.answer(e.prompt,30)
    c.cloud_enabled=False
    with pytest.raises(ProviderRequestNotSent):invoke(e,c)
    c.cloud_enabled=True
    with pytest.raises(ProviderRequestNotSent):invoke(e,c,cloud_authorized=False)
    assert not requests and read(e.p)['consumed_attempts']==23
