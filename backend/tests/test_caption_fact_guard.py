"""SIMULATED evidence; real QualityGate, validator and citation serialization."""
from dataclasses import replace
import hashlib

import pytest

from backend.app.application.answer_validation import AnswerValidator
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.quality import QualityGate
from backend.app.application.query_router import EvidencePlan, EvidenceTarget
from backend.app.domain.chunking import CAPTION_EVIDENCE_PREFIX
from backend.app.domain.models import ChunkRecord, EvidenceSnapshot
from backend.tests.test_structured_evidence import record


LENGTH = EvidenceTarget('\u8bbe\u5907\u7532', '\u957f\u5ea6')
BUDGET = EvidenceTarget('\u7532', '\u9884\u7b97')
MONEY = EvidenceTarget('\u5317\u57ce\u95e8\u5e97', '\u6536\u5165', '2025-02', '\u5143')


def plan(target):
    return EvidencePlan('SIMULATED numeric question', targets=(target,), kind='single')


def prose(text, *, caption=False, identifier='source', status='complete'):
    text = (CAPTION_EVIDENCE_PREFIX if caption else '') + text
    locator = {'kind':'pdf' if caption else 'text', 'quote':text, 'parse_status':status}
    if caption:
        locator.update({'asset_id':'SIMULATED-caption', 'raw_evidence':{
            'schema_version':'local-caption/v1', 'evidence_kind':'model_generated_caption',
            'validation_status':'UNVERIFIED', 'source_image_sha256':'1'*64,
            'input_image_sha256':'1'*64, 'model_requested':'SIMULATED-requested',
            'model_reported':'SIMULATED-observed', 'model_digest':'UNKNOWN',
            'prompt_version':'local-figure-caption/v1', 'page':1, 'bbox':None,
            'coordinate_basis':'UNKNOWN'}})
    return ChunkRecord(identifier, 'kb', 'doc', 'v', text, locator,
        content_sha256=hashlib.sha256(text.encode()).hexdigest())


def frozen(*chunks):
    service = CitationService(InMemoryCitationStore())
    for chunk in chunks:
        service.freeze('run', chunk)
    # Exercise the actual snapshot JSON shape crossing storage/API boundaries.
    snapshots = tuple(EvidenceSnapshot.model_validate_json(s.model_dump_json())
        for s in service.snapshots.values())
    for snapshot in snapshots:
        resolved = service.resolve('run', snapshot.label)
        assert resolved.quote == snapshot.quote and resolved.locator == snapshot.locator
    return snapshots


@pytest.mark.parametrize('target,text,answer',[
    (LENGTH, '\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73', '\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73 [E1]'),
    (BUDGET, '\u7532\u65b9\u6848\u9884\u7b97\u4e3a99\u5143', '\u7532\u65b9\u6848\u9884\u7b97\u4e3a99\u5143 [E1]'),
    (MONEY, '\u5317\u57ce\u95e8\u5e972025-02\u6536\u5165\u4e3a1230\u5143', '\u5317\u57ce\u95e8\u5e972025-02\u6536\u5165\u4e3a1230\u5143 [E1]'),
],ids=['quantity','early-budget','monthly-money'])
def test_caption_alone_cannot_support_exact_fact_in_gate_or_validator(target,text,answer):
    source = prose(text, caption=True)
    decision = QualityGate().evaluate_chunks([source], plan(target))
    validation = AnswerValidator().validate(answer, frozen(source), plan(target))
    assert not decision.accepted
    assert validation == 'UNSUPPORTED_ANSWER'


