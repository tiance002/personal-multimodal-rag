"""SIMULATED provider/DB contracts through the normal Factory/Guard/sender.

No credentials, live DB, transport probes, or real ledger/claim paths.
"""
from dataclasses import replace
from datetime import datetime,timezone
from contextlib import contextmanager
from types import SimpleNamespace
import hashlib,json,threading,os
import pytest

from backend.app.adapters.models.cloud import (
    EmbeddingAdmission,RerankAdmission,SiliconFlowAuthorizedTransport,
    SimulatedEmbeddingCounter,SimulatedRerankCounter,SimulatedXingSend,
    XingChatGrant,XingAuthorizedTransport,
)
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.budget import InMemoryBudgetGate,BudgetDenied
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderRequestNotSent,ProviderUnavailable
from backend.app.ports.xing_zero_reservation import (
    SiliconFlowFreePermit,SiliconFlowFreeBinding,SiliconFlowZeroPriceContract,
    FreeModelPlannedCall,FreeModelRequestContext,free_model_requests,FREE_OWNER_DEADLINE,
)
from backend.app.application.rule_router_v1 import V6XingGenerationBinding,GenerationRole
from backend.tests.test_v6_xing_generation_binding import counter,no_live_sender
from backend.app.adapters.storage import ContentAddressedStorage


@pytest.fixture(autouse=True)
def result_store(tmp_path,monkeypatch):
    monkeypatch.setenv('V6_SIMULATED_RESULT_ROOT',str(tmp_path/'results'))


def test_bootstrap_zero_only_installs_guard_for_explicit_plan(monkeypatch,tmp_path):
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    adapter,permit,claims,gate,wires,payloads,_=embedding_setup(monkeypatch)
    settings=replace(Settings(),storage_root=tmp_path/'typed',database_url='postgresql+psycopg://rag@localhost/SIMULATED',
        cloud_enabled=True,embedding_egress_enabled=True,monthly_cloud_budget_microunits=10,cloud_cost_estimate_microunits=0,
        local_answer_enabled=False)
    typed=build_container(settings,model=None,agent_model=None,cloud_model=None,
        v6_free_model_bindings={'embedding':adapter.free_binding})
    assert typed.store.embedding_provider.free_binding is adapter.free_binding
    assert typed.provider_factory.usage_guards['embedding'].zero_price_permit is permit
    assert typed.budget_gate.monthly_budget_microunits==10
    ordinary=build_container(replace(settings,storage_root=tmp_path/'ordinary'),model=None,
        agent_model=None,cloud_model=None)
    assert not ordinary.provider_factory.usage_guards and not wires and not claims.rows

NOW=datetime(2026,10,10,2,0,tzinfo=timezone.utc)
SCOPE=Scope.from_ids(['public-kb'],['public-doc'])
REGISTRY=ModelRegistry.frozen_defaults()


def sha(value): return hashlib.sha256(value if type(value) is bytes else value.encode()).hexdigest()


class Claims:
    """SIMULATED durable callback shared across adapter rebuilds."""
    def __init__(self): self.rows={};self.lock=threading.Lock();self.permit=None
    def __call__(self,**identity):
        with self.lock:
            if identity['request_id'] in self.rows: raise ValueError('SIMULATED_ALREADY_CONSUMED')
            call=next(c for c in self.permit.calls if c.request_id==identity['request_id'])
            claim=SimpleNamespace(**identity,run_id=self.permit.run_id,qid=call.qid,arm=call.arm,claimed_at_utc=NOW.isoformat())
            self.rows[identity['request_id']]={'claim':claim,'status':'pending','result':None}
            return claim
    def check_before_send(self,*,request_id):
        if request_id not in self.rows: raise ValueError('SIMULATED_CLAIM_MISSING')
        if any((r.get('result') or {}).get('provider_dispatch',{}).get('request_window_blocked') for r in self.rows.values()):
            raise ValueError('SIMULATED_WINDOW_BLOCKED')
    def settle(self,*,request_id,status,result=None,error_code=None):
        self.rows[request_id].update(status=status,result=result,error_code=error_code)


def permit_for(role,calls,*,kind='SIMULATED'):
    spec=REGISTRY.select(role)
    permit=SiliconFlowFreePermit('owner-v6-free-mainpath-r1','SIMULATED-RUN',SCOPE,FREE_OWNER_DEADLINE,
        '1'*64,'2'*64,spec.model_id,role,SiliconFlowZeroPriceContract(spec.model_id),tuple(calls),
        sum(c.input_token_bound+c.output_token_bound for c in calls),'0'*64,kind)
    return replace(permit,frozen_plan_sha256=permit.fingerprint)


