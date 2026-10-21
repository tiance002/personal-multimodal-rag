"""Actual retrieval orchestration, offline original rows and mocked adapters."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResult
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing, SearchError
from backend.weknora_port.retrieval.types import SearchResult

CTX=CallContext('root',TrustedScope('owner',('kb',)))
def raw(id,text,doc=None,kb='kb'):
    return SearchResult(id,text,.8,knowledge_id=doc or id,knowledge_base_id=kb,
        metadata={'source_version':'v'},chunk_metadata={'source_version':'v'},end_at=len(text))
class Repo:
    def __init__(self):self.calls=[];self.sources={};self.active=0;self.max_active=0
    async def hybrid_search(self,targets,params,context,query_embedding=None):
        self.calls.append((params.query_text,context.request_id,tuple(targets),query_embedding))
        self.active+=1;self.max_active=max(self.max_active,self.active)
        try:
            await asyncio.sleep(0)
            if 'Redis' in params.query_text and 'PostgreSQL' not in params.query_text:r=raw('redis','Redis concurrency uses a single command executor.')
            else:r=raw('postgres','PostgreSQL concurrency uses multiple backends.')
            r.knowledge_base_id=targets[0].knowledge_base_id
            if r.knowledge_base_id!='kb':r.id+='-'+r.knowledge_base_id;r.knowledge_id=r.id
            self.sources[r.id]=r
            return [deepcopy(r)]
        finally:self.active-=1
    async def get_chunks(self,ids,context):return {i:deepcopy(self.sources[i]) for i in ids if i in self.sources}
    async def get_chunk_children(self,ids,context):return []
    async def get_faq_answers(self,ids,context):return {}
    async def get_source_unit(self,anchor,context):return [deepcopy(self.sources[anchor.id])]
class Models:
    def __init__(self):self.qu=[];self.ranks=[]
    async def chat(self,messages,options,context):
        self.qu.append(context.request_id)
        return SimpleNamespace(content='{"rewrite_query":"Compare PostgreSQL and Redis in terms of concurrency.","intent":"kb_search","image_description":"","query_type":"SIMPLE_COMPARE"}',finish_reason='stop')
    async def rerank(self,query,documents,context):
        self.ranks.append((query,context.request_id))
        return SimpleNamespace(results=[RerankResult(i,.85) for i in range(len(documents))])
class Registry:
    def __init__(self,model):self.model=model
    async def chat(self,*args):return self.model
    async def rerank(self,*args):return self.model
def state(**kw):
    args=dict(query='Compare PostgreSQL and Redis in terms of concurrency.',
        targets=(SearchTarget('kb'),),source_evidence_mode='bounded_source_v1',
        enable_rewrite=True,enable_expansion=True,chat_model_id='cheap',rerank_model_id='rank',
        context_template='{{contexts}}\n{{query}}')
    args.update(kw);s=PipelineState(**args);s.retrieval_strategy_mode='query_structure_v1';return s

def test_retrieve_once_has_no_understanding_context_or_persistence():
    async def run():
        repo=Repo();models=Models();persist=[];s=state(enable_rewrite=False)
        s.rewrite_query='PostgreSQL concurrency';s._strategy_serial=True
        pipeline=RetrievalPipeline(repo,Registry(models),persist_sink=lambda *args:persist.append(args))
        result=await pipeline.retrieve_once(s,CTX.child('strategy/sq-1'))
        assert result.status=='OK' and result.state is s
        assert models.qu==[] and persist==[]
        assert not getattr(s,'_finalized_context',None)
        assert [r.id for r in s.merge_results]==['postgres']
        assert repo.calls[0][0]=='PostgreSQL concurrency'
        assert models.ranks==[('PostgreSQL concurrency','root/strategy/sq-1/rerank')]
    asyncio.run(run())

def test_actual_pipeline_one_qu_two_serial_rounds_and_original_final_question():
    async def run():
        repo=Repo();models=Models();persist=[];s=state(user_message_id='user')
        original=s.query;history=s.history;targets=s.targets
        result=await RetrievalPipeline(repo,Registry(models),persist_sink=lambda *args:persist.append(args)).run(s,CTX)
        assert len(models.qu)==1 and len(models.ranks)==2 and len(repo.calls)==2
        assert all('strategy/sq-' in key for _,key in models.ranks)
        assert all(q!=original for q,_ in models.ranks)
        assert repo.max_active==1 and s.enable_expansion is True
        assert s.query==original and s.rewrite_query==original and s.history == history and s.targets is targets
        assert set(result.result_ids)=={'postgres','redis'} and original in result.user_content
        assert len(persist)==1
    asyncio.run(run())

def test_candidate_bad_source_config_rejected_before_qu_or_search():
    async def run():
        repo=Repo();models=Models();s=state(source_evidence_mode='off')
        with pytest.raises(ValueError,match='RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE'):
            await RetrievalPipeline(repo,Registry(models)).run(s,CTX)
        assert not models.qu and not repo.calls
    asyncio.run(run())

def test_candidate_shared_connection_groups_are_serial_and_embedding_permission_propagates():
    async def run():
        repo=Repo();models=Models();s=state(enable_rewrite=False)
        s.rewrite_query='PostgreSQL concurrency';s._strategy_serial=True
        s.targets=(SearchTarget('kb',embedding_model_key='one'),SearchTarget('kb2',embedding_model_key='two'))
        context=replace(CTX,scope=TrustedScope('owner',('kb','kb2')))
        async def embedding(*args):return [1.]
        await RetrievalPipeline(repo,Registry(models),embedding_resolver=embedding).retrieve_once(s,context)
        assert repo.max_active==1 and len(repo.calls)==2
        async def denied(*args):raise PermissionError('DENIED')
        repo=Repo();s=state(enable_rewrite=False);s.rewrite_query='PostgreSQL';s._strategy_serial=True
        s.targets=(SearchTarget('kb',embedding_model_key='one'),)
        with pytest.raises(PermissionError):
            await RetrievalPipeline(repo,Registry(models),embedding_resolver=denied).retrieve_once(s,CTX)
        assert not repo.calls
    asyncio.run(run())

def test_candidate_infrastructure_failure_is_not_empty_branch():
    async def run():
        class Broken(Repo):
            async def hybrid_search(self,*args,**kw):raise RuntimeError('private infra detail')
        models=Models();s=state(enable_rewrite=False);s.rewrite_query='PostgreSQL';s._strategy_serial=True
        result=await RetrievalPipeline(Broken(),Registry(models)).retrieve_once(s,CTX)
        assert result.status=='ERROR' and result.reason=='SEARCH_FAILED'
        assert 'private infra detail' not in repr(result)
    asyncio.run(run())


@pytest.mark.parametrize('failure',[PermissionError('DENIED'),
    __import__('backend.weknora_port.models.types',fromlist=['ProviderError']).ProviderError('UNCERTAIN_RESULT',sent=True),
    __import__('backend.weknora_port.models.types',fromlist=['ProviderError']).ProviderError('LOCAL_EXECUTION_UNKNOWN_STOP')])
def test_candidate_threshold_retry_never_swallows_denial_or_uncertain_send(failure):
    async def run():
        class Retry(Models):
            async def rerank(self,query,documents,context):
                self.ranks.append((query,context.request_id))
                if len(self.ranks)==2:raise failure
                return SimpleNamespace(results=[RerankResult(0,.01)])
        models=Retry();s=state(enable_rewrite=False,rerank_threshold=.8)
        s.rewrite_query='PostgreSQL';s._strategy_serial=True
        with pytest.raises(type(failure)):
            await RetrievalPipeline(Repo(),Registry(models)).retrieve_once(s,CTX)
        assert [key for _,key in models.ranks]==['root/rerank','root/rerank/threshold-2']
        assert not s.merge_results
    asyncio.run(run())

def test_candidate_entity_unknown_is_not_discarded_as_empty():
    async def run():
        from backend.weknora_port.models.types import ProviderError
        async def denied(*args):raise ProviderError('LOCAL_EXECUTION_UNKNOWN_STOP')
        s=state(enable_rewrite=False,entities=('subject',));s.rewrite_query='PostgreSQL';s._strategy_serial=True
        with pytest.raises(ProviderError,match='LOCAL_EXECUTION_UNKNOWN_STOP'):
            await RetrievalPipeline(Repo(),Registry(Models()),entity_search=denied).retrieve_once(s,CTX)
    asyncio.run(run())

def test_actual_final_generation_delivery_observer_and_embedding_queries():
    async def run():
        from backend.weknora_port.generation import GenerationRequest,Evidence,RouterPolicy
        from backend.weknora_port.retrieval.strategy_executor import record_delivered_coverage
        from test_generation import setup,event
        embeddings=[]
        async def embed(key,query,context):
            embeddings.append((query,context.request_id));return [1.]
        s=state(targets=(SearchTarget('kb',embedding_model_key='emb'),))
        repo=Repo();models=Models()
        finalized=await RetrievalPipeline(repo,Registry(models),embedding_resolver=embed).run(s,CTX)
        assert [q for q,_ in embeddings]==[q for q,_ in models.ranks]==[q for q,_,_,_ in repo.calls]
        assert all(e[3]==[1.] for e in repo.calls)
        assert all(entry['delivery']=='PENDING' for entry in s.strategy_coverage.values())
        prepared=[]
        def observed(value):
            prepared.append(value);record_delivered_coverage(s,value)
        req=GenerationRequest('root','session','assistant',s.query,finalized,
            evidence=tuple(Evidence.from_result(r) for r in s.merge_results),
            citations_enabled=False,policy=RouterPolicy(mode='CHEAP_ONLY'),on_context_prepared=observed)
        coordinator,cheap,expensive,*_=setup([event('Comparison based on both originals.',done=True,finish='stop',final=True)])
        result=await coordinator.run(req,CTX)
        assert len(prepared)==1 and len(cheap.calls)==1 and not expensive.calls
        assert cheap.calls[0][0]==prepared[0].messages
        assert s.query in cheap.calls[0][0][-1].content
        assert set(r.id for r in s.merge_results)=={'postgres','redis'}
        assert all(entry['delivery']=='DELIVERED' and entry['semantic_support']=='NOT_CHECKED' for entry in s.strategy_coverage.values())
    asyncio.run(run())

def test_final_context_budget_cannot_leave_free_generation_with_no_sources():
    async def run():
        class LargeHeader(Repo):
            async def hybrid_search(self,*args,**kwargs):
                rows=await super().hybrid_search(*args,**kwargs)
                for r in rows:r.knowledge_title='Large source header '*1200;self.sources[r.id]=deepcopy(r)
                return rows
        s=state(enable_rewrite=False);models=Models()
        with pytest.raises(SearchNothing):
            await RetrievalPipeline(LargeHeader(),Registry(models)).run(s,CTX)
        assert not s.merge_results
        assert all(e['delivery']=='BUDGET_OMITTED' for e in s.strategy_coverage.values())
    asyncio.run(run())
