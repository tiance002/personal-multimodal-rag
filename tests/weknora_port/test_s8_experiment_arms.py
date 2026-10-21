"""Offline synthetic ABC contracts; no Provider, database or HTTP requests."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import pytest

from backend.weknora_port.development import DevelopmentSettings,DevelopmentConfigurationError
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.rerank import rerank,InvalidRerankResponse
from backend.weknora_port.retrieval.search import SearchNothing
from backend.weknora_port.models.types import RerankResult,CallCancelled
from tests.weknora_port.test_pipeline import CTX,Model,Registry,Repo,row,state,run
from tests.weknora_port.test_development_entry import settings,development_environment


def test_startup_default_a_and_b_opt_in_without_public_override(tmp_path):
    cfg=settings(tmp_path);env=development_environment(cfg)
    assert cfg.rerank_top1_floorless is False
    assert DevelopmentSettings.from_environment(env).rerank_top1_floorless is False
    env['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']='true'
    assert DevelopmentSettings.from_environment(env).rerank_top1_floorless is True
    env['WEKNORA_PORT_GRAPH_ENABLED']='true'
    with pytest.raises(DevelopmentConfigurationError,match='GRAPH_TOP1_EXPERIMENT_CONFLICT'):
        DevelopmentSettings.from_environment(env)
    with pytest.raises(DevelopmentConfigurationError,match='RERANK_TOP1_SETTING_INVALID'):
        replace(cfg,rerank_top1_floorless=1)
    from backend.weknora_port.http_api import SessionCreate
    with pytest.raises(Exception):SessionCreate(knowledge_base_scope=['kb'],rerank_top1_floorless=True)


@pytest.mark.parametrize('bad',[True,'true',1])
def test_d_rejected_before_qu_or_repository(bad):
    model=Model([]);registry=Registry(model);repo=Repo()
    s=state(graph_enabled=True,rerank_top1_floorless=bad,enable_rewrite=True,chat_model_id='chat')
    with pytest.raises(ValueError,match='GRAPH_TOP1_EXPERIMENT_CONFLICT|RERANK_TOP1_SETTING_INVALID'):
        run(RetrievalPipeline(repo,registry).run(s,CTX))
    assert not repo.calls and not registry.lookups and not model.calls


@pytest.mark.parametrize('score,baseline_accept',[(0,False),(.149,False),(.15,True),(.3,True)])
def test_only_b_floorless_changes_legal_empty_filter(score,baseline_accept):
    for floorless in (False,True):
        s=state(rerank_model_id='r',rerank_top1_floorless=floorless);s.search_results=[row()]
        m=Model([[RerankResult(0,score)]])
        if baseline_accept or floorless:
            run(rerank(s,CTX,Registry(m)))
            assert [r.id for r in s.rerank_results]==['a']
        else:
            with pytest.raises(SearchNothing):run(rerank(s,CTX,Registry(m)))
        assert len(m.calls)==1


def test_b_selects_actual_max_legal_top1_and_preserves_threshold_results():
    for raw,expected in [([RerankResult(0,.01),RerankResult(1,.12)],['b']),
                         ([RerankResult(0,.31),RerankResult(1,.12)],['a'])]:
        s=state(rerank_model_id='r',rerank_top1_floorless=True)
        s.search_results=[row('a'),row('b')]
        run(rerank(s,CTX,Registry(Model([raw]))))
        assert [r.id for r in s.rerank_results]==expected


@pytest.mark.parametrize('raw',[
    [RerankResult(-1,.1)],[RerankResult(1,.1)],
    [RerankResult(True,.1)],[RerankResult(0,True)],
    [RerankResult(0,float('nan'))],[RerankResult(0,float('inf'))],
    [RerankResult(0,10**1000)],[RerankResult(0,.1),RerankResult(0,.2)],
    [SimpleNamespace(index=0)],None,
])
@pytest.mark.parametrize('floorless',[False,True])
def test_malformed_results_fail_closed_independently_of_policy(raw,floorless):
    s=state(rerank_model_id='r',rerank_top1_floorless=floorless);s.search_results=[row()]
    with pytest.raises(InvalidRerankResponse):run(rerank(s,CTX,Registry(Model([raw]))))
    assert not s.rerank_results


def test_empty_and_cancelled_are_never_fabricated_top1():
    s=state(rerank_model_id='r',rerank_top1_floorless=True);s.search_results=[row()]
    with pytest.raises(SearchNothing):run(rerank(s,CTX,Registry(Model([[]]))))
    with pytest.raises(CallCancelled):run(rerank(s,CTX,Registry(Model([CallCancelled()]))))
    reg=Registry(Model([]));s.search_results=[]
    run(rerank(s,CTX,reg));assert not reg.model.calls
    s.search_results=[row()];s.rerank_model_id=''
    run(rerank(s,CTX,reg));assert not reg.model.calls


def test_normal_valid_a_matches_saved_s7_function_without_provider(monkeypatch):
    """Code baseline, same synthetic sorted response: not a new quality score."""
    import subprocess,types
    text=subprocess.check_output(['git','show','b9268e2d6707f73c48b5624a96e39879d02ce12c:backend/weknora_port/retrieval/rerank.py'],text=True,encoding='utf-8')
    original=types.ModuleType('backend.weknora_port.retrieval._s8_saved_rerank')
    original.__package__='backend.weknora_port.retrieval';exec(compile(text,'saved_s7_rerank.py','exec'),original.__dict__)
    for raw in ([RerankResult(1,.91),RerankResult(0,.5)],
                [RerankResult(1,.16),RerankResult(0,.05)],
                [RerankResult(1,.1),RerankResult(0,.05)]):
        outputs=[]
        for function in (original.rerank,rerank):
            s=state(rerank_model_id='r');s.search_results=[row('a'),row('b',content='other precise fact')]
            try:run(function(s,CTX,Registry(Model([deepcopy(raw)]))))
            except SearchNothing:outputs.append('SearchNothing')
            else:outputs.append([(r.id,r.score,r.metadata) for r in s.rerank_results])
        assert outputs[0]==outputs[1]
