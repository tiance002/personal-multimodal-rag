"""Offline frozen-cohort/profile/HTTP collector checks, no model transports."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import httpx
import pytest

from backend.weknora_port.http_api import SessionCreate, MessageCreate, GenerationSettingsWrite
from backend.weknora_port.models.types import TrustedScope
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
from backend.weknora_port.generation.prompt_candidates import apply_candidate
from scripts import s88_r2_unified_acceptance as unified
from scripts.s88_r2_input_contract import inference_inputs
from test_s88_acceptance_profile import fixture as configuration_fixture


def plan_fixture(tmp_path):
    cases = []
    for ordinal in range(55):
        dataset = ('longbench-zh', 'mmlongbench-doc', 'hotpotqa')[ordinal % 3]
        question = f'Synthetic source question {ordinal}?'
        cases.append({'ordinal': ordinal, 'case_id': f'{dataset}:fixture-{ordinal}',
            'dataset': dataset, 'qid': f'fixture-{ordinal}', 'old_question': question,
            'new_question': question, 'question_changed': False, 'status': 'FROZEN',
            'q0_sha256': unified.digest(question.encode()),
            'new_scope': {'owner_id': 'owner', 'knowledge_base_ids': ['kb'],
                          'document_ids': [] if dataset == 'hotpotqa' else [f'doc-{ordinal}']},
            'answers': ['AUDIT_ONLY_NEVER_SEND'], 'historical_label': 'AUDIT_ONLY_NEVER_SEND'})
    manifest = {'schema': 'ECS55_INPUT_CONTRACT_V2', 'status': 'FROZEN_OFFLINE', 'cases': cases}
    inputs = inference_inputs(manifest)
    ledger = tmp_path / 'execution.json'
    ledger.write_text(json.dumps({'status': 'OPEN', 'accounting_mode': 'reported_usage_only_v1',
        'max_calls': 1000, 'max_tokens': 1000000, 'entries': []}), encoding='utf8')
    ids = {i: f's88-r2-{i}-new-fixture-id' for i in range(55)}
    models = {role: {'model_id': f'{role}-id', 'provider': 'fixture', 'model': f'{role}-model',
        'capability': {'embedding': 'embedding', 'rerank': 'rerank'}.get(role, 'chat')}
        for role in ('qu', 'cheap', 'expensive', 'embedding', 'rerank')}
    plan = unified.build_plan(inputs, frozen_manifest=manifest, model_bindings=models, request_ids=ids,
        qu_request_sha256s={client: 'c' * 64 for client in ids.values()},
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        deployment={'image_id': 'sha256:' + 'a' * 64, 'source_commit': 'b' * 40}, execution_file=ledger)
    return manifest, plan


def rehash(plan):
    plan['plan_sha256'] = unified.digest({k: v for k, v in plan.items() if k != 'plan_sha256'})


def test_frozen_manifest_projects_only_inputs_and_binds_actual_catalog_v3(tmp_path):
    manifest, plan = plan_fixture(tmp_path)
    assert unified.validate_frozen_cohort(manifest, plan['inference_inputs']) == plan['inference_inputs']
    assert 'AUDIT_ONLY_NEVER_SEND' not in json.dumps(plan)
    runtime, cfg, turn, _ = configuration_fixture()
    case = plan['inference_inputs']['cases'][0]
    cfg.pipeline_state.query = turn.request.content = case['question']
    cfg.pipeline_state.rerank_context_enabled = True
    turn.client_request_id = plan['request_ids']['0']
    turn.context = replace(turn.context, scope=TrustedScope('owner', ('kb',), ('doc-0',)))
    original_options = cfg.generation_options['options']
    original_policy = cfg.generation_options['policy']
    unified.install(runtime, plan, tmp_path / 'audit')
    result = asyncio.run(runtime.ordinary.configuration(turn))
    assert result.generation_options['system_prompt'] == apply_candidate(
        DEFAULT_SYSTEM_PROMPT + '\n\n' + unified.prior.PROMPT_B_SUFFIX, version='s88-source-precision/v3')
    assert result.generation_options['options'] is original_options
    assert result.generation_options['policy'] is original_policy
    assert result.fallback is cfg.fallback
    assert cfg.generation_options['system_prompt'] == DEFAULT_SYSTEM_PROMPT
    audit = json.loads(next((tmp_path / 'audit').glob('*.r2-prompt.json')).read_text())
    assert audit['prompt_version'] == 's88-source-precision/v3'
    # Caller mutation cannot broaden installed admission.
    plan['inference_inputs']['cases'][0]['document_ids'] = []
    turn.context = replace(turn.context, scope=TrustedScope('owner', ('kb',)))
    with pytest.raises(ValueError, match='S861_APPROVED_SCOPE_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))


@pytest.mark.parametrize('change,code', [
    ('gold', 'WHITELIST'), ('scope', 'TASK_SCOPE'), ('q0', 'Q0_SHA256'),
    ('ordinal', 'UNIQUE_ORDINAL'), ('duplicate', 'UNIQUE_ORDINAL'), ('qu', 'EXACT_QU'),
    ('expiry', 'EXPIRED'), ('prompt', 'UNIFORM_PROFILE')])
def test_changed_or_contaminated_plan_refuses_without_http(tmp_path, change, code):
    _, plan = plan_fixture(tmp_path)
    case = plan['inference_inputs']['cases'][0]
    if change == 'gold': case['expected_answers'] = ['forbidden']
    if change == 'scope': case['document_ids'] = []
    if change == 'q0': case['question'] += ' changed'
    if change == 'ordinal': case['ordinal'] = True
    if change == 'duplicate': case['case_id'] = plan['inference_inputs']['cases'][1]['case_id']
    if change == 'qu': plan['qu_request_sha256s'].pop(plan['request_ids']['0'])
    if change == 'expiry': plan['expires_at'] = '2000-01-01T00:00:00+00:00'
    if change == 'prompt': plan['prompt_version'] = 'private-same-name-v3'
    rehash(plan)
    with pytest.raises(ValueError, match=code): unified.validate_plan(plan)


def test_manifest_projection_cannot_spoof_selected_document_or_changed_question(tmp_path):
    manifest, plan = plan_fixture(tmp_path)
    altered = deepcopy(plan['inference_inputs'])
    altered['cases'][0]['document_ids'] = ['other-doc']
    with pytest.raises(ValueError, match='MANIFEST_INFERENCE_MISMATCH'):
        unified.validate_frozen_cohort(manifest, altered)


@pytest.mark.parametrize('scope', [TrustedScope('owner', ('other',), ('doc-0',)),
    TrustedScope('owner', ('kb',), ('other-doc',)), TrustedScope('other-owner', ('kb',), ('doc-0',))])
def test_three_scope_boundaries_block_before_normal_configuration(tmp_path, scope):
    _, plan = plan_fixture(tmp_path)
    runtime, _, turn, _ = configuration_fixture()
    calls = []
    async def forbidden(_):
        calls.append(True)
        raise AssertionError('must not reach configuration/provider')
    runtime.ordinary.configuration = forbidden
    turn.request.content = plan['inference_inputs']['cases'][0]['question']
    turn.client_request_id = plan['request_ids']['0']
    turn.context = replace(turn.context, scope=scope)
    unified.install(runtime, plan)
    with pytest.raises(ValueError, match='SCOPE'):
        asyncio.run(runtime.ordinary.configuration(turn))
    assert calls == []


class SyntheticHTTP:
    """HTTP contract fixture; all generation and native usage are synthetic."""
    def __init__(self, plan, *, fixed=False, fail_stream=False, replay_send=False, source_scope=None):
        self.plan, self.fixed, self.fail_stream = plan, fixed, fail_stream
        self.replay_send, self.source_scope = replay_send, source_scope
        self.sessions, self.posts, self.replays = {}, [], []
        self.ledger = __import__('pathlib').Path(plan['execution_file'])

    def append_entry(self, request_id):
        value = json.loads(self.ledger.read_text())
        capability = 'chat'
        value['entries'].append({'id': hashlib.sha256(json.dumps([request_id, capability]).encode()).hexdigest(),
            'request_id': request_id, 'provider': 'fixture', 'model': 'qu-model', 'model_id': 'qu-id',
            'capability': capability, 'wire_sha256': 'a' * 64, 'wire_bytes': 64, 'output_budget': 2048,
            'status': 'SETTLED', 'outcome': 'RESPONSE_RECEIVED',
            'usage': {'status': 'REPORTED', 'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11}})
        self.ledger.write_text(json.dumps(value), encoding='utf8')

    @staticmethod
    def wire(frames):
        return ''.join('event: message\ndata: ' + json.dumps(f) + '\n\n' for f in frames)

    async def __call__(self, request):
        path, method = request.url.path, request.method
        if path == '/api/v1/runtime': return httpx.Response(200, json={'data': {'implementation': 'weknora-python-port'}})
        if path == '/api/v1/sessions':
            assert method == 'POST'
            body = json.loads(request.content)
            SessionCreate.model_validate(body)
            sid = 'session-' + str(len(self.sessions))
            self.sessions[sid] = {'id': sid, **body}
            return httpx.Response(200, json={'data': self.sessions[sid]})
        if path.startswith('/api/v1/chunks/'):
            sid = path.split('/')[-1].removeprefix('chunk-')
            session = self.sessions[sid]
            return httpx.Response(200, json={'data': {'chunk_id': 'chunk-' + sid,
                'knowledge_base_id': self.source_scope or session['knowledge_base_scope'][0],
                'document_id': (session['document_scope'] or ['whole-kb-document'])[0],
                'source_version': 'version-1', 'text': 'Synthetic source passage.'}})
        if '/continue-stream/' in path:
            sid = path.rsplit('/', 1)[-1]
            self.replays.append(sid)
            if self.replay_send: self.append_entry('unexpected-replay-send')
            return httpx.Response(200, text=self.wire(self.sessions[sid]['frames']))
        sid = path.split('/')[4]
        session = self.sessions[sid]
        if path.endswith('/generation-settings'):
            if method == 'PUT':
                body = json.loads(request.content)
                GenerationSettingsWrite.model_validate(body)
                session['settings'] = body
            return httpx.Response(200, json={'data': {'saved': session['settings'], 'effective': session['settings']}})
        assert path.endswith('/messages')
        if method == 'GET': return httpx.Response(200, json={'data': session.get('history', [])})
        body = json.loads(request.content)
        MessageCreate.model_validate(body)
        case = next(c for c in self.plan['inference_inputs']['cases'] if self.plan['request_ids'][str(c['ordinal'])] == body['request_id'])
        assert body['content'] == case['question']
        assert body['document_scope'] == case['document_ids']
        assert body['knowledge_base_scope'] == case['knowledge_base_ids']
        assert body['reasoning_mode'] == 'quick' and body['max_output_tokens'] == 2048
        assert 'query_type' not in body and 'AUDIT_ONLY_NEVER_SEND' not in request.content.decode()
        self.posts.append(body)
        self.append_entry(body['request_id'])
        run, message = 'run-' + sid, 'answer-' + sid
        text = 'Please specify the intended subject.' if self.fixed else 'Synthetic supported answer.'
        refs = [] if self.fixed else [{'id': 'chunk-' + sid, 'metadata': {'source_version': 'version-1'}}]
        terminal = {'finish_reason': 'stop', 'truncated': False}
        if self.fixed:
            terminal = {'completion_source': 'server_fixed', 'provider_completion_required': False,
                'lifecycle_completed': True, 'is_fallback': True, 'attempts': []}
        frames = [{'id': run, 'response_type': 'agent_query', 'data': {'request_id': run,
            'query': case['question'], 'session_id': sid, 'assistant_message_id': message}},
            {'id': run, 'response_type': 'references', 'knowledge_references': refs},
            {'id': run, 'response_type': 'answer', 'content': text, 'done': True},
            {'id': run, 'response_type': 'complete', 'data': terminal}]
        session['frames'] = frames
        session['history'] = [{'role': 'user', 'content': case['question']}, {'id': message, 'role': 'assistant',
            'content': text, 'request_id': run, 'knowledge_references': refs,
            'is_completed': True, 'turn_status': 'completed'}]
        if self.fail_stream:
            class Broken(httpx.AsyncByteStream):
                async def __aiter__(inner):
                    yield SyntheticHTTP.wire(frames[:-1]).encode()
                    raise httpx.ReadError('synthetic disconnected stream')
            return httpx.Response(200, stream=Broken())
        return httpx.Response(200, text=self.wire(frames))


def test_three_distinct_scoped_cases_share_one_api_without_duplicate_post_or_replay_generation(tmp_path):
    _, plan = plan_fixture(tmp_path)
    transport = SyntheticHTTP(plan)
    async def run():
        async with httpx.AsyncClient(base_url='http://fixture', transport=httpx.MockTransport(transport)) as client:
            return [await unified.collect_one(client, plan, ordinal, tmp_path / f'case-{ordinal}',
                binding=plan['deployment']) for ordinal in (0, 1, 2)]
    results = asyncio.run(run())
    assert all(r['status'] == 'HTTP_COMPLETED_REVIEW_REQUIRED' for r in results), results
    assert len(transport.sessions) == len(transport.posts) == len(transport.replays) == 3
    assert [r['document_scope'] for r in transport.posts] == [['doc-0'], ['doc-1'], []]
    assert len(json.loads(transport.ledger.read_text())['entries']) == 3
    for ordinal in (0, 1, 2):
        delta = json.loads((tmp_path / f'case-{ordinal}/ledger-delta.json').read_text())
        assert len(delta['added']) == 1 and not delta['changed'] and not delta['removed_ids']
    assert all(r['final_answer_correct'] == 'NOT_REVIEWED' for r in results)
    # Exclusive output/submission claim: rerunning a case cannot send again.
    async def repeated():
        async with httpx.AsyncClient(base_url='http://fixture', transport=httpx.MockTransport(transport)) as client:
            await unified.collect_one(client, plan, 0, tmp_path / 'case-0', binding=plan['deployment'])
    with pytest.raises(FileExistsError): asyncio.run(repeated())
    assert len(transport.posts) == 3


@pytest.mark.parametrize('failure', ['stream', 'replay', 'source', 'fixed'])
def test_terminal_failure_and_server_fixed_evidence_is_saved_without_retry_or_quality_fabrication(tmp_path, failure):
    _, plan = plan_fixture(tmp_path)
    transport = SyntheticHTTP(plan, fixed=failure == 'fixed', fail_stream=failure == 'stream',
        replay_send=failure == 'replay', source_scope='wrong-kb' if failure == 'source' else None)
    async def run():
        async with httpx.AsyncClient(base_url='http://fixture', transport=httpx.MockTransport(transport)) as client:
            return await unified.collect_one(client, plan, 0, tmp_path / 'case', binding=plan['deployment'])
    result = asyncio.run(run())
    assert len(transport.posts) == 1 and result['message_posts'] == 1
    assert (tmp_path / 'case/response.sse').exists()
    assert (tmp_path / 'case/history.json').exists()
    assert (tmp_path / 'case/result.json').exists()
    assert result['final_answer_correct'] == result['evidence_support'] == 'NOT_REVIEWED'
    if failure == 'fixed':
        assert result['status'] == 'HTTP_COMPLETED_REVIEW_REQUIRED'
        assert result['interaction_kind'] == 'SERVER_FIXED_REPLY_REVIEW_REQUIRED'
        assert result['clarification'] == 'REASONABLENESS_NOT_REVIEWED'
    else:
        assert result['status'] == 'FAILED'
        if failure == 'stream': assert result['error']['type'] == 'ReadError'
        if failure == 'replay': assert 'REPLAY_NEW_SEND' in result['verification_error']['message']
        if failure == 'source': assert 'CITATION_SCOPE' in result['verification_error']['message']


def test_wrong_image_source_binding_blocks_before_any_http(tmp_path):
    _, plan = plan_fixture(tmp_path)
    async def forbidden(_): raise AssertionError('HTTP must not be reached')
    async def run():
        async with httpx.AsyncClient(base_url='http://fixture', transport=httpx.MockTransport(forbidden)) as client:
            await unified.collect_one(client, plan, 0, tmp_path / 'case', binding={**plan['deployment'], 'source_commit': 'd' * 40})
    with pytest.raises(ValueError, match='EXECUTION_BINDING_MISMATCH'): asyncio.run(run())


def test_native_ledger_delta_preserves_old_unknown_without_reclassification():
    old = {'entries': [{'id': 'old', 'request_id': 'historical-unknown', 'capability': 'chat',
        'status': 'UNKNOWN', 'usage': None}]}
    new = deepcopy(old)
    new['entries'].append({'id': 'new', 'request_id': 'new-native-request', 'capability': 'chat',
        'status': 'SETTLED', 'usage': {'status': 'REPORTED', 'prompt_tokens': 1,
            'completion_tokens': 0, 'total_tokens': 1}})
    delta = unified.ledger_delta(old, new)
    assert old['entries'][0]['status'] == new['entries'][0]['status'] == 'UNKNOWN'
    assert not delta['changed'] and not delta['removed_ids']
    assert delta['added_identities'] == [{'request_id': 'new-native-request', 'capability': 'chat'}]


def test_native_ledger_delta_detects_mutation_and_removal_by_request_capability():
    old = {'entries': [{'id': 'id-a', 'request_id': 'same-request', 'capability': 'chat', 'status': 'UNKNOWN'}]}
    changed = {'entries': [{'id': 'id-a', 'request_id': 'same-request', 'capability': 'chat', 'status': 'SETTLED'}]}
    removed = {'entries': []}
    assert len(unified.ledger_delta(old, changed)['changed']) == 1
    delta = unified.ledger_delta(old, removed)
    assert delta['removed_ids'] == [{'request_id': 'same-request', 'capability': 'chat'}]
