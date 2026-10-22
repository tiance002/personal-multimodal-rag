"""Owner-approved server Top1 default, explicit opt-out and profile identity."""
import asyncio
from dataclasses import replace

import pytest

from backend.weknora_port.development import DevelopmentConfigurationError, DevelopmentSettings
from backend.weknora_port.http_api import MessageCreate, SessionCreate
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.runtime import ConversationSettings
from scripts import s8_6_1_api_acceptance as prior
from scripts import s8_6_2_r3_40_acceptance as ecs40
from test_development_entry import development_environment, settings
from test_pipeline import CTX, Registry, Repo, state
from test_s861_preflight import guarded_runtime, install_guard
from test_s86_qu_configuration import assembled_runtime
from types import SimpleNamespace


def test_effective_server_defaults_true_and_explicit_false_survives(tmp_path):
    config=settings(tmp_path)
    env=development_environment(config)
    assert ConversationSettings().rerank_top1_floorless is True
    assert config.rerank_top1_floorless is True
    assert DevelopmentSettings.from_environment(env).rerank_top1_floorless is True
    env['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']='false'
    assert DevelopmentSettings.from_environment(env).rerank_top1_floorless is False
    assert replace(config,rerank_top1_floorless=False).rerank_top1_floorless is False
    assert ConversationSettings.from_mapping({'conversation':{'rerank_top1_floorless':False}}).rerank_top1_floorless is False
    assert PipelineState().rerank_top1_floorless is False


@pytest.mark.parametrize('configured,flag',[(None,True),('true',True),('false',False)])
def test_normal_runtime_transmits_server_flag_without_public_override(monkeypatch,tmp_path,configured,flag):
    from scripts.s8_ecs_runtime import runtime_environment
    from test_s8_ecs_runtime import base_env
    env={**development_environment(settings(tmp_path)),**base_env(tmp_path)}
    if configured is not None:env['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']=configured
    startup=DevelopmentSettings.from_environment(runtime_environment(env))
    assert startup.rerank_top1_floorless is flag
    app,saved,_=assembled_runtime(monkeypatch,ConversationSettings(rerank_top1_floorless=startup.rerank_top1_floorless))
    asyncio.run(app.create_session(app.principal,SessionCreate(knowledge_base_scope=['kb'])))
    turn=SimpleNamespace(session_id=saved['id'],run_id='run',context=CallContext('run',TrustedScope('synthetic-subject',('kb',))),
        request=SimpleNamespace(summary_model_id='',reasoning_effort='off',accept_language='en-US',knowledge_base_scope=('kb',)))
    assert asyncio.run(app._configuration(turn)).pipeline_state.rerank_top1_floorless is flag
    for dto,fields in ((SessionCreate,{'knowledge_base_scope':['kb']}),
                       (MessageCreate,{'knowledge_base_scope':['kb'],'content':'q','request_id':'id'})):
        with pytest.raises(Exception):dto(**fields,rerank_top1_floorless=flag)


def test_profile_fingerprints_and_state_snapshot_record_effective_true():
    assert prior.profile_manifest()['rerank_top1_floorless'] is True
    assert ecs40.profile_manifest()['rerank_top1_floorless'] is True
    assert prior.state_snapshot(PipelineState(rerank_top1_floorless=True))['rerank_top1_floorless'] is True


@pytest.mark.parametrize('flag',[True,False])
def test_approved_profile_validates_top1_policy_instead_of_rewriting_it(flag):
    app,base,turn,approved,_=guarded_runtime()
    base.pipeline_state.rerank_top1_floorless=flag
    install_guard(app,approved)
    if flag:
        assert asyncio.run(app.ordinary.configuration(turn)).pipeline_state.rerank_top1_floorless is True
    else:
        with pytest.raises(ValueError,match='S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED'):
            asyncio.run(app.ordinary.configuration(turn))
        assert base.pipeline_state.rerank_top1_floorless is False


def test_graph_requires_explicit_false_override_and_bridge_conflict_stays_closed(tmp_path):
    env=development_environment(settings(tmp_path))
    env['WEKNORA_PORT_GRAPH_ENABLED']='true'
    with pytest.raises(DevelopmentConfigurationError,match='GRAPH_TOP1_EXPERIMENT_CONFLICT'):
        DevelopmentSettings.from_environment(env)
    env['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']='false'
    assert DevelopmentSettings.from_environment(env).graph_enabled is True
    for options,code in [({'graph_enabled':True},'GRAPH_TOP1_EXPERIMENT_CONFLICT'),
                         ({'bridge_rescue_mode':'cheap_bridge_v1'},'BRIDGE_EXPERIMENT_CONFLICT')]:
        s=state(rerank_top1_floorless=True,**options)
        repo=Repo();registry=Registry()
        with pytest.raises(ValueError,match=code):asyncio.run(RetrievalPipeline(repo,registry).run(s,CTX))
        assert not repo.calls and not registry.lookups
