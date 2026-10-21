import asyncio

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository, custom_metadata_text
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
from tests.weknora_port.test_storage import FakeConnection, FakeCursor
from backend.weknora_port.storage.chunks import ChunkPlan, StoredChunk, DocumentScope


class SourceConnection(FakeConnection):
    def __init__(self, rows):
        super().__init__()
        self.rows=rows
    async def execute(self, query, params=()):
        if 'FROM chunks c' in query:
            self.calls.append((query,params))
            return FakeCursor(self.rows)
        return await super().execute(query,params)


def fixture_row(**updates):
    return dict(id="chunk",tenant_id=1,knowledge_base_id="kb",knowledge_id="doc",content="original source",
        chunk_type="text",chunk_index=2,start_at=20,end_at=35,content_revision=1,
        parent_chunk_id="parent",metadata={"origin":"literal"},knowledge_title="Title",
        knowledge_custom_metadata={"topic":"learning"},**updates)


async def tenant(owner):
    if owner!="owner": raise PermissionError("unknown server principal")
    return 1


def test_loader_uses_original_body_and_bound_tenant_kb_document_scope():
    async def exercise():
        conn=SourceConnection([fixture_row()])
        retriever=PostgresRetriever(conn,IsolationPolicy("new",frozenset({"old"})))
        await retriever.verify_identity()
        repo=ScopedKnowledgeRepository(retriever,tenant)
        context=CallContext("r",TrustedScope("owner",("kb",),("doc",)))
        rows=await repo.get_chunks(["chunk"],context)
        assert rows["chunk"].content=="original source"
        assert rows["chunk"].content_revision==1 and rows["chunk"].parent_chunk_id=="parent"
        assert rows["chunk"].knowledge_custom_metadata=="topic: learning"
        assert rows["chunk"].chunk_metadata=={"origin":"literal"} and rows["chunk"].metadata=={}
        sql,values=conn.calls[-1]
        assert "c.knowledge_id IN (%s)" in sql and "k.tenant_id = %s" in sql
        assert sql.count("%s")==len(values)
        assert values==[1,1,1,"chunk","kb","doc"]
    asyncio.run(exercise())


def test_custom_metadata_matches_sorted_source_text_not_json_envelope():
    assert custom_metadata_text({'z':None,'b':True,'a':['one','two'],'count':1.0,
        'tiny':.00001,'large':1000000.0,'nested':{'b':2,'a':1},'empty':' '}) == (
        'a: [one two]\nb: true\ncount: 1\nlarge: 1e+06\nnested: map[a:1 b:2]\ntiny: 1e-05')


@pytest.mark.parametrize('field,value',[('tenant_id',2),('knowledge_base_id','other'),('knowledge_id','other')])
def test_returned_cross_scope_row_is_rejected(field,value):
    async def exercise():
        row=fixture_row(); row[field]=value
        conn=SourceConnection([row]); retriever=PostgresRetriever(conn,IsolationPolicy("new",frozenset({"old"})))
        await retriever.verify_identity()
        with pytest.raises(PermissionError,match="SCOPE_MISMATCH"):
            await ScopedKnowledgeRepository(retriever,tenant).get_chunks(["chunk"],
                CallContext("r",TrustedScope("owner",("kb",),("doc",))))
    asyncio.run(exercise())


def test_faq_only_decodes_source_metadata_not_answered_text():
    async def exercise():
        row=fixture_row(); row['chunk_type']='faq'; row['metadata']={'standard_question':'Q','answers':['A','B']}
        conn=SourceConnection([row]); retriever=PostgresRetriever(conn,IsolationPolicy("new",frozenset({"old"})))
        await retriever.verify_identity()
        answers=await ScopedKnowledgeRepository(retriever,tenant).get_faq_answers(["chunk"],
            CallContext("r",TrustedScope("owner",("kb",))))
        assert answers['chunk'].question=='Q' and answers['chunk'].answers==('A','B')
    asyncio.run(exercise())


def test_chunk_writer_500_row_batches_and_scope_checked_before_insert():
    async def exercise():
        conn=SourceConnection([])
        retriever=PostgresRetriever(conn,IsolationPolicy("new",frozenset({"old"})),
            writable_knowledge_base_ids=frozenset({"kb"}))
        await retriever.verify_identity()
        repo=ScopedKnowledgeRepository(retriever,tenant)
        scope=DocumentScope(1,"owner","kb","doc","v")
        rows=tuple(StoredChunk(str(i),scope,"literal",i,i*10,i*10+7,"text","") for i in range(501))
        context=CallContext("r",TrustedScope("owner",("kb",),("doc",)))
        await repo.create_chunks(ChunkPlan(rows,()),context)
        inserts=[(sql,params) for sql,params in conn.calls if sql.startswith("INSERT INTO chunks")]
        assert [len(params)//17 for _,params in inserts]==[500,1]
        assert all(sql.count("%s")==len(params) and "ON CONFLICT" not in sql for sql,params in inserts)
        before=len(conn.calls)
        bad_context=CallContext("r",TrustedScope("owner",("other",)))
        with pytest.raises(PermissionError): await repo.create_chunks(ChunkPlan(rows,()),bad_context)
        assert len(conn.calls)==before
    asyncio.run(exercise())


def test_image_children_loader_excludes_disabled_children_and_binds_parent_scope():
    async def exercise():
        visible=fixture_row(); visible.update(id='image',chunk_type='image_caption',is_enabled=True)
        disabled=fixture_row(); disabled.update(id='disabled',is_enabled=False)
        conn=SourceConnection([visible,disabled])
        retriever=PostgresRetriever(conn,IsolationPolicy('new',frozenset({'old'})))
        await retriever.verify_identity()
        repo=ScopedKnowledgeRepository(retriever,tenant)
        result=await repo.get_chunk_children(['parent'],CallContext('r',TrustedScope('owner',('kb',),('doc',))))
        assert [row.id for row in result]==['image']
        sql,params=conn.calls[-1]
        assert 'c.parent_chunk_id IN (%s)' in sql and 'c.is_enabled = TRUE' in sql
        assert params==[1,1,1,'parent','kb','doc']
    asyncio.run(exercise())


def test_primary_hydration_excludes_stale_index_bodies_but_parent_loader_does_not():
    async def exercise():
        stale=fixture_row(); stale['index_status']='failed'
        conn=SourceConnection([stale]); retriever=PostgresRetriever(conn,IsolationPolicy('new',frozenset({'old'})))
        await retriever.verify_identity()
        repo=ScopedKnowledgeRepository(retriever,tenant)
        context=CallContext('r',TrustedScope('owner',('kb',),('doc',)))
        assert await repo._read_chunks(['chunk'],context,searchable=True)=={}
        assert (await repo.get_chunks(['chunk'],context))['chunk'].content=='original source'
    asyncio.run(exercise())
