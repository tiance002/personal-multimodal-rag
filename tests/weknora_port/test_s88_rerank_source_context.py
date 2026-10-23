"""Opt-in scoring context only; synthetic sources and SQL, no Provider sends."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import pytest

from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.models.types import CallContext,TrustedScope,RerankResult
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.storage.postgres import PostgresRetriever,IsolationPolicy
from tests.weknora_port.test_knowledge_storage import SourceConnection,tenant
from tests.weknora_port.test_pipeline import Model,Registry

module=importlib.import_module('backend.weknora_port.retrieval.rerank')
CTX=CallContext('r',TrustedScope('owner',('kb',),('doc',)))


def source(id='body',content='旅游收入增长18%。',**kwargs):
    values=dict(knowledge_id='doc',knowledge_base_id='kb',chunk_index=1,start_at=100,
                end_at=100+len(content),metadata={'source_version':'v1'},chunk_metadata={'source_version':'v1'})
    values.update(kwargs)
    return SearchResult(id,content,.5,**values)


def head(**kwargs):
    return source('first','2019年北松市统计公报\n原始标题和年份。',chunk_index=0,start_at=0,end_at=24,**kwargs)


def valid_head():
    row=head();row.end_at=len(row.content);return row


def test_source_urls_survive_optin_scoring_and_legacy_remains_exact():
    text='联系 [support](https://example.test/r/ctl/21) 或 https://example.test/contact'
    row=source(content=text);old=module.enriched_passage(row)
    assert 'https://' not in old
    actual,proof=module.render_scoring_passage(row)
    assert 'https://example.test/r/ctl/21' in actual and 'https://example.test/contact' in actual
    assert not proof and row.content==text and module.enriched_passage(row)==old


def test_prefix_comes_from_same_current_original_and_has_exact_proof():
    row=source();first=valid_head();original=deepcopy(row)
    actual,proof=module.render_scoring_passage(row,first)
    assert first.content in actual and row.content in actual
    assert proof=={'source_chunk_id':'first','source_version':'v1','document_id':'doc',
                   'start':0,'end':len(first.content),'quote_sha256':hashlib.sha256(first.content.encode()).hexdigest()}
    assert row==original
    long=source('first','源'*600,chunk_index=0,start_at=0,end_at=600)
    rendered,bound=module.render_scoring_passage(row,long)
    assert bound['end']==400 and '源'*401 not in rendered


@pytest.mark.parametrize('change',[
    {'knowledge_id':'other'},{'knowledge_base_id':'other'},{'metadata':{'source_version':'v2'}},
    {'chunk_metadata':{'source_version':'old'}},{'is_enabled':False},{'content_revision':1},
    {'chunk_type':'summary'},{'chunk_index':2},{'end_at':999},{'content_rewritten':True},
])
def test_invalid_source_proof_adds_no_prefix(change):
    actual,proof=module.render_scoring_passage(source(),replace(valid_head(),**change))
    assert actual==module.render_scoring_passage(source())[0] and proof=={}


def test_one_batch_prefix_lookup_changes_only_scoring_not_mmr_or_source(monkeypatch):
    async def run():
        first=valid_head();row=source(content='literal https://example.test/link')
        class Repository:
            calls=[]
            async def get_first_source_chunks(self,ids,context):self.calls.append((ids,context));return {'doc':first}
        repo=Repository();observed=[];old_mmr=module.mmr
        def capture(rows,passages,k,segmenter=None):observed.append(passages);return old_mmr(rows,passages,k,segmenter)
        monkeypatch.setattr(module,'mmr',capture)
        s=PipelineState(query='unrelated question',rewrite_query='unrelated question',targets=(SearchTarget('kb'),),
            search_results=[row],rerank_model_id='rank',rerank_context_enabled=True)
        model=Model([[RerankResult(0,.8)]])
        await module.rerank(s,CTX,Registry(model),repository=repo)
        assert repo.calls==[(('doc',),CTX)] and first.content in model.calls[0][1][0]
        assert 'https://example.test/link' in model.calls[0][1][0]
        assert observed==[[module.enriched_passage(row,deduplicate_images=True)]]
        assert s.rerank_results[0].content==row.content and s.rerank_threshold==.3
        assert any(t['stage']=='rerank_source_context' and t['prefixes'][0]['source_chunk_id']=='first' for t in s.evidence_trace)
        s.rerank_context_enabled=False;repo.calls.clear();legacy=Model([[RerankResult(0,.8)]])
        await module.rerank(s,CTX,Registry(legacy),repository=repo)
        assert not repo.calls and legacy.calls[0][1]==[module.enriched_passage(row)]
    asyncio.run(run())


def stored(**updates):
    text='2019年北松市统计公报'
    value=dict(id='first',tenant_id=1,knowledge_base_id='kb',knowledge_id='doc',content=text,
        chunk_type='text',chunk_index=0,start_at=0,end_at=len(text),content_revision=0,is_enabled=True,
        metadata={'source_version':'v1'},knowledge_metadata={'source_version':'v1'},
        knowledge_enable_status='enabled',knowledge_parse_status='completed')
    value.update(updates);return value


def test_first_source_lookup_is_one_scoped_current_query_and_rejects_stale_duplicate():
    async def run():
        conn=SourceConnection([stored()]);retriever=PostgresRetriever(conn,IsolationPolicy('new',frozenset({'old'})))
        await retriever.verify_identity();repo=ScopedKnowledgeRepository(retriever,tenant)
        before=len(conn.calls);rows=await repo.get_first_source_chunks(('doc',),CTX)
        assert list(rows)==['doc'] and len(conn.calls)==before+1
        sql,params=conn.calls[-1]
        assert "c.chunk_index = 0" in sql and "c.chunk_type = 'text'" in sql and "k.parse_status='completed'" in sql
        assert "c.metadata->>'source_version'=k.metadata->>'source_version'" in sql
        assert 'c.tenant_id = %s' in sql and 'c.knowledge_base_id IN (%s)' in sql and 'c.knowledge_id IN (%s)' in sql
        assert sql.count('%s')==len(params)
        conn.rows=[stored(metadata={'source_version':'old'})];assert not await repo.get_first_source_chunks(('doc',),CTX)
        conn.rows=[stored(),stored(id='duplicate')];assert not await repo.get_first_source_chunks(('doc',),CTX)
        conn.rows=[stored(knowledge_id='foreign')]
        with pytest.raises(PermissionError):await repo.get_first_source_chunks(('doc',),CTX)
        with pytest.raises(ValueError):await repo.get_first_source_chunks(tuple('d'+str(i) for i in range(31)),CTX)
    asyncio.run(run())


@pytest.mark.parametrize('invalid_first',[True,False])
def test_invalid_duplicate_first_source_is_ambiguous_in_either_order(invalid_first):
    async def run():
        rows=[stored(id='invalid',end_at=999),stored()]
        conn=SourceConnection(rows if invalid_first else rows[::-1])
        guard=PostgresRetriever(conn,IsolationPolicy('new',frozenset({'old'})))
        await guard.verify_identity()
        assert not await ScopedKnowledgeRepository(guard,tenant).get_first_source_chunks(('doc',),CTX)
    asyncio.run(run())


def test_leased_runtime_delegates_first_source_read_with_scope():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    async def run():
        conn=SourceConnection([stored()])
        guard=PostgresRetriever(conn,IsolationPolicy('new',frozenset({'old'})))
        await guard.verify_identity()
        class Leases:
            entered=0
            exited=0
            @asynccontextmanager
            async def lease(self):
                self.entered+=1
                try:yield guard
                finally:self.exited+=1
        leases=Leases()
        app=SimpleNamespace(principal=SimpleNamespace(tenant_id=1,subject_id='owner'),leases=leases)
        repo=LeasedKnowledgeRepository(app,SimpleNamespace(physical_key=None))
        assert list(await repo.get_first_source_chunks(('doc',),CTX))==['doc']
        with pytest.raises(PermissionError):await repo.get_first_source_chunks(('foreign',),CTX)
        assert leases.entered==leases.exited==2
        assert not hasattr(repo,'unscoped_query')
    asyncio.run(run())


@pytest.mark.parametrize('stop',['run_deadline','model_deadline','cancel'])
def test_blocked_first_source_read_propagates_deadline_or_cancel_without_rerank(stop):
    from backend.weknora_port.models.types import CallCancelled,RunDeadlineExceeded
    async def run():
        started,closed,cancelled=asyncio.Event(),asyncio.Event(),asyncio.Event()
        class Repository:
            async def get_first_source_chunks(self,ids,context):
                started.set()
                try:await asyncio.Event().wait()
                finally:closed.set()
        ctx=replace(CTX,cancelled=cancelled,deadline_kind='model' if stop=='model_deadline' else 'run',
                    deadline=None if stop=='cancel' else asyncio.get_running_loop().time()+.03)
        model=Model([[RerankResult(0,.8)]])
        state=PipelineState(query='q',rewrite_query='q',targets=(SearchTarget('kb'),),
            search_results=[source()],rerank_model_id='rank',rerank_context_enabled=True)
        task=asyncio.create_task(module.rerank(state,ctx,Registry(model),repository=Repository()))
        await asyncio.wait_for(started.wait(),1)
        if stop=='cancel':cancelled.set()
        with pytest.raises(CallCancelled if stop=='cancel' else RunDeadlineExceeded):
            await asyncio.wait_for(task,.5)
        assert closed.is_set() and not model.calls
    asyncio.run(run())
