"""SIMULATED ranking/metadata; production retrieval, merge and citations."""
from dataclasses import replace
import hashlib
import json

import pytest

from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.application.retrieval_merge import join_body, merge_passages
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.ports.providers import EmbeddingResult, ProviderUnavailable, ProviderRequestNotSent
from backend.app.ports.model_access import model_access, scope_allows
from backend.app.adapters.models.cloud import SiliconFlowRerank
from backend.app.domain.model_registry import ModelRegistry
from backend.tests.test_model_provider_contracts import SimulatedGuard


def chunk(id, content='synthetic cost 42.75', **kwargs):
    return ChunkRecord(id, kwargs.pop('knowledge_base_id', 'kb'), kwargs.pop('document_id', 'doc'),
        kwargs.pop('version_id', 'v1'), content,
        kwargs.pop('locator', {'kind': 'text', 'start': 0, 'end': len(content), 'quote': content}),
        content_sha256=hashlib.sha256(content.encode()).hexdigest(), **kwargs)


def repository(*chunks):
    repo = InMemoryRetrievalRepository()
    for c in chunks:
        repo.add(c)
    return repo


SCOPE = Scope.from_ids(['kb'])


@pytest.mark.parametrize('mode', ['keyword', 'vector', 'hybrid'])
def test_channels_keep_original_profile_and_scope(mode):
    class Embedding:
        def embed(self, *a, **k):
            return EmbeddingResult([[1., 0.]], 'SIMULATED', 2, 0., 'p')
    repo = repository(chunk('child', embedding=(1., 0.), embedding_profile_id='p'),
        chunk('wrong-profile', 'unrelated', embedding=(1., 0.), embedding_profile_id='other'),
        chunk('foreign', knowledge_base_id='other', embedding=(1., 0.), embedding_profile_id='p'),
        chunk('parent', chunk_role='parent', embedding=(1., 0.), embedding_profile_id='p'))
    result = HybridRetriever(repo, Embedding(), mode=mode).retrieve(SCOPE, 'synthetic cost')
    assert [i.chunk.chunk_id for i in result.items] == ['child']
    assert result.retrieval_stats['candidate_count'] == 1


@pytest.mark.parametrize('bad', ['raise', 'empty', 'inject', 'duplicate', 'omit'])
def test_ranker_failures_keep_complete_fusion(bad):
    repo = repository(chunk('a'), chunk('b', document_id='doc2'))
    expected = HybridRetriever(repo, mode='keyword').retrieve(SCOPE, 'synthetic')
    class Ranker:
        def rank(self, question, hits, chunks):
            if bad == 'raise': raise TimeoutError('SIMULATED')
            if bad == 'empty': return []
            if bad == 'inject': return [RankedHit(chunk_id='foreign', rank=1)]
            if bad == 'duplicate': return [hits[0], hits[0]]
            return hits[:1]
    actual = HybridRetriever(repo, mode='keyword', ranker=Ranker()).retrieve(SCOPE, 'synthetic')
    assert actual.fused_ranking == expected.fused_ranking
    assert actual.items == expected.items
    assert 'RANKER_UNAVAILABLE' in actual.degradation_flags


def test_reordering_preserves_original_hit_metadata_and_topk():
    repo = repository(chunk('a'), chunk('b', document_id='doc2'))
    class Ranker:
        def rank(self, question, hits, chunks):
            return [RankedHit(chunk_id=h.chunk_id, rank=99, sources=('forged',), fused_score=999.)
                    for h in reversed(hits)]
    result = HybridRetriever(repo, mode='keyword', top_k=1, ranker=Ranker()).retrieve(SCOPE, 'synthetic')
    assert result.items[0].chunk.chunk_id == 'b'
    assert result.items[0].hit.sources == ('keyword',)
    assert result.items[0].hit.fused_score != 999.


@pytest.mark.parametrize('allowed', [True, False])
def test_cloud_ranker_gets_only_revalidated_candidates_and_kb_permission(allowed):
    repo = repository(chunk('a'), chunk('foreign', knowledge_base_id='other'), chunk('old', is_current=False))
    repo.embedding_scope_allowed = lambda scope: allowed
    # A faulty recall cannot authorize historical/out-of-scope evidence.
    repo.keyword_candidates = lambda *a: [RankedHit(chunk_id=id, rank=n) for n, id in enumerate(['a','foreign','old'], 1)]
    class Ranker:
        provider_kind = 'cloud'
        def rank(self, question, hits, chunks):
            assert set(chunks) == {'a'}
            assert scope_allows('rerank') is allowed
            if not allowed: raise ProviderRequestNotSent('MODEL_ROLE_EGRESS_DENIED')
            return hits
    result = HybridRetriever(repo, mode='keyword', ranker=Ranker()).retrieve(SCOPE, 'synthetic')
    assert [i.chunk.chunk_id for i in result.items] == ['a']
    assert ('RANKER_UNAVAILABLE' in result.degradation_flags) is (not allowed)


