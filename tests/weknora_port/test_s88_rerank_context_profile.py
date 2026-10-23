"""Synthetic approved-profile opt-in; no retrieval or Provider is invoked."""
import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
from backend.weknora_port.generation_settings import resolve_settings,apply_settings
from backend.weknora_port.models.types import ChatOptions,CallContext,TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.service import TurnConfiguration
from scripts import s8_8_acceptance_profile as profile

MISSING=object()

def fixture(actual=False,approved=MISSING):
    settings=resolve_settings({}, {},reasoning_mode='quick',max_output_tokens=2048)
    options=apply_settings(ChatOptions(temperature=.7),settings)
    state=PipelineState(query='Synthetic source query',language='English',
        targets=(SearchTarget('kb'),),query_understand_model_id='qu',bridge_cheap_model_id='cheap',
        rerank_model_id='rerank',rerank_context_enabled=actual,rerank_top1_floorless=True,
        retrieval_strategy_mode='query_structure_v1',source_evidence_mode='bounded_source_v1')
    configuration=TurnConfiguration(state,{'policy':RouterPolicy(mode='DYNAMIC'),'options':options,
        'generation_settings':settings,'system_prompt':DEFAULT_SYSTEM_PROMPT})
    turn=SimpleNamespace(run_id='server',client_request_id='client',
        request=SimpleNamespace(content=state.query,generation_settings=settings,accept_language='en-US'),
        context=CallContext('server',TrustedScope('owner',('kb',))))
    row={'request_id':'client','question':state.query,'mode':'query_structure_v1',
        'reasoning_mode':'quick','max_output_tokens':2048}
    if approved is not MISSING:row['rerank_context_enabled']=approved
    batch={'turns':[row],'qu_model_id':'qu','model_ids':{'cheap':'cheap','rerank':'rerank'}}
    async def resolve(turn):return configuration
    runtime=SimpleNamespace(ordinary=SimpleNamespace(configuration=resolve))
    return runtime,configuration,turn,batch

def run(runtime,turn,batch):
    profile.install_profile(runtime,approved_batch=batch,
        approved_scope={'knowledge_base_ids':['kb'],'document_ids':[]},
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    return asyncio.run(runtime.ordinary.configuration(turn))

@pytest.mark.parametrize('actual,approved',[(False,MISSING),(False,False),(True,True)])
def test_approved_context_matches_actual_and_preserves_normal_configuration(actual,approved):
    app,cfg,turn,batch=fixture(actual,approved);result=run(app,turn,batch)
    assert result.pipeline_state.rerank_context_enabled is actual
    assert result.generation_options['options'] is cfg.generation_options['options']
    assert result.generation_options['policy'] is cfg.generation_options['policy']
    assert result.generation_options['policy']==RouterPolicy(mode='DYNAMIC')
    assert result.pipeline_state.rerank_threshold==.3 and result.pipeline_state.rerank_top_k==30
    assert result.pipeline_state.source_evidence_mode=='bounded_source_v1'
    assert result.generation_options['citations_enabled'] is True

@pytest.mark.parametrize('actual,approved',[(True,MISSING),(True,False),(False,True),(1,True),(0,False)])
def test_context_drift_or_nonboolean_actual_is_rejected(actual,approved):
    app,_,turn,batch=fixture(actual,approved)
    with pytest.raises(ValueError,match='S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED'):run(app,turn,batch)

@pytest.mark.parametrize('value',[0,1,None,'true',{},[]])
def test_nonboolean_approved_context_is_denied(value):
    app,_,turn,batch=fixture(False,value)
    with pytest.raises(ValueError,match='S88_APPROVED_RERANK_CONTEXT_REQUIRED'):run(app,turn,batch)

@pytest.mark.parametrize('drift',['threshold','topk','floorless','qu','scope','question','policy'])
def test_opt_in_retains_all_other_profile_guards(drift):
    app,cfg,turn,batch=fixture(True,True)
    if drift=='threshold':cfg.pipeline_state.rerank_threshold=.1
    elif drift=='topk':cfg.pipeline_state.rerank_top_k=10
    elif drift=='floorless':cfg.pipeline_state.rerank_top1_floorless=False
    elif drift=='qu':cfg.pipeline_state.query_understand_model_id='other'
    elif drift=='scope':turn.context=replace(turn.context,scope=TrustedScope('owner',('other-kb',)))
    elif drift=='question':turn.request.content='Unapproved query'
    elif drift=='policy':cfg.generation_options['policy']=RouterPolicy(mode='OFF')
    with pytest.raises(ValueError):run(app,turn,batch)