def test_early_budget_validation_also_guards_opaque_plan():
    from backend.app.application.answer_validation import _PLAN_AMOUNT
    text = '\u7532\u65b9\u6848\u9884\u7b97\u4e3a99\u5143'
    assert len(list(_PLAN_AMOUNT.finditer(text))) == 1
    source = prose(text, caption=True)
    opaque = EvidencePlan('SIMULATED opaque question')
    assert QualityGate().evaluate_chunks([source], opaque).accepted
    assert AnswerValidator().validate(text+' [E1]', frozen(source), opaque) == 'UNSUPPORTED_ANSWER'
    original = prose(text, identifier='original')
    assert AnswerValidator().validate(text+' [E1]', frozen(original), opaque) is None


def test_opaque_numeric_fallback_requires_independent_original_numbers():
    text = '\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73'
    caption = prose(text, caption=True, identifier='caption')
    unrelated = prose('\u8bbe\u5907\u7532\u4f7f\u7528\u91d1\u5c5e\u5916\u58f3', identifier='unrelated')
    original = prose(text, identifier='original')
    opaque = EvidencePlan('SIMULATED opaque question')
    assert AnswerValidator().validate(text+' [E1]', frozen(caption), opaque) == 'UNSUPPORTED_ANSWER'
    assert AnswerValidator().validate(text+' [E1][E2]', frozen(caption, unrelated), opaque) == 'UNSUPPORTED_ANSWER'
    assert AnswerValidator().validate(text+' [E1][E2]', frozen(caption, original), opaque) is None


@pytest.mark.parametrize('claimed_status',['UNVERIFIED','VERIFIED','UNKNOWN',None])
def test_caption_cannot_self_promote_verification_status(claimed_status):
    source = prose('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73', caption=True)
    locator = {**source.locator, 'raw_evidence':{**source.locator['raw_evidence'], 'validation_status':claimed_status}}
    source = replace(source, locator=locator)
    assert not QualityGate().evaluate_chunks([source], plan(LENGTH)).accepted
    assert AnswerValidator().validate('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73 [E1]', frozen(source), plan(LENGTH)) == 'UNSUPPORTED_ANSWER'


@pytest.mark.parametrize('marker',['evidence_kind','schema_version','asset_type','content_type'])
def test_each_existing_caption_source_marker_survives_snapshot_guard(marker):
    source = prose('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73')
    values = {'evidence_kind':'model_generated_caption','schema_version':'local-caption/v1',
        'asset_type':'caption','content_type':'image_caption'}
    locator = dict(source.locator)
    if marker in {'evidence_kind','schema_version'}: locator['raw_evidence'] = {marker:values[marker]}
    else: locator[marker] = values[marker]
    source = replace(source, locator=locator)
    assert not QualityGate().evaluate_chunks([source], plan(LENGTH)).accepted
    assert AnswerValidator().validate('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73 [E1]', frozen(source), plan(LENGTH)) == 'UNSUPPORTED_ANSWER'


def test_forged_table_metadata_cannot_turn_caption_into_original_row_fact():
    source = record(subject=MONEY.subject, identifier='caption-table')
    assert QualityGate().evaluate_chunks([source], plan(MONEY)).accepted
    assert AnswerValidator().validate('\u5317\u57ce\u95e8\u5e972025-02\u6536\u5165\u4e3a1230\u5143 [E1]', frozen(source), plan(MONEY)) is None
    provenance = prose('SIMULATED', caption=True).locator['raw_evidence']
    source = replace(source, locator={**source.locator, 'raw_evidence':provenance})
    assert not QualityGate().evaluate_chunks([source], plan(MONEY)).accepted
    assert AnswerValidator().validate('\u5317\u57ce\u95e8\u5e972025-02\u6536\u5165\u4e3a1230\u5143 [E1]', frozen(source), plan(MONEY)) == 'UNSUPPORTED_ANSWER'


