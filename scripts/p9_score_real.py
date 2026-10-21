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
from types import FunctionType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import p8_score_real as p8
from eval_center.weknora_scoring import answer_diagnostics


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


def citation_offsets(reference, chunk):
    values = (reference.get('start_at'), reference.get('end_at'), chunk.get('start'), chunk.get('end'))
    if any(type(value) is not int for value in values):
        return {'status': 'NOT_RUN_EXPLICIT_SOURCE_OFFSETS_REQUIRED', 'valid': False,
                'reference_offsets': list(values[:2]), 'readback_offsets': list(values[2:])}
    valid = values[:2] == values[2:] and 0 <= values[0] <= values[1]
    return {'status': 'PASS' if valid else 'FAIL_OFFSET_READBACK_MISMATCH', 'valid': valid,
            'reference_offsets': list(values[:2]), 'readback_offsets': list(values[2:]),
            'convention': 'SOURCE_UNICODE_HALF_OPEN_ZERO_IS_DATA'}


def system_answer_gold(run, dataset, case, prediction, *, completed, query_attempted, answer_model_called, fixed_refusal):
    """Score the actual completed application answer, including fixed refusal.

    This system-level denominator is independent of whether answer generation
    called a model. Missing/unfinished output is never invented as a zero score.
    """
    origin = 'FIXED_REFUSAL' if fixed_refusal else 'REAL_LLM_RESPONSE' if answer_model_called else 'APP_RESPONSE_MODEL_EXECUTION_UNCONFIRMED'
    base = {'origin': origin, 'answer_model_called': answer_model_called,
            'included_in_answer_metric_denominator': False, 'metrics': None}
    if not case or not case.get('answers'):
        return {**base, 'status': 'NOT_APPLICABLE_NO_ANSWER_GOLD'}
    if not query_attempted or not completed or prediction is None:
        return {**base, 'status': 'NOT_RUN_NO_COMPLETED_REAL_APP_RESPONSE'}
    diagnostic = p8.long_answer(run, case['qid'], prediction, case['answers']) if dataset == 'longbench-zh' else answer_diagnostics(prediction, case['answers'], dataset)
    return {**base, **diagnostic, 'origin': origin, 'answer_model_called': answer_model_called,
            'included_in_answer_metric_denominator': diagnostic.get('metrics') is not None,
            'evaluation_unit': 'COMPLETED_APP_SYSTEM_RESPONSE_INCLUDING_FIXED_REFUSALS',
            'official_judge': 'NOT_RUN' if dataset == 'mmlongbench-doc' else diagnostic.get('official_judge', 'NOT_APPLICABLE')}


