"""ECS40 adapter contracts: offline JSON/ASGI only; SQL and models are mocks."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import httpx
import pytest

from scripts import s8_6_2_r3_40_acceptance as adapter
from scripts import s8_6_1_api_acceptance as prior
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from test_s861_preflight import guarded_runtime

INPUT = Path(__file__).parent / 'fixtures/s862r3_40_input.json'


@pytest.fixture(autouse=True)
def forbid_providers(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('ECS40 offline test cannot send to Provider')
    for method in ('chat', 'chat_stream', 'embed', 'rerank', 'vision'):
        monkeypatch.setattr(ProviderModel, method, forbidden)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    return path


@pytest.fixture
def authorization(tmp_path):
    inputs = adapter.validate_inputs(adapter.read(INPUT))
    input_path = write(tmp_path / 'input.json', inputs)
    models = {name: name + '-model' for name in ('cheap', 'expensive', 'embedding', 'rerank')}
    allowed = [{'model_id': mid, 'provider': 'mock-offline', 'model': role,
        'capability': role if role in ('embedding', 'rerank') else 'chat'} for role, mid in models.items()]
    identity = {'source_revision': 'a' * 40,
        'image_id': 'sha256:' + 'b' * 64, 'profile_sha256': adapter.digest(adapter.profile_manifest()),
        'frozen_inputs_sha256': adapter.digest(input_path.read_bytes()), 'inventory_sha256': 'c' * 64,
        'model_ids': models}
    batches = {}
    for number, case in enumerate((r for r in inputs['cases'] if r['status'] == 'ELIGIBLE'), 1):
        identifier = case['case_id']
        request = 's840-' + f'{number:024x}'
        scope = adapter.case_scope(case)
        path = tmp_path / ('execution-' + str(number)) / 'execution.json'
        write(path, {'status': 'OPEN', 'entries': [], 'tenant_id': 332, 'subject_id': 'offline-subject',
            'capture_evidence': True, 'allowed_kbs': scope['knowledge_base_ids'], 'allowed_models': allowed,
            'allowed_request_prefixes': ['*'], 'max_calls': 12, 'max_tokens': 100000})
        batches[identifier] = {'case_id': identifier, 'request_id': request, 'scope': scope,
            'scope_sha256': adapter.digest(scope), 'question_sha256': case['question_sha256'],
            'execution_file': str(path), 'max_calls': 12, 'max_tokens': 100000,
            'model_ids': models, 'qu_model_id': models['cheap'], 'allowed_models': allowed,
            'qu_request_sha256s': {request: 'd' * 64}}
    inventory_path = write(tmp_path / 'inventory.json', {'models': models, 'source': 'offline mock only'})
    grant = {'schema': adapter.GRANT_SCHEMA, 'goal': adapter.GOAL, 'owner_approved': True,
        'authority_mode': adapter.OWNER_TOKEN_MODE, 'unlimited_provider_calls': True,
        'model_ids': models, 'owner_lock_file': str(tmp_path / 'owner-token.lock'),
        'owner_approval_evidence_sha256': 'e' * 64, 'profile_sha256': identity['profile_sha256'],
        'source_revision': identity['source_revision'], 'image_id': identity['image_id'],
        'frozen_inputs_sha256': identity['frozen_inputs_sha256'], 'inventory_sha256': adapter.digest(inventory_path.read_bytes()),
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'max_total_tokens': 500000, 'batches': batches}
    grant_path = write(tmp_path / 'grant.json', grant)
    chosen = next(iter(batches))
    env = {'S840_AUTHORIZATION_FILE': str(grant_path), 'S840_BATCH_ID': chosen,
        'S840_FROZEN_INPUT_FILE': str(input_path), 'S840_CURRENT_INVENTORY_FILE': str(inventory_path),
        'S840_SOURCE_REVISION': identity['source_revision'], 'S840_IMAGE_ID': identity['image_id'],
        'WEKNORA_PORT_EXECUTION_FILE': batches[chosen]['execution_file']}
    return env, grant, identity, inputs


def test_original_forty_exactly_preserves_two_unknown_and_has_no_gold():
    value = adapter.validate_inputs(adapter.read(INPUT))
    assert len(value['cases']) == 40
    assert sum(row['status'] == 'ELIGIBLE' for row in value['cases']) == 38
    assert {r['case_id'] for r in value['cases'] if r['status'] != 'ELIGIBLE'} == adapter.PROTECTED
    assert all(not {'answers', 'qrels', 'expected_documents'} & set(r) for r in value['cases'])


@pytest.mark.parametrize('mutation,reason', [('question', 'S840_ORIGINAL_INPUT_IDENTITY_CHANGED'),
    ('scope', 'S840_ORIGINAL_INPUT_IDENTITY_CHANGED'), ('gold', 'S840_CLOSED_NON_GOLD_INPUT_REQUIRED'),
    ('protected', 'S840_PROTECTED_CASE_STATUS_MISMATCH')])
def test_frozen_input_tampering_is_rejected(mutation, reason):
    value = adapter.read(INPUT)
    if mutation == 'question':
        value['cases'][0]['question'] += '?'
        value['cases'][0]['question_sha256'] = adapter.digest(value['cases'][0]['question'].encode())
    if mutation == 'scope': value['cases'][0]['knowledge_base_id'] = 'other-kb'
    if mutation == 'gold': value['cases'][0]['answers'] = ['forbidden']
    if mutation == 'protected': next(r for r in value['cases'] if r['case_id'] in adapter.PROTECTED)['status'] = 'ELIGIBLE'
    with pytest.raises(ValueError, match=reason): adapter.validate_inputs(value)


def test_new_owner_token_grant_profile_scope_and_ledger_all_bind(authorization):
    env, grant, _, _ = authorization
    loaded, case, budget = adapter.approved(env)
    assert loaded == grant and case['case_id'] == env['S840_BATCH_ID']
    assert budget is None and loaded['model_ids'] == loaded['batches'][case['case_id']]['model_ids']
    assert loaded['max_total_tokens'] == 500000 and loaded['unlimited_provider_calls'] is True
    assert not {'budget_preflight_sha256', 'max_total_calls'} & set(loaded)


@pytest.mark.parametrize('mutation,code', [('old_goal', 'NEW_OWNER_AUTHORIZATION'),
    ('old_profile', 'NEW_OWNER_AUTHORIZATION'), ('source', 'SOURCE_REVISION'), ('image', 'IMAGE_ID'),
    ('expired', 'AUTH_EXPIRED'), ('protected', 'PROTECTED_OR_UNKNOWN_CASE'),
    ('parent_calls', 'NEW_OWNER_AUTHORIZATION'), ('parent_tokens', 'NEW_OWNER_AUTHORIZATION'),
    ('calls', 'EXISTING_CHILD_LIMIT_REQUIRED'), ('tokens', 'EXISTING_CHILD_LIMIT_REQUIRED'),
    ('scope', 'CASE_SCOPE_MISMATCH'), ('qu', 'EXACT_QU_BODY_IDENTITY'), ('request', 'FRESH_CLIENT_ID'),
    ('duplicate_id', 'FRESH_CLIENT_ID'), ('ledger', 'SELECTED_LEDGER'),
    ('unknown_other', 'UNKNOWN_OR_UNSETTLED_STOP')])
def test_grant_rejections_preserve_no_send(authorization, mutation, code):
    env, grant, budget, _ = authorization
    selected = grant['batches'][env['S840_BATCH_ID']]
    if mutation == 'old_goal': grant['goal'] = 'S8.6.2-R'
    if mutation == 'old_profile': grant['profile_sha256'] = '0' * 64
    if mutation == 'source': env['S840_SOURCE_REVISION'] = 'f' * 40
    if mutation == 'image': env['S840_IMAGE_ID'] = 'sha256:' + 'f' * 64
    if mutation == 'expired': grant['expires_at'] = '2000-01-01T00:00:00+00:00'
    if mutation == 'protected': env['S840_BATCH_ID'] = 'scifact-339'
    if mutation == 'parent_calls': grant['unlimited_provider_calls'] = False
    if mutation == 'parent_tokens': grant['max_total_tokens'] = 1
    if mutation == 'calls': selected['max_calls'] = 13
    if mutation == 'tokens': selected['max_tokens'] = 100001
    if mutation == 'scope': selected['scope'] = {'knowledge_base_ids': ['other'], 'document_ids': []}
    if mutation == 'qu': selected['qu_request_sha256s'][selected['request_id']] = 'not-a-sha'
    if mutation == 'request': selected['request_id'] = 's861-old-request'
    if mutation == 'duplicate_id': list(grant['batches'].values())[1]['request_id'] = selected['request_id']
    if mutation == 'ledger':
        row = adapter.read(selected['execution_file']); row['entries'] = [{'status': 'SETTLED'}]
        write(Path(selected['execution_file']), row)
    if mutation == 'unknown_other':
        other = list(grant['batches'].values())[1]; row = adapter.read(other['execution_file']); row['status'] = 'STOP'
        write(Path(other['execution_file']), row)
    write(Path(env['S840_AUTHORIZATION_FILE']), grant)
    with pytest.raises(ValueError, match=code): adapter.approved(env)


def test_closed_scope_extension_keeps_legacy_default_guard():
    runtime, base, turn, batch, _ = guarded_runtime()
    turn.context = replace(turn.context, scope=TrustedScope(turn.context.scope.owner_id, ('different-kb',), ()))
    prior.install_profile(runtime, approved_batch=batch, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(ValueError, match='S861_APPROVED_SCOPE_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))


def test_case_admission_keeps_client_and_server_ids_distinct_and_whole_kb():
    runtime, base, turn, batch, _ = guarded_runtime()
    case = {'case_id': 'offline-case', 'question': turn.request.content, 'knowledge_base_id': 'other-kb'}
    turn.context = replace(turn.context, scope=TrustedScope(turn.context.scope.owner_id, ('other-kb',), ()))
    base.pipeline_state.targets = [SearchTarget('other-kb')]
    base.pipeline_state.bridge_cheap_model_id = 'approved-qu'
    base.pipeline_state.rerank_model_id = 'approved-rerank'
    batch.update(model_ids={'cheap': 'approved-qu', 'expensive': 'other', 'embedding': 'embedding', 'rerank': 'approved-rerank'},
        request_id=turn.client_request_id)
    adapter.install_case_profile(runtime, case, batch, datetime.now(timezone.utc) + timedelta(hours=1))
    result = asyncio.run(runtime.ordinary.configuration(turn))
    assert turn.request.request_id == turn.context.request_id == turn.run_id != turn.client_request_id
    assert result.pipeline_state.targets[0].knowledge_base_id == 'other-kb'
    assert result.pipeline_state.enable_expansion is False and result.generation_options['citations_enabled'] is True


@pytest.mark.parametrize('scope', [{'knowledge_base_ids': ['a', 'b'], 'document_ids': []},
    {'knowledge_base_ids': ['a'], 'document_ids': ['gold-doc']},
    {'knowledge_base_ids': 'a', 'document_ids': []}])
def test_scope_projection_cannot_expand_beyond_one_whole_kb(scope):
    runtime, _, _, _, _ = guarded_runtime()
    with pytest.raises(ValueError, match='CLOSED_CASE_SCOPE_REQUIRED'):
        prior.install_profile(runtime, approved_scope=scope)


def settled_row(model, request_id, tokens):
    return {'id': hashlib.sha256(json.dumps([request_id, model['capability']]).encode()).hexdigest(),
        'request_id': request_id, **model, 'wire_sha256': 'f' * 64,
        'reserved_tokens': max(1024, tokens), 'held_tokens': tokens, 'status': 'SETTLED',
        'outcome': 'RESPONSE_RECEIVED', 'usage': {'status': 'REPORTED', 'total_tokens': tokens,
            'prompt_tokens': tokens, 'completion_tokens': 0}}


def add_settled(batch, tokens, state='CLOSED'):
    path = Path(batch['execution_file']); ledger = adapter.read(path)
    ledger['entries'].append(settled_row(ledger['allowed_models'][0], 'old-settled-' + path.parent.name, tokens))
    ledger['status'] = state
    write(path, ledger)


def test_midrun_admission_accepts_closed_settled_previous_case_and_never_resets(authorization):
    env, grant, _, _ = authorization
    previous = list(grant['batches'].values())[1]
    add_settled(previous, 100)
    before = Path(previous['execution_file']).read_bytes()
    adapter.approved(env)
    assert Path(previous['execution_file']).read_bytes() == before
    assert adapter.owner_occupancy(grant, env['WEKNORA_PORT_EXECUTION_FILE']) == 100


def test_global_cap_counts_settled_actual_and_inflight_full_reservation(authorization):
    env, grant, _, _ = authorization
    previous = list(grant['batches'].values())[1]
    add_settled(previous, 90000)
    path = Path(env['WEKNORA_PORT_EXECUTION_FILE']); ledger = adapter.read(path)
    row = settled_row(ledger['allowed_models'][0], 'current-inflight', 5000)
    row.update(status='INFLIGHT', usage=None, outcome='UNKNOWN')
    ledger['entries'].append(row); write(path, ledger)
    assert adapter.owner_occupancy(grant, path) == 95000
    with pytest.raises(ValueError, match='UNSETTLED_STOP'): adapter.approved(env)


def test_owner_cap_rejects_unknown_other_and_does_not_release_it(authorization):
    env, grant, _, _ = authorization
    previous = list(grant['batches'].values())[1]
    path = Path(previous['execution_file']); ledger = adapter.read(path)
    row = settled_row(ledger['allowed_models'][0], 'old-unknown', 5000)
    row.update(status='UNKNOWN', usage=None, outcome='UNKNOWN')
    ledger['entries'].append(row); ledger['status'] = 'STOP'; write(path, ledger)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='UNKNOWN_OR_UNSETTLED_STOP'):
        adapter.owner_occupancy(grant, env['WEKNORA_PORT_EXECUTION_FILE'])
    assert path.read_bytes() == before


def test_owner_process_lock_prevents_overlapping_case_api_process(tmp_path):
    path = tmp_path / 'owner-token.lock'
    with adapter.owner_process_lock(path):
        with pytest.raises(ValueError, match='PARALLEL_API_PROCESS_DENIED'):
            with adapter.owner_process_lock(path):
                raise AssertionError('second API must not start')
    with adapter.owner_process_lock(path): pass


@pytest.mark.parametrize('wire,allowed', [(7900, True), (7901, False)])
def test_actual_gate_global_cap_checked_before_unchanged_child_reserve(authorization, wire, allowed):
    env, grant, _, _ = authorization
    for batch in list(grant['batches'].values())[1:6]: add_settled(batch, 98000)
    path = Path(env['WEKNORA_PORT_EXECUTION_FILE'])
    execution = adapter.execution_module.LocalExecution(path)
    principal = SimpleNamespace(tenant_id=332, subject_id='offline-subject')
    admissions = []
    async def authorize(call): admissions.append(call.context.request_id)
    async def finish(call, *, outcome, usage): return True
    gate = SimpleNamespace(application=SimpleNamespace(principal=principal), authorize=authorize, finish=finish)
    async def make(config, key, current_gate):
        execution.install_gate(current_gate)
        return SimpleNamespace(transport=SimpleNamespace(gate=current_gate))
    runtime = SimpleNamespace(_make_model=make, execution=execution)
    adapter.install_owner_cap(runtime, grant, path)
    model = grant['batches'][env['S840_BATCH_ID']]['allowed_models'][0]
    call = SimpleNamespace(context=CallContext('fresh-provider-run', TrustedScope('offline-subject',
        tuple(adapter.read(path)['allowed_kbs']), ())), **model, wire_bytes=wire, output_budget=0,
        wire_sha256='a' * 64)
    async def exercise():
        await runtime._make_model(None, None, gate)
        token = adapter.execution_module._OUTPUT_BOUND.set(1076)
        try: await gate.authorize(call)
        finally: adapter.execution_module._OUTPUT_BOUND.reset(token)
    if allowed:
        asyncio.run(exercise())
        #490000 settled +7900 wire +1076 output +1024 overhead =500000 exactly.
        row = adapter.read(path)['entries'][0]
        assert row['held_tokens'] == row['reserved_tokens'] == 10000
        assert admissions == ['fresh-provider-run']
    else:
        with pytest.raises(Exception, match='MODEL_SEND_AUTHORIZATION_FAILED'): asyncio.run(exercise())
        assert not admissions and adapter.read(path)['entries'] == []
        stop_file = Path(grant['owner_lock_file']).with_name('owner-stop.json')
        assert adapter.read(stop_file)['code'] == 'S840_OWNER_TOKEN_CAP_EXCEEDED'
        with pytest.raises(ValueError, match='S840_OWNER_SCOPE_STOPPED'): adapter.approved(env)


def fake_asgi(authorization, terminal='stop'):
    env, grant, _, inputs = authorization
    chosen = env['S840_BATCH_ID']; batch = grant['batches'][chosen]
    case = next(r for r in inputs['cases'] if r['case_id'] == chosen)
    app, calls = FastAPI(), []
    sid, mid, run_id = 'mock-session', 'mock-assistant', '00000000-0000-4000-8000-000000000001'
    refs = [{'id': 'mock-chunk', 'knowledge_base_id': case['knowledge_base_id']}]
    frames = [
        {'id': run_id, 'response_type': 'agent_query', 'data': {'request_id': run_id,
            'query': case['question'], 'session_id': sid, 'assistant_message_id': mid}},
        {'id': run_id, 'response_type': 'answer', 'content': 'Mock answer only.'},
        {'id': run_id, 'response_type': 'references', 'knowledge_references': refs},
        {'id': run_id, 'response_type': 'complete', 'data': {'status': 'completed',
            'finish_reason': terminal, 'truncated': terminal == 'length'}}]
    sse = ''.join('event: message\ndata: ' + json.dumps(f) + '\n\n' for f in frames)
    @app.post('/api/v1/sessions')
    async def session(request: Request):
        value = await request.json(); calls.append(('session', value))
        from backend.weknora_port.http_api import SessionCreate
        SessionCreate(**value)
        return {'data': {'id': sid}}
    @app.post('/api/v1/sessions/{session_id}/messages')
    async def message(session_id: str, request: Request):
        value = await request.json(); calls.append(('message', value))
        ledger = adapter.read(batch['execution_file'])
        ledger['entries'] = [{'status': 'SETTLED', 'held_tokens': 10, 'usage': {'total_tokens': 10}}]
        write(Path(batch['execution_file']), ledger)
        return StreamingResponse(iter([sse]), media_type='text/event-stream')
    @app.get('/api/v1/sessions/{session_id}/messages')
    async def history(session_id: str):
        return {'data': [{'role': 'user', 'content': case['question']}, {'id': mid, 'role': 'assistant',
            'request_id': run_id, 'content': 'Mock answer only.', 'is_completed': True,
            'turn_status': 'completed', 'knowledge_references': refs}]}
    @app.get('/api/v1/sessions/continue-stream/{session_id}')
    async def replay(session_id: str): return StreamingResponse(iter([sse]), media_type='text/event-stream')
    @app.get('/api/v1/chunks/{chunk_id}')
    async def chunk(chunk_id: str): return {'data': {'id': chunk_id, 'knowledge_base_id': case['knowledge_base_id']}}
    return app, calls


@pytest.mark.parametrize('finish,success', [('stop', True), ('length', False)])
def test_collector_asgi_mock_saves_one_post_replay_and_rejects_incomplete(authorization, monkeypatch, tmp_path, finish, success):
    from scripts import s8_ecs_runtime
    monkeypatch.setattr(s8_ecs_runtime, 'api_token', lambda env: 'offline-token')
    app, calls = fake_asgi(authorization, finish)
    env, _, _, _ = authorization
    target = tmp_path / 'collector'
    async def collect(): return await adapter.collect_http('http://127.0.0.1:8000', target,
        env, transport=httpx.ASGITransport(app=app))
    if success:
        result = asyncio.run(collect())
        assert result['status'] == 'HTTP_COMPLETED_REVIEW_REQUIRED'
        assert result['final_answer_correct'] == 'NOT_REVIEWED'
        assert result['server_run_id'] != result['client_request_id']
    else:
        with pytest.raises(ValueError, match='INCOMPLETE_OUTPUT_STOP'): asyncio.run(collect())
        assert adapter.read(target / 'results.json')['status'] == 'STOP_NO_RETRY'
    assert [name for name, _ in calls] == ['session', 'message']
    session = calls[0][1]; body = calls[1][1]
    assert session['source_evidence_mode'] == 'bounded_source_v1' and session['retrieval_strategy_mode'] == 'query_structure_v1'
    assert session['document_scope'] == body['document_scope'] == []
    assert not {'answers', 'qrels', 'query_type', 'tenant_id'} & set(body)
    assert (target / 'submit-claim.json').is_file() and (target / 'response.sse').is_file()
    with pytest.raises((FileExistsError, ValueError)): asyncio.run(collect())
    assert len(calls) == 2


def test_actual_fastapi_session_saves_profile_and_uses_trusted_client_admission(monkeypatch):
    from test_s86_qu_configuration import assembled_runtime
    from backend.weknora_port.http_api import create_app
    from backend.weknora_port.runtime import ConversationSettings
    runtime, stored, calls = assembled_runtime(monkeypatch, ConversationSettings(query_understand_model_id='cheap-id'))
    runtime.ordinary = SimpleNamespace(configuration=runtime._configuration)
    question = 'What is stored in this KB?'
    case = {'case_id': 'offline-case', 'knowledge_base_id': 'kb', 'question': question}
    client_id, run_id = 's840-' + '1' * 24, '00000000-0000-4000-8000-000000000002'
    batch = {'request_id': client_id, 'qu_model_id': 'cheap-id',
        'model_ids': {'cheap': 'cheap-id', 'expensive': 'expensive-id', 'rerank': 'rerank-id', 'embedding': 'embedding-id'}}
    adapter.install_case_profile(runtime, case, batch, datetime.now(timezone.utc) + timedelta(hours=1))
    app = create_app(runtime, resolve_principal=lambda request: runtime.principal, manage_background_workers=False)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Origin': 'http://test'}) as client:
            response = await client.post('/api/v1/sessions', json={'knowledge_base_scope': ['kb'], 'document_scope': [],
                'source_evidence_mode': 'bounded_source_v1', 'retrieval_strategy_mode': 'query_structure_v1'})
        assert response.status_code == 200
        turn = SimpleNamespace(session_id=stored['id'], run_id=run_id, client_request_id=client_id,
            context=CallContext(run_id, TrustedScope('synthetic-subject', ('kb',), ())),
            request=SimpleNamespace(request_id=run_id, content=question, summary_model_id='', reasoning_effort='off',
                accept_language='en-US', knowledge_base_scope=('kb',)))
        return await runtime.ordinary.configuration(turn)
    result = asyncio.run(exercise())
    assert stored['context_config']['conversation'] == {'source_evidence_mode': 'bounded_source_v1', 'retrieval_strategy_mode': 'query_structure_v1'}
    assert result.generation_options['citations_enabled'] is True
    assert result.pipeline_state.enable_expansion is False
    assert result.generation_options['policy'].mode == 'DYNAMIC'
