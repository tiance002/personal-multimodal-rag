"""S6 bounded source retrieval: synthetic contracts and saved real responses.

No Provider, database, Gold injection or source mutation. A passing quote proof
does not claim that the model's extracted predicate is semantically true.
"""
import hashlib
import json
from pathlib import Path

import pytest

from backend.weknora_port.graph.extraction import (parse_extraction, GraphExtractionError,
                                                   validate_batch_proofs)
from backend.weknora_port.graph.quote_completion import parse_with_quote_completion
from backend.weknora_port.graph.types import GraphSource


def case(content='Alpha Institute works with Beta Lab.', quote='works with Beta Lab',
         predicate='works with', *, anchor=True, time=''):
    source = GraphSource(1, 'owner', 'kb', 'doc', 'version', 'chunk', content, 0, len(content))
    raw = {'entities': [
        {'key': 'a', 'name': 'Alpha Institute', 'type': 'organization', 'aliases': [], 'anchor': anchor, 'qualifier': ''},
        {'key': 'b', 'name': 'Beta Lab', 'type': 'organization', 'aliases': [], 'anchor': False, 'qualifier': ''}],
        'relations': [{'source': 'a', 'target': 'b', 'predicate': predicate, 'quote': quote,
                       'polarity': 'affirmed', 'time': time}]}
    return source, json.dumps(raw)


def test_unique_short_quote_obtains_single_sentence_without_changing_original_response():
    source, raw = case()
    baseline = parse_extraction(source, raw, 'op')
    assert not baseline.edges
    enhanced, mappings = parse_with_quote_completion(source, raw, 'op')
    assert len(enhanced.edges) == 1 and len(mappings) == 1
    assert enhanced.nodes == baseline.nodes and enhanced.rejected == baseline.rejected
    assert enhanced.source == source and enhanced.extraction_id == baseline.extraction_id
    mapping = mappings[0]
    assert mapping['kind'] == 'SOURCE_SENTENCE_EXTENSION_NOT_LLM_QUOTE'
    assert mapping['offset_space'] == 'chunk_local_unicode_codepoints'
    assert mapping['source_sha256'] == source.content_sha256
    assert source.content[mapping['original_quote_start']:mapping['original_quote_end']] == mapping['original_quote']
    assert source.content[mapping['derived_quote_start']:mapping['derived_quote_end']] == mapping['derived_quote']
    assert mapping['edge_id'] == enhanced.edges[0].id
    assert json.loads(raw)['relations'][0]['quote'] == 'works with Beta Lab'
    validate_batch_proofs(enhanced)


def test_valid_complete_quote_is_unchanged():
    source, raw = case(quote='Alpha Institute works with Beta Lab.')
    baseline = parse_extraction(source, raw, 'op')
    enhanced, mappings = parse_with_quote_completion(source, raw, 'op')
    assert enhanced == baseline and len(enhanced.edges) == 1 and mappings == ()


@pytest.mark.parametrize('content,quote,predicate,time', [
    ('Alpha Institute. It works with Beta Lab.', 'works with Beta Lab', 'works with', ''),
    ('Alpha Institute\nworks with Beta Lab.', 'works with Beta Lab', 'works with', ''),
    ('Alpha Institute\rworks with Beta Lab.', 'works with Beta Lab', 'works with', ''),
    ('Alpha Institute notes that Beta Lab works with Alpha Institute.',
     'Alpha Institute notes that Beta Lab works with Alpha Institute', 'works with', ''),
    ('Alpha Institute does not work with Beta Lab.', 'work with Beta Lab', 'work with', ''),
    ('Alpha Institute may work with Beta Lab.', 'work with Beta Lab', 'work with', ''),
    ('Alpha Institute works with Beta Lab. Gamma Institute works with Beta Lab.',
     'works with Beta Lab', 'works with', ''),
    ('Alpha Institute ' + 'word ' * 220 + 'works with Beta Lab.', 'works with Beta Lab', 'works with', ''),
    ('Alpha Institute works with Beta Lab.', 'works with', 'works with', ''),
    ('Alpha Institute works with Beta Lab.', 'works with Beta Lab', 'operates with', ''),
    ('The report describes Alpha Institute works with Beta Lab.', 'works with Beta Lab', 'works with', ''),
    ('Alpha Institute and Alpha Institute work with Beta Lab.', 'work with Beta Lab', 'work with', ''),
    ('Alpha Institute worked with Beta Lab in 2020.', 'worked with Beta Lab in 2020', 'worked with', ''),
    ('Alpha Institute worked with Beta Lab in 2020.', 'worked with Beta Lab in 2020', 'worked with', 'in 2020'),
    ('Alpha Institute lists partners; Gamma works with Beta Lab.', 'works with Beta Lab', 'works with', ''),
])
def test_direction_cross_sentence_uncertainty_ambiguity_and_length_stay_rejected(content, quote, predicate, time):
    source, raw = case(content, quote, predicate, time=time)
    baseline = parse_extraction(source, raw, 'op')
    result, mappings = parse_with_quote_completion(source, raw, 'op')
    assert result == baseline and mappings == () and not result.edges


