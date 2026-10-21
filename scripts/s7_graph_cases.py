"""Freeze S7 questions, the shared corpus and post-response criteria; no sends.

This is an offline diagnostic plan builder, not a graph builder or an evaluator.
Gold and source-locator expectations are kept outside each request payload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


FRANKLIN = '4846280b-37b3-4fbf-97c9-e4d798b9009c'
LEYDEN = '9d440f3c-021d-4c9b-adc5-6e7084fc8886'
SELECTED_DOCS = (
    FRANKLIN, LEYDEN, 'a973df4a-0b4b-43f7-ad17-55265aac1556', '3fdf4942-1265-4e9e-959d-27772f57e5dd',
    '03a732df-64ad-4044-a2c6-caebf2409e7c', '443629fe-7970-4c67-b132-e5574c771ade',
    '73ea918d-bbdf-4a8e-ad9c-065101af492f', '35a50ce3-5f0a-449e-939c-318294480751',
    'a9aa34bf-0b2b-4bce-b0d4-fa1c18a8b253', '4c4ac0af-303d-4ef8-8456-da62c9996153',
    '60de9db0-996d-4348-aba7-af0ed176b98e', '5965c37f-9b18-4202-ac70-c6cf75132fb6',
)
DISTRACTOR_DOCS = set(SELECTED_DOCS[-2:])
OFFICIAL = (
    ('frost', '5a7631cd5542994ccc91870c'),
    ('romeo', '5a8ad6805542992d82986f93'),
    ('books-holdout', '5a745a6055429974ef308be4'),
    ('tribometer-holdout', '5a729fe55542991f9a20c53b'),
)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(pool, document_id, literal):
    matches = []
    for row in pool['rows']:
        if row['knowledge_id'] != document_id or row['chunk_type'] not in ('text', 'image_ocr'):
            continue
        start = row['content'].find(literal)
        if start >= 0:
            matches.append({'chunk_id': row['id'], 'document_id': document_id,
                            'chunk_local_start': start, 'chunk_local_end': start + len(literal),
                            'quote': literal, 'chunk_content_sha256': sha(row['content'].encode()),
                            'source_version': next(d['source_version'] for d in pool['documents'] if d['id'] == document_id)})
    if not matches:
        raise ValueError('S7_SCORING_LITERAL_NOT_IN_CURRENT_RAW_SOURCE')
    return {'literal': literal, 'matches': matches, 'use': 'POST_RESPONSE_SOURCE_SUPPORT_REVIEW_ONLY'}


def request(question, kb):
    return {'content': question, 'knowledge_base_scope': [kb], 'document_scope': [], 'mode': 'quick', 'reasoning_effort': 'off'}


def freeze(args):
    pool, questions, state, candidate = read(args.pool), read(args.cases), read(args.state), read(args.candidate)
    if pool['tenant'] != 332 or len(pool['documents']) != 24 or len(pool['rows']) != 59:
        raise ValueError('S7_SHARED_CORPUS_IDENTITY_MISMATCH')
    kb = pool['knowledge_base_id']
    raw = [r for r in pool['rows'] if r['chunk_type'] in ('text', 'image_ocr')]
    if len(raw) != 35:
        raise ValueError('S7_RAW_CHUNK_COUNT_CHANGED')
    historical_docs = state['datasets']['hotpotqa']['documents']
    original_by_sha = {d['source']['sha256']: d['source'] for d in historical_docs.values()}
    originals = []
    for document in pool['documents']:
        origin = original_by_sha[document['file_hash']]
        path = Path(origin['path'])
        original = path.read_bytes()
        if sha(original) != document['file_hash']:
            raise ValueError('S7_ORIGINAL_FILE_CHANGED')
        rows = [r for r in raw if r['knowledge_id'] == document['id']]
        if len(rows) > 12 or sum(len(r['content']) for r in rows) > 32768:
            raise ValueError('S7_PER_DOCUMENT_GRAPH_BOUNDS_EXCEEDED')
        originals.append({**document, 'original_path': str(path), 'original_bytes': len(original),
                          'sha256_verified': True, 'historical_source_id': origin['source_id'],
                          'raw_chunks': [{'chunk_id': r['id'], 'start_at': r['start_at'], 'end_at': r['end_at'],
                                          'chars': len(r['content']), 'sha256': sha(r['content'].encode()),
                                          'source_slice_equal': original.decode('utf-8')[r['start_at']:r['end_at']] == r['content']}
                                         for r in rows],
                          'selected_for_graph_extraction': document['id'] in SELECTED_DOCS,
                          'selection_role': 'DISTRACTOR' if document['id'] in DISTRACTOR_DOCS else 'CASE_SOURCE' if document['id'] in SELECTED_DOCS else 'SEARCH_POOL_DISTRACTOR_NOT_GRAPH_INDEXED'})
    vector_by_chunk = {v['chunk_id']: v for v in pool['vectors']}
    if not all(vector_by_chunk.get(r['id'], {}).get('has_vector') is True
               and vector_by_chunk[r['id']]['dimension'] > 0 for r in raw):
        raise ValueError('S7_CURRENT_RAW_VECTOR_MISSING')
    cases = []
    for identifier, question in (
        ('franklin-en', 'What type of electricity is stored by the device Franklin bells are designed to work with? Answer according to the knowledge base.'),
        ('franklin-mixed', 'Franklin bells配合使用的装置储存的是什么电？请依据知识库回答。'),
    ):
        cases.append({'id': identifier, 'kind': 'ENGINEERING_TWO_HOP_NOT_OFFICIAL_QA',
                      'request': request(question, kb), 'fact_group': 'franklin-leyden-storage',
                      'scoring_only': {'answer_points': ['static electricity'], 'all_required_documents': [FRANKLIN, LEYDEN],
                                       'facts': [fact(pool, FRANKLIN, 'designed to work with a Leyden jar'),
                                                 fact(pool, LEYDEN, '"stores" static electricity')],
                                       'pass_rule': 'Both source relationships delivered and answer identifies static electricity; general-knowledge-only correctness does not pass evidence support.'},
                      'planned_validation': 'NORMAL_API_ON_THEN_FIXED_OFF_FROM_SAME_ACTUAL_PREPARATION' if identifier == 'franklin-mixed' else 'OFFLINE_LANGUAGE_VARIANT_NO_EXTRA_GENERATION'})
    indexed_questions = {q['qid']: q for q in questions if q['dataset'] == 'hotpotqa'}
    for identifier, qid in OFFICIAL:
        original = indexed_questions[qid]
        if original['knowledge_base_id'] != kb or original['document_scope'] != []:
            raise ValueError('S7_OFFICIAL_CASE_SCOPE_CHANGED')
        cases.append({'id': identifier, 'kind': 'FROZEN_HOTPOT_QUESTION_DIAGNOSTIC', 'qid': qid,
                      'request': request(original['question'], kb),
                      'holdout_from_s7_path_rule_design': identifier.endswith('holdout'),
                      'scoring_only': {'answers': original['answers'], 'qrels': original['qrels'],
                                       'all_required_documents': list(original['qrel_source_to_document'].values()),
                                       'supporting_facts': original['evaluation_metadata']['supporting_facts'],
                                       'metric_rule': 'Report any-source and all-required-source separately; retain official answer EM/F1 separately from fact support. Not a new official full-dataset score.'},
                      'planned_validation': 'FIXED_SAVED_M4_PREPARATION_GRAPH_OFF_ON_GENERATION'})
    cases.append({'id': 'leyden-single-hop', 'kind': 'ENGINEERING_SINGLE_HOP',
                  'request': request('What type of electricity does a Leyden jar store? Please answer using the knowledge base.', kb),
                  'scoring_only': {'answer_points': ['static electricity'], 'all_required_documents': [LEYDEN],
                                   'facts': [fact(pool, LEYDEN, '"stores" static electricity')]},
                  'planned_validation': 'OFFLINE_EXISTING_SOURCE_REGRESSION_NO_EXTRA_GENERATION'})
    cases.append({'id': 'franklin-maintenance-unanswerable', 'kind': 'CONSTRUCTED_MISSING_FACT_DIAGNOSTIC_NOT_OFFICIAL_GOLD',
                  'request': request('After how many days do Franklin bells require maintenance? Please answer only from this knowledge base.', kb),
                  'scoring_only': {'answer_points': [], 'all_required_documents': [],
                                   'missing_fact': 'The available frozen 24-document text pool does not state a required maintenance interval in days for Franklin bells.',
                                   'pass_rule': 'Explain that the supplied sources do not provide the interval; no invented number, interval or advice attributed to sources.',
                                   'scope_limit': 'This is a claim about this bounded public text pool, not proof that no maintenance requirement exists outside it.',
                                   'lexical_diagnostic': {term: sum(term in r['content'].casefold() for r in raw)
                                                          for term in ('maintenance', 'service interval', 'inspection interval')}},
                  'planned_validation': 'NORMAL_API_ON_THEN_FIXED_OFF_FROM_SAME_ACTUAL_PREPARATION'})
    selected = [d for d in originals if d['id'] in SELECTED_DOCS]
    if len(selected) != 12:
        raise ValueError('S7_TWELVE_SELECTED_DOCUMENTS_MISSING')
    roles = pool['roles']
    result = {'schema': 's7-graph-targeted-plan/v1', 'status': 'FROZEN_ZERO_SEND_AUTHORIZATION_NOT_CREATED',
              'input_sha256': {str(path): sha(Path(path).read_bytes()) for path in (args.pool, args.cases, args.state, args.candidate)},
              'tenant_id': 332, 'subject_sha256': pool['subject_sha256'], 'knowledge_base_id': kb,
              'all_case_document_scope': [], 'shared_search_corpus_documents': len(originals),
              'shared_rows': len(pool['rows']), 'shared_raw_rows': len(raw),
              'corpus_originals': originals, 'cases': cases,
              'graph_build_plan': {'selected_documents': list(SELECTED_DOCS), 'selected_document_count': 12,
                                   'selected_raw_chunks': sum(len(d['raw_chunks']) for d in selected),
                                   'selected_raw_characters': sum(r['chars'] for d in selected for r in d['raw_chunks']),
                                   'distractor_document_ids': sorted(DISTRACTOR_DOCS),
                                   'minimum_extract_calls_one_per_raw_chunk': sum(len(d['raw_chunks']) for d in selected),
                                   'all_24_document_extract_calls_if_separately_authorized': len(raw),
                                   'source_ids_from_s6_must_not_be_reused': True,
                                   'selection_limit': 'Targeted subset, not blind full-corpus graph quality evaluation. Search pool remains all 24 documents for every case.'},
              'models': {'default_roles_read_only': roles, 'archived_qwen_readiness_reference': str(args.candidate),
                         'archived_qwen': candidate, 'no_default_role_changes': True,
                         'graph_extraction_candidate_requires_owner_authorization': 'existing DeepSeek deepseek-flash; do not silently substitute daily roles'},
              'suggested_cloud_operations_not_authorization': {'graph_extractions': 19, 'fixed_m4_generations': 8,
                  'fixed_off_diagnostics_from_new_api_preparation': 2, 'normal_api_requests': 2,
                  'normal_api_auxiliary_provider_estimate_max': 14, 'graph_branch_estimated_provider_max': 43,
                  'combined_with_independent_top1_estimated_provider_max': 56,
                  'root_requested_envelope': {'max_provider_calls': 60, 'max_tokens': 500000},
                  'note': 'Counts are a finite plan, not an authorization or replacement for existing per-execution SendGate/fences; no repeated renewal to evade parent total.'},
              'gold_handling': 'Only each case.request may be submitted. scoring_only, corpus expectations and QID metadata never enter prompts, extraction or runtime path selection.',
              'source_slice_check_mismatches': [{'document_id': d['id'], **row} for d in originals for row in d['raw_chunks'] if not row['source_slice_equal']],
              'provider_calls': 0, 'database_writes': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pool', 'cases', 'state', 'candidate', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    result = freeze(args)
    print(json.dumps({'status': result['status'], 'case_count': len(result['cases']),
                      'graph_build_plan': result['graph_build_plan'],
                      'source_slice_check_mismatches': len(result['source_slice_check_mismatches']),
                      'output': str(args.output), 'provider_calls': 0}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
