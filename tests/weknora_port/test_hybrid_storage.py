import asyncio

import pytest

from backend.weknora_port.models.types import CallContext,TrustedScope
from backend.weknora_port.retrieval.search import SearchParams,SearchTarget
from backend.weknora_port.storage.hybrid import PostgresKnowledgeRepository
from backend.weknora_port.storage.postgres import PostgresRetriever,IsolationPolicy
from tests.weknora_port.test_knowledge_storage import SourceConnection,fixture_row,tenant
from tests.weknora_port.test_storage import FakeCursor


class HybridConnection(SourceConnection):
    def __init__(self, rows, kbs): super().__init__(rows); self.kbs=kbs
    async def execute(self,query,params=()):
        if query.startswith('SELECT id,tenant_id,type,embedding_model_id'):
            self.calls.append((query,params)); return FakeCursor(self.kbs)
        return await super().execute(query,params)


class HybridRetriever(PostgresRetriever):
    def __init__(self,connection,vector_rows=(),keyword_rows=()):
        super().__init__(connection,IsolationPolicy('new',frozenset({'old'})))
        self.vector_rows,self.keyword_rows=vector_rows,keyword_rows
        self.vector_calls,self.keyword_calls=[],[]
    async def vectors(self,params): self.vector_calls.append(params); return list(self.vector_rows)
    async def keywords(self,params): self.keyword_calls.append(params); return list(self.keyword_rows)


def kb(id='kb',kind='document',model='model',strategy=None):
    return dict(id=id,tenant_id=1,type=kind,embedding_model_id=model,indexing_strategy=strategy)


def hit(chunk='chunk',score=.8):
    return dict(chunk_id=chunk,score=score,content='title\nindex input',knowledge_id='doc',knowledge_base_id='kb')


CTX=CallContext('r',TrustedScope('owner',('kb',),('doc',)))
TARGET=SearchTarget('kb','knowledge',('doc',))


def test_document_hybrid_uses_scoped_source_body_and_source_pool_with_one_embedding():
    async def exercise():
        conn=HybridConnection([fixture_row()],[kb()])
        retriever=HybridRetriever(conn,[hit()],[hit(score=10)])
        await retriever.verify_identity()
        calls=[]
        async def embed(kb_id,q,ctx): calls.append((kb_id,q,ctx.request_id)); return [1.0]
        repo=PostgresKnowledgeRepository(retriever,tenant,embed_query=embed)
        rows=await repo.hybrid_search([TARGET],SearchParams('学习',match_count=30,skip_context_enrichment=True),CTX)
        assert calls==[('kb','学习','r')]
        assert retriever.vector_calls[0].top_k==retriever.keyword_calls[0].top_k==150
        assert retriever.vector_calls[0].knowledge_ids==('doc',)
        assert rows[0].content=='original source' and rows[0].matched_content=='title\nindex input'
        assert rows[0].score==pytest.approx(1/61)
    asyncio.run(exercise())


def test_keyword_only_disabled_vector_and_zero_matchcount_default():
    async def exercise():
        conn=HybridConnection([fixture_row()],[kb(strategy={'wiki_enabled':True,'keyword_enabled':True})])
        retriever=HybridRetriever(conn,keyword_rows=[hit(score=10)])
        await retriever.verify_identity()
        rows=await PostgresKnowledgeRepository(retriever,tenant).hybrid_search([TARGET],
            SearchParams('query',match_count=0,skip_context_enrichment=True),CTX)
        assert not retriever.vector_calls and retriever.keyword_calls[0].top_k==250
        assert rows[0].score==1
    asyncio.run(exercise())


def test_mixed_full_partial_batch_and_wrong_physical_models_fail_before_retrieval():
    async def exercise():
        ctx=CallContext('r',TrustedScope('owner',('kb','other')))
        conn=HybridConnection([fixture_row()],[kb(),kb('other',model='different')])
        retriever=HybridRetriever(conn); await retriever.verify_identity()
        repo=PostgresKnowledgeRepository(retriever,tenant)
        with pytest.raises(ValueError,match='INCOMPATIBLE'):
            await repo.hybrid_search([SearchTarget('kb'),SearchTarget('other','knowledge',('doc',))],SearchParams('q'),ctx)
        async def identity(model,context): return model
        repo.model_identity=identity
        with pytest.raises(ValueError,match='MODELS_DIFFER'):
            await repo.hybrid_search([SearchTarget('kb'),SearchTarget('other')],SearchParams('q'),ctx)
        assert not retriever.keyword_calls and not retriever.vector_calls
    asyncio.run(exercise())


def test_faq_exact_negative_question_filter_does_not_invent_semantic_rejection():
    async def exercise():
        row=fixture_row(); row.update(chunk_type='faq',metadata={'negative_questions':['excluded']})
        conn=HybridConnection([row],[kb(kind='faq')]); retriever=HybridRetriever(conn,[hit()])
        await retriever.verify_identity()
        repo=PostgresKnowledgeRepository(retriever,tenant)
        params=SearchParams(' Excluded ',skip_context_enrichment=True)
        assert await repo.hybrid_search([TARGET],params,CTX,[1.0])==[]
        from dataclasses import replace
        rows=await repo.hybrid_search([TARGET],replace(params,query_text='excluded topic'),CTX,[1.0])
        assert rows[0].content=='original source'
        assert not retriever.keyword_calls
    asyncio.run(exercise())
