"""Prompt enum clarity and strict saved-output rejection; no model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest

from backend.weknora_port.retrieval.bridge_rescue import (
    CONTRACT_VERSION, EXTRACTION_KEYS, SYSTEM, fact_windows, validate_extraction,
)
from backend.weknora_port.retrieval.types import SearchResult


# Exact message.content of the last saved real response, not a repaired
# output. Raw response: stage4 execution/evidence/5c3dd835...response.body.
SAVED_INVALID_OUTPUT = '''{
  "status": "FIRST_HOP_CANDIDATE",
  "sentence_id": "s-c9a81754f7673886cbb035bf",
  "subject": "She",
  "subject_resolution": "",
  "predicate": "served as the Director of",
  "object": "The Frost Place Advanced Seminar",
  "time": "2007–2010",
  "target_attribute": "location",
  "bridge_search_phrases": [
    "The Frost Place Advanced Seminar"
  ]
}'''
SAVED_CONTENT_SHA256 = '9a2b8eaaa28631d5e92f9d9958bf97e3c4cc86b176186c5c8a4eb15d3ed68920'
SAVED_B1_OUTPUT = '''{
  "status": "FIRST_HOP_CANDIDATE",
  "sentence_id": "s-c9a81754f7673886cbb035bf",
  "subject": "She",
  "subject_resolution": "UNVERIFIED",
  "predicate": "served as the Director of",
  "object": "The Frost Place Advanced Seminar from 2007–2010",
  "time": "",
  "target_attribute": "location",
  "bridge_search_phrases": ["The Frost Place Advanced Seminar"]
}'''
SAVED_B1_SHA256 = '756442eae897221d4bddf904c4cf0e8b4478eec7fae62b9ddf2dbd868403c4a5'
FIXTURE = Path(__file__).parent / 'fixtures' / 's8_4_bridge' / 'inputs.json'


def prompt_objects():
    values = []
    for match in re.finditer(r'\{[^{}]*\}', SYSTEM):
        try:
            values.append(json.loads(match[0]))
        except json.JSONDecodeError:
            pass
    return values


def frozen_source():
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135'
    case = next(case for case in json.loads(raw)['cases'] if case['case_id'] == 'frost-A')
    source = SearchResult(**next(row for row in case['source_rows']
                                if row['id'] == 'ec07a1fd-8a8e-4f23-a7d6-65d385d2343b'))
    return case, source


def test_prompt_declares_required_nonempty_resolution_enum_as_schema():
    schemas = [value for value in prompt_objects() if value.get('type') == 'string' and 'enum' in value]
    assert any(schema['enum'] == ['EXPLICIT', 'UNVERIFIED'] for schema in schemas)
    assert re.search(r'subject_resolution.*(?:mandatory|required)', SYSTEM, re.I)
    assert re.search(r'never be empty, null, omitted', SYSTEM, re.I)


def test_prompt_gives_literal_pronoun_and_explicit_name_field_examples():
    objects = prompt_objects()
    assert {'subject': 'She', 'subject_resolution': 'UNVERIFIED'} in objects
    assert {'subject': 'Ada Rowan', 'subject_resolution': 'EXPLICIT'} in objects
    assert re.search(r'every pronoun.*including I/We/You.*UNVERIFIED', SYSTEM, re.I)
    assert re.search(r'not source facts', SYSTEM, re.I)


def test_prompt_preserves_sentence_id_and_original_source_boundaries():
    assert CONTRACT_VERSION == 'sentence-id/v2'
    assert EXTRACTION_KEYS == {'status', 'sentence_id', 'subject', 'subject_resolution',
                               'predicate', 'object', 'time', 'target_attribute',
                               'bridge_search_phrases'}
    assert 'Do not compute offsets or output a quote or chunk ID' in SYSTEM
    assert 'Never fill a name or date from the question or a preceding sentence' in SYSTEM
    assert 'Program code separately checks identity' in SYSTEM
    assert 'Do not answer' in SYSTEM and 'do not generate a search query' in SYSTEM


def test_prompt_separates_attached_relation_dates_from_complete_entity_name():
    assert re.search(r'object.*complete entity name, excluding attached temporal modifiers', SYSTEM, re.I)
    assert re.search(r'adjacent year/date clause.*copy.*verbatim into time', SYSTEM, re.I)
    assert re.search(r'time must be empty only when.*no date/year span', SYSTEM, re.I)
    assert {'object':'Orchard Arts Seminar', 'time':'2011–2014'} in prompt_objects()
    assert "Never move a later clause's dates into time" in SYSTEM
    assert 'strip numbers within the entity name' in SYSTEM


def test_exact_saved_real_empty_resolution_output_remains_rejected():
    assert hashlib.sha256(SAVED_INVALID_OUTPUT.encode('utf-8')).hexdigest() == SAVED_CONTENT_SHA256
    before = json.loads(SAVED_INVALID_OUTPUT)
    case, source = frozen_source()
    with pytest.raises(ValueError, match='^BRIDGE_JSON_FIELD_INVALID$'):
        validate_extraction(SAVED_INVALID_OUTPUT, fact_windows(source), {source.id: source}, case['query'])
    assert before['subject_resolution'] == ''
    assert json.loads(SAVED_INVALID_OUTPUT) == before


def test_exact_saved_b1_temporal_object_contamination_stays_unverified():
    assert hashlib.sha256(SAVED_B1_OUTPUT.encode('utf-8')).hexdigest() == SAVED_B1_SHA256
    original = json.loads(SAVED_B1_OUTPUT)
    case, source = frozen_source()
    result = validate_extraction(SAVED_B1_OUTPUT, fact_windows(source), {source.id:source}, case['query'])
    assert result['span_verified'] is True
    assert result['relation_form_verified'] is False and result['relation_verified'] is False
    assert result['object'] == original['object']
    assert result['time'] == original['time'] == ''
    assert json.loads(SAVED_B1_OUTPUT) == original


@pytest.mark.parametrize('invalid', ['', None, 'unverified', 'VERIFIED', 'UNKNOWN'])
def test_invalid_resolution_values_are_not_coerced_or_repaired(invalid):
    case, source = frozen_source()
    data = json.loads(SAVED_INVALID_OUTPUT)
    data['subject_resolution'] = invalid
    before = deepcopy(data)
    with pytest.raises(ValueError, match='^BRIDGE_JSON_FIELD_INVALID$'):
        validate_extraction(json.dumps(data), fact_windows(source), {source.id: source}, case['query'])
    assert data == before
