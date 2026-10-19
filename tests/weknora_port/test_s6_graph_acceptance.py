"""Offline controller contracts; no service, DB or Provider is started."""
from dataclasses import dataclass
import json
from pathlib import Path
import asyncio

import pytest

from scripts import s6_graph_acceptance as acceptance


def grant():
    return {'scope': 'S6_20261019', 'status': 'OPEN', 'owner_approved': True,
            'max_calls': 20, 'max_tokens': 200000, 'manifest_sha256': 'a' * 64}


@pytest.mark.parametrize('changes', [
    {'scope': 'S5_20261019'}, {'status': 'CLOSED'}, {'status': 'STOP'},
    {'owner_approved': False}, {'max_calls': 21}, {'max_tokens': 200001},
    {'manifest_sha256': 'b' * 64}, {'max_calls': True},
])
def test_rejects_old_missing_or_expanded_owner_authorization(changes):
    with pytest.raises(ValueError, match='S6_CURRENT_EXTERNAL_OWNER_AUTHORIZATION_REQUIRED'):
        acceptance.validate_authorization(grant() | changes, 'a' * 64)


def test_external_current_authorization_is_accepted_without_rewriting_it():
    original = grant()
    acceptance.validate_authorization(original, 'a' * 64)
    assert original == grant()


@pytest.mark.parametrize('contents', [None, {'owner_approved': False}, grant() | {'status': 'CLOSED'}])
def test_prepare_without_current_authorization_is_zero_send_and_does_not_self_approve(tmp_path, contents):
    path = tmp_path / 'owner.json'
    if contents is not None:
        acceptance.write_new(path, contents)
    before = path.read_bytes() if path.exists() else None
    assert acceptance.authorization_state(path, 'a' * 64, sending=False) == 'BLOCKED_AUTHORIZATION_NO_SEND'
    assert (path.read_bytes() if path.exists() else None) == before
    with pytest.raises(ValueError, match='S6_CURRENT_EXTERNAL_OWNER_AUTHORIZATION_REQUIRED'):
        acceptance.authorization_state(path, 'a' * 64, sending=True)


def test_frozen_sources_require_exact_file_sha_bytes_and_text(tmp_path):
    sources = []
    for index, text in enumerate(('Original source one.', 'Original source two.')):
        file = tmp_path / f'{index}.txt'
        file.write_bytes(text.encode())
        sources.append({'path': str(file), 'sha256': acceptance.digest(file.read_bytes()),
                        'bytes': file.stat().st_size, 'text': text})
    manifest = tmp_path / 'sources.json'
    manifest.write_text(json.dumps({'sources': sources}))
    assert acceptance.frozen_sources(manifest) == sources
    Path(sources[0]['path']).write_text('Changed')
    with pytest.raises(ValueError, match='S6_FROZEN_SOURCE_CHANGED'):
        acceptance.frozen_sources(manifest)


@dataclass
class Principal:
    tenant_id: int = 1
    subject_id: str = 'existing-owner'


MODEL = {'provider': 'siliconflow', 'model': 'Qwen/Qwen3.5-4B', 'model_id': 'registered-id', 'capability': 'chat'}


def test_two_existing_fences_sum_to_authorized_total_and_never_allow_another_phase():
    phase1 = acceptance.make_ledger(Principal(), [MODEL], 1)
    phase2 = acceptance.make_ledger(Principal(), [MODEL], 2)
    assert phase1['max_calls'] + phase2['max_calls'] == 20
    assert phase1['max_tokens'] + phase2['max_tokens'] == 200000
    assert phase1['allowed_kbs'] == phase2['allowed_kbs'] == [acceptance.KB]
    assert phase1['subject_id'] == 'existing-owner'
    assert phase2['allowed_models'] == [MODEL]
    assert phase1['capture_evidence'] is phase2['capture_evidence'] is True
    with pytest.raises(IndexError):
        acceptance.make_ledger(Principal(), [MODEL], 3)


def execution_row(status, usage=None):
    return {'id': acceptance.digest(json.dumps(['request', 'chat']).encode()), 'request_id': 'request',
            **MODEL, 'wire_sha256': 'b' * 64, 'reserved_tokens': 1024, 'held_tokens': 1024,
            'status': status, 'usage': usage}


