"""Pinned complete-search image/seq counterexamples; no real model/network."""
import asyncio
import json
import re
from dataclasses import replace
from types import SimpleNamespace
import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope, CallCancelled, RunDeadlineExceeded
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.search import SearchParams, SearchTarget, RetrievalScopeError
from backend.weknora_port.retrieval.merge import collect_image_info
from backend.weknora_port.storage.hybrid import PostgresKnowledgeRepository
from backend.weknora_port.generation import Evidence
from tests.weknora_port.test_hybrid_storage import HybridConnection, HybridRetriever, kb
from tests.weknora_port.test_knowledge_storage import fixture_row, tenant
from tests.weknora_port.test_storage import FakeCursor

FIELDS={'url','original_url','start_pos','end_pos','caption','ocr_text'}
CTX=CallContext('seq-image',TrustedScope('owner',('kb',)))
TARGET=SearchTarget('kb')
STATE=SimpleNamespace(targets=(TARGET,))


def raw_image(url='images/a.png',**extra):
    return json.dumps([dict(url=url,caption='caption '+url,ocr_text='OCR '+url,**extra)],separators=(',',':'))


def source(id,*,parent='',kind='text',index=0,doc='doc',version='v1',raw='',enabled=True):
    row=fixture_row()
    row.update(id=id,knowledge_id=doc,parent_chunk_id=parent,chunk_type=kind,chunk_index=index,
               content='source '+id+' ![scan](images/a.png)',metadata={'source_version':version},
               image_info=raw,is_enabled=enabled)
    return row


class SearchConnection(HybridConnection):
    """Filter actual requested IDs; leave scope/enabled defense to production."""
    async def execute(self,query,params=()):
        if 'FROM chunks c' in query:
            self.calls.append((query,params))
            if 'c.parent_chunk_id IN' in query:
                self.child_calls+=1
                if self.child_calls==self.child_fail_at:raise RuntimeError('fixture query failure')
            match=re.search(r'c\.(parent_chunk_id|id) IN \(([^)]*)\)',query)
            assert match,query
            key=match[1];ids=params[3:3+match[2].count('%s')]
            return FakeCursor([r for r in self.rows if r.get(key) in ids])
        return await super().execute(query,params)


async def search(rows,ids,*,context=CTX,targets=(TARGET,),fail_at=0):
    conn=SearchConnection(rows,[kb(model='')])
    conn.child_fail_at=fail_at;conn.child_calls=0
    hits=[dict(chunk_id=id,score=20-i,content='index '+id,knowledge_id=next(r['knowledge_id'] for r in rows if r['id']==id),knowledge_base_id='kb') for i,id in enumerate(ids)]
    retriever=HybridRetriever(conn,keyword_rows=hits)
    await retriever.verify_identity()
    repo=PostgresKnowledgeRepository(retriever,tenant)
    result=await repo.hybrid_search(targets,SearchParams('scan',skip_context_enrichment=True),context)
    return result,conn,repo


@pytest.mark.parametrize('index',[0,1,9])
def test_complete_search_maps_chunk_index_to_seq(index):
    rows,_,_=asyncio.run(search([source('parent',index=index)],['parent']))
    assert rows[0].seq==rows[0].chunk_index==index
    assert Evidence.from_result(rows[0]).seq==index


def test_complete_search_fills_two_parents_without_touching_child_or_rank():
    child_raw=raw_image(page_number=4,source_type='owner',ocr_layout={'line':1},status='completed',binding='original')
    data=[source('p0'),source('p1',index=3),
          source('ocr',parent='p0',kind='image_ocr',raw=child_raw),
          source('text',parent='p1'),source('caption',parent='text',kind='image_caption',raw=raw_image('images/b.png'))]
    rows,conn,_=asyncio.run(search(data,['p0','p1','ocr']))
    assert [r.id for r in rows]==['p0','p1','ocr']
    images=[json.loads(r.image_info) for r in rows[:2]]
    assert images[0][0]['url']=='images/a.png' and images[1][0]['url']=='images/b.png'
    assert all(set(info)==FIELDS for group in images for info in group)
    assert rows[2].image_info==child_raw
    assert rows[0].content==data[0]['content'] and rows[0].matched_content=='index p0'
    assert rows[0].score>=rows[1].score>=rows[2].score
    assert len([q for q,_ in conn.calls if 'c.parent_chunk_id IN' in q])==2
    evidence=Evidence.from_result(rows[0])
    assert 'Image caption' in evidence.content and 'OCR images/a.png' in evidence.content
    assert evidence.source_content==data[0]['content']


@pytest.mark.parametrize('existing',['[]',' ','broken JSON',raw_image('existing')])
def test_nonempty_parent_image_info_is_preserved_literal(existing):
    data=[source('parent',raw=existing),source('child',parent='parent',kind='image_ocr',raw=raw_image())]
    rows,conn,_=asyncio.run(search(data,['parent']))
    assert rows[0].image_info==existing
    assert not any('c.parent_chunk_id IN' in q for q,_ in conn.calls)


@pytest.mark.parametrize('field,value',[('knowledge_id','other-doc'),('knowledge_base_id','other-kb'),('tenant_id',2),('metadata',{'source_version':'v2'})])
@pytest.mark.parametrize('level',[1,2])
def test_child_cannot_cross_parent_domain_even_when_document_allowed(field,value,level):
    data=[source('parent'),source('text',parent='parent'),source('child',parent='parent' if level==1 else 'text',kind='image_ocr',raw=raw_image())]
    data[-1][field]=value
    with pytest.raises((PermissionError,RetrievalScopeError)):
        asyncio.run(search(data,['parent']))


