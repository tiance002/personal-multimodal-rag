"""Literal offsets across scripts, with independent identifier protections."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.weknora_port.graph.extraction import _occurrences, parse_extraction, validate_batch_proofs
from backend.weknora_port.graph.retrieval import find_paths, mentioned
from backend.weknora_port.graph.quote_completion import parse_with_quote_completion
from backend.weknora_port.graph.types import GraphSource


def source(text):
    return GraphSource(1, 'boundary-owner', 'boundary-kb', 'document', 'v1', 'chunk', text, 0, len(text))


def payload(subject, predicate, target, text):
    return {'entities': [
        {'key': 's', 'name': subject, 'type': 'PLACE', 'aliases': [], 'anchor': True, 'qualifier': ''},
        {'key': 't', 'name': target, 'type': 'PLACE', 'aliases': [], 'anchor': False, 'qualifier': ''}],
        'relations': [{'source': 's', 'predicate': predicate, 'target': 't', 'quote': text,
                       'polarity': 'affirmed', 'time': ''}]}


def test_contiguous_chinese_exact_relation_keeps_original_offsets():
    text = '北京位于中国。'
    assert _occurrences(text, '北京') == [(0, 2)]
    assert _occurrences(text, '位于') == [(2, 4)]
    assert _occurrences(text, '中国') == [(4, 6)]
    batch = parse_extraction(source(text), json.dumps(payload('北京', '位于', '中国', text)), 'offline')
    assert len(batch.nodes) == 2 and len(batch.edges) == 1 and not batch.rejected
    assert batch.nodes[0].is_anchor
    edge = batch.edges[0]
    assert text[edge.quote_start:edge.quote_end] == edge.quote == text
    validate_batch_proofs(batch)


@pytest.mark.parametrize('text,surface,spans', [
    ('请问Franklin bells配套什么设备？', 'Franklin bells', [(2, 16)]),
    ('设备NP-204位于东区。', 'NP-204', [(2, 8)]),
    ('北京位于China。', 'China', [(4, 9)]),
    ('Beijing位于中国。', 'Beijing', [(0, 7)]),
])
def test_mixed_script_literals_preserve_source_text_and_offsets(text, surface, spans):
    assert _occurrences(text, surface) == spans
    assert all(text[start:end] == surface for start, end in spans)


def test_mixed_script_query_seed_matches_but_extraction_case_contract_is_unchanged():
    assert mentioned('请问Franklin bells配套什么设备？', 'Franklin bells')
    assert mentioned('请问FRANKLIN BELLS配套什么设备？', 'Franklin bells')
    assert not _occurrences('设备FRANKLIN BELLS位于这里。', 'Franklin bells')


@pytest.mark.parametrize('text,surface', [
    ('superFranklin bells', 'Franklin bells'), ('Franklin bellsExtra', 'Franklin bells'),
    ('Franklin bells', 'Franklin bell'), ('NP-2040', 'NP-204'), ('NP2040', 'NP204'),
    ('NP-204', 'NP'), ('NP-204', '204'), ('prefix_NP204', 'NP204'),
    ('NP204_suffix', 'NP204'), ('North-Pier', 'Pier'), ('North-Pier', 'North'),
    ("Gordon's Bells", 'Gordon'), ('Gordon’s Bells', 'Gordon'),
    ('ＡＢＣＤ', 'ＡＢＣ'), ('MünchenNord', 'München'),
    ('Cafe\u0301North', 'Cafe'), ('North‐Pier', 'North'),
])
def test_identifier_word_and_entity_containment_boundaries_remain_rejected(text, surface):
    assert _occurrences(text, surface) == []
    assert not mentioned(text, surface)


def test_hyphenated_entity_remains_matchable_as_a_whole():
    assert _occurrences('请问North-Pier的情况', 'North-Pier') == [(2, 12)]
    assert mentioned('请问North-Pier的情况', 'North-Pier')


def test_whole_entity_text_and_multiple_literal_occurrences_are_not_fuzzy_merged():
    text = '北京和北京大学是不同名称。'
    assert len(_occurrences(text, '北京大学')) == 1
    # Literal occurrence does not prove that the substring names an independent
    # entity. The parser keeps entity identity/type/quote and ambiguity checks.
    assert len(_occurrences(text, '北京')) == 2
    assert not _occurrences(text, '北大')


def test_chinese_direction_and_unique_quote_protection_remain_active():
    text = '北京位于中国。'
    data = payload('北京', '位于', '中国', text)
    data['relations'][0].update(source='t', target='s')
    batch = parse_extraction(source(text), json.dumps(data), 'offline')
    assert not batch.edges and batch.rejected[-1]['reason'] == 'DIRECTION_OR_PREDICATE_NOT_IN_QUOTE'
    batch = parse_extraction(source(text + text), json.dumps(payload('北京', '位于', '中国', text)), 'offline')
    assert not batch.edges and batch.rejected[-1]['reason'] == 'QUOTE_NOT_UNIQUE_IN_SOURCE'


def test_chinese_quote_completion_reuses_literal_boundary_and_preserves_source_mapping():
    text = '北京位于中国。'
    data = payload('北京', '位于', '中国', '位于中国')
    batch, mappings = parse_with_quote_completion(source(text), json.dumps(data), 'offline')
    assert len(batch.edges) == 1 and len(mappings) == 1
    assert mappings[0]['derived_quote'] == text[:-1]
    assert mappings[0]['source_sha256'] == source(text).content_sha256
    validate_batch_proofs(batch)


def test_chinese_literal_match_does_not_resolve_ambiguous_same_name_document_anchors():
    first = '林澈创办海青公司。'
    a = payload('林澈', '创办', '海青公司', first)
    a['entities'][0]['type'], a['entities'][1]['type'] = 'PERSON', 'ORGANIZATION'
    first_batch = parse_extraction(source(first), json.dumps(a), 'first')
    seconds = []
    for index, place in enumerate(('东港', '西湾')):
        text = f'海青公司位于{place}。'
        data = payload('海青公司', '位于', place, text)
        data['entities'][0]['type'] = 'ORGANIZATION'
        other_source = replace(source(text), document_id=f'other-{index}', chunk_id=f'other-{index}')
        seconds.append(parse_extraction(other_source, json.dumps(data), f'other-{index}'))
    context = SimpleNamespace(scope=SimpleNamespace(owner_id='boundary-owner',
                  knowledge_base_ids=('boundary-kb',), document_ids=()))
    paths = find_paths('林澈创办的机构位于哪里？', (first_batch, *seconds), context, tenant_id=1)
    assert len(first_batch.edges) == 1 and all(len(batch.edges) == 1 for batch in seconds)
    assert not any(len(path.edges) == 2 for path in paths)


def test_chinese_entity_name_containment_does_not_create_fuzzy_cross_document_link():
    first = '林澈创办海青公司。'
    data = payload('林澈', '创办', '海青公司', first)
    data['entities'][0]['type'], data['entities'][1]['type'] = 'PERSON', 'ORGANIZATION'
    first_batch = parse_extraction(source(first), json.dumps(data), 'first')
    second = '海青公司研究院位于东港。'
    data = payload('海青公司研究院', '位于', '东港', second)
    data['entities'][0]['type'] = 'ORGANIZATION'
    other = replace(source(second), document_id='different-entity', chunk_id='other')
    second_batch = parse_extraction(other, json.dumps(data), 'second')
    context = SimpleNamespace(scope=SimpleNamespace(owner_id='boundary-owner',
                  knowledge_base_ids=('boundary-kb',), document_ids=()))
    paths = find_paths('林澈创办的机构位于哪里？', (first_batch, second_batch), context, tenant_id=1)
    assert not any(path.reference_links for path in paths)
    assert not any(len(path.edges) == 2 for path in paths)
