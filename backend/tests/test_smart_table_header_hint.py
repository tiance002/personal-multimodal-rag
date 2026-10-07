"""SIMULATED actual Smart entry: scripted local model, fake DB/store, native source replay."""
import copy,json,os
from pathlib import Path
from dataclasses import replace
import pytest
from backend.app.application.answer_service import AnswerService
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.domain.agent_policy import AgentLimits
from backend.app.domain.scope import Scope
from backend.app.domain.models import ChunkRecord
from backend.tests.test_original_header_boundary import original,QUESTION
from backend.tests.test_table_header_hint import ServiceRetrieval,assert_hint
from backend.tests.test_clarification_persistence import LinkedStore
from backend.tests.test_langchain_agent import ScriptedChatModel,TraceStore
from backend.tests.test_agent_limits import LimitChatModel

class SmartStore(LinkedStore):
    def finalize_answer(self,**kwargs):
        self.agent_terminal=kwargs['agent_terminal']
        return super().finalize_answer(**kwargs)

def ask(chunks,question=QUESTION,cancel=False,answer='资料不足。'):
    store=SmartStore(cancel_commit=cancel);model=ScriptedChatModel(final_answer=answer);trace=TraceStore()
    service=AnswerService(knowledge_gateway=KnowledgeGateway(ServiceRetrieval(chunks)),runs=store,
                          smart_agent=LangChainAgentAdapter(model),agent_trace_store=trace)
    outcome=service.answer({'id':'conv','knowledge_base_scope':['offline-kb'],'document_scope':['offline-document']},question,mode='smart')
    return outcome,store,model,trace

