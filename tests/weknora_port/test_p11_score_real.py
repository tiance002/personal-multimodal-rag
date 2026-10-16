"""Synthetic scorer counterexamples; no real service or model verification."""
import hashlib
import json

from scripts.p11_score_real import derived_delivery, evaluate, text_hits, wire_payloads, query_scope_checks


def test_missing_run_does_not_invent_result_or_score(tmp_path):
    for group in ('A', 'B'):
        folder = tmp_path / group; folder.mkdir()
        (folder / 'selection.json').write_text(json.dumps({'datasets': [{'name': 'mmlongbench-doc',
            'cases': [{'qid': 'q', 'question': 'frozen'}]}]}), encoding='utf-8')
    result = evaluate(tmp_path, tmp_path / 'missing-gold.json')
    assert result['counts'] == {'NOT_RUN_UNIQUE_RESULT_BINDING_REQUIRED': 2}
    assert all(g['cases'][0]['answer_diagnostics']['metrics'] is None for g in result['groups'])
    assert all(g['cases'][0]['quote_validity']['status'] == 'NOT_RUN_NO_CITATIONS' for g in result['groups'])


def test_derived_wire_requires_hash_receipt_and_content():
    text = 'Actual parser evidence'
    capture = {'run_id': 'run', 'context': {'rendered_contexts': text}, 'messages': [{'content': text}]}
    candidate = {'text': text, 'origin': 'MINERU_SOURCE_LOCATORS', 'chunk_id': 'c'}
    body = {'messages': [{'content': text}]}
    sha = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
    request = {'run_id': 'run', 'stage': 'answer', 'request_id': 'r', 'wire_sha256': sha,
        'boundary': 'HTTPX_REQUEST_HOOK_AFTER_DURABLE_GATE', 'body': body}
    ledger = [{'request_id': 'r', 'wire_sha256': sha, 'outcome': 'RESPONSE_RECEIVED'}]
    assert derived_delivery([candidate], capture, wire_payloads(capture, [request], ledger))['status'] == 'PASS'
    assert derived_delivery([candidate], capture, wire_payloads(capture, [request], []))['status'].startswith('NOT_RUN')
    request['body']['messages'][0]['content'] = 'placeholder citation'
    assert derived_delivery([candidate], capture, wire_payloads(capture, [request], ledger))['any_bound_text_delivered'] is False


def test_0959_requires_both_terms_and_preserves_full_answer_metric_policy():
    assert text_hits('Radiowaves are used.', 'row-0959')['all_terms_hit'] is False
    assert text_hits('Radio waves and microwaves', 'row-0959')['all_terms_hit'] is True


def test_explicit_kb_scope_rejects_extra_or_cross_kb_docs_and_keeps_document_scope_strict():
    case = {'question': 'q'}
    entry = {'kb': 'kb', 'documents': {'s1': {'id': 'd1', 'status': 'completed'}, 's2': {'id': 'd2', 'status': 'completed'}}}
    docs = {d: {'knowledge_base_id': 'kb', 'parse_status': 'completed'} for d in ('d1', 'd2')}
    identity = {'question': 'q', 'kb': 'kb', 'document_scope': [], 'documents': ['d1', 'd2']}
    mode = {'scope_mode': 'knowledge_base'}
    assert all(query_scope_checks(identity, case, mode, entry, docs, {'d1': 's1'}).values())
    assert not all(query_scope_checks({**identity, 'documents': ['d1', 'd2', 'extra']}, case, mode, entry, docs, {'d1': 's1'}).values())
    cross = {**docs, 'd2': {'knowledge_base_id': 'other', 'parse_status': 'completed'}}
    assert not all(query_scope_checks(identity, case, mode, entry, cross, {'d1': 's1'}).values())
    assert not all(query_scope_checks(identity, case, {'scope_mode': 'document'}, entry, docs, {'d1': 's1'}).values())
