"""Full current Pipeline + Mock final model; zero real Provider or DB traffic."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import pytest

from backend.weknora_port.generation import GenerationRequest,Evidence,RouterPolicy
from backend.weknora_port.models.types import ChatResponse,RerankResponse,RerankResult
from backend.weknora_port.retrieval.pipeline import PipelineState,RetrievalPipeline
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.types import context_results
from backend.weknora_port.retrieval.strategy_executor import record_delivered_coverage
from backend.weknora_port.retrieval.bridge_strategy import BridgeStatus
from test_bridge_rescue import Repository,Models,row,extraction,QUOTE,QUESTION,CTX,USAGE
from test_generation import setup,event


class Pool(Repository):
    def __init__(self,first,new,related=()):
        super().__init__([first,*related,*new]);self.first=first;self.related=related;self.new=new
        self.hybrid_calls=[];self.max_inflight=0;self.inflight=0
    async def hybrid_search(self,targets,params,context,query_embedding=None):
        self.hybrid_calls.append((params.query_text,context.request_id,targets,query_embedding,params.serial))
        self.inflight+=1;self.max_inflight=max(self.max_inflight,self.inflight)
        try:
            await asyncio.sleep(0)
            return deepcopy([self.first,*self.related] if params.query_text==QUESTION else self.new)
        finally:self.inflight-=1
    async def get_chunks(self,ids,context):return {i:deepcopy(self.rows[i]) for i in ids if i in self.rows}
    async def get_chunk_children(self,ids,context):return []
    async def get_faq_answers(self,ids,context):return {}
    async def get_source_unit(self,anchor,context):return [deepcopy(self.rows[anchor.id])]

class Dual(Models):
    def __init__(self,first):
        super().__init__(json.dumps(extraction(first)));self.qu_calls=[];self.round_ranks=[]
    async def _chat(self,messages,options,context):
        if context.request_id.endswith('/query_rewrite'):
            self.qu_calls.append((messages,options,context))
            return ChatResponse(json.dumps({'rewrite_query':QUESTION,'intent':'kb_search','image_description':'','query_type':'DEPENDENT'}),finish_reason='stop',usage=USAGE)
        return await super()._chat(messages,options,context)
    async def _rerank(self,query,passages,context):
        if '/strategy/bridge-rerank' in context.request_id:
            return await super()._rerank(query,passages,context)
        self.round_ranks.append((query,context.request_id))
        return RerankResponse(tuple(RerankResult(i,.95 if i==0 else .01) for i in range(len(passages))),usage=USAGE)

def scenario(*,related=False,new=True,topk=30):
    first=row('first-hop-current',QUOTE,score=.82)
    target=row('new-place-evidence','Arbor Poetry Seminar is located in Harbor City, Canada.',document='place',score=.21)
    clue=row('related-institution-current','Arbor Poetry is located in Harbor City, Canada.',document='institution')
    repository=Pool(first,[target] if new else [],[clue] if related else [])
    models=Dual(first)
    state=PipelineState(query=QUESTION,targets=(SearchTarget('kb',embedding_model_key='embedding'),),
        chat_model_id='cheap',rerank_model_id='bge',bridge_cheap_model_id='cheap',
        source_evidence_mode='bounded_source_v1',retrieval_strategy_mode='query_structure_v1',
        bridge_rescue_mode='off',enable_rewrite=True,enable_expansion=True,rerank_top_k=topk,
        context_template='{{contexts}}\n{{query}}')
    return state,repository,models


@pytest.mark.parametrize('related',[False,True])
def test_actual_dependent_pipeline_new_scoped_search_and_final_model_delivery(related):
    async def run():
        state,repo,models=scenario(related=related);embeddings=[]
        async def embed(key,query,context):
            embeddings.append((query,context.request_id,context.scope));return [1.]
        final=await RetrievalPipeline(repo,models,embedding_resolver=embed).run(state,CTX)
        q1='Where is Arbor Poetry Seminar located?'
        assert [q for q,_,_,_,_ in repo.hybrid_calls]==[QUESTION,q1]
        assert [q for q,_,_ in embeddings]==[q for q,_ in models.round_ranks]==[QUESTION,q1]
        assert repo.max_inflight==1 and all(serial for *_,serial in repo.hybrid_calls)
        assert len(models.qu_calls)==len(models.chat_calls)==1
        assert len(models.rerank_calls)==int(related)
        assert models.round_ranks[1][1]=='run/strategy/round-1/rerank'
        assert all(tuple(targets)==state.targets for _,_,targets,_,_ in repo.hybrid_calls)
        assert all(scope==CTX.scope for _,_,scope in embeddings)
        assert state.query==state.rewrite_query==QUESTION and state.bridge_rescue_mode=='off'
        assert state.strategy_units_used==2 and len(state.strategy_rounds)==2
        assert {r.id for r in state.search_results}=={'first-hop-current',*(['related-institution-current'] if related else [])}
        assert 'new-place-evidence' in final.result_ids
        before=state._strategy_bridge_assessment
        assert before.status==BridgeStatus.COMPLETE and before.context_admitted
        assert not before.delivered and not before.final_answer_supported
        coordinator,cheap,expensive,*_=setup([event('The answer is grounded in the supplied originals.',done=True,finish='stop',final=True)])
        def observe(prepared):record_delivered_coverage(state,prepared)
        req=GenerationRequest('run','session','assistant',QUESTION,final,
            evidence=tuple(Evidence.from_result(r) for r in context_results(state)),
            citations_enabled=True,policy=RouterPolicy(mode='CHEAP_ONLY'),on_context_prepared=observe)
        await coordinator.run(req,CTX)
        assert len(cheap.calls)==1 and not expensive.calls
        wire='\n'.join(m.content for m in cheap.calls[0][0])
        assert QUOTE in wire and repo.new[0].content in wire and QUESTION in wire
        after=state._strategy_bridge_assessment
        assert after.delivered and after.final_answer_supported
        assert any(t.get('decision')=='ACTUAL_MESSAGES_DELIVERED' for t in state.strategy_trace)
    asyncio.run(run())


def test_related_only_empty_new_recall_keeps_evidence_without_claiming_complete():
    async def run():
        state,repo,models=scenario(related=True,new=False)
        async def embed(*args):return [1.]
        final=await RetrievalPipeline(repo,models,embedding_resolver=embed).run(state,CTX)
        result=state._strategy_bridge_assessment
        assert result.status==BridgeStatus.RELATED_ONLY and result.new_recall
        assert not result.final_answer_supported and not result.delivered
        assert set(final.result_ids)=={'first-hop-current','related-institution-current'}
        assert len(repo.hybrid_calls)==2 and len(models.chat_calls)==1
        assert 'seminar_venue="unverified"' in final.rendered_contexts
    asyncio.run(run())


def test_global_topk_omits_new_relation_preserving_original_firsthop():
    async def run():
        state,repo,models=scenario(topk=1)
        async def embed(*args):return [1.]
        final=await RetrievalPipeline(repo,models,embedding_resolver=embed).run(state,CTX)
        assert final.result_ids==('first-hop-current',)
        assessment=state._strategy_bridge_assessment
        assert assessment.budget_omitted and not assessment.delivered and not assessment.final_answer_supported
        assert state.merge_results[0].content==QUOTE and not state.rescue_evidence
    asyncio.run(run())


def test_verified_q1_duplicate_of_original_query_stops_before_new_hybrid():
    async def run():
        first=row('duplicate-first-hop','Alice Bennett directed the Alice Bennett Seminar from 2011–2014.',score=.82)
        q0='Where is Alice Bennett Seminar located?'
        class DuplicatePool(Pool):
            async def hybrid_search(self,targets,params,context,query_embedding=None):
                self.hybrid_calls.append((params.query_text,context.request_id,targets,query_embedding,params.serial))
                return [deepcopy(first)]
        class DuplicateModels(Models):
            async def _chat(self,messages,options,context):
                if context.request_id.endswith('/query_rewrite'):
                    return ChatResponse(json.dumps({'rewrite_query':q0,'intent':'kb_search','image_description':'','query_type':'DEPENDENT'}),finish_reason='stop',usage=USAGE)
                return await super()._chat(messages,options,context)
        model=DuplicateModels(json.dumps(extraction(first,sentence_text=first.content,
            object='Alice Bennett Seminar',bridge_search_phrases=['Alice Bennett Seminar'])))
        repo=DuplicatePool(first,[])
        state=PipelineState(query=q0,targets=(SearchTarget('kb'),),chat_model_id='cheap',
            rerank_model_id='bge',bridge_cheap_model_id='cheap',enable_expansion=True,
            source_evidence_mode='bounded_source_v1',retrieval_strategy_mode='query_structure_v1',
            context_template='{{contexts}}\n{{query}}')
        finalized=await RetrievalPipeline(repo,model).run(state,CTX)
        assert len(repo.hybrid_calls)==1 and state.strategy_units_used==1
        assessment=state._strategy_bridge_assessment
        assert assessment.q1==q0 and assessment.budget_omitted and assessment.reason=='DUPLICATE_QUERY'
        assert finalized.result_ids==(first.id,) and not assessment.final_answer_supported
        assert len(model.chat_calls)==1
    asyncio.run(run())


def test_existing_zero_topk_means_no_additional_global_cap():
    async def run():
        state,repo,models=scenario(topk=0)
        async def embed(*args):return [1.]
        final=await RetrievalPipeline(repo,models,embedding_resolver=embed).run(state,CTX)
        assert set(final.result_ids)=={'first-hop-current','new-place-evidence'}
        assert state.rerank_top_k==0 and state._strategy_bridge_assessment.context_admitted
    asyncio.run(run())
