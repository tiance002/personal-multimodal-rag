"""Offline scoring of frozen P8 real-run evidence. Never imports runtime or sends.

Gold is read only by this post-run evaluator. Wiring evidence, retrieval quality,
answer diagnostics and unavailable official judging are separate result fields.
"""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval_center.metrics import ranking_metrics
from eval_center.omni_contract import _ned
from eval_center.public_data import PublicCase
from eval_center.weknora_scoring import project_native_documents, score_native_qa, score_native_omni

DEFAULT_RUN = Path('E:/WK/run/p8-20261016')
LONG_REVISION = 'c5ea10bcd06285223c58dfed76bbc92d22273709'
LONG_METRICS_SHA = 'e22e2a2662e0f7e683137fa3541f64edb6a801e9138d16d2f3459a6ab9941323'
MODELS = {'embedding': 'BAAI/bge-m3', 'rerank': 'BAAI/bge-reranker-v2-m3', 'chat': 'XingChenAGI/Xing4.0-29B'}


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def plain_answer(value):
    # Preserve all prose/Markdown; remove only the system's self-closing tag.
    return re.sub(r'<kb\b[^>]*?/\s*>', '', value).strip()


def official_longbench(run):
    receipt = load(run / 'official-longbench/receipt.json')
    if not receipt or receipt.get('revision') != LONG_REVISION:
        raise ValueError('OFFICIAL_LONGBENCH_REVISION_RECEIPT_REQUIRED')
    path = run / 'official-longbench/metrics.py'
    source = path.read_bytes()
    actual = hashlib.sha256(source).hexdigest()
    bound = next((row for row in receipt['files'] if Path(row['path']).name == 'metrics.py'), None)
    if not bound or actual != bound['sha256'] or actual != LONG_METRICS_SHA:
        raise ValueError('OFFICIAL_LONGBENCH_METRICS_SHA_MISMATCH')
    deps = run / 'eval-deps'
    if deps.exists() and str(deps) not in sys.path: sys.path.insert(0, str(deps))
    import jieba
    cache = run / 'eval-cache'; cache.mkdir(exist_ok=True)
    jieba.dt.tmp_dir = str(cache)
    jieba.dt.cache_file = 'p8-jieba.cache'
    spec = importlib.util.spec_from_file_location('p8_bound_official_longbench_metrics', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, {'revision': LONG_REVISION, 'sha256': actual, 'source': bound['source']}


def long_answer(run, qid, prediction, answers):
    if not answers: return {'status': 'NOT_APPLICABLE_NO_ANSWER_GOLD', 'metrics': None}
    if prediction is None: return {'status': 'NOT_RUN_NO_NATIVE_RESULT', 'metrics': None}
    task = qid.split(':', 1)[0]
    function = {'multifieldqa_zh': 'qa_f1_zh_score', 'dureader': 'rouge_zh_score'}.get(task)
    if function is None: return {'status': 'NOT_RUN_UNSUPPORTED_TASK', 'metrics': None}
    try:
        module, binding = official_longbench(run)
        score = max(float(getattr(module, function)(prediction, reference)) for reference in answers)
        return {'status': 'COMPUTED_OFFICIAL_TASK_METRIC_ON_SMALL_SELECTED_SAMPLE', 'task': task,
                'function': function, 'max_over_references': True, 'metrics': {function: score}, 'binding': binding,
                'complete_official_leaderboard': 'NOT_RUN'}
    except (ImportError, OSError, ValueError) as exc:
        return {'status': 'NOT_RUN_OFFICIAL_SCORER_UNAVAILABLE', 'metrics': None, 'reason': str(exc)}


def validate_reference(ref, chunk, *, kb, bindings, source_records, document_rows):
    """Validate actual identity + version + quote, not document-hit semantics."""
    document = ref.get('knowledge_id') or ref.get('document_id')
    source = bindings.get(document)
    doc = document_rows.get(document, {})
    source_row = source_records.get(document, {})
    version = ref.get('chunk_metadata', {}).get('source_version') or ref.get('metadata', {}).get('source_version')
    checks = {
        'reference_kb_in_scope': ref.get('knowledge_base_id') == kb,
        'reference_document_in_scope': document in bindings,
        'readback_kb_in_scope': chunk.get('knowledge_base_id') == kb,
        'readback_document_matches': chunk.get('document_id') == document,
        'chunk_id_matches': bool(ref.get('id')) and chunk.get('chunk_id') == ref.get('id'),
        'quote_matches_chunk': bool(ref.get('content')) and ref.get('content') == chunk.get('text'),
        'source_version_matches': bool(version) and chunk.get('source_version') == version
            and doc.get('metadata', {}).get('source_version') == version,
        'document_completed': doc.get('parse_status') == 'completed' and doc.get('deleted_at') is None,
        'document_snapshot_kb_matches': doc.get('knowledge_base_id') == kb,
        'source_id_matches': bool(source) and source_row.get('source_id') == source and source_row.get('document_id') == document,
        'original_sha_readback_matches': bool(source_row.get('sha256')) and source_row.get('sha256') == source_row.get('expected_sha256')
            and source_row.get('sha256') == source_row.get('selection_sha256') and source_row.get('matches') is True,
    }
    return {'chunk_id': ref.get('id'), 'document_id': document, 'source_id': source,
            'checks': checks, 'valid': all(checks.values()), 'semantic_support': 'NOT_EVALUATED'}


def stage_rankings(capture, *, kb, bindings, qrels):
    result = {}
    for stage in ('search', 'rerank', 'context'):
        rows = (capture or {}).get('rankings', {}).get(stage)
        if rows is None:
            result[stage] = {'status': 'NOT_RUN_NO_CAPTURED_STAGE', 'metrics': None}; continue
        try:
            if any(len(row) != 3 or row[1] != kb or row[2] not in bindings for row in rows):
                raise ValueError('RANKING_SCOPE_OR_SOURCE_IDENTITY_MISMATCH')
            projected = project_native_documents(rows, bindings)
            result[stage] = {'status': 'COMPUTED' if qrels else 'NOT_APPLICABLE_NO_RETRIEVAL_GOLD',
                'ranked_source_ids': projected, 'original_chunk_count': len(rows), 'distinct_document_count': len(projected),
                'k': 10, 'unit': 'ORIGINAL_SOURCE_DOCUMENT',
                'metrics': ranking_metrics(projected, qrels, k=10) if qrels else None}
        except ValueError as exc:
            result[stage] = {'status': 'BLOCKED', 'reason': str(exc), 'metrics': None}
    return result


def receipt_facts(ledger, request_ids):
    matched = [row for row in ledger if any(str(row.get('request_id', '')).startswith(str(value) + '/') for value in request_ids if value)]
    def received(row):
        return (row.get('provider') == 'siliconflow' and row.get('account_id') == 'siliconflow-owner-main'
                and row.get('model') == MODELS.get(row.get('capability')) and row.get('outcome') == 'RESPONSE_RECEIVED')
    checks = {cap: any(received(row) and row.get('capability') == cap and
                     (cap != 'chat' or '/answer/' in row.get('request_id', '')) for row in matched)
              for cap in ('embedding', 'rerank', 'chat')}
    def token_bucket(state, field):
        rows = [row for row in matched if row.get('state') == state]
        known = [row[field] for row in rows if type(row.get(field)) is int and row[field] >= 0]
        return {'row_count': len(rows), 'known_value_count': len(known),
                'tokens': sum(known) if rows and len(known) == len(rows) else None,
                'status': 'RECORDED' if rows and len(known) == len(rows) else 'NOT_RUN_NO_ROWS' if not rows else 'UNKNOWN_MISSING_VALUES'}
    counts = Counter(row.get('capability', 'UNKNOWN') for row in matched)
    return {'checks': checks, 'all_received': all(checks.values()),
        'rows': [{name: row.get(name) for name in ('request_id', 'capability', 'provider', 'model', 'state', 'outcome', 'actual_total', 'held_tokens')} for row in matched],
        'usage_unknown_count': sum(row.get('state') == 'unknown' for row in matched),
        'token_totals': {'settled_actual_total': token_bucket('settled', 'actual_total'),
                         'unknown_held_tokens': token_bucket('unknown', 'held_tokens'),
                         'reserved_held_tokens': token_bucket('reserved', 'held_tokens')},
        'physical_counts': {'ledger_call_rows': len(matched), 'by_capability': dict(counts),
            'response_received': sum(row.get('outcome') == 'RESPONSE_RECEIVED' for row in matched),
            'uncertain_sent_outcome': sum(row.get('outcome') == 'UNKNOWN' for row in matched),
            'physical_attempts_at_least': sum(row.get('outcome') in ('RESPONSE_RECEIVED', 'UNKNOWN') for row in matched),
            'reserved_rows_without_confirmed_response': sum(row.get('state') == 'reserved' and row.get('outcome') != 'RESPONSE_RECEIVED' for row in matched),
            'meaning': 'Per-question server-run ledger rows; reserved rows are not proof of a physical send.'},
        'usage_unknown_is_free': False}


def http_latency(run, name, qid):
    attempt_key = 'query-' + identity(name, qid)
    attempt = load(run / 'attempts.json', {}).get(attempt_key, {})
    result = {'attempt_key': attempt_key, 'started_at': attempt.get('at'), 'finished_at': attempt.get('finished_at'),
              'seconds': None, 'status': 'NOT_RUN_EXPLICIT_HTTP_TIMESTAMPS_REQUIRED',
              'scope': 'RECORDED_FULL_HTTP_REQUEST_DURATION_NOT_MODEL_ONLY'}
    try:
        start = datetime.fromisoformat(attempt['at'])
        end = datetime.fromisoformat(attempt['finished_at'])
        if start.tzinfo is not None and end.tzinfo is not None and end >= start:
            result.update(seconds=(end-start).total_seconds(), status='RECORDED')
    except (KeyError, TypeError, ValueError):
        pass
    return result


def evaluate_case(run, dataset, selected_case, gold_case, *, state, ledger, documents, captures):
    name, qid = dataset['name'], selected_case['qid']
    entry = state.get('datasets', {}).get(name, {})
    kb = entry.get('kb'); mapping = entry.get('documents', {})
    bindings = {row['id']: source for source, row in mapping.items()}
    selected_sources = {row['source_id']: row for row in dataset['files']}
    bindings = {doc: source for doc, source in bindings.items() if source in selected_sources}
    records = {}
    for doc, source in bindings.items():
        row = load(run / ('sources/' + identity(name, source) + '.json'), {})
        records[doc] = {**row, 'selection_sha256': selected_sources[source]['sha256']}
    record = entry.get('queries', {}).get(qid, {})
    prefix = run / ('queries/' + identity(name, qid))
    messages = load(Path(str(prefix) + '-messages.json'), {}).get('data', [])
    # Client request_id and the persisted assistant's server run_id differ.
    # Bind only this question's unique session/message/preparation identities.
    capture_rows = [row for row in captures if row.get('session_id') == record.get('session')
                    and row.get('question') == selected_case['question']]
    pairs = [(message, captured) for message in messages if message.get('role') == 'assistant'
             for captured in capture_rows if message.get('id') == captured.get('message_id')
             and message.get('request_id') == captured.get('run_id')]
    assistant, capture = pairs[0] if len(pairs) == 1 else (None, None)
    raw = assistant.get('content', '') if assistant else None
    prediction = plain_answer(raw) if raw is not None else None
    chunks = {row['data']['chunk_id']: row['data'] for row in load(Path(str(prefix) + '-chunkreadback.json'), [])}
    refs = (assistant or {}).get('knowledge_references') or []
    citations = [validate_reference(ref, chunks.get(ref.get('id'), {}), kb=kb, bindings=bindings,
                 source_records=records, document_rows=documents) for ref in refs]
    receipts = receipt_facts(ledger, [(capture or {}).get('run_id')])
    fixed = capture is not None and capture.get('fixed_fallback') is not None
    completed = bool(assistant and assistant.get('is_completed') and prediction and not assistant.get('turn_error_code')
                     and assistant.get('turn_status') not in ('error', 'failed', 'cancelled'))
    reasons = []
    source_checks = []
    for source, selected_source in selected_sources.items():
        uploaded = mapping.get(source, {})
        doc_id = uploaded.get('id'); doc = documents.get(doc_id, {})
        source_row = records.get(doc_id, {})
        expected = selected_source['sha256']
        checks = {
            'uploaded': bool(doc_id),
            'completed': uploaded.get('status') == 'completed' and doc.get('parse_status') == 'completed',
            'current_scope': doc.get('knowledge_base_id') == kb and doc.get('deleted_at') is None,
            'source_identity': source_row.get('source_id') == source and source_row.get('document_id') == doc_id,
            'sha_readback': source_row.get('matches') is True and source_row.get('sha256') == expected
                and source_row.get('expected_sha256') == expected and doc.get('metadata', {}).get('sha256') == expected,
        }
        source_checks.append({'source_id': source, 'document_id': doc_id, 'checks': checks, 'valid': all(checks.values())})
    if not source_checks or not all(row['valid'] for row in source_checks):
        reasons.append('SELECTED_SOURCES_MISSING_INCOMPLETE_OR_SHA_READBACK_FAILED')
    if record and record.get('question') != selected_case['question']:
        reasons.append('RECORDED_QUESTION_IDENTITY_MISMATCH')
    if len(pairs) != 1 and record:
        reasons.append('UNIQUE_SESSION_MESSAGE_QUESTION_CAPTURE_BINDING_REQUIRED')
    if not assistant: reasons.append('NO_NATIVE_ASSISTANT_RESULT')
    elif not completed: reasons.append('ASSISTANT_INCOMPLETE_EMPTY_OR_ERROR')
    if not capture: reasons.append('NO_PASSIVE_PREPARATION_CAPTURE')
    if not citations: reasons.append('NO_CURRENT_SCOPE_CITATION')
    elif not all(row['valid'] for row in citations): reasons.append('CITATION_IDENTITY_VERSION_QUOTE_OR_SOURCE_HASH_FAILED')
    if not receipts['all_received']: reasons.append('MISSING_REAL_QUERY_EMBEDDING_RERANK_OR_ANSWER_RECEIPT')
    if fixed and not receipts['checks']['chat']: reasons.append('FIXED_FALLBACK_WITHOUT_ANSWER_MODEL')
    wiring = 'PASS' if not reasons else ('NOT_RUN' if not record else 'BLOCKED' if not assistant else 'PARTIAL' if fixed and completed else 'FAIL')
    case = PublicCase(**{**gold_case, 'answers': tuple(gold_case.get('answers', []))}) if gold_case else None
    qrels = case.qrels if case else {}
    ranks = stage_rankings(capture, kb=kb, bindings=bindings, qrels=qrels)
    if case and prediction is not None and receipts['checks']['chat']:
        quality = score_native_qa(case, dataset=name, prediction=prediction,
            ranked_source_ids=ranks['context'].get('ranked_source_ids', []), citations=[r['valid'] for r in citations],
            execution_kind='REAL_PROVIDER', k=10)
        # Each stage has its own rank metric; avoid implying context == search.
        quality.pop('retrieval', None)
        if not case.answers: quality['answer'] = {'status': 'NOT_APPLICABLE_NO_ANSWER_GOLD', 'metrics': None}
        elif name == 'longbench-zh': quality['answer'] = long_answer(run, qid, prediction, case.answers)
        if name == 'mmlongbench-doc':
            quality['answer']['status'] = 'COMPUTED_PROJECT_TYPED_ANSWER_DIAGNOSTIC'
            quality['answer']['official_judge'] = 'NOT_RUN'
            quality['source_annotations'] = {'status': 'NOT_APPLICABLE_NO_EVIDENCE_PAGE_GOLD' if not case.evaluation_metadata.get('evidence_pages')
                else 'NOT_RUN_PAGE_INDEX_CONVENTION_UNKNOWN', 'page_index_convention': 'UNKNOWN', 'metrics': None}
    else:
        quality = {'status': 'NOT_APPLICABLE_NO_INDEPENDENT_QA_GOLD' if not case else 'NOT_RUN_NO_CONFIRMED_NATIVE_ANSWER', 'quality_metrics': None,
            'answer': {'status': 'NOT_APPLICABLE_NO_ANSWER_GOLD' if not case or not case.answers else 'NOT_RUN_NO_CONFIRMED_NATIVE_ANSWER', 'metrics': None}}
    return {'dataset': name, 'qid': qid, 'question': selected_case['question'], 'wiring_status': wiring, 'wiring_reasons': reasons,
        'question_identity': record, 'completed_assistant': completed, 'raw_answer': raw, 'plain_answer': prediction,
        'selected_source_checks': source_checks,
        'fixed_fallback': fixed, 'citations': citations, 'real_provider_receipts': receipts, 'stage_rankings': ranks,
        'http_latency': http_latency(run, name, qid),
        'gold_quality': quality, 'qrels_kind': (gold_case or {}).get('qrels_kind', 'NO_RETRIEVAL_GOLD'),
        'candidate_pool': {'scope': 'SMALL_SELECTED_SOURCE_SUBSET_NOT_FULL_CORPUS', 'selected_document_count': len(dataset['files']),
                           'mapped_document_count': len(bindings)}, 'official_full_benchmark': 'NOT_RUN'}


def page_text_diagnostic(page, gold, outcomes, *, source_bound):
    base = {'official_omnidocbench_score': 'NOT_RUN', 'qa_score': 'NOT_APPLICABLE',
            'comparison': 'LITERAL_UNMODIFIED_STRINGS', 'layout_block_table_formula_metrics': 'NOT_RUN'}
    if not source_bound:
        return {**base, 'status': 'NOT_RUN_PAGE_SOURCE_HASH_VERSION_BINDING_REQUIRED', 'metrics': None}
    ocr = [row['ocr_result'] for row in outcomes.values() if isinstance(row.get('ocr_result'), str)
           and row['ocr_result'] and row.get('ocr_text', {}).get('status') == 'completed']
    if len(ocr) != 1:
        return {**base, 'status': 'NOT_RUN_UNIQUE_COMPLETED_PAGE_OCR_RESULT_REQUIRED', 'metrics': None}
    eligible = [row for row in (gold or {}).get('layout_dets', []) if not row.get('ignore', False)
                and isinstance(row.get('text'), str)]
    orders = [row.get('order') for row in eligible]
    if not eligible or any(type(order) is not int for order in orders) or len(set(orders)) != len(orders):
        return {**base, 'status': 'NOT_RUN_UNIQUE_NATIVE_GOLD_TEXT_ORDER_REQUIRED', 'metrics': None}
    reference = '\n'.join(row['text'] for row in sorted(eligible, key=lambda row: row['order']))
    return {**base, 'status': 'PROJECT_PAGE_TEXT_NED', 'page_id': page['page_id'],
            'metrics': {'page_text_normalized_edit_distance': _ned(reference, ocr[0])},
            'gold_text': reference, 'native_ocr_text': ocr[0], 'gold_text_blocks': len(eligible),
            'gold_order_basis': 'EXISTING_UNIQUE_INTEGER_ORDER', 'native_ocr_modifications': 'NONE'}


def omni_parsing(gold_dataset, tasks, *, run, state, selected, documents):
    results = []
    entry = state.get('datasets', {}).get('omnidocbench', {})
    selected_sources = {row['source_id']: row for row in selected.get('files', [])}
    for page in gold_dataset.get('pages', []):
        gold = gold_dataset.get('parsing_gold', {}).get(page['page_id'])
        source_id = page['document_id']
        source = selected_sources.get(source_id, {})
        uploaded = entry.get('documents', {}).get(source_id, {})
        doc_id = uploaded.get('id'); doc = documents.get(doc_id, {})
        readback = load(run / ('sources/' + identity('omnidocbench', source_id) + '.json'), {})
        matching_tasks = [task for task in tasks if task.get('knowledge_id') == doc_id
            and task.get('knowledge_base_id') == entry.get('kb') and task.get('tenant_id') == state.get('tenant')
            and task.get('subject_id') == state.get('subject') and task.get('source_version') == doc.get('metadata', {}).get('source_version')]
        source_bound = bool(source and doc_id and len(matching_tasks) == 1 and
            source.get('media_type', '').startswith('image/') and doc.get('parse_status') == 'completed' and
            doc.get('knowledge_base_id') == entry.get('kb') and doc.get('deleted_at') is None and
            source.get('sha256') == page.get('image_sha256') == doc.get('metadata', {}).get('sha256') == readback.get('sha256') == readback.get('expected_sha256')
            and readback.get('matches') is True and readback.get('source_id') == source_id and readback.get('document_id') == doc_id)
        outcomes = matching_tasks[0].get('outcomes', {}) if len(matching_tasks) == 1 else {}
        layouts = [item.get('ocr_layout') for item in outcomes.values() if item.get('ocr_layout')]
        # text_block is a native OCR grouping, not a semantic text/title/table
        # classifier. Do not relabel predictions with evaluation categories.
        result = score_native_omni(page, gold, prediction=None)
        result['reason'] = 'OCR_TEXT_BLOCK_TO_OMNI_SEMANTIC_CATEGORY_MAPPING_REQUIRED' if layouts else 'NO_NATIVE_OCR_LAYOUT_RESULT'
        result['native_layout_count'] = len(layouts)
        result['native_layout_bases'] = [{k: layout.get(k) for k in ('schema', 'coordinate_basis', 'category_basis', 'status')} for layout in layouts]
        result['page_text_diagnostic'] = page_text_diagnostic(page, gold, outcomes, source_bound=source_bound)
        result['page_text_source_binding'] = {'valid': source_bound, 'source_id': source_id, 'document_id': doc_id,
            'matching_task_ids': [task.get('id') for task in matching_tasks], 'source_version': doc.get('metadata', {}).get('source_version')}
        results.append(result)
    return results


def evaluate(run):
    selected = load(run / 'selection.json')
    if not selected: raise ValueError('FROZEN_SELECTION_REQUIRED')
    gold = load(run / 'gold-selected.json', {'datasets': []})
    state = load(run / 'state.json', {})
    ledger = load(run / 'ledger-after.json', [])
    documents = {row['id']: row for row in load(run / 'documents-after.json', [])}
    captures = [load(path) for path in sorted((run / 'captures').glob('*.json'))]
    gold_sets = {row['name']: row for row in gold['datasets']}
    results = []
    for dataset in selected['datasets']:
        gold_cases = {row['qid']: row for row in gold_sets.get(dataset['name'], {}).get('cases', [])}
        for case in dataset['cases']:
            results.append(evaluate_case(run, dataset, case, gold_cases.get(case['qid']), state=state,
                ledger=ledger, documents=documents, captures=captures))
    inputs = ['selection.json', 'gold-selected.json', 'state.json', 'attempts.json', 'ledger-after.json', 'documents-after.json', 'chunks-after.json', 'image-tasks-after.json']
    input_facts = {name: {'status': 'PRESENT', 'sha256': hashlib.sha256((run / name).read_bytes()).hexdigest()} if (run / name).exists()
                   else {'status': 'NOT_RUN_MISSING_ARTIFACT'} for name in inputs}
    tasks = load(run / 'image-tasks-after.json', [])
    return {'schema': 'p8-offline-real-sample-results/v1', 'execution': 'OFFLINE_RECORDED_EVIDENCE_ONLY',
        'denominator': len(results), 'selection_denominator': sum(len(row['cases']) for row in selected['datasets']),
        'wiring_counts': dict(Counter(row['wiring_status'] for row in results)), 'inputs': input_facts, 'cases': results,
        'omni_parsing': omni_parsing(gold_sets.get('omnidocbench', {}), tasks, run=run, state=state,
            selected=next((row for row in selected['datasets'] if row['name'] == 'omnidocbench'), {}), documents=documents),
        'image_tasks': tasks, 'complete_six_dataset_acceptance': 'NOT_CLAIMED', 'official_full_benchmark': 'NOT_RUN'}


def summary(result):
    lines = [f"P8 offline sample: {result['denominator']} questions; wiring {result['wiring_counts']}.",
             'Small selected document pools; rank metrics at k=10 are per stage and original source document.',
             'Wiring success and Gold quality are separate. No complete benchmark or release acceptance.', '',
             '| Dataset | QID | Wiring | Citation readback | Answer scoring |', '|---|---|---|---|---|']
    for row in result['cases']:
        lines.append(f"| {row['dataset']} | {row['qid']} | {row['wiring_status']} | {sum(c['valid'] for c in row['citations'])}/{len(row['citations'])} | {row['gold_quality']['answer']['status']} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, default=DEFAULT_RUN)
    args = parser.parse_args()
    result = evaluate(args.run_dir)
    (args.run_dir / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.run_dir / 'summary.md').write_text(summary(result), encoding='utf-8')
    print(json.dumps({'denominator': result['denominator'], 'wiring_counts': result['wiring_counts']}, ensure_ascii=False))


if __name__ == '__main__': main()
