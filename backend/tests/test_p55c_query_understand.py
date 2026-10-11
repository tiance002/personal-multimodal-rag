"""SIMULATED Query Understand; never supplier tokens or commercial transport."""
import json
import copy
from dataclasses import replace
from types import SimpleNamespace
from threading import Lock
import pytest
from backend.app.application.query_understand import QueryUnderstandService,intent_and_tasks
from backend.app.ports.query_understand import QueryUnderstandDenied
from backend.app.application.context_window import ContextManager
from backend.app.application.rule_router_v1 import RouterPolicy,decide,MODELS
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.ports.context_budget import identity
from backend.app.ports.model_usage import capture_usage
from backend.tests.test_p55b_context_window import History,Store,window,turns,SCOPE

class AttemptStore:
    """SIMULATED persistence for unit injection, not PG verification."""
    def __init__(self):self.rows={};self.lock=Lock()
    def claim(self,a,r,c,s,p):
        with self.lock:
            if r in self.rows:raise QueryUnderstandDenied('QUERY_ATTEMPT_ALREADY_CONSUMED')
            self.rows[r]={'attempt_id':a,'state':'CLAIMED',**p}
    def reserve(self,a,r,res):self.rows[r].update(state='RESERVED',reservation_id=res)
    def before_send(self,a,r,s):self.rows[r]['state']='DISPATCHING'
    def finish(self,a,r,state,d):self.rows[r].update(state=state,diagnostics=d)

class Parser:
    # Owner: rewrite tests use the configured Cheap identity. Transport,
    # capacity counter and returned usage remain explicitly SIMULATED.
    execution_kind='SIMULATED';model_role='chat_cheap'
    provider_name,chat_model=MODELS[model_role]
    def __init__(self,change=None):self.calls=[];self.change=change
    def understand(self,messages,**kwargs):
        self.calls.append((messages,kwargs))
        if isinstance(self.change,Exception):raise self.change
        output=json.loads(messages[-1]['content'])['expected']
        if callable(self.change):output=self.change(output)
        return {'finish_reason':'stop','output':output,'usage':{'prompt_tokens':17,'completion_tokens':9}}

def context(*qs,memories=None):
    ts=turns(len(qs))
    for t,q in zip(ts,qs):t['q0']=q
    manager=ContextManager(History(ts),Store(),{},memory_retriever=memories)
    return manager

def make(*qs,parser=None,memories=None):
    p=parser or Parser();store=AttemptStore();gate=InMemoryBudgetGate(100000)
    service=QueryUnderstandService(context(*qs,memories=memories),provider=p,attempt_store=store,budget_gate=gate,
        window=replace(window(10000),provider=p.provider_name,model=p.chat_model),reserve_microunits=7,egress_enabled=True)
    return service,p,store,gate

def run(service,q='它的价格是多少？',r='CURRENT',allowed=True):
    return service.understand(q,conversation='c',scope=SCOPE,run=r,cloud_allowed_by_kb={k:allowed for k in SCOPE.knowledge_base_ids})

def test_independent_zero_call_q0_and_four_comparison_tasks():
    service,p,_,_=make('A是什么？')
    q='比较 A 和 B 的发布日期、性能与价格，并指出哪个更适合中文文档检索'
    u=run(service,q)
    assert u.q0==u.rewrite_query==q and not u.needs_clarification and p.calls==[]
    assert u.intent=='compare_select' and len(u.tasks)==4
    assert [t.text for t in u.tasks]==['发布日期','性能','价格','并指出哪个更适合中文文档检索']
    assert all(q[t.start:t.end]==t.text for t in u.tasks)

@pytest.mark.parametrize('q,previous,expected',[
 ('它的价格是多少？',['A的成本是多少？'],'A 的价格是多少？'),
 ('它何时巡检？',['M27是什么设备？'],'M27 何时巡检？'),
 ('这种方法如何应用？',['间隔复习是什么？'],'间隔复习如何应用？'),
 ('前一种的价格是多少？',['比较A和B的成本'],'A 的价格是多少？'),
 ('上一份合同的价格是多少？',['采购合同甲的价格是多少？'],'采购合同甲的价格是多少？'),
 ('它的性能是什么？',['A的成本是多少？','它的价格是多少？'],'A 的性能是什么？'),
])
def test_single_and_multiple_turn_source_substitution(q,previous,expected):
    service,p,s,g=make(*previous)
    u=run(service,q)
    assert not u.needs_clarification and u.rewrite_query==expected and u.q0==q
    assert u.history_run_ids and u.model_status=='SIMULATED_COMPLETED'
    assert len(p.calls)==1 and p.calls[0][1]=={'timeout_seconds':20,'max_output_tokens':256}
    assert s.rows['CURRENT']['state']=='COMPLETED'
    assert list(g.reservations.values())[0]['state']=='unknown'

