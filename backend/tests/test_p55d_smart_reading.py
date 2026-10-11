"""SIMULATED model driven through the real LangChain read tool/ContextManager."""
import json
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage,ToolMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.context_window import ContextManager
from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.retrieval import HybridRetriever,InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.app.domain.agent_policy import AgentLimits
from backend.tests.test_p55b_context_window import window
from types import SimpleNamespace

class ReadingModel(BaseChatModel):
    calls:int=0
    @property
    def _llm_type(self):return 'SIMULATED-P55D'
    def bind_tools(self,tools,**kwargs):return self
    def _generate(self,messages,**kwargs):
        self.calls+=1
        pages=[json.loads(m.content) for m in messages if isinstance(m,ToolMessage) and m.name=='read_document']
        if not pages or pages[-1]['has_more']:
            name='read_document';args=dict(document_id='doc',offset=pages[-1]['next_offset'] if pages else 0,length=3000)
        elif not any(isinstance(m,ToolMessage) and m.name=='search_knowledge' for m in messages):
            name='search_knowledge';args=dict(query='尾段事实')
        else:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='尾段事实为只读 [E1]'))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[dict(name=name,args=args,id=f'call-{self.calls}',type='tool_call')]))])

def test_real_langchain_paging_and_final_child_citation_context_checkpoint_memory():
    scope=Scope.from_ids(['kb']);body=('开头资料。\n'+'中段资料。\n'*1000+'尾段事实为只读。')
    repo=InMemoryRetrievalRepository();repo.add(ChunkRecord('tail','kb','doc','v','尾段事实为只读。',{'start':len(body)-len('尾段事实为只读。'),'end':len(body)}))
    evidence=EvidenceAccumulator();g=KnowledgeToolGateway(retriever=HybridRetriever(repo),content_reader=lambda d:body,
        document_resolver=lambda d:dict(knowledge_base_id='kb',active_version_id='v',index_status='ready'),evidence_accumulator=evidence,
        max_read_tokens=20000) # SIMULATED JSON units, not an actual model capacity.
    # SIMULATED protocol counter and large fixture window, never a real capacity.
    w=window(size=100000)
    history=SimpleNamespace(completed_history_context=lambda *a,**k:dict(turns=[]))
    manager=ContextManager(history,None,{'local_chat':w})
    trace=[];store=SimpleNamespace(create_run=lambda *a:None,is_cancelled=lambda r:False,
        append_step=lambda r,step:trace.append(step),complete_run=lambda *a,**k:None)
    model=ReadingModel();result=LangChainAgentAdapter(model,context_manager=manager).run('conv','尾段事实',scope,
        run_id='SIMULATED-P55D-READ',gateway=g,evidence=evidence,trace_store=store,limits=AgentLimits(max_steps=8,max_tokens=3000,max_seconds=60))
    assert result.status=='completed' and result.citations==('E1',)
    assert result.evidence[0].chunk_id=='tail' and result.evidence[0].quote=='尾段事实为只读。'
    assert len([s for s in trace if s.tool_name=='read_document'])==3
    assert g._read_chars==len(body) and g._read_tokens>0 and len(evidence.chunks)==1
    assert model.calls==5