def test_parent_group_topk_and_original_citation_survive():
    body = 'prefix. synthetic cost 42.75. synthetic total 17.25. suffix.'
    parent = chunk('parent', body, chunk_role='parent', chunk_index=0)
    a = chunk('a', 'synthetic cost 42.75.', parent_id='parent', chunk_index=1)
    b = chunk('b', 'synthetic total 17.25.', parent_id='parent', chunk_index=2)
    repo = repository(parent, a, b, chunk('z', document_id='doc2'))
    result = HybridRetriever(repo, mode='keyword', top_k=1).retrieve(SCOPE, 'synthetic')
    assert {i.chunk.chunk_id for i in result.items} == {'a','b'}
    assert result.retrieval_stats['final_context_count'] == 1
    assert result.retrieval_stats['parent_count'] == 1
    store = InMemoryCitationStore()
    for c in (a,b): store.add(c)
    citations = CitationService(store)
    text, labels = ContextBuilder().build('run', result.items, citations)
    assert text.count(body) == 1
    assert labels == ['E1','E2'] and '[E1] '+a.content in text and '[E2] '+b.content in text
    for label, c in zip(labels, (a,b), strict=True):
        snap = citations.snapshots['run',label]
        assert (snap.chunk_id, snap.quote, snap.locator, snap.quote_sha256) == (c.chunk_id,c.content,c.locator,c.content_sha256)
        assert citations.resolve('run', label).quote == c.content


@pytest.mark.parametrize('change', [{'knowledge_base_id':'other'}, {'document_id':'other'},
    {'version_id':'v2'}, {'is_current':False}, {'index_identity':'old'}, {'content_sha256':'bad'}])
def test_parent_cannot_cross_boundaries(change):
    seed = chunk('a', parent_id='parent')
    parent = replace(chunk('parent', 'SECRET CONTEXT', chunk_role='parent'), **change)
    result = HybridRetriever(repository(seed,parent), mode='keyword').retrieve(SCOPE, 'synthetic')
    assert result.items[0].context_passage is None


def test_exact_position_overlap_does_not_remove_periodic_source_text():
    source = 'same table row; '*30
    a = chunk('a', source[:240], chunk_index=0,
        locator={'kind':'text','start':0,'end':240,'quote':source[:240]})
    b = chunk('b', source[210:420], chunk_index=1,
        locator={'kind':'text','start':210,'end':420,'quote':source[210:420]})
    groups = merge_passages(SCOPE, [(a,RankedHit(chunk_id='a',rank=1)),(b,RankedHit(chunk_id='b',rank=2))], {}, {})
    assert len(groups) == 1 and groups[0][0].content == source[:420]
    assert len(groups[0][1]) == 2


@pytest.mark.parametrize('boundary', ['version_id','document_id','knowledge_base_id'])
def test_duplicate_bodies_never_merge_across_source(boundary):
    a = chunk('a')
    b = replace(chunk('b'), **{boundary:'other'})
    groups = merge_passages(Scope.from_ids(['kb','other']),
        [(a,RankedHit(chunk_id='a',rank=1)),(b,RankedHit(chunk_id='b',rank=2))], {}, {})
    assert len(groups) == 2


def test_short_neighbor_is_whole_bounded_context_and_not_hit_or_citation():
    seed, small, large = chunk('s', 'original seed'), chunk('n', 'neighbor '*45), chunk('l','L'*900)
    hit = RankedHit(chunk_id='s',rank=1)
    passage, units = merge_passages(SCOPE,[(seed,hit)],{}, {'s':[(1,small),(-1,large)]})[0]
    assert passage.neighbor_ids == ('n',)
    assert small.content in passage.content and 'L'*900 not in passage.content
    assert units == [(seed,hit)] and len(passage.content) <= 850


def test_oversized_parent_falls_back_to_whole_child_without_truncation():
    seed = chunk('s','synthetic child',parent_id='p')
    parent = chunk('p','P'*1000,chunk_role='parent')
    result = HybridRetriever(repository(seed,parent),mode='keyword').retrieve(SCOPE,'synthetic')
    selected = ContextBuilder(100).select(result.items)
    assert len(selected)==1 and selected[0].chunk is seed and selected[0].context_passage is None


def test_native_table_proof_is_not_rewritten_and_missing_evidence_is_not_model_difficulty():
    row = chunk('row', locator={'kind':'xlsx','table_id':'t','cells':[{'value':'42.75'}]})
    result = HybridRetriever(repository(row),mode='keyword').retrieve(SCOPE,'synthetic')
    assert result.items[0].chunk is row and result.items[0].context_passage is None
    service = EvidenceService()
    missing = HybridRetriever(repository(),mode='keyword').retrieve(SCOPE,'missing')
    assert missing.reason_codes == ('NO_CANDIDATES',)
    assert not service.bundle(service.plan('missing'),missing).decision.accepted