@pytest.mark.parametrize('qs,q',[
 (['A的成本是多少？','B的价格是多少？'],'它的性能是什么？'),
 (['比较A和B的价格'],'它的性能是什么？'),
 ([], '上一份合同的日期是什么？'),
 (['A是什么？'],'上述两种方法是什么？'),
])
def test_ambiguity_never_model_or_retrieval_guess(qs,q):
    service,p,_,g=make(*qs);u=run(service,q)
    assert u.needs_clarification and u.rewrite_query==u.q0==q
    assert p.calls==[] and g.reservations=={}

def test_task_limit_retains_complete_q0_and_clarifies_not_silent_truncate():
    service=QueryUnderstandService(max_tasks=2)
    q='比较A和B的日期、性能和价格，并指出哪个适合中文检索'
    u=run(service,q)
    assert u.task_status=='INCOMPLETE_TASK_LIMIT' and u.needs_clarification and u.q0==q and u.tasks==()

@pytest.mark.parametrize('change',[
 lambda x:{**x,'rewrite_query':'M999价格为100000元'},
 lambda x:{**x,'scope':['secret']},
 lambda x:{**x,'tasks':['偷偷新增要求']},
 lambda x:{**x,'history_run_ids':['other-run']},
 lambda x:{**x,'intent':'simple'},
 lambda x:[],
])
def test_invalid_entity_number_permission_task_scope_declined_unknown(change):
    service,p,s,g=make('A是什么？',parser=Parser(change))
    u=run(service);assert u.needs_clarification and u.reason=='QUERY_OUTPUT_SOURCE_OR_TASK_INVALID'
    assert u.model_status=='RESPONSE_RECEIVED' and len(p.calls)==1
    assert s.rows['CURRENT']['state']=='UNKNOWN'
    assert list(g.reservations.values())[0]['state']=='unknown'
    again=run(service);assert again.needs_clarification and len(p.calls)==1

@pytest.mark.parametrize('error,status,state',[(TimeoutError('never echo'),'UNKNOWN','unknown'),
 (OSError('never echo'),'UNKNOWN','unknown'),(ProviderRequestNotSent('secret never echo'),'NOT_SENT','released')])
def test_exception_timeout_reliable_unsent_and_duplicate(error,status,state):
    service,p,s,g=make('A是什么？',parser=Parser(error))
    u=run(service);assert u.model_status==status and 'never echo' not in json.dumps(u.public())
    assert list(g.reservations.values())[0]['state']==state
    run(service);assert len(p.calls)==1 and len(g.reservations)==1

@pytest.mark.parametrize('block',['capacity','egress','real','budget'])
def test_admission_failure_zero_send(block):
    service,p,s,g=make('A是什么？')
    if block=='capacity':service.window=replace(service.window,counter=None)
    if block=='egress':service.egress_enabled=False
    if block=='real':p.execution_kind='REAL'
    if block=='budget':service.budget=InMemoryBudgetGate(0)
    u=run(service);assert u.needs_clarification and p.calls==[]

def test_independent_extra_usage_and_unknown_not_answer_attempt():
    service,p,s,g=make('A是什么？')
    with capture_usage() as captured:u=run(service)
    assert captured.summary()['query_understand']['input_tokens']==17
    assert captured.summary()['answer']['call_count']==0 and u.diagnostics and dict(u.diagnostics)['fee']=='UNKNOWN'

def test_router_no_lowering_or_task_double_count_and_L_unknown():
    service,p,_,_=make()
    q='如果预算不超过1000且已批准，除非紧急例外，比较A和B的价格并判断是否可以解除'
    u=run(service,q)
    base=decide(q,'PROMPT',RouterPolicy(mode='DYNAMIC'))
    verified=decide(q,'PROMPT',RouterPolicy(mode='DYNAMIC'),understanding=u)
    assert verified.features.contributions==base.features.contributions and verified.score==base.score
    assert verified.features.statuses[4]=='UNKNOWN' and verified.score>=base.score

