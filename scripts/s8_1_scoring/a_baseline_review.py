"""Offline S8.1 saved arm auditor. No services or model clients imported."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re

import s8_1_score as scoring
from first_case_review import frames, compact, doc_metrics, rank

ROOT = scoring.BASE.parent / 'a-baseline-evidence'


def read(path):
    return scoring.read(path)


def entries(stage):
    return stage.get('stages', stage.get('entries', []))


def state(entry):
    return entry.get('state', entry.get('fields', {}))


def audit_case(root, case_dir, frozen):
    # ECS retains a historical MML PDF and a current same-byte upload. Resolve
    # source identity through saved original bytes, never answer text or title.
    frozen = {**frozen, 'qrel_source_mappings': list(frozen['qrel_source_mappings'])}
    aliases = []
    for source_file in (case_dir / 'readback').glob('source-*.bin'):
        document_id = source_file.stem.removeprefix('source-')
        if any(r['document_id'] == document_id for r in frozen['qrel_source_mappings']):
            continue
        matches = [m for m in frozen['qrel_source_mappings'] if scoring.sha(source_file) == m['original_sha256']]
        if len(matches) == 1:
            chunk = next((read(p)['data'] for p in (case_dir / 'readback').glob('chunk*.json')
                          if read(p)['data']['document_id'] == document_id), None)
            if chunk and chunk['knowledge_base_id'] == frozen['knowledge_base_id']:
                alias = {**matches[0], 'document_id': document_id, 'source_version': chunk['source_version'],
                         'mapping_basis': 'ACTUAL_ECS_HTTP_ORIGINAL_BYTES_SHA_AND_CHUNK_KB_VERSION',
                         'source_readback_path': str(source_file)}
                frozen['qrel_source_mappings'].append(alias)
                aliases.append(alias)
    api_dir = next((case_dir / 'api-cases').iterdir())
    result = read(api_dir / 'result.json')
    ev = root / 'executions' / case_dir.name / 'evidence'
    stage = read(next(ev.glob('*.stages.json')))
    retr = read(next(ev.glob('*.retrieval.json')))
    pg = read(case_dir / 'pg-final-after-stop.json')
    ledger = read(root / 'executions' / case_dir.name / 'execution.json')
    reqs = [(p, read(p)) for p in ev.glob('*.request.json')]
    final_path, final = next((p, r) for p, r in reqs if '/answer/' in r['request_id'])
    assistant = next(m for m in pg['messages'] if m['role'] == 'assistant')
    api_frames = frames(api_dir / 'response.sse')
    provider_frames = frames(final_path.with_name(final_path.name.replace('.request.json', '.response.body')))
    raw_answer = ''.join(ch.get('delta', {}).get('content') or '' for r in provider_frames for ch in r.get('choices', []))
    api_answer = ''.join(r.get('content', '') for r in api_frames if r.get('response_type') == 'answer')
    history = read(api_dir / 'history.json')
    if isinstance(history, dict):
        history = history['data']
    history_answer = next(x['content'] for x in history if x['role'] == 'assistant')
    question = read(api_dir / 'question.json')
    checks = []
    for path, req in reqs:
        body_sha = hashlib.sha256(json.dumps(req['body'], ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()
        entry = next(x for x in ledger['entries'] if x['request_id'] == req['request_id'])
        call = next(x for x in pg['provider_calls'] if x['request_id'] == req['request_id'])
        response = read(path.with_name(path.name.replace('.request.json', '.response.json')))
        response_text = path.with_name(path.name.replace('.request.json', '.response.body')).read_text(encoding='utf-8-sig')
        checks.append({'request_id': req['request_id'], 'model': req['model'], 'capability': req['capability'],
            'wire_hash_matches_ledger_pg': body_sha == entry['wire_sha256'] == call['wire_sha256'],
            'scope_matches': req['scope']['knowledge_base_ids'] == [frozen['knowledge_base_id']] and req['scope']['document_ids'] == [],
            'settled': entry['status'] == 'SETTLED', 'outcome': call['outcome'],
            'response_http200': response['status_code'] == 200,
            'protocol_complete': response['complete'] or 'data: [DONE]' in response_text,
            'network_eof_observed': response['complete'], 'usage': entry['usage']})
    final_context = stage.get('finalized_context')
    chunks = {r['data']['chunk_id']: r['data'] for r in (read(p) for p in (case_dir / 'readback').glob('chunk*.json'))}
    selected = []
    if final_context:
        snapshots = [e for e in entries(stage) if 'merge_results' in state(e)]
        merged = {x['id']: x for x in state(snapshots[-1])['merge_results'] if x}
        for cid in final_context['result_ids']:
            row = merged[cid]
            rb = chunks.get(cid)
            source = case_dir / 'readback' / ('source-' + row['knowledge_id'] + '.bin')
            source_sha = scoring.sha(source) if source.is_file() else None
            source_text = source.read_text(encoding='utf-8-sig') if source.is_file() and row.get('chunk_type') != 'image' and source.suffix == '.md' else None
            # Source may be PDF; bind its bytes and chunk's explicit version.
            selected.append({'chunk_id': cid, 'document_id': row['knowledge_id'], 'kb': row['knowledge_base_id'],
                'chunk_type': row['chunk_type'], 'content': row['content'], 'readback': rb,
                'source_version': row.get('metadata', {}).get('source_version'),
                'source_path': str(source), 'source_sha256': source_sha,
                'source_hash_matches_metadata': source_sha == row.get('metadata', {}).get('sha256'),
                'readback_identity_version_matches': bool(rb and rb['document_id'] == row['knowledge_id']
                    and rb['knowledge_base_id'] == frozen['knowledge_base_id']
                    and rb.get('source_version') == row.get('metadata', {}).get('source_version')),
                'exact_content_matches_chunk_readback': bool(rb and row['content'] == rb['text']),
                'chunk_is_original_text': row['chunk_type'] == 'text' and row.get('chunk_metadata', {}).get('origin') != 'model_generated_summary'})
    request_text = scoring.textual_messages(final)
    for row in selected:
        row['exact_content_present_in_wire'] = compact(row['content']) in compact(html.unescape(request_text))
        if row['source_path'].endswith('.bin'):
            raw = Path(row['source_path']).read_bytes()
            try:
                original_text = raw.decode('utf-8-sig')
                row['content_present_in_original_source'] = compact(row['content']) in compact(original_text)
            except UnicodeDecodeError:
                row['content_present_in_original_source'] = 'NOT_APPLICABLE_BINARY_SOURCE'
    rendered_equal = bool(final_context and compact(final_context['rendered_contexts']) in compact(request_text))
    bound = bool(selected and all(x['exact_content_present_in_wire'] for x in selected))
    source_checks = read(case_dir / 'readback/manifest.json')
    readback_errors = []
    for row in source_checks:
        local = case_dir / 'readback' / Path(row['saved']).name
        if row['status'] != 200 or not local.is_file() or scoring.sha(local) != row['sha256']:
            readback_errors.append(row['path'])
    qu = next(r for _, r in reqs if r['request_id'].endswith('/query_rewrite'))
    rr_path, rerank = next((p, r) for p, r in reqs if r['capability'] == 'rerank')
    raw_rerank = read(rr_path.with_name(rr_path.name.replace('.request.json', '.response.body')))
    ranking = {}
    for channel in retr['channels']:
        ranking[channel['channel']] = doc_metrics(channel['rows'], frozen)
        ranking[channel['channel']]['chunk_count'] = len(channel['rows'])
        ranking[channel['channel']]['positive_positions'] = {m['source_id']: rank(channel['rows'], m['document_id'])
            for m in frozen['qrel_source_mappings'] if frozen['gold']['qrels'].get(m['source_id'], 0) > 0}
    ranking['search'] = doc_metrics(retr['returned_results'], frozen)
    ranking['search']['chunk_count'] = len(retr['returned_results'])
    fusion = retr['fusions'][0]['full_fused_order']
    ranking['full_weighted_rrf'] = doc_metrics(fusion, frozen)
    ranking['full_weighted_rrf']['positive_positions'] = {m['source_id']: rank(fusion, m['document_id'])
        for m in frozen['qrel_source_mappings'] if frozen['gold']['qrels'].get(m['source_id'], 0) > 0}
    for label, event, field in [('rerank', 'rerank', 'rerank_results'), ('merge', 'merge', 'merge_results')]:
        snapshots = [e for e in entries(stage) if e.get('stage') == event and e.get('status') == 'completed']
        rows = [r for r in state(snapshots[-1]).get(field, []) if r] if snapshots else []
        ranking[label] = {**doc_metrics(rows, frozen), 'chunk_count': len(rows),
                          'execution': 'COMPLETED' if snapshots else 'NOT_RUN_OR_SEARCH_NOTHING'}
    ranking['context'] = doc_metrics([{'knowledge_id': r['document_id']} for r in selected], frozen)
    ranking['context']['chunk_count'] = len(selected)
    gold = {**frozen['gold'], 'qid': frozen['identity']['qid']}
    if frozen['identity']['dataset'] == 'mmlongbench-doc':
        answer_metric = scoring.typed_answer(assistant['content'], gold['answers'], gold['evaluation_metadata'])
    else:
        answer_metric = scoring.answer_metric(frozen['identity']['dataset'], gold, assistant['content'], True, scoring.METRICS, scoring.BASE / 'metric-cache')
    return {'case_id': result['case_id'], 'dataset': frozen['identity']['dataset'], 'qid': result['qid'], 'arm': result['arm'],
        'run_id': stage['run_id'], 'session_id': stage['session_id'], 'question': question,
        'completed': result['status'] == 'COMPLETE' and assistant['is_completed'] and pg['turns'][0]['status'] == 'completed',
        'answer': assistant['content'], 'answer_metric': answer_metric,
        'raw_sse_pg_history_result_equal': raw_answer == api_answer == assistant['content'] == history_answer == result['assistant']['content'],
        'sse_complete_count': sum(x.get('response_type') == 'complete' for x in api_frames),
        'sse_terminal_error_count': sum(x.get('response_type') == 'error' and not (x.get('data') or {}).get('non_terminal') for x in api_frames),
        'same_run_identity': assistant['request_id'] == stage['run_id'] == pg['turns'][0]['run_id'],
        'no_gold_fields_in_post': not any(k in question for k in ['gold','qrels','answers','expected_chunk_ids','answer_points','supporting_facts']),
        'frozen_question_scope_match': question['content'] == frozen['question'] and question['knowledge_base_scope'] == [frozen['knowledge_base_id']] and question['document_scope'] == [],
        'provider_calls': checks, 'actual_tokens': sum(r['usage']['total_tokens'] for r in checks),
        'ledger_status': ledger['status'], 'elapsed_seconds': result['elapsed_seconds'],
        'readback_count': len(source_checks), 'readback_failures': readback_errors,
        'context_bound_to_actual_wire': bound, 'context_rendered_wrapper_verbatim_in_wire': rendered_equal,
        'context_wire_binding_basis': 'Each selected chunk factual content exact after XML entity unescaping and whitespace normalization; generation renders a new XML wrapper.', 'context': final_context,
        'context_chunks': selected, 'retrieval': ranking, 'verified_same_byte_source_aliases': aliases,
        'search_error': stage.get('error_type'), 'rerank_max_score': max(r['relevance_score'] for r in raw_rerank['results']),
        'rerank_passage_count': len(rerank['body']['documents']),
        'final_wire': {k:v for k,v in final['body'].items() if k != 'messages'},
        'qu_wire': {k:v for k,v in qu['body'].items() if k != 'messages'},
        'final_messages': final['body']['messages'],
        'actual_attempts': [{k:v for k,v in r.items() if k in ('attempt_id','role','status','finish_reason','model_requested','model_reported','upgrade_reason','context_sha256','prompt_sha256')} for r in pg['generation_attempts']],
        'public_structured_references': assistant['knowledge_references'],
        'supporting_sentences': scoring.supporting_sentence_review(frozen['identity']['case_id'], {'actual_context': final_context}, [final]),
        'new_calls_by_review': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--case-substring')
    args = parser.parse_args()
    frozen = {c['identity']['case_id']: c for c in read(scoring.GOLD)['cases']}
    rows = []
    for folder in (args.root / 'cases').iterdir():
        result = read(next(folder.glob('api-cases/*/result.json')))
        if args.case_substring and args.case_substring not in result['case_id']:
            continue
        if result['case_id'] == 'scifact-1304':
            prior = read(scoring.BASE / 'first-case-score.results.json')
            rows.append({'case_id': 'scifact-1304', 'arm': 'A', 'reuse': str(scoring.BASE / 'first-case-score.results.json'), 'actual_tokens': prior['usage']['actual_tokens'], 'new_calls_by_review': 0})
        else:
            rows.append(audit_case(args.root, folder, frozen[result['case_id']]))
    output = {'schema': 's8-1-independent-arm-review/v1', 'cases': rows,
              'actual_tokens': sum(r['actual_tokens'] for r in rows), 'new_calls_by_review': 0}
    scoring.write_new(args.output, output)
    print(json.dumps({'cases': len(rows), 'tokens': output['actual_tokens'],
        'checks': [{k:r.get(k) for k in ['case_id','raw_sse_pg_history_result_equal','context_bound_to_actual_wire','readback_failures','search_error']} for r in rows]}, ensure_ascii=True))


if __name__ == '__main__':
    main()
