"""Actual C3 T5 ambiguity regression; entirely offline ordinary-chain checks."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.models.types import RerankResult
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.query import understand_query, validate_rewrite, resolve_intent
from backend.weknora_port.service import TurnConfiguration
from test_pipeline import CTX, Model, Registry, Repo, row, state
from test_service import REQ, runtime
from dataclasses import replace

Q0 = 'What is it used for?'
PRIOR = 'Compare a Leyden jar and Franklin bells.'
C3_T5_OUTPUT = '{"rewrite_query":"What is it used for?", "intent":"kb_search", "image_description":""}'
C3_UNSUPPORTED_ANSWER = ('The question "What is it used for?" refers to the "Franklin bells" mentioned in the previous answer. '
    'Franklin bells are used as a qualitative indicator of electric charge. '
    'The detailed comparison and historical context were derived from general knowledge.')


def previous(query,answer='Prior answer for dialogue only'):
    return SimpleNamespace(query=query,answer=answer,knowledge_base_scope=('kb',),document_scope=())


def test_actual_C3_T5_unchanged_rewrite_preserves_program_ambiguity_reason():
    s = state(query=Q0,enable_rewrite=True,chat_model_id='cheap',history=[previous(PRIOR)])
    assert validate_rewrite(s,Q0) == 'AMBIGUOUS_REFERENCE'
    asyncio.run(understand_query(s,CTX,Registry(Model([C3_T5_OUTPUT]))))
    assert s.query == s.rewrite_query == Q0
    assert s.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'
    assert s.query_trace[-1]['reason'] == 'AMBIGUOUS_REFERENCE'
    assert resolve_intent(s) == 'kb_search'


@pytest.mark.parametrize('accepted_score',[0.0,0.9])
def test_actual_ordinary_path_searches_reranks_then_fixed_clarification_without_factual_model(accepted_score):
    async def run():
        service,repo,_,co,cheap,expensive,*_ = runtime()
        rows = [row('source-'+str(i),'Original candidate number '+str(i)+' about unresolved objects.') for i in range(11)]
        retrieval = Repo(rows,chunks={r.id:r for r in rows})
        calls = {'rewrite':0,'rerank':[],'catalog':0}
        class Models:
            async def chat(self,messages,options,context):
                calls['rewrite'] += 1
                return SimpleNamespace(content=C3_T5_OUTPUT,finish_reason='stop')
            async def rerank(self,query,documents,context):
                calls['rerank'].append((query,list(documents)))
                return SimpleNamespace(results=[RerankResult(i,accepted_score) for i in range(len(documents))])
        async def history(*args,**kwargs): return (previous(PRIOR),)
        service.history_service = SimpleNamespace(load=history)
        s = state(enable_rewrite=True,chat_model_id='cheap',rerank_model_id='rerank',language='English')
        async def catalog(): calls['catalog'] += 1; return 'Document catalog titles only'
        service.configuration = lambda turn: TurnConfiguration(s,{'policy':RouterPolicy(mode='CHEAP_ONLY')},
            fallback={'strategy':'model','response':'Insufficient evidence','prompt':'Answer from your general knowledge {{kb_documents}}',
                      'documents':catalog,'options':None})
        async def pipeline(turn,progress): return RetrievalPipeline(retrieval,Registry(Models()),on_progress=progress)
        service.pipeline_factory = pipeline
        accepted = await repo.accept(replace(REQ,content=Q0),'owner')
        prepared = await service.prepare_turn(accepted)
        assert prepared.request.fixed_fallback and prepared.request.fixed_fallback != 'Insufficient evidence'
        assert prepared.request.evidence == () and prepared.request.history == () and prepared.request.memory_prompt == ''
        assert prepared.request.finalized.result_ids == () and prepared.request.finalized.rendered_contexts == ''
        assert len(retrieval.calls)==1 and retrieval.calls[0][1].query_text == Q0
        assert calls['rewrite']==1 and len(calls['rerank'])==1 and calls['rerank'][0][0] == Q0
        assert len(calls['rerank'][0][1])==11 and calls['catalog']==0
        assert s.needs_retrieval is True and s.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'
        result = await co.run(prepared.request,prepared.context)
        assert result.references == () and not cheap.calls and not expensive.calls
        assert 'Franklin' not in result.answer and 'general knowledge' not in result.answer
        assert 'specify' in result.answer.lower() or 'which' in result.answer.lower()
    asyncio.run(run())


def test_format_of_known_ambiguous_old_answer_cannot_promote_its_unsupported_facts():
    async def run():
        service,repo,retrieval,*_ = runtime()
        async def history(*args,**kwargs): return (previous(PRIOR),previous(Q0,C3_UNSUPPORTED_ANSWER))
        service.history_service = SimpleNamespace(load=history)
        s = state(enable_rewrite=False,language='English')
        service.configuration = lambda turn: TurnConfiguration(s)
        accepted = await repo.accept(replace(REQ,content='Format the previous answer as a table.'),'owner')
        prepared = await service.prepare_turn(accepted)
        assert prepared.request.fixed_fallback and prepared.request.evidence == () and prepared.request.history == ()
        assert 'Franklin' not in prepared.request.fixed_fallback
        assert not retrieval.calls and s.needs_retrieval is False
    asyncio.run(run())


@pytest.mark.parametrize('intent',['follow_up','greeting','web_search','chitchat'])
def test_known_multiple_antecedents_cannot_authorize_no_search(intent):
    s = state(query=Q0,intent=intent,history=[previous(PRIOR)])
    assert resolve_intent(s) == 'kb_search'


@pytest.mark.parametrize('output',[None,'not json',RuntimeError('optional unavailable')])
def test_optional_rewrite_failure_does_not_erase_known_ambiguity(output):
    s = state(query=Q0,enable_rewrite=True,chat_model_id='cheap',history=[previous(PRIOR)])
    asyncio.run(understand_query(s,CTX,Registry(Model([output]))))
    assert s.query == s.rewrite_query == Q0 and s.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'


def test_new_independent_topic_and_valid_format_remain_available():
    s = state(query=Q0,history=[previous(PRIOR),previous('What is a Leyden jar?')])
    assert validate_rewrite(s,'What is a Leyden jar used for?') == ''
    s = state(query='Format the previous answer as a table.',history=[previous('Who is Jeanne Marie Beaumont?')],intent='follow_up')
    assert resolve_intent(s) == 'follow_up'