@pytest.mark.parametrize('caption_value',['80','999'])
def test_independent_original_support_ignores_caption_conflict_but_requires_original_citation(caption_value):
    original = prose('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73', identifier='original')
    caption = prose(f'\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a{caption_value}\u5398\u7c73', caption=True, identifier='caption')
    assert QualityGate().evaluate_chunks([caption, original], plan(LENGTH)).accepted
    assert QualityGate.supporting_chunks([caption, original], LENGTH) == [original]
    snapshots = frozen(caption, original)
    assert AnswerValidator().validate('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73 [E2]', snapshots, plan(LENGTH)) is None
    assert AnswerValidator().validate(f'\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a{caption_value}\u5398\u7c73 [E1]', snapshots, plan(LENGTH)) == 'UNSUPPORTED_ANSWER'


@pytest.mark.parametrize('status',['complete','partial'])
def test_original_table_status_and_support_stay_independent_of_caption(status):
    original = record(subject=MONEY.subject, identifier='original-table')
    original = replace(original, locator={**original.locator, 'parse_status':status})
    assert QualityGate().evaluate_chunks([original], plan(MONEY)).accepted is (status == 'complete')
    caption = prose('\u5317\u57ce\u95e8\u5e972025-02\u6536\u5165\u4e3a999\u5143', caption=True, identifier='caption')
    assert QualityGate().evaluate_chunks([caption, original], plan(MONEY)).accepted is (status == 'complete')
    result = AnswerValidator().validate('\u5317\u57ce\u95e8\u5e972025-02\u6536\u5165\u4e3a1230\u5143 [E2]', frozen(caption, original), plan(MONEY))
    assert result == (None if status == 'complete' else 'UNSUPPORTED_ANSWER')


def test_independent_original_support_does_not_relax_citation_requirements():
    original = prose('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73')
    snapshots = frozen(original)
    assert QualityGate().evaluate_chunks([original], plan(LENGTH)).accepted
    assert AnswerValidator().validate('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73', snapshots, plan(LENGTH)) == 'UNSUPPORTED_ANSWER'
    assert AnswerValidator().validate('\u8bbe\u5907\u7532\u957f\u5ea6\u4e3a80\u5398\u7c73 [E9]', snapshots, plan(LENGTH)) == 'INVALID_CITATION'


@pytest.mark.parametrize('claim,other',[
    ('\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73', '\u5e93\u623f\u6e29\u5ea680\u6444\u6c0f\u5ea6'),
    ('\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73', '\u8bbe\u5907\u7532\u957f\u5ea680\u7c73'),
    ('\u5317\u57ce\u95e8\u5e972025-02\u6536\u516580\u5143', '\u5317\u57ce\u95e8\u5e972025-03\u6536\u516580\u5143'),
    ('\u5317\u57ce\u95e8\u5e972025-02\u6536\u516580\u5143', '\u7edf\u8ba1\u5305\u542b2025-02\u4e0e2025-03\uff0c\u5317\u57ce\u95e8\u5e972025-03\u6536\u516580\u5143'),
    ('\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73', '\u8bbe\u5907\u7532\u5bbd\u5ea680\u5398\u7c73'),
], ids=['different-subject','different-unit','different-month','month-number-pool','different-attribute'])
def test_opaque_same_numbers_in_another_fact_are_not_independent_support(claim,other):
    caption = prose(claim, caption=True, identifier='caption')
    original = prose(other, identifier='original')
    opaque = EvidencePlan('SIMULATED opaque question')
    assert QualityGate().evaluate_chunks([caption, original], opaque).accepted
    assert AnswerValidator().validate(claim+'[E1][E2]', frozen(caption, original), opaque) == 'UNSUPPORTED_ANSWER'


@pytest.mark.parametrize('placement',['shared-trailing','per-fact'])
def test_two_original_facts_can_jointly_support_one_sentence_with_qualitative_caption(placement):
    left = '\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73'
    right = '\u8bbe\u5907\u4e59\u5bbd\u5ea620\u5398\u7c73'
    qualitative = '\u56fe\u793a\u4e3a\u8bbe\u5907\u793a\u610f\u5e03\u5c40'
    originals = [prose(left, identifier='left'), prose(right, identifier='right')]
    caption = prose(qualitative, caption=True, identifier='qualitative-caption')
    answer = (left+'\uff0c'+right+'[E1][E2][E3]' if placement == 'shared-trailing'
        else left+'[E1]\uff0c'+right+'[E2]\uff0c'+qualitative+'[E3]')
    opaque = EvidencePlan('SIMULATED opaque question')
    assert QualityGate().evaluate_chunks([*originals, caption], opaque).accepted
    assert AnswerValidator().validate(answer, frozen(*originals, caption), opaque) is None