def call_for(payload,profile,role='embedding',*,number=0,purpose=None,batch_id=None,count=1,input_bound=10,timeout=10):
    arm='INDEXING' if purpose=='index_embedding' else 'CHEAP_ONLY'
    return FreeModelPlannedCall(f'SIMULATED-{role}-{number}',f'Q{number}',arm,
        purpose or ('query_embedding' if role=='embedding' else 'rerank'),batch_id or f'batch-{number}',
        profile,sha(json.dumps(payload).encode()),input_bound,0,count,timeout)


def context_for(permit,call):
    return FreeModelRequestContext(permit.run_id,call.request_id,call.purpose,call.batch_id,permit.scope)


def embedding_setup(monkeypatch,*,count=1,tokens=10,failure=None,purpose='query_embedding',items=1):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-NOT-A-REAL-KEY')
    admission=EmbeddingAdmission(SimulatedEmbeddingCounter(lambda t:tokens),
        tokenizer_identity='SIMULATED-BGE-PROFILE',non_truncating_verified=True)
    prototype=ProviderFactory(REGISTRY,embedding_admission=admission).build('embedding')
    payloads=[dict(model=prototype.spec.model_id,input=[f'public-{n}-{i}' for i in range(items)],encoding_format='float') for n in range(count)]
    calls=[call_for(p,prototype.input_profile_sha256,number=n,purpose=purpose,count=items,
        input_bound=tokens*items,timeout=60 if purpose=='index_embedding' else 10) for n,p in enumerate(payloads)]
    permit=permit_for('embedding',calls)
    claims=Claims();claims.permit=permit
    gate=InMemoryBudgetGate(10)
    old=gate.reserve(run_id=None,provider='siliconflow',model_name='historical',capability='embedding',estimate_microunits=10)
    gate.mark_unknown(old.reservation_id)
    wires=[]
    def sender(request,timeout):
        wires.append(request.data)
        if isinstance(failure,Exception): raise failure
        p=json.loads(request.data)
        response={'model':prototype.spec.model_id,'data':[{'index':i,'embedding':[1.0]+[0.0]*1023} for i in range(len(p['input']))],
            'usage':{'prompt_tokens':tokens*len(p['input']),'total_tokens':tokens*len(p['input'])}}
        if callable(failure):failure(response)
        return response
    transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=claims,
        sender=SimulatedXingSend(sender),clock=lambda:NOW)
    binding=SiliconFlowFreeBinding(permit,transport,admission,ContentAddressedStorage(os.environ['V6_SIMULATED_RESULT_ROOT']))
    guard=BudgetUsageGuard(gate,prototype.spec,0,zero_price_permit=permit)
    def build():
        return ProviderFactory(REGISTRY,enabled_roles=['embedding'],usage_guards={'embedding':guard},
            free_model_bindings={'embedding':binding}).build('embedding')
    return build(),permit,claims,gate,wires,payloads,build


def embed_one(adapter,permit,payload,call,*,scope=SCOPE,allowed=True,lease_check=lambda:True):
    with free_model_requests((context_for(permit,call),)),model_access('embedding',allowed=allowed,scope=scope):
        return adapter.embed(payload['input'],call.timeout_seconds,purpose=call.purpose,
            batch_id=call.batch_id,lease_check=lease_check)


def test_free_embedding_normal_path_at_existing_full_budget(monkeypatch):
    adapter,permit,claims,gate,wires,payloads,_=embedding_setup(monkeypatch)
    result=embed_one(adapter,permit,payloads[0],permit.calls[0])
    assert result.model=='BAAI/bge-m3' and result.dimensions==1024
    assert result.identity_fingerprint==adapter.identity.fingerprint
    assert len(wires)==len(claims.rows)==1 and sha(wires[0])==permit.calls[0].wire_sha256
    rows=list(gate.reservations.values())
    assert [(r['reserved_microunits'],r['settled_microunits'],r['state']) for r in rows]==[(10,0,'unknown'),(0,None,'unknown')]
    assert gate._used(rows[0]['month'])==gate.monthly_budget_microunits==10
    receipt=adapter.receipts[-1]
    assert receipt['actual_charge']=='UNKNOWN' and receipt['actual_charge_verified'] is False
    summary=claims.rows[permit.calls[0].request_id]['result']
    restored=adapter.free_binding.read_result(summary,request_id=permit.calls[0].request_id)
    assert restored['budget_reservation_id'] in gate.reservations


