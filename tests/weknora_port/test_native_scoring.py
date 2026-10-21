from eval_center.public_data import PublicCase
from eval_center.weknora_scoring import answer_diagnostics,score_native_qa,score_native_omni,annotation_diagnostics,project_native_documents
import pytest

def test_native_answer_formulas_do_not_import_legacy_executor():
    assert answer_diagnostics('The blue-widget.',('blue widget',),'hotpotqa')['metrics']['hotpot_answer_em']==0
    assert answer_diagnostics('The blue widget.',('blue widget',),'hotpotqa')['metrics']['hotpot_answer_em']==1
    assert answer_diagnostics('yes',('yes indeed',),'hotpotqa')['metrics']['hotpot_answer_token_f1']==0
    assert answer_diagnostics('答案：17',('17',),'mmlongbench-doc')['official_judge']=='NOT_RUN'
    assert answer_diagnostics('anything',(),'scifact')['metrics'] is None

def test_mock_native_answer_cannot_be_a_quality_score():
    case=PublicCase('q','question',{'doc':1},'development',('answer',))
    result=score_native_qa(case,dataset='hotpotqa',prediction='answer',ranked_source_ids=['doc'],citations=[True],execution_kind='ISOLATED_MOCK')
    assert result['quality_metrics'] is None and result['status'].startswith('NOT_RUN')

def test_mml_page_base_unknown_and_missing_not_fabricated():
    case=PublicCase('q','question',{'doc':1},'development',evaluation_metadata={'evidence_pages':[10],'page_index_convention':'UNKNOWN'})
    assert annotation_diagnostics(case,[{'document_id':'doc','pages_one_based':[10]}])['metrics'] is None
    empty=PublicCase('q','question',{'doc':1},'development',evaluation_metadata={'evidence_pages':[]})
    assert annotation_diagnostics(empty,[])['status']=='N/A_NO_NATIVE_PAGE_REFERENCES'

def test_omni_parsing_can_score_without_qa_overlay():
    page={'page_id':'page','image_sha256':'a'*64}
    block={'poly':[0,0,10,0,10,10,0,10],'category_type':'text_block','text':'genuine text','order':0}
    prediction={**page,'implementation':'weknora-python-port','blocks':[block]}
    result=score_native_omni(page,{'layout_dets':[block]},prediction,execution_kind='NATIVE_LOCAL_PARSER')
    assert result['metrics']['layout_f1']==1 and result['independent_qa'].startswith('NOT_RUN')
    assert result['official_omnidocbench_score']=='NOT_RUN'
    assert score_native_omni(page,{'layout_dets':[block]},prediction,execution_kind='ISOLATED_MOCK')['metrics'] is None

def test_native_chunk_to_original_document_mapping_is_explicit():
    assert project_native_documents([('c','kb','native'),('c2','kb','native')],{'native':'original'})==['original']
    with pytest.raises(ValueError):project_native_documents([('c','kb','native')],{})