def test_opaque_fact_cannot_borrow_a_citation_attached_to_another_fact():
    left = '\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73'
    right = '\u8bbe\u5907\u4e59\u5bbd\u5ea620\u5398\u7c73'
    caption = prose(left, caption=True, identifier='caption')
    original = prose(left+'\uff0c'+right, identifier='original')
    assert AnswerValidator().validate(left+'[E1]\uff0c'+right+'[E2]', frozen(caption, original),
        EvidencePlan('SIMULATED opaque question')) == 'UNSUPPORTED_ANSWER'


def test_opaque_caption_qualitative_use_does_not_require_numeric_original():
    text = '\u56fe\u793a\u4e3a\u8bbe\u5907\u793a\u610f\u5e03\u5c40'
    caption = prose(text, caption=True)
    assert AnswerValidator().validate(text+'[E1]', frozen(caption), EvidencePlan('SIMULATED opaque question')) is None


@pytest.mark.parametrize('status',['partial','complete'])
def test_opaque_same_fact_requires_complete_original(status):
    text = '\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73'
    caption = prose(text, caption=True, identifier='caption')
    original = prose(text, identifier='original', status=status)
    result = AnswerValidator().validate(text+'[E1][E2]', frozen(caption, original), EvidencePlan('SIMULATED opaque question'))
    assert result == (None if status == 'complete' else 'UNSUPPORTED_ANSWER')


def test_targeted_native_row_still_supports_amount_with_qualitative_caption_citation():
    original = record(subject=MONEY.subject, identifier='original-table')
    caption = prose('\u56fe\u793a\u4e3a\u8d22\u52a1\u793a\u610f\u5e03\u5c40', caption=True, identifier='caption')
    assert QualityGate().evaluate_chunks([caption, original], plan(MONEY)).accepted
    assert AnswerValidator().validate('\u5317\u57ce\u95e8\u5e972025-02\u6536\u5165\u4e3a1230\u5143 [E2][E1]',
        frozen(caption, original), plan(MONEY)) is None


@pytest.mark.parametrize('claim,other',[
    ('\u5317\u57ce\u95e8\u5e97\u6536\u51651,230\u5143', '\u5317\u57ce\u95e8\u5e97\u6536\u51651,000\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u6536\u51652,230\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u6536\u5165-1,230\u5143', '\u5317\u57ce\u95e8\u5e97\u6536\u5165-1,000\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u6536\u5165-2,230\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u6536\u5165+1,230\u5143', '\u5317\u57ce\u95e8\u5e97\u6536\u5165+1,000\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u6536\u5165+2,230\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u6536\u51651,230.50\u5143', '\u5317\u57ce\u95e8\u5e97\u6536\u51651,000.75\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u6536\u51652,230.50\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u8303\u56f480,100\u5143', '\u5317\u57ce\u95e8\u5e97\u8303\u56f480,90\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u8303\u56f490,100\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u6536\u51651\uff0c230\u5143', '\u5317\u57ce\u95e8\u5e97\u6536\u51651\uff0c000\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u6536\u51652\uff0c230\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u6536\u51651, 230\u5143', '\u5317\u57ce\u95e8\u5e97\u6536\u51651, 000\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u6536\u51652, 230\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u8303\u56f480, -100\u5143', '\u5317\u57ce\u95e8\u5e97\u8303\u56f480, -90\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u8303\u56f490, -100\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u8303\u56f480, \u2212100\u5143', '\u5317\u57ce\u95e8\u5e97\u8303\u56f480, \u221290\u5143\uff0c\u5357\u57ce\u95e8\u5e97\u8303\u56f490, \u2212100\u5143'),
    ('\u5317\u57ce\u95e8\u5e97\u6536\u51651\uff1b230\u5143', '\u5317\u57ce\u95e8\u5e97\u6536\u51651\uff1b000\u5143\uff0c230\u5143'),
],ids=['thousands','negative-thousands','positive-thousands','grouped-decimal','ambiguous-range-comma','wide-comma','spaced-comma','signed-spaced-range','unicode-minus','outer-semicolon'])
def test_numeric_internal_separator_cannot_stitch_another_source_fact(claim,other):
    caption = prose(claim, caption=True, identifier='caption')
    original = prose(other, identifier='original')
    assert AnswerValidator().validate(claim+'[E1][E2]', frozen(caption, original),
        EvidencePlan('SIMULATED opaque question')) == 'UNSUPPORTED_ANSWER'


