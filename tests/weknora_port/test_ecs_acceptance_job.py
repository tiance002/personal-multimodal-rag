"""Offline contracts for the detached ECS job runner; no SSH/DB/provider sends."""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import ecs_acceptance_job as jobs


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


@pytest.fixture
def job(tmp_path, monkeypatch):
    diagnostics = tmp_path / 'diagnostics'
    diagnostics.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.setattr(jobs, 'DIAGNOSTICS_ROOT', diagnostics)
    monkeypatch.setattr(jobs, 'WORKSPACE_ROOT', tmp_path)
    boot_id_reader = jobs._boot_id
    monkeypatch.setattr(jobs, '_boot_id', lambda: 'offline-current-boot')
    root = diagnostics / 'offline-job'
    payload = tmp_path / 'payload.py'
    payload.write_text('print("offline probe")\n', encoding='utf-8')
    manifest = dict(schema_version=1, kind='zero_provider_probe', job_id=root.name,
                    goal='offline-contract', source='a' * 40,
                    image='sha256:' + 'b' * 64, profile='c' * 64,
                    payload_sha256=hashlib.sha256(payload.read_bytes()).hexdigest(),
                    workspace=str(workspace), python=str(Path(sys.executable).resolve()))
    manifest_path = write_json(tmp_path / 'manifest.json', manifest)
    return SimpleNamespace(root=root, payload=payload, manifest=manifest,
                           manifest_path=manifest_path, workspace=workspace,
                           diagnostics=diagnostics, tmp=tmp_path, boot_id_reader=boot_id_reader)


def prepare(job):
    return jobs.prepare_job(job.root, job.manifest_path, job.payload)


