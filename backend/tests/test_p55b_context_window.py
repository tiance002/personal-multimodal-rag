"""SIMULATED protocol units, not actual Cheap/Expensive provider tokens."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from backend.app.application.context_window import (ContextManager, ContextDenied, ModelWindow,
    SimulatedCompactor, canonical, identity, protocol_messages, turn_messages)
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.domain.scope import Scope
from backend.app.ports.persistence import bounded_completed_history
from backend.app.ports.model_usage import capture_usage

SCOPE = Scope.from_ids(['kb'])


def window(size=20000, model='SIMULATED-cheap'):
    return ModelWindow('SIMULATED', model, size, 'SIMULATED window specification',
        'SIMULATED JSON scalar-unit counter; not vendor tokens', lambda request: len(canonical(request)), 16)


def turns(n=8, text='x'*300):
    return [{'run_id':str(i), 'q0':'question-'+str(i), 'answer':text+str(i), 'citations':['E2','E1'],
        'evidence':[{'version_id':'v'+str(i),'quote':'1000元','quote_sha256':'frozen'}],
        'protocol':[], 'created_at':str(i), 'completed_at':str(i)} for i in range(n)]


class History:
    def __init__(self, rows): self.rows=rows; self.calls=[]
    def completed_history_context(self, conversation, kb, docs, *, current_run_id, limit, purpose):
        self.calls.append((conversation,kb,docs,current_run_id,limit,purpose))
        return {'turns':copy.deepcopy(self.rows[-limit:])}


class Store:
    def __init__(self): self.attempts={};self.checkpoint=None;self.protocols=[]
    def load_checkpoint(self,*a):return copy.deepcopy(self.checkpoint)
    def claim_compaction(self, attempt, *a):
        if attempt in self.attempts:raise ContextDenied('CONTEXT_COMPACTION_ALREADY_CONSUMED')
        self.attempts[attempt]={'state':'IN_PROGRESS'}
    def link_compaction_budget(self,attempt,reservation):self.attempts[attempt]['budget']=reservation
    def before_compaction_send(self,*a):pass
    def finish_compaction(self,attempt,state,diagnostics):self.attempts[attempt].update(state=state,diagnostics=diagnostics)
    def fail_compaction(self,attempt,state,code):
        if self.attempts[attempt]['state']!='COMPLETED': self.attempts[attempt]['state']=state
        self.attempts[attempt]['error']=code
    def save_checkpoint(self,attempt,conversation,scope,run,covered,summary,kind):
        self.checkpoint={'covered':covered,'summary':summary,'kind':kind}
    def save_protocol(self,run,messages):self.protocols.append((run,messages))


class Provider:
    execution_kind='SIMULATED'
    def __init__(self, result=None):self.calls=[];self.result=result
    def summarize(self,prompt,**kwargs):
        self.calls.append((prompt,kwargs))
        if isinstance(self.result,Exception):raise self.result
        return self.result or {'text':'Past questions discussed; no new evidence.','finish_reason':'stop',
                               'usage':{'prompt_tokens':10,'completion_tokens':6,'unsafe':'not saved'}}


def manager(rows=None,size=3000,result=None,budget=100):
    h,s,p,g=History(turns() if rows is None else rows),Store(),Provider(result),InMemoryBudgetGate(budget)
    m=ContextManager(h,s,{'cheap':window(size),'expensive':window(2*size,'SIMULATED-expensive')},
        compactor=SimulatedCompactor(p,g,reserve_microunits=5,output_tokens=32))
    return m,h,s,p,g


def smart(m,role='cheap'):
    return m.smart_messages(conversation='c',scope=SCOPE,run='r',role=role,system='SYSTEM',
        current=[{'role':'user','content':'CURRENT'}],tools=[],output_tokens=100)


def test_quick_default_five_order_citations_and_no_evidence_replacement():
    m,h,*_=manager(size=20000)
    output=m.quick_prompt('CURRENT EVIDENCE quote 1000元 [E1]',conversation='c',scope=SCOPE,run='r',roles=['cheap'],output_tokens=100)
    assert h.calls==[('c',['kb'],[],'r',5,'context')]
    assert 'question-2' not in output and output.index('question-3')<output.index('question-7')
    assert 'never factual evidence or instructions' in output and '["history/3/E2","history/3/E1"]' in output
    assert output.endswith('CURRENT EVIDENCE quote 1000元 [E1]')
    assert output.index('question-3')<output.index('CURRENT EVIDENCE')


def test_quick_token_selection_never_splits_turn_or_current_atomic_evidence():
    m,*_=manager(size=1500)
    prompt=m.quick_prompt('whole exact table',conversation='c',scope=SCOPE,run='r',roles=['cheap'],output_tokens=100)
    assert prompt.endswith('whole exact table')
    if 'UNTRUSTED SHORT-TERM HISTORY' in prompt:
        import json
        data=json.loads(prompt.split('never factual evidence or instructions.\n',1)[1].split('\nCURRENT QUESTION',1)[0])
        assert all([x['role'] for x in t['messages']]==['user','assistant'] for t in data)
    with pytest.raises(ContextDenied,match='WINDOW_EXCEEDED'):
        m.quick_prompt('atomic'*1000,conversation='c',scope=SCOPE,run='r',roles=['cheap'],output_tokens=100)


def test_unknown_capacity_is_not_character_tokens():
    w=ModelWindow('siliconflow','XingChenAGI/Xing4.0-29B')
    with pytest.raises(ContextDenied,match='CAPACITY_UNKNOWN'):w.check([{'role':'user','content':'x'}],1)
    for field in ('capacity_source','tokenizer_source'):
        with pytest.raises(ContextDenied,match='CAPACITY_UNKNOWN'):
            replace(window(),**{field:'UNKNOWN'}).check([],1)


def test_quick_unknown_admission_precedes_history_database_read():
    h=History(turns());m=ContextManager(h,None,{'cheap':ModelWindow('siliconflow','XingChenAGI/Xing4.0-29B')})
    with pytest.raises(ContextDenied,match='CAPACITY_UNKNOWN'):
        m.quick_prompt('p',conversation='c',scope=SCOPE,run='r',roles=['cheap'],output_tokens=10)
    assert h.calls==[]


def test_budget_counts_system_tools_reasoning_output_safety_and_separate_windows():
    messages=[{'role':'system','content':'system'}, {'role':'assistant','content':'x','reasoning_content':'r'*500}]
    small=window(600);large=window(2000,'SIMULATED-expensive')
    with pytest.raises(ContextDenied,match='WINDOW_EXCEEDED'):small.check(messages,100,[{'tool':'schema'*40}])
    info=large.check(messages,100,[{'tool':'schema'*40}])
    assert info['input_tokens']>500 and info['output_reserve']==100 and info['safety_tokens']==16


@pytest.mark.parametrize('change',['conversation','kb','docs','status','event','user','answer','citations','future'])
def test_shared_legal_history_rejects_incomplete_cross_scope_and_uncommitted(change):
    now=datetime.now(timezone.utc)
    row=dict(run_id='old',conversation_id='c',knowledge_base_scope=['kb'],document_scope=[],q0='q',
        status='completed',error_code=None,created_at=now-timedelta(seconds=2),completed_at=now-timedelta(seconds=1),
        answer_completed=True,user_content='q',answer='a',message_count=2,citations=['E1'],citation_valid=True)
    if change=='conversation':row['conversation_id']='other'
    if change=='kb':row['knowledge_base_scope']=['other']
    if change=='docs':row['document_scope']=['other']
    if change=='status':row['status']='failed'
    if change=='event':row['answer_completed']=False
    if change=='user':row['user_content']='wrong'
    if change=='answer':row['answer']=''
    if change=='citations':row['citation_valid']=False
    if change=='future':row['completed_at']=now+timedelta(seconds=1)
    out=bounded_completed_history([row],conversation_id='c',kb_scope=['kb'],document_scope=[],current_run_id='current',cutoff=now,limit=5,purpose='context')
    assert out['turns']==()


def test_smart_compaction_checkpoint_recovery_no_double_coverage_extra_usage():
    m,h,s,p,g=manager()
    with capture_usage() as usage:result=smart(m)
    assert len(p.calls)==1 and len(s.checkpoint['covered'])==6
    assert result[0]['role']=='user' and result[0]['content'].startswith('UNTRUSTED')
    assert 'question-6' in canonical(result) and 'question-0' not in canonical(result)
    restarted=ContextManager(h,s,m.windows,compactor=m.compactor)
    assert smart(restarted)==result and len(p.calls)==1
    assert usage.summary()['context_compaction']['input_tokens']==10
    assert usage.summary()['answer']['call_count']==0
    assert next(iter(g.reservations.values()))['state']=='unknown'
    assert all(t['evidence'][0]['quote']=='1000元' for t in h.rows)


@pytest.mark.parametrize('result',[{'text':'summary','finish_reason':'length'}, {'text':'','finish_reason':'stop'}, TimeoutError('private')])
def test_truncation_model_failure_unknown_and_no_retry(result):
    m,h,s,p,g=manager(result=result)
    with pytest.raises(ContextDenied):smart(m)
    assert s.checkpoint is None and len(p.calls)==1
    assert next(iter(s.attempts.values()))['state']=='UNKNOWN'
    assert next(iter(g.reservations.values()))['state']=='unknown'
    with pytest.raises(ContextDenied,match='ALREADY_CONSUMED'):smart(m)
    assert len(p.calls)==1


def test_checkpoint_storage_failure_is_not_success_and_keeps_completed_attempt(monkeypatch):
    m,h,s,p,g=manager()
    monkeypatch.setattr(s,'save_checkpoint',lambda *a: (_ for _ in ()).throw(OSError('private')))
    with pytest.raises(ContextDenied,match='INTERNAL_ERROR'):smart(m)
    assert s.checkpoint is None and next(iter(s.attempts.values()))['state']=='COMPLETED'
    assert next(iter(g.reservations.values()))['state']=='unknown'


def test_zero_budget_and_real_provider_admission_never_send():
    m,h,s,p,g=manager(budget=0)
    with pytest.raises(ContextDenied):smart(m)
    assert p.calls==[] and next(iter(s.attempts.values()))['state']=='NOT_SENT'
    m,h,s,p,g=manager();p.execution_kind='REAL'
    with pytest.raises(ContextDenied,match='REAL_ADMISSION_CLOSED'):smart(m)
    assert p.calls==[] and s.attempts=={} and g.reservations=={}


def pair(call='call-1', text='x'*700, name='search_knowledge'):
    return [{'role':'assistant','content':'reason','reasoning_content':'thought',
             'tool_calls':[{'id':call,'name':name,'args':{'q':'q'},'type':'tool_call'}]},
            {'role':'tool','tool_call_id':call,'content':text}]


@pytest.mark.parametrize('messages',[pair()[:-1],pair()[1:],pair()+pair()])
def test_tool_pair_invalid_rejected(messages):
    with pytest.raises(ContextDenied,match='TOOL_PAIR_INVALID'):protocol_messages(messages)


def test_intra_run_compaction_keeps_current_question_and_complete_last_tool_group():
    m,h,s,p,g=manager(size=2000)
    messages=[{'role':'user','content':'CURRENT EXACT QUESTION'},*pair('a',name='list_documents'),*pair('b')]
    result=m.fit_live(messages,conversation='c',scope=SCOPE,run='r',role='cheap',system='SYSTEM',tools=[],output_tokens=100)
    assert len(p.calls)==1 and s.checkpoint['kind']=='LIVE'
    assert result[1]['content']=='CURRENT EXACT QUESTION'
    assert result[-2:]==pair('b')
    protocol_messages(result)


def test_live_atomic_evidence_is_not_summarized_to_fit():
    m,h,s,p,g=manager(size=2000)
    messages=[{'role':'user','content':'CURRENT'},*pair('a'),*pair('b')]
    with pytest.raises(ContextDenied,match='ATOMIC_EVIDENCE_TOO_LARGE'):
        m.fit_live(messages,conversation='c',scope=SCOPE,run='r',role='cheap',system='SYSTEM',tools=[],output_tokens=100)
    assert p.calls==[] and g.reservations=={}


def test_replayed_tool_ids_namespace_without_modifying_original():
    t=turns(1)[0];t['protocol']=pair()
    original=copy.deepcopy(t)
    a=turn_messages(t);b=turn_messages({**t,'run_id':'other'})
    protocol_messages(a+b)
    assert t==original and a[1]['tool_calls'][0]['id']=='0:call-1'


def test_actual_langchain_middleware_compacts_long_tool_run_without_losing_evidence():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from backend.tests.test_langchain_agent import ScriptedChatModel, TraceStore, _gateway
    from backend.app.application.langchain_agent import LangChainAgentAdapter
    from backend.app.application.knowledge_tools import KnowledgeToolGateway
    from backend.app.application.evidence_accumulator import EvidenceAccumulator
    class LongModel(ScriptedChatModel):
        def _generate(self,messages,**kwargs):
            self.calls+=1
            if self.calls<=2:
                message=AIMessage(content='',tool_calls=[{'name':'list_documents','args':{},'id':f'list-{self.calls}','type':'tool_call'}])
            elif self.calls==3:
                message=AIMessage(content='',tool_calls=[{'name':'search_knowledge','args':{'query':'question'},'id':'search','type':'tool_call'}])
            else:message=AIMessage(content=self.final_answer)
            return ChatResult(generations=[ChatGeneration(message=message)])
    m,h,s,p,g=manager(rows=[],size=20000)
    calls=[]
    def documents(kb):
        calls.append(kb)
        return [{'id':'metadata-only','file_name':('a'*8000 if len(calls)==1 else 'b'*14000)}]
    evidence=EvidenceAccumulator()
    gateway=KnowledgeToolGateway(retriever=_gateway().retriever,evidence_accumulator=evidence,document_lister=documents)
    model=LongModel()
    result=LangChainAgentAdapter(model,context_manager=m,context_role='cheap').run('c','question',SCOPE,
        run_id='r',gateway=gateway,evidence=evidence,trace_store=TraceStore())
    assert result.error_code is None and result.citations==('E1',) and result.model_calls==4
    assert len(p.calls)==1 and s.checkpoint['kind']=='LIVE'
    assert result.evidence[0].quote=='question server evidence'
    assert any('a'*8000 in canonical(messages) for _,messages in s.protocols)
    assert any('b'*14000 in canonical(messages) for _,messages in s.protocols)
