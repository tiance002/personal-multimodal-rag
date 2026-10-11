"""SIMULATED offline contracts; no client, DB, parser/model execution."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from eval_center.scoring_mappers import (hotpot_sources, HotpotAnnotationMapper, mml_page_metadata,
    mml_annotation_for_chunk, omni_prediction_from_normalized, computed_numeric_scores)
from eval_center.public_paired_reporting import score_retrieval_annotations
from eval_center.omni_contract import score_page_parsing
from eval_center.public_langfuse import _validate_payload, PublicTelemetryError


def row():
    return {'context':{'title':['T','Other'], 'sentences':[['same','same'],['same']]},
            'supporting_facts':{'title':['T'], 'sent_id':[1]}}


def test_hotpot_duplicate_sentences_use_offsets_and_formal_version():
    sources=hotpot_sources([row()]); source_id=next(iter(sources))
    mapper=HotpotAnnotationMapper(sources,[{'source_document_id':source_id,'document_id':'SIMULATED-doc',
        'version_id':'SIMULATED-v','normalized_text':'same\nsame'}])
    chunk=SimpleNamespace(document_id='SIMULATED-doc',version_id='SIMULATED-v',content='same',locator={'start':5,'end':9})
    a=mapper(chunk)
    assert a['sentence_indices_zero_based']==[1] and a['document_id']==source_id
    case=SimpleNamespace(evaluation_metadata={'supporting_facts':[{'document_id':source_id,'sentence_index_zero_based':1}]})
    assert score_retrieval_annotations(case,[a])['metrics']['supporting_fact_recall']==1
    chunk.locator={'start':0,'end':4}
    assert score_retrieval_annotations(case,[mapper(chunk)])['metrics']['supporting_fact_recall']==0
    chunk.version_id='stale'
    with pytest.raises(KeyError):mapper(chunk)


def test_hotpot_never_guesses_normalization_or_partial_sentence():
    sources=hotpot_sources([row()]);sid=next(iter(sources))
    binding={'source_document_id':sid,'document_id':'d','version_id':'v','normalized_text':'samesame'}
    with pytest.raises(ValueError,match='offsets_not_proven'):HotpotAnnotationMapper(sources,[binding])
    binding['normalized_text']='same\nsame';mapper=HotpotAnnotationMapper(sources,[binding])
    c=SimpleNamespace(document_id='d',version_id='v',content='ame',locator={'start':1,'end':4})
    assert mapper(c)['sentence_indices_zero_based']==[]
    c.content='wrong'
    with pytest.raises(ValueError,match='readback_mismatch'):mapper(c)


@pytest.mark.parametrize('base,pages',[(0,[0,1]),(1,[1,2])])
def test_mml_proven_basis_wrong_document_and_duplicates(base,pages):
    original={'evidence_pages':pages,'page_index_convention':'UNKNOWN'}
    proof={'gold_page_index_base':base,'evidence_sha256':'a'*64,'evidence_pointer':'SIMULATED-contract#/base'}
    meta=mml_page_metadata(original,convention_contract=proof)
    assert original['page_index_convention']=='UNKNOWN'
    case=SimpleNamespace(evaluation_metadata=meta,qrels={'source.pdf':1})
    annotations=[{'document_id':'source.pdf','pages_one_based':[1,1]}, {'document_id':'other.pdf','pages_one_based':[2]}]
    result=score_retrieval_annotations(case,annotations)
    assert result['metrics']['evidence_page_recall']==.5
    assert result['metrics']['evidence_page_precision']==.5
    assert result['counts']=={'gold_pages':2,'retrieved_pages':2,'matched_pages':1}


def test_mml_unknown_and_no_gold_never_impute_zero():
    c=SimpleNamespace(evaluation_metadata={'evidence_pages':[10],'page_index_convention':'ONE_BASED'},qrels={'s':1})
    assert score_retrieval_annotations(c,[])['metrics'] is None
    c.evaluation_metadata['evidence_pages']=[]
    assert score_retrieval_annotations(c,[])['status']=='NOT_RUN_NO_OFFICIAL_PAGE_GOLD'
    a=mml_annotation_for_chunk(SimpleNamespace(locator={'page':1}),source_document_id='s')
    assert a=={'document_id':'s','pages_one_based':[1]}
    with pytest.raises(ValueError):mml_annotation_for_chunk(SimpleNamespace(locator={'page':0}),source_document_id='s')


def test_mml_not_run_annotations_differ_from_observed_empty_retrieval():
    proof={'gold_page_index_base':1,'evidence_sha256':'a'*64,'evidence_pointer':'SIMULATED-contract#/base'}
    meta=mml_page_metadata({'evidence_pages':[1]},convention_contract=proof)
    case=SimpleNamespace(evaluation_metadata=meta,qrels={'s':1})
    assert score_retrieval_annotations(case,None)=={
        'status':'NOT_RUN_SOURCE_ANNOTATION_MAPPER_REQUIRED','metrics':None}
    scored=score_retrieval_annotations(case,[])
    assert scored['status']=='SCORED_RETRIEVAL_ANNOTATION_DIAGNOSTIC'
    assert scored['metrics']=={'evidence_page_recall':0.,'evidence_page_precision':0.,'evidence_page_f1':0.}
    assert scored['counts']=={'gold_pages':1,'retrieved_pages':0,'matched_pages':0}


def test_omni_export_actual_fields_without_order_inference():
    page={'page_id':'SIMULATED-p','image_sha256':'b'*64,'width':100,'height':100}
    normalized={'source_locators':[{'kind':'image','bbox':[1,2,20,30],'quote':'abc','raw_evidence':{}}]}
    copy=deepcopy(normalized)
    export=omni_prediction_from_normalized(page,normalized,source_image_sha256='b'*64)
    block=export['prediction']['blocks'][0]
    assert 'order' not in block and block['poly']==[1,2,20,2,20,30,1,30]
    result=score_page_parsing(page,{'layout_dets':[dict(block,order=1)]},export['prediction'])
    assert result['metrics']['layout_f1']==1 and result['metrics']['reading_order_pairwise_accuracy'] is None
    assert normalized==copy
    normalized['source_locators'][0]['bbox']=None
    export=omni_prediction_from_normalized(page,normalized,source_image_sha256='b'*64)
    assert export['prediction'] is None and export['counts']['unavailable_locators']==1
    with pytest.raises(ValueError):omni_prediction_from_normalized(page,normalized,source_image_sha256='c'*64)


def test_numeric_mapping_origin_and_missing_values():
    scores=computed_numeric_scores(answer={'hotpot_answer_em':1,'character_f1':None},
        retrieval={'recall_at_k':0},parsing={'layout_f1':None,'text_normalized_edit_distance':.2})
    assert scores=={'official_hotpot_answer_em':1.,'project_retrieval_recall_at_k':0.,
                   'project_omni_text_normalized_edit_distance':.2}
    _,accepted=_validate_payload(dict(scores,dataset='hotpotqa',qid='q',arm='DYNAMIC'))
    assert accepted==scores
    with pytest.raises(ValueError):computed_numeric_scores(answer={'hotpot_answer_em':float('nan')})
    with pytest.raises(PublicTelemetryError):_validate_payload({'dataset':'x','qid':'q','arm':'DYNAMIC','question':'secret'})


def test_new_scores_use_existing_client_injection_and_stable_ids():
    from eval_center.tests.test_public_langfuse import _FakeClient
    from eval_center.public_langfuse import write_and_readback_public_event
    scores=computed_numeric_scores(answer={'hotpot_answer_em':1},annotations={'supporting_fact_recall':0})
    first,second=_FakeClient(),_FakeClient()
    for client in (first,second):
        receipt=write_and_readback_public_event(client,run_id='SIMULATED-run',qid='q',arm='DYNAMIC',
            metadata={'dataset':'hotpotqa'},scores=scores)
        assert receipt['scores_count']==2 and client.flush_count==1
    assert first.created_scores==second.created_scores
