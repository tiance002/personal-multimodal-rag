"""SYNTHETIC public package and bounded run guards; no historical acceptance.

The actual private requests, questions and scoring remain in the original local
branch. These generated examples test contracts only and perform no live sends.
"""
import asyncio
import json
import shutil
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from scripts import s8_6_5_generation_compare as compare


@pytest.fixture(scope='module')
def synthetic_package(tmp_path_factory):
    from backend.weknora_port.models import provider_config

    package = tmp_path_factory.mktemp('s865-synthetic-public')
    profile = compare.candidate_profile()
    manifest = dict(schema='s8.6.5-fixed-generation/v1',
        fixture_kind='SYNTHETIC_NOT_HISTORICAL_ACCEPTANCE', cases=[],
        maximum_generation_calls=30, new_provider_calls=0, accounting_mode=compare.ACCOUNTING,
        candidate_profile={k: v for k, v in profile.items()
                           if k not in ('suffix', 'baseline_prompt_b_suffix')})
    configs = {'cheap': replace(provider_config('siliconflow', 'Qwen/Qwen3.5-4B'), model_id='current-cheap'),
               'expensive': replace(provider_config('deepseek', 'deepseek-flash'), model_id='current-expensive')}
    snapshot = dict(fixture_kind='SYNTHETIC_NOT_CURRENT_ECS', selected_roles=[
        dict(role=role, provider=config.provider, model=config.model, model_id=config.model_id,
             base_url=config.base_url, base_url_redacted=False, parameters={}, source='remote',
             removed_custom_headers_or_sensitive_parameters=False, selection_status='MATCHED')
        for role, config in configs.items()])
    compare.write(package / 'current_model_configuration_safe.json', snapshot)
    manifest['current_model_configuration'] = dict(file='current_model_configuration_safe.json',
        sha256=compare.fingerprint(package / 'current_model_configuration_safe.json')['sha256'])
    for index in range(1, 11):
        question = f'SYNTHETIC fixture question {index}: which fixture value is recorded?'
        body = dict(model='Qwen/Qwen3.5-4B', messages=[
            dict(role='system', content='SYNTHETIC contract fixture. Current time: fixture\n'
                 + profile['baseline_prompt_b_suffix']),
            dict(role='user', content=question + f'\n<content>SYNTHETIC fixture value {index}.</content>')],
            stream=True, stream_options={'include_usage': True}, temperature=0.3,
            max_tokens=2048, enable_thinking=False)
        request_file = f'requests/{index:02}.json'
        compare.write(package / request_file, body)
        manifest['cases'].append(dict(case_id=f'SYNTHETIC-{index:02}', question=question,
            request_file=request_file, body_sha256=compare.digest(body), messages_sha256=compare.digest(body['messages']),
            source_messages_sha256=compare.digest(body['messages'][1:]),
            q0_sha256=compare.hashlib.sha256(question.encode('utf8')).hexdigest(),
            source_cohort='SYNTHETIC_HTTP_SHAPE' if index <= 3 else 'SYNTHETIC_DIAGNOSTIC_SHAPE' if index <= 8 else 'SYNTHETIC_CONTROL_SHAPE',
            selection_role='CORRECT_CONTROL' if index >= 9 else 'SYNTHETIC_FAILURE_SHAPE',
            kb_scope=['SYNTHETIC-KB'], document_scope=['SYNTHETIC-DOCUMENT'],
            historical_settlement=dict(status='SETTLED', outcome='RESPONSE_RECEIVED', fixture_kind='SYNTHETIC_ONLY')))
    compare.write(package / 'manifest.json', manifest)
    compare.write(package / 'scoring_only.json', dict(fixture_kind='SYNTHETIC_ONLY', cases=[]))
    compare.freeze_wires(package)
    return package


