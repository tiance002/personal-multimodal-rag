"""SIMULATED/offline. No actual provider tokens, prices or transport."""
import os
import copy
import types
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import pytest
from backend.app.application.context_window import ContextManager, ContextDenied
from backend.app.application.memory import MemoryRetriever, MemoryService
from backend.app.ports.memory import MemoryDenied, item_value, subject_id, memory_message
from backend.app.ports.context_budget import turn_messages, canonical
from backend.tests.test_p55b_context_window import History, Store, window, turns, SCOPE
from backend.tests.test_p5_router_generation import make_chain, GOOD
from backend.app.application.rule_router_v1 import RouterPolicy
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.ports.providers import ProviderUnavailable

ROOT=Path(__file__).resolve().parents[2]
def baseline_class():
    if os.getenv('P55B2_USE_BASELINE')!='1':return LangChainQuickChain
    mod=types.ModuleType('frozen_b1_quick')
    import sys
    sys.modules[mod.__name__]=mod
    p=ROOT/'var/reports/p55b-memory-r1/before/backend/app/application/quick_chain.py'
    exec(compile(p.read_bytes(),str(p),'exec'),mod.__dict__)
    return mod.LangChainQuickChain

class Chat:
    execution_kind='SIMULATED'
    def __init__(self,provider,model,result=GOOD):self.provider_name,self.chat_model,self.result=provider,model,result;self.calls=[]
    def answer_with_budget(self,prompt,*args):
        self.calls.append(prompt)
        if isinstance(self.result,Exception):raise self.result
        return self.result

@pytest.mark.parametrize('fallback',[False,True])
def test_old_cloud_or_fallback_cannot_use_local_window(monkeypatch,fallback):
    base,*_=make_chain(monkeypatch,policy=RouterPolicy(mode='OFF'))
    local=Chat('ollama','SIMULATED-local',ProviderUnavailable('unavailable') if fallback else GOOD)
    cloud=Chat('SIMULATED-cloud','SIMULATED-cloud')
    m=ContextManager(History([]),Store(),{'local_chat':replace(window(),provider=local.provider_name,model=local.chat_model),
        'cloud_chat':replace(window(),provider=cloud.provider_name,model=cloud.chat_model,window_tokens=None)})
    chain=baseline_class()(base.knowledge_gateway,answer_gateway=local,cloud_answer_gateway=cloud,budget_gate=InMemoryBudgetGate(100),context_manager=m)
    result=chain.invoke('A 的成本是多少？',SCOPE,run_id='current',conversation_id='c',cloud_allowed_by_kb={'kb':True},
        settings=QuickSettings(cloud_enabled=True,prefer_cloud=not fallback,cloud_fallback_enabled=fallback,cloud_cost_estimate_microunits=1))
    assert result.error_code=='MODEL_CONTEXT_CAPACITY_UNKNOWN'
    assert cloud.calls==[]
    assert len(local.calls)==int(fallback)

def test_history_E1_different_document_is_removed_only_from_outbound_copy():
    row=turns(1,text='Old document quote [E1]')[0];before=copy.deepcopy(row)
    func=turn_messages
    if os.getenv('P55B2_USE_BASELINE')=='1':
        mod=types.ModuleType('old_context');p=ROOT/'var/reports/p55b-memory-r1/before/backend/app/ports/context_budget.py'
        import sys
        sys.modules[mod.__name__]=mod;exec(compile(p.read_bytes(),str(p),'exec'),mod.__dict__);func=mod.turn_messages
    assert '[E1]' not in canonical(func(row))
    assert row==before and '[E1]' in row['answer']

def test_quick_history_before_current_evidence_and_instructions_first():
    m=ContextManager(History(turns(1,text='old [E1]')),Store(),{'local':window()})
    p=m.quick_prompt('SYSTEM SAFETY\nQuestion: now\nEvidence: current document [E1]',conversation='c',scope=SCOPE,run='now',roles=['local'],output_tokens=100)
    assert p.startswith('SYSTEM SAFETY') and p.index('old')<p.index('Question: now')<p.index('current document [E1]')
    assert p.count('[E1]')==1