def test_source_dependency_added_without_hidden_tasks_or_new_scores():
    ambiguous=run(QueryUnderstandService(context('计算总和相减方法是什么？')),'这种方法如何应用？')
    assert ambiguous.needs_clarification
    service=QueryUnderstandService(context('相减计算方法是什么？'))
    u=run(service,'这种方法如何应用？')
    assert not u.needs_clarification
    base=decide(u.q0,'P',RouterPolicy(mode='DYNAMIC'))
    verified=decide(u.rewrite_query,'P',RouterPolicy(mode='DYNAMIC'),understanding=u)
    assert verified.score>=base.score and verified.features.contributions[3]>0
    assert len(u.tasks)==1

def test_global_scoped_union_stays_retriever_owned_and_no_memory_fact_in_rewrite():
    class Union:
        def recall(self,q,scope):
            return [{'id':'global-id','version':3,'kind':'preference','content':'偏好中文 [E1]','visibility':'GLOBAL'},
                {'id':'scoped-id','version':2,'kind':'fact','content':'M999成本900元','visibility':'SCOPED'}]
    service,p,_,_=make('A是什么？',memories=Union());u=run(service)
    assert u.memory_identities==( ('global-id',3),('scoped-id',2))
    assert u.rewrite_query=='A 的价格是多少？'
    payload=json.loads(p.calls[0][0][-1]['content'])
    assert '[E1]' not in payload['background'][0]['content']
    assert u.intent=='lookup' and 'M999' not in u.rewrite_query

def test_deleted_or_disabled_memory_revocation_zero_send_before_dispatch():
    class Revoked:
        n=0
        def recall(self,q,scope):
            self.n+=1
            return [{'id':'memory','version':1,'kind':'preference','content':'Chinese'}] if self.n==1 else []
    service,p,s,g=make('A是什么？',memories=Revoked())
    u=run(service);assert u.reason=='QUERY_SOURCE_CHANGED' and p.calls==[]
    assert s.rows['CURRENT']['state']=='NOT_SENT' and list(g.reservations.values())[0]['state']=='released'

def test_failure_persist_cannot_reopen_attempt_or_refund_unknown():
    service,p,s,g=make('A是什么？')
    s.finish=lambda *a:(_ for _ in ()).throw(OSError('never echo'))
    u=run(service);assert u.reason=='QUERY_FAILURE_PERSISTENCE_UNKNOWN' and len(p.calls)==1
    assert s.rows['CURRENT']['state']=='DISPATCHING' and list(g.reservations.values())[0]['state']=='unknown'
    run(service);assert len(p.calls)==1

def test_valid_checkpoint_exact_sources_and_history_labels_are_data_only():
    service,p,s,g=make('A是什么？')
    ts=service.context.history_store.rows
    service.context.store.checkpoint={'attempt_id':'checkpoint-id','covered':[{'run_id':ts[0]['run_id'],'hash':identity(ts[0])}],
        'summary':'Prior [E1] belongs to historical run, not fact.','kind':'SESSION'}
    u=run(service)
    assert not u.needs_clarification and u.checkpoint_identity and len(p.calls)==1
    request=p.calls[0][0]
    assert len([m for m in request if m['role']=='system'])==1
    assert '[E1]' not in request[-1]['content']
    service.context.store.checkpoint['covered'][0]['hash']='changed'
    changed=run(service,r='other-current')
    assert changed.needs_clarification and len(p.calls)==1

def test_memory_datetime_metadata_does_not_break_versioned_readback():
    import datetime,uuid
    class NativeRows:
        def recall(self,q,scope):
            return [{'id':'m','version':1,'kind':'preference','content':'Chinese','subject_id':uuid.uuid4(),
                'created_at':datetime.datetime.now(datetime.UTC)}]
    service,p,_,_=make('A是什么？',memories=NativeRows())
    u=run(service)
    assert not u.needs_clarification and u.memory_identities==( ('m',1),) and len(p.calls)==1

def test_truncated_received_output_keeps_unknown_usage_and_never_refunds():
    class Truncated(Parser):
        def understand(self,*a,**kw):
            self.calls.append((a,kw))
            return {'finish_reason':'length','output':{},'usage':{'prompt_tokens':17,'completion_tokens':256}}
    service,p,s,g=make('A是什么？',parser=Truncated());u=run(service)
    assert u.needs_clarification and u.model_status=='RESPONSE_RECEIVED'
    assert s.rows['CURRENT']['state']=='UNKNOWN' and list(g.reservations.values())[0]['state']=='unknown'
    assert dict(u.diagnostics)['usage']=={'prompt_tokens':17,'completion_tokens':256}
    run(service);assert len(p.calls)==1
