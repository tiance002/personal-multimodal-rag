import json
import pytest
from scripts.weknora_port_p20_evidence import answer_units,validate_audit,structural_screen

TEXT='Ada was born in London. She wrote mathematical notes.'
S={'c1':dict(text=TEXT,chunk_id='ch',document_id='doc',kb_id='kb',source_version='v1')}
U=answer_units('Ada was born in London.')
def body(verdict='SUPPORTED',quote=TEXT,handle='c1'):
    return {'units':[{'unit_id':'u1','verdict':verdict,'bindings':[{'handle':handle,'quote':quote}],'reason':'test'}]}
def check(b,**kw):return validate_audit(json.dumps(b),U,S,finish_reason=kw.get('finish','stop'),original_citations=kw.get('refs',[{'id':'ch'}]))

def test_exact_provenance_not_semantic_certificate():
    out=check(body());assert out['experimental_candidate_retained']
    assert not out['ready_for_automatic_publish'] and out['units'][0]['bindings'][0]['start']==0
    assert out['citations_added']==0 and not out['answer_modified']

@pytest.mark.parametrize('change',[lambda b:b['units'].append(b['units'][0]),lambda b:b['units'].clear(),lambda b:b['units'][0].update(unit_id='u2'),lambda b:b['units'][0].update(bindings=[]),lambda b:b['units'][0].update(verdict='MAYBE'),lambda b:b.update(answer='fixed')])
def test_incomplete_or_extra_schema_rejected(change):
    b=body();change(b);assert not check(b)['structurally_valid']

@pytest.mark.parametrize('quote,handle',[(TEXT,'foreign'),('Ada was born in Paris.','c1'),('Ada','c1')])
def test_fabricated_or_weak_binding_rejected(quote,handle):
    assert not check(body(quote=quote,handle=handle))['structurally_valid']

def test_missing_citation_not_repaired_by_judge():
    out=check(body(),refs=[]);assert out['model_support_clear'] and not out['experimental_candidate_retained']
    assert out['citations_added']==0

@pytest.mark.parametrize('verdict',['UNSUPPORTED','CONFLICT','UNKNOWN'])
def test_negative_verdict_holds_original(verdict):
    out=check(body(verdict));assert out['structurally_valid'] and not out['experimental_candidate_retained']

def test_truncation_and_duplicate_keys_fail_closed():
    assert not check(body(),finish='length')['structurally_valid']
    out=validate_audit('{"units":[],"units":[]}',U,S,finish_reason='stop',original_citations=[])
    assert not out['structurally_valid']

def test_wrong_claim_with_real_quote_is_not_certified():
    # Critical counterexample: exact quote can be irrelevant to the claim.
    wrong=answer_units('Ada was born in Paris.')
    out=validate_audit(json.dumps(body()),wrong,S,finish_reason='stop',original_citations=[{'id':'ch'}])
    assert out['structurally_valid'] and not out['ready_for_automatic_publish']
    assert out['semantic_support']=='MODEL_JUDGMENT_NOT_CERTIFICATION'

def test_units_cover_mixed_paragraph_and_preserve_original_spans():
    text='# Title\n\nFirst fact. Second inference.\n\n<ref id="c1"/>'
    units=answer_units(text);assert len(units)==1
    assert units[0]['text']=='First fact. Second inference.'
    assert text[units[0]['start']:units[0]['end']]==units[0]['raw']
    assert structural_screen(text,[])['missing_citations']