def acceptance_grant(job):
    ledger = write_json(job.tmp / 'ledger.json', {'status': 'OPEN', 'entries': []})
    return write_json(job.tmp / 'owner-grant.json', {
        'owner_approved': True, 'goal': job.manifest['goal'],
        'source_revision': job.manifest['source'], 'image_id': job.manifest['image'],
        'profile_sha256': job.manifest['profile'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'owner_lock_file': str(job.tmp / 'owner-token.lock'),
        'batches': {'offline-case': {'execution_file': str(ledger)}}})


def test_prepare_freezes_manifest_payload_runner_and_fsyncs(job, monkeypatch):
    real_fsync = jobs.os.fsync
    fsyncs = []

    def record_fsync(fd):
        fsyncs.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(jobs.os, 'fsync', record_fsync)
    original_manifest = job.manifest_path.read_bytes()
    original_payload = job.payload.read_bytes()
    prepare(job)
    assert (job.root / 'control/manifest.json').read_bytes() == original_manifest
    assert (job.root / 'control/payload.py').read_bytes() == original_payload
    assert (job.root / 'control/runner.py').is_file()
    assert (job.root / 'control/prepared.json').is_file()
    assert fsyncs, 'prepare must flush frozen control records to stable storage'
    job.payload.write_text('raise RuntimeError("changed source")\n', encoding='utf-8')
    assert (job.root / 'control/payload.py').read_bytes() == original_payload


def test_prepare_rejects_payload_hash_mismatch_without_creating_runnable_job(job):
    job.payload.write_text('print("tampered")\n', encoding='utf-8')
    with pytest.raises(jobs.JobError):
        prepare(job)
    assert not (job.root / 'control/prepared.json').exists()


def test_prepared_job_cannot_be_overwritten(job):
    prepare(job)
    frozen = (job.root / 'control/manifest.json').read_bytes()
    job.manifest['goal'] = 'replacement'
    write_json(job.manifest_path, job.manifest)
    with pytest.raises(jobs.JobError):
        prepare(job)
    assert (job.root / 'control/manifest.json').read_bytes() == frozen


@pytest.mark.parametrize('kind', ['shell', 'framework', 'provider_retry', ''])
def test_only_explicit_job_kinds_are_admitted(job, kind):
    job.manifest['kind'] = kind
    write_json(job.manifest_path, job.manifest)
    with pytest.raises(jobs.JobError):
        prepare(job)


def test_acceptance_grant_is_optional_but_supplied_pair_requires_exact_hash(job):
    job.manifest['kind'] = 'acceptance'
    grant = job.tmp / 'owner-grant.json'
    grant.write_text('opaque payload authorization reference\n', encoding='utf-8')
    job.manifest['grant_path'] = str(grant)
    write_json(job.manifest_path, job.manifest)
    with pytest.raises(jobs.JobError):
        prepare(job)
    job.manifest['grant_sha256'] = '0' * 64
    write_json(job.manifest_path, job.manifest)
    with pytest.raises(jobs.JobError):
        prepare(job)
    assert not (job.root / 'control/prepared.json').exists()
    del job.manifest['grant_path'], job.manifest['grant_sha256']
    write_json(job.manifest_path, job.manifest)
    assert prepare(job)['status'] == 'PREPARED'


def test_interpreter_and_workspace_must_be_explicit_trusted_paths(job):
    job.manifest['python'] = 'python'
    write_json(job.manifest_path, job.manifest)
    with pytest.raises(jobs.JobError):
        prepare(job)
    job.manifest['python'] = str(Path(sys.executable).resolve())
    job.manifest['workspace'] = str(job.tmp.parent)
    write_json(job.manifest_path, job.manifest)
    with pytest.raises(jobs.JobError):
        prepare(job)


@pytest.mark.parametrize('bad_name', ['../escape', '.', 'nested/job'])
def test_job_root_is_a_direct_safe_diagnostics_child(job, bad_name):
    with pytest.raises(jobs.JobError):
        jobs.validate_job_root(job.diagnostics / bad_name)


def test_job_root_outside_diagnostics_is_rejected(job):
    with pytest.raises(jobs.JobError):
        jobs.validate_job_root(job.tmp / 'elsewhere' / 'offline-job')


def symlink_or_skip(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip('OS does not permit test symlinks: ' + str(exc))


def test_job_root_symlink_cannot_escape_diagnostics(job):
    target = job.tmp / 'outside'
    target.mkdir()
    symlink_or_skip(job.root, target, directory=True)
    with pytest.raises(jobs.JobError):
        jobs.validate_job_root(job.root, must_exist=True)


def test_payload_symlink_is_rejected(job):
    link = job.tmp / 'payload-link.py'
    symlink_or_skip(link, job.payload)
    with pytest.raises(jobs.JobError):
        jobs.prepare_job(job.root, job.manifest_path, link)


def test_artifact_hash_is_streamed_without_read_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'large-artifact.bin'
    content = bytes(range(256)) * 16385
    path.write_bytes(content)

    def forbidden(*args, **kwargs):
        raise AssertionError('artifact hashing must not materialize the entire file')

    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    assert jobs.sha256_file(path) == hashlib.sha256(content).hexdigest()


def test_existing_started_claim_prevents_any_new_child(job, monkeypatch):
    prepare(job)
    write_json(job.root / 'control/started.json', {'attempt': 'prior-unconfirmed'})
    monkeypatch.setattr(jobs.sys, 'platform', 'linux')
    calls = []
    monkeypatch.setattr(jobs.subprocess, 'Popen', lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(jobs.JobError):
        jobs.start_job(job.root, handshake_timeout=.001)
    assert calls == []


def test_frozen_payload_tampering_never_spawns(job, monkeypatch):
    prepare(job)
    frozen = job.root / 'control/payload.py'
    frozen.chmod(0o600)
    frozen.write_text('print("replacement")\n', encoding='utf-8')
    monkeypatch.setattr(jobs.sys, 'platform', 'linux')
    calls = []
    monkeypatch.setattr(jobs.subprocess, 'Popen', lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(jobs.JobError):
        jobs.start_job(job.root, handshake_timeout=.001)
    assert calls == []


def test_changed_acceptance_grant_never_spawns(job, monkeypatch):
    grant = acceptance_grant(job)
    job.manifest.update(kind='acceptance', grant_path=str(grant),
                        grant_sha256=hashlib.sha256(grant.read_bytes()).hexdigest())
    write_json(job.manifest_path, job.manifest)
    prepare(job)
    grant.write_text('{"owner_approved": false}\n', encoding='utf-8')
    monkeypatch.setattr(jobs.sys, 'platform', 'linux')
    calls = []
    monkeypatch.setattr(jobs.subprocess, 'Popen', lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(jobs.JobError):
        jobs.start_job(job.root, handshake_timeout=.001)
    assert calls == []


def test_start_detaches_stdio_session_and_keeps_claim_after_handshake_timeout(job, monkeypatch):
    prepare(job)
    monkeypatch.setattr(jobs.sys, 'platform', 'linux')
    calls = []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=424242, poll=lambda: None)

    monkeypatch.setattr(jobs.subprocess, 'Popen', popen)
    result = jobs.start_job(job.root, handshake_timeout=.001)
    assert result['status'] == 'START_UNCONFIRMED'
    assert result['start_acknowledged'] is False
    assert len(calls) == 1
    argv, options = calls[0]
    assert Path(argv[0]).resolve() == Path(sys.executable).resolve()
    assert argv[1] == str(job.root / 'control/runner.py')
    assert '_supervise' in argv and str(job.root) in argv
    assert options['start_new_session'] is True
    assert options['stdin'] == subprocess.DEVNULL
    assert options['close_fds'] is True
    assert options['stderr'] == subprocess.STDOUT
    assert Path(options['stdout'].name) == job.root / 'output/supervisor.log'
    assert options['stdout'].closed
    assert (job.root / 'control/started.json').is_file()
    with pytest.raises(jobs.JobError):
        jobs.start_job(job.root, handshake_timeout=.001)
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['boot_id', 'start_ticks', 'zombie'])
def test_identity_alive_rejects_reboot_pid_reuse_and_zombie(monkeypatch, change):
    identity = {'pid': 4242, 'boot_id': 'boot-current', 'start_ticks': 98765}
    observed = dict(identity)
    if change == 'boot_id':
        observed['boot_id'] = 'boot-new'
    if change == 'start_ticks':
        observed['start_ticks'] += 1
    monkeypatch.setattr(jobs, '_boot_id', lambda: observed['boot_id'])
    monkeypatch.setattr(jobs, 'process_identity', lambda pid: observed)
    original = Path.read_text

    def read_stat(path, *args, **kwargs):
        if str(path).replace('\\', '/') == '/proc/4242/stat':
            return '4242 (worker) ' + ('Z' if change == 'zombie' else 'S') + ' 0' * 18 + ' 98765'
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_stat)
    assert jobs.identity_alive(identity) is False


def test_process_identity_reads_start_ticks_after_parenthesized_process_name(monkeypatch):
    monkeypatch.setattr(jobs, '_boot_id', lambda: 'boot-current')
    original = Path.read_text

    def read_stat(path, *args, **kwargs):
        if str(path).replace('\\', '/') == '/proc/4242/stat':
            return '4242 (python (acceptance worker)) S' + ' 0' * 18 + ' 98765 0 0'
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_stat)
    assert jobs.process_identity(4242) == {'pid': 4242, 'boot_id': 'boot-current', 'start_ticks': 98765}


@pytest.mark.parametrize('reason', ['reboot', 'dead_supervisor'])
def test_status_marks_interruption_read_only_and_never_restarts(job, monkeypatch, reason):
    prepare(job)
    write_json(job.root / 'control/started.json', {
        'boot_id': 'offline-old-boot' if reason == 'reboot' else 'offline-current-boot'})
    state_path = write_json(job.root / 'output/state.json', {
        'status': 'RUNNING', 'supervisor': {'pid': 4242, 'boot_id': 'offline-current-boot', 'start_ticks': 8}})
    checkpoint = write_json(job.root / 'output/checkpoint.json', {'completed_cases': ['offline-case']})
    before = {path: path.read_bytes() for path in (state_path, checkpoint, job.root / 'control/started.json')}
    monkeypatch.setattr(jobs, 'identity_alive', lambda identity: False)
    calls = []
    monkeypatch.setattr(jobs.subprocess, 'Popen', lambda *a, **kw: calls.append((a, kw)))
    result = jobs.job_status(job.root)
    assert result['status'] == 'INTERRUPTED_UNCONFIRMED'
    assert result['reason'] == ('boot_changed' if reason == 'reboot' else 'supervisor_identity_not_alive')
    assert result['read_only'] is True and result['checkpoint']['completed_cases'] == ['offline-case']
    assert calls == []
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize('state', ['FAILED', 'INCOMPLETE_UNCONFIRMED', 'INTERRUPTED_UNCONFIRMED'])
def test_terminal_or_unknown_jobs_have_no_automatic_resume(job, monkeypatch, state):
    prepare(job)
    write_json(job.root / 'control/started.json', {'boot_id': 'offline-current-boot'})
    write_json(job.root / 'output/state.json', {'status': state, 'exit_code': 1})
    calls = []
    monkeypatch.setattr(jobs.subprocess, 'Popen', lambda *a, **kw: calls.append((a, kw)))
    assert jobs.job_status(job.root)['status'] == state
    monkeypatch.setattr(jobs.sys, 'platform', 'linux')
    with pytest.raises(jobs.JobError):
        jobs.start_job(job.root, handshake_timeout=.001)
    assert calls == []


def test_state_checkpoint_heartbeat_atomic_write_flushes_before_return(job, monkeypatch):
    prepare(job)
    real_fsync = jobs.os.fsync
    flushed = []

    def fsync(fd):
        flushed.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(jobs.os, 'fsync', fsync)
    result = jobs._state(job.root, {'status': 'STARTING'}, status='RUNNING')
    assert result['heartbeat_at'] and result['updated_at']
    assert json.loads((job.root / 'output/state.json').read_text(encoding='utf-8')) == result
    assert flushed
    assert not list((job.root / 'output').glob('*.tmp'))


def test_fetch_exports_durable_evidence_hashes_without_external_or_credential_files(job):
    credential = job.tmp / 'runtime.env'
    credential.write_text('API_KEY=NEVER_EXPORT_THIS_VALUE\n', encoding='utf-8')
    job.manifest['readonly_paths'] = [str(credential)]
    write_json(job.manifest_path, job.manifest)
    prepare(job)
    write_json(job.root / 'output/result.json', {'status': 'offline-result'})
    write_json(job.root / 'output/checkpoint.json', {'completed_cases': ['offline-case']})
    (job.root / 'output/stdout.log').write_text('durable log\n', encoding='utf-8')
    (job.root / 'output/runtime.env').write_bytes(credential.read_bytes())
    (job.root / 'output/credentials.json').write_text('{"token":"NEVER_EXPORT"}', encoding='utf-8')
    (job.root / 'unapproved-file.txt').write_text('NEVER_EXPORT', encoding='utf-8')
    export = job.tmp / 'fetched'
    receipt = jobs.fetch_job(job.root, export)
    assert receipt['all_files_stable'] is True
    assert (export / 'output/result.json').is_file()
    assert (export / 'output/checkpoint.json').is_file()
    assert (export / 'output/stdout.log').read_text(encoding='utf-8') == 'durable log\n'
    assert not (export / 'output/runtime.env').exists()
    assert not (export / 'output/credentials.json').exists()
    assert not (export / 'unapproved-file.txt').exists()
    assert not list(export.rglob('runtime.env'))
    for relative, info in receipt['files'].items():
        assert hashlib.sha256((export / relative).read_bytes()).hexdigest() == info['sha256']
    assert json.loads((export / 'export-manifest.json').read_text(encoding='utf-8')) == receipt


def test_fetch_rejects_any_symlink_without_following_it(job):
    prepare(job)
    source = job.tmp / 'external.txt'
    source.write_text('private external bytes', encoding='utf-8')
    symlink_or_skip(job.root / 'output/public-looking.txt', source)
    with pytest.raises(jobs.JobError):
        jobs.fetch_job(job.root, job.tmp / 'fetched')
    assert not (job.tmp / 'fetched').exists()


def supervise_offline(job, monkeypatch, result, *, close_grant=False):
    """Exercise supervisor I/O locally with only the payload process mocked."""
    grant = None
    if close_grant:
        grant = acceptance_grant(job)
        job.manifest.update(kind='acceptance', grant_path=str(grant),
                            grant_sha256=hashlib.sha256(grant.read_bytes()).hexdigest())
        write_json(job.manifest_path, job.manifest)
    prepare(job)
    write_json(job.root / 'control/started.json', {
        'launch_token': 'offline-claim', 'boot_id': 'offline-current-boot'})
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(
        LOCK_EX=2, LOCK_NB=4, flock=lambda *args: None))
    monkeypatch.setattr(jobs, 'process_identity', lambda pid: {
        'pid': pid, 'boot_id': 'offline-current-boot', 'start_ticks': 1})
    calls = []

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        if result is not None:
            write_json(job.root / 'output/result.json', result)
        if close_grant:
            write_json(job.tmp / 'ledger.json', {'status': 'CLOSED', 'entries': []})
            write_json(job.tmp / 'owner-stop.json', {'status': 'CLOSED'})
        return SimpleNamespace(pid=424242, poll=lambda: 0, wait=lambda: 0)

    monkeypatch.setattr(jobs.subprocess, 'Popen', popen)
    code = jobs._supervise(job.root, 'offline-claim')
    state = json.loads((job.root / 'output/state.json').read_text(encoding='utf-8'))
    return code, state, calls


