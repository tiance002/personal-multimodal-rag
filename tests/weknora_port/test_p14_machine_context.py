from copy import deepcopy
import inspect
import pytest
from scripts.weknora_port_p14_machine_context import prepare_sources,source_units,deduplicate,build_machine_candidate


def row(index,text,*,start=None):
    start=index*50 if start is None else start
    return {'id':'c'+str(index),'knowledge_id':'doc','chunk_index':index,'start_at':start,'end_at':start+len(text),'content':text,'chunk_type':'text','is_enabled':True,'deleted_at':None,'image_info':'','metadata':{'source_version':'v1','mineru_source':{'engine':'mineru-basic'},'source_locators':[]}}


def test_constructor_has_no_query_gold_or_title_selection_parameter():
    assert set(inspect.signature(build_machine_candidate).parameters)=={'source','anchor_id','original_wire','cap','step'}


def test_same_document_version_only_original_enabled_machine_text():
    good=row(0,'machine')
    bad=[]
    for key,value in [('knowledge_id','otherdoc'),('chunk_type','summary'),('is_enabled',False),('deleted_at','deleted'),('image_info','hasimage')]:
        r=deepcopy(good);r[key]=value;bad.append(r)
    r=deepcopy(good);r['metadata']['source_version']='v2';bad.append(r)
    r=deepcopy(good);r['metadata']['mineru_source']=None;bad.append(r)
    r=deepcopy(good);r['metadata']['origin']='generated';bad.append(r)
    assert [r['id'] for r in prepare_sources([good,*bad],'doc','v1')['rows']]==['c0']


def test_source_overlap_deduplicated_by_real_unicode_coordinate():
    rows=[row(0,'abcde',start=0),row(1,'deXYZ',start=3)];s=prepare_sources(rows,'doc','v1')
    assert ''.join(ch for _,ch in source_units(s,s['rows']))=='abcdeXYZ'


def test_conflicting_overlap_rejected():
    with pytest.raises(AssertionError,match='OVERLAP_CONFLICT'):prepare_sources([row(0,'abcde',start=0),row(1,'XXXYZ',start=3)],'doc','v1')


def test_normalized_duplicate_paragraph_and_line_never_readded_to_anchor():
    kept,dropped,*_=deduplicate(list(enumerate('Alpha\nbeta\n')),'Alpha beta')
    assert not kept and dropped
    kept,dropped,*_=deduplicate(list(enumerate('repeat\nnew\nrepeat\n')),'repeat')
    assert ''.join(ch for _,ch in kept)=='new\n' and len(dropped)==2


def test_source_only_near_length_control_is_bounded_matched_and_located():
    rows=[row(i,('text '+str(i)+' machine source values\n')) for i in range(10)];s=prepare_sources(rows,'doc','v1');a=rows[2];r=build_machine_candidate(s,a['id'],a['content'],cap=70)
    assert r['near_chars']==r['length_chars']<=70
    assert r['anchor_position_near']==r['anchor_position_length']
    assert a['content'] in r['near_text'] and a['content'] in r['length_text']
    assert r['near_neighbor_chunk_indices']==[1,3]
    assert all(abs(i-2)>1 for i in r['nonadjacent_sample_chunk_indices'])
    for key in ['near_mapping','length_mapping']:
        for m in r[key]:
            if m['kind']=='SOURCE_SUBSTRING_IDENTITY':assert m['raw_text']==''.join(s['chars'][i] for i in range(m['source_start'],m['source_end']))


def test_anchor_larger_than_cap_rejected_without_truncation():
    rows=[row(i,'real source '+str(i)) for i in range(10)];s=prepare_sources(rows,'doc','v1')
    with pytest.raises(AssertionError):build_machine_candidate(s,rows[2]['id'],rows[2]['content'],cap=2)
