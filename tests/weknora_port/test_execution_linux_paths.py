"""Private Linux ledger location only; no provider or real authorization mutation."""
import json
import os
from pathlib import PurePosixPath

import pytest

from backend.weknora_port import execution

ROOT = '/opt/rag-s8/state/executions'


def test_linux_root_requires_explicit_server_configuration():
    path = ROOT + '/batch-01/execution.json'
    assert execution._linux_execution_location(path, ROOT) == PurePosixPath(path)
    for approved in (None, '', '/', '/tmp', '/opt/rag-s8', ROOT + '/other'):
        with pytest.raises(ValueError, match='LINUX_ROOT'):
            execution._linux_execution_location(path, approved)


@pytest.mark.parametrize('path', ['execution.json', './execution.json', '/tmp/execution.json',
                                '/app/execution.json', ROOT, ROOT + '/../outside.json',
                                ROOT + '/nested/../../outside.json', ROOT + '/./execution.json',
                                '//opt/rag-s8/state/executions/a.json', ROOT + '\\outside.json',
                                'E:/run/execution.json'])
def test_relative_repository_ancestor_and_path_escape_are_rejected(path):
    with pytest.raises(ValueError, match='LINUX_ROOT'):
        execution._linux_execution_location(path, ROOT)


@pytest.mark.skipif(os.name == 'nt', reason='Real POSIX resolve/flock must be tested on Linux; lexical guards above run on Windows')
def test_native_linux_constructor_preserves_stop_unknown_and_rejects_symlink(tmp_path, monkeypatch):
    root = tmp_path / 'executions'
    root.mkdir()
    monkeypatch.setattr(execution, '_LINUX_EXECUTION_ROOT', root.as_posix())
    monkeypatch.setenv('WEKNORA_PORT_EXECUTION_ROOT', root.as_posix())
    grant = {'status': 'STOP', 'tenant_id': 332, 'subject_id': 'synthetic-test', 'allowed_kbs': ['kb'],
             'allowed_models': [{'provider': 'siliconflow', 'model': 'synthetic', 'model_id': 'm', 'capability': 'embedding'}],
             'allowed_request_prefixes': ['synthetic/'], 'max_calls': 1, 'max_tokens': 10000, 'entries': []}
    ledger = root / 'execution.json'
    ledger.write_text(json.dumps(grant))
    fence = execution.LocalExecution(ledger)
    assert fence.path == ledger
    assert json.loads(ledger.read_text()) == grant
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps(grant))
    alias = root / 'alias.json'
    alias.symlink_to(outside)
    with pytest.raises(ValueError, match='LINUX_PATH'):
        execution.LocalExecution(alias)
    (root / 'execution.json.lock').unlink()
    (root / 'execution.json.lock').symlink_to(outside)
    with pytest.raises(ValueError, match='LINUX_PATH'):
        execution.LocalExecution(ledger)
