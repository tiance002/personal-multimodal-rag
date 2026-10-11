"""Offline scoring consumers. No ingestion, retrieval, judge, or telemetry I/O.

Gold identifiers remain source identifiers. Formal document/version identities
are supplied by the operator's frozen ingestion mapping, never inferred by text.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path


def read_frozen_json(path, *, expected_sha256):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('scoring_input_hash_mismatch')
    return json.loads(raw)


def hotpot_sources(rows):
    """Return every original context, retaining zero-based sentence offsets.

    Uses the exact source-ID algorithm in special_public_data. Offsets refer to
    newline-joined original sentences; repeated strings keep distinct offsets.
    Supporting facts are validated but never select/filter source contexts.
    """
    sources = {}
    for row in rows:
        titles, sentences = row['context']['title'], row['context']['sentences']
        if len(titles) != len(sentences) or len(set(titles)) != len(titles):
            raise ValueError('hotpot_context_alignment')
        for title, sents in zip(titles, sentences):
            if not isinstance(title, str) or any(not isinstance(s, str) for s in sents):
                raise ValueError('hotpot_context_invalid')
            key = hashlib.sha256((title+'\0'+json.dumps(sents, ensure_ascii=False, separators=(',', ':'))).encode()).hexdigest()
            source_id = 'hotpotqa-context:'+key
            spans, offset = [], 0
            for index, sentence in enumerate(sents):
                spans.append((index, offset, offset+len(sentence)))
                offset += len(sentence)+1
            sources[source_id] = {'title': title, 'text': '\n'.join(sents), 'sentence_spans': spans}
        facts = row['supporting_facts']
        if len(facts['title']) != len(facts['sent_id']):
            raise ValueError('hotpot_supporting_fact_alignment')
        for title, index in zip(facts['title'], facts['sent_id']):
            if title not in titles or type(index) is not int or not 0 <= index < len(sentences[titles.index(title)]):
                raise ValueError('hotpot_supporting_fact_out_of_source')
    return sources


class HotpotAnnotationMapper:
    """Callable for source_annotations_for_chunk; binds actual immutable version.

    bindings: [{source_document_id, document_id, version_id, normalized_text}].
    normalized_text must exactly equal the frozen original newline-joined text.
    Chunk locator start/end and quote/content must read back that same slice.
    Counts only fully covered nonempty sentences, never substring matching.
    """
    def __init__(self, sources, bindings):
        self._bindings = {}
        for binding in bindings:
            source = sources[binding['source_document_id']]
            key = (binding['document_id'], binding['version_id'])
            if key in self._bindings or not all(isinstance(x, str) and x for x in key):
                raise ValueError('hotpot_binding_identity_invalid')
            if binding['normalized_text'] != source['text']:
                raise ValueError('hotpot_normalized_offsets_not_proven')
            self._bindings[key] = (binding['source_document_id'], deepcopy(source))

    def __call__(self, chunk):
        source_id, source = self._bindings[(chunk.document_id, chunk.version_id)]
        start, end = chunk.locator.get('start'), chunk.locator.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source['text']):
            raise ValueError('hotpot_chunk_span_invalid')
        quote = chunk.locator.get('quote')
        if (quote if quote is not None else chunk.content) != source['text'][start:end]:
            raise ValueError('hotpot_chunk_span_readback_mismatch')
        return {'document_id': source_id, 'formal_document_id': chunk.document_id,
                'version_id': chunk.version_id, 'title': source['title'],
                'sentence_indices_zero_based': [i for i, a, b in source['sentence_spans'] if start <= a < b <= end]}


def mml_page_metadata(metadata, *, convention_contract=None):
    """Return scoring-only metadata; UNKNOWN unless frozen proof says 0 or 1.

    convention_contract: {gold_page_index_base: 0|1, evidence_sha256: sha,
    evidence_pointer: str}. Range validity alone is not convention proof.
    Original metadata/Gold are untouched. Operator must freeze authoritative
    documentation with its hash before passing this explicit contract.
    """
    result = deepcopy(metadata)
    result['page_index_convention'] = 'UNKNOWN'
    proof = convention_contract or {}
    sha = proof.get('evidence_sha256', '')
    if (type(proof.get('gold_page_index_base')) is int and proof['gold_page_index_base'] in (0, 1)
            and isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha)
            and isinstance(proof.get('evidence_pointer'), str) and proof['evidence_pointer']):
        result['page_index_convention'] = 'ZERO_BASED' if proof['gold_page_index_base'] == 0 else 'ONE_BASED'
        result['page_index_proof'] = deepcopy(proof)
    return result


def mml_annotation_for_chunk(chunk, *, source_document_id):
    """Normal SourceLocator.page is one-based (domain Field ge=1)."""
    page = chunk.locator.get('page')
    if type(page) is not int or page < 1:
        raise ValueError('mml_locator_page_unavailable')
    return {'document_id': source_document_id, 'pages_one_based': [page]}


def omni_prediction_from_normalized(page, normalized, *, source_image_sha256):
    """Export only parser-proven geometry and text; do not invent layout/order.

    NormalizedDocument or its JSON dump accepted. Missing bbox is an explicit
    export failure (prediction None), not zero layout quality. Text-only
    locators with actual bbox map to text_block; native explicit categories and
    html/latex/order are carried from raw_evidence when present.
    """
    n = normalized.model_dump() if hasattr(normalized, 'model_dump') else normalized
    if source_image_sha256 != page['image_sha256']:
        raise ValueError('omni_prediction_source_mismatch')
    blocks, missing = [], 0
    for locator in n['source_locators']:
        layout = (locator.get('raw_evidence') or {}).get('ocr_layout')
        if layout is not None:
            if (layout.get('schema') != 'image-ocr-layout-v1'
                    or layout.get('coordinate_basis') != 'image-pixels-top-left'
                    or layout.get('text_alignment') != 'EXACT_NATIVE_TEXT'):
                missing += 1
                continue
            for native in layout['blocks']:
                bbox = native['bbox']
                if (len(bbox) != 4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in bbox)
                        or not 0 <= bbox[0] < bbox[2] <= page['width']
                        or not 0 <= bbox[1] < bbox[3] <= page['height']):
                    raise ValueError('omni_parser_bbox_out_of_image')
                x0,y0,x1,y1 = bbox
                block = {'poly':[x0,y0,x1,y0,x1,y1,x0,y1],
                         'category_type':native['category_type'],'text':native['text']}
                if type(native.get('order')) is int:
                    block['order'] = native['order']
                blocks.append(block)
            continue
        bbox = locator.get('bbox')
        if bbox is None:
            missing += 1
            continue
        if len(bbox) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in bbox):
            raise ValueError('omni_parser_bbox_invalid')
        x0, y0, x1, y1 = bbox
        if not 0 <= x0 < x1 <= page['width'] or not 0 <= y0 < y1 <= page['height']:
            raise ValueError('omni_parser_bbox_out_of_image')
        raw = locator.get('raw_evidence') or {}
        category = raw.get('category_type', 'text_block' if locator['kind'] in ('text', 'image', 'pdf') else None)
        if not isinstance(category, str):
            missing += 1
            continue
        block = {'poly': [x0,y0,x1,y0,x1,y1,x0,y1], 'category_type': category}
        if isinstance(locator.get('quote'), str):
            block['text'] = locator['quote']
        for name in ('html', 'latex'):
            if isinstance(raw.get(name), str):
                block[name] = raw[name]
        if type(raw.get('order')) is int:
            block['order'] = raw['order']
        blocks.append(block)
    available = bool(blocks) and missing == 0
    return {'status': 'EXPORTED' if available else 'NOT_RUN_PARSER_GEOMETRY_UNAVAILABLE',
            'prediction': {'page_id': page['page_id'], 'image_sha256': source_image_sha256,
                           'blocks': blocks} if available else None,
            'counts': {'exported_blocks': len(blocks), 'unavailable_locators': missing},
            'official_qa_accuracy': 'NOT_AVAILABLE'}


# Every mapping is an existing computed number, with metric origin in its name.
SCORE_MAPPING = {
    'answer': {'normalized_exact_match': 'project_answer_exact_match', 'character_f1': 'project_answer_character_f1',
               'hotpot_answer_em': 'official_hotpot_answer_em', 'hotpot_answer_token_f1': 'official_hotpot_answer_token_f1'},
    'retrieval': {k: 'project_retrieval_'+k for k in ('recall_at_k','precision_at_k','f1_at_k','hit_at_k','mrr_at_k','map_at_k','ndcg_at_k')},
    'annotations': {k: 'project_'+k for k in ('supporting_fact_recall','supporting_fact_precision','supporting_fact_f1',
                                           'evidence_page_recall','evidence_page_precision','evidence_page_f1')},
    'parsing': {k: 'project_omni_'+k for k in ('layout_f1','text_normalized_edit_distance','table_normalized_edit_distance',
                                             'formula_normalized_edit_distance','reading_order_pairwise_accuracy')},
}
NUMERIC_SCORE_FIELDS = frozenset(v for group in SCORE_MAPPING.values() for v in group.values())


def computed_numeric_scores(*, answer=None, retrieval=None, annotations=None, parsing=None):
    """Accept metric dictionaries only; missing values omitted, true zero kept."""
    scores = {}
    for group, values in [('answer',answer), ('retrieval',retrieval), ('annotations',annotations), ('parsing',parsing)]:
        for key, name in SCORE_MAPPING[group].items():
            value = (values or {}).get(key)
            if value is None or isinstance(value, str):
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('computed_numeric_score_invalid')
            scores[name] = float(value)
    return scores