def test_ten_unique_synthetic_contract_cases_without_gold_inputs(synthetic_package):
    manifest = compare.read_manifest(synthetic_package)
    assert manifest['fixture_kind'] == 'SYNTHETIC_NOT_HISTORICAL_ACCEPTANCE'
    assert len(manifest['cases']) == len({row['question'] for row in manifest['cases']}) == 10
    assert len({row['case_id'] for row in manifest['cases']}) == 10
    assert manifest['new_provider_calls'] == 0 and manifest['maximum_generation_calls'] == 30
    assert sum(row['source_cohort'] == 'SYNTHETIC_HTTP_SHAPE' for row in manifest['cases']) == 3
    assert sum(row['source_cohort'] == 'SYNTHETIC_DIAGNOSTIC_SHAPE' for row in manifest['cases']) == 5
    assert sum(row['selection_role'] == 'CORRECT_CONTROL' for row in manifest['cases']) >= 2
    for row in manifest['cases']:
        arms = compare.load_case(synthetic_package, row['case_id'])
        assert arms['A'][0] == arms['B'][0] == arms['C'][0]  # same options
        assert arms['A'][1][1:] == arms['B'][1][1:] == arms['C'][1][1:]
        assert arms['B'][1] == arms['C'][1]
        assert row['historical_settlement']['status'] == 'SETTLED'
        assert row['historical_settlement']['outcome'] == 'RESPONSE_RECEIVED'


def test_scoring_can_be_destroyed_without_affecting_rendering(tmp_path, synthetic_package):
    package = tmp_path / 'frozen'
    shutil.copytree(synthetic_package, package)
    (package / 'scoring_only.json').write_text('INVALID_JSON_SECRET_GOLD', encoding='utf8')
    report = compare.dry_run(package)
    assert report['cases'] == 10 and report['arms'] == 30
    assert 'SECRET_GOLD' not in json.dumps(report)
    assert report['actual_current_role_snapshot'] in ('NOT_VERIFIED_CURRENT_ECS_READ_REQUIRED',
        'SOURCE_READ_CURRENT_SNAPSHOT_EXECUTION_RECHECK_REQUIRED')
    assert report['accounting_mode'] == 'reported_usage_only_v1'
    assert report['planning_tokens_upper_bound'] > 30 * 2048


def test_duplicate_questions_and_unknown_cases_fail_closed(tmp_path, synthetic_package):
    package = tmp_path / 'frozen'
    shutil.copytree(synthetic_package, package)
    path = package / 'manifest.json'
    manifest = json.loads(path.read_text('utf8'))
    manifest['cases'][1]['question'] = manifest['cases'][0]['question']
    path.write_text(json.dumps(manifest), encoding='utf8')
    with pytest.raises(ValueError, match='UNIQUE'):
        compare.read_manifest(package)