@pytest.mark.parametrize('text',[
    '\u56fe1\u5c55\u793a\u8bbe\u5907\u5e03\u5c40',
    '\u578b\u53f7X80\u8bbe\u5907\u91c7\u7528\u5706\u5f62\u5916\u58f3',
],ids=['figure-id','model-id'])
def test_caption_local_identifier_is_not_a_quantity_claim(text):
    assert AnswerValidator().validate(text+'[E1]', frozen(prose(text, caption=True)),
        EvidencePlan('SIMULATED opaque question')) is None


@pytest.mark.parametrize('text',[
    '\u56fe1\u663e\u793a\u6536\u516580\u5143',
    '\u578b\u53f7X80\u8bbe\u5907\u6536\u516580\u5143',
    '\u56fe1\u663e\u793a\u6e29\u5ea6-5.5\u6444\u6c0f\u5ea6',
    '\u578b\u53f7X80\u8bbe\u5907\u8303\u56f480-100\u5398\u7c73',
    '\u56fe1\u5398\u7c73',
    '\u578b\u53f7X80\u5143',
],ids=['figure-money','model-money','figure-signed-decimal','model-range','figure-adjacent-unit','model-adjacent-unit'])
def test_identifier_only_masks_its_own_digits_not_real_quantities(text):
    assert AnswerValidator().validate(text+'[E1]', frozen(prose(text, caption=True)),
        EvidencePlan('SIMULATED opaque question')) == 'UNSUPPORTED_ANSWER'


@pytest.mark.parametrize('claim,value',[
    ('1.25','2.25'),('.5','5'),('+1.25','-1.25'),('-1.25','+1.25'),
    ('80-100','80-90'),('-10--5','-10--8'),
],ids=['decimal','leading-decimal','positive-sign','negative-sign','range','negative-range'])
def test_numeric_boundaries_do_not_change_value_or_sign(claim,value):
    # Bare quantities also must not lose leading decimal/sign characters.
    text = claim+'\u5143'
    caption = prose(text, caption=True, identifier='caption')
    original = prose(value+'\u5143', identifier='original')
    assert AnswerValidator().validate(text+'[E1][E2]', frozen(caption, original),
        EvidencePlan('SIMULATED opaque question')) == 'UNSUPPORTED_ANSWER'


@pytest.mark.parametrize('value',['1,230','-1,230','+1,230','1,230.50','.5','-1.25','80-100','-10--5','1, 230'])
def test_complete_literal_original_preserves_numeric_formats(value):
    text = '\u8bbe\u5907\u7532\u6d4b\u91cf\u503c'+value+'\u5398\u7c73'
    caption = prose(text, caption=True, identifier='caption')
    original = prose(text, identifier='original')
    assert AnswerValidator().validate(text+'[E1][E2]', frozen(caption, original),
        EvidencePlan('SIMULATED opaque question')) is None


