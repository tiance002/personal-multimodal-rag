"""Offline scorer for the bounded RAG-M3 A/B executor bundle.

The executor receives only the frozen plan and normal query inputs. This scorer
reads the recorded outputs after execution, then reads existing P32/P9 Gold.
It never imports the application runtime, calls a provider, or changes Gold.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval_center.weknora_scoring import answer_diagnostics
from scripts import p32_score_results as p32
from scripts import p8_score_real as p8
from scripts import weknora_port_p19_score as p19

DEFAULT_ROOT = Path('E:/WK/run/rag-m3-20261017')
DEFAULT_P32_PLAN = Path('E:/WK/run/p32-20261017/plan.json')
DEFAULT_P32_ROOT = Path('E:/WK/run/p32-20261017')
FROZEN_P32_RESULTS = Path('E:/RAG-Worktrees/weknora-python-port-20261013/docs/evaluation/P32_BROWSER_AND_SIX_DATASET_RAG.results.json')
DEFAULT_RUBRIC = DEFAULT_ROOT / 'scoring-rubric.json'
DEFAULT_METRIC_ROOT = Path('E:/WK/run/p9-20261016/baseline')
PLAN_SHA256 = '5bdc2932f1da071fc10079a508c6a683918844d7b677d10923634ab000624674'
P32_PLAN_SHA256 = '88a44dab00333521dd1c2d1dd845ff423dc352fe14bc000cda15414735bbb318'
RUBRIC_SHA256 = 'a4e3323cfcb62e524b11eaea109ec0fdb95c0f70343e6a139d6099b52055d8c2'
P32_RESULTS_SHA256 = 'ce1c914e423a8a9b21d52a00e97b6b10e12862ad65fdd183689a871ce014b1dd'
RETRIEVAL_DATASETS = {'scifact', 'miracl-zh'}


def read(path: Path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metric_result_not_run(reason='NOT_RUN_NO_EXECUTOR_OUTPUT'):
    return {'status': reason, 'metrics': None}


def completed_record(record):
    if record.get('error_code') or record.get('status') in ('FAILED', 'failed', 'ERROR'):
        return False
    complete = record.get('complete')
    if complete is True or record.get('completed') is True:
        return True
    if isinstance(complete, dict) and complete.get('response_type', complete.get('type')) == 'complete':
        return True
    if isinstance(complete, str) and complete.lower() in ('complete', 'completed'):
        return True
    return record.get('status') in ('COMPLETED', 'COMPLETE', 'completed')


def normalize_rankings(record):
    """Accept source-ID rankings or the P32 captured (chunk, KB, document) tuples."""
    rankings = record.get('rankings')
    if isinstance(rankings, dict):
        return {name: rows for name, rows in rankings.items() if name in ('search', 'rerank', 'context')}
    capture = record.get('capture')
    if isinstance(capture, dict) and isinstance(capture.get('rankings'), dict):
        # P32 scorer has already performed scope and immutable source binding.
        # Prefer its source-ID projection when present.
        return capture['rankings']
    stages = record.get('stage_rankings')
    if isinstance(stages, dict):
        return {name: value.get('ranked_source_ids') if isinstance(value, dict) else value
                for name, value in stages.items() if name in ('search', 'rerank', 'context')}
    return {}


def source_id_rankings(capture, *, kb, bindings):
    """Project P32-style native chunk/KB/document rows to frozen source IDs."""
    projected = {}
    captured = (capture or {}).get('rankings', {})
    for stage in ('search', 'rerank', 'context'):
        rows = captured.get(stage)
        if rows is None:
            continue
        if not isinstance(rows, list):
            projected[stage] = None
            continue
        sources, valid = [], True
        for row in rows:
            if isinstance(row, str):
                source = row
            elif isinstance(row, (list, tuple)) and len(row) == 3:
                _chunk_id, row_kb, document_id = row
                source = bindings.get(document_id) if row_kb == kb else None
            else:
                source = None
            if not source:
                valid = False
                break
            if source not in sources:
                sources.append(source)
        projected[stage] = sources if valid else None
    return projected


def load_executor_bundle(root, plan, p32_plan, p32_root):
    """Join per-case files and passive executor evidence by saved run ID."""
    snapshot = read(root / 'snapshot.json', {}) or {}
    summary = read(root / 'summary.json', {}) or {}
    summary_cases = summary.get('cases', {}) if isinstance(summary.get('cases'), dict) else {}
    assets = read(p32_root / 'asset-audit.json', {}) or {}
    current = read(p32_root / 'source-bindings.json', {}) or {}
    historical = read(p32.HISTORICAL_STATE, {}) or {}
    try:
        bindings, binding_facts = p32.build_bindings(p32_plan, assets, current, historical)
    except (KeyError, TypeError, ValueError):
        bindings, binding_facts = {}, []
    by_kb = {}
    for (_dataset, kb), mapping in bindings.items():
        by_kb.setdefault(kb, {}).update(mapping)

    evidence_root = root / 'evidence-332'
    input_paths = [path for path in (root / 'snapshot.json', root / 'summary.json') if path.exists()]
    evidence = {}
    for folder in ('captures', 'stages', 'configuration', 'provider-timing', 'api-timing', 'provider-requests'):
        rows = []
        for path in sorted((evidence_root / folder).glob('*.json')):
            row = read(path)
            if isinstance(row, dict):
                rows.append(row)
                input_paths.append(path)
        evidence[folder] = rows
    calls = snapshot.get('provider_calls') or snapshot.get('p32_provider_calls') or []
    def belongs_to_run(row, run_id):
        return bool(run_id and (row.get('run_id') == run_id
                                or p32.belongs(row.get('request_id'), run_id)))
    loaded = {}
    for case in plan.get('cases', []):
        for variant in ('A', 'B'):
            base = root / 'cases' / f"{case['case_id']}-{variant}"
            result_paths = [base / 'result.json', *sorted(base.glob('attempts/*/result.json'))]
            result_paths.extend(path / 'result.json' for path in sorted((root / 'cases').glob(f"{case['case_id']}-{variant}-attempt*")))
            attempt_rows = []
            for result_path in result_paths:
                folder = result_path.parent
                artifacts = (result_path, folder / 'answer.sse', folder / 'messages.json',
                             folder / 'client-timing.json', folder / 'configuration.json', folder / 'request.json')
                input_paths.extend(path for path in artifacts if path.exists())
                result = read(result_path)
                sse = p32.parse_sse(folder / 'answer.sse')
                request = read(folder / 'request.json', {}) or {}
                local_config = read(folder / 'configuration.json', {}) or {}
                if not isinstance(result, dict):
                    # Preserve early gate failures whose SSE/timing were
                    # durably recorded before a normal result.json existed.
                    if not sse.get('events'):
                        continue
                    errors = [event for event in sse.get('errors', [])
                              if not (isinstance(event.get('data'), dict) and event['data'].get('non_terminal'))]
                    complete_events = [event for event in sse.get('events', [])
                                       if event.get('response_type', event.get('type')) == 'complete']
                    error_code = next((event.get('data', {}).get('error_code') for event in errors
                                       if isinstance(event.get('data'), dict) and event['data'].get('error_code')), None)
                    server_runs = {event.get('id') for event in sse.get('events', []) if event.get('id')}
                    result = {'case_id': case['case_id'], 'variant': variant,
                              'run_id': next(iter(server_runs)) if len(server_runs) == 1 else None,
                              'session_id': local_config.get('before', {}).get('id'),
                              'answer': sse.get('answer') or '', 'status': 'FAILED' if errors else 'RECORDED',
                              'error_code': error_code,
                              'complete': bool(complete_events and not errors),
                              'http_status': (read(folder / 'client-timing.json', {}) or {}).get('http_status')}
                if result.get('case_id') != case['case_id'] or result.get('variant') != variant:
                    raise ValueError('M3_CASE_RESULT_IDENTITY_MISMATCH')
                run_id = result.get('run_id') or (next(iter({event.get('id') for event in sse.get('events', []) if event.get('id')}), None))
                session_id = result.get('session_id') or local_config.get('before', {}).get('id')
                capture_rows = [row for row in evidence['captures'] if run_id and row.get('run_id') == run_id]
                stage_rows = [row for row in evidence['stages'] if run_id and row.get('run_id') == run_id]
                configuration_rows = [row for row in evidence['configuration'] if belongs_to_run(row, run_id)]
                timing_rows = [row for row in evidence['provider-timing'] if belongs_to_run(row, run_id)]
                api_timing_rows = [row for row in evidence['api-timing'] if belongs_to_run(row, run_id)]
                request_rows = [row for row in evidence['provider-requests']
                                if run_id and p32.belongs(row.get('request_id'), run_id)]
                selected_calls = [row for row in calls if run_id and p32.belongs(row.get('request_id'), run_id)
                                  and (not session_id or not row.get('session_id') or row.get('session_id') == session_id)]
                capture = capture_rows[0] if len(capture_rows) == 1 else {}
                native = by_kb.get(case['knowledge_base_id'], {})
                terminal_errors = [event for event in sse.get('errors', [])
                                   if not (isinstance(event.get('data'), dict) and event['data'].get('non_terminal'))]
                error_code = result.get('error_code') or next((event.get('data', {}).get('error_code') for event in terminal_errors
                                  if isinstance(event.get('data'), dict) and event['data'].get('error_code')), None)
                gate_failure = error_code == 'RUNTIME_DURABLE_GATE_REQUIRED' and not selected_calls
                success = completed_record(result) and not terminal_errors
                attempt_status = ('PRE_SEND_GATE_REJECTED' if gate_failure else 'COMPLETED' if success
                                  else 'FAILED_OR_INCOMPLETE' if result.get('status') in ('FAILED', 'failed') or terminal_errors
                                  else 'RECORDED_INCOMPLETE')
                normalized = {
                    **result, 'run_id': run_id, 'session_id': session_id,
                    'attempt_number': result.get('attempt_number') or (1 if folder == base else None),
                    'artifact_dir': result.get('artifact_dir') or str(folder),
                    'completed': success, 'attempt_status': attempt_status,
                    'capture': capture,
                    'source_binding_by_document': native,
                    'rankings': source_id_rankings(capture, kb=case['knowledge_base_id'], bindings=native),
                    'provider_calls': selected_calls,
                    'configuration': local_config or configuration_rows,
                    'provider_timing': timing_rows, 'api_timing': api_timing_rows,
                    'provider_requests': request_rows,
                    'latency': {
                        'client': read(folder / 'client-timing.json', result.get('client_timing')),
                        'stages': stage_rows[0].get('stages') if len(stage_rows) == 1 else None,
                        'provider': timing_rows or None, 'api': api_timing_rows or None,
                    },
                    'summary': summary_cases.get(f"{case['case_id']}-{variant}"),
                }
                normalized['ocr_source_evidence'] = [
                    {'source_id': native.get(row.get('knowledge_id') or row.get('document_id')),
                     'chunk_id': row.get('id'), 'chunk_type': row.get('chunk_type')}
                    for row in capture.get('evidence', []) if row.get('chunk_type') == 'image_ocr'
                ]
                understand = next((row for row in (stage_rows[0].get('stages', []) if len(stage_rows) == 1 else [])
                                   if row.get('stage') in ('understand', 'query_understand')), {})
                normalized['intent'] = result.get('intent') or understand.get('intent') or understand.get('fields', {}).get('intent')
                normalized['rewrite'] = (result.get('rewrite') or understand.get('rewrite_query')
                                         or understand.get('fields', {}).get('rewrite_query'))
                normalized['route'] = result.get('route') or normalized['summary']
                attempt_rows.append(normalized)
            if attempt_rows:
                loaded[(case['case_id'], variant)] = attempt_rows
    input_hashes = {str(path): file_sha(path) for path in input_paths if path.exists()}
    return loaded, binding_facts, snapshot, summary, input_hashes


def score_retrieval(dataset, gold, ranking):
    if dataset not in RETRIEVAL_DATASETS:
        return metric_result_not_run('NOT_APPLICABLE_RETRIEVAL_QRELS_NOT_USED_FOR_DATASET')
    if ranking is None:
        return {'status': 'BLOCKED_SOURCE_IDENTITY_SCOPE_OR_BINDING_MISMATCH', 'metrics': None}
    if not isinstance(ranking, list):
        return metric_result_not_run()
    qrels = gold.get('qrels') or {}
    if not qrels:
        return metric_result_not_run('NOT_RUN_FROZEN_QRELS_MISSING')
    try:
        metrics = p8.ranking_metrics(ranking, qrels, k=10)
    except ValueError as exc:
        return {'status': 'BLOCKED_INVALID_RANKING_OR_QRELS', 'reason': str(exc), 'metrics': None}
    return {'status': 'COMPUTED_FROZEN_SOURCE_QRELS', 'unit': 'FROZEN_SOURCE_ID', 'k': 10,
            'ranked_source_ids': ranking, 'metrics': metrics}


def source_identity_comparison(record_a, record_b):
    a, b = normalize_rankings(record_a), normalize_rankings(record_b)
    comparison = {}
    for stage in ('search', 'rerank', 'context'):
        left, right = a.get(stage), b.get(stage)
        if (stage in a and left is None) or (stage in b and right is None):
            comparison[stage] = {'status': 'BLOCKED_SOURCE_IDENTITY_SCOPE_OR_BINDING_MISMATCH',
                                 'a_source_ids': left, 'b_source_ids': right}
            continue
        if not isinstance(left, list) or not isinstance(right, list):
            comparison[stage] = {'status': 'NOT_RUN_MISSING_STAGE_SOURCE_IDENTITIES',
                                 'a_source_ids': left, 'b_source_ids': right}
            continue
        left_set, right_set = set(left), set(right)
        comparison[stage] = {
            'status': 'COMPARED',
            'a_source_ids': left,
            'b_source_ids': right,
            'same_ordered_source_ids': left == right,
            'same_source_set': left_set == right_set,
            'a_only_source_ids': [source for source in left if source not in right_set],
            'b_only_source_ids': [source for source in right if source not in left_set],
        }
    return comparison


def score_answer(case, gold, record, metric_root, cache_dir):
    dataset = case['source_dataset']
    answer = record.get('answer', record.get('raw_answer'))
    completed = completed_record(record)
    if dataset in ('scifact', 'miracl-zh'):
        return {'status': 'NOT_APPLICABLE_NO_ANSWER_GOLD', 'metrics': None,
                'interpretation': 'Retrieval qrels only; answer accuracy is not inferred.'}
    if dataset == 'omnidocbench':
        return {'status': 'NOT_APPLICABLE_NO_OFFICIAL_QA_GOLD', 'metrics': None,
                'ocr_diagnostic': score_omni(case, gold, record),
                'official_qa_accuracy': 'NOT_APPLICABLE'}
    if not completed or not isinstance(answer, str):
        return metric_result_not_run('NOT_RUN_NO_COMPLETED_ANSWER')
    if dataset == 'longbench-zh':
        result = p19.answer_metric(dataset, gold, answer, completed, metric_root, cache_dir)
        return {**result, 'task_specific': True, 'tasks_must_not_be_combined': True}
    if dataset == 'hotpotqa':
        return answer_diagnostics(answer, gold.get('answers', []), dataset)
    if dataset == 'mmlongbench-doc':
        return {**answer_diagnostics(answer, gold.get('answers', []), dataset),
                'typed_gold': True, 'semantic_judgment': 'NOT_AUTOMATED_SEPARATE_MANUAL_REVIEW'}
    return {'status': 'NOT_APPLICABLE_NO_AUTOMATIC_ANSWER_METRIC', 'metrics': None}


def score_omni(case, gold, record):
    text = record.get('ocr_result') or record.get('ocr_text')
    if not isinstance(text, str) or not text:
        target_raw = ((case.get('source_files') or [{}])[0]).get('source_id')
        # Omni's frozen page-QID includes a dataset/hash prefix, while P32's
        # bound source unit for this dataset is the exact source filename.
        # Compare stage rankings in that same bound identity space.
        target = p32.source_id('omnidocbench', target_raw) if target_raw else None
        stage_sources = normalize_rankings(record)
        ocr_sources = {row.get('source_id') for row in record.get('ocr_source_evidence', []) if row.get('source_id')}
        target_documents = {document_id for document_id, source_id in
                            (record.get('source_binding_by_document') or {}).items()
                            if source_id == target}
        completed_stages = [stage for stage in (record.get('latency') or {}).get('stages', [])
                            if stage.get('status') == 'completed']
        target_ocr_chunk_ids = sorted({row.get('id') for stage in completed_stages
                                       if stage.get('stage') == 'retrieval'
                                       for row in stage.get('search_results', [])
                                       if row.get('chunk_type') == 'image_ocr'
                                       and row.get('knowledge_id') in target_documents
                                       and row.get('id')})
        raw_stage_rankings = (record.get('capture') or {}).get('rankings', {})
        exact_ocr_stage_presence = {}
        for stage in ('search', 'rerank', 'context'):
            rows = raw_stage_rankings.get(stage)
            if not isinstance(rows, list):
                exact_ocr_stage_presence[stage] = None
                continue
            exact_ocr_stage_presence[stage] = any(
                isinstance(row, (list, tuple)) and len(row) == 3
                and row[0] in target_ocr_chunk_ids and row[2] in target_documents
                for row in rows
            ) if target_ocr_chunk_ids else None
        source_stage_presence = {
            stage: target in stage_sources.get(stage, [])
            if target and isinstance(stage_sources.get(stage), list) else None
            for stage in ('search', 'rerank', 'context')
        }
        return {'status': 'OCR_SOURCE_PRESENCE_DIAGNOSTIC_ONLY', 'metrics': None,
                'target_source_id': target,
                'target_source_id_raw': target_raw,
                'target_source_identity_in_stages': source_stage_presence,
                'target_source_in_stages': source_stage_presence,
                'target_ocr_chunk_ids': target_ocr_chunk_ids,
                'target_ocr_chunk_in_stages': exact_ocr_stage_presence,
                'ocr_chunk_ids_observed': [row.get('chunk_id') for row in record.get('ocr_source_evidence', [])],
                'target_ocr_chunk_observed_in_evidence': target in ocr_sources if target else None,
                'current_reparse': 'NOT_RUN', 'official_qa_accuracy': 'NOT_APPLICABLE'}
    bound = (record.get('ocr_source_bound') is True
             and record.get('ocr_page_id') == case['qid']
             and record.get('ocr_gold_sha256') == gold.get('page_gold_sha256'))
    outcomes = {'recorded': {'ocr_result': text, 'ocr_text': {'status': 'completed'}}}
    result = p8.page_text_diagnostic({'page_id': case['qid']}, gold.get('gold'), outcomes,
                                     source_bound=bound)
    return {**result, 'official_qa_accuracy': 'NOT_APPLICABLE',
            'diagnostic_only': True, 'semantic_qa': 'NOT_SCORED'}


def score_variant(case, p32_case, gold, record, metric_root, cache_dir):
    if record is None:
        return {'case_id': case['case_id'], 'dataset': case['source_dataset'], 'variant': None,
                'status': 'NOT_RUN', 'answer': metric_result_not_run(),
                'stage_rankings': {stage: metric_result_not_run() for stage in ('search', 'rerank', 'context')},
                'route': None, 'models': [], 'usage': {'status': 'NOT_RUN'}, 'latency': None}
    rankings = normalize_rankings(record)
    stage_scores = {stage: (score_retrieval(case['source_dataset'], gold, rankings[stage])
                            if stage in rankings else metric_result_not_run())
                    for stage in ('search', 'rerank', 'context')}
    answer = score_answer(case, gold, record, metric_root, cache_dir)
    calls = record.get('provider_calls') or record.get('calls') or []
    if not isinstance(calls, list):
        calls = []
    latency = record.get('latency') or record.get('timing')
    summary = record.get('summary') if isinstance(record.get('summary'), dict) else {}
    return {
        'case_id': case['case_id'], 'dataset': case['source_dataset'],
        'variant': record.get('variant'),
        'status': 'COMPLETED' if completed_record(record) else record.get('status', 'RECORDED'),
        'intent': record.get('intent') or summary.get('intent'),
        'rewrite': record.get('rewrite') or summary.get('rewrite'),
        'answer_text': record.get('answer', record.get('raw_answer')),
        'answer': answer, 'stage_rankings': stage_scores,
        'source_ids': rankings,
        'route': record.get('route') or summary.get('route') or summary.get('roles'),
        'configuration': record.get('configuration'),
        'models': [{'capability': call.get('capability'), 'role': call.get('role'),
                    'provider': call.get('provider'), 'model': call.get('model') or call.get('model_reported')}
                   for call in calls],
        'provider_calls': calls,
        'usage': p32.usage_summary(calls),
        'latency': latency,
        'ocr_diagnostic': score_omni(p32_case, gold, record) if case['source_dataset'] == 'omnidocbench' else None,
    }


def attempt_summary(rows):
    return [{'attempt_number': row.get('attempt_number'), 'run_id': row.get('run_id'),
             'status': row.get('attempt_status'), 'error_code': row.get('error_code'),
             'artifact_dir': row.get('artifact_dir'),
             'provider_call_count': len(row.get('provider_calls', [])),
             'http_status': row.get('http_status'), 'latency': row.get('latency')}
            for row in rows]


def load_gold(case, p32_cases, cache):
    frozen = p32_cases.get(case['case_id'])
    if frozen is None:
        # Conversation controls have no dataset Gold, but keep their recorded
        # evidence, routing and latency available for the A/B comparison.
        return None, {}, 'NOT_APPLICABLE_NO_FROZEN_DATASET_GOLD'
    if frozen.get('source_dataset') != case['source_dataset'] or frozen.get('question') != case['question']:
        return frozen, None, 'BLOCKED_M3_PLAN_P32_GOLD_IDENTITY_MISMATCH'
    try:
        return frozen, p32.gold_for(frozen, cache), None
    except (OSError, ValueError, KeyError) as exc:
        return frozen, None, 'BLOCKED_FROZEN_GOLD_UNAVAILABLE:' + str(exc)


def evaluate(root: Path, p32_plan_path: Path, p32_root: Path, rubric_path: Path,
             metric_root: Path):
    plan_path = root / 'plan.json'
    if file_sha(plan_path) != PLAN_SHA256:
        raise ValueError('FROZEN_M3_PLAN_SHA256_MISMATCH')
    if file_sha(p32_plan_path) != P32_PLAN_SHA256:
        raise ValueError('FROZEN_P32_PLAN_SHA256_MISMATCH')
    if file_sha(rubric_path) != RUBRIC_SHA256:
        raise ValueError('FROZEN_M3_RUBRIC_SHA256_MISMATCH')
    if file_sha(FROZEN_P32_RESULTS) != P32_RESULTS_SHA256:
        raise ValueError('FROZEN_P32_RESULTS_SHA256_MISMATCH')
    plan, p32_plan, rubric = read(plan_path), read(p32_plan_path), read(rubric_path)
    if plan.get('p32_frozen_plan_sha256') != P32_PLAN_SHA256:
        raise ValueError('M3_PLAN_P32_HASH_REFERENCE_MISMATCH')
    if rubric.get('frozen_p32_results_sha256') != P32_RESULTS_SHA256:
        raise ValueError('M3_RUBRIC_P32_RESULTS_HASH_REFERENCE_MISMATCH')
    record_map, binding_facts, snapshot, summary, executor_hashes = load_executor_bundle(root, plan, p32_plan, p32_root)
    p32_cases = {row['case_id']: row for row in p32_plan.get('cases', [])}
    rubric_cases = {row['case_id']: row for row in rubric.get('cases', [])}
    gold_cache = {}
    cache_dir = root / 'scoring-cache-m3'
    case_results, pair_results = [], []
    selected_records = {}
    for case in plan['cases']:
        frozen, gold, gold_error = load_gold(case, p32_cases, gold_cache)
        variants = {}
        for variant in ('A', 'B'):
            attempts = record_map.get((case['case_id'], variant), [])
            successful = [row for row in attempts if row.get('attempt_status') == 'COMPLETED']
            ambiguous_successes = len(successful) > 1
            record = successful[0] if len(successful) == 1 else None
            selected_records[(case['case_id'], variant)] = record
            if record is None:
                result = score_variant(case, frozen or {}, gold or {}, None, metric_root, cache_dir)
                result['variant'] = variant
                result['attempts'] = attempt_summary(attempts)
                if ambiguous_successes:
                    result['status'] = 'BLOCKED_MULTIPLE_COMPLETED_ATTEMPTS'
                elif any(row.get('attempt_status') == 'PRE_SEND_GATE_REJECTED' for row in attempts):
                    result['status'] = 'NOT_RUN_PRE_SEND_GATE_REJECTED'
                elif attempts:
                    result['status'] = 'NOT_RUN_FAILED_OR_INCOMPLETE_ATTEMPT'
                if gold_error:
                    result['gold_status'] = gold_error
                variants[variant] = result
            elif gold_error and gold_error != 'NOT_APPLICABLE_NO_FROZEN_DATASET_GOLD':
                result = score_variant(case, frozen or {}, {}, None, metric_root, cache_dir)
                result.update(variant=variant, status=record.get('status', 'RECORDED'), gold_status=gold_error)
                variants[variant] = result
            else:
                variants[variant] = score_variant(case, frozen, gold, record, metric_root, cache_dir)
                variants[variant]['attempts'] = attempt_summary(attempts)
                variants[variant]['gold_status'] = gold_error or 'FROZEN_P32_P9_GOLD_READ_AFTER_EXECUTION'
        case_results.extend(variants[v] for v in ('A', 'B'))
        semantic = rubric_cases.get(case['case_id'], {})
        pair_results.append({
            'case_id': case['case_id'], 'dataset': case['source_dataset'],
            'a_status': variants['A']['status'], 'b_status': variants['B']['status'],
            'source_identity_comparison': source_identity_comparison(
                selected_records.get((case['case_id'], 'A')) or {}, selected_records.get((case['case_id'], 'B')) or {}),
            'route_a': variants['A'].get('route'), 'route_b': variants['B'].get('route'),
            'models_a': variants['A'].get('models'), 'models_b': variants['B'].get('models'),
            'usage_a': variants['A'].get('usage'), 'usage_b': variants['B'].get('usage'),
            'latency_a': variants['A'].get('latency'), 'latency_b': variants['B'].get('latency'),
            'stage_metric_pairs': {
                stage: {'a': (variants['A'].get('stage_rankings', {}).get(stage, {}).get('metrics')),
                        'b': (variants['B'].get('stage_rankings', {}).get(stage, {}).get('metrics'))}
                for stage in ('search', 'rerank', 'context')
            },
            'semantic_review': {
                'status': 'PENDING_MANUAL_REVIEW' if semantic.get('semantic_checks') or semantic.get('expected_facts') or semantic.get('expected_fact') else 'NOT_APPLICABLE',
                'manual_fact_review_todo': {
                    'expected_fact(s)': semantic.get('expected_facts') or ([semantic['expected_fact']] if semantic.get('expected_fact') else []),
                    'checks': semantic.get('semantic_checks', []),
                },
                'rubric_case': semantic,
            },
        })
    gold_paths = {Path(row['gold_path']) for row in p32_plan.get('cases', []) if row.get('gold_path')}
    source_hashes = {str(path): file_sha(path) for path in
                     (plan_path, p32_plan_path, rubric_path, FROZEN_P32_RESULTS, *gold_paths, root / 'snapshot.json',
                      root / 'summary.json', p32_root / 'asset-audit.json', p32_root / 'source-bindings.json')
                     if path.exists()}
    return {
        'schema': 'rag-m3-offline-scored-results/v1',
        'scored_at': datetime.now(timezone.utc).isoformat(),
        'selected_case_count': len(plan['cases']),
        'scheduled_variant_count': len(plan.get('schedule', [])),
        'scored_variant_rows': case_results,
        'paired_comparisons': pair_results,
        'execution_counts': dict(Counter(row['status'] for row in case_results)),
        'method': 'P32_FROZEN_GOLD_AND_P9_PURE_SCORERS_ON_SAVED_M3_OUTPUTS',
        'automatic_metrics': {
            'scifact_miracl': 'Frozen source qrels retrieval metrics at Search/Rerank/Context only.',
            'longbench': 'Task-specific official metric; task scores remain separate.',
            'hotpotqa': 'Answer EM and token F1; supporting-fact joint score is not inferred.',
            'mmlongbench_doc': 'Typed Gold answer diagnostics; semantic facts are a separate manual review.',
            'omnidocbench': 'OCR page-text diagnostic only; QA accuracy is not applicable.',
        },
        'gold_policy': 'P32/P9 frozen Gold is loaded offline after executor results; never part of M3 plan or provider request.',
        'semantic_quality': 'RUBRIC_RECORDED_FOR_SEPARATE_MANUAL_REVIEW_NOT_AUTOMATED',
        'input_sha256': source_hashes,
        'executor_input_sha256': executor_hashes,
        'source_binding_facts': binding_facts,
        'provider_call_count_from_snapshot': len(snapshot.get('provider_calls') or snapshot.get('p32_provider_calls') or []),
        'executor_summary': summary,
        'cloud_calls_by_scorer': 0, 'database_connections_by_scorer': 0,
        'gold_or_historical_result_edits': 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--p32-plan', type=Path, default=DEFAULT_P32_PLAN)
    parser.add_argument('--p32-root', type=Path, default=DEFAULT_P32_ROOT)
    parser.add_argument('--rubric', type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument('--metric-root', type=Path, default=DEFAULT_METRIC_ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.root / 'scored.json'
    if output.exists():
        raise ValueError('OUTPUT_EXISTS_PRESERVE_PRIOR_SCORES')
    report = evaluate(args.root, args.p32_plan, args.p32_root, args.rubric, args.metric_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(output), 'selected_cases': report['selected_case_count'],
                      'variant_rows': len(report['scored_variant_rows']),
                      'execution_counts': report['execution_counts']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
