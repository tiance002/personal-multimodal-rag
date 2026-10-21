"""Program-owned sentence identity, exact source offsets, no provider calls."""
import json
from dataclasses import replace

import pytest

from backend.weknora_port.retrieval import bridge_rescue as bridge
from backend.weknora_port.retrieval.types import SearchResult


QUESTION = 'Where is the seminar Alice Bennett directed in 2011–2014 located?'
QUOTE = 'Alice Bennett directed the Arbor Poetry Seminar from 2011–2014.'


def source(content='序言。\n'+QUOTE, id='original', index=0, start=0):
    return SearchResult(id, content, .8, knowledge_id='doc', knowledge_base_id='kb',
        chunk_index=index, start_at=start, end_at=start+len(content),
        metadata={'source_version':'v1'}, chunk_metadata={'source_version':'v1'})


def candidate(row, **changes):
    windows = bridge.fact_windows(row)
    records = bridge.sentence_table(windows, {row.id:row})
    selected = next(record for record in records.values() if record.text == QUOTE)
    result = dict(status='FIRST_HOP_CANDIDATE', sentence_id=selected.sentence_id,
        subject='Alice Bennett', subject_resolution='EXPLICIT', predicate='directed',
        object='Arbor Poetry Seminar', time='2011–2014', target_attribute='location',
        bridge_search_phrases=['Arbor Poetry Seminar'])
    result.update(changes)
    return result, windows


def test_model_selects_id_while_program_owns_unicode_source_offsets():
    row = source()
    data, windows = candidate(row)
    assert not {'quote_start', 'quote_end', 'quote', 'source_chunk_id'} & data.keys()
    verified = bridge.validate_extraction(json.dumps(data), windows, {row.id:row}, QUESTION)
    assert verified['quote_start'] == 4
    assert verified['quote_end'] == 4 + len(QUOTE)
    assert verified['quote'] == row.content[verified['quote_start']:verified['quote_end']] == QUOTE
    assert verified['source_chunk_id'] == row.id and verified['relation_verified']


@pytest.mark.parametrize('change', [
    {'sentence_id':'invented'}, {'sentence_id':True}, {'quote_start':4},
    {'quote_end':100}, {'quote':QUOTE}, {'source_chunk_id':'other'},
])
def test_old_offsets_or_foreign_sentence_identity_cannot_be_admitted(change):
    row = source()
    data, windows = candidate(row, **change)
    with pytest.raises((ValueError, TypeError)):
        bridge.validate_extraction(json.dumps(data), windows, {row.id:row}, QUESTION)


def test_preceding_sentence_id_cannot_replace_main_relation():
    row = source()
    windows = bridge.fact_windows(row, role='preceding')
    data, _ = candidate(row)
    with pytest.raises(ValueError):
        bridge.validate_extraction(json.dumps(data), windows, {row.id:row}, QUESTION)


def test_sentence_identity_is_bound_to_source_version_and_body():
    row = source()
    data, windows = candidate(row)
    changed = replace(row, metadata={'source_version':'v2'}, chunk_metadata={'source_version':'v2'})
    with pytest.raises(ValueError):
        bridge.validate_extraction(json.dumps(data), windows, {row.id:changed}, QUESTION)
    changed = replace(row, content=row.content.replace('Bennett', 'Benetto'))
    with pytest.raises(ValueError):
        bridge.validate_extraction(json.dumps(data), windows, {row.id:changed}, QUESTION)


def test_same_sentence_in_distinct_originals_has_distinct_identity():
    first = source()
    second = source(id='second', start=1000)
    windows = bridge.fact_windows(first) + bridge.fact_windows(second)
    table = bridge.sentence_table(windows, {first.id:first, second.id:second})
    matches = [record for record in table.values() if record.text == QUOTE]
    assert len(matches) == 2 and len({r.sentence_id for r in matches}) == 2