def inspect_run(run):
    """Read an existing P8/P8.1/P9 run without altering any evidence artifact."""
    marker = p8.load(run / 'snapshot-selection.json', {'label': 'after'})
    label = marker.get('label')
    if not isinstance(label, str) or not re.fullmatch('[A-Za-z0-9_-]+', label):
        raise ValueError('INVALID_RECORDED_SNAPSHOT_SELECTION')
    snapshot_names = ('ledger', 'documents', 'chunks', 'image-tasks')
    snapshot_paths = {run / (name + '-after.json'): run / (name + '-' + label + '.json') for name in snapshot_names}
    missing = [str(path) for path in snapshot_paths.values() if not path.exists()]
    def snapshot_load(path, default=None):
        return p8.load(snapshot_paths.get(path, path), default)
    # Clone only evaluate's globals; P8 source/module and historical evidence
    # remain untouched. Its source/Gold readers keep their existing behavior.
    evaluate = FunctionType(p8.evaluate.__code__, {**p8.evaluate.__globals__, 'load': snapshot_load})
    result = evaluate(run)
    for alias, actual in snapshot_paths.items():
        result['inputs'][alias.name] = {'status': 'PRESENT' if actual.exists() else 'BLOCKED_MISSING_SELECTED_SNAPSHOT',
            'path': str(actual), 'sha256': artifact_hash(actual), 'snapshot_label': label}
    result['inputs']['snapshot-selection.json'] = {'path': str(run / 'snapshot-selection.json'),
        'sha256': artifact_hash(run / 'snapshot-selection.json'), 'label': label,
        'status': 'PRESENT' if (run / 'snapshot-selection.json').exists() else 'DEFAULT_AFTER'}
    state = p8.load(run / 'state.json', {})
    selected = p8.load(run / 'selection.json', {'datasets': []})
    case_config = {(dataset['name'], case['qid']): case for dataset in selected['datasets'] for case in dataset.get('cases', [])}
    gold = p8.load(run / 'gold-selected.json', {'datasets': []})
    answer_gold = {(dataset['name'], case['qid']): case for dataset in gold['datasets'] for case in dataset.get('cases', [])}
    operations = p8.load(run / 'attempts.json', {})
    captures = [p8.load(path) for path in sorted((run / 'captures').glob('*.json'))]
    stages = [p8.load(path) for path in sorted((run / 'stages').glob('*.json'))]
    requests = [p8.load(path) for path in sorted((run / 'provider-requests').glob('*.json'))]
    after = p8.load(run / ('ledger-' + label + '.json'), [])
    before = p8.load(run / 'ledger-before.json', [])
    before_ids = {row.get('call_id') for row in before}
    delta = [row for row in after if row.get('call_id') not in before_ids] if (run / 'ledger-before.json').exists() else None
    for row in result['cases']:
        if missing:
            row['wiring_status'] = 'BLOCKED'
            row['wiring_reasons'].append('RECORDED_SNAPSHOT_INCOMPLETE_NO_FALLBACK')
        record = row['question_identity']
        found = [capture for capture in captures if capture.get('session_id') == record.get('session') and capture.get('question') == row['question']]
        capture = found[0] if len(found) == 1 else None
        run_id = (capture or {}).get('run_id')
        stage = next((item for item in stages if item.get('run_id') == run_id), None)
        delivery = ocr_delivery(capture, stage, requests, after, required=row['dataset'] == 'omnidocbench')
        matching_calls = [call for call in after if run_id and call.get('request_id', '').startswith(run_id + '/')]
        refs = row['citations']
        prefix = run / ('queries/' + p8.identity(row['dataset'], row['qid']))
        operation_key = 'query-' + p8.identity(row['dataset'], row['qid'])
        query_operation = operations.get(operation_key)
        result_artifact_present = any(Path(str(prefix) + suffix).exists() for suffix in ('-messages.json', '.sse'))
        query_attempted = bool(query_operation) or bool(record.get('session') and record.get('request_id') and result_artifact_present)
        frozen_config = case_config.get((row['dataset'], row['qid']), {})
        row['query_execution_evidence'] = {'attempted': query_attempted,
            'operation_key': operation_key, 'operation_recorded': bool(query_operation),
            'identity_and_result_artifact': bool(record.get('session') and record.get('request_id') and result_artifact_present),
            'actual_model_call_claimed': row['real_provider_receipts']['physical_counts']['response_received'] > 0}
        row['case_execution_status'] = frozen_config.get('execution_status')
        row['case_execution_reason'] = frozen_config.get('execution_reason')
        row['system_answer_gold'] = system_answer_gold(run, row['dataset'], answer_gold.get((row['dataset'], row['qid'])),
            row['plain_answer'], completed=row['completed_assistant'], query_attempted=query_attempted,
            answer_model_called=row['real_provider_receipts']['checks']['chat'], fixed_refusal=row['fixed_fallback'])
        messages = p8.load(Path(str(prefix) + '-messages.json'), {}).get('data', [])
        native_refs = [ref for message in messages if message.get('role') == 'assistant'
            and message.get('id') == (capture or {}).get('message_id') for ref in (message.get('knowledge_references') or [])]
        chunks = {item['data']['chunk_id']: item['data'] for item in p8.load(Path(str(prefix) + '-chunkreadback.json'), [])}
        for ref in refs:
            actual = next((item for item in native_refs if item.get('id') == ref['chunk_id']), {})
            offsets = citation_offsets(actual, chunks.get(ref['chunk_id'], {}))
            ref['offset_readback'] = offsets
            ref['checks']['offsets_match_readback'] = offsets['valid']
            ref['valid'] = ref['valid'] and offsets['valid']
        if refs and not all(ref['valid'] for ref in refs):
            if row['wiring_status'] == 'PASS': row['wiring_status'] = 'FAIL'
            if not all(ref['offset_readback']['valid'] for ref in refs):
                row['wiring_reasons'].append('CITATION_OFFSETS_MISSING_OR_READBACK_MISMATCH')
        row.update(source_code_sha=state.get('source_sha'), run_directory=str(run), server_run_id=run_id,
            quote_validity={'status': 'PASS' if refs and all(ref['valid'] for ref in refs) else
                'NOT_RUN_EXPLICIT_SOURCE_OFFSETS_REQUIRED' if any(ref['offset_readback']['status'].startswith('NOT_RUN') for ref in refs)
                else 'FAIL' if refs else 'NOT_RUN_NO_CITATION',
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
        'wiring_counts': dict(Counter(row['wiring_status'] for row in result['cases'])),
        'source_code_sha': state.get('source_sha'), 'ledger_delta': delta,
        'snapshot_selection': {'label': label, 'status': 'BLOCKED_MISSING_SELECTED_SNAPSHOT' if missing else 'SELECTED',
                               'missing_artifacts': missing, 'fallback_used': False},
        'ledger_before_available': (run / 'ledger-before.json').exists(),
        'method': 'QUOTE_IDENTITY_OCR_PAYLOAD_DELIVERY_AND_GOLD_QUALITY_SEPARATE',
        'official_omni_qa': 'NOT_APPLICABLE_NO_INDEPENDENT_QA_GOLD'}


def evaluate_p9(root):
    selection = p8.load(root / 'selection.json')
    if not selection: raise ValueError('P9_FROZEN_SELECTION_REQUIRED')
    # Actual runner roots can live directly below P9 or below runs/. Avoid
    # treating nested frozen selection helpers as independent executed runs.
    state_paths = [*root.glob('*/state.json'), *root.glob('runs/*/state.json')]
    run_dirs = sorted({path.parent for path in state_paths if (path.parent / 'selection.json').exists()})
    inspected = [inspect_run(path) for path in run_dirs]
    attempts = defaultdict(list)
    availability = defaultdict(list)
    for report in inspected:
        for case in report['cases']:
            identity = (case['dataset'], case['qid'])
            availability[identity].append(case)
            if case['query_execution_evidence']['attempted']: attempts[identity].append(case)
    frozen_cases = []
    for dataset in selection['datasets']:
        if dataset['name'] == 'omnidocbench': continue
        for case in dataset.get('cases', []):
            records = attempts.get((dataset['name'], case['qid']), [])
            unexecuted = availability.get((dataset['name'], case['qid']), [])
            capability = ('CAPABILITY_LIMIT' in case.get('execution_status', '') or any(
                'CAPABILITY_LIMIT' in (item.get('case_execution_status') or '') for item in unexecuted))
            frozen_cases.append({'dataset': dataset['name'], 'qid': case['qid'], 'question': case['question'],
                'status': 'BLOCKED_FROZEN_QUESTION_MISMATCH' if any(item['question'] != case['question'] for item in records)
                    else 'RECORDED_ATTEMPTS' if records else 'BLOCKED_CAPABILITY_LIMIT' if capability else 'NOT_RUN', 'attempts': records,
                'reason': None if records else case.get('execution_reason') or next((item['case_execution_reason'] for item in unexecuted
                    if item.get('case_execution_reason')), None) or 'NO_ACTUAL_QUERY_ATTEMPT_EVIDENCE',
                'actual_query_attempt_count': len(records),
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