@pytest.mark.parametrize('result', [None, {}, {'runtime_complete': False},
    {'runtime_complete': True, 'provider_calls': 1},
    {'runtime_complete': True, 'provider_calls': 0, 'unknown': 1, 'unsettled': 0},
    {'runtime_complete': True, 'provider_calls': 0, 'unknown': 0, 'unsettled': 1}])
def test_exit_zero_missing_incomplete_or_unknown_result_never_succeeds(job, monkeypatch, result):
    code, state, calls = supervise_offline(job, monkeypatch, result)
    assert len(calls) == 1
    assert code != 0
    assert state['status'] == 'INCOMPLETE_UNCONFIRMED'
    assert (job.root / 'control/payload-started.json').is_file()
    # Re-entering the private supervisor cannot repeat the payload either.
    with pytest.raises(jobs.JobError):
        jobs._supervise(job.root, 'offline-claim')
    assert len(calls) == 1


def test_successful_acceptance_can_close_its_unchanged_grant_namespace(job, monkeypatch):
    code, state, calls = supervise_offline(job, monkeypatch, {
        'runtime_complete': True, 'unknown': 0, 'unsettled': 0}, close_grant=True)
    assert code == 0
    assert state['status'] == 'SUCCEEDED'
    assert len(calls) == 1
    assert json.loads((job.tmp / 'ledger.json').read_text())['status'] == 'CLOSED'
    assert (job.tmp / 'owner-stop.json').exists()
    assert (job.root / 'output/artifacts.json').is_file()


