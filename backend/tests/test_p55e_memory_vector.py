"""Memory retrieval modes use an isolated, local-only vector index."""
import copy
from backend.app.application.memory import MemoryRetriever
from backend.app.domain.scope import Scope


class FakeRepo:
    def __init__(self, rows, mode):
        self.rows=copy.deepcopy(rows);self.mode=mode;self.vectors={};self.upserts=[]
    def recall_items(self,*_):return copy.deepcopy(self.rows)
    def settings(self,*_):return {'retrieval_mode':self.mode,'read_enabled':True}
    def vector_rows(self,principal,rows,*,provider,model,dimensions,profile_fingerprint,input_version):
        return {r['id']:(self.vectors[r['id']][4],self.vectors[r['id']][5],self.vectors[r['id']][3])
            for r in rows if r['id'] in self.vectors and self.vectors[r['id']][:3]==(provider,model,dimensions)
            and self.vectors[r['id']][6:8]==(profile_fingerprint,input_version)}
    def upsert_vector(self,principal,row,*,provider,model,dimensions,profile_fingerprint,input_version,vector):
        self.vectors[row['id']]=(provider,model,dimensions,vector,row['version'],row['content_hash'],
                                 profile_fingerprint,input_version)
        self.upserts.append(row['id']);return True


class FakeEmbedding:
    provider_kind='local';provider_name='ollama';embedding_model='simulated-memory-embeddings'
    calls=0
    def embed(self,texts,timeout_seconds):
        self.calls+=1
        def vector(text):
            text=text.casefold()
            return [1.0,0.0] if 'travel' in text or 'journey' in text else [0.0,1.0]
        from backend.app.ports.providers import EmbeddingResult
        return EmbeddingResult([vector(x) for x in texts],self.embedding_model,2,0.0,
                               identity_fingerprint=getattr(self,'cloud_fingerprint',None))


def rows():
    return [
        dict(id='a',scope_type='SCOPED',fact_key='topic',kind='fact',content='journey planning',version=1,content_hash='ha'),
        dict(id='b',scope_type='SCOPED',fact_key='other',kind='fact',content='unrelated language',version=1,content_hash='hb'),
    ]


def test_semantic_mode_indexes_and_ranks_only_visible_memory():
    repo=FakeRepo(rows(),'semantic');provider=FakeEmbedding()
    retriever=MemoryRetriever(repo,'principal',embedding_provider=provider)
    result=retriever.recall('travel options',Scope.from_ids(['kb']))
    assert [r['id'] for r in result]==['a']
    assert set(repo.vectors)=={'a','b'} and retriever.last_vector_status=='READY'
    provider.calls=0
    assert [r['id'] for r in retriever.recall('travel options',Scope.from_ids(['kb']))]==['a']
    assert provider.calls==1 and repo.upserts==['a','b']


def test_hybrid_uses_rank_fusion_without_changing_lexical_default():
    repo=FakeRepo(rows(),'hybrid');retriever=MemoryRetriever(repo,'principal',embedding_provider=FakeEmbedding())
    assert {r['id'] for r in retriever.recall('language travel',Scope.from_ids(['kb']))}=={'a','b'}
    lexical=MemoryRetriever(FakeRepo(rows(),'lexical'),'principal')
    assert [r['id'] for r in lexical.recall('language',Scope.from_ids(['kb']))]==['b']


def test_semantic_mode_never_calls_nonlocal_provider_or_uses_false_lexical_fallback():
    repo=FakeRepo(rows(),'semantic')
    provider=FakeEmbedding();provider.provider_kind='cloud'
    retriever=MemoryRetriever(repo,'principal',embedding_provider=provider)
    assert retriever.recall('language',Scope.from_ids(['kb']))==[]
    assert provider.calls==0 and not repo.vectors
    assert retriever.last_vector_status=='CLOUD_SCOPE_DENIED'


def test_cloud_provider_requires_authorized_scoped_egress_and_excludes_global_text():
    from types import SimpleNamespace
    from backend.app.ports.model_access import scope_allows
    class CloudFake(FakeEmbedding):
        provider_kind='cloud';provider_name='siliconflow';identity=SimpleNamespace(fingerprint='simulated-cloud-fingerprint')
        cloud_fingerprint='simulated-cloud-fingerprint'
        seen=[]
        def embed(self,texts,timeout_seconds):
            assert scope_allows('embedding')
            self.seen.extend(texts)
            return super().embed(texts,timeout_seconds)
    repo=FakeRepo(rows(),'semantic');repo.memory_embedding_scope_allowed=lambda scope:True
    repo.rows.append(dict(id='g',scope_type='GLOBAL',fact_key='owner',kind='profile',content='private global memory',version=1,content_hash='hg'))
    provider=CloudFake();retriever=MemoryRetriever(repo,'principal',embedding_provider=provider)
    result=retriever.recall('travel options',Scope.from_ids(['kb']))
    assert [r['id'] for r in result]==['a']
    assert provider.seen and all('private global memory' not in text for text in provider.seen)


def test_cloud_global_memory_has_no_scope_and_is_never_sent():
    repo=FakeRepo([dict(id='g',scope_type='GLOBAL',fact_key='owner',kind='profile',content='private global memory',version=1,content_hash='hg')],'semantic')
    repo.memory_embedding_scope_allowed=lambda scope:False
    provider=FakeEmbedding();provider.provider_kind='cloud';provider.provider_name='siliconflow'
    retriever=MemoryRetriever(repo,'principal',embedding_provider=provider)
    assert retriever.recall('travel options',Scope.from_ids([]))==[]
    assert provider.calls==0 and not repo.vectors and retriever.last_vector_status=='CLOUD_SCOPE_DENIED'


def test_isolated_revision_gate_is_e_only_and_exact(monkeypatch):
    import pytest
    from backend.tests.test_p55a_lifecycle_integration import _require_isolated_revision
    monkeypatch.setenv('P55B21_ISOLATED_AUTHORIZED','p55b21-global-scoped-r1')
    monkeypatch.setenv('P55E_ISOLATED_AUTHORIZED','p55e-memory-vector-r1')
    _require_isolated_revision('0022_memory_vector_index')
    with pytest.raises(AssertionError,match='ISOLATED_REVISION_MISMATCH'):
        _require_isolated_revision('0021_global_scoped_memory')
    monkeypatch.delenv('P55E_ISOLATED_AUTHORIZED')
    _require_isolated_revision('0021_global_scoped_memory')
    with pytest.raises(AssertionError,match='ISOLATED_REVISION_MISMATCH'):
        _require_isolated_revision('0022_memory_vector_index')
    monkeypatch.setenv('P55E_ISOLATED_AUTHORIZED','wrong-token')
    with pytest.raises(AssertionError,match='ISOLATED_REVISION_MISMATCH'):
        _require_isolated_revision('0022_memory_vector_index')
