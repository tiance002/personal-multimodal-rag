import importlib
import importlib.util

import pytest


def subject():
    assert importlib.util.find_spec('eval_center.gold') is not None, 'stable Gold mapping is not implemented'
    return importlib.import_module('eval_center.gold')


def span(module, start=0, end=100, **kwargs):
    return module.SourceSpan('document-a', 'a' * 64, start, end, **kwargs)


def test_split_gold_unions_spans_without_double_counting_overlap():
    m = subject()
    gold = m.GoldEvidence('gold-a', span(m), grade=2)
    partial = m.coverage([gold], [span(m, 0, 45)], threshold=0.8)
    assert partial['covered_ids'] == []
    assert partial['fractions']['gold-a'] == pytest.approx(0.45)
    complete = m.coverage([gold], [span(m, 0, 60), span(m, 40, 90), span(m, 0, 60)], threshold=0.8)
    assert complete['covered_ids'] == ['gold-a']
    assert complete['fractions']['gold-a'] == pytest.approx(0.9)
    assert complete['evidence_coverage'] == pytest.approx(0.9)


def test_one_chunk_can_cover_multiple_gold_and_cross_document_is_separate():
    m = subject()
    gold = [m.GoldEvidence('g1', span(m, 5, 20)), m.GoldEvidence('g2', span(m, 35, 60)),
            m.GoldEvidence('g3', m.SourceSpan('document-b', 'b'*64, 0, 10))]
    result = m.coverage(gold, [span(m, 0, 70)])
    assert result['covered_ids'] == ['g1', 'g2']
    assert result['context_recall'] == pytest.approx(2/3)


def test_changed_source_version_never_matches_old_gold():
    m = subject()
    gold = m.GoldEvidence('g', span(m))
    result = m.coverage([gold], [m.SourceSpan('document-a', 'b'*64, 0, 100)])
    assert result['covered_ids'] == []
    assert result['fractions']['g'] == 0


@pytest.mark.parametrize('kind,extra', [('pdf', {'page': 2}), ('image', {'page': 2, 'asset_id': 'ocr-page-2'})])
def test_pdf_and_ocr_page_asset_coordinates_are_respected(kind, extra):
    m = subject()
    g = m.GoldEvidence('g', span(m, kind=kind, **extra))
    wrong = {**extra, 'page': 3}
    assert m.coverage([g], [span(m, kind=kind, **wrong)])['context_recall'] == 0
    assert m.coverage([g], [span(m, kind=kind, **extra)])['context_recall'] == 1
    if kind == 'image':
        assert m.coverage([g], [span(m, kind=kind, page=2, asset_id='other')])['context_recall'] == 0


def test_spreadsheet_cells_are_unioned_only_in_same_sheet():
    m = subject()
    g = m.GoldEvidence('g', span(m, kind='sheet', sheet='Budget', cells=('B2', 'B3', 'C3')))
    first = span(m, kind='sheet', sheet='Budget', cells=('B2', 'B3'))
    other = span(m, kind='sheet', sheet='Other', cells=('C3',))
    assert m.coverage([g], [first, other])['fractions']['g'] == pytest.approx(2/3)
    final = span(m, kind='sheet', sheet='Budget', cells=('C3',))
    assert m.coverage([g], [first, final, first])['context_recall'] == 1


def test_no_answer_gold_has_unavailable_coverage_not_zero():
    m = subject()
    result = m.coverage([], [span(m)])
    assert result['context_recall'] is None
    assert result['evidence_coverage'] is None


def test_gold_rejects_ambiguous_or_invalid_source_ranges():
    m = subject()
    with pytest.raises(ValueError):
        span(m, 10, 10)
    with pytest.raises(ValueError):
        span(m, kind='pdf')
    with pytest.raises(ValueError):
        m.SourceSpan('document-a', 'mutable-version-label', 0, 10)
    with pytest.raises(ValueError):
        m.coverage([m.GoldEvidence('g', span(m)), m.GoldEvidence('g', span(m))], [])


def test_fixture_conversion_freezes_sources_and_preserves_original_fields():
    m = subject()
    fixture = [{'chunk_id':'old-1', 'document_id':'d', 'version_id':'v', 'content':'first fact', 'knowledge_base_id':'kb'},
               {'chunk_id':'old-2', 'document_id':'d', 'version_id':'v', 'content':'second fact', 'knowledge_base_id':'kb'}]
    cases = [{'case_id':'c', 'question':'first?', 'expected_chunk_ids':['old-1'], 'answer_points':['first'], 'kb_scope':['kb']}]
    converted = m.convert_legacy_fixture(cases, fixture, dataset_version='fixture-stable-v2')
    assert cases[0]['expected_chunk_ids'] == ['old-1']
    assert converted['cases'][0]['expected_chunk_ids'] == ['old-1']
    evidence = converted['cases'][0]['gold_evidence'][0]
    assert evidence['locator']['start'] == 0
    assert evidence['locator']['end'] == len('first fact')
    assert len(evidence['source_version']) == 64
    assert converted['coordinate_space'] == 'frozen_fixture_text'
    assert converted == m.convert_legacy_fixture(cases, list(reversed(fixture)), dataset_version='fixture-stable-v2')
    with pytest.raises(ValueError):
        m.convert_legacy_fixture([{**cases[0], 'expected_chunk_ids':['missing']}], fixture, dataset_version='v2')