def test_unknown_is_not_zero_usage_and_blocks_phase_continuation(tmp_path):
    path = tmp_path / 'ledger.json'
    ledger = acceptance.make_ledger(Principal(), [MODEL], 1)
    ledger['status'] = 'STOP'
    ledger['entries'] = [execution_row('UNKNOWN')]
    acceptance.write_new(path, ledger)
    summary = acceptance.ledger_summary([path])
    assert summary['calls'] == summary['unknown'] == summary['unsettled'] == 1
    assert summary['held_tokens'] == 1024
    assert summary['entries'][0]['usage'] is None
    with pytest.raises(ValueError, match='S6_UNKNOWN_OR_SETTLEMENT_STOP'):
        acceptance.require_settled([path])
    assert acceptance.read(path) == ledger


def test_settled_ledger_uses_reported_usage_not_reserved_amount(tmp_path):
    path = tmp_path / 'ledger.json'
    ledger = acceptance.make_ledger(Principal(), [MODEL], 1)
    row = execution_row('SETTLED', {'status': 'REPORTED', 'total_tokens': 18})
    row['held_tokens'] = 18
    ledger['entries'] = [row]
    acceptance.write_new(path, ledger)
    summary = acceptance.require_settled([path])
    assert summary['reported_tokens'] == summary['held_tokens'] == 18


def test_exclusive_claim_and_ledger_files_never_reset_existing_data(tmp_path):
    path = tmp_path / 'claim.json'
    acceptance.write_new(path, {'first': 'preserved'})
    with pytest.raises(FileExistsError):
        acceptance.write_new(path, {'first': 'overwritten'})
    assert acceptance.read(path) == {'first': 'preserved'}


def test_phase_config_does_not_mutate_daily_configuration(tmp_path):
    cfg = {'state_dir': 'daily-runtime', 'environment': {'WEKNORA_PORT_CLOUD_ALLOWED': 'false',
           'WEKNORA_PORT_EVIDENCE_DIR': 'old-passive', 'WEKNORA_PORT_REDIS_PREFIX': 'unchanged-prefix'}}
    path = tmp_path / 'local.json'
    env = acceptance.phase_environment(cfg, {'WEKNORA_PORT_CLOUD_ALLOWED': 'false'}, tmp_path / 'execution.json', path)
    assert cfg['environment']['WEKNORA_PORT_CLOUD_ALLOWED'] == 'false'
    assert cfg['state_dir'] == 'daily-runtime'
    copied = acceptance.read(path)
    assert 'WEKNORA_PORT_EVIDENCE_DIR' not in copied['environment']
    assert copied['environment']['WEKNORA_PORT_REDIS_PREFIX'] == 'unchanged-prefix'
    assert env['WEKNORA_PORT_EXECUTION_FILE'] == str(tmp_path / 'execution.json')
    assert env['WEKNORA_PORT_GRAPH_ENABLED'] == 'true'


def test_process_with_inflight_is_not_terminated(tmp_path):
    path = tmp_path / 'ledger.json'
    ledger = acceptance.make_ledger(Principal(), [MODEL], 1)
    ledger['entries'] = [execution_row('INFLIGHT')]
    acceptance.write_new(path, ledger)

    class Process:
        def terminate(self):
            pytest.fail('A potentially sending process must never be killed')

    with pytest.raises(ValueError, match='S6_INFLIGHT_PROCESS_MUST_NOT_BE_KILLED'):
        asyncio.run(acceptance.stop_api(Process(), path))


def test_only_supplied_owned_process_is_stopped_after_no_inflight(tmp_path):
    path = tmp_path / 'ledger.json'
    acceptance.write_new(path, acceptance.make_ledger(Principal(), [MODEL], 1))
    actions = []

    class Process:
        def poll(self):
            return None

        def terminate(self):
            actions.append('terminate-owned')

        def wait(self, timeout):
            actions.append(('wait-owned', timeout))

    asyncio.run(acceptance.stop_api(Process(), path))
    assert actions == ['terminate-owned', ('wait-owned', 15)]


def test_arguments_require_explicit_external_files_and_no_auto_enable_switch():
    args = acceptance.arguments(['prepare', '--config', 'local.json', '--authorization', 'owner.json',
                                 '--manifest', 'sources.json', '--output', 'output'])
    assert args.command == 'prepare'
    assert not hasattr(args, 'approve') and not hasattr(args, 'unlimited')
