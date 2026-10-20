"""Read saved first S8.1 case only; no runtime/network/database operations."""
import hashlib
import json
from pathlib import Path
import zipfile
import s8_1_score as scoring

ROOT = scoring.BASE.parent / 'first-case-evidence'


def compact(value):
    return ''.join(value.split())


def frames(path):
    result = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if line.startswith('data:'):
            raw = line[5:].strip()
            if raw and raw != '[DONE]':
                result.append(json.loads(raw))
    return result


def rank(rows, target):
    return [{'chunk_rank': n, 'chunk_id': r.get('chunk_id', r.get('id')), 'score': r.get('score')}
            for n, r in enumerate(rows, 1) if r.get('knowledge_id') == target]


def doc_metrics(rows, case, k=10):
    binding = {r['document_id']: r['source_id'] for r in case['qrel_source_mappings']}
    ids = []
    for row in rows:
        doc = row['knowledge_id']
        source = binding.get(doc, 'UNMAPPED_DOCUMENT:' + doc)
        if source not in ids:
            ids.append(source)
    return {'k': k, 'ranked_source_document_ids': ids,
            'metrics': scoring.ranking_metrics(ids, case['gold']['qrels'], k=k)}


def main():
    case_dir = next((ROOT / 'cases').iterdir())
    api_dir = next((case_dir / 'api-cases').iterdir())
    execution_dir = next((ROOT / 'executions').iterdir())
    evidence = execution_dir / 'evidence'
    result = scoring.read(api_dir / 'result.json')
    stage_path = next(evidence.glob('*.stages.json'))
    stage = scoring.read(stage_path)
    retrieval_path = next(evidence.glob('*.retrieval.json'))
    retrieval = scoring.read(retrieval_path)
    pg = scoring.read(case_dir / 'pg-final-after-stop.json')
    ledger = scoring.read(execution_dir / 'execution.json')
    frozen = next(c for c in scoring.read(scoring.GOLD)['cases'] if c['identity']['case_id'] == 'scifact-1304')
    target = frozen['qrel_source_mappings'][0]['document_id']
    requests = [(p, scoring.read(p)) for p in evidence.glob('*.request.json')]
    final_path, final = next((p, r) for p, r in requests if '/answer/' in r['request_id'])
    qu_path, qu = next((p, r) for p, r in requests if r['request_id'].endswith('/query_rewrite'))
    rr_path, rr = next((p, r) for p, r in requests if r['capability'] == 'rerank')
    rerank_raw = scoring.read(rr_path.with_name(rr_path.name.replace('.request.json', '.response.body')))
    qu_raw = scoring.read(qu_path.with_name(qu_path.name.replace('.request.json', '.response.body')))
    api_frames = frames(api_dir / 'response.sse')
    answer_frames = [r for r in api_frames if r.get('response_type') == 'answer']
    sse_answer = ''.join(r.get('content', '') for r in answer_frames)
    provider_frames = frames(final_path.with_name(final_path.name.replace('.request.json', '.response.body')))
    raw_answer = ''.join(choice.get('delta', {}).get('content') or '' for r in provider_frames for choice in r.get('choices', []))
    assistant = next(m for m in pg['messages'] if m['role'] == 'assistant')
    history = scoring.read(api_dir / 'history.json')
    if isinstance(history, dict):
        history = history.get('data', history)
    history_assistant = next(m for m in history if m['role'] == 'assistant')
    request_checks = []
    for path, req in requests:
        canonical = json.dumps(req['body'], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        entry = next(x for x in ledger['entries'] if x['request_id'] == req['request_id'])
        pg_call = next(x for x in pg['provider_calls'] if x['request_id'] == req['request_id'])
        response = scoring.read(path.with_name(path.name.replace('.request.json', '.response.json')))
        raw_text = path.with_name(path.name.replace('.request.json', '.response.body')).read_text(encoding='utf-8-sig')
        protocol_done = 'data: [DONE]' in raw_text
        request_checks.append({'request_id': req['request_id'], 'capability': req['capability'], 'model': req['model'],
            'status': entry['status'], 'provider_outcome': pg_call['outcome'],
            'usage': entry['usage'], 'request_body_wire_hash_matches_capture_ledger_pg':
                hashlib.sha256(canonical).hexdigest() == entry['wire_sha256'] == pg_call['wire_sha256'],
            'scope_matches_kb': req['scope']['knowledge_base_ids'] == [frozen['knowledge_base_id']] and req['scope']['document_ids'] == [],
            'response_complete_http200': response.get('status_code') == 200 and (response.get('complete') is True or protocol_done),
            'response_network_eof_observed': response.get('complete'), 'response_protocol_done': protocol_done,
            'completion_meaning': 'Streaming [DONE] establishes protocol end; network EOF flag may remain false.'})
    manifest = scoring.read(ROOT / 'MANIFEST.json')
    failures = [name for name, meta in manifest.items() if not (ROOT / name).is_file()
                or (ROOT / name).stat().st_size != meta['bytes'] or scoring.sha(ROOT / name) != meta['sha256']]
    readbacks = scoring.read(case_dir / 'readback/manifest.json')
    readback_failures = []
    for r in readbacks:
        local = case_dir / 'readback' / Path(r['saved']).name
        if r['status'] != 200 or not local.is_file() or scoring.sha(local) != r['sha256']:
            readback_failures.append(r['path'])
    channels = {r['channel']: r['rows'] for r in retrieval['channels']}
    fusion = retrieval['fusions'][0]
    stages = {'vector': {**doc_metrics(channels['vector'], frozen), 'candidate_chunks': len(channels['vector']), 'gold_chunk_positions': rank(channels['vector'], target)},
        'bm25': {**doc_metrics(channels['bm25'], frozen), 'candidate_chunks': len(channels['bm25']), 'gold_chunk_positions': rank(channels['bm25'], target)},
        'full_weighted_rrf': {**doc_metrics(fusion['full_fused_order'], frozen), 'candidate_chunks': len(fusion['full_fused_order']), 'gold_chunk_positions': rank(fusion['full_fused_order'], target)},
        'search_returned_top30': {**doc_metrics(retrieval['returned_results'], frozen), 'candidate_chunks': len(retrieval['returned_results']), 'gold_chunk_positions': rank(retrieval['returned_results'], target)},
        'rerank': {'status': 'REAL_RERANK_ALL_FILTERED_SEARCH_NOTHING', 'input_count': len(rr['body']['documents']),
            'returned_count': len(rerank_raw['results']), 'max_score': max(r['relevance_score'] for r in rerank_raw['results']),
            'threshold': .3, 'top1_floor': .15, 'kept_count': 0, 'metrics': scoring.ranking_metrics([], frozen['gold']['qrels'], k=10)},
        'merge': {'status': 'NOT_RUN_SEARCH_NOTHING', 'metrics': None},
        'context': {'status': 'NO_RETRIEVED_EVIDENCE_CONTEXT_METADATA_FALLBACK_ONLY',
            'evidence_document_recall_at_10': 0, 'evidence_document_mrr_at_10': 0,
            'normal_context_builder': 'NOT_RUN_SEARCH_NOTHING', 'finalized_context_present': 'finalized_context' in stage}}
    original_sha = frozen['qrel_source_mappings'][0]['original_sha256']
    source_zip = Path('D:/RAG-v6-final-ingestion-pack-r1/attempt3-ocr-layout/scifact.zip')
    with zipfile.ZipFile(source_zip) as archive:
        source_bytes = archive.read('objects/' + original_sha)
    assert hashlib.sha256(source_bytes).hexdigest() == original_sha
    original = source_bytes.decode('utf-8')
    prompt = final['body']['messages'][0]['content']
    report = {'schema': 's8-1-first-case-independent-review/v1', 'case_id': 'scifact-1304', 'arm': 'A',
        'status': 'REAL_API_COMPLETED_WITH_RETRIEVAL_AND_GROUNDING_QUALITY_FAILURE',
        'chain_acceptance': {'api_model_sse_pg': 'PASS', 'evidence_rag_merge_context_answer': 'NOT_PROVEN_FIRST_CASE_SEARCH_NOTHING',
            'citation_readback': 'NOT_APPLICABLE_ZERO_GENERATED_REFERENCES; source/chunk HTTP readbacks verified separately',
            'may_continue_healthy_A_cases': True, 'permission_identity_settlement_stop_condition_found': False},
        'run_id': stage['run_id'], 'session_id': stage['session_id'], 'client_request_id': result['request_id'],
        'question': frozen['question'], 'rewrite': qu_raw['choices'][0]['message']['content'],
        'answer': {'text': assistant['content'], 'official_answer_score': 'NOT_APPLICABLE_SCIFACT_NO_ANSWER_GOLD',
            'official_support_refute_accuracy': 'NOT_APPLICABLE_NO_STANCE_GOLD',
            'fact_support': 'FAIL_UNSUPPORTED_DETAILED_PHARMACOLOGY_AFTER_EXPLICIT_KB_GAP',
            'claims_not_supported_by_actual_model_input': ['GFRα/胃泌素受体激动剂', '用于治疗心衰', 'IGF-1通路、抗纤维化、增强心脏收缩机制'],
            'fact_review_basis': 'Actual final request contains metadata snippets only, declares no search match, and supplies none of those detailed drug claims. No external medical truth score is claimed.',
            'original_source_readback': {'path': str(source_zip), 'member': 'objects/' + original_sha,
                'sha256': original_sha, 'text': original, 'meaning': 'Verified source discusses CK-2017357 fast-skeletal-troponin activation. It was not delivered as retrieved evidence to this answer.'}},
        'consistency': {'raw_provider_equals_sse_equals_pg_equals_history_equals_result': raw_answer == sse_answer == assistant['content'] == history_assistant['content'] == result['assistant']['content'],
            'api_complete_count': sum(r.get('response_type') == 'complete' for r in api_frames),
            'api_terminal_error_count': sum(r.get('response_type') == 'error' and not (r.get('data') or {}).get('non_terminal') for r in api_frames),
            'pg_turn_completed': pg['turns'][0]['status'] == 'completed',
            'pg_message_completed': assistant['is_completed'] is True, 'knowledge_reference_count': len(assistant['knowledge_references']),
            'provider_finish_reasons': sorted({choice.get('finish_reason') for r in provider_frames for choice in r.get('choices', []) if choice.get('finish_reason')}),
            'captured_manifest_files': len(manifest), 'manifest_failures': failures,
            'source_http_readbacks': len(readbacks), 'source_http_readback_failures': readback_failures},
        'model_input': {'kind': 'NO_SEARCH_MATCH_METADATA_LISTING_FALLBACK', 'actual_messages': final['body']['messages'],
            'wire_body_sha256': next(r['wire_sha256'] for r in ledger['entries'] if r['request_id'] == final['request_id']), 'generation_input_bound_to_real_response_and_ledger': all(r['request_body_wire_hash_matches_capture_ledger_pg'] and r['response_complete_http200'] for r in request_checks),
            'fallback_prompt_allows_general_knowledge': 'answer the user\'s question based on your general knowledge' in prompt,
            'metadata_not_original_evidence_instruction_present': 'Treat the document listing as metadata' in prompt,
            'no_retrieved_context_to_bind': True},
        'configuration': {'final_model': final['body']['model'], 'final_temperature': final['body']['temperature'],
            'final_max_tokens': final['body']['max_tokens'], 'final_enable_thinking': final['body']['enable_thinking'],
            'qu_model': qu['body']['model'], 'qu_temperature': qu['body']['temperature'],
            'qu_max_tokens': qu['body']['max_tokens'], 'qu_enable_thinking': qu['body']['enable_thinking'],
            'answer_attempts': len(pg['generation_attempts']), 'upgraded': False, 'graph': False, 'floorless': False},
        'retrieval': stages,
        'earliest_gold_loss': {'stage': 'WEIGHTED_RRF_TOP30_CUTOFF', 'basis': 'Gold source exists in both raw channels and full fusion, absent from actual 30 Rerank passages.',
            'q0_vs_rewrite_causal_effect': 'UNVERIFIED_NO_CONTROL_CALL',
            'later_quality_issue': 'All 30 Rerank scores below .15; SearchNothing fallback sends no retrieved evidence and generates unsupported detail.'},
        'gold_input_isolation': {'question_json': scoring.read(api_dir / 'question.json'),
            'scoring_fields_in_question_payload': [k for k in scoring.read(api_dir / 'question.json') if any(s in k.lower() for s in ('gold','qrel','expected','answer_points','supporting_facts'))],
            'gold_artifact_in_request_capture': any('ground-truth.json' in json.dumps(r) or 'fact-criteria.json' in json.dumps(r) for _, r in requests),
            'assessment': 'No scoring labels/artifacts in saved POST or actual Provider request bodies; original question is frozen. Metadata snippets are pre-existing business data, not scoring Gold.'},
        'usage': {'provider_calls': len(ledger['entries']), 'actual_tokens': sum(r['usage']['total_tokens'] for r in ledger['entries']),
            'settled_calls': sum(r['status'] == 'SETTLED' for r in ledger['entries']), 'unknown_calls': sum(r['status'] == 'UNKNOWN' for r in ledger['entries']),
            'child_status': ledger['status'], 'post_elapsed_seconds': result['elapsed_seconds'], 'calls': request_checks},
        'new_calls_by_review': 0}
    assert report['consistency']['raw_provider_equals_sse_equals_pg_equals_history_equals_result']
    assert not failures and not readback_failures
    assert all(r['scope_matches_kb'] and r['request_body_wire_hash_matches_capture_ledger_pg'] and r['response_complete_http200'] and r['status'] == 'SETTLED' for r in request_checks)
    scoring.write_new(scoring.BASE / 'first-case-score.results.json', report)
    print(json.dumps({'status': report['status'], 'tokens': report['usage']['actual_tokens'],
        'search_recall_at_10': stages['search_returned_top30']['metrics']['recall_at_k'],
        'bm25_recall_at_10': stages['bm25']['metrics']['recall_at_k'],
        'raw_sse_pg_equal': report['consistency']['raw_provider_equals_sse_equals_pg_equals_history_equals_result'],
        'manifest_files': len(manifest), 'http_readbacks': len(readbacks), 'new_calls': 0}, ensure_ascii=True))


if __name__ == '__main__':
    main()
