"""S8.5 source-entailment regressions; fixed originals, no model or network.

These initial tests intentionally use the S8.4 offset extraction contract so
their RED evidence isolates semantic validation from the sentence-ID change.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.weknora_port.retrieval.bridge_rescue import (
    _location_facts, fact_windows, sentence_spans, validate_extraction,
)


def relation(quote, *, subject='Alice Bennett', predicate='directed',
             object='Arbor Poetry Seminar', time='2011–2014', question=None):
    source = SimpleNamespace(id='original', content=quote)
    data = {
        'status': 'FIRST_HOP_CANDIDATE', 'source_chunk_id': source.id,
        'quote_start': 0, 'quote_end': len(quote), 'quote': quote,
        'subject': subject, 'subject_resolution': 'EXPLICIT',
        'predicate': predicate, 'object': object, 'time': time,
        'target_attribute': 'location', 'bridge_search_phrases': [object],
    }
    question = question or f'Where is the seminar {subject} directed in 2011–2014 located?'
    return validate_extraction(json.dumps(data, ensure_ascii=False),
                               fact_windows(source), {source.id: source}, question)


@pytest.mark.parametrize('suffix', [' Annex', ' II', ' Foundation'])
def test_relation_object_must_include_complete_named_object(suffix):
    quote = f'Alice Bennett directed the Arbor Poetry Seminar{suffix} from 2011–2014.'
    result = relation(quote)
    assert result['span_verified'] is True
    assert result['relation_verified'] is False


@pytest.mark.parametrize('quote', [
    'Alice Bennett directed the Arbor Poetry Seminar before moving to London from 2011–2014.',
    'Alice Bennett directed the Arbor Poetry Seminar; she lived in London from 2011–2014.',
    'Alice Bennett directed the Arbor Poetry Seminar while Bob Smith taught in London from 2011–2014.',
])
def test_time_must_attach_to_extracted_relation(quote):
    # The semicolon is a sentence boundary in the existing splitter. Keep the
    # regression about attachment within a single sentence, rather than offsets.
    quote = quote.replace(';', ',')
    result = relation(quote)
    assert result['span_verified'] is True
    assert result['relation_verified'] is False


@pytest.mark.parametrize('quote,predicate,object', [
    ('Alice Bennett directed the Arbor Poetry Seminar from 2011–2014.',
     'directed', 'Arbor Poetry Seminar'),
    ('Alice Bennett directed the Arbor Poetry Seminar from 2011–2014, and serves on faculty.',
     'directed', 'Arbor Poetry Seminar'),
    ('Alice Bennett served as the Director of The Frost Place Advanced Seminar from 2011–2014, and serves on the faculty for the Stonecoast MFA Program in Creative Writing.',
     'served as the Director of', 'The Frost Place Advanced Seminar'),
])
def test_complete_relation_and_coordinated_second_predicate_remain_verified(quote, predicate, object):
    result = relation(quote, predicate=predicate, object=object)
    assert result['span_verified'] is True
    assert result['subject_verified'] is True
    assert result['relation_verified'] is True


def test_coordinated_second_object_is_not_single_object_relation():
    result = relation('Alice Bennett directed the Arbor Poetry Seminar and the Oak Arts Workshop from 2011–2014.')
    assert result['relation_verified'] is False


def test_coordinated_named_subject_is_outside_narrow_relation_grammar():
    result = relation('Alice Bennett directed the Arbor Poetry Seminar from 2011–2014, and Bob Smith serves on faculty.')
    assert result['subject_verified'] is True
    assert result['relation_form_verified'] is False
    assert result['relation_verified'] is False


def test_frozen_frost_relation_form_is_valid_independently_of_pronoun_identity():
    frozen = json.loads((Path(__file__).parent / 'fixtures/s8_4_bridge/inputs.json').read_text(encoding='utf-8'))
    case = frozen['cases'][0]
    source = next(row for row in case['source_rows']
                  if row['id'] == 'ec07a1fd-8a8e-4f23-a7d6-65d385d2343b')
    quote = next(source['content'][start:end] for start, end in sentence_spans(source['content'])
                 if source['content'][start:end].startswith('She served as the Director'))
    result = relation(quote, subject='She', predicate='served as the Director of',
                      object='The Frost Place Advanced Seminar', time='2007–2010',
                      question=case['query'])
    assert result['span_verified'] is True
    assert result['relation_form_verified'] is True
    assert result['subject_verified'] is False
    assert result['relation_verified'] is False


@pytest.mark.parametrize('subject', [
    'I', 'We', 'You', 'Me', 'Us', 'My', 'Mine', 'Our', 'Ours', 'Your', 'Yours',
    'Myself', 'Yourself', 'Ourselves', 'She', 'He', 'They', 'It',
    '我', '我们', '你', '您', '你们', '它', '它们',
])
def test_pronouns_never_become_explicit_identity_from_model_label_or_question(subject):
    result = relation(f'{subject} directed the Arbor Poetry Seminar from 2011–2014.',
                      subject=subject)
    assert result['span_verified'] is True
    assert result['subject_verified'] is False
    assert result['relation_verified'] is False


@pytest.mark.parametrize('quote', [
    'The rival of Arbor Poetry Seminar is located in Paris.',
    'The supporter of Arbor Poetry Seminar is based in Paris.',
    'Arbor Poetry Seminar is studying whether the laboratory is located in Paris.',
    'Arbor Poetry Seminar is aware that the workshop is situated in Paris.',
    'Arbor Poetry Seminar is not located in Paris.',
    'Arbor Poetry Seminar might be located in Paris.',
])
def test_locative_predicate_must_apply_directly_to_matched_entity(quote):
    assert _location_facts(SimpleNamespace(content=quote), ['Arbor Poetry Seminar']) == []


@pytest.mark.parametrize('quote,phrase,place', [
    ('Arbor Poetry Seminar is located in Paris.', 'Arbor Poetry Seminar', 'Paris'),
    ('Arbor Poetry Seminar was based in Harbor City, Canada.',
     'Arbor Poetry Seminar', 'Harbor City, Canada'),
    ('The Arbor Poetry Seminar is situated in Paris.', 'Arbor Poetry Seminar', 'Paris'),
    ('The Frost Place is a museum and nonprofit educational center for poetry located at Robert Frost\'s former home in Franconia, New Hampshire, United States.',
     'The Frost Place', "Robert Frost's former home in Franconia, New Hampshire, United States"),
])
def test_direct_and_classificatory_locatives_preserve_exact_source(quote, phrase, place):
    facts = _location_facts(SimpleNamespace(content=quote), [phrase])
    assert len(facts) == 1
    fact = facts[0]
    assert fact['phrase'] == phrase
    assert fact['location_description'] == place
    assert fact['quote'] == quote[fact['quote_start']:fact['quote_end']] == quote
