"""Offline P19 evidence scoring. No runtime, database, HTTP or model calls.

P9 historical attempts remain a separate cohort. Full answer text is scored;
metric zero is never automatically labelled a factual error.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import p9_score_real as p9

p8 = p9.p8


def load(path, default=None):
    return p8.load(Path(path), default)


def digest(path):
    return p9.artifact_hash(Path(path))


def is_budget_target(historical_case):
    return (not historical_case.get('attempts')
            and str(historical_case.get('reason', '')).startswith('NOT_RUN_BUDGET'))


def official_longbench(metric_root, cache_dir):
    """Reuse the P9 pinned official source, writing caches only to P19 scoring."""
    receipt = load(metric_root / 'official-longbench/receipt.json', {})
    path = metric_root / 'official-longbench/metrics.py'
    bound = next((r for r in receipt.get('files', []) if Path(r['path']).name == 'metrics.py'), {})
    if (receipt.get('revision') != p8.LONG_REVISION or digest(path) != p8.LONG_METRICS_SHA
            or bound.get('sha256') != p8.LONG_METRICS_SHA):
        raise ValueError('OFFICIAL_LONGBENCH_PINNED_SOURCE_REQUIRED')
    deps = metric_root / 'eval-deps'
    if deps.exists() and str(deps) not in sys.path:
        sys.path.insert(0, str(deps))
    import jieba
    cache_dir.mkdir(parents=True, exist_ok=True)
    jieba.dt.tmp_dir = str(cache_dir)
    jieba.dt.cache_file = 'p19-jieba.cache'
    spec = importlib.util.spec_from_file_location('p19_official_longbench', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {'revision': p8.LONG_REVISION, 'sha256': digest(path), 'source': bound.get('source')}


def answer_metric(dataset, gold, prediction, completed, metric_root, cache_dir):
    base = {'metrics': None, 'included_in_answer_metric_denominator': False}
    if not gold.get('answers'):
        return {**base, 'status': 'NOT_APPLICABLE_NO_ANSWER_GOLD'}
    if not completed or prediction is None:
        return {**base, 'status': 'NOT_RUN_NO_COMPLETED_APP_RESPONSE'}
    if dataset == 'longbench-zh':
        task = gold['qid'].split(':', 1)[0]
        function = {'dureader': 'rouge_zh_score', 'multifieldqa_zh': 'qa_f1_zh_score'}.get(task)
        if not function:
            return {**base, 'status': 'NOT_RUN_UNSUPPORTED_TASK'}
        try:
            module, binding = official_longbench(metric_root, cache_dir)
            score = max(float(getattr(module, function)(prediction, ref)) for ref in gold['answers'])
            result = {'status': 'COMPUTED_OFFICIAL_TASK_METRIC_ON_SMALL_SELECTED_SAMPLE',
                      'metrics': {function: score}, 'task': task, 'function': function, 'binding': binding}
        except (ImportError, OSError, ValueError) as exc:
            return {**base, 'status': 'NOT_RUN_OFFICIAL_SCORER_UNAVAILABLE', 'reason': str(exc)}
    else:
        result = p9.answer_diagnostics(prediction, gold['answers'], dataset)
    return {**result, 'included_in_answer_metric_denominator': result.get('metrics') is not None,
            'evaluation_unit': 'FULL_COMPLETED_APP_RESPONSE_INCLUDING_FIXED_REFUSALS',
            'official_whole_benchmark': 'NOT_CLAIMED'}


def format_diagnostic(dataset, gold, prediction, metrics):
    """Only exact answer-string containment; no semantic/factual inference."""
    if prediction is None or not gold.get('answers'):
        return {'status': 'NOT_APPLICABLE', 'fact_error': 'UNJUDGED'}
    exact = [ref for ref in gold['answers'] if isinstance(ref, str) and ref and ref in prediction]
    em_zero = (metrics or {}).get('hotpot_answer_em') == 0
    return {'status': 'EXACT_GOLD_STRING_PRESENT_WITH_EXTRA_TEXT' if dataset == 'hotpotqa' and em_zero and exact
            else 'NO_DETERMINISTIC_FORMAT_DIAGNOSIS', 'exact_gold_substring_count': len(exact),
            'semantic_correctness': 'UNJUDGED', 'fact_error': 'UNJUDGED',
            'unsupported_claims': 'UNJUDGED', 'citation_entailment': 'UNJUDGED'}


def aggregate(cases):
    """Different datasets/tasks/stages retain independent evaluated denominators."""
    groups = defaultdict(list)
    for row in cases:
        groups[row['dataset']].append(row)
    output = {}
    for dataset, rows in groups.items():
        metrics = defaultdict(list)
        for row in rows:
            for stage, rank in row.get('stage_rankings', {}).items():
                for key, value in (rank.get('metrics') or {}).items():
                    if isinstance(value, (int, float)):
                        metrics[f'{stage}.{key}'].append(value)
            for key, value in (row.get('answer', {}).get('metrics') or {}).items():
                metrics['answer.' + key].append(value)
        refs = [ref for row in rows for ref in row.get('citations', [])]
        output[dataset] = {'selected_cases': len(rows), 'completed_app_answers': sum(row.get('completed_assistant', False) for row in rows),
            'response_counts': {'completed': sum(row.get('completed_assistant', False) for row in rows),
                'fixed_refusal': sum(row.get('completed_assistant', False) and row.get('fixed_refusal', False) for row in rows),
                'confirmed_answer_model_response': sum(row.get('real_provider_receipts', {}).get('checks', {}).get('chat', False) for row in rows)},
            'answer_status_counts': dict(Counter(row['answer']['status'] for row in rows)),
            'metrics': {key: {'mean': sum(values) / len(values), 'evaluated_denominator': len(values),
                              'selected_denominator': len(rows)} for key, values in metrics.items()},
            'citations': {'valid': sum(ref['valid'] for ref in refs), 'existing': len(refs),
                          'cases_with_citations': sum(bool(row.get('citations')) for row in rows),
                          'rate': sum(ref['valid'] for ref in refs) / len(refs) if refs else None}}
    return output


def evaluate(run, selection_path, gold_path, history_path, metric_root, source_records_dir, cache_dir):
    selection = load(selection_path)
    gold = load(gold_path)
    history = load(history_path)
    state = load(run / 'state.json', {})
    snapshot_path = run / 'snapshot-final.json'
    snapshot = load(snapshot_path, {})
    documents = snapshot.get('documents', load(run / 'documents-final.json', []))
    ledger = snapshot.get('ledger', load(run / 'ledger-final.json', []))
    if isinstance(documents, dict):
        documents = list(documents.values())
    doc_rows = {row['id']: row for row in documents}
    histories = {(r['dataset'], r['qid']): r for r in history['cases']}
    golds = {(d['name'], c['qid']): c for d in gold['datasets'] for c in d.get('cases', [])}
    captures = [load(p) for directory in ('captures', 'capture') for p in sorted((run / directory).glob('*.json'))]
    sources = {r['document_id']: r for r in (load(p) for p in source_records_dir.glob('*.json')) if r.get('document_id')}
    selected = [(d, c) for d in selection['datasets'] for c in d.get('cases', [])
                if is_budget_target(histories.get((d['name'], c['qid']), {}))]
    cases = []
    for dataset, case in selected:
        name, qid = dataset['name'], case['qid']
        gold_case = golds[(name, qid)]
        directory = run / 'cases' / p8.identity(name, qid)
        record = load(directory / 'identity.json', {})
        entry = state.get('datasets', {}).get(name, {})
        bindings = {r['id']: source for source, r in entry.get('documents', {}).items()}
        files = {r['source_id']: r for r in dataset.get('files', [])}
        # Selection hashes are a third independent binding in the P9 validator.
        source_records = {doc: {**r, 'selection_sha256': files.get(bindings.get(doc), {}).get('sha256')}
                          for doc, r in sources.items()}
        messages = load(directory / 'messages.json', {})
        messages = messages.get('data', []) if isinstance(messages, dict) else messages
        session = record.get('session') or record.get('session_id')
        candidates = [cap for cap in captures if session and cap.get('session_id') == session
                      and cap.get('question') == case['question']]
        pairs = [(message, cap) for message in messages if message.get('role') == 'assistant'
                 for cap in candidates if message.get('id') == cap.get('message_id')
                 and message.get('request_id') == cap.get('run_id')]
        assistant, capture = pairs[0] if len(pairs) == 1 else ({}, {})
        errors = []
        if record and (record.get('question') != case['question'] or record.get('dataset', name) != name or record.get('qid', qid) != qid):
            errors.append('FROZEN_QUESTION_IDENTITY_MISMATCH')
        if gold_case['question'] != case['question']:
            errors.append('FROZEN_GOLD_QUESTION_MISMATCH')
        if len(pairs) != 1:
            errors.append('UNIQUE_SESSION_MESSAGE_QUESTION_CAPTURE_BINDING_REQUIRED')
        raw = assistant.get('content')
        prediction = p8.plain_answer(raw) if isinstance(raw, str) else None
        completed = bool(assistant.get('is_completed') and prediction and not assistant.get('turn_error_code')
                         and assistant.get('turn_status') not in ('error', 'failed', 'cancelled') and not errors)
        receipts = p8.receipt_facts(ledger, [capture.get('run_id')])
        ranks = p8.classify_stage_execution(p8.stage_rankings(capture, kb=entry.get('kb'), bindings=bindings,
                                                            qrels=gold_case.get('qrels', {})), receipts)
        if errors:
            ranks = {stage: {'status': 'BLOCKED_CASE_IDENTITY', 'metrics': None} for stage in ranks}
        readbacks = load(directory / 'chunkreadback.json', [])
        chunks = {r['data']['chunk_id']: r['data'] for r in readbacks if isinstance(r.get('data'), dict) and r['data'].get('chunk_id')}
        citations = []
        for ref in assistant.get('knowledge_references') or []:
            chunk = chunks.get(ref.get('id'), {})
            result = p8.validate_reference(ref, chunk, kb=entry.get('kb'), bindings=bindings,
                                           source_records=source_records, document_rows=doc_rows)
            offsets = p9.citation_offsets(ref, chunk)
            result['checks']['offsets_match_readback'] = offsets['valid']
            result.update(offset_readback=offsets, valid=result['valid'] and offsets['valid'])
            citations.append(result)
        answer = answer_metric(name, gold_case, prediction, completed, metric_root, cache_dir)
        cases.append({'dataset': name, 'qid': qid, 'question': case['question'], 'source_code_sha': state.get('source_sha'),
            'status': 'BLOCKED' if errors else 'COMPLETED' if completed else 'NOT_RUN_OR_INCOMPLETE',
            'identity_errors': errors, 'question_identity': record, 'server_run_id': capture.get('run_id'),
            'completed_assistant': completed, 'raw_answer': raw, 'plain_answer': prediction,
            'fixed_refusal': capture.get('fixed_fallback') is not None, 'real_provider_receipts': receipts,
            'stage_rankings': ranks, 'qrels_kind': gold_case.get('qrels_kind'), 'answer': answer,
            'citations': citations, 'semantic_review': format_diagnostic(name, gold_case, prediction, answer.get('metrics')),
            'failures': {'missing_citations': completed and not citations, 'source_errors': [r['chunk_id'] for r in citations if not r['valid']],
                         'fact_errors': 'UNJUDGED', 'unsupported_claims': 'UNJUDGED'},
            'supporting_facts': 'NOT_RUN_SENTENCE_MAPPING_REQUIRED' if name == 'hotpotqa' else 'NOT_APPLICABLE',
            'artifact_sha256': {p.name: digest(p) for p in sorted(directory.glob('*')) if p.is_file()}})
    return {'schema': 'p19-offline-quality/v1', 'cases': cases, 'summary_by_dataset': aggregate(cases),
        'execution_counts': dict(Counter(row['status'] for row in cases)),
        'failure_counts_nonexclusive': {
            'identity_or_binding_errors': sum(bool(row['identity_errors']) for row in cases),
            'missing_citations_on_completed_answers': sum(row['failures']['missing_citations'] for row in cases),
            'invalid_existing_citations': sum(len(row['failures']['source_errors']) for row in cases),
            'exact_gold_string_with_extra_text': sum(row['semantic_review']['status'] == 'EXACT_GOLD_STRING_PRESENT_WITH_EXTRA_TEXT' for row in cases),
            'factual_error': 'UNJUDGED', 'unsupported_claim': 'UNJUDGED', 'citation_entailment': 'UNJUDGED'},
        'selection_rule': 'ONLY_P9_NOT_RUN_BUDGET_NO_REPLACEMENT_OR_BEST_ATTEMPT_SELECTION',
        'selected_denominator': len(cases), 'expected_denominator': 17, 'selected_count_valid': len(cases) == 17,
        'source_code_sha': state.get('source_sha'), 'snapshot_status': 'PRESENT' if snapshot_path.exists() else 'BLOCKED_FINAL_SNAPSHOT_MISSING',
        'historical_p9': {'path': str(history_path), 'sha256': digest(history_path),
                          'historical_attempts': sum(len(c.get('attempts', [])) for c in history['cases']),
                          'aggregation_with_current_run': False},
        'inputs': {str(path): digest(path) for path in (selection_path, gold_path, run / 'state.json', snapshot_path)},
        'candidate_pool': 'EXISTING_SELECTED_KB_INDEXES_NOT_OFFICIAL_WHOLE_CORPUS',
        'official_full_benchmark': 'NOT_CLAIMED', 'semantic_review_status': 'UNJUDGED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--gold', type=Path, default=Path('E:/WK/run/p9-20261016/gold-all.json'))
    parser.add_argument('--history', type=Path, default=Path('E:/WK/run/p9-20261016/cohort-results.json'))
    parser.add_argument('--metric-root', type=Path, default=Path('E:/WK/run/p9-20261016/baseline'))
    parser.add_argument('--source-records-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('OUTPUT_EXISTS_PRESERVE_PRIOR_REPORT')
    report = evaluate(args.run_dir, args.selection or args.run_dir / 'selection.json', args.gold, args.history,
                      args.metric_root, args.source_records_dir or args.run_dir / 'sources', args.output.parent / 'eval-cache')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(args.output), 'selected_denominator': report['selected_denominator']}))


if __name__ == '__main__':
    main()