def test_explicit_finite_window_exceeds_defaults_without_instance_reset(monkeypatch):
    adapter,permit,claims,gate,wires,payloads,_=embedding_setup(monkeypatch,count=11,tokens=2000)
    for call,payload in zip(permit.calls,payloads):embed_one(adapter,permit,payload,call)
    assert adapter.requests==11 and adapter.planned_tokens==22000
    assert adapter.max_requests==10 and adapter.max_planned_tokens==20000
    assert len(wires)==len(claims.rows)==11 and len(gate.reservations)==12


@pytest.mark.parametrize('condition',['scope','egress','wire','profile','deadline','not_planned','no_context','token_bool','index_no_fence'])
def test_free_preflight_denials_send_nothing(monkeypatch,condition):
    index=condition=='index_no_fence'
    adapter,permit,claims,gate,wires,payloads,_=embedding_setup(monkeypatch,purpose='index_embedding' if index else 'query_embedding')
    call=permit.calls[0];payload=payloads[0];scope=SCOPE;allowed=True;lease_check=lambda:True
    if condition=='scope':scope=Scope.from_ids(['other'])
    if condition=='egress':allowed=False
    if condition=='wire':payload['input']=['different']
    if condition=='profile':adapter.admission.tokenizer_identity+='-changed'
    if condition=='deadline':adapter.transport.clock=lambda:datetime(2026,10,10,7,44,tzinfo=timezone.utc)
    if condition=='not_planned':call=replace(call,request_id='unplanned')
    if condition=='token_bool':adapter.admission.counter.count=lambda t:True
    if index:lease_check=None
    with pytest.raises((ValueError,ProviderRequestNotSent)):
        if condition=='no_context':
            with model_access('embedding',allowed=True,scope=SCOPE):adapter.embed(payload['input'],10)
        else:embed_one(adapter,permit,payload,call,scope=scope,allowed=allowed,lease_check=lease_check)
    assert not wires and not claims.rows and len(gate.reservations)==1


@pytest.mark.parametrize('failure',['timeout','missing_usage','wrong_model','overrun','not_sent'])
def test_free_unknown_and_durable_no_replay(monkeypatch,failure):
    error={'timeout':TimeoutError('SIMULATED'),'not_sent':ProviderRequestNotSent('SIMULATED_NOT_SENT')}.get(failure)
    if failure=='missing_usage':error=lambda r:r.pop('usage')
    if failure=='wrong_model':error=lambda r:r.update(model='Pro/BAAI/bge-m3')
    if failure=='overrun':error=lambda r:r.update(usage={'prompt_tokens':11,'total_tokens':11})
    adapter,permit,claims,gate,wires,payloads,build=embedding_setup(monkeypatch,failure=error)
    with pytest.raises((ProviderUnavailable,ProviderRequestNotSent)):embed_one(adapter,permit,payloads[0],permit.calls[0])
    new=list(gate.reservations.values())[1]
    assert new['state']==('released' if failure=='not_sent' else 'unknown') and new['settled_microunits'] is None
    with pytest.raises((ProviderRequestNotSent,ValueError)):embed_one(build(),permit,payloads[0],permit.calls[0])
    assert len(wires)==1 and len(claims.rows)==1


@pytest.mark.parametrize('value',[0,False,True,0.0,-1])
@pytest.mark.parametrize('role',['embedding','rerank','chat_cheap','chat_expensive'])
def test_no_bare_zero_switch(role,value):
    with pytest.raises(ValueError):BudgetUsageGuard(InMemoryBudgetGate(10),REGISTRY.select(role),value)


@pytest.mark.parametrize('field,value',[('model','Pro/BAAI/bge-m3'),('source_sha256','0'*64),
    ('prompt_per_million',1),('completion_per_million',True),('currency','UNKNOWN')])
def test_zero_price_contract_paid_pro_or_unknown_denied(field,value):
    with pytest.raises(ValueError):replace(SiliconFlowZeroPriceContract('BAAI/bge-m3'),**{field:value}).validate()