def test_nonanchor_subject_and_failed_entity_are_not_repaired():
    source, raw = case(anchor=False)
    baseline = parse_extraction(source, raw, 'op')
    assert parse_with_quote_completion(source, raw, 'op') == (baseline, ())
    payload = json.loads(raw)
    payload['entities'][0].pop('qualifier')
    raw = json.dumps(payload)
    baseline = parse_extraction(source, raw, 'op')
    assert any(item['kind'] == 'entity' for item in baseline.rejected)
    assert parse_with_quote_completion(source, raw, 'op') == (baseline, ())


@pytest.mark.parametrize('raw', ['not JSON', '{"entities":[],"relations":[],"time":""}'])
def test_invalid_json_and_root_schema_raise_original_parser_error(raw):
    source, _ = case()
    with pytest.raises(GraphExtractionError):
        parse_with_quote_completion(source, raw, 'op')


def test_missing_object_can_only_be_retrieved_from_same_verified_anchor_sentence():
    source, raw = case(quote='Alpha Institute works with')
    enhanced, mappings = parse_with_quote_completion(source, raw, 'op')
    assert len(enhanced.edges) == 1 and len(mappings) == 1
    validate_batch_proofs(enhanced)


_RUN = Path('E:/WK/run/s6-graphrag-20261019')


@pytest.mark.parametrize('directory,index,additional', [
    ('acceptance-deepseek', 0, 1), ('acceptance-deepseek', 1, 0),
    ('acceptance-origin-fixed', 0, 0)])
def test_saved_real_receipt_readonly_replay(directory, index, additional):
    path = _RUN / directory / f'graph-{index}.json'
    if not path.is_file():
        pytest.skip('Saved local S6 receipt is not present on this machine')
    original_bytes = path.read_bytes()
    record = json.loads(original_bytes)['chunks'][0]
    source = GraphSource(**record['source'])
    baseline = parse_extraction(source, record['raw_response'], record['request_id'])
    enhanced, mappings = parse_with_quote_completion(source, record['raw_response'], record['request_id'])
    assert len(enhanced.edges) == len(baseline.edges) + additional
    assert len(mappings) == additional
    assert enhanced.nodes == baseline.nodes and enhanced.rejected == baseline.rejected
    for mapping in mappings:
        assert mapping['source_sha256'] == source.content_sha256
        assert source.content[mapping['derived_quote_start']:mapping['derived_quote_end']] == mapping['derived_quote']
        assert mapping['relation_index'] == 1  # Post-hoc receipt assertion, never runtime selection.
    validate_batch_proofs(enhanced)
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(original_bytes).digest()


def test_saved_qwen_invalid_root_schema_remains_rejected():
    path = _RUN / 'acceptance-origin-fixed' / 'graph-1.json'
    if not path.is_file():
        pytest.skip('Saved local S6 receipt is not present on this machine')
    record = json.loads(path.read_bytes())['chunks'][0]
    with pytest.raises(GraphExtractionError, match='Expected entities and relations only'):
        parse_with_quote_completion(GraphSource(**record['source']), record['raw_response'], record['request_id'])
