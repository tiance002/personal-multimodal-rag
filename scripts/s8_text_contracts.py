"""S8 zero-send inventory of existing SciFact, MIRACL-ZH and LongBench contracts.

Reads immutable public assets and saved receipts only. No API/DB/model imports,
no resampling, no indexing, no historical response or Gold writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ('scifact', 'miracl-zh', 'longbench-zh')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def positive_sources(qrels):
    return sorted(key for key, grade in qrels.items() if float(grade) > 0)


def historical_status(row):
    """Derived explanation, never rewrite the saved raw terminal fields."""
    if row.get('unknown_call_count', 0):
        return 'UNKNOWN'
    if str(row.get('status', '')).startswith('NOT_RUN'):
        return row['status']
    assistant = row.get('assistant_message') or {}
    if assistant.get('turn_error_code') or assistant.get('turn_status') in ('error', 'cancelled'):
        return 'FAILED_OR_CANCELLED'
    if row.get('terminal_status') == 'complete' and assistant.get('is_completed') is True:
        return 'COMPLETE_DERIVED_FROM_SSE_AND_PERSISTENCE'
    return 'UNVERIFIED'


class Inputs:
    def __init__(self):
        self.identities = {}

    def raw(self, path):
        path = Path(path)
        raw = path.read_bytes()
        self.identities[str(path)] = {'sha256': digest(raw), 'bytes': len(raw)}
        return raw

    def obj(self, path):
        return json.loads(self.raw(path))

    def lines(self, path):
        return [json.loads(line) for line in self.raw(path).decode('utf-8').splitlines() if line.strip()]

    def ref(self, path):
        path = Path(path)
        if str(path) not in self.identities:
            self.raw(path)
        return {'path': str(path), **self.identities[str(path)]}


def history_summary(row, raw_path, line):
    calls = []
    for call in row.get('provider_calls', []):
        usage = call.get('usage') or {}
        calls.append({key: call.get(key) for key in
            ('call_id', 'request_id', 'capability', 'provider', 'model', 'model_id', 'outcome', 'created_at', 'finished_at')} |
            {'usage': {key: usage.get(key) for key in ('status', 'prompt_tokens', 'completion_tokens', 'total_tokens')}})
    return {'path': str(raw_path), 'jsonl_line': line, 'raw_status': row.get('status'),
            'terminal_status': row.get('terminal_status'), 'derived_status': historical_status(row),
            'session_id': row.get('session_id'), 'request_id': row.get('request_id'),
            'elapsed_seconds': row.get('elapsed_seconds'), 'unknown_call_count': row.get('unknown_call_count'),
            'provider_calls': calls, 'stage_capture_path': row.get('stage_capture_path'),
            'source_version_scope': row.get('expected_documents', []), 'counts_are_historical_not_new': True}


def inventory(args):
    inputs = Inputs()
    frozen = inputs.obj(args.frozen_cases)
    gold = inputs.obj(args.gold)
    states = inputs.obj(args.source_state)['datasets']
    history_by_id = {}
    # Hash the whole old file once; copy only safe identities/usage, never request headers/config.
    for number, row in enumerate(inputs.lines(args.history), 1):
        if row.get('dataset') in DATASETS:
            history_by_id[row['case_id']] = history_summary(row, args.history, number)
    package_index = inputs.obj(args.packages / 'six-package-manifest.json')
    source_contract = inputs.obj(args.source_manifest)
    current = inputs.obj(args.current_inventory) if args.current_inventory else None
    tenant = next((row for row in current['identities'] if row['tenant_id'] == 332), None) if current else None
    current_docs = {row['id']: row for row in tenant['documents']} if tenant else {}
    addendum = inputs.obj(args.current_addendum) if args.current_addendum else None
    extra = next((row for row in addendum['identities'] if row['tenant_id'] == 332), None) if addendum else None
    version_by_chunk = {row['id']: row for row in extra['chunk_version_status']} if extra else {}
    gold_by_dataset = {row['name']: row for row in gold['datasets']}
    datasets = []
    for name in DATASETS:
        base = args.adapters / name
        manifest = inputs.obj(base / 'manifest.json')
        corpus_rows = inputs.lines(base / 'corpus.jsonl')
        corpus = {row['doc_id']: {'line': n, 'title': row.get('title', ''),
            'text_sha256': digest(row['text'].encode('utf-8'))} for n, row in enumerate(corpus_rows, 1)}
        development = inputs.lines(base / 'cases/development.jsonl')
        holdout = inputs.lines(base / 'cases/locked_holdout.jsonl')
        for reference in manifest['files']:
            actual = inputs.ref(base / reference['path'])
            if actual['sha256'] != reference['sha256'] or actual['bytes'] != reference['bytes']:
                raise ValueError('ADAPTER_FILE_IDENTITY_MISMATCH:' + name + ':' + reference['path'])
        package = next(row for row in package_index['packages'] if row['dataset'] == name)
        package_manifest = inputs.obj(args.packages / package['manifest']['path'])
        archive_ref = inputs.ref(args.packages / package['archive']['path'])
        if archive_ref['sha256'] != package['archive']['sha256']:
            raise ValueError('SOURCE_ARCHIVE_IDENTITY_MISMATCH:' + name)
        units = {unit['source']['source_document_id']: unit for unit in package_manifest['units']}
        state = states[name]
        known_sources = state['documents']
        source_bindings = {}
        with zipfile.ZipFile(args.packages / package['archive']['path']) as archive:
            for source_id, document in known_sources.items():
                source = document['source']
                original = inputs.ref(Path(source['path']))
                if original['sha256'] != source['sha256']:
                    raise ValueError('FROZEN_ORIGINAL_SHA_MISMATCH:' + name + ':' + source_id)
                unit = units.get(source_id)
                package_proof = {'status': 'NOT_IN_TRANSPORT_PACKAGE'}
                if unit:
                    ref = unit['source']['artifact']
                    raw = archive.read(ref['path'])
                    if digest(raw) != ref['sha256'] or len(raw) != ref['bytes']:
                        raise ValueError('PACKAGE_ORIGINAL_BYTES_MISMATCH:' + name + ':' + source_id)
                    package_proof = {'status': 'EXACT_ORIGINAL_SHA_MATCH' if ref['sha256'] == original['sha256'] else 'DIFFERENT_SOURCE_PROJECTION',
                                     'artifact': ref, 'provenance': unit['source']['provenance']}
                source_bindings[source_id] = {'source_id': source_id, 'document_id': document['id'],
                    'knowledge_base_id': state['kb'], 'original': original,
                    'prepared_corpus': corpus.get(source_id), 'transport': package_proof}
                if tenant:
                    live = current_docs.get(document['id'])
                    valid = bool(live and live['tenant_id'] == 332 and live['knowledge_base_id'] == state['kb']
                        and live['file_hash'] == original['sha256'] and live['parse_status'] == 'completed'
                        and live['enable_status'] == 'enabled' and live['source_version'])
                    original_text = Path(source['path']).read_text(encoding='utf-8')
                    chunks = []
                    for row in tenant['chunks']:
                        if row['knowledge_id'] != document['id']:
                            continue
                        start, end = row.get('start_at'), row.get('end_at')
                        source_match = (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(original_text)
                                        and original_text[start:end] == row['content'])
                        chunks.append({key: row.get(key) for key in ('id', 'chunk_type', 'chunk_index', 'start_at', 'end_at',
                            'parent_chunk_id', 'pre_chunk_id', 'next_chunk_id', 'index_status', 'is_enabled', 'context_header')} |
                            {'content_sha256': digest(row['content'].encode('utf-8')),
                             'persisted_source_version': version_by_chunk.get(row['id'], {}).get('chunk_source_version'),
                             'original_offset_check': ('DERIVED_SUMMARY_NOT_ORIGINAL' if row['chunk_type'] == 'summary'
                                                       else 'EXACT_ORIGINAL_SLICE_MATCH' if source_match else 'NOT_EXACT_REQUIRES_NORMALIZED_SOURCE_PROOF')})
                    vectors = [row for row in extra['document_vectors'] if row['knowledge_id'] == document['id'] and row['knowledge_base_id'] == state['kb']] if extra else []
                    vector_ready = bool(vectors) and all(row['dimension'] == 1024 and row['count'] == row['nonnull'] == row['enabled']
                                                         and row['count'] > 0 for row in vectors)
                    source_bindings[source_id]['current_S8'] = {'document': live, 'source_identity_valid': valid,
                        'chunks': chunks, 'all_chunks_enabled_ready': bool(chunks) and all(r['is_enabled'] and r['index_status'] == 'ready' for r in chunks),
                        'per_document_vectors': vectors, 'per_document_vector_ready': vector_ready,
                        'vector_row_count_equals_chunk_count': sum(row['count'] for row in vectors) == len(chunks) if extra else None,
                        'raw_chunk_source_versions_match_current': all(r['persisted_source_version'] == live['source_version'] for r in chunks if r['chunk_type'] == 'text') if extra and live else None,
                        'vector_chunk_ID_bijection': 'NOT_IN_AGGREGATE_AUDIT; per-document enabled non-null1024 counts checked'}
        frozen_cases = []
        development_by_id = {row['qid']: (n, row) for n, row in enumerate(development, 1)}
        original_gold = {row['qid']: row for row in gold_by_dataset[name]['cases']}
        for number, case in enumerate(frozen, 1):
            if case['dataset'] != name:
                continue
            line, adapter_case = development_by_id[case['qid']]
            original = original_gold[case['qid']]
            for key, default in (('question', ''), ('qrels', {}), ('answers', [])):
                if not (case.get(key, default) == adapter_case.get(key, default) == original.get(key, default)):
                    raise ValueError('FROZEN_GOLD_OR_QUESTION_MISMATCH:' + case['case_id'] + ':' + key)
            positives = positive_sources(case['qrels'])
            documents = []
            for expected in case['expected_documents']:
                binding = source_bindings.get(expected['source_id'])
                documents.append({**expected, 'frozen_original': binding,
                    'identity_match': bool(binding and binding['document_id'] == expected['id']
                                           and binding['original']['sha256'] == expected['file_hash'])})
            frozen_cases.append({key: case.get(key) for key in ('case_id', 'qid', 'question', 'knowledge_base_id',
                'document_scope', 'dataset_version', 'qrels_kind', 'qrels', 'answers', 'evaluation_metadata', 'unbound_qrels')} |
                {'gold_references': {'frozen_cases_line': number, 'adapter_development_line': line,
                                    'adapter_path': str(base / 'cases/development.jsonl'), 'frozen_gold_path': str(args.gold)},
                 'question_gold_match_three_frozen_sources': True,
                 'all_positive_sources_bound_in_saved_scope': all(src in source_bindings for src in positives),
                 'positive_source_count': len(positives), 'expected_documents': documents,
                 'current_versions_equal_M4_freeze': (all(current_docs.get(r['id'], {}).get('source_version') == r['source_version']
                                                            for r in case['expected_documents']) if tenant else None),
                 'history': history_by_id.get(case['case_id'], {'derived_status': 'MISSING_SAVED_RECEIPT'})})
        catalog = []
        if len({row['qid'] for row in development}) != len(development) or len({row['qid'] for row in holdout}) != len(holdout):
            raise ValueError('DUPLICATE_QID_WITHIN_PREPARED_SPLIT:' + name)
        overlap = sorted({row['qid'] for row in development} & {row['qid'] for row in holdout})
        for split, line, row in [(split, n, row) for split, rows in (('development', development), ('locked_holdout', holdout))
                                  for n, row in enumerate(rows, 1)]:
            positives = positive_sources(row['qrels'])
            catalog.append({'qid': row['qid'], 'question': row['question'], 'qrels': row['qrels'],
                'answers': row.get('answers', []), 'split': split, 'source_line': line,
                'locked_evaluation_only': split == 'locked_holdout', 'S8_3_tuning_eligible': split != 'locked_holdout',
                'gold_reference': str(base / 'cases' / (split + '.jsonl')),
                'source_ids_in_prepared_corpus': [src for src in row['qrels'] if src in corpus],
                'all_positive_sources_in_saved_KB': bool(positives) and all(src in source_bindings for src in positives),
                'all_positive_sources_in_current_S8_KB': (bool(positives) and all(source_bindings.get(src, {}).get('current_S8', {}).get('source_identity_valid')
                    and source_bindings.get(src, {}).get('current_S8', {}).get('all_chunks_enabled_ready')
                    and (source_bindings.get(src, {}).get('current_S8', {}).get('per_document_vector_ready') if extra else True) for src in positives)) if tenant else None,
                'historical_UNKNOWN_disposition': (history_by_id.get(f'{name}-{row["qid"]}', {}).get('derived_status')
                    if 'UNKNOWN' in history_by_id.get(f'{name}-{row["qid"]}', {}).get('derived_status', '') else None),
                'fresh_send_authorization': 'NOT_GRANTED_BY_THIS_INVENTORY; do not resend historical UNKNOWN operation',
                'missing_positive_sources_in_saved_KB': [src for src in positives if src not in source_bindings]})
        metrics = ({'retrieval': 'source-document Recall/MRR on explicitly declared indexed subset',
                    'generation': 'NOT_APPLICABLE_NO_GENERATION_ANSWER_GOLD',
                    'support_refute': 'NOT_AVAILABLE_IN_LOCAL_BEIR_EXPORT; qrel=1 is relevant, not SUPPORT'} if name == 'scifact' else
                   {'retrieval': 'positive-passage Recall/MRR on declared candidate/index pool; grade0 never positive',
                    'generation': 'NOT_APPLICABLE_NO_GENERATION_ANSWER_GOLD',
                    'full_corpus_metrics': 'NOT_CLAIMED_ONLY_ONE_SHARD_AND_PROJECT_POOL_AVAILABLE'} if name == 'miracl-zh' else
                   {'retrieval': 'paired-context identity diagnostic, not official qrels or exact fact recall',
                    'generation': {'dureader': 'official rouge_zh_score (ROUGE-L)', 'multifieldqa_zh': 'official qa_f1_zh_score'},
                    'aggregate': 'REPORT_TASKS_SEPARATELY; preserve full answer and semantic/source review'})
        current_positive = [row['qid'] for row in catalog if row['split'] == 'development' and row['all_positive_sources_in_current_S8_KB']]
        holdout_positive = [row['qid'] for row in catalog if row['split'] == 'locked_holdout' and row['all_positive_sources_in_current_S8_KB']]
        datasets.append({'dataset': name, 'dataset_version': manifest['dataset_version'], 'counts': {
            'frozen_QIDs': len(frozen_cases), 'prepared_corpus_documents': len(corpus),
            'prepared_development_QIDs': len(development), 'locked_holdout_QIDs': len(holdout),
            'transport_original_units': len(units), 'saved_KB_documents': len(source_bindings),
            'development_QIDs_all_positive_sources_in_saved_KB': sum(c['all_positive_sources_in_saved_KB'] for c in catalog if c['split'] == 'development'),
            'development_QIDs_all_positive_sources_in_current_S8_KB': len(current_positive) if tenant else None,
            'development_QIDs_missing_required_current_sources': len(development) - len(current_positive) if tenant else None,
            'holdout_QIDs_all_positive_sources_in_current_S8_KB': len(holdout_positive) if tenant else None,
            'holdout_QIDs_missing_required_current_sources': len(holdout) - len(holdout_positive) if tenant else None,
            'all_prepared_QIDs': len(catalog), 'all_prepared_QIDs_all_positive_sources_in_current_S8_KB': len(current_positive) + len(holdout_positive) if tenant else None,
            'all_prepared_QIDs_missing_required_current_sources': len(catalog) - len(current_positive) - len(holdout_positive) if tenant else None,
            'prepared_corpus_sources_missing_from_current_dataset_KB': len(set(corpus) - set(source_bindings)) if tenant else None},
            'metric_contract': metrics, 'adapter_manifest': inputs.ref(base / 'manifest.json'),
            'transport_archive': archive_ref, 'transport_manifest': inputs.ref(args.packages / package['manifest']['path']),
            'source_full_scope_reference': next(row for row in source_contract['datasets'] if row['dataset'] == name),
            'saved_source_bindings': source_bindings, 'frozen_cases': frozen_cases,
            'all_prepared_development_cases': [row for row in catalog if row['split'] == 'development'],
            'all_prepared_locked_holdout_cases': [row for row in catalog if row['split'] == 'locked_holdout'],
            'split_qid_overlap': overlap,
            'current_mapped_development_QIDs': current_positive,
            'current_mapped_locked_holdout_QIDs': holdout_positive,
            'current_KB_vector_aggregate': [row for row in tenant['vectors'] if row['knowledge_base_id'] == state['kb']] if tenant else None,
            'saved_index_status': 'CURRENT_ROOT_READONLY_PG_AND_PER_DOC_VECTOR_AGGREGATE_JOINED' if extra else 'CURRENT_ROOT_READONLY_PG_AUDIT_JOINED; PER_DOC_VECTOR_ID_NOT_IN_AUDIT' if tenant else 'HISTORICAL_ONLY_PENDING_CURRENT_AUDIT',
            'full_public_benchmark': 'NOT_CLAIMED'} )
    return {'schema': 's8-text-contract-inventory/v1', 'source_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'status': 'ZERO_SEND_CONTRACTS_READY_PENDING_OWNER_STAGE_AUTHORIZATION' if tenant else 'ZERO_SEND_PREPARED_PENDING_CURRENT_PG_AND_OWNER_STAGE_AUTHORIZATION', 'new_provider_calls': 0,
        'historical_unknowns_unchanged': True, 'gold_only_for_post_response_scoring': True,
        'datasets': datasets, 'input_identities': inputs.identities,
        'current_model_roles': tenant['models'] if tenant else None,
        'current_archived_qwen_candidate': tenant.get('existing_qwen_candidate') if tenant else None,
        'current_memory_scope': extra.get('memory') if extra else None,
        'provider_upper_bound': {'conditions': 'single legal KB; text-only; existing embedding group1; threshold0.3; no upload or Graph extraction',
            'query_understanding': 1, 'query_embedding': 6, 'rerank': 1, 'generation_including_upgrade': 2, 'per_question_total': 10,
            'code_proof': ['retrieval/query.py:expand_queries returns out[:5]', 'retrieval/search.py:group+expand per target',
                'storage/hybrid.py:embedding when query vector absent', 'retrieval/rerank.py:second invoke only threshold>0.3',
                'generation/coordinator.py:for ordinal in (1, 2)'],
            'token_upper_bound': 'NOT_ESTIMATED_FROM_HISTORICAL_AVERAGES; freeze actual wire recipes/source limits before admission'}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapters', type=Path, default=Path('D:/RAG-Public-Bench/prepared/adapters'))
    parser.add_argument('--packages', type=Path, default=Path('D:/RAG-v6-final-ingestion-pack-r1/attempt3-ocr-layout'))
    parser.add_argument('--frozen-cases', type=Path, default=Path('E:/WK/run/m4-full-sweep-20261018/qa_cases.json'))
    parser.add_argument('--gold', type=Path, default=Path('E:/WK/run/p9-20261016/gold-all.json'))
    parser.add_argument('--source-state', type=Path, default=Path('E:/WK/run/p19-20261016/state.json'))
    parser.add_argument('--source-manifest', type=Path, default=Path('E:/RAG quention/var/reports/v6-full40-evaluation-entry-r4-r17/frozen-local/source_manifest_40.json'))
    parser.add_argument('--history', type=Path, default=Path('E:/WK/run/m4-full-sweep-20261018/api-execution/qa_results.jsonl'))
    parser.add_argument('--current-inventory', type=Path)
    parser.add_argument('--current-addendum', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_absolute() or output.is_relative_to(ROOT):
        raise ValueError('EXTERNAL_INVENTORY_OUTPUT_REQUIRED')
    result = inventory(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'datasets': {row['dataset']: row['counts'] for row in result['datasets']},
                      'new_provider_calls': 0, 'output': str(output)}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