class Repo:
    def __init__(self):
        self.rows=[dict(id='memory-1',kind='preference',fact_key='language',content='Prefer Chinese [E1]',version=1,status='active')]
        self.enabled=True
    def list_items(self,*a,**k):return copy.deepcopy(self.rows) if self.enabled else []

def test_memory_budget_order_revocation_and_no_evidence_marker():
    repo=Repo();r=MemoryRetriever(repo,'SIMULATED',token_budget=800)
    p=r.quick_context('SYSTEM\nQuestion: now\nEvidence: current [E1]',scope=SCOPE,windows=[window()],output_tokens=100)
    assert p.index('SYSTEM')<p.index('LONG-TERM')<p.index('Question:') and p.count('[E1]')==1
    r.validate_frozen(p,SCOPE)
    repo.enabled=False
    with pytest.raises(ContextDenied,match='REVOKED'):r.validate_frozen(p,SCOPE)
    assert r.quick_context('p',scope=SCOPE,windows=[window()],output_tokens=100)=='p'

def test_memory_too_large_cannot_evict_current_atomic_evidence():
    r=MemoryRetriever(Repo(),'SIMULATED',token_budget=1)
    p='current exact table [E1]'
    assert r.quick_context(p,scope=SCOPE,windows=[window()],output_tokens=100)==p

def test_smart_memory_not_checkpointed_and_deleted_not_replayed():
    repo=Repo();r=MemoryRetriever(repo,'SIMULATED',token_budget=800)
    m=ContextManager(History([]),Store(),{'local':window()},memory_retriever=r)
    kw=dict(conversation='c',scope=SCOPE,run='r',role='local',system='SYSTEM',tools=[],output_tokens=100)
    original=[{'role':'user','content':'question'}]
    messages=m.smart_messages(current=original,**kw)
    assert messages[0]['name']=='long_term_memory' and messages[-1]==original[0]
    repo.enabled=False
    assert m.fit_live(messages,**kw)==original and m.store.checkpoint is None

@pytest.mark.parametrize('kind,key,content',[('unknown','key','x'),('fact','','x'),('fact','k','x'*301),('fact','k','\x00')])
def test_structured_memory_invalid_rejected(kind,key,content):
    with pytest.raises(MemoryDenied):item_value(kind,key,content)

@pytest.mark.parametrize('principal',[None,'','other-user',42])
def test_no_fake_principal(principal):
    with pytest.raises(MemoryDenied,match='PRINCIPAL_REQUIRED'):subject_id(principal)

def test_real_extraction_admission_stays_closed_before_claim():
    repo=SimpleNamespace(claim=lambda *a:pytest.fail('no claim'))
    service=MemoryService(repo,'SIMULATED',extractor=SimpleNamespace(execution_kind='REAL'))
    with pytest.raises(MemoryDenied,match='REAL_ADMISSION_CLOSED'):service.extract_once('job')

def test_context_and_memory_restore_with_short_history_checkpoint():
    h=History(turns(1,text='old'))
    s=Store();s.checkpoint={'covered':[],'summary':'Past [E1]','kind':'SESSION'}
    m=ContextManager(h,s,{'local':window()},memory_retriever=MemoryRetriever(Repo(),'SIMULATED',token_budget=800))
    result=m.smart_messages(conversation='c',scope=SCOPE,run='r',role='local',system='SYSTEM',current=[{'role':'user','content':'now'}],tools=[],output_tokens=100)
    wire=canonical(result)
    assert wire.index('LONG-TERM')<wire.index('SUMMARY')<wire.index('question-0')
    assert result[-1]=={'role':'user','content':'now'}
    assert '[E1]' not in wire and s.checkpoint['summary']=='Past [E1]'