def test_disabled_missing_and_bad_children_do_not_invent_parent_info():
    data=[source('disabled',parent='parent',kind='image_ocr',raw=raw_image(),enabled=False),
          source('broken',parent='parent',kind='image_caption',raw='[{"url":"a"},{"url":false}]'),
          source('parent'),source('unrelated',parent='elsewhere',kind='image_ocr',raw=raw_image())]
    rows,_,_=asyncio.run(search(data,['parent']))
    assert rows[0].image_info==''


class Children:
    def __init__(self,rows,fail_at=0,error=None):self.rows=rows;self.calls=[];self.fail_at=fail_at;self.error=error
    async def get_chunk_children(self,ids,context):
        self.calls.append(tuple(ids))
        if len(self.calls)==self.fail_at:raise self.error or RuntimeError('fixture query failure')
        return [r for r in self.rows if r.parent_chunk_id in ids]


def result(id,parent='',kind='text',raw='',**kw):
    return SearchResult(id,'source '+id,.8,'doc','kb',parent_chunk_id=parent,chunk_type=kind,image_info=raw,**kw)


def test_second_batch_failure_retains_direct_images_and_typed_projection():
    direct=result('direct','parent','image_ocr',raw_image(page_number=3))
    repo=Children([direct,result('text','parent')],fail_at=2)
    out=asyncio.run(collect_image_info(['parent'],STATE,CTX,repo))
    assert set(json.loads(out['parent'])[0])==FIELDS
    assert len(repo.calls)==2
    assert direct.image_info==raw_image(page_number=3)


@pytest.mark.parametrize('error',[PermissionError('denied'),RetrievalScopeError(),CallCancelled(),RunDeadlineExceeded()])
def test_collector_security_and_cancellation_failures_propagate(error):
    with pytest.raises(type(error)):
        asyncio.run(collect_image_info(['parent'],STATE,CTX,Children([],1,error)))


def test_empty_source_and_ordinary_query_failure_leave_empty():
    assert asyncio.run(collect_image_info([],STATE,CTX,Children([])))=={}
    assert asyncio.run(collect_image_info(['parent'],STATE,CTX,Children([],1)))=={}


def test_fixed_go_collector_and_enrich_same_fixtures_through_complete_search():
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures/image_completion_go_oracle.json').read_text(encoding='utf-8'))
    assert fixture['exit_code']==0
    def normalized(raw):
        try:
            value=json.loads(raw)
        except (ValueError,TypeError):return raw
        if not isinstance(value,list):return value
        return sorted(value,key=lambda row:json.dumps(row,sort_keys=True))
    for case,expected in zip(fixture['cases'],fixture['rows']):
        data=[source(r['id'],raw=r['image_info']) for r in case['results']]
        # Search results are unique IDs; duplicate result fill semantics are also
        # exercised by the extracted Go enrich and the source collector dedup.
        data=list({r['id']:r for r in reversed(data)}.values())
        data.extend(source(r['id'],parent=r['parent_chunk_id'],kind=r['chunk_type'],raw=r['image_info'],enabled=r['is_enabled']) for r in case['children'])
        ids=list(dict.fromkeys(r['id'] for r in case['results']))
        rows,conn,_=asyncio.run(search(data,ids,fail_at=case['fail_at']))
        wanted={r['id']:r['image_info'] for r in reversed(expected['results'])}
        assert {r.id:normalized(r.image_info) for r in rows}=={k:normalized(v) for k,v in wanted.items()},case['name']
        calls=[]
        for query,params in conn.calls:
            if 'c.parent_chunk_id IN' in query:
                count=re.search(r'c.parent_chunk_id IN \(([^)]*)\)',query)[1].count('%s')
                calls.append(list(params[3:3+count]))
        assert calls==expected['calls'],case['name']


def test_summary_child_without_source_version_is_ignored_by_image_collector():
    data=[source('parent'),source('ocr',parent='parent',kind='image_ocr',raw=raw_image()),
          source('summary',parent='parent',kind='summary',version=None)]
    data[-1]['metadata']={'origin':'model_generated_summary','upstream_parent_link':True}
    rows,_,_=asyncio.run(search(data,['parent']))
    assert json.loads(rows[0].image_info)[0]['url']=='images/a.png'


def test_legacy_both_versions_absent_remains_supported():
    data=[source('parent',version=None),source('ocr',parent='parent',kind='image_ocr',version=None,raw=raw_image())]
    for row in data:row['metadata']={}
    rows,_,_=asyncio.run(search(data,['parent']))
    assert json.loads(rows[0].image_info)[0]['url']=='images/a.png'


def test_stale_index_hit_without_source_is_not_returned_or_completed():
    async def exercise():
        conn=SearchConnection([source('parent')],[kb(model='')]);conn.child_calls=0;conn.child_fail_at=0
        hits=[dict(chunk_id=id,score=score,content='index '+id,knowledge_id='doc',knowledge_base_id='kb') for id,score in [('missing',20),('parent',10)]]
        retriever=HybridRetriever(conn,keyword_rows=hits);await retriever.verify_identity()
        repo=PostgresKnowledgeRepository(retriever,tenant)
        rows=await repo.hybrid_search((TARGET,),SearchParams('scan',skip_context_enrichment=True),CTX)
        assert [row.id for row in rows]==['parent']
        assert all('missing' not in params[3:] for query,params in conn.calls if 'c.parent_chunk_id IN' in query)
    asyncio.run(exercise())
