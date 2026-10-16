"""Offline P11 A/B diagnostics. Reads frozen artifacts; never calls services.

Answer Gold is evaluation-only. Missing evidence stays NOT_RUN, and valid
citations never imply that OCR or parser-derived text reached the provider.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import p8_score_real as p8, p9_score_real as p9

GOLD = Path('E:/WK/run/p11-20261016/gold-evaluation-only.json')


def obj(value):
    return json.loads(value) if isinstance(value, str) and value else value or {}


def rows(folder):
    return [p8.load(path) for path in sorted(folder.glob('*.json'))]


def critical_terms(qid):
    if qid.endswith('row-0434'):
        return [('kaggle',), ('cat vs dog',), ('dataset',)]
    if qid.endswith('row-0959'):
        return [('radiowaves', 'radio waves'), ('microwaves',)]
    return []


def text_hits(text, qid):
    normalized = p9.canonical_text(text).casefold()
    terms = critical_terms(qid)
    return {'status': 'RECORDED' if terms else 'NOT_APPLICABLE_NO_FROZEN_KEY_TEXT',
            'term_hits': [{'alternatives': list(group), 'hit': any(term in normalized for term in group)} for group in terms],
            'all_terms_hit': all(any(term in normalized for term in group) for group in terms) if terms else None}


def answer_entities(text, qid):
    """Literal entity presence only; separate from full-answer EM and support."""
    value = p9.canonical_text(text).casefold()
    if qid.endswith('row-0434'):
        facts = {'cat_and_dog_mentioned': ('cat' in value and 'dog' in value) or ('猫' in value and '狗' in value),
                 'kaggle_mentioned': 'kaggle' in value}
    elif qid.endswith('row-0959'):
        facts = {'radio_waves_mentioned': any(t in value for t in ('radiowaves', 'radio waves', '无线电波')),
                 'microwaves_mentioned': any(t in value for t in ('microwaves', '微波'))}
    else:
        return {'status': 'NOT_APPLICABLE_NO_FROZEN_ENTITY_DIAGNOSTIC'}
    return {'status': 'PROJECT_LITERAL_ENTITY_DIAGNOSTIC', 'facts': facts,
            'all_entities_mentioned': all(facts.values()), 'answer_correctness_or_entailment_proven': False}


def query_scope_checks(identity, case, dataset, entry, documents, expected_docs):
    checks = {'frozen_question_matches': identity.get('question') == case['question'],
        'kb_matches': identity.get('kb') == entry.get('kb') and bool(entry.get('kb'))}
    scope = identity.get('document_scope')
    if dataset.get('scope_mode') != 'knowledge_base':
        checks['document_scope_matches_frozen_source'] = isinstance(scope, list) and bool(expected_docs) and set(scope) == set(expected_docs)
        return checks
    completed = {row['id'] for row in entry.get('documents', {}).values() if row.get('status') == 'completed'}
    actual = identity.get('documents')
    checks.update(knowledge_base_document_scope_empty=isinstance(scope, list) and scope == [],
        knowledge_base_documents_match_completed_sources=isinstance(actual, list) and bool(completed)
            and len(actual) == len(completed) and set(actual) == completed,
        completed_documents_snapshot_in_same_kb=bool(completed) and all(
            documents.get(doc, {}).get('knowledge_base_id') == entry.get('kb')
            and documents.get(doc, {}).get('parse_status') == 'completed'
            and documents.get(doc, {}).get('deleted_at') is None for doc in completed))
    return checks


def wire_payloads(capture, requests, ledger):
    result = []
    for request in requests:
        if not capture or request.get('run_id') != capture['run_id'] or request.get('stage') != 'answer':
            continue
        digest = hashlib.sha256(json.dumps(request.get('body', {}), ensure_ascii=False,
            separators=(',', ':'), sort_keys=True, allow_nan=False).encode()).hexdigest()
        verified = (request.get('boundary') == 'HTTPX_REQUEST_HOOK_AFTER_DURABLE_GATE'
            and digest == request.get('wire_sha256') and any(
                row.get('request_id') == request.get('request_id') and row.get('wire_sha256') == digest
                and row.get('outcome') == 'RESPONSE_RECEIVED' for row in ledger))
        result.append({'request_id': request.get('request_id'), 'wire_sha256': request.get('wire_sha256'),
            'verified': verified, 'finish_reason': request.get('finish_reason'),
            'text': '\n'.join(p9.texts(request.get('body', {}).get('messages', [])))})
    return result


def derived_delivery(candidates, capture, wires):
    context = (capture or {}).get('context', {}).get('rendered_contexts', '')
    prepared = '\n'.join(p9.texts((capture or {}).get('messages', [])))
    facts = []
    for candidate in candidates:
        text = candidate['text']
        facts.append({**{k: v for k, v in candidate.items() if k != 'text'},
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text_length': len(text),
            'in_final_context': p9.contains_ocr(context, text),
            'in_prepared_messages': p9.contains_ocr(prepared, text),
            'in_verified_wire': any(w['verified'] and p9.contains_ocr(w['text'], text) for w in wires)})
    verified = any(w['verified'] for w in wires)
    delivered = any(f['in_final_context'] and f['in_prepared_messages'] and f['in_verified_wire'] for f in facts)
    status = ('NOT_RUN_NO_BOUND_DERIVED_TEXT' if not facts else
              'NOT_RUN_ACTUAL_PROVIDER_PAYLOAD_UNVERIFIED' if not verified else
              'PASS' if delivered else 'FAIL_DERIVED_TEXT_NOT_DELIVERED')
    return {'status': status, 'any_bound_text_delivered': delivered, 'source_facts': facts,
            'quote_validity_implies_delivery': False, 'official_omni_qa': 'NOT_APPLICABLE'}


def inspect_group(run, gold):
    selection = p8.load(run / 'selection.json', {'datasets': []})
    state = p8.load(run / 'state.json', {})
    docs_raw = p8.load(run / 'documents-final.json', [])
    documents = {row['id']: {**row, 'metadata': obj(row.get('metadata'))} for row in docs_raw}
    chunks = p8.load(run / 'chunks-final.json', [])
    tasks = p8.load(run / 'image-tasks-final.json', [])
    ledger = p8.load(run / 'ledger-final.json', [])
    captures, stages, requests = (rows(run / folder) for folder in ('captures', 'stages', 'provider-requests'))
    cases = []
    for dataset in selection.get('datasets', []):
        name = dataset['name']
        entry = state.get('datasets', {}).get(name, {})
        mapping = entry.get('documents', {})
        for case in dataset.get('cases', []):
            qid = case['qid']; prefix = run / ('queries/' + p8.identity(name, qid))
            record = entry.get('queries', {}).get(qid, {})
            query_identity = p8.load(Path(str(prefix) + '-identity.json'), record)
            expected_docs = {row['id']: source for source, row in mapping.items()
                             if not case.get('source_id') or source == case['source_id']}
            identity_checks = query_scope_checks(query_identity, case, dataset, entry, documents, expected_docs)
            # Negatives from the same frozen KB remain legitimate identities;
            # relevance remains relative to the single frozen Gold target.
            ranking_bindings = ({row['id']: source for source, row in mapping.items()}
                if dataset.get('scope_mode') == 'knowledge_base' else expected_docs)
            binding_captures = [c for c in captures if c.get('session_id') == record.get('session') and c.get('question') == case['question']]
            messages = p8.load(Path(str(prefix) + '-messages.json'), {}).get('data', [])
            pairs = [(m, c) for m in messages if m.get('role') == 'assistant' for c in binding_captures
                     if m.get('id') == c.get('message_id') and m.get('request_id') == c.get('run_id')]
            assistant, capture = pairs[0] if len(pairs) == 1 else ({}, {})
            stage = next((s for s in stages if capture and s.get('run_id') == capture['run_id']), {})
            receipts = p8.receipt_facts(ledger, [capture.get('run_id')])
            rankings = p8.classify_stage_execution(p8.stage_rankings(capture, kb=entry.get('kb'), bindings=ranking_bindings,
                qrels=gold.get(qid, {}).get('qrels', {})), receipts)
            for phase, field in [('search', 'search_results'), ('rerank', 'rerank_results'), ('context', 'merge_results')]:
                observed = [s[field] for s in stage.get('stages', []) if field in s]
                stage_rows = observed[-1] if observed else None
                rankings[phase]['key_text'] = text_hits('\n'.join(r.get('content', '') for r in stage_rows or []), qid) if stage_rows is not None else {'status': 'NOT_RUN_NO_STAGE_TEXT'}
                rankings[phase]['observed_chunks'] = [{'chunk_id': r.get('id'), 'document_id': r.get('knowledge_id'),
                    'knowledge_base_id': r.get('knowledge_base_id'), 'source_version': r.get('chunk_metadata', {}).get('source_version'),
                    'source_id': ranking_bindings.get(r.get('knowledge_id')), 'is_expected_source': r.get('knowledge_id') in expected_docs,
                    'key_text': text_hits(r.get('content', ''), qid)} for r in stage_rows or []]
            rankings['context']['rendered_key_text'] = text_hits(capture.get('context', {}).get('rendered_contexts', ''), qid) if capture else {'status': 'NOT_RUN_NO_CONTEXT'}
            source_records = {}
            selected = {s['source_id']: s for s in dataset.get('files', [])}
            for document, source in ranking_bindings.items():
                source_records[document] = {**p8.load(run / ('sources/' + p8.identity(name, source) + '.json'), {}),
                    'selection_sha256': selected.get(source, {}).get('sha256')}
            readback = {r['data']['chunk_id']: r['data'] for r in p8.load(Path(str(prefix) + '-chunkreadback.json'), [])}
            citations = []
            for ref in assistant.get('knowledge_references') or []:
                chunk = readback.get(ref.get('id'), {})
                checked = p8.validate_reference(ref, chunk, kb=entry.get('kb'), bindings=ranking_bindings,
                    source_records=source_records, document_rows=documents)
                offset = p9.citation_offsets(ref, chunk)
                source_positive = checked['document_id'] in expected_docs
                checked.update(offset_readback=offset, readback_locator={k: chunk.get(k) for k in ('page_number', 'image_info', 'source_locators', 'parser_evidence', 'source_url')},
                    valid=checked['valid'] and offset['valid'], source_positive=source_positive,
                    gold_source_relevance='PASS' if source_positive else 'FAIL_NOT_FROZEN_POSITIVE_SOURCE',
                    source_relevance_does_not_prove_entailment=True)
                citations.append(checked)
            candidates = []; parse = []
            for document, source in expected_docs.items():
                doc = documents.get(document, {}); version = doc.get('metadata', {}).get('source_version')
                current = [r for r in chunks if doc.get('parse_status') == 'completed' and doc.get('deleted_at') is None
                           and doc.get('knowledge_base_id') == entry.get('kb')
                           and r.get('knowledge_id') == document and r.get('knowledge_base_id') == entry.get('kb')
                           and r.get('deleted_at') is None and version and obj(r.get('metadata')).get('source_version') == version]
                parse.append({'source_id': source, 'document_id': document, 'source_version': version,
                    'status': 'RECORDED' if doc else 'NOT_RUN_NO_DOCUMENT_SNAPSHOT', 'parse_status': doc.get('parse_status'),
                    'current_chunk_count': len(current), 'key_text': text_hits('\n'.join(r.get('content', '') for r in current), qid),
                    'image_tasks': [{k: task.get(k) for k in ('id', 'state', 'source_version', 'error_code', 'outcomes')}
                        for task in tasks if task.get('knowledge_id') == document and task.get('knowledge_base_id') == entry.get('kb') and task.get('source_version') == version]})
                for row in current:
                    meta = obj(row.get('metadata')); info = obj(row.get('image_info'))
                    texts = [(i.get('ocr_text'), 'IMAGE_OCR_INFO') for i in info if isinstance(i, dict)] if isinstance(info, list) else []
                    if row.get('chunk_type') == 'image_ocr': texts.append((row.get('content'), 'IMAGE_OCR_CHUNK'))
                    mineru = meta.get('mineru_source') or {}
                    if (mineru and meta.get('source_locators')
                        and mineru == doc.get('metadata', {}).get('parser_metadata', {}).get('mineru_source')
                        and mineru.get('document_id') == document and mineru.get('source_version') == version
                        and mineru.get('source_sha256') == doc.get('metadata', {}).get('sha256')):
                        texts.append((row.get('source_content') or row.get('content'), 'MINERU_SOURCE_LOCATORS'))
                    for text, origin in texts:
                        if text: candidates.append({'chunk_id': row['id'], 'document_id': document, 'source_id': source,
                            'knowledge_base_id': entry.get('kb'), 'source_version': version, 'origin': origin, 'text': text})
            wires = wire_payloads(capture, requests, ledger)
            raw = assistant.get('content'); prediction = p8.plain_answer(raw) if raw is not None else None
            completed = bool(assistant.get('is_completed') and prediction and not assistant.get('turn_error_code') and assistant.get('turn_status') not in ('failed', 'error', 'cancelled'))
            context_hit = rankings['context']['rendered_key_text'].get('all_terms_hit')
            cases.append({'dataset': name, 'qid': qid, 'question': case['question'], 'query_identity': query_identity,
                'query_identity_checks': identity_checks, 'query_identity_status': 'PASS' if all(identity_checks.values()) else 'FAIL' if record else 'NOT_RUN',
                'status': 'RECORDED' if len(pairs) == 1 else 'NOT_RUN_UNIQUE_RESULT_BINDING_REQUIRED', 'server_run_id': capture.get('run_id'),
                'parse': parse, 'stages': rankings, 'answer_full': raw, 'plain_answer_full': prediction,
                'answer_key_text': text_hits(prediction or '', qid), 'completed': completed,
                'answer_entity_diagnostic': answer_entities(prediction or '', qid),
                'evidence_support_missing': not context_hit if context_hit is not None else None,
                'evidence_support_basis': 'FROZEN_REQUIRED_KEY_TEXT_ABSENT_FROM_FINAL_CONTEXT' if context_hit is False else
                    'KEY_TEXT_PRESENT_SEMANTIC_ENTAILMENT_NOT_EVALUATED' if context_hit is True else 'NOT_RUN_NO_KEY_TEXT_OR_CONTEXT',
                'citation_semantic_support': 'NOT_EVALUATED_QUOTE_VALIDITY_DOES_NOT_PROVE_ENTAILMENT',
                'answer_diagnostics': p9.system_answer_gold(run, name, gold.get(qid), prediction, completed=completed,
                    query_attempted=bool(record), answer_model_called=receipts['checks']['chat'], fixed_refusal=capture.get('fixed_fallback') is not None),
                'citations': citations, 'quote_validity': {'status': 'PASS' if citations and all(r['valid'] for r in citations) else 'FAIL' if citations else 'NOT_RUN_NO_CITATIONS'},
                'derived_text_delivery': derived_delivery(candidates, capture, wires),
                'provider_wires': [{k: v for k, v in w.items() if k != 'text'} for w in wires], 'real_provider_receipts': receipts,
                'official_omni_qa': 'NOT_APPLICABLE_NO_OFFICIAL_QA_GOLD' if name == 'omnidocbench' else 'NOT_APPLICABLE',
                'artifact_sha256': {suffix: p9.artifact_hash(Path(str(prefix) + suffix)) for suffix in ('-messages.json', '-identity.json', '-references.json', '-chunkreadback.json')}})
    return {'group': run.name, 'cases': cases, 'snapshot_inputs': {name: {'path': str(run / name), 'sha256': p9.artifact_hash(run / name),
        'status': 'PRESENT' if (run / name).exists() else 'NOT_RUN_MISSING_SNAPSHOT'} for name in
        ('documents-final.json', 'chunks-final.json', 'image-tasks-final.json', 'ledger-final.json', 'ledger-before.json')}, 'source_sha': state.get('source_sha')}


def evaluate(root, gold_path=GOLD):
    gold = {r['qid']: r for r in p8.load(gold_path, {}).get('mml', [])}
    groups = [inspect_group(root / name, gold) for name in ('A', 'B')]
    return {'schema': 'p11-readonly-real-evidence-results/v1', 'groups': groups,
        'gold_path': str(gold_path), 'gold_sha256': p9.artifact_hash(gold_path),
        'counts': dict(Counter(c['status'] for g in groups for c in g['cases'])),
        'full_benchmark_acceptance': 'NOT_CLAIMED', 'official_judge': 'NOT_RUN',
        'scoring_method': 'EXISTING_FULL_ANSWER_PROJECT_EM_CHAR_F1_NO_GOLD_REWRITE'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('OUTPUT_EXISTS_PRESERVE_PRIOR_RESULT')
    result = evaluate(args.root_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'schema': result['schema'], 'counts': result['counts'], 'output': str(args.output)}))


if __name__ == '__main__': main()