def test_rerank_circuit_has_no_retry_and_independent_usage(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-P4-ONLY')
    guard, calls = SimulatedGuard(), []
    def fail(request, timeout):
        calls.append(json.loads(request.data))
        assert timeout == 30
        raise TimeoutError('SIMULATED')
    adapter = SiliconFlowRerank(ModelRegistry.frozen_defaults().select('rerank'), enabled=True,
        usage_guard=guard, transport=fail)
    c = chunk('c'); hits = [RankedHit(chunk_id='c',rank=1)]
    with model_access('rerank',allowed=True):
        with pytest.raises(ProviderUnavailable): adapter.rank('synthetic',hits,{'c':c})
        with pytest.raises(ProviderRequestNotSent, match='CIRCUIT_OPEN'): adapter.rank('synthetic',hits,{'c':c})
    assert len(calls)==1 and adapter.circuit_open
    assert adapter.receipts[0]['role']=='rerank' and adapter.receipts[0]['settlement']=='UNKNOWN'


@pytest.mark.parametrize('cloud,egress', [(False,False),(False,True),(True,False),(True,True)])
def test_composition_wires_rerank_independently_with_default_deny(tmp_path,cloud,egress):
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    settings=Settings(database_url='sqlite+pysqlite:///:memory:',storage_root=tmp_path,
        rerank_enabled=True,cloud_enabled=cloud,rerank_egress_enabled=egress)
    container=build_container(settings,model=None,agent_model=None,cloud_model=None)
    ranker=container.knowledge_gateway.retriever.ranker
    assert isinstance(ranker,SiliconFlowRerank) and ranker.enabled is (cloud and egress)
    assert ranker.usage_guard is None and ranker.requests==0


def test_provider_meta_tokens_are_observed_not_billing_settlement(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-P4-META')
    guard=SimulatedGuard()
    adapter=SiliconFlowRerank(ModelRegistry.frozen_defaults().select('rerank'),enabled=True,
        usage_guard=guard,transport=lambda *args:dict(results=[dict(index=0,relevance_score=.8)],
            meta=dict(tokens=dict(input_tokens=15,output_tokens=2),billed_units=dict(search_units=1))))
    c=chunk('c')
    with model_access('rerank',allowed=True):adapter.rank('synthetic',[RankedHit(chunk_id='c',rank=1)],{'c':c})
    assert adapter.last_result.usage_actual==dict(prompt_tokens=15,completion_tokens=2,total_tokens=17)
    assert adapter.receipts[0]['settlement']=='UNKNOWN'
    assert guard.reservations[0]['planned_tokens'] is None


def test_authorized_transport_counts_bytes_before_uncertain_send_without_secret_echo():
    from urllib.request import Request
    from scripts.verify_p4_rag import AuthorizedTransport
    record={'attempts':[]}; saved=[]; sent=[]
    def send(request,timeout):sent.append(1);return {'SIMULATED':True}
    transport=AuthorizedTransport(record,lambda:saved.append(len(record['attempts'])),send,{'synthetic'})
    payload=json.dumps(dict(model='BAAI/bge-reranker-v2-m3',query='Synthetic cost',documents=['synthetic'])).encode()
    request=Request('https://api.siliconflow.cn/v1/rerank',data=payload,
        headers={'Authorization':'Bearer SIMULATED-P4-SECRET'})
    for _ in range(3):transport(request,30)
    with pytest.raises(ProviderRequestNotSent,match='CAP_EXCEEDED'):transport(request,30)
    assert saved==[1,2,3] and len(sent)==3
    assert sum(a['request_body_bytes'] for a in record['attempts'])==3*len(payload)
    assert 'SIMULATED-P4-SECRET' not in json.dumps(record)
    big='x'*20000
    transport=AuthorizedTransport({'attempts':[]},lambda:None,send,{big})
    oversized=Request(request.full_url,data=json.dumps(dict(model='BAAI/bge-reranker-v2-m3',
        query='Synthetic cost',documents=[big])).encode())
    with pytest.raises(ProviderRequestNotSent,match='CAP_EXCEEDED'):transport(oversized,30)
    assert len(sent)==3


def test_authorized_transport_does_not_retry_network_exception():
    from urllib.request import Request
    from scripts.verify_p4_rag import AuthorizedTransport
    record={'attempts':[]}; calls=[]
    def fail(*args):calls.append(1);raise TimeoutError('SIMULATED')
    transport=AuthorizedTransport(record,lambda:None,fail,{'synthetic'})
    request=Request('https://api.siliconflow.cn/v1/rerank',data=json.dumps(dict(
        model='BAAI/bge-reranker-v2-m3',query='Synthetic cost',documents=['synthetic'])).encode())
    with pytest.raises(TimeoutError):transport(request,30)
    assert len(calls)==len(record['attempts'])==1


def test_local_egress_denial_does_not_open_provider_failure_circuit(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-P4-DENIAL')
    adapter=SiliconFlowRerank(ModelRegistry.frozen_defaults().select('rerank'),enabled=True,
        usage_guard=SimulatedGuard(),transport=lambda *args:dict(results=[dict(index=0,relevance_score=.8)]))
    c=chunk('c');hits=[RankedHit(chunk_id='c',rank=1)]
    with model_access('rerank',allowed=False):
        with pytest.raises(ProviderRequestNotSent):adapter.rank('synthetic',hits,{'c':c})
    assert not adapter.circuit_open and not adapter.requests
    with model_access('rerank',allowed=True):assert adapter.rank('synthetic',hits,{'c':c})==tuple(hits)
