import asyncio

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
from tests.weknora_port.test_storage import FakeConnection, FakeCursor


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
        sql,values=conn.calls[-1]
        assert "c.knowledge_id IN (%s)" in sql and "k.tenant_id = %s" in sql
        assert sql.count("%s")==len(values)
        assert values==[1,1,1,"chunk","kb","doc"]
    asyncio.run(exercise())


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
