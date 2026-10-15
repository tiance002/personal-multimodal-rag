"""Exact source-coordinate/metadata counterexamples through the actual generation coordinator."""
from dataclasses import replace
import pytest
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.merge import sequential
from backend.weknora_port.generation import Evidence
from backend.weknora_port.retrieval.context import FinalizedContext
from tests.weknora_port.test_generation import setup,request,event,run,CTX


def result(content='\u4e2d\U0001f600A',start=5,end=8):
    return SearchResult('chunk-current',content,.9,'doc-current','kb',start_at=start,end_at=end,
        match_type='embedding',knowledge_title='Source',knowledge_description='\u4e2d\u6587 description',
        matched_content='original matched passage',parent_chunk_id='parent',image_info='[]',
        metadata={'origin':'fixture'},chunk_metadata={'questions':['question']},
        knowledge_custom_metadata='custom',seq=7,knowledge_channel='api')


def generate(row):
    evidence=Evidence.from_result(row)
    co,*parts=setup([event('answer <ref id="c1"/>',done=True,finish='stop',final=True)])
    req=request(mode='CHEAP_ONLY',evidence=(evidence,),finalized=FinalizedContext(row.content,row.content,(row.id,)))
    output=run(co.run(req,CTX));commit=parts[3].calls[0]
    assert output.references==tuple(commit.references)
    return evidence,commit.references[0]


@pytest.mark.parametrize('row',[result(start=0,end=3),result(),result(start=0,end=0),
    replace(result('\u77ed',5,88),content_revision=1,content_rewritten=True)])
def test_positions_and_metadata_survive_real_final_commit(row):
    evidence,saved=generate(row)
    assert (saved['start_at'],saved['end_at'])==(row.start_at,row.end_at)
    assert (evidence.start_at,evidence.end_at)==(row.start_at,row.end_at)
    for field in ('knowledge_description','matched_content','metadata','chunk_metadata','parent_chunk_id',
                  'image_info','knowledge_custom_metadata','knowledge_channel','seq'):
        assert saved[field]==getattr(row,field)
    assert saved['match_type']==0
    assert saved['content']==row.content
    row.metadata['later']='mutation';assert 'later' not in saved['metadata']


def test_merged_non_ascii_positions_and_children_are_not_byte_or_utf16_lengths():
    text='\u7532\U0001f600\u4e59\u4e19\u4e01'
    left=result(text[:4],0,4);right=replace(result(text[2:],2,5),id='next',chunk_index=1)
    merged=sequential([left,right]);assert len(merged)==1
    assert merged[0].content==text and len(text)==5 and len(text.encode('utf-8'))!=5
    evidence,saved=generate(merged[0])
    assert (saved['start_at'],saved['end_at'],saved['content'])==(0,5,text)
    assert saved['sub_chunk_id']==['next'] and evidence.sub_chunk_ids==('next',)


@pytest.mark.parametrize('bad',[None,False,'0',0.0])
def test_missing_or_noninteger_offset_is_not_replaced_by_zero(bad):
    with pytest.raises(ValueError,match='SOURCE_OFFSETS'):
        Evidence.from_result(replace(result(),start_at=bad))


@pytest.mark.parametrize('kind,expected',[('',0),('embedding',0),('keywords',1),('nearby',2),('history',3),
    ('parent',4),('relation',5),('graph',6),('web_search',7),('direct_load',8),('data_analysis',9),(3,3)])
def test_reference_match_type_uses_pinned_source_enum(kind,expected):
    assert Evidence.from_result(replace(result(),match_type=kind)).match_type==expected