@pytest.mark.parametrize('support',['correct','wrong-fact','uncited'])
def test_caption_identifier_in_another_sentence_cannot_hide_numeric_citation_gaps(support):
    text = '\u8bbe\u5907\u7532\u957f\u5ea680\u5398\u7c73'
    qualitative = '\u56fe1\u5c55\u793a\u8bbe\u5907\u5e03\u5c40'
    caption = prose(qualitative, caption=True, identifier='caption')
    original = prose('\u5e93\u623f\u6e29\u5ea680\u6444\u6c0f\u5ea6' if support == 'wrong-fact' else text, identifier='original')
    reference = '' if support == 'uncited' else '[E2]'
    answer = text+reference+'\u3002'+qualitative+'[E1]'
    result = AnswerValidator().validate(answer, frozen(caption, original), EvidencePlan('SIMULATED opaque question'))
    assert result == (None if support == 'correct' else 'UNSUPPORTED_ANSWER')


@pytest.mark.parametrize('left,right,other_left,other_right',[
    ('1','.25','2','.50'),
    ('.1','.25','.2','.50'),
    ('+.1','+.25','+.2','+.50'),
    ('-.1','-.25','-.2','-.50'),
    ('−.1','−.25','−.2','−.50'),
    ('-.1','+.25','-.2','+.50'),
    ('1','.25','2','2.50'),
    ('1','2.5','2','3.5'),
    ('1','25','2','50'),
    ('1','+.25','2','+.50'),
    ('1','-.25','2','-.50'),
    ('.1','25','.2','50'),
    ('.1','+.25','.2','+.50'),
    ('.1','-.25','.2','-.50'),
    ('+.1','25','+.2','50'),
    ('+.1','.25','+.2','.50'),
    ('+.1','-.25','+.2','-.50'),
    ('-.1','25','-.2','50'),
    ('-.1','.25','-.2','.50'),
    ('(.1)','.25','(.2)','.50'),
    ('.1','(.25)','.2','(.50)'),
    ('.1','\u00b1.25','.2','\u00b1.50'),
    ('.1','\u066b25','.2','\u066b50'),
],ids=['integer-leading-decimal','leading-decimals','signed-plus','signed-minus','unicode-minus','mixed-signs','right-leading-decimal','right-decimal',
    'integer-both','integer-signed-plus-right','integer-signed-minus-right','unsigned-leading-left-integer-right',
    'unsigned-leading-left-signed-plus-right','unsigned-leading-left-signed-minus-right','signed-plus-left-integer-right',
    'signed-plus-left-unsigned-leading-right','signed-plus-left-signed-minus-right','signed-minus-left-integer-right','signed-minus-left-unsigned-leading-right',
    'unknown-left-wrapper','unknown-right-wrapper','unknown-right-sign','unknown-right-decimal-mark'])
def test_leading_decimal_right_boundary_cannot_stitch_caption_from_two_facts(
        left,right,other_left,other_right):
    claim='\u8bbe\u5907\u7532\u8303\u56f4'+left+','+right+'\u5398\u7c73'
    caption=prose(claim,caption=True,identifier='caption')
    first=prose('\u8bbe\u5907\u7532\u8303\u56f4'+left+'\uff0c'+other_right+'\u5398\u7c73',identifier='first-original')
    second=prose('\u8bbe\u5907\u4e59\u8303\u56f4'+other_left+'\uff0c'+right+'\u5398\u7c73',identifier='second-original')
    opaque=EvidencePlan('SIMULATED opaque question')
    assert AnswerValidator().validate(claim+'[E1][E2][E3]',frozen(caption,first,second),opaque)=='UNSUPPORTED_ANSWER'
    # One complete, literal original witness still supports this exact claim.
    complete=prose(claim,identifier='complete-original')
    assert AnswerValidator().validate(claim+'[E1][E2]',frozen(caption,complete),opaque) is None
