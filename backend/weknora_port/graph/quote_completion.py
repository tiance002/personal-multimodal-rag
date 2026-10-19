"""Bounded source-sentence retrieval for a validated anchor's short quote.

This does not rewrite the model response or relax the extraction parser. A
derived quote is labelled as application-selected source text, and is passed
through the same strict proof parser before a new edge can be retained.
Literal quote/direction checks are not a semantic truth verdict.
"""
from copy import deepcopy
from dataclasses import replace
import json

from .extraction import (parse_extraction, _occurrences, _surrounding_statement,
                         _UNCERTAIN, _TEMPORAL)
from .types import stable_id


MAX_SENTENCE_CHARS = 1024
_DELIMITERS = '.!?。！？'


def _one_sentence_line(text):
    if not text or any(char in text for char in ('\r', '\n', ';', '；')):
        return False
    stripped = text.rstrip()
    # At most a terminal sentence delimiter is allowed. No abbreviation or
    # multi-sentence inference is attempted when punctuation is ambiguous.
    return not any(char in _DELIMITERS for char in stripped[:-1])


def _has_endpoint(quote, node):
    return any(_occurrences(quote, surface) for surface in (node.name, *node.aliases))


def parse_with_quote_completion(source, text, extraction_id):
    """Return (batch, mappings), preserving every original rejected suggestion.

Malformed JSON/schema and invalid entities are never repaired. Only a unique
short quote with a valid predicate and exactly one missing endpoint can obtain
its own enclosing source sentence, with a validated document-subject anchor.
"""
    baseline = parse_extraction(source, text, extraction_id)
    if any(item.get('kind') == 'entity' for item in baseline.rejected):
        return baseline, ()
    eligible = {item['index'] for item in baseline.rejected
                if item.get('kind') == 'relation' and
                item.get('reason') == 'DIRECTION_OR_PREDICATE_NOT_IN_QUOTE'}
    if not eligible:
        return baseline, ()
    # The first strict parse has already checked JSON keys/types/limits.
    original = json.loads(text)
    scope = (source.tenant_id, source.owner_id, source.knowledge_base_id,
             source.document_id, source.source_version, source.chunk_id)
    by_id = {node.id: node for node in baseline.nodes}
    by_key = {}
    for entity in original['entities']:
        node = by_id.get(stable_id('gn', *scope, entity['name'], entity['type'], entity['qualifier']))
        if node is not None:
            by_key[entity['key']] = node
    retained = {edge.id: edge for edge in baseline.edges}
    mappings = []
    for index in sorted(eligible):
        relation = original['relations'][index]
        subject, target = by_key.get(relation['source']), by_key.get(relation['target'])
        quote, predicate = relation['quote'], relation['predicate']
        if (subject is None or target is None or not subject.is_anchor or subject.id == target.id or
                source.content.count(quote) != 1 or not _one_sentence_line(quote) or
                not _occurrences(quote, predicate)):
            continue
        # Both-present directional mistakes and both-absent snippets are not a
        # missing-anchor quote problem and must remain rejected.
        if _has_endpoint(quote, subject) == _has_endpoint(quote, target):
            continue
        start = source.content.index(quote)
        sentence = _surrounding_statement(source.content, start, start + len(quote))
        if (not _one_sentence_line(sentence) or len(sentence) > MAX_SENTENCE_CHARS or
                source.content.count(sentence) != 1 or sentence.count(quote) != 1 or
                len(_occurrences(sentence, subject.name)) != 1 or
                relation['time'] or _TEMPORAL.search(sentence) or _UNCERTAIN.search(sentence)):
            continue
        derived_start = source.content.index(sentence)
        if not derived_start <= start < start + len(quote) <= derived_start + len(sentence):
            continue
        trial = deepcopy(original)
        trial['relations'][index]['quote'] = sentence
        replay = parse_extraction(source, json.dumps(trial, ensure_ascii=False), extraction_id)
        expected_id = stable_id('ge', *scope, subject.id, target.id, predicate, sentence, relation['time'])
        new_edge = next((edge for edge in replay.edges if edge.id == expected_id), None)
        if new_edge is None or new_edge.id in retained:
            continue
        retained[new_edge.id] = new_edge
        mappings.append({'kind': 'SOURCE_SENTENCE_EXTENSION_NOT_LLM_QUOTE',
            'relation_index': index, 'original_quote': quote,
            'original_quote_start': start, 'original_quote_end': start + len(quote),
            'derived_quote': sentence, 'derived_quote_start': derived_start,
            'derived_quote_end': derived_start + len(sentence),
            'offset_space': 'chunk_local_unicode_codepoints',
            'chunk_id': source.chunk_id, 'source_version': source.source_version,
            'source_sha256': source.content_sha256, 'edge_id': new_edge.id,
            'verification': new_edge.verification})
    if not mappings:
        return baseline, ()
    # Rejected records refer to the untouched real response, so they stay even
    # when a separately labelled source-sentence extension supplies a new edge.
    return replace(baseline, edges=tuple(retained.values())), tuple(mappings)
