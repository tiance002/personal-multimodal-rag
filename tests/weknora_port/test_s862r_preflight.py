"""Current bounded acceptance: real local renderers, no network/Provider sends."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import s8_6_1_api_acceptance as profile
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.request import build_embedding_body
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.service import TurnConfiguration, AcceptedTurn, OrdinaryTurnRequest
from backend.weknora_port.conversation.types import MemoryContext
from test_s86_pipeline_integration import Repo, Registry, Models, state
from test_s861_preflight import INVENTORY, ORIGINALS, authorization_files, write_json, guarded_runtime


@pytest.fixture(autouse=True)
def zero_provider(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('S8.6.2-R offline preflight cannot send')
    for method in ('chat', 'chat_stream', 'embed', 'rerank', 'vision'):
        monkeypatch.setattr(ProviderModel, method, forbidden)


@pytest.fixture(scope='module')
def bounded_report():
    return asyncio.run(profile.dry_run_inventory(INVENTORY, ORIGINALS, bounded_query_profile=True))


@pytest.mark.parametrize('text,valid', [('a' * 256, True), ('界' * 256, True),
    ('😀' * 256, True), ('a' * 257, False), ('😀' * 257, False), ('', False),
    ('\"\\\n\x1c' * 64, True), ('\ud800', False)])
def test_input_boundary_unicode_and_json_escaping(text, valid):
    result = profile.query_input_bound(text)
    assert result['valid'] is valid
    if valid:
        assert result['unicode_characters'] <= 256 and result['utf8_bytes'] <= 1024


def test_utf8_guard_is_independent_of_character_guard(monkeypatch):
    monkeypatch.setitem(profile.PROFILE_QUERY_BOUND, 'unicode_characters', 512)
    assert profile.query_input_bound('界' * 342)['valid'] is False


def test_bounded_profile_accepts_ordinary_expensive_baseline_with_explicit_cheap_qu():
    runtime, base, turn, approved, _ = guarded_runtime()
    approved['model_ids'] = {'cheap': 'approved-qu', 'expensive': 'ordinary-expensive', 'rerank': 'approved-rerank'}
    base.pipeline_state.chat_model_id = 'ordinary-expensive'
    base.pipeline_state.bridge_cheap_model_id = 'approved-qu'
    base.pipeline_state.rerank_model_id = 'approved-rerank'
    profile.install_profile(runtime, approved_batch=approved,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), bounded_query_profile=True)
    current = asyncio.run(runtime.ordinary.configuration(turn))
    assert current.pipeline_state.chat_model_id == 'ordinary-expensive'
    assert current.pipeline_state.query_understand_model_id == current.pipeline_state.bridge_cheap_model_id == 'approved-qu'


def accepted_client_fixture():
    runtime, base, old, approved, resolutions = guarded_runtime()
    client_id = approved['turns'][0]['request_id']
    request = OrdinaryTurnRequest('offline-session', old.request.content, old.run_id,
        knowledge_base_scope=profile.KB_IDS, document_scope=profile.DOCUMENT_IDS, reasoning_effort='off',
        accept_language='en-US')
    turn = AcceptedTurn(old.run_id, request.session_id, 'user-message', 'assistant-message',
        replace(old.context, session_id=request.session_id), MemoryContext(332, old.context.scope.owner_id),
        request, is_new=True, replay_only=False, client_request_id=client_id)
    return runtime, base, turn, approved, resolutions


def test_accepted_client_identity_authorizes_original_client_and_preserves_server_run():
    runtime, base, turn, approved, resolutions = accepted_client_fixture()
    context, request = turn.context, turn.request
    profile.install_profile(runtime, approved_batch=approved, expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    result = asyncio.run(runtime.ordinary.configuration(turn))
    assert resolutions == [turn] and result.pipeline_state.query == turn.request.content
    assert turn.context is context and turn.request is request
    assert turn.client_request_id == approved['turns'][0]['request_id']
    assert turn.request.request_id == turn.context.request_id == turn.run_id != turn.client_request_id


@pytest.mark.parametrize('client', ['', None, 's861-unknown-client'])
def test_accepted_client_identity_missing_unknown_denied_without_nested_request_fallback(client):
    runtime, base, turn, approved, resolutions = accepted_client_fixture()
    # An adversarial/incomplete AcceptedTurn cannot gain admission from a
    # nested request_id that happens to equal an approved HTTP client ID.
    turn = replace(turn, client_request_id=client,
        request=replace(turn.request, request_id=approved['turns'][0]['request_id']))
    profile.install_profile(runtime, approved_batch=approved, expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    with pytest.raises(ValueError, match='S861_APPROVED_TURN_REQUIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))
    assert resolutions == []


def test_accepted_client_identity_cannot_rebind_to_second_server_run():
    runtime, base, turn, approved, resolutions = accepted_client_fixture()
    profile.install_profile(runtime, approved_batch=approved, expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    asyncio.run(runtime.ordinary.configuration(turn))
    other_id = '11111111-1111-4111-8111-111111111111'
    other = replace(turn, run_id=other_id, request=replace(turn.request, request_id=other_id),
        context=replace(turn.context, request_id=other_id))
    with pytest.raises(ValueError, match='S861_CLIENT_REQUEST_ALREADY_BOUND'):
        asyncio.run(runtime.ordinary.configuration(other))
    assert resolutions == [turn]


def test_accepted_client_identity_qu_body_lookup_uses_client_but_model_context_uses_server_run(bounded_report, tmp_path):
    from backend.weknora_port.retrieval.query import understand_query
    runtime, base, turn, approved, _ = accepted_client_fixture()
    approved.update(model_ids=bounded_report['model_ids'], qu_model_id=bounded_report['model_ids']['cheap'],
        qu_request_sha256s={turn.client_request_id: bounded_report['records']['compare:query_structure_v1:QU']['body_sha256']})
    base.pipeline_state.chat_model_id = bounded_report['model_ids']['expensive']
    base.pipeline_state.query_understand_model_id = base.pipeline_state.bridge_cheap_model_id = approved['qu_model_id']
    base.pipeline_state.rerank_model_id = bounded_report['model_ids']['rerank']
    seen = []
    class Model:
        config = real_config('cheap')
        def build_request_body(self, messages, options):
            return ProviderModel(self.config, None).build_request_body(messages, options)
        async def chat(self, messages, options, context):
            seen.append(context.request_id)
            return SimpleNamespace(content=json.dumps({'rewrite_query': turn.request.content,
                'query_type':'SIMPLE_COMPARE','intent':'kb_search','image_description':''}), finish_reason='stop')
    async def factory(active_turn, progress):
        return RetrievalPipeline(Repo(), Registry(Model()), on_progress=progress)
    runtime.ordinary.pipeline_factory = factory
    profile.install_profile(runtime, tmp_path, approved_batch=approved,
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1), bounded_query_profile=True)
    async def exercise():
        cfg = await runtime.ordinary.configuration(turn)
        pipeline = await runtime.ordinary.pipeline_factory(turn, None)
        await understand_query(cfg.pipeline_state, turn.context, pipeline.registry)
        await pipeline._progress('understand', 'completed')
        return cfg.pipeline_state
    current = asyncio.run(exercise())
    assert seen == [turn.run_id + '/query_rewrite']
    assert current.rewrite_query == turn.request.content and current.query_type == 'SIMPLE_COMPARE'
    records = [json.loads(path.read_text(encoding='utf8')) for path in tmp_path.glob('*.profile.json')]
    wire = next(row for row in records if row['stage']=='query_understanding_wire')
    assert wire['fields']['body_sha256'] == approved['qu_request_sha256s'][turn.client_request_id]
    assert runtime._s862r_raw_qu_outputs[turn.run_id]['request_id'] == seen[0]


@pytest.mark.parametrize('overflow,finish', [(True, 'stop'), (False, 'length'), (False, '')])
def test_actual_pipeline_completed_guard_propagates_and_preserves_raw_before_downstream(tmp_path, overflow, finish):
    q0 = 'Compare Alpha and Beta in terms of uses.'
    rewritten = q0.replace(' ', ' ' * 40) if overflow else q0
    class Query(Models):
        async def chat(self, messages, options, context):
            self.qu.append(context.request_id)
            return SimpleNamespace(content=json.dumps({'rewrite_query': rewritten, 'intent': 'kb_search',
                'image_description': '', 'query_type': 'SIMPLE_COMPARE'}), finish_reason=finish)
    current, repo, model, embeddings, generation = state(query=q0), Repo(), Query(), [], []
    current.language = profile.expected_profile_language(q0)[1]
    async def configuration(turn):
        return TurnConfiguration(current, {})
    async def pipeline_factory(turn, progress):
        async def embed(*args):
            embeddings.append(args); return [1.]
        return RetrievalPipeline(repo, Registry(model), on_progress=progress, embedding_resolver=embed)
    async def generation_factory(turn, progress):
        generation.append(turn); return object()
    runtime = SimpleNamespace(ordinary=SimpleNamespace(configuration=configuration,
        pipeline_factory=pipeline_factory, generation_factory=generation_factory))
    turn = SimpleNamespace(run_id='offline-run',
        request=SimpleNamespace(content=q0, accept_language='en-US'))
    profile.install_profile(runtime, tmp_path, bounded_query_profile=True)
    expected = 'PROFILE_INPUT_BOUND_EXCEEDED' if overflow else 'S862R_INCOMPLETE_QU_OUTPUT_STOP'
    active = []
    async def exercise():
        cfg = await runtime.ordinary.configuration(turn)
        active.append(cfg.pipeline_state)
        pipeline = await runtime.ordinary.pipeline_factory(turn, None)
        with pytest.raises(ValueError, match=expected):
            await pipeline.run(cfg.pipeline_state, CallContext(turn.run_id, TrustedScope('owner', ('kb',))))
    asyncio.run(exercise())
    assert len(model.qu) == 1 and not embeddings and not repo.calls and not model.ranks and not generation
    saved = [json.loads(path.read_text(encoding='utf8')) for path in tmp_path.glob('*.profile.json')]
    raw = next(row['fields'] for row in saved if row['stage'] == 'query_understanding_raw')
    assert json.loads(raw['content'])['rewrite_query'] == rewritten and raw['finish_reason'] == finish
    assert any(row['status'] == expected for row in saved)
    assert current.query == q0
    if overflow:
        assert active[0].rewrite_query == rewritten and len(rewritten) > 256


@pytest.mark.parametrize('question', ['a' * 256, '界' * 256])
def test_actual_pipeline_accepts_legal_boundary_and_keeps_one_plain_retrieval(tmp_path, question):
    class Query(Models):
        async def chat(self, messages, options, context):
            self.qu.append(context.request_id)
            return SimpleNamespace(content=json.dumps({'rewrite_query': question, 'intent': 'kb_search',
                'image_description': '', 'query_type': 'UNKNOWN'}), finish_reason='stop')
    locale, language = profile.expected_profile_language(question)
    current, repo, model = state(query=question), Repo(), Query()
    current.language = language
    async def configuration(turn): return TurnConfiguration(current, {})
    async def factory(turn, progress): return RetrievalPipeline(repo, Registry(model), on_progress=progress)
    runtime = SimpleNamespace(ordinary=SimpleNamespace(configuration=configuration, pipeline_factory=factory))
    profile.install_profile(runtime, tmp_path, bounded_query_profile=True)
    async def exercise():
        turn = SimpleNamespace(run_id='boundary',
            request=SimpleNamespace(content=question, accept_language=locale))
        cfg = await runtime.ordinary.configuration(turn)
        pipeline = await runtime.ordinary.pipeline_factory(turn, None)
        await pipeline.run(cfg.pipeline_state, CallContext(turn.run_id, TrustedScope('owner', ('kb',))))
        return cfg.pipeline_state
    result = asyncio.run(exercise())
    assert len(model.qu) == len(model.ranks) == len(repo.calls) == 1
    assert result.strategy_plan.strategy == 'PLAIN' and result.query == result.rewrite_query == question


def test_current_request_tree_bound_and_ledger_caps(bounded_report):
    report = bounded_report
    assert report['schema'] == profile.BOUNDED_BUDGET_SCHEMA and report['status'] == 'DRY_RUN_COMPLETE'
    assert report['requested_full_calls'] == 9
    assert set(report['requested_batches']) == {'compare'}
    assert set(report['candidate_scopes_not_authorized']) == {'qu-only', 'parallel', 'dependent'}
    assert report['candidate_scopes_not_authorized']['qu-only']['max_calls'] == 6
    for case in profile.BOUNDED_QUESTIONS:
        row = report['submission_bounds'][case + ':query_structure_v1']
        assert row['max_calls'] == 9
        assert row['request_paths']['simple-family-max3']['max_calls'] == 9
        assert row['request_paths']['dependent-max1-new-search']['max_calls'] == 9
        assert all(row['reservation_tokens_upper'] >= branch['reservation_tokens_upper']
                   for branch in row['request_paths'].values())
    assert all(row['fits_existing_ledger'] and row['max_calls'] <= 12
        and row['reservation_tokens_upper'] <= 100000 for row in report['requested_batches'].values())
    assert report['recommended_reservation_upper'] <= 500000
    assert report['new_actual_provider_calls'] == report['actual_tokens'] == 0
    assert report['derived_query_bounds']['child_characters'] == 288


def test_actual_wire_bytes_contract_previews_and_fixed_inputs(bounded_report):
    records = bounded_report['records']
    for row in records.values():
        assert profile.digest(row['request_body']) == row['body_sha256'] == row['wire_sha256']
        assert len(profile.canonical(row['request_body'])) == row['wire_bytes']
        assert row['wire_bytes_upper'] == row['wire_bytes'] + row['extra_wire_bytes_upper']
        assert row['reserved_tokens_upper'] == row['wire_bytes_upper'] + row['output_upper'] + 1024
        assert row['sent'] is False
    for case, question in profile.QU_ONLY_QUESTIONS.items():
        record = records['qu-only:' + case + ':query_structure_v1:QU']
        body = record['request_body']
        assert question in body['messages'][-1]['content']
        assert 'COMPARE_SHARED/v2' in body['messages'][0]['content']
        assert 'PARALLEL_ATTRIBUTE/v2' in body['messages'][0]['content']
        assert body['enable_thinking'] is False and body['max_tokens'] == 256
    extract = records['extract']['request_body']
    nested, end = json.JSONDecoder().raw_decode(extract['messages'][-1]['content'])
    assert extract['messages'][-1]['content'][end:] == '\nUse this JSON schema: json'
    assert nested['question'] == '\x1c' * profile.PROFILE_QUERY_BOUND['unicode_characters']
    assert nested['sentences'] and all(row['sentence_id'].startswith('s-') for row in nested['sentences'])


def real_config(role):
    from backend.weknora_port.model_registry import SourceCatalogue
    from backend.weknora_port.storage.configuration import ConfiguredRole
    row = json.loads(INVENTORY.read_text(encoding='utf8'))['models'][role]
    return SourceCatalogue().resolve(ConfiguredRole(role, row['provider'], row['model'],
        row['reconstruction']['model_config']['base_url'], '', {}, row['source']), row['model_id'])


@pytest.mark.parametrize('query', ['界' * 256, '😀' * 256, '\"\\\n\x1c' * 64, '<|' * 128])
def test_actual_embedding_renderer_stays_below_same_contract_envelope(bounded_report, query):
    assert profile.query_input_bound(query)['valid']
    body = build_embedding_body(real_config('embedding'), (query,), 'query')
    assert len(profile.canonical(body)) <= bounded_report['records']['embedding:initial']['wire_bytes_upper']


@pytest.mark.parametrize('finish', ['stop', 'length'])
def test_six_qu_runner_uses_normal_function_one_call_each_and_stops_incomplete(tmp_path, monkeypatch, bounded_report, finish):
    cheap_config = real_config('cheap')
    ledger_path = tmp_path / 'mock-only-ledger.json'
    ledger = {'status': 'OPEN', 'entries': [], 'max_calls': 6, 'max_tokens': 50000}
    write_json(ledger_path, ledger)
    turns = [{'case': case, 'question': question, 'mode': 'query_structure_v1',
        'request_id': 's861-offline-qu-' + str(index)}
        for index, (case, question) in enumerate(profile.QU_ONLY_QUESTIONS.items())]
    batch = {'kind': 'qu_only', 'turns': turns, 'execution_file': str(ledger_path),
        'model_ids': bounded_report['model_ids'], 'qu_request_sha256s': {turn['request_id']:
            bounded_report['records']['qu-only:' + turn['case'] + ':query_structure_v1:QU']['body_sha256']
            for turn in turns}}
    owner = {'batches': {'qu': batch}, 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'source_revision': 'a' * 40, 'image_id': 'MOCK_NOT_AN_IMAGE', 'profile_sha256': bounded_report['profile_sha256']}
    owner_path = tmp_path / 'mock-only-owner.json'; write_json(owner_path, owner)
    env = {'S861_BOUNDED_QUERY_PROFILE': 'true', 'WEKNORA_PORT_CLOUD_ALLOWED': 'true',
        'S861_AUTHORIZATION_FILE': str(owner_path), 'S861_BATCH_ID': 'qu'}
    monkeypatch.setattr(profile, '_approved', lambda environment: owner)  # admission is tested independently above
    calls, sessions, closed = [], [], []
    class QueryModel:
        config = cheap_config
        def build_request_body(self, messages, options):
            return ProviderModel(self.config, None).build_request_body(messages, options)
        async def chat(self, messages, options, context):
            spec = turns[len(calls)]
            calls.append((messages, options, context))
            ledger['entries'].append({'status': 'SETTLED', 'held_tokens': 0,
                'usage': {'total_tokens': 17}, 'request_id': context.request_id, 'simulated': True})
            write_json(ledger_path, ledger)
            return SimpleNamespace(content=json.dumps({'rewrite_query': spec['question'],
                'query_type': 'UNKNOWN', 'intent': 'kb_search', 'image_description': ''}), finish_reason=finish)
    class Models:
        async def chat(self, model_id, context):
            assert model_id == bounded_report['model_ids']['cheap']; return QueryModel()
        async def close(self): closed.append(True)
    class Runtime:
        cloud_allowed, graph_enabled = True, False
        principal = SimpleNamespace(subject_id='offline-owner')
        def registry_for_context(self): return Models()
        async def create_session(self, principal, request):
            assert request.retrieval_strategy_mode == 'query_structure_v1'
            assert request.source_evidence_mode == 'bounded_source_v1'
            result = {'id': 'mock-session-' + str(len(sessions))}
            sessions.append(result); return result
    @asynccontextmanager
    async def factory(settings): yield Runtime()
    settings_factory = lambda environment: (object(), factory, {})
    target = tmp_path / 'fresh-output'
    if finish == 'length':
        with pytest.raises(ValueError, match='S862R_INCOMPLETE_QU_OUTPUT_STOP'):
            asyncio.run(profile.collect_qu_only(target, env, settings_factory=settings_factory))
        assert len(calls) == len(sessions) == len(closed) == 1
        assert json.loads((target / 'failure.json').read_text(encoding='utf8'))['ledger']['reported_tokens'] == 17
    else:
        result = asyncio.run(profile.collect_qu_only(target, env, settings_factory=settings_factory))
        assert len(calls) == len(sessions) == len(closed) == len(result) == 6
        assert all(row['actual_query_type'] == 'UNKNOWN' and row['plan']['strategy'] == 'PLAIN' for row in result)
        assert all(row['retrieval_calls'] == row['final_generation_calls'] == 0 for row in result)
        assert all(context.session_id == sessions[index]['id'] for index, (_, _, context) in enumerate(calls))
    assert all(options.thinking is False and options.max_completion_tokens == 256 for _, options, _ in calls)


def bounded_authorization(tmp_path, monkeypatch, report):
    owner, ledger, environment, auth_path, ledger_path = authorization_files(tmp_path, monkeypatch)
    batch = owner['batches']['candidate-only']
    batch.update(kind='http', model_ids=report['model_ids'], max_calls=9,
        qu_request_sha256s={batch['turns'][0]['request_id']: report['records']['compare:query_structure_v1:QU']['body_sha256']})
    ledger['max_calls'] = 9
    owner.update(goal=profile.BOUNDED_GOAL, max_total_tokens=500000,
        profile_sha256=profile.digest(profile.profile_manifest(bounded_query_profile=True)))
    budget = dict(report)
    budget['implementation_sha256_LF'] = profile.digest(Path(profile.__file__).read_bytes().replace(b'\r\n', b'\n'))
    path = tmp_path / 'bounded-current-budget.json'; write_json(path, budget)
    owner.update(budget_preflight_file=str(path), budget_preflight_sha256=profile.digest(path.read_bytes()))
    environment['S861_BOUNDED_QUERY_PROFILE'] = 'true'
    write_json(auth_path, owner); write_json(ledger_path, ledger)
    return owner, ledger, environment, auth_path, ledger_path


@pytest.mark.parametrize('mutation,code', [('', ''), ('old-goal', 'S861_FRESH_OWNER_AUTHORIZATION_REQUIRED'),
    ('old-r-goal', 'S861_FRESH_OWNER_AUTHORIZATION_REQUIRED'),
    ('wire', 'S862R_EXACT_QU_PREFLIGHT_REQUIRED'), ('aggregate', 'S862R_AGGREGATE_RESERVATION_LIMIT')])
def test_new_goal_admission_binds_current_request_and_preserves_ledger(tmp_path, monkeypatch, bounded_report, mutation, code):
    owner, ledger, environment, auth_path, ledger_path = bounded_authorization(tmp_path, monkeypatch, bounded_report)
    if mutation == 'old-goal': owner['goal'] = 'S8.6.1'
    elif mutation == 'old-r-goal': owner['goal'] = 'S8.6.2-R'
    elif mutation == 'wire': owner['batches']['candidate-only']['qu_request_sha256s'] = {}
    elif mutation == 'aggregate': owner['max_total_tokens'] = 1
    write_json(auth_path, owner)
    before = ledger_path.read_bytes()
    if mutation:
        with pytest.raises(ValueError, match=code): profile._approved(environment)
    else:
        assert profile._approved(environment) == owner
    assert ledger_path.read_bytes() == before