def test_complete_zero_provider_result_saves_terminal_state_and_artifacts(job, monkeypatch):
    code, state, calls = supervise_offline(job, monkeypatch, {
        'runtime_complete': True, 'provider_calls': 0})
    assert code == 0 and state['status'] == 'SUCCEEDED'
    assert state['exit_code'] == 0 and state['result_valid'] is True
    assert len(calls) == 1
    _, options = calls[0]
    assert options['start_new_session'] is True
    assert options['stdin'] == subprocess.DEVNULL
    assert options['stdout'].closed and options['stderr'].closed
    artifacts = json.loads((job.root / 'output/artifacts.json').read_text(encoding='utf-8'))
    assert artifacts['status'] == 'SUCCEEDED'
    assert 'output/result.json' in artifacts['files']
    assert 'output/state.json' in artifacts['files']


def test_launcher_preserves_opaque_grant_and_does_not_interpret_payload_authorization(job):
    grant_path = job.tmp / 'owner-grant.json'
    grant_path.write_text('opaque grant interpreted only by the acceptance payload\n', encoding='utf-8')
    ledger = job.tmp / 'ledger.json'
    write_json(ledger, {'status': 'OPEN', 'entries': [{'status': 'UNKNOWN'}]})
    stop = write_json(job.tmp / 'owner-stop.json', {'status': 'CLOSED'})
    job.manifest.update(kind='acceptance', grant_path=str(grant_path),
                        grant_sha256=hashlib.sha256(grant_path.read_bytes()).hexdigest())
    write_json(job.manifest_path, job.manifest)
    before = {path: path.read_bytes() for path in (grant_path, ledger, stop)}
    assert prepare(job)['status'] == 'PREPARED'
    assert all(path.read_bytes() == original for path, original in before.items())