def test_smart_actual_entry_explains_original_table_refusal_and_restores_history(original):
    format,_,chunks=original;outcome,store,model,trace=ask(chunks)
    assert_hint(outcome,format)
    assert outcome.trace['mode']=='smart' and outcome.trace['model_calls']==2 and model.calls==2
    assert outcome.trace['reason_codes']==['INSUFFICIENT_EVIDENCE','TABLE_HEADER_UNCONFIRMED']
    assert len(outcome.trace['steps'])==1 and outcome.trace['steps'][0]['tool_name']=='search_knowledge'
    assert store.completed==[(outcome.run_id,'failed','INSUFFICIENT_EVIDENCE')]
    assert store.agent_terminal==('failed','INSUFFICIENT_EVIDENCE',0)
    assert trace.completed==[] # Atomic run finalizer receives the deferred Agent terminal.
    assert store.links==[('user',outcome.run_id)] and not store.evidence
    metadata=next(payload for _,event,payload in store.events if event=='run.metrics')
    assert metadata['presentation']==outcome.trace['evidence_hint'] and not metadata['citations']
    history=PostgresKnowledgeRepository._message_row({'role':'user','content':QUESTION,'run_id':outcome.run_id,'citations':[],'presentation':metadata['presentation']})
    assert history['presentation']==metadata['presentation']
    if os.getenv('RAG_OFFLINE_SMART_HINT_ARTIFACTS'):
        dest=Path(os.environ['RAG_OFFLINE_SMART_HINT_ARTIFACTS'])
        (dest/(format+'-smart-mock-payload.json')).write_text(json.dumps({'response':{'run_id':outcome.run_id,'answer':outcome.answer,'error_code':outcome.error_code,'citations':[],'trace':outcome.trace},'history':[history]},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

@pytest.mark.parametrize('question',['其他门店2026-09的营业额是多少？','杉桥门店2027-11的营业额是多少？','杉桥门店2026-09的成本是多少？'])
def test_smart_unrelated_insufficient_evidence_is_not_header_hint(original,question):
    outcome,store,model,_=ask(original[2],question)
    assert outcome.error_code=='INSUFFICIENT_EVIDENCE' and not outcome.answer and not outcome.citations
    assert not outcome.trace.get('evidence_hint') and model.calls==2
    assert all('presentation' not in p for _,_,p in store.events)

@pytest.mark.parametrize('mutation',['other-parser-error','cross-document','cross-version','inconsistent-cell','other-format'])
def test_smart_does_not_promote_invalid_source_relationships(original,mutation):
    altered=[]
    for chunk in original[2]:
        loc=copy.deepcopy(chunk.locator)
        is_header=loc.get('kind')=='table' and all(c.get('row')==1 for c in loc.get('cells',[]))
        if mutation=='other-parser-error':loc['parse_warnings']=['OCR_LANGUAGE_UNAVAILABLE']
        if mutation=='inconsistent-cell' and loc.get('cells'):loc['cells'][0]['coordinate']='R999C99'
        if mutation=='other-format':loc['source_format']='html'
        altered.append(replace(chunk,locator=loc,document_id='foreign' if mutation=='cross-document' and is_header else chunk.document_id,version_id='old' if mutation=='cross-version' and is_header else chunk.version_id))
    outcome,store,_,_=ask(altered)
    assert outcome.error_code=='INSUFFICIENT_EVIDENCE' and not outcome.trace.get('evidence_hint') and not outcome.answer
    assert all('presentation' not in p for _,_,p in store.events)

def test_smart_no_candidates_remains_no_candidates():
    outcome,store,model,_=ask([])
    assert outcome.error_code=='NO_CANDIDATES' and not outcome.answer and not outcome.trace.get('evidence_hint')
    assert model.calls==2 and all('presentation' not in p for _,_,p in store.events)

def test_smart_commit_cancel_does_not_publish_hint(original):
    outcome,store,_,trace=ask(original[2],cancel=True)
    assert outcome.error_code=='CANCELLED' and not outcome.answer and not store.completed
    assert all(role!='assistant' for role,_ in store.links)
    assert trace.completed==[] # Deferred trace is not finalized when the final commit loses cancellation.

@pytest.mark.parametrize('budget',['step','time','tokens','cost','cancel'])
def test_smart_original_table_never_bypasses_budget_or_cancel(original,budget):
    model=LimitChatModel(final_answer='这是一个较长的回答。',final_cost=25)
    limits={'step':AgentLimits(max_steps=0),'time':AgentLimits(max_seconds=0),
            'tokens':AgentLimits(max_tokens=1),'cost':AgentLimits(max_cost_microunits=1),'cancel':AgentLimits()}[budget]
    trace=TraceStore(cancelled=budget=='cancel');evidence=EvidenceAccumulator()
    result=LangChainAgentAdapter(model).run('conv',QUESTION,Scope.from_ids(['offline-kb'],['offline-document']),
        run_id='run',gateway=KnowledgeToolGateway(knowledge_gateway=KnowledgeGateway(ServiceRetrieval(original[2])),evidence_accumulator=evidence),evidence=evidence,limits=limits,trace_store=trace)
    expected={'step':'AGENT_STEP_LIMIT','time':'AGENT_TIME_LIMIT','tokens':'AGENT_TOKEN_LIMIT','cost':'AGENT_COST_LIMIT','cancel':'CANCELLED'}[budget]
    assert result.error_code==expected and not result.answer and not result.citations and not getattr(result,'evidence_hint',None)
    if budget in {'step','time','cancel'}:assert model.calls==0
    if budget=='cost':assert result.cost_microunits==25

@pytest.mark.parametrize('answer,error', [('杉桥门店2026-09营业额312千元 [E99]。','INVALID_CITATION'),('资料不足。','UNSUPPORTED_ANSWER'),('杉桥门店2026-09营业额312千元 [E1]。',None)])
def test_smart_prose_and_citation_semantics_remain_distinct(answer,error):
    chunk=ChunkRecord('plain','offline-kb','offline-document','version','杉桥门店2026-09营业额312千元。',{'kind':'text'})
    outcome,store,_,_=ask([chunk],answer=answer)
    assert outcome.error_code==error and not outcome.trace.get('evidence_hint')
    assert all('presentation' not in p for _,_,p in store.events)