def test_exact_chunk_overlap_is_deduplicated_without_text_normalization():
    first = source(QUOTE, id='first')
    second = source(QUOTE+'\nShe wrote poems.', id='second')
    windows = bridge.fact_windows(first, role='preceding') + bridge.fact_windows(second)
    table = bridge.sentence_table(windows, {first.id:first, second.id:second})
    assert sum(record.text == QUOTE for record in table.values()) == 1
    assert next(record for record in table.values() if record.text == QUOTE).role == 'main'


def test_abbreviation_periods_do_not_create_fake_biographical_sentences():
    text = 'She earned her B.A. from Eastern College and an M.F.A. in Writing. She taught poetry.'
    spans = list(bridge.sentence_spans(text))
    assert [text[a:b] for a,b in spans] == [
        'She earned her B.A. from Eastern College and an M.F.A. in Writing.', 'She taught poetry.']


def test_payload_has_only_versioned_sentence_choices_and_original_text():
    row = source()
    data, windows = candidate(row)
    payload = bridge.extraction_payload(QUESTION, windows, {row.id:row})
    assert payload['contract_version'] == 'sentence-id/v2'
    assert payload['question'] == QUESTION
    assert all(set(item) == {'sentence_id','text','role'} for item in payload['sentences'])
    assert sum(len(item['text']) for item in payload['sentences']) <= 2400
    assert any(item['sentence_id'] == data['sentence_id'] for item in payload['sentences'])


def test_copied_negation_clause_is_not_a_verified_time_expression():
    quote = 'Alice Bennett directed the Arbor Poetry Seminar from 2011–2014 but not in 2012.'
    row = source(quote)
    selected = next(iter(bridge.sentence_table(bridge.fact_windows(row), {row.id:row}).values()))
    data = dict(status='FIRST_HOP_CANDIDATE', sentence_id=selected.sentence_id,
        subject='Alice Bennett', subject_resolution='EXPLICIT', predicate='directed',
        object='Arbor Poetry Seminar', time='2011–2014 but not in 2012', target_attribute='location',
        bridge_search_phrases=['Arbor Poetry Seminar'])
    result = bridge.validate_extraction(json.dumps(data), bridge.fact_windows(row), {row.id:row},
        'Where is the seminar Alice Bennett directed in 2012 located?')
    assert result['span_verified'] and not result['relation_verified']


@pytest.mark.parametrize('place', ['Paris or Rome', 'a painting of Paris',
                                  'Paris according to a fictional story'])
def test_ambiguous_or_narrative_location_is_not_a_physical_location_fact(place):
    row = source('Arbor Poetry Seminar is located in '+place+'.')
    assert bridge._location_facts(row, ['Arbor Poetry Seminar']) == []


@pytest.mark.parametrize('question', [
    'Where is the seminar Alice Bennett did not direct in 2011–2014 located?',
    'Where is the seminar Alice Bennett directed before 2011–2014 located?',
    'Where is the seminar Alice Bennett directed in 1400 located?',
])
def test_unsupported_question_time_or_negation_does_not_gain_positive_relation(question):
    row = source()
    data, windows = candidate(row)
    result = bridge.validate_extraction(json.dumps(data), windows, {row.id:row}, question)
    assert not result['relation_verified']


@pytest.mark.parametrize('predicate,gap', [('directed',' at the '), ('headed',' for the ')])
def test_directional_preposition_does_not_establish_director_relation(predicate, gap):
    row = source('Alice Bennett '+predicate+gap+'Arbor Poetry Seminar from 2011–2014.')
    selected = next(iter(bridge.sentence_table(bridge.fact_windows(row), {row.id:row}).values()))
    data, _ = candidate(source())
    data.update(sentence_id=selected.sentence_id, predicate=predicate)
    result = bridge.validate_extraction(json.dumps(data), bridge.fact_windows(row), {row.id:row}, QUESTION)
    assert not result['relation_verified']


def test_literal_institution_location_does_not_prove_historical_held_venue():
    from test_bridge_rescue import case, run
    state, repository, models = case()
    state.query = 'Where was the seminar Alice Bennett directed in 2011–2014 held?'
    run(state, repository, models)
    assert len(state.rescue_evidence) == 1
    assert state.rescue_evidence[0].metadata['bridge_rescue']['final_answer_supported'] is False