def test_tampered_request_and_scoring_path_are_rejected(tmp_path, synthetic_package):
    package = tmp_path / 'frozen'
    shutil.copytree(synthetic_package, package)
    manifest = compare.read_manifest(package)
    row = manifest['cases'][0]
    path = package / row['request_file']
    path.write_text(path.read_text('utf8').replace('Current time:', 'Altered time:'), encoding='utf8')
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        compare.load_case(package, row['case_id'])
    manifest['cases'][0]['request_file'] = 'scoring_only.json'
    (package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    with pytest.raises(ValueError, match='PATH_INVALID'):
        compare.load_case(package, row['case_id'])


def test_historical_closed_grant_cannot_authorize_new_execution(synthetic_package):
    with pytest.raises(ValueError, match='FRESH_OWNER_GRANT'):
        compare.validate_owner_grant({'status': 'CLOSED', 'owner_approved': True}, synthetic_package, 'unused')


def test_unknown_settlement_is_never_eligible():
    with pytest.raises(ValueError, match='SETTLED'):
        compare.checked_settlement({'entries': [{'request_id': 'r', 'status': 'UNKNOWN'}]},
                                   {'request_id': 'r', 'body': {}})


def test_missing_current_source_proof_blocks_before_any_provider(synthetic_package):
    with pytest.raises(ValueError, match='BLOCKED_CURRENT_SOURCE_PREFLIGHT'):
        compare.validate_source_proof({}, synthetic_package, compare.read_manifest(synthetic_package)['cases'][0]['case_id'])


def test_unknown_usage_keeps_known_reported_total_and_absent_actual_total():
    facts = compare.usage_facts([
        {'status': 'UNKNOWN', 'usage': {'status': 'REPORTED', 'total_tokens': 7}},
        {'status': 'UNKNOWN', 'usage': None}])
    assert facts == dict(reported_total_tokens=7, usage_missing_entries=1,
                         actual_total_tokens=None, unknown_entries=2, unsettled_entries=2, attempts=2)


def test_preparation_refuses_to_overwrite_frozen_package(synthetic_package):
    with pytest.raises(ValueError, match='ALREADY_EXISTS'):
        compare.prepare(synthetic_package)


@pytest.mark.parametrize('command', ['prepare', 'dry-run', 'run'])
def test_cli_requires_explicit_private_package(monkeypatch, capsys, command):
    monkeypatch.setattr(compare.sys, 'argv', ['s8_6_5_generation_compare.py', command])
    with pytest.raises(SystemExit) as stopped:
        compare.main()
    assert stopped.value.code == 2
    assert '--package' in capsys.readouterr().err


@pytest.mark.parametrize('provided', [[], ['--source-export', 'SYNTHETIC-EXPORT'],
                                    ['--ecs40-snapshot', 'SYNTHETIC-SNAPSHOT']])
def test_prepare_cli_requires_explicit_private_inputs(monkeypatch, capsys, tmp_path, provided):
    package = tmp_path / 'not-created'
    monkeypatch.setattr(compare.sys, 'argv', ['s8_6_5_generation_compare.py', 'prepare',
        '--package', str(package), *provided])
    with pytest.raises(SystemExit) as stopped:
        compare.main()
    assert stopped.value.code == 2 and not package.exists()
    assert 'prepare requires --source-export --ecs40-snapshot' in capsys.readouterr().err


def test_prepare_api_has_no_implicit_private_inputs(tmp_path):
    package = tmp_path / 'not-created'
    with pytest.raises(ValueError, match='PRIVATE_PREPARATION_INPUTS_REQUIRED'):
        compare.prepare(package)
    assert not package.exists()


def test_cli_dry_run_uses_explicit_synthetic_package_without_printing_content(monkeypatch, capsys, synthetic_package):
    monkeypatch.setattr(compare.sys, 'argv', ['s8_6_5_generation_compare.py', 'dry-run',
        '--package', str(synthetic_package)])
    compare.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result['cases'] == 10 and result['provider_calls'] == 0
    assert 'SYNTHETIC fixture question' not in output and '<content>' not in output


@pytest.mark.parametrize('known_usage', [False, True])
def test_mock_normal_registry_run_claims_before_each_provider_and_stops_unknown(tmp_path, monkeypatch, known_usage, synthetic_package):
    from backend.weknora_port import execution
    from backend.weknora_port.models import provider_config
    from backend.weknora_port.models.types import StreamResponse, TokenUsage
    row = compare.read_manifest(synthetic_package)['cases'][0]
    # The independent mocked runner fixture omits the real role inventory; it
    # never substitutes synthetic model IDs into production provenance.
    package = tmp_path / 'mock-package'
    shutil.copytree(synthetic_package, package)
    mocked_manifest = compare.read_manifest(package)
    mocked_manifest.pop('current_model_inventory', None)
    mocked_manifest.pop('current_model_configuration', None)
    compare.write(package / 'manifest.json', mocked_manifest)
    output = tmp_path / 'run'
    grant_path = tmp_path / 'grant.json'
    configs = {'cheap': replace(provider_config('siliconflow', 'Qwen/Qwen3.5-4B'), model_id='current-cheap'),
               'expensive': replace(provider_config('deepseek', 'deepseek-flash'), model_id='current-expensive')}
    proof_path = tmp_path / 'current-proof.json'
    now = time.time()
    compare.write(proof_path, dict(status='PASS_CURRENT_AUTHORIZED_READBACK',
        dataset_manifest_sha256=compare.fingerprint(package / 'manifest.json')['sha256'],
        checked_at_unix=now - 1, expires_at_unix=now + 2300,
        cases=[dict(case_id=row['case_id'], q0_sha256=row['q0_sha256'], source_messages_sha256=row['source_messages_sha256'],
                    knowledge_base_ids=row['kb_scope'], document_ids=row['document_scope'], source_version_membership='PASS',
                    permissions='PASS', cloud_consent='PASS', plaintext_matches_frozen='PASS',
                    chunk_version_readbacks=[{'id': 'SYNTHETIC_TEST_PROOF', 'source_version': 'SYNTHETIC'}])]))
    proof = compare.read(proof_path)
    proof['source_identity'] = dict(dataset_manifest_sha256=proof['dataset_manifest_sha256'],
        cases=[{key: value for key, value in proof['cases'][0].items() if key in
                ('case_id', 'q0_sha256', 'source_messages_sha256', 'knowledge_base_ids', 'document_ids', 'chunk_version_readbacks')}])
    compare.write(proof_path, proof)
    grant = dict(owner_approved=True, status='OPEN', scope='S8_6_5_FIXED_GENERATION',
        dataset_manifest_sha256=compare.fingerprint(package / 'manifest.json')['sha256'],
        allowed_case_ids=[row['case_id']], accounting_mode=compare.ACCOUNTING,
        max_calls=3, max_tokens=100000, allow_current_frozen_source_egress=True,
        tenant_id=332, subject_id='new-authorized-owner', request_prefix='fresh-s865-mock',
        script_sha256=compare.fingerprint(compare.__file__)['sha256'],
        candidate_suffix_sha256=compare.candidate_profile()['suffix_sha256'],
        rendered_wire_manifest_sha256=compare.fingerprint(package / 'wire_manifest.json')['sha256'],
        batch_started_at_unix=now - 10, batch_deadline_unix=now + 2300, expires_at_unix=now + 2300,
        source_proof_manifest_path=str(proof_path), source_identity_sha256=compare.digest(proof['source_identity']),
        resolved_role_bindings={role: {name: getattr(config, name) for name in ('model_id', 'provider', 'model')}
                                for role, config in configs.items()})
    compare.write(grant_path, grant)
    ledger = dict(status='OPEN', entries=[], max_calls=3, tenant_id=332,
        subject_id='new-authorized-owner', allowed_kbs=row['kb_scope'],
        allowed_request_prefixes=[compare.case_request_prefix(grant, row['case_id'])])

    class MockExecution:
        accounting_mode = compare.ACCOUNTING
        @contextmanager
        def _ledger(self):
            yield ledger
    monkeypatch.setattr(execution, 'LocalExecution', MockExecution)

    calls = []
    class MockModel:
        def __init__(self, config): self.config = config
        def build_request_body(self, messages, options, stream):
            from backend.weknora_port.models.provider import ProviderModel
            return ProviderModel(self.config, None).build_request_body(messages, options, stream=stream)
        async def chat_stream(self, messages, options, context):
            persisted = compare.read(output / 'state.json')
            arm = context.request_id.rsplit('/', 1)[1]
            assert persisted['status'] == 'BEFORE_PROVIDER' and persisted['arms'][arm]['status'] == 'CLAIMED_NO_RETRY'
            calls.append(context.request_id)
            ledger['entries'].append(dict(request_id=context.request_id, status='UNKNOWN', outcome='RESPONSE_UNCONFIRMED',
                usage={'status': 'REPORTED', 'total_tokens': 5, 'prompt_tokens': 3, 'completion_tokens': 2} if known_usage else None))
            yield StreamResponse('answer', 'partial', True, is_final=True, finish_reason='stop', usage=TokenUsage())

    models = {config.model_id: MockModel(config) for config in configs.values()}
    class Registry:
        async def chat(self, model_id, context): return models[model_id]
        async def close(self): pass
    @asynccontextmanager
    async def lease():
        yield object()
    async def snapshot(guard):
        return SimpleNamespace(model_id=lambda role: configs[role].model_id)
    app = SimpleNamespace(execution=MockExecution(), principal=SimpleNamespace(tenant_id=332, subject_id='new-authorized-owner'),
        registry_for_context=Registry, leases=SimpleNamespace(lease=lease), _snapshot=snapshot)
    with pytest.raises(RuntimeError, match='UNKNOWN'):
        asyncio.run(compare.run_case(package, row['case_id'], app=app, owner_grant=grant_path, output=output))
    assert len(calls) == 1 and ledger['status'] == 'STOP'
    assert compare.read(grant_path)['status'] == 'STOP'
    assert compare.read(grant_path)['reported_total_tokens'] == (5 if known_usage else 0)
    assert compare.read(grant_path)['actual_total_tokens'] == (5 if known_usage else None)
    assert compare.read(grant_path)['unknown_entries'] == 1
    assert compare.read(output / 'state.json')['status'] == 'STOP_NO_RETRY'
    with pytest.raises(ValueError):
        compare.claim_case(grant_path, package, row['case_id'], tmp_path / 'retry')
