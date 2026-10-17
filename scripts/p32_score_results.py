"""Score saved P32 normal-chain evidence offline; never execute an application.

Only new reports/cache files are written. Gold, historical results and provider
observations are inputs. No runtime, authority, HTTP or database client is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.p8_score_real import page_text_diagnostic, stage_rankings
from scripts.weknora_port_p19_score import aggregate, answer_metric, format_diagnostic
from backend.weknora_port.models.usage_facts import ProviderUsageFacts, UsageValue

DEFAULT_ROOT = Path('E:/WK/run/p32-20261017')
METRIC_ROOT = Path('E:/WK/run/p9-20261016/baseline')
HISTORICAL_STATE = Path('E:/WK/run/p19-20261016/state.json')


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_id(dataset, value):
    # Omni's page-QID and source filename name the same frozen image. Its
    # retrieval has no QA qrels; retain the established filename source unit.
    return value.rsplit(':', 1)[-1] if dataset == 'omnidocbench' else value


def build_bindings(plan, assets, current, historical):
    """Exact stored hashes/receipts bind UUIDs; model text is never consulted."""
    catalog = defaultdict(list)
    for name, entry in historical.get('datasets', {}).items():
        for original, row in entry.get('documents', {}).items():
            record = row.get('source', {})
            if record.get('sha256'):
                catalog[name].append((source_id(name, original), record['sha256'], record.get('file_name', '')))
    for case in plan['cases']:
        name = case['source_dataset']
        for row in case.get('source_files', []):
            if row.get('source_id') and row.get('sha256'):
                catalog[name].append((source_id(name, row['source_id']), row['sha256'], row.get('file_name', '')))
    explicit = defaultdict(set)
    for case in assets.get('database_observation', {}).get('selected_case_coverage', []):
        name = case['dataset']
        for original in case.get('positive_sources', []):
            for row in original.get('matched_documents', []):
                if row.get('file_hash') == original.get('sha256'):
                    explicit[(name, row['kb_id'], row['knowledge_id'])].add(source_id(name, original['source_id']))
    names_by_kb = defaultdict(set)
    for case in plan['cases']:
        names_by_kb[case['knowledge_base_id']].add(case['source_dataset'])
    bindings, facts = defaultdict(dict), []
    for doc in current.get('documents', []):
        kb, identity, digest = doc['knowledge_base_id'], doc['id'], doc.get('file_hash')
        for name in names_by_kb[kb]:
            matches = {sid for sid, h, _ in catalog[name] if digest and h == digest}
            exact_name = {sid for sid, h, filename in catalog[name]
                          if digest and h == digest and filename == doc.get('file_name')}
            confirmed = explicit.get((name, kb, identity), set())
            selected = confirmed or exact_name or matches
            conflict = bool(confirmed and matches and not confirmed <= matches)
            valid = len(selected) == 1 and not conflict
            if valid:
                bindings[(name, kb)][identity] = next(iter(selected))
            facts.append({'dataset': name, 'knowledge_base_id': kb, 'document_id': identity,
                          'file_hash': digest, 'source_version': doc.get('source_version'),
                          'status': 'BOUND' if valid else 'AMBIGUOUS_OR_UNMAPPED',
                          'source_id': next(iter(selected)) if valid else None,
                          'hash_candidates': sorted(matches), 'hash_collision': len(matches) > 1,
                          'binding_candidates': sorted(selected), 'receipt_conflict': conflict})
    return bindings, facts


def parse_sse(path):
    if not path.exists():
        return {'present': False, 'events': [], 'errors': [], 'answer': '', 'complete': False}
    events, malformed = [], []
    for block in path.read_text(encoding='utf-8-sig').replace('\r\n', '\n').split('\n\n'):
        data = '\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith('data:'))
        if not data or data == '[DONE]':
            continue
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError('non-object SSE data')
            events.append(value)
        except ValueError:
            malformed.append(data[:200])
    errors = [event for event in events if event.get('response_type', event.get('type')) == 'error']
    return {'present': True, 'events': events, 'errors': errors, 'malformed': malformed,
            'answer': ''.join(event.get('content', '') for event in events
                              if event.get('response_type', event.get('type')) == 'answer'),
            'complete': any(event.get('response_type', event.get('type')) == 'complete' for event in events)}


def belongs(request_id, run_id):
    return bool(run_id and isinstance(request_id, str)
                and (request_id == run_id or request_id.startswith(run_id + '/')))


def role_of(request_id):
    parts = str(request_id).split('/')
    if 'answer' not in parts:
        return None
    return next((p for p in reversed(parts) if p in ('cheap', 'expensive')), None)


def usage_value(call, name):
    usage = call.get('usage') or {}
    if isinstance(usage, str):
        usage = json.loads(usage)
    facts = usage.get('provider_usage_facts') or {}
    if facts.get('simulated') is True:
        return None
    fields = facts.get('fields', [])
    fields = fields if isinstance(fields, dict) else dict(fields)
    if name == 'total' and facts:
        # Reuse the existing pure validator, including SF's explicitly reported
        # text input/output + image_tokens=0 rule. This is actual component
        # arithmetic, never a text-token estimate or a monetary billed_unit.
        validated = ProviderUsageFacts(
            facts['semantics'], tuple((key, UsageValue(**value)) for key, value in fields.items()),
            facts.get('numeric_raw', ''), facts.get('raw_sha256', ''), facts.get('derived_total'),
            facts.get('simulated', False), facts.get('observed_lower_bound', 0),
            tuple(facts.get('prior_numeric_raw', [])))
        actual, _lower_bound = validated.accounting()
        return actual
    fact = fields.get(name, {})
    if fact.get('valid') is True and type(fact.get('value')) is int:
        return fact['value']
    key = {'input': 'prompt_tokens', 'output': 'completion_tokens', 'total': 'total_tokens'}[name]
    # Preserve the durable observer's actual reported fields; never estimate or
    # derive new consumption from text, lengths, rates or billed monetary units.
    value = usage.get(key)
    if not facts and usage.get('status') == 'REPORTED' and type(value) is int and value >= 0:
        return value
    return None


def usage_summary(calls):
    result = {'calls': len(calls), 'outcomes': dict(Counter(c.get('outcome') for c in calls))}
    for name in ('input', 'output', 'total'):
        values = [usage_value(row, name) for row in calls]
        known = [value for value in values if value is not None]
        result[name] = {'tokens': sum(known) if calls and len(known) == len(calls) else None,
                        'known_tokens': sum(known) if known else None,
                        'known_calls': len(known), 'unavailable_calls': len(calls) - len(known)}
    result['unknown_outcome_calls'] = sum(c.get('outcome') != 'RESPONSE_RECEIVED' for c in calls)
    result['basis'] = 'snapshot.p32_provider_calls only; missing fields are not zero'
    return result


def seconds(start, end):
    try:
        a, b = (datetime.fromisoformat(str(value).replace('Z', '+00:00')) for value in (start, end))
        return (b - a).total_seconds() if a.tzinfo and b.tzinfo and b >= a else None
    except (TypeError, ValueError):
        return None


def gold_for(case, cache):
    path = Path(case['gold_path'])
    if path not in cache:
        cache[path] = read(path, {})
    bundle = cache[path]
    if case['source_dataset'] == 'omnidocbench':
        found = [r for r in bundle.get('omnidocbench_parsing_gold', {}).get('pages', [])
                 if r['page_id'] == case['qid']]
        if len(found) != 1:
            raise ValueError('OMNI_PAGE_GOLD_IDENTITY_MISSING')
        return found[0]
    found = [r for d in bundle.get('datasets', []) if d['name'] == case['source_dataset']
             for r in d.get('cases', []) if r['qid'] == case['qid']]
    if len(found) != 1:
        raise ValueError('FROZEN_QID_GOLD_IDENTITY_MISSING')
    gold = found[0]
    if (gold['question'] != case['question'] or gold.get('qrels', {}) != case.get('qrels', {})
            or gold.get('answers', []) != case.get('answers', [])):
        raise ValueError('PLAN_QUESTION_OR_GOLD_MISMATCH')
    return gold


def omni_score(case, gold, current):
    expected = {r['sha256'] for r in case.get('source_files', [])}
    docs = [r for r in current.get('documents', []) if r['knowledge_base_id'] == case['knowledge_base_id']
            and r['id'] in case.get('expected_doc_ids', []) and r.get('file_hash') in expected]
    eligible = []
    for doc in docs:
        chunks = [r for r in current.get('omni_ocr_chunks', [])
                  if r.get('knowledge_id') == doc['id'] and r.get('chunk_type') == 'image_ocr'
                  and r.get('metadata', {}).get('source_version') == doc.get('source_version')]
        for chunk in chunks:
            if isinstance(chunk.get('content'), str) and chunk['content']:
                eligible.append((doc, chunk))
    source_bound = (len(eligible) == 1 and gold.get('page_gold_sha256') == case.get('gold_page_sha256'))
    outcomes = ({'persisted': {'ocr_result': eligible[0][1]['content'], 'ocr_text': {'status': 'completed'}}}
                if len(eligible) == 1 else {})
    diagnostic = page_text_diagnostic({'page_id': case['qid']}, gold['gold'], outcomes, source_bound=source_bound)
    return {**diagnostic, 'provenance': 'EXISTING_PARSE_CONFIGURATION_PERSISTED_IMAGE_OCR_TEXT',
            'current_reparse': 'NOT_RUN', 'qa_accuracy': 'NOT_APPLICABLE_NO_OFFICIAL_QA_GOLD',
            'document_ids': [d['id'] for d in docs], 'image_ocr_chunk_ids': [c['id'] for _, c in eligible],
            'parser_engines': sorted({d.get('parser_engine', '') for d in docs}),
            'gold_sha256': gold.get('page_gold_sha256')}


def stage_counts(capture, stages, requests):
    completed = {row['stage']: row for row in stages.get('stages', []) if row.get('status') == 'completed'}
    search, rerank, merge = (completed.get(name, {}) for name in ('retrieval', 'rerank', 'merge'))
    native_reranks = [r for r in stages.get('rerank_calls', []) if isinstance(r, dict)]
    wire_reranks = [r for r in requests if r.get('capability') == 'rerank']
    input_counts = [len(r.get('body', {}).get('documents', [])) for r in wire_reranks
                    if isinstance(r.get('body', {}).get('documents'), list)]
    if not input_counts:
        input_counts = [len(r['documents']) for r in native_reranks if isinstance(r.get('documents'), list)]
    rankings = capture.get('rankings', {})
    return {'search_candidates': len(search['search_results']) if 'search_results' in search else None,
            'rerank_inputs_per_call': input_counts or None,
            'rerank_scored_results_per_call': [len(r['results']) for r in native_reranks
                                               if isinstance(r.get('results'), list)] or None,
            'rerank_retained': len(rerank['rerank_results']) if 'rerank_results' in rerank else None,
            'merge_chunks': len(merge['merge_results']) if 'merge_results' in merge else None,
            'context_chunks': len(rankings['context']) if isinstance(rankings.get('context'), list) else None,
            'context_characters': len(capture.get('context', {}).get('rendered_contexts', '')) if capture else None,
            'stage_completion_fields': {name: row.get('fields', {}) for name, row in completed.items()},
            'rank_units': 'Counts above are native chunks, distinct-source metrics are separate.'}


def score_case(case, root, state, captures, stage_records, requests, calls, bindings, current, gold_cache, metric_root):
    cid, name = case['case_id'], case['source_dataset']
    folder = root / 'cases' / cid
    record = {**state.get('cases', {}).get(cid, {}), **read(folder / 'result.json', {})}
    session = record.get('session_id')
    sse = parse_sse(folder / 'answer.sse')
    sse_runs = {r['id'] for r in sse['events'] if isinstance(r.get('id'), str) and r['id']}
    # The POST's request_id is a client idempotency key; response frames and
    # persisted messages carry the distinct server run ID. Reused UI sessions
    # can contain later follow-ups, so neither first/last assistant is safe.
    expected_run = next(iter(sse_runs)) if len(sse_runs) == 1 else record.get('run_id')
    candidates = [r for r in captures if session and r.get('session_id') == session
                  and r.get('question') == case['question']
                  and (not expected_run or r.get('run_id') == expected_run)]
    capture = candidates[0] if len(candidates) == 1 else {}
    run_id = capture.get('run_id') or expected_run
    matching_calls = [c for c in calls if int(c['tenant_id']) == int(case['tenant_id'])
                      and session and c.get('session_id') == session and belongs(c.get('request_id'), run_id)]
    matching_requests = [r for r in requests if belongs(r.get('request_id'), run_id)]
    stage = next((r for r in stage_records if r.get('run_id') == run_id and r.get('session_id') == session), {})
    messages = read(folder / 'messages.json', {})
    messages = messages.get('data', []) if isinstance(messages, dict) else messages
    assistants = [m for m in messages if m.get('role') == 'assistant' and run_id and m.get('request_id') == run_id]
    assistant = assistants[0] if len(assistants) == 1 else {}
    errors = []
    if len(sse_runs) > 1:
        errors.append('MULTIPLE_SERVER_RUN_IDS_IN_CASE_SSE')
    if record.get('run_id') and expected_run and record['run_id'] != expected_run:
        errors.append('EXPLICIT_SERVER_RUN_ID_MISMATCH')
    if record.get('question') and record['question'] != case['question']:
        errors.append('EXECUTED_QUESTION_MISMATCH')
    if len(candidates) != 1:
        errors.append('UNIQUE_PREPARATION_CAPTURE_MISSING')
    if len(assistants) != 1:
        errors.append('UNIQUE_PERSISTED_ASSISTANT_MISSING')
    if capture and capture.get('message_id') != assistant.get('id'):
        errors.append('CAPTURE_MESSAGE_ID_MISMATCH')
    content = assistant.get('content')
    text = content if isinstance(content, str) else None
    if sse.get('malformed'):
        errors.append('MALFORMED_SSE')
    if text is not None and sse['present'] and sse['answer'] != text:
        errors.append('SSE_PERSISTED_ANSWER_MISMATCH')
    final_calls = [c for c in matching_calls if role_of(c.get('request_id'))]
    final_requests = [r for r in matching_requests if role_of(r.get('request_id'))]
    received_final = any(c.get('outcome') == 'RESPONSE_RECEIVED' for c in final_calls)
    fixed = bool(capture and capture.get('fixed_fallback') is not None)
    terminal_errors = [e for e in sse['errors'] if not e.get('data', {}).get('non_terminal')]
    app_error = assistant.get('turn_error_code') or assistant.get('error_code')
    completed = bool(assistant.get('is_completed') and text and sse['complete'] and not app_error
                     and not terminal_errors and assistant.get('turn_status') not in ('failed', 'error', 'cancelled')
                     and not errors and (received_final or fixed))
    if not received_final and not fixed:
        errors.append('NO_SUCCESSFUL_FINAL_ROLE_CALL_HTTP200_IS_NOT_SUCCESS')
    gold = gold_for(case, gold_cache)
    if name == 'omnidocbench':
        answer = {'status': 'NOT_APPLICABLE_NO_OFFICIAL_QA_GOLD', 'metrics': None,
                  'included_in_answer_metric_denominator': False}
        parsing = omni_score(case, gold, current)
    else:
        answer = answer_metric(name, gold, text, completed, metric_root, root / 'scoring-cache')
        parsing = None
    ranks = stage_rankings(capture, kb=case['knowledge_base_id'],
                           bindings=bindings.get((name, case['knowledge_base_id']), {}), qrels=case.get('qrels', {}))
    if not capture or 'EXECUTED_QUESTION_MISMATCH' in errors:
        ranks = {key: {'status': 'NOT_RUN_VALID_CAPTURE_MISSING', 'metrics': None} for key in ('search', 'rerank', 'context')}
    all_context = {r[2] for r in capture.get('rankings', {}).get('context', []) if len(r) == 3}
    source_map = bindings.get((name, case['knowledge_base_id']), {})
    context_sources = {source_map[doc] for doc in all_context if doc in source_map}
    positives = {key for key, grade in case.get('qrels', {}).items() if grade > 0}
    operations = read(root / 'operations.json', {})
    operation = operations.get('query-' + cid, {})
    timing = {'post_seconds': seconds(operation.get('at'), operation.get('finished_at')),
              'post_started_at': operation.get('at'), 'post_finished_at': operation.get('finished_at'),
              'state_case_seconds': seconds(record.get('started_at'), record.get('completed_at')),
              'state_case_basis': 'May include history readback; not substituted for POST latency.',
              'reuse_stage_a': bool(case.get('reuse_stage_a'))}
    for key in ('post_seconds', 'post_elapsed_seconds', 'request_duration_seconds'):
        if timing['post_seconds'] is None and type(record.get(key)) in (int, float):
            timing['post_seconds'] = record[key]
            timing['post_basis'] = 'case result.' + key
    return {'case_id': cid, 'dataset': name, 'qid': case['qid'], 'question': case['question'],
            'tenant_id': case['tenant_id'], 'knowledge_base_id': case['knowledge_base_id'],
            'session_id': session, 'client_request_id': record.get('request_id'),
            'run_id': run_id, 'message_id': assistant.get('id'),
            'status': 'COMPLETED' if completed else 'FAILED_OR_INCOMPLETE' if record or sse['present'] else 'NOT_RUN',
            'completed_assistant': completed,
            'http_status': record.get('http_status'), 'identity_or_execution_errors': errors,
            'application_error': app_error, 'sse_errors': sse['errors'], 'sse_complete': sse['complete'],
            'sse_equals_persisted': text is not None and sse['present'] and sse['answer'] == text,
            'raw_answer': text, 'fixed_refusal': fixed, 'fallback_system_prompt': capture.get('context', {}).get('system_prompt_override'),
            'answer': answer, 'parsing': parsing, 'stage_rankings': ranks,
            'candidate_counts': stage_counts(capture, stage, matching_requests),
            'positive_sources_in_full_context': sorted(positives & context_sources),
            'positive_sources_missing_from_full_context': sorted(positives - context_sources),
            'context_source_identity_complete': all(doc in source_map for doc in all_context),
            'real_provider_receipts': {'checks': {'chat': received_final}},
            'actual_final_roles': sorted({role_of(c['request_id']) for c in final_calls}),
            'provider_finish_reasons': [{'request_id': r['request_id'], 'model': r.get('model'),
                                         'finish_reason': r.get('finish_reason')} for r in final_requests],
            'provider_calls': matching_calls, 'usage': usage_summary(matching_calls), 'timing': timing,
            'semantic_review': format_diagnostic(name, case, text, answer.get('metrics')),
            'citation_validity': 'NOT_APPLICABLE_PUBLIC_CITATIONS_DISABLED',
            'supporting_fact_or_page_score': 'NOT_RUN_SENTENCE_MAPPING_OR_PAGE_BASE_PROOF_UNAVAILABLE',
            'quality_scope': case.get('score_applicability'),
            'artifacts': {p.name: sha(p) for p in folder.glob('*') if p.is_file()}}


def evaluate(root, metric_root=METRIC_ROOT):
    plan, state = read(root / 'plan.json'), read(root / 'state.json')
    snapshot = read(root / 'snapshot.json')
    if not plan or not state or not snapshot or not isinstance(snapshot.get('p32_provider_calls'), list):
        raise ValueError('PLAN_STATE_AND_P32_PROVIDER_CALL_DIFF_REQUIRED')
    current, assets = read(root / 'source-bindings.json', {}), read(root / 'asset-audit.json', {})
    bindings, binding_facts = build_bindings(plan, assets, current, read(HISTORICAL_STATE, {}))
    captures, stages, requests, reranks = [], [], [], []
    for directory in sorted(root.glob('evidence-*')):
        for name, target in (('captures', captures), ('stages', stages), ('provider-requests', requests), ('rerank', reranks)):
            target.extend(read(path) for path in sorted((directory / name).glob('*.json')))
    for record in stages:
        native = [r for r in reranks if r.get('run_id') == record.get('run_id')]
        record['rerank_calls'] = [call for r in native for call in r.get('calls', [])]
    calls = snapshot['p32_provider_calls']
    identities = [(c['tenant_id'], c['subject_id'], c['call_id']) for c in calls]
    if len(identities) != len(set(identities)):
        raise ValueError('DUPLICATE_PROVIDER_CALLS_IN_SNAPSHOT_DIFF')
    gold_cache = {}
    cases = [score_case(c, root, state, captures, stages, requests, calls, bindings, current, gold_cache, metric_root)
             for c in plan['cases']]
    allocations = Counter((c['tenant_id'], c['subject_id'], c['call_id']) for row in cases for c in row['provider_calls'])
    if any(count > 1 for count in allocations.values()):
        raise ValueError('PROVIDER_CALL_ASSIGNED_TO_MULTIPLE_CASES')
    extras = [c for c in calls if (c['tenant_id'], c['subject_id'], c['call_id']) not in allocations]
    extra_groups = defaultdict(list)
    for call in extras:
        observed = next((cap for cap in captures if cap.get('session_id') == call.get('session_id')
                         and belongs(call.get('request_id'), cap.get('run_id'))), None)
        category = ('UI_FOLLOWUP_OR_OTHER_QUERY' if observed else
                    'UPLOAD_OR_INDEXING' if '/index/' in call.get('request_id', '') else 'UNASSIGNED_OTHER')
        extra_groups[category].append({**call, 'observed_question': observed.get('question') if observed else None})
    summary = aggregate(cases)
    for name, dataset in summary.items():
        parsed = [row['parsing']['metrics']['page_text_normalized_edit_distance'] for row in cases
                  if row['dataset'] == name and row.get('parsing') and row['parsing'].get('metrics')]
        if parsed:
            dataset['persisted_page_text_ned'] = {'mean': sum(parsed) / len(parsed), 'evaluated_denominator': len(parsed),
                                                   'current_reparse': False, 'official_qa_accuracy': 'NOT_APPLICABLE'}
    inputs = [root / name for name in ('plan.json', 'state.json', 'snapshot.json', 'source-bindings.json', 'asset-audit.json')]
    inputs.extend(gold_cache)
    return {'schema': 'p32-offline-scored-results/v1', 'scored_at': datetime.now(timezone.utc).isoformat(),
            'cases': cases, 'summary_by_dataset': summary, 'selected_denominator': len(plan['cases']),
            'execution_counts': dict(Counter(row['status'] for row in cases)),
            'source_bindings': binding_facts, 'usage_all_p32_diff': usage_summary(calls),
            'usage_planned_cases': usage_summary([c for row in cases for c in row['provider_calls']]),
            'extra_provider_calls_separate_from_case_scores': {
                name: {'usage': usage_summary(rows), 'calls': rows} for name, rows in extra_groups.items()},
            'inputs': {str(path): sha(path) for path in inputs if path.exists()},
            'method': 'EXISTING_PURE_P9_P19_SCORES_ON_CURRENT_NORMAL_CHAIN_OUTPUTS',
            'official_full_benchmark': 'NOT_CLAIMED', 'semantic_correctness': 'UNJUDGED_SEPARATE_SOURCE_REVIEW',
            'cloud_calls_by_scorer': 0, 'database_connections_by_scorer': 0,
            'gold_or_historical_result_edits': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--metric-root', type=Path, default=METRIC_ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    target = args.output or args.root / 'scored.json'
    if target.exists():
        raise ValueError('OUTPUT_EXISTS_PRESERVE_PRIOR_SCORES')
    report = evaluate(args.root, args.metric_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(target), 'cases': len(report['cases']),
                      'execution_counts': report['execution_counts']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
