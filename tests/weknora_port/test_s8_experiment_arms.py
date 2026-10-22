"""Offline synthetic ABC contracts; no Provider, database or HTTP requests."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import pytest

from backend.weknora_port.development import DevelopmentSettings,DevelopmentConfigurationError
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.rerank import rerank,InvalidRerankResponse
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.search import SearchNothing
from backend.weknora_port.models.types import RerankResult,CallCancelled
from tests.weknora_port.test_pipeline import CTX,Model,Registry,Repo,row,state,run
from tests.weknora_port.test_development_entry import settings,development_environment


def test_owner_startup_top1_default_and_explicit_off_without_public_override(tmp_path):
    cfg=settings(tmp_path);env=development_environment(cfg)
    assert cfg.rerank_top1_floorless is True
    assert DevelopmentSettings.from_environment(env).rerank_top1_floorless is True
    env['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']='false'
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
def test_floorless_acceptance_and_s864_search_fallback_preserve_distinct_decisions(score,baseline_accept):
    for floorless in (False,True):
        s=state(rerank_model_id='r',rerank_top1_floorless=floorless);s.search_results=[row()]
        original=deepcopy(s.search_results)
        m=Model([[RerankResult(0,score)]])
        run(rerank(s,CTX,Registry(m)))
        if baseline_accept or floorless:
            assert [r.id for r in s.rerank_results]==['a']
            assert s.evidence_trace[-1]['reason']=='RERANK_ACCEPTED'
        else:
            # Owner S8.6.4 supersedes the old low-score SearchNothing contract.
            assert not s.rerank_results and s.search_results==original
            assert s.evidence_trace[-1]['reason']=='RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK'
            run(merge(s,CTX,None))
            assert [(r.id,r.score) for r in s.merge_results]==[(r.id,r.score) for r in original]
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
    original=deepcopy(s.search_results)
    run(rerank(s,CTX,Registry(Model([[]]))))
    assert not s.rerank_results and s.search_results==original
    assert s.evidence_trace[-1]['reason']=='RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK'
    run(merge(s,CTX,None))
    assert [(r.id,r.score) for r in s.merge_results]==[(r.id,r.score) for r in original]
    with pytest.raises(CallCancelled):run(rerank(s,CTX,Registry(Model([CallCancelled()]))))
    assert not s.rerank_results and s.search_results==original
    reg=Registry(Model([]));s.search_results=[]
    with pytest.raises(SearchNothing):run(rerank(s,CTX,reg))
    assert s.evidence_trace[-1]['reason']=='SEARCH_EMPTY' and not reg.model.calls
    s.search_results=[row()];s.rerank_model_id=''
    run(rerank(s,CTX,reg));assert not reg.model.calls


def test_saved_s7_accepted_parity_and_owner_s864_empty_pool_divergence_without_provider():
    """Keep the immutable oracle; S8.6.4 intentionally retains low-score Search."""
    import subprocess,types
    text=subprocess.check_output(['git','show','b9268e2d6707f73c48b5624a96e39879d02ce12c:backend/weknora_port/retrieval/rerank.py'],text=True,encoding='utf-8')
    original=types.ModuleType('backend.weknora_port.retrieval._s8_saved_rerank')
    original.__package__='backend.weknora_port.retrieval';exec(compile(text,'saved_s7_rerank.py','exec'),original.__dict__)
    for raw in ([RerankResult(1,.91),RerankResult(0,.5)],
                [RerankResult(1,.16),RerankResult(0,.05)],
                [RerankResult(1,.1),RerankResult(0,.05)]):
        outputs=[];states=[]
        for function in (original.rerank,rerank):
            s=state(rerank_model_id='r');s.search_results=[row('a'),row('b',content='other precise fact')]
            states.append(s)
            try:run(function(s,CTX,Registry(Model([deepcopy(raw)]))))
            except SearchNothing:outputs.append('SearchNothing')
            else:outputs.append([(r.id,r.score,r.metadata) for r in s.rerank_results])
        if outputs[0]=='SearchNothing':
            # Only the Owner-approved empty-pool behavior diverges; this does
            # not promote low-scoring Search candidates to supported facts.
            current=states[1]
            assert outputs[1]==[] and current.search_results==states[0].search_results
            assert current.evidence_trace[-1]['reason']=='RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK'
            run(merge(current,CTX,None))
            assert [(r.id,r.score) for r in current.merge_results]==[(r.id,r.score) for r in current.search_results]
        else:
            assert outputs[0]==outputs[1]