def test_spawn_exception_remains_claimed_and_cannot_retry(job, monkeypatch):
    prepare(job)
    monkeypatch.setattr(jobs.sys, 'platform', 'linux')
    calls = []

    def fail_spawn(*args, **kwargs):
        calls.append(args)
        raise OSError('simulated local spawn failure')

    monkeypatch.setattr(jobs.subprocess, 'Popen', fail_spawn)
    with pytest.raises(jobs.JobError):
        jobs.start_job(job.root, handshake_timeout=.001)
    assert jobs.job_status(job.root)['status'] == 'INTERRUPTED_UNCONFIRMED'
    assert (job.root / 'control/started.json').is_file()
    with pytest.raises(jobs.JobError):
        jobs.start_job(job.root, handshake_timeout=.001)
    assert len(calls) == 1


def test_job_json_metadata_still_requires_an_object(job):
    path = job.tmp / 'invalid-metadata.json'
    path.write_text('[]', encoding='utf-8')
    with pytest.raises(jobs.JobError, match='object'):
        jobs._json(path)


def test_large_json_and_artifact_index_support_status_and_fetch_without_size_caps(job):
    prepare(job)
    artifacts = write_json(job.root / 'output/artifacts.json', {
        'files': jobs._file_manifest(job.root), 'metadata_padding': 'x' * (1024 * 1024)})
    assert artifacts.stat().st_size > 1024 * 1024
    assert jobs.job_status(job.root)['artifact_integrity'] == 'MATCH'
    destination = job.tmp / 'large-index-fetch'
    receipt = jobs.fetch_job(job.root, destination)
    assert receipt['all_files_stable'] is True
    assert receipt['status_observation']['artifact_integrity'] == 'MATCH'
    assert jobs.sha256_file(destination / 'output/artifacts.json') == jobs.sha256_file(artifacts)
    ordinary = write_json(job.tmp / 'ordinary-control.json', {'padding': 'x' * (1024 * 1024)})
    assert len(jobs._json(ordinary)['padding']) == 1024 * 1024


