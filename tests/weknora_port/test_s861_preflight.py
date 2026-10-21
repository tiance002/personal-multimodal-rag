"""S8.6.1 preflight bounds and admission tests: no Provider, DB or network."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from scripts import s8_6_1_api_acceptance as profile
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.types import CallContext, ChatOptions, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.service import TurnConfiguration


FIXTURES = Path(__file__).parent / 'fixtures'
INVENTORY = FIXTURES / 's861_api_inventory.json'
ORIGINALS = FIXTURES / 's8_4_bridge' / 'inputs.json'


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('preflight must not call any Provider')
    for name in ('chat', 'chat_stream', 'embed', 'rerank', 'vision'):
        monkeypatch.setattr(ProviderModel, name, forbidden)


@pytest.fixture(scope='module')
def frozen_dryrun():
    # The method performs actual local body construction; no mocked bound data.
    with pytest.MonkeyPatch.context() as patch:
        async def forbidden(*args, **kwargs):
            raise AssertionError('dry run unexpectedly called a Provider')
        for name in ('chat', 'chat_stream', 'embed', 'rerank', 'vision'):
            patch.setattr(ProviderModel, name, forbidden)
        return asyncio.run(profile.dry_run_inventory(INVENTORY, ORIGINALS))


def test_actual_frozen_metadata_dryrun_counts_and_proof_boundary(frozen_dryrun):
    report = frozen_dryrun
    assert {key: value['max_calls'] for key, value in report['submission_bounds'].items()} == {
        'compare:off': 5, 'compare:query_structure_v1': 7,
        'dependent:off': 5, 'dependent:query_structure_v1': 9}
    assert report['requested_full_calls'] == 26
    assert report['originals_hash_matched_to_current_inventory'] == 7
    assert report['all_searchable_rows_matched'] == 11
    assert report['inventory_sha256'] == profile.digest(INVENTORY.read_bytes())
    assert report['frozen_source_sha256'] == profile.digest(ORIGINALS.read_bytes())
    assert report['physical_embedding_groups'] == report['rerank_batches_per_nonempty_pool'] == 1
    assert report['memory_calls'] == report['automatic_threshold_retry_calls'] == 0
    assert report['query_expansion'] is False
    assert report['new_actual_provider_calls'] == report['actual_tokens'] == 0
    assert report['authorization'] == 'NONE'
    assert report['classification_quality'].startswith('NOT_RUN')


def test_physical_wire_reservations_are_formula_bounds_not_actual_usage(frozen_dryrun):
    report = frozen_dryrun
    records = report['records']
    for record in records.values():
        assert record['sent'] is False
        assert record['reserved_tokens_upper'] == record['wire_bytes_upper'] + record['output_upper'] + 1024
        assert record['output_upper'] == record['contract']['output_upper']
        assert len(record['body_sha256']) == 64
    for case in profile.QUESTIONS:
        assert records[case + ':off:QU']['output_upper'] == 150
        assert records[case + ':query_structure_v1:QU']['output_upper'] == 256
        for role in ('cheap', 'expensive'):
            assert records[case + ':final:' + role]['output_upper'] == 2048
    assert records['extract']['output_upper'] == 256
    for submission in report['submission_bounds'].values():
        names = submission['physical_call_records']
        assert submission['max_calls'] == len(names)
        assert submission['reservation_tokens_upper'] == sum(records[name]['reserved_tokens_upper'] for name in names)
        assert submission['actual_usage'] == 'NOT_RUN'
        assert submission['final_generation_reserve_included'] is True


def test_even_single_candidates_are_blocked_after_normal_fallback_is_budgeted(frozen_dryrun):
    report = frozen_dryrun
    assert report['fallback_included'] is True
    assert report['status'] == 'BLOCKED_RESERVATION_BUDGET'
    assert report['requested_batches']['dependent']['max_calls'] == 14
    assert all(not row['fits_existing_ledger'] for row in report['requested_batches'].values())
    assert report['recommended_batches_not_authorized'] == {}
    assert report['recommended_max_calls'] == report['recommended_reservation_upper'] == 0
    candidates = report['candidate_scopes_not_authorized']
    assert [candidates[case]['max_calls'] for case in profile.QUESTIONS] == [7, 9]
    assert all(not row['fits_existing_ledger'] and row['reservation_tokens_upper'] > 100000
               for row in candidates.values())
    for case in profile.QUESTIONS:
        for mode in profile.MODES:
            names = report['submission_bounds'][case + ':' + mode]['physical_call_records']
            for role in ('cheap', 'expensive'):
                name = next(name for name in names if name.endswith(':' + role))
                assert report['records'][name]['reserved_tokens_upper'] >= report['records'][case + ':fallback:' + role]['reserved_tokens_upper']


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    return path


def test_actual_profile_manifest_has_stable_json_roundtrip_digest():
    manifest = profile.profile_manifest()
    encoded = profile.canonical(manifest)
    assert len(profile.digest(manifest)) == 64
    assert profile.digest(manifest) == profile.digest(json.loads(encoded))
    assert profile.digest(profile.profile_manifest()) == profile.digest(manifest)


@pytest.mark.parametrize('mutation,code', [
    ('hash', 'S861_CURRENT_ORIGINAL_READBACK_HASH_MISMATCH'),
    ('source_version', 'S861_CURRENT_ORIGINAL_READBACK_HASH_MISMATCH'),
    ('document_version', 'S861_CURRENT_DOCUMENT_VERSION_MISMATCH'),
    ('physical_group', 'S861_SINGLE_PHYSICAL_EMBEDDING_REQUIRED'),
    ('unreviewed_parameter', 'S861_UNREVIEWED_MODEL_PARAMETERS'),
    ('unsafe_config', 'S861_UNSAFE_MODEL_CONFIG'),
    ('qu_model', 'S861_CHEAP_QU_BINDING_REQUIRED'),
    ('memory', 'S861_RECOMPUTE_MEMORY_BUDGET'),
])
def test_current_inventory_drift_fails_closed(tmp_path, mutation, code):
    value = json.loads(INVENTORY.read_text(encoding='utf8'))
    first = value['current_searchable_chunk_stats'][0]
    if mutation == 'hash': first['content_sha256'] = '0' * 64
    elif mutation == 'source_version': first['source_version'] = 'different-version'
    elif mutation == 'document_version':
        next(row for row in value['documents'] if row['id'] == first['document_id'])['source_version'] = 'different-version'
    elif mutation == 'physical_group': value['knowledge_base_bindings'][0]['embedding_physical_key'] = 'different-group'
    elif mutation == 'unreviewed_parameter': value['models']['cheap']['unreviewed_parameter_keys'] = ['max_tokens']
    elif mutation == 'unsafe_config': value['models']['cheap']['reconstruction']['blocked_fields'] = ['custom_headers']
    elif mutation == 'qu_model': value['safe_environment']['WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID'] = 'other-model'
    elif mutation == 'memory': value['memory']['origin_knowledge_base_ids'] = list(profile.KB_IDS)
    with pytest.raises(ValueError, match=code):
        asyncio.run(profile.dry_run_inventory(write_json(tmp_path / 'inventory.json', value), ORIGINALS))


@pytest.mark.parametrize('role,section,field,value', [
    ('cheap', 'chat', 'max_tokens_field', 'max_completion_tokens'),
    ('rerank', 'reranking', 'max_document_chars', 64),
])
def test_reconstructed_protocol_configuration_drift_cannot_reuse_bound(tmp_path, role, section, field, value):
    inventory = json.loads(INVENTORY.read_text(encoding='utf8'))
    inventory['models'][role]['reconstruction']['model_config'][section][field] = value
    with pytest.raises(ValueError, match='S861_'):
        asyncio.run(profile.dry_run_inventory(write_json(tmp_path / 'inventory.json', inventory), ORIGINALS))


def guarded_runtime(mode='query_structure_v1'):
    state = PipelineState(query=profile.QUESTIONS['compare'], rewrite_query=profile.QUESTIONS['compare'],
        targets=[SearchTarget(profile.KB_IDS[0])], retrieval_strategy_mode=mode,
        source_evidence_mode='bounded_source_v1', query_understand_model_id='approved-qu',
        rerank_threshold=.3, rerank_top_k=30, embedding_top_k=30, rerank_context_enabled=False,
        language='English')
    base = TurnConfiguration(state, {'options': ChatOptions(max_completion_tokens=2048,
        temperature=.7, thinking=False), 'system_prompt': DEFAULT_SYSTEM_PROMPT})
    calls = []
    async def configuration(turn):
        calls.append(turn)
        return base
    runtime = SimpleNamespace(ordinary=SimpleNamespace(configuration=configuration))
    turn = SimpleNamespace(run_id=str(uuid.uuid4()), request=SimpleNamespace(
        request_id='s861-offline-bound', content=profile.QUESTIONS['compare']),
        context=CallContext(str(uuid.uuid4()), TrustedScope('approved-user', profile.KB_IDS, profile.DOCUMENT_IDS)))
    turn.context = replace(turn.context, request_id=turn.run_id)
    approved = {'qu_model_id': 'approved-qu', 'turns': [{'request_id': turn.request.request_id,
        'question': turn.request.content, 'mode': mode}]}
    return runtime, base, turn, approved, calls


def install_guard(runtime, approved, *, expiry=None):
    return profile.install_profile(runtime, approved_batch=approved,
        expires_at=expiry or datetime.now(timezone.utc) + timedelta(hours=1))


def test_guard_preserves_normal_server_uuid_and_exact_approved_configuration():
    runtime, base, turn, approved, calls = guarded_runtime()
    initial_context = turn.context
    install_guard(runtime, approved)
    result = asyncio.run(runtime.ordinary.configuration(turn))
    assert uuid.UUID(turn.run_id) and turn.run_id != turn.request.request_id
    assert turn.context is initial_context and turn.context.request_id == turn.run_id
    assert calls == [turn]
    assert result.pipeline_state.query == turn.request.content
    assert result.pipeline_state.rerank_threshold == .3
    assert result.pipeline_state.query_understand_model_id == 'approved-qu'
    assert result.generation_options['options'] is base.generation_options['options']
    assert result.generation_options['citations_enabled'] is True


@pytest.mark.parametrize('field,value', [
    ('system_prompt', 'Unbudgeted custom generation system prompt'),
    ('context_template', 'Custom context: {{contexts}}; question={{query}}'),
    ('rewrite_system_prompt', 'Unbudgeted custom rewrite system prompt'),
    ('rewrite_user_prompt', 'Unbudgeted custom rewrite user {{query}}'),
    ('language', 'Chinese'),
    ('attachment_prompt', 'Unbudgeted attachment content'),
    ('images', ('offline-image-resource',)),
    ('quoted_context', 'Unbudgeted quoted context'),
    ('system_prompt_override', 'Unbudgeted intent override'),
    ('intent_system_prompts', {'kb_search': 'Unbudgeted session intent prompt'}),
    ('global_intent_system_prompts', {'kb_search': 'Unbudgeted global intent prompt'}),
])
def test_approved_guard_rejects_unbudgeted_prompt_and_attachment_inputs(field, value):
    runtime, base, turn, approved, calls = guarded_runtime()
    if field == 'system_prompt': base.generation_options[field] = value
    else: setattr(base.pipeline_state, field, value)
    install_guard(runtime, approved)
    with pytest.raises(ValueError, match='S861_'):
        asyncio.run(runtime.ordinary.configuration(turn))
    assert calls == [turn]


def test_unapproved_composition_still_preserves_custom_resolved_prompts_and_language():
    runtime, base, turn, approved, calls = guarded_runtime()
    custom_fields = {'context_template': 'Custom {{contexts}} {{query}}',
        'rewrite_system_prompt': 'Custom rewrite system', 'rewrite_user_prompt': 'Custom rewrite {{query}}',
        'language': 'Chinese', 'intent_system_prompts': {'kb_search': 'Custom session intent'},
        'global_intent_system_prompts': {'greeting': 'Custom global intent'}}
    for field, value in custom_fields.items(): setattr(base.pipeline_state, field, value)
    base.generation_options['system_prompt'] = 'Custom generation prompt'
    profile.install_profile(runtime)
    resolved = asyncio.run(runtime.ordinary.configuration(turn))
    assert calls == [turn]
    assert resolved.generation_options['system_prompt'] == 'Custom generation prompt\n\n' + profile.PROMPT_B_SUFFIX
    assert all(getattr(resolved.pipeline_state, field) == value for field, value in custom_fields.items())
    assert base.generation_options['system_prompt'] == 'Custom generation prompt'


def fake_pipeline_runtime():
    runtime, base, turn, approved, calls = guarded_runtime()
    stages, simulated_sends = [], []
    async def normal_factory(normal_turn, progress):
        assert normal_turn is turn
        class Pipeline:
            async def run(self, state, context):
                await progress('understand', 'started')
                simulated_sends.append((state.query, context.request_id))
                return 'normal-factory-result'
        return Pipeline()
    runtime.ordinary.pipeline_factory = normal_factory
    async def normal_progress(stage, status, **fields): stages.append((stage, status, fields))
    install_guard(runtime, approved)
    return runtime, turn, stages, simulated_sends, normal_progress


@pytest.mark.parametrize('field,value', [
    ('history', [SimpleNamespace(query='Prior question', answer='Prior answer')]),
    ('memory_background', 'Unbudgeted memory background'),
])
@pytest.mark.parametrize('populate_after_factory', [False, True])
def test_approved_guard_rejects_history_or_memory_before_any_pipeline_model_send(field, value, populate_after_factory):
    runtime, turn, stages, simulated_sends, progress = fake_pipeline_runtime()
    async def exercise():
        cfg = await runtime.ordinary.configuration(turn)
        if not populate_after_factory: setattr(cfg.pipeline_state, field, value)
        with pytest.raises(ValueError, match='S861_'):
            pipeline = await runtime.ordinary.pipeline_factory(turn, progress)
            if populate_after_factory: setattr(cfg.pipeline_state, field, value)
            await pipeline.run(cfg.pipeline_state, turn.context)
    asyncio.run(exercise())
    assert simulated_sends == []
    assert stages == []


def test_approved_fresh_empty_context_forwards_normal_factory_and_progress():
    runtime, turn, stages, simulated_sends, progress = fake_pipeline_runtime()
    async def exercise():
        cfg = await runtime.ordinary.configuration(turn)
        pipeline = await runtime.ordinary.pipeline_factory(turn, progress)
        return await pipeline.run(cfg.pipeline_state, turn.context)
    assert asyncio.run(exercise()) == 'normal-factory-result'
    assert simulated_sends == [(turn.request.content, turn.run_id)]
    assert stages == [('understand', 'started', {})]


@pytest.mark.parametrize('mutation,code,before_resolution', [
    ('question', 'S861_APPROVED_TURN_REQUIRED', True),
    ('client', 'S861_APPROVED_TURN_REQUIRED', True),
    ('kb', 'S861_APPROVED_SCOPE_REQUIRED', True),
    ('document', 'S861_APPROVED_SCOPE_REQUIRED', True),
    ('mode', 'S861_SAVED_MODE_MISMATCH', False),
    ('threshold', 'S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED', False),
    ('rerank_top_k', 'S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED', False),
    ('embedding_top_k', 'S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED', False),
    ('rerank_context', 'S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED', False),
    ('targets', 'S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED', False),
    ('qu_model', 'S861_PREFLIGHT_QU_BINDING_CHANGED', False),
    ('output', 'S861_PREFLIGHT_GENERATION_OPTIONS_CHANGED', False),
    ('temperature', 'S861_PREFLIGHT_GENERATION_OPTIONS_CHANGED', False),
    ('thinking', 'S861_PREFLIGHT_GENERATION_OPTIONS_CHANGED', False),
])
def test_guard_rejects_turn_or_resolved_contract_drift(mutation, code, before_resolution):
    runtime, base, turn, approved, calls = guarded_runtime()
    if mutation == 'question': turn.request.content += ' extra question'
    elif mutation == 'client': turn.request.request_id = 's861-unapproved'
    elif mutation == 'kb': turn.context = replace(turn.context, scope=TrustedScope('approved-user', ('other-kb',), profile.DOCUMENT_IDS))
    elif mutation == 'document': turn.context = replace(turn.context, scope=TrustedScope('approved-user', profile.KB_IDS, ()))
    elif mutation == 'mode': base.pipeline_state.retrieval_strategy_mode = 'off'
    elif mutation == 'threshold': base.pipeline_state.rerank_threshold = .29
    elif mutation in ('rerank_top_k', 'embedding_top_k'): setattr(base.pipeline_state, mutation, 31)
    elif mutation == 'rerank_context': base.pipeline_state.rerank_context_enabled = True
    elif mutation == 'targets': base.pipeline_state.targets *= 2
    elif mutation == 'qu_model': base.pipeline_state.query_understand_model_id = 'different-qu'
    else:
        field, value = {'output': ('max_completion_tokens', 2049), 'temperature': ('temperature', .71),
            'thinking': ('thinking', True)}[mutation]
        base.generation_options['options'] = replace(base.generation_options['options'], **{field: value})
    install_guard(runtime, approved)
    with pytest.raises(ValueError, match=code): asyncio.run(runtime.ordinary.configuration(turn))
    assert len(calls) == (0 if before_resolution else 1)


def test_guard_rechecks_expiry_on_every_turn_and_denies_different_run_reuse(monkeypatch):
    runtime, base, turn, approved, calls = guarded_runtime()
    now = datetime.now(timezone.utc)
    install_guard(runtime, approved, expiry=now + timedelta(seconds=10))
    asyncio.run(runtime.ordinary.configuration(turn))
    conflicting = SimpleNamespace(**vars(turn)); conflicting.run_id = str(uuid.uuid4())
    with pytest.raises(ValueError, match='S861_CLIENT_REQUEST_ALREADY_BOUND'):
        asyncio.run(runtime.ordinary.configuration(conflicting))
    class ExpiredClock:
        @staticmethod
        def now(tz): return now + timedelta(seconds=11)
    monkeypatch.setattr(profile, 'datetime', ExpiredClock)
    with pytest.raises(ValueError, match='S861_AUTH_EXPIRED'):
        asyncio.run(runtime.ordinary.configuration(turn))
    assert calls == [turn]


def authorization_files(tmp_path, monkeypatch):
    # Linux path remains in metadata; no actual old or remote ledger is opened.
    ledger_name = '/opt/rag-s8/state/executions/s861-offline-only/execution.json'
    ledger_path = tmp_path / 'new-offline-ledger.json'
    auth_path = tmp_path / 'new-offline-owner.json'
    allowed_models = ['offline-approved-physical-model']
    batch = {'max_calls': 7, 'max_tokens': 99999, 'execution_file': ledger_name,
        'allowed_models': allowed_models, 'turns': [{'request_id': 's861-offline-only',
            'question': profile.QUESTIONS['compare'], 'case': 'compare', 'mode': 'query_structure_v1'}]}
    owner = {'goal': 'S8.6.1', 'owner_approved': True, 'profile_sha256': profile.digest(profile.profile_manifest()),
        'source_revision': 'a' * 40, 'image_id': 'sha256:' + 'b' * 64,
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'batches': {'candidate-only': batch}}
    ledger = {'status': 'OPEN', 'entries': [], 'max_calls': 7, 'max_tokens': 99999,
        'capture_evidence': True, 'allowed_kbs': list(profile.KB_IDS), 'allowed_models': allowed_models,
        'allowed_request_prefixes': ['*']}
    environment = {'S861_AUTHORIZATION_FILE': str(auth_path), 'S861_SOURCE_REVISION': owner['source_revision'],
        'S861_IMAGE_ID': owner['image_id'], 'S861_BATCH_ID': 'candidate-only',
        'WEKNORA_PORT_EXECUTION_FILE': ledger_name}
    real_path = Path
    monkeypatch.setattr(profile, 'Path', lambda value: ledger_path if str(value) == ledger_name else real_path(value))
    budget_path = tmp_path / 'synthetic-admission-budget.json'
    # Pure metadata admission fixture; never presented as the actual dry-run.
    budget = {'schema': 's861-reservation-dryrun/v2',
        'profile_sha256': profile.digest(profile.profile_manifest()),
        'implementation_sha256_LF': profile.digest(real_path(profile.__file__).read_bytes().replace(b'\r\n', b'\n')),
        'submission_bounds': {'compare:query_structure_v1': {'max_calls': 7, 'reservation_tokens_upper': 99999}}}
    write_json(budget_path, budget)
    owner.update(budget_preflight_file=str(budget_path),
        budget_preflight_sha256=profile.digest(budget_path.read_bytes()))
    return owner, ledger, environment, auth_path, ledger_path


def test_fresh_authorization_binds_explicit_uuid_prefix_envelope_without_touching_ledger(tmp_path, monkeypatch):
    owner, ledger, environment, auth_path, ledger_path = authorization_files(tmp_path, monkeypatch)
    write_json(auth_path, owner); write_json(ledger_path, ledger)
    before = ledger_path.read_bytes()
    assert profile._approved(environment) == owner
    assert ledger_path.read_bytes() == before


@pytest.mark.parametrize('mutation,code', [
    ('old_goal', 'S861_FRESH_OWNER_AUTHORIZATION_REQUIRED'),
    ('expiry', 'S861_AUTH_EXPIRED'),
    ('profile', 'S861_AUTH_PROFILE_MISMATCH'),
    ('source', 'S861_AUTH_SOURCE_MISMATCH'),
    ('models', 'S861_AUTH_MODEL_SCOPE_MISMATCH'),
    ('kb', 'S861_AUTH_KB_SCOPE_MISMATCH'),
    ('prefix_empty', 'S861_AUTH_REQUEST_SCOPE_MISMATCH'),
    ('prefix_extra', 'S861_AUTH_REQUEST_SCOPE_MISMATCH'),
    ('long_client', 'S861_AUTH_REQUEST_SCOPE_MISMATCH'),
    ('old_entries', 'S861_FRESH_EMPTY_LEDGER_REQUIRED'),
    ('unbounded_calls', 'S861_AUTH_BATCH_CALL_LIMIT'),
    ('unbounded_tokens', 'S861_AUTH_BATCH_TOKEN_LIMIT'),
])
def test_fresh_authorization_rejects_stale_or_unbounded_metadata(tmp_path, monkeypatch, mutation, code):
    owner, ledger, environment, auth_path, ledger_path = authorization_files(tmp_path, monkeypatch)
    batch = owner['batches']['candidate-only']
    if mutation == 'old_goal': owner['goal'] = 'S8.5'
    elif mutation == 'expiry': owner['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    elif mutation == 'profile': owner['profile_sha256'] = '0' * 64
    elif mutation == 'source': owner['source_revision'] = 'c' * 40
    elif mutation == 'models': ledger['allowed_models'] = ['different-model']
    elif mutation == 'kb': ledger['allowed_kbs'] = ['different-kb']
    elif mutation == 'prefix_empty': ledger['allowed_request_prefixes'] = ['']
    elif mutation == 'prefix_extra': ledger['allowed_request_prefixes'] = ['*', 'old-run']
    elif mutation == 'long_client': batch['turns'][0]['request_id'] = 's861-' + 'a' * 32
    elif mutation == 'old_entries': ledger['entries'] = [{'status': 'UNKNOWN', 'held_tokens': 123}]
    elif mutation == 'unbounded_calls': batch['max_calls'] = 13
    elif mutation == 'unbounded_tokens': batch['max_tokens'] = 100001
    write_json(auth_path, owner); write_json(ledger_path, ledger)
    before = ledger_path.read_bytes()
    with pytest.raises(ValueError, match=code): profile._approved(environment)
    assert ledger_path.read_bytes() == before


def test_api_and_collector_claims_are_separate_atomic_one_use_files(tmp_path):
    execution_path = tmp_path / 'new-offline-ledger.json'
    execution_path.write_text('unchanged ledger bytes', encoding='utf8')
    before = execution_path.read_bytes()
    for component in ('api', 'collector'):
        profile.claim_batch(str(execution_path), component, 'profile-sha')
        marker = tmp_path / ('.s861-' + component + '-claim.json')
        marker_before = marker.read_bytes()
        with pytest.raises(FileExistsError): profile.claim_batch(str(execution_path), component, 'different-profile')
        assert marker.read_bytes() == marker_before
    with pytest.raises(ValueError, match='S861_CLAIM_COMPONENT_INVALID'):
        profile.claim_batch(str(execution_path), 'worker', 'profile-sha')
    assert execution_path.read_bytes() == before


def test_parser_accepts_actual_wire_shape_and_rejects_non_message_event():
    from backend.weknora_port.streaming.events import StreamEvent, to_sse
    run_id = str(uuid.uuid4())
    binding = {'request_id': run_id, 'query': profile.QUESTIONS['compare'],
        'session_id': 'offline-session', 'assistant_message_id': 'offline-message'}
    raw = to_sse(StreamEvent(run_id, 'agent_query', data=binding), run_id)
    frames = profile.parse_sse(raw.decode().replace('\n', '\r\n'))
    assert len(frames) == 1 and frames[0]['id'] == run_id and frames[0]['data'] == binding
    with pytest.raises(ValueError, match='S861_SSE_EVENT_CONTRACT'):
        profile.parse_sse(raw.decode().replace('event: message', 'event: other'))



def test_approved_ledger_cannot_activate_when_full_preflight_exceeds_it(tmp_path, monkeypatch):
    owner, ledger, environment, auth_path, ledger_path = authorization_files(tmp_path, monkeypatch)
    budget_path = Path(owner['budget_preflight_file'])
    budget = json.loads(budget_path.read_text(encoding='utf8'))
    budget['submission_bounds']['compare:query_structure_v1']['reservation_tokens_upper'] = 132474
    write_json(budget_path, budget)
    owner['budget_preflight_sha256'] = profile.digest(budget_path.read_bytes())
    write_json(auth_path, owner); write_json(ledger_path, ledger)
    before = ledger_path.read_bytes()
    with pytest.raises(ValueError, match='S861_PREFLIGHT_BUDGET_EXCEEDED'):
        profile._approved(environment)
    assert ledger_path.read_bytes() == before
