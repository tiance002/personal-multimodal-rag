"""Read-only P9 scoring: quote identity, OCR delivery and answer Gold are separate.

No model judge, provider calls, database access, runtime import or Gold ingestion.
Use --output explicitly to save a new report; historical artifacts stay intact.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import p8_score_real as p8


def texts(value):
    if isinstance(value, str): return [value]
    if isinstance(value, list): return [text for row in value for text in texts(row)]
    if isinstance(value, dict):
        return texts(value.get('content', value.get('text', '')))
    return []


def canonical_text(value):
    return ' '.join(re.sub(r'(?m)^\s*>\s?', '', html.unescape(value)).split())


def contains_ocr(container, ocr):
    expected = canonical_text(ocr)
    return bool(expected and expected in canonical_text(container))


def ocr_delivery(capture, stage_capture, provider_requests, ledger, *, required=False):
    """Only exact observed OCR text, current source domains and real wire prove delivery."""
    capture = capture or {}
    domains = {(row.get('knowledge_base_id'), row.get('knowledge_id'), row.get('chunk_metadata', {}).get('source_version'))
               for row in capture.get('evidence', [])}
    candidates = {}
    for stage in (stage_capture or {}).get('stages', []):
        for field in ('search_results', 'rerank_results', 'merge_results'):
            for row in stage.get(field, []):
                domain = (row.get('knowledge_base_id'), row.get('knowledge_id'), row.get('chunk_metadata', {}).get('source_version'))
                if domain not in domains or not domain[2]: continue
                if row.get('chunk_type') == 'image_ocr' and row.get('content'):
                    # Search/rerank rows are original OCR; merge can be enriched.
                    if field != 'merge_results': candidates.setdefault(row['id'], {'id': row['id'], 'text': row['content'], 'domain': domain})
    context = capture.get('context', {}).get('rendered_contexts', '')
    blocks = re.findall(r'<context\b[^>]*>(.*?)</context>', context, flags=re.S)
    context_text = '\n'.join(blocks)
    prepared = '\n'.join(texts(capture.get('messages', [])))
    actual = []
    for request in provider_requests:
        if request.get('run_id') != capture.get('run_id') or request.get('stage') != 'answer': continue
        request_id = request.get('request_id')
        matching = [row for row in ledger if row.get('request_id') == request_id]
        verified = [row for row in matching if row.get('wire_sha256') == request.get('wire_sha256') and row.get('outcome') == 'RESPONSE_RECEIVED']
        # Re-serialize exactly as the existing transport does; an invented hash
        # or edited JSON body cannot stand in for the originally captured bytes.
        body = request.get('body', {})
        digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False).encode()).hexdigest()
        trusted = (request.get('boundary') == 'HTTPX_REQUEST_HOOK_AFTER_DURABLE_GATE'
                   and digest == request.get('wire_sha256') and bool(verified))
        actual.append({'request_id': request_id, 'wire_sha256': request.get('wire_sha256'), 'ledger_wire_verified': trusted,
                       'finish_reason': request.get('finish_reason'), 'text': '\n'.join(texts(body.get('messages', [])))})
    facts = []
    for candidate in candidates.values():
        facts.append({'chunk_id': candidate['id'], 'knowledge_base_id': candidate['domain'][0],
            'document_id': candidate['domain'][1], 'source_version': candidate['domain'][2],
            'ocr_sha256': hashlib.sha256(candidate['text'].encode()).hexdigest(),
            'final_context_contains_ocr': contains_ocr(context_text, candidate['text']),
            'prepared_messages_contain_ocr': contains_ocr(prepared, candidate['text']),
            'verified_actual_payload_contains_ocr': any(row['ledger_wire_verified'] and contains_ocr(row['text'], candidate['text']) for row in actual)})
    in_context = any(row['final_context_contains_ocr'] for row in facts)
    in_prepared = any(row['prepared_messages_contain_ocr'] for row in facts)
    delivered = any(row['final_context_contains_ocr'] and row['prepared_messages_contain_ocr'] and row['verified_actual_payload_contains_ocr'] for row in facts)
    if not facts: status = 'NOT_RUN_NO_CURRENT_SCOPE_OCR_CANDIDATE' if required else 'NOT_APPLICABLE_NO_OCR_CANDIDATE'
    elif not in_context: status = 'FAIL_OCR_MISSING_FROM_FINAL_CONTEXT'
    elif not in_prepared: status = 'FAIL_OCR_MISSING_FROM_PREPARED_MESSAGES'
    elif not any(row['ledger_wire_verified'] for row in actual): status = 'NOT_RUN_ACTUAL_PROVIDER_PAYLOAD_UNVERIFIED'
    else: status = 'PASS' if delivered else 'FAIL_OCR_MISSING_FROM_ACTUAL_PROVIDER_PAYLOAD'
    return {'status': status, 'pass': delivered, 'final_context_ocr': in_context, 'prepared_message_ocr': in_prepared,
        'actual_provider_payload_ocr': delivered if any(row['ledger_wire_verified'] for row in actual) else None,
        'source_facts': facts, 'provider_requests': [{k: v for k, v in row.items() if k != 'text'} for row in actual],
        'quote_validity_implies_ocr_delivery': False, 'answer_correctness': 'SEPARATE_GOLD_DIAGNOSTIC'}


def usage_totals(rows):
    totals = {'input': [], 'output': [], 'total': []}
    for row in rows:
        if row.get('state') != 'settled': continue
        values = dict((row.get('usage_facts') or {}).get('fields', []))
        for name in totals:
            fact = values.get(name, {})
            if fact.get('valid') is True and type(fact.get('value')) is int: totals[name].append(fact['value'])
    settled_count = sum(row.get('state') == 'settled' for row in rows)
    return {'settled_' + name: {'tokens': sum(values) if settled_count and len(values) == settled_count else None,
                'settled_rows': settled_count, 'known_rows': len(values)} for name, values in totals.items()}


def artifact_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def inspect_run(run):
    """Read an existing P8/P8.1/P9 run without altering any evidence artifact."""
    result = p8.evaluate(run)
    state = p8.load(run / 'state.json', {})
    captures = [p8.load(path) for path in sorted((run / 'captures').glob('*.json'))]
    stages = [p8.load(path) for path in sorted((run / 'stages').glob('*.json'))]
    requests = [p8.load(path) for path in sorted((run / 'provider-requests').glob('*.json'))]
    after = p8.load(run / 'ledger-after.json', [])
    before = p8.load(run / 'ledger-before.json', [])
    before_ids = {row.get('call_id') for row in before}
    delta = [row for row in after if row.get('call_id') not in before_ids] if (run / 'ledger-before.json').exists() else None
    for row in result['cases']:
        record = row['question_identity']
        found = [capture for capture in captures if capture.get('session_id') == record.get('session') and capture.get('question') == row['question']]
        capture = found[0] if len(found) == 1 else None
        run_id = (capture or {}).get('run_id')
        stage = next((item for item in stages if item.get('run_id') == run_id), None)
        delivery = ocr_delivery(capture, stage, requests, after, required=row['dataset'] == 'omnidocbench')
        matching_calls = [call for call in after if run_id and call.get('request_id', '').startswith(run_id + '/')]
        refs = row['citations']
        row.update(source_code_sha=state.get('source_sha'), run_directory=str(run), server_run_id=run_id,
            quote_validity={'status': 'PASS' if refs and all(ref['valid'] for ref in refs) else 'FAIL' if refs else 'NOT_RUN_NO_CITATION',
                            'valid': sum(ref['valid'] for ref in refs), 'total': len(refs)},
            ocr_delivery=delivery, answer_correctness=row['gold_quality']['answer'],
            provider_finish_reasons=[{'request_id': request.get('request_id'), 'finish_reason': request.get('finish_reason'),
                'status': 'RECORDED' if request.get('finish_reason') else 'NOT_RUN'} for request in requests
                if request.get('run_id') == run_id and request.get('stage') == 'answer'],
            usage=usage_totals(matching_calls), ledger_calls=matching_calls,
            ledger_delta_call_ids=[call['call_id'] for call in (delta or []) if call in matching_calls] if delta is not None else None,
            artifact_sha256={suffix: artifact_hash(run / ('queries/' + p8.identity(row['dataset'], row['qid']) + suffix))
                for suffix in ('-messages.json', '-chunkreadback.json', '.sse')})
        row['ocr_complete_chain_status'] = 'PASS' if row['wiring_status'] == 'PASS' and delivery['status'] == 'PASS' else (
            delivery['status'] if delivery['status'] != 'PASS' else row['wiring_status'])
        row['provider_finish_reason_status'] = 'RECORDED' if any(item['status'] == 'RECORDED' for item in row['provider_finish_reasons']) else 'NOT_RUN_NO_CAPTURED_PROVIDER_FINISH_REASON'
    return {**result, 'schema': 'p9-readonly-real-evidence-results/v1', 'run_directory': str(run),
        'source_code_sha': state.get('source_sha'), 'ledger_delta': delta,
        'ledger_before_available': (run / 'ledger-before.json').exists(),
        'method': 'QUOTE_IDENTITY_OCR_PAYLOAD_DELIVERY_AND_GOLD_QUALITY_SEPARATE',
        'official_omni_qa': 'NOT_APPLICABLE_NO_INDEPENDENT_QA_GOLD'}


def evaluate_p9(root):
    selection = p8.load(root / 'selection.json')
    if not selection: raise ValueError('P9_FROZEN_SELECTION_REQUIRED')
    run_dirs = sorted({path.parent for path in root.rglob('selection.json') if path.parent != root
        and path.parent.name not in ('selection-recheck', 'selection')})
    inspected = [inspect_run(path) for path in run_dirs]
    attempts = defaultdict(list)
    for report in inspected:
        for case in report['cases']: attempts[(case['dataset'], case['qid'])].append(case)
    frozen_cases = []
    for dataset in selection['datasets']:
        if dataset['name'] == 'omnidocbench': continue
        for case in dataset.get('cases', []):
            records = attempts.get((dataset['name'], case['qid']), [])
            frozen_cases.append({'dataset': dataset['name'], 'qid': case['qid'], 'question': case['question'],
                'status': 'BLOCKED_FROZEN_QUESTION_MISMATCH' if any(item['question'] != case['question'] for item in records)
                    else 'RECORDED_ATTEMPTS' if records else 'NOT_RUN', 'attempts': records,
                'reason': None if records else 'NO_NATIVE_RESULT_OR_CAPABILITY_NOT_EXECUTED',
                'quality_failures_remain_in_denominator': True})
    gold = p8.load(root / 'gold-selected.json', {'datasets': []})
    pages = [page for dataset in gold.get('datasets', []) if dataset['name'] == 'omnidocbench' for page in dataset.get('pages', [])]
    omni = []
    for page in pages:
        observations = [item for report in inspected for item in report['omni_parsing'] if item['page_id'] == page['page_id']]
        omni.append({'page_id': page['page_id'], 'status': 'RECORDED' if observations else 'NOT_RUN', 'observations': observations,
                     'official_qa': 'NOT_APPLICABLE', 'official_layout_score': 'NOT_RUN'})
    groups = Counter((attempt.get('source_code_sha') or 'UNKNOWN') for records in attempts.values() for attempt in records)
    return {'schema': 'p9-frozen-cohort-results/v1', 'question_denominator': len(frozen_cases),
        'expected_question_denominator': 40, 'question_cohort_count_valid': len(frozen_cases) == 40,
        'omni_page_denominator': len(omni), 'expected_omni_page_denominator': 24,
        'omni_page_count_valid': len(omni) == 24, 'cases': frozen_cases, 'omni_pages': omni,
        'source_version_groups': dict(groups), 'run_directories': [str(path) for path in run_dirs],
        'omni_project_qa_diagnostics': [attempt for (dataset, _), records in attempts.items()
            if dataset == 'omnidocbench' for attempt in records],
        'selection_sha256': artifact_hash(root / 'selection.json'), 'gold_sha256': artifact_hash(root / 'gold-selected.json'),
        'full_benchmark_acceptance': 'NOT_CLAIMED', 'historical_attempts_overwritten': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--run-dir', type=Path); group.add_argument('--root-dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = inspect_run(args.run_dir) if args.run_dir else evaluate_p9(args.root_dir)
    if args.output:
        assert not args.output.exists(), 'OUTPUT_EXISTS_PRESERVE_PRIOR_RESULT'
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'schema': result['schema'], 'denominator': result.get('question_denominator', result.get('denominator')),
                      'output': str(args.output) if args.output else None}, ensure_ascii=False))


if __name__ == '__main__': main()