def test_rerank_normal_same_budget_and_claim_path(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-NOT-A-REAL-KEY')
    admission=RerankAdmission(SimulatedRerankCounter(lambda q,d:7),tokenizer_identity='SIMULATED-RERANK',execution_kind='SIMULATED')
    spec=REGISTRY.select('rerank');payload=dict(model=spec.model_id,query='public',documents=['a','b'],top_n=2,return_documents=True)
    call=call_for(payload,admission.input_profile_sha256,'rerank',count=2,input_bound=14,timeout=30)
    permit=permit_for('rerank',[call]);claims=Claims();claims.permit=permit;gate=InMemoryBudgetGate(0);wires=[]
    def sender(request,timeout):
        wires.append(request.data)
        return dict(model=spec.model_id,results=[dict(index=i,relevance_score=1.0,document={'text':payload['documents'][i]}) for i in (1,0)],
            usage=dict(prompt_tokens=14,completion_tokens=0,total_tokens=14))
    transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=claims,sender=SimulatedXingSend(sender),clock=lambda:NOW)
    binding=SiliconFlowFreeBinding(permit,transport,admission,ContentAddressedStorage(os.environ['V6_SIMULATED_RESULT_ROOT']))
    guard=BudgetUsageGuard(gate,spec,0,zero_price_permit=permit)
    ranker=ProviderFactory(REGISTRY,enabled_roles=['rerank'],usage_guards={'rerank':guard},free_model_bindings={'rerank':binding}).build('rerank')
    hits=[SimpleNamespace(chunk_id='a'),SimpleNamespace(chunk_id='b')]
    with free_model_requests((context_for(permit,call),)),model_access('rerank',allowed=True,scope=SCOPE):
        ranked=ranker.rank('public',hits,{'a':SimpleNamespace(content='a',version_id='version-a'),'b':SimpleNamespace(content='b',version_id='version-b')})
    assert ranked==(hits[1],hits[0]) and len(wires)==1 and claims.rows[call.request_id]['claim'].purpose=='rerank'
    assert list(gate.reservations.values())[0]['state']=='unknown'


@pytest.mark.parametrize('purpose',['answer','understand','rewrite','expansion'])
def test_xing_answer_helpers_typed_zero_same_generation_factory(counter,monkeypatch,purpose):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-NOT-A-REAL-KEY')
    messages=[dict(role='user',content='public')]
    payload=dict(model=REGISTRY.select('chat_cheap').model_id,messages=messages,max_tokens=32,stream=False,enable_thinking=False)
    estimate=counter.measure('siliconflow',payload['model'],payload,scope='public-scope',wire_bytes=json.dumps(payload).encode())
    call=replace(call_for(payload,sha(estimate.renderer_identity),'chat_cheap',purpose=purpose,
        input_bound=estimate.reservation_bound,timeout=20),output_token_bound=32)
    permit=permit_for('chat_cheap',[call]);claims=Claims();claims.permit=permit;wires=[]
    grant=XingChatGrant(permit.grant_id,'public-scope',SCOPE,permit.deadline_utc,(purpose,),'SIMULATED')
    def sender(request,timeout):
        wires.append(request.data)
        return dict(model=payload['model'],choices=[dict(message={'content':'OK'},finish_reason='stop')],
            usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2))
    transport=XingAuthorizedTransport(grant,sender=SimulatedXingSend(sender),clock=lambda:NOW,
        send_claim_callback=claims,free_model_permit=permit)
    binding=V6XingGenerationBinding(grant,counter,transport,permit)
    gate=InMemoryBudgetGate(0);guard=BudgetUsageGuard(gate,REGISTRY.select('chat_cheap'),0,zero_price_permit=permit)
    gateway=ProviderFactory(REGISTRY,enabled_roles=['chat_cheap'],usage_guards={'chat_cheap':guard}).build('chat_cheap',xing_binding=binding)
    role=GenerationRole('chat_cheap',gateway,xing_binding=binding)
    with model_access('chat_cheap',allowed=True):
        if purpose=='answer':result=gateway.generate_with_xing_scope(messages,timeout_seconds=20,max_tokens=32,scope=SCOPE,purpose=purpose,request_id=call.request_id)
        else:result=role.invoke_auxiliary(messages,purpose=purpose,request_id=call.request_id,run_id=permit.run_id,scope=SCOPE,timeout_seconds=20,max_tokens=32)
    assert result.model_reported==payload['model'] and len(wires)==1
    assert list(gate.reservations.values())[0]['state']=='unknown' and gateway.receipts[-1]['actual_charge']=='UNKNOWN'
