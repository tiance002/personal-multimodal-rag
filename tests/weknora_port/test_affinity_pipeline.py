"""Fixed source memory-affinity counterexamples through the ordinary pipeline."""
import asyncio
from types import SimpleNamespace
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from tests.weknora_port.test_pipeline import Repo,Registry,Model,row,state,CTX


def test_normal_pipeline_consumes_familiar_document_after_rerank():
    from dataclasses import replace
    rows=[replace(row('stranger',content='The stranger document contains evidence A.',score=.8),knowledge_id='doc-stranger'),replace(row('familiar',content='The familiar document contains separate evidence B.',score=.78),knowledge_id='doc-familiar')]
    asked=[]
    async def affinity(ids):asked.append(ids);return {'doc-familiar':8}
    model=Model([(SimpleNamespace(index=0,score=.8),SimpleNamespace(index=1,score=.78))])
    s=state(rerank_model_id='reranker',rerank_top_k=2)
    pipe=RetrievalPipeline(Repo(rows),Registry(model),affinity_resolver=affinity)
    asyncio.run(pipe.run(s,CTX))
    assert asked==[('doc-stranger','doc-familiar')]
    assert s.rerank_results[0].knowledge_id=='doc-familiar'
    assert s.merge_results[0].knowledge_id=='doc-familiar'


def test_affinity_does_not_add_a_lane_when_rerank_is_disabled():
    async def affinity(ids):raise AssertionError('source has no RerankResult')
    s=state();pipe=RetrievalPipeline(Repo([row()]),Registry(),affinity_resolver=affinity)
    asyncio.run(pipe.run(s,CTX));assert s.merge_results


def test_fixed_go_affinity_traces():
    import json,math
    from pathlib import Path
    from backend.weknora_port.retrieval.memory_affinity import apply_memory_affinity
    fixture=json.loads((Path(__file__).parent/'fixtures/affinity_kb_go_oracle.json').read_text())
    assert fixture['exit_code']==0 and len(fixture['rows'])==12
    for case,expected in zip(fixture['cases'],fixture['rows']):
        if case['Kind']!='affinity':continue
        values=[SimpleNamespace(id=r['ID'],knowledge_id=r['KnowledgeID'],score=r['Score']) for r in case['Rows']]
        queried=[]
        async def lookup(ids):queried.extend(ids);return case['Hits']
        state=SimpleNamespace(rerank_results=values)
        asyncio.run(apply_memory_affinity(state,CTX,lookup))
        assert queried==(expected['asked'] or [])
        assert [r.id for r in state.rerank_results]==[r['ID'] for r in expected['rows'] or []]
        for actual,wanted in zip(state.rerank_results,expected['rows'] or []):assert math.isclose(actual.score,wanted['Score'],rel_tol=1e-14)


def test_affinity_lookup_unique_bounded_stable_and_failure_noop():
    from backend.weknora_port.retrieval.memory_affinity import apply_memory_affinity
    rows=[SimpleNamespace(id=str(i),knowledge_id=str(i),score=.5) for i in range(205)]
    rows.insert(1,SimpleNamespace(id='duplicate',knowledge_id='0',score=.5))
    queried=[]
    async def lookup(ids):queried.extend(ids);return {id:8 for id in ids}
    s=SimpleNamespace(rerank_results=rows)
    asyncio.run(apply_memory_affinity(s,CTX,lookup));assert len(queried)==len(set(queried))==200 and queried[0]=='0'
    assert [r.id for r in s.rerank_results[:2]]==['0','duplicate']
    async def fail(ids):raise RuntimeError('synthetic read unavailable')
    before=[r.score for r in rows];asyncio.run(apply_memory_affinity(s,CTX,fail));assert [r.score for r in rows]==before
