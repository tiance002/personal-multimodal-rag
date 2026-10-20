"""Bounded source fallback output; never a new provider attempt or refund."""
from dataclasses import replace
import pytest
from test_generation import setup,event,request,run,CTX
from backend.weknora_port.retrieval.context import FinalizedContext
from backend.weknora_port.models.types import ProviderError,ModelCallTimeout,CallCancelled,RunDeadlineExceeded
FIXED='Sorry, I am unable to answer this question.'

def fallback(mode='CHEAP_ONLY'):
    return request(mode,is_fallback=True,system_prompt='FALLBACK',finalized=FinalizedContext('q','',()),evidence=())

@pytest.mark.parametrize('kind,outputs,prefix',[
    ('startup',[ProviderError('PROVIDER_HTTP_ERROR',status_code=503,sent=True)],''),
    ('nil',[],''),('empty',[],''),('eof',[event('partial ')],'partial '),
])
@pytest.mark.parametrize('mode',['CHEAP_ONLY','DYNAMIC'])
def test_fallback_output_closes_with_fixed_without_additional_model(kind,outputs,prefix,mode):
    co,c,e,store,commit,life=setup(outputs)
    if kind=='nil':
        def unavailable(*args):c.calls.append(args);return None
        c.chat_stream=unavailable
    result=run(co.run(fallback(mode),CTX))
    assert result.answer==prefix+FIXED
    assert len(c.calls)==1 and not e.calls
    assert len(life.claims)==len(life.finished)==len(life.started)==1
    assert life.finished[0][0].status=='FAILED' and life.finished[0][0].cost_status=='UNKNOWN'
    assert result.attempts==tuple(row[0] for row in life.finished)
    assert ''.join(row.content for row in store.events if row.type=='answer')==result.answer
    assert sum(row.type=='answer' and row.done for row in store.events)==1
    assert commit.calls[0].answer==result.answer and store.trace[-2:]==['COMMIT','complete']

@pytest.mark.parametrize('failure',[
    CallCancelled(),RunDeadlineExceeded(),ModelCallTimeout(sent=True),
    ProviderError('MODEL_BUDGET_DENIED'),ProviderError('MODEL_ORIGIN_KB_EGRESS_DENIED'),
    ProviderError('GENERATION_EVIDENCE_SCOPE_MISMATCH'),ProviderError('PROVIDER_MODEL_IDENTITY_MISMATCH',sent=True),
    ProviderError('MODEL_USAGE_SETTLEMENT_FAILED',sent=True),
    ProviderError('PROVIDER_HTTP_ERROR',status_code=401,sent=True),
    ProviderError('PROVIDER_HTTP_ERROR',status_code=403,sent=True),
    ProviderError('PROVIDER_API_ERROR',sent=True),RuntimeError('untyped failure'),
])
def test_fallback_safety_and_unclassified_errors_still_raise(failure):
    co,c,e,store,commit,life=setup([failure])
    with pytest.raises(ProviderError if isinstance(failure,RuntimeError) else type(failure)):run(co.run(fallback(),CTX))
    assert len(c.calls)==1 and not e.calls and not commit.calls
    assert not any(row.content==FIXED for row in store.events)

def test_fallback_normal_done_keeps_answer():
    co,c,e,store,commit,life=setup([event('normal answer',done=True,finish='stop',final=True)])
    result=run(co.run(fallback(),CTX))
    assert result.answer=='normal answer' and len(c.calls)==1 and not e.calls
    assert life.finished[0][0].status=='COMPLETED'


def test_nonvision_fallback_image_context_uses_source_label():
    from test_service import runtime,REQ
    from test_pipeline import state
    from backend.weknora_port.service import TurnConfiguration
    from backend.weknora_port.models.types import ChatOptions
    from backend.weknora_port.retrieval.search import SearchNothing
    from backend.weknora_port.generation import prepare_generation
    async def scenario():
        config=TurnConfiguration(state(),fallback={'strategy':'model','prompt':'FALLBACK {{query}} {{language}} {{kb_documents}}',
            'response':'FIXED','documents':lambda:'- doc\n','options':ChatOptions(thinking=False)})
        service,repository,*_=runtime(configuration=lambda _:config)
        class EmptyPipeline:
            async def run(self,state,context):
                state.rewrite_query='rewritten question';state.language='English'
                state.image_description='a public widget picture';state.chat_model_supports_vision=False
                state.quoted_context='QUOTED SOURCE'
                raise SearchNothing()
        service.pipeline_factory=lambda *_:EmptyPipeline()
        turn=await repository.accept(REQ,'owner')
        prepared=await service.prepare_turn(turn)
        messages=prepare_generation(prepared.request).messages
        import json
        from pathlib import Path
        oracle=json.loads((Path(__file__).parent/'fixtures/fallback_output_go_20261015.json').read_text(encoding='utf-8'))
        assert oracle['exit_code']==0
        expected=next(row['prompt'] for row in oracle['rows'] if row['name']=='nonvision_image')
        assert prepared.request.finalized.system_prompt_override==expected
        assert expected in messages[0].content
        assert '[用户上传图片描述]' not in messages[0].content
        # The source fallback query is unchanged; the established workbench
        # language preference is appended by normal generation preparation.
        assert messages[-1].content=='rewritten question\n\n## Configured answer language\nAnswer in English.'
        assert not messages[-1].images
    run(scenario())


@pytest.mark.parametrize('mode',['CHEAP_ONLY','DYNAMIC'])
@pytest.mark.parametrize('name',['empty','partial_eof','normal_done','thinking_ignored'])
def test_same_plain_text_fixtures_match_executed_go_consumer_and_handler(name,mode):
    import json
    from pathlib import Path
    from backend.weknora_port.models.types import StreamResponse
    oracle=json.loads((Path(__file__).parent/'fixtures/fallback_output_go_20261015.json').read_text(encoding='utf-8'))
    assert oracle['exit_code']==0
    row=next(row for row in oracle['rows'] if row['name']==name)
    outputs={'empty':[], 'partial_eof':[event('partial ')],
        'normal_done':[event('normal answer',done=True,finish='stop',final=True)],
        'thinking_ignored':[StreamResponse('thinking','private thought'),event('partial ')]}[name]
    co,c,e,store,commit,life=setup(outputs)
    result=run(co.run(replace(fallback(mode),fallback_response='FIXED_SOURCE_REPLY'),CTX))
    answers=[{'content':r.content,'done':r.done,'is_fallback':r.data['is_fallback']} for r in store.events if r.type=='answer']
    assert answers==[{k:r[k] for k in ('content','done','is_fallback')} for r in row['events']]
    assert result.answer==commit.calls[0].answer==row['persisted_content']
    assert sum(e.type=='complete' for e in store.events)==row['completion_count']==1
    assert not any(e.type=='thinking' for e in store.events)
    assert len(c.calls)==1 and not e.calls