@pytest.mark.skipif(sys.platform != 'linux', reason='real detached child/session and /proc proof require Linux')
def test_real_probe_survives_launcher_exit_with_logs_checkpoint_and_result(job, monkeypatch):
    monkeypatch.setattr(jobs, '_boot_id', job.boot_id_reader)
    job.payload.write_text(
        'import json, os, pathlib, sys, time\n'
        'out = pathlib.Path(os.environ["ECS_ACCEPTANCE_OUTPUT_DIR"])\n'
        'time.sleep(1.5)\n'
        '(out / "checkpoint.json").write_text(json.dumps({"completed": 1}))\n'
        'print("stdout-after-launcher-exit", flush=True)\n'
        'print("stderr-after-launcher-exit", file=sys.stderr, flush=True)\n'
        '(out / "result.json").write_text(json.dumps({"runtime_complete": True, "provider_calls": 0}))\n', encoding='utf-8')
    job.manifest['payload_sha256'] = hashlib.sha256(job.payload.read_bytes()).hexdigest()
    write_json(job.manifest_path, job.manifest)
    prepare(job)
    launcher = (
        'from pathlib import Path; from scripts import ecs_acceptance_job as j; '
        f'j.DIAGNOSTICS_ROOT=Path({str(job.diagnostics)!r}); '
        f'j.WORKSPACE_ROOT=Path({str(job.tmp)!r}); '
        f'print(j.start_job(Path({str(job.root)!r}), handshake_timeout=5))')
    completed = subprocess.run([sys.executable, '-B', '-c', launcher],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = jobs.job_status(job.root)
        if state['status'] in jobs.TERMINAL_STATES:
            break
        time.sleep(.1)
    assert state['status'] == 'SUCCEEDED', state
    assert (job.root / 'output/checkpoint.json').is_file()
    assert 'stdout-after-launcher-exit' in (job.root / 'output/stdout.log').read_text()
    assert 'stderr-after-launcher-exit' in (job.root / 'output/stderr.log').read_text()
    assert json.loads((job.root / 'output/result.json').read_text())['provider_calls'] == 0
    assert (job.root / 'output/artifacts.json').is_file()
