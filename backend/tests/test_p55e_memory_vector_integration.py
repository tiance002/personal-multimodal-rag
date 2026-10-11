"""REAL isolated PostgreSQL storage; embedding provider remains SIMULATED."""
import uuid
from backend.app.application.memory import MemoryRetriever
from backend.app.domain.scope import Scope
from backend.app.ports.providers import EmbeddingResult
from backend.tests.test_p55a_lifecycle_integration import data
from backend.tests.test_p55b2_memory_integration import memory


class FakeEmbedding:
    provider_kind='local';provider_name='ollama';embedding_model='SIMULATED-memory-v1'
    calls=0
    def embed(self,texts,timeout_seconds):
        self.calls+=1
        return EmbeddingResult([[1.0,0.0] if ('travel' in t or 'journey' in t) else [0.0,1.0] for t in texts],
                               self.embedding_model,2,0.0)


def test_pg_memory_vectors_are_separate_versioned_and_scope_bounded(data,memory):
    m=memory;scope=Scope.from_ids([data.kb]);other=Scope.from_ids([str(uuid.uuid4())])
    first=m.repo.save(m.principal,scope,'fact','journey','journey planning')
    second=m.repo.save(m.principal,scope,'fact','language','unrelated language')
    other_id=m.repo.save(m.principal,other,'fact','foreign','journey foreign')
    m.repo.settings(m.principal,{'retrieval_mode':'semantic'})
    provider=FakeEmbedding();retriever=MemoryRetriever(m.repo,m.principal,embedding_provider=provider)
    assert [r['id'] for r in retriever.recall('travel choices',scope)]==[first]
    with m.repo.engine.connect() as c:
        rows=list(c.execute(__import__('sqlalchemy').text('SELECT item_id,item_version,profile_fingerprint,input_version,provider,model,dimensions,vector FROM memory_item_vectors ORDER BY item_id,profile_fingerprint')).mappings())
    assert {str(r['item_id']) for r in rows}=={first,second}
    assert all(r['provider']=='ollama' and r['model']=='SIMULATED-memory-v1' and r['dimensions']==2
               and r['profile_fingerprint'] and r['input_version']=='memory-fact-key-content/v1' for r in rows)
    assert m.repo.transition(m.principal,scope,first,'deleted') is None
    assert [r['id'] for r in retriever.recall('travel choices',scope)]==[]
    assert other_id not in {r['id'] for r in retriever.recall('travel choices',scope)}
    with m.repo.engine.connect() as c:
        assert c.execute(__import__('sqlalchemy').text('SELECT count(*) FROM memory_item_vectors WHERE item_id=:id'),{'id':first}).scalar_one()==1
