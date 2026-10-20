"""Offline S8.1 scoring; never imports runtime or sends any request.

Receipt index: {"receipts": [{"result": ".../result.json", "stages":
"...stages.json", "requests": ["...request.json"]}]}. Paths are explicit:
an unrelated historical receipt is never selected by a filename substring.
Gold and fact criteria are scoring inputs only.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path
import sys

REPO = Path('E:/RAG-Worktrees/s8-six-dataset-evaluation')
sys.path.insert(0, str(REPO))
from eval_center.metrics import ranking_metrics
from eval_center.weknora_scoring import answer_diagnostics
from scripts.weknora_port_p19_score import answer_metric

BASE = Path(__file__).resolve().parent
GOLD = BASE.parent / 'cases/ground-truth.json'
QUESTIONS = BASE.parent / 'cases/questions-only.json'
METRICS = Path('E:/WK/run/p9-20261016/baseline')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, data):
    path = Path(path).resolve()
    if not path.is_relative_to(BASE):
        raise ValueError('SCORING_OUTPUT_MUST_STAY_IN_OWNED_DIRECTORY')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def validate_frozen(gold, questions):
    by_id = {c['identity']['case_id']: c for c in questions['cases']}
    if len(by_id) != 9 or len(gold['cases']) != 9:
        raise ValueError('NINE_FROZEN_CASES_REQUIRED')
    for case in gold['cases']:
        q = by_id[case['identity']['case_id']]
        if (case['question'] != q['question'] or case['identity'] != q['identity']
                or case['knowledge_base_id'] != q['scope']['knowledge_base_id']):
            raise ValueError('SCORING_QUESTION_IDENTITY_MISMATCH')


def completed_response(result):
    assistant = result.get('assistant') or {}
    events = result.get('events') or []
    terminal = [e for e in events if e.get('response_type') == 'complete']
    errors = [e for e in events if e.get('response_type') == 'error'
              and not (e.get('data') or {}).get('non_terminal')]
    good = bool(terminal and not errors and assistant.get('is_completed') is True
                and not assistant.get('turn_error_code')
                and assistant.get('turn_status') not in ('error', 'failed', 'cancelled')
                and isinstance(assistant.get('content'), str) and assistant['content'].strip())
    if any((e.get('data') or {}).get('status') in ('failed', 'error', 'cancelled') for e in terminal):
        good = False
    return good, assistant.get('content')


def typed_answer(prediction, references, metadata):
    """Project diagnostic only. Preserve full response; do not extract Gold text."""
    result = answer_diagnostics(prediction, references, 'mmlongbench-doc')
    result.update(answer_format=metadata.get('answer_format'), official_gpt_judge='NOT_RUN')
    if metadata.get('answer_format') == 'List':
        def parse(value):
            try:
                items = ast.literal_eval(value.strip())
                if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
                    return None
                return [''.join(c.lower() for c in x if c.isalnum()) for x in items]
            except (ValueError, SyntaxError):
                return None
        pred = parse(prediction)
        gold = [parse(x) for x in references]
        result['typed_list'] = {'status': 'COMPUTED_PROJECT_LIST_DIAGNOSTIC' if pred is not None
                                else 'UNPARSEABLE_FULL_RESPONSE_NOT_EXTRACTED',
            'unordered_multiset_exact': max((int(Counter(pred) == Counter(x)) for x in gold
                                             if x is not None), default=None) if pred is not None else None,
            'normalization': 'literal Python/JSON string list; alphanumeric lowercase; order ignored; duplicates preserved'}
    return result


def stages_for_case(case, stage):
    if not stage:
        return {}, {'status': 'UNVERIFIED_NO_STAGE_OR_CONTEXT_RECEIPT'}
    snapshots = stage.get('stages', [])
    final = stage.get('finalized_context')
    result = {}
    positives = {key for key, grade in case['gold']['qrels'].items() if grade > 0}
    bindings = {r['document_id']: r['source_id'] for r in case['qrel_source_mappings']}
    for name, event_name, field in [('search', 'retrieval', 'search_results'),
                                    ('rerank', 'rerank', 'rerank_results'),
                                    ('context', 'merge', 'merge_results')]:
        candidates = [e for e in snapshots if e.get('stage') == event_name and e.get('status') == 'completed']
        if name == 'context' and final is not None:
            candidates = [e for e in snapshots if 'state' in e and field in e['state']]
        if not candidates or name == 'context' and final is None:
            result[name] = {'status': 'NOT_RUN_NO_CAPTURED_STAGE', 'metrics': None}
            continue
        rows = [r for r in candidates[-1].get('state', {}).get(field, []) if r is not None]
        if name == 'context':
            by_id = {r['id']: r for r in rows}
            if any(cid not in by_id for cid in final.get('result_ids', [])):
                result[name] = {'status': 'BLOCKED_FINAL_CONTEXT_CHUNK_NOT_IN_CAPTURE', 'metrics': None}
                continue
            rows = [by_id[cid] for cid in final.get('result_ids', [])]
        if any(r.get('knowledge_base_id') != case['knowledge_base_id'] or not r.get('knowledge_id') for r in rows):
            result[name] = {'status': 'BLOCKED_SCOPE_OR_DOCUMENT_ID_MISSING', 'metrics': None}
            continue
        ranked, seen = [], set()
        for row in rows:
            doc = row['knowledge_id']
            if doc not in seen:
                ranked.append(bindings.get(doc, 'UNMAPPED_DOCUMENT:' + doc))
                seen.add(doc)
        hits = positives & set(ranked)
        result[name] = {'status': 'COMPUTED_DOCUMENT_IDENTITY_METRICS',
            'unit': 'ORIGINAL_SOURCE_DOCUMENT', 'k': 10, 'ranked_source_ids': ranked,
            'unmapped_non_gold_document_count': sum(x.startswith('UNMAPPED_DOCUMENT:') for x in ranked),
            'metrics': ranking_metrics(ranked, case['gold']['qrels'], k=10),
            'any_required_source': bool(hits), 'all_required_sources': hits == positives,
            'required_sources_present': sorted(hits), 'required_sources_missing': sorted(positives - hits),
            'does_not_prove_fact_support': True}
    context = {'status': 'CAPTURED_FACT_REVIEW_REQUIRED' if isinstance(final, dict) else 'UNVERIFIED_NO_FINAL_CONTEXT',
        'actual_context': final, 'fact_support': 'UNVERIFIED_PENDING_CONTENT_AND_SOURCE_REVIEW',
        'supporting_sentence_coverage': 'UNVERIFIED_SOURCE_SENTENCES_AND_ACTUAL_WIRE_REQUIRED',
        'page_coverage': 'UNVERIFIED_PAGE_INDEX_CONVENTION_UNKNOWN' if case['identity']['dataset'] == 'mmlongbench-doc' else 'NOT_APPLICABLE'}
    return result, context


def textual_messages(request):
    texts = []
    for message in request.get('body', {}).get('messages', []):
        content = message.get('content')
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(item['text'] for item in content if isinstance(item, dict)
                         and isinstance(item.get('text'), str))
    return '\n'.join(texts)


def supporting_sentence_review(case_id, context, requests):
    criteria_path = BASE / 'fact-criteria.json'
    if not criteria_path.exists():
        return {'status': 'UNVERIFIED_SOURCE_SENTENCES_UNAVAILABLE'}
    criteria = next(c for c in read(criteria_path)['cases'] if c['case_id'] == case_id)
    facts = criteria['supporting_sentences']
    if not facts:
        return {'status': 'NOT_APPLICABLE_NO_SUPPORTING_SENTENCE_GOLD'}
    actual = (context.get('actual_context') or {}).get('rendered_contexts')
    if not isinstance(actual, str) or not requests:
        return {'status': 'UNVERIFIED_ACTUAL_CONTEXT_OR_GENERATION_INPUT_MISSING',
                'required_sentence_count': len(facts), 'metrics': None}
    compact = lambda value: ''.join(html.unescape(value).split())
    body_texts = [compact(textual_messages(r)) for r in requests]
    bound = all(compact(actual) in text for text in body_texts)
    rows = [{**f, 'exact_sentence_in_final_context': compact(f['text']) in compact(actual),
             'exact_sentence_in_every_generation_input_capture': all(compact(f['text']) in text for text in body_texts)}
            for f in facts]
    hit = sum(r['exact_sentence_in_final_context'] and r['exact_sentence_in_every_generation_input_capture'] for r in rows)
    # Generation uses its own XML wrapper; matching each required sentence in
    # both captured context and sent wire establishes sentence-level delivery.
    sentence_bound = all(not r['exact_sentence_in_final_context'] or r['exact_sentence_in_every_generation_input_capture'] for r in rows)
    return {'status': 'COMPUTED_EXACT_SENTENCE_TEXT_DIAGNOSTIC' if bound or sentence_bound else 'UNVERIFIED_CONTEXT_TO_WIRE_BINDING',
        'context_text_in_every_generation_input_capture': bound, 'required_sentences': rows,
        'sentence_level_wire_binding': sentence_bound,
        'exact_text_coverage': hit / len(facts) if bound or sentence_bound else None,
        'interpretation': 'Exact text diagnostic only; absence needs semantic review. Input captures precede SendGate; response/ledger reconciliation separately proves send.'}


def score_case(case, arm, receipt, metric_root):
    result, stage = read(receipt['result']), read(receipt['stages']) if receipt.get('stages') else {}
    cid = case['identity']['case_id']
    if result.get('case_id') != cid or result.get('qid') != case['identity']['qid'] or result.get('arm') != arm:
        raise ValueError('RECEIPT_CASE_OR_ARM_MISMATCH')
    assistant = result.get('assistant') or {}
    expected_run = assistant.get('request_id') or result.get('run_id')
    if stage and (stage.get('session_id') != result.get('session_id')
                  or not expected_run or stage.get('run_id') != expected_run):
        raise ValueError('STAGE_SESSION_OR_RUN_MISMATCH')
    question_path = Path(receipt['result']).parent / 'question.json'
    if not question_path.is_file():
        raise ValueError('EXECUTED_QUESTION_RECEIPT_REQUIRED')
    question = read(question_path)
    if (question.get('content') != case['question']
            or question.get('knowledge_base_scope') != [case['knowledge_base_id']]
            or question.get('document_scope') != []):
        raise ValueError('EXECUTED_QUESTION_OR_SCOPE_MISMATCH')
    completed, prediction = completed_response(result)
    gold = {**case['gold'], 'qid': case['identity']['qid']}
    dataset = case['identity']['dataset']
    if completed and dataset == 'mmlongbench-doc':
        answer = typed_answer(prediction, gold['answers'], gold['evaluation_metadata'])
    else:
        answer = answer_metric(dataset, gold, prediction, completed, metric_root, BASE / 'metric-cache')
    ranks, context = stages_for_case(case, stage)
    requests = [read(p) for p in receipt.get('requests', [])]
    final_requests = [r for r in requests if stage.get('run_id') and str(r.get('request_id', '')).startswith(stage['run_id'])
                      and r.get('capability') == 'chat' and '/answer/' in r.get('request_id', '')
                      and r.get('request_id', '').rsplit('/', 1)[-1] in ('cheap', 'expensive')]
    context['supporting_sentence_review'] = supporting_sentence_review(cid, context, final_requests)
    return {'case_id': cid, 'dataset': dataset, 'qid': case['identity']['qid'], 'arm': arm,
        'question': case['question'], 'status': result.get('status'), 'completed_app_response': completed,
        'full_answer': prediction, 'answer': answer, 'stage_rankings': ranks, 'context_review': context,
        'final_generation_requests': final_requests,
        'real_provider_execution': 'UNVERIFIED_REQUIRES_RESPONSE_AND_LEDGER_RECONCILIATION',
        'artifacts': {str(p): sha(p) for p in [receipt['result'], *([receipt['stages']] if receipt.get('stages') else []), *receipt.get('requests', [])]}}


def evaluate(index, metric_root):
    frozen, questions = read(GOLD), read(QUESTIONS)
    validate_frozen(frozen, questions)
    receipts = {}
    for r in index.get('receipts', []):
        record = read(r['result'])
        key = record.get('case_id'), record.get('arm')
        if key in receipts:
            raise ValueError('DUPLICATE_RECEIPT_FOR_CASE_ARM')
        receipts[key] = r
    rows = []
    for case in frozen['cases']:
        for arm in case['identity']['arms']:
            key = case['identity']['case_id'], arm
            rows.append(score_case(case, arm, receipts[key], metric_root) if key in receipts else
                {**case['identity'], 'arm': arm, 'status': 'NOT_RUN', 'answer': {'metrics': None},
                 'context_review': {'fact_support': 'UNVERIFIED_NO_NEW_RECEIPT'}})
    unknown = set(receipts) - {(r['case_id'], r['arm']) for r in rows}
    if unknown:
        raise ValueError('OUT_OF_SCOPE_RECEIPT')
    aggregates = defaultdict(lambda: defaultdict(list))
    for row in rows:
        task = row['qid'].split(':')[0] if row['dataset'] == 'longbench-zh' else row['dataset']
        for name, value in (row.get('answer', {}).get('metrics') or {}).items():
            aggregates[row['arm'] + '/' + task][name].append(value)
    return {'schema': 's8-1-offline-scoring/v1', 'scope': 'FROZEN_PREPARED_SUBCORPUS_SMALL_SAMPLE_NOT_OFFICIAL_WHOLE_BENCHMARK',
        'scoring_only_gold_sha256': sha(GOLD), 'questions_only_sha256': sha(QUESTIONS),
        'cases': rows, 'answer_aggregates': {group: {name: {'mean': sum(v)/len(v), 'evaluated_denominator': len(v)}
            for name, v in metrics.items()} for group, metrics in aggregates.items()},
        'omnidocbench': {'policy': 'REUSE_SAVED_PARSE_RECEIPTS_ONLY', 'mapped_project_text_ned_pages': 3,
            'not_scorable_pages': 21, 'new_ocr': 0, 'rag_question_denominator': 0,
            'new_verification': 'NOT_RUN', 'official_layout_table_formula_metrics': 'NOT_RUN'},
        'new_provider_calls_by_scorer': 0, 'new_database_writes_by_scorer': 0,
        'fact_support_policy': 'Document IDs, page numbers and SHA establish identity only; absent original or actual model content remains UNVERIFIED.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt-index', type=Path)
    parser.add_argument('--metric-root', type=Path, default=METRICS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(read(args.receipt_index) if args.receipt_index else {}, args.metric_root)
    write_new(args.output, report)
    print(json.dumps({'output': str(args.output), 'case_arms': len(report['cases']),
                      'executed_receipts': sum(r['status'] != 'NOT_RUN' for r in report['cases']), 'provider_calls': 0}))


if __name__ == '__main__':
    main()
