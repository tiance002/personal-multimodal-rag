"""Offline counterexamples only; these fakes do not prove real model quality."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from scripts.p9_score_real import ocr_delivery, evaluate_p9, citation_offsets, system_answer_gold, inspect_run
from scripts.weknora_port_p9_observed_api import install_wire_observer


def evidence(*, include_ocr):
    ocr = 'Changes to English\nChange 7 pronunciation'
    context = '<context id="1">![page](images/page.png)' + ('\n\n> ' + ocr.replace('\n', '\n> ') if include_ocr else '') + '</context>'
    capture = {'run_id': 'run', 'evidence': [{'knowledge_base_id': 'kb', 'knowledge_id': 'doc', 'chunk_metadata': {'source_version': 'v1'}}],
        'context': {'rendered_contexts': context}, 'messages': [{'role': 'user', 'content': context}]}
    stage = {'stages': [{'search_results': [{'id': 'ocr', 'chunk_type': 'image_ocr', 'content': ocr,
        'knowledge_base_id': 'kb', 'knowledge_id': 'doc', 'chunk_metadata': {'source_version': 'v1'}}]}]}
    body = {'messages': capture['messages'], 'model': 'model'}
    sha = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
    request = {'run_id': 'run', 'request_id': 'run/answer/1/cheap', 'stage': 'answer',
        'boundary': 'HTTPX_REQUEST_HOOK_AFTER_DURABLE_GATE', 'body': body, 'wire_sha256': sha}
    ledger = [{'request_id': request['request_id'], 'wire_sha256': sha, 'outcome': 'RESPONSE_RECEIVED'}]
    return capture, stage, request, ledger


def test_valid_placeholder_quote_is_not_ocr_context_or_delivery_pass():
    capture, stage, request, ledger = evidence(include_ocr=False)
    original_quote = '![page](images/page.png)'
    assert original_quote == original_quote  # Valid readback alone cannot imply OCR.
    result = ocr_delivery(capture, stage, [request], ledger, required=True)
    assert result['status'] == 'FAIL_OCR_MISSING_FROM_FINAL_CONTEXT'
    assert result['pass'] is False
    assert result['quote_validity_implies_ocr_delivery'] is False


@pytest.mark.parametrize('reference,chunk,status', [
    ({'start_at': 0, 'end_at': 0}, {'start': 0, 'end': 0}, 'PASS'),
    ({'start_at': 0, 'end_at': 139}, {'start': 0, 'end': 139}, 'PASS'),
    ({'start_at': 0, 'end_at': 139}, {'start': 0, 'end': 0}, 'FAIL_OFFSET_READBACK_MISMATCH'),
    ({'end_at': 0}, {'start': 0, 'end': 0}, 'NOT_RUN_EXPLICIT_SOURCE_OFFSETS_REQUIRED'),
])
def test_citation_offsets_require_explicit_exact_readback_and_preserve_zero(reference, chunk, status):
    result = citation_offsets(reference, chunk)
    assert result['status'] == status
    assert result['valid'] is (status == 'PASS')


def test_real_ocr_delivery_requires_body_hash_and_ledger_while_quote_stays_raw():
    capture, stage, request, ledger = evidence(include_ocr=True)
    original_quote = '![page](images/page.png)'
    assert 'pronunciation' not in original_quote
    assert ocr_delivery(capture, stage, [request], ledger, required=True)['status'] == 'PASS'
    assert ocr_delivery(capture, stage, [], ledger, required=True)['status'] == 'NOT_RUN_ACTUAL_PROVIDER_PAYLOAD_UNVERIFIED'
    changed = deepcopy(request); changed['body']['messages'][0]['content'] = original_quote
    assert ocr_delivery(capture, stage, [changed], ledger, required=True)['pass'] is False
    wrong_ledger = deepcopy(ledger); wrong_ledger[0]['wire_sha256'] = 'wrong'
    assert ocr_delivery(capture, stage, [request], wrong_ledger, required=True)['pass'] is False


def test_existing_p81_recheck_placeholder_is_not_ocr_pass():
    folder = Path('E:/WK/run/p8-1-20261016/recheck')
    captures = list((folder / 'captures').glob('*.json'))
    if not captures: pytest.skip('Historical real recheck evidence unavailable')
    capture = json.loads(captures[0].read_text(encoding='utf-8'))
    stage_path = folder / 'stages' / captures[0].name
    stage = json.loads(stage_path.read_text(encoding='utf-8'))
    result = ocr_delivery(capture, stage, [], [], required=True)
    assert result['status'] == 'FAIL_OCR_MISSING_FROM_FINAL_CONTEXT'
    assert result['source_facts']
    assert result['pass'] is False


def test_missing_results_keep_40_questions_and_24_pages(tmp_path):
    selected = {'datasets': [{'name': name, 'files': [], 'cases': [{'qid': str(i), 'question': 'original'} for i in range(8)]}
        for name in ('scifact', 'miracl-zh', 'longbench-zh', 'hotpotqa', 'mmlongbench-doc')]}
    (tmp_path / 'selection.json').write_text(json.dumps(selected), encoding='utf-8')
    (tmp_path / 'gold-selected.json').write_text(json.dumps({'datasets': [{'name': 'omnidocbench', 'pages': [{'page_id': str(i)} for i in range(24)]}]}), encoding='utf-8')
    result = evaluate_p9(tmp_path)
    assert result['question_denominator'] == 40 and result['omni_page_denominator'] == 24
    assert all(row['status'] == 'NOT_RUN' for row in result['cases'])
    assert all(row['status'] == 'NOT_RUN' for row in result['omni_pages'])


def test_unexecuted_selected_cases_are_not_recorded_attempts_and_capability_stays_blocked(tmp_path):
    selected = {'datasets': [{'name': 'mmlongbench-doc', 'files': [], 'cases': [
        {'qid': 'blocked', 'question': 'visual question', 'execution_status': 'NO_MODEL_CALL_CAPABILITY_LIMIT',
         'execution_reason': 'Native text path lacks visual evidence'},
        {'qid': 'later', 'question': 'future question'}]}]}
    baseline = tmp_path / 'baseline'; baseline.mkdir()
    for path in (tmp_path / 'selection.json', baseline / 'selection.json'):
        path.write_text(json.dumps(selected), encoding='utf-8')
    (baseline / 'state.json').write_text(json.dumps({'datasets': {}}), encoding='utf-8')
    result = evaluate_p9(tmp_path)
    assert result['question_denominator'] == 2
    assert [row['status'] for row in result['cases']] == ['BLOCKED_CAPABILITY_LIMIT', 'NOT_RUN']
    assert all(row['actual_query_attempt_count'] == 0 and row['attempts'] == [] for row in result['cases'])
    from scripts.p8_score_real import identity
    (baseline / 'attempts.json').write_text(json.dumps({'query-' + identity('mmlongbench-doc', 'later'):
        {'state': 'UNKNOWN', 'at': '2026-10-16T12:00:00+00:00'}}), encoding='utf-8')
    result = evaluate_p9(tmp_path)
    assert result['cases'][1]['status'] == 'RECORDED_ATTEMPTS'
    assert result['cases'][1]['actual_query_attempt_count'] == 1
    assert not result['cases'][1]['attempts'][0]['query_execution_evidence']['actual_model_call_claimed']


def test_completed_fixed_refusal_counts_system_answer_metric_without_claiming_answer_model(tmp_path):
    case = {'qid': 'hotpot', 'answers': ['Pieter van Musschenbroek']}
    result = system_answer_gold(tmp_path, 'hotpotqa', case, 'Sorry, I am unable to answer this question.',
        completed=True, query_attempted=True, answer_model_called=False, fixed_refusal=True)
    assert result['origin'] == 'FIXED_REFUSAL'
    assert result['answer_model_called'] is False
    assert result['included_in_answer_metric_denominator'] is True
    assert result['metrics']['hotpot_answer_em'] == 0
    assert result['metrics']['hotpot_answer_token_f1'] == 0
    missing = system_answer_gold(tmp_path, 'hotpotqa', case, None,
        completed=False, query_attempted=False, answer_model_called=False, fixed_refusal=False)
    assert missing['status'] == 'NOT_RUN_NO_COMPLETED_REAL_APP_RESPONSE'
    assert missing['metrics'] is None and not missing['included_in_answer_metric_denominator']
    no_gold = system_answer_gold(tmp_path, 'scifact', {'qid': 'claim', 'answers': []}, 'fixed refusal',
        completed=True, query_attempted=True, answer_model_called=False, fixed_refusal=True)
    assert no_gold['status'] == 'NOT_APPLICABLE_NO_ANSWER_GOLD' and no_gold['metrics'] is None


def test_recorded_final_snapshot_supplies_new_calls_without_touching_old_after(tmp_path):
    def save(name, value):
        (tmp_path / name).write_text(json.dumps(value), encoding='utf-8')
    save('selection.json', {'datasets': []})
    save('state.json', {'datasets': {}})
    save('snapshot-selection.json', {'label': 'final'})
    save('ledger-before.json', [{'call_id': 'old'}])
    save('ledger-after.json', [{'call_id': 'old'}])
    old_bytes = (tmp_path / 'ledger-after.json').read_bytes()
    save('ledger-final.json', [{'call_id': 'old'}, {'call_id': 'new'}])
    for name in ('documents', 'chunks', 'image-tasks'):
        save(name + '-after.json', [])
        save(name + '-final.json', [])
    result = inspect_run(tmp_path)
    assert result['snapshot_selection']['label'] == 'final'
    assert result['snapshot_selection']['status'] == 'SELECTED'
    assert result['ledger_delta'] == [{'call_id': 'new'}]
    assert result['inputs']['ledger-after.json']['path'] == str(tmp_path / 'ledger-final.json')
    assert result['inputs']['ledger-after.json']['sha256'] == hashlib.sha256((tmp_path / 'ledger-final.json').read_bytes()).hexdigest()
    assert (tmp_path / 'ledger-after.json').read_bytes() == old_bytes
    (tmp_path / 'ledger-final.json').unlink()
    missing = inspect_run(tmp_path)
    assert missing['snapshot_selection']['status'] == 'BLOCKED_MISSING_SELECTED_SNAPSHOT'
    assert missing['snapshot_selection']['fallback_used'] is False
    assert missing['ledger_delta'] == []
    assert (tmp_path / 'ledger-after.json').read_bytes() == old_bytes


def test_wire_observer_preserves_single_gate_and_model_call_and_records_wire_hash(tmp_path):
    import httpx
    from backend.weknora_port.models.types import ChatResponse
    calls = []
    response = ChatResponse(content='answer', finish_reason='stop')
    body = {'model': 'model', 'messages': [{'role': 'user', 'content': 'actual OCR'}]}
    raw = json.dumps(body, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
    context = SimpleNamespace(request_id='run/answer/1/cheap')
    class Transport:
        def __init__(self): self.client = SimpleNamespace(event_hooks={}); self.gate = object()
        @asynccontextmanager
        async def open(self, config, path, supplied_body, supplied_context, **kwargs):
            calls.append(('gate', supplied_context, supplied_body))
            request = httpx.Request('POST', 'https://fixture.invalid/chat', content=raw,
                                    headers={'Authorization': 'Bearer SECRET_NEVER_SAVED'})
            for hook in self.client.event_hooks['request']: await hook(request)
            for hook in self.client.event_hooks['response']: await hook(httpx.Response(200, request=request))
            yield object()
    class Model:
        def __init__(self): self.transport = Transport(); self.config = SimpleNamespace(provider='siliconflow', model='model')
        async def chat(self, messages, options, supplied_context, **kwargs):
            calls.append(('model', supplied_context, messages))
            async with self.transport.open(self.config, '', body, supplied_context, capability='chat'): pass
            return response
        async def chat_stream(self, messages, options, supplied_context):
            raise AssertionError('Not invoked')
            yield
    model = Model(); gate = model.transport.gate
    install_wire_observer(model, tmp_path)
    result = asyncio.run(model.chat(body['messages'], None, context))
    assert result is response and len(calls) == 2
    assert model.transport.gate is gate
    assert all(call[1] is context for call in calls)
    artifact = next((tmp_path / 'provider-requests').glob('*.json')).read_text(encoding='utf-8')
    assert 'SECRET_NEVER_SAVED' not in artifact and 'Authorization' not in artifact
    saved = json.loads(artifact)
    assert saved['body'] == body and saved['wire_sha256'] == hashlib.sha256(raw).hexdigest()
    assert saved['finish_reason'] == 'stop'
