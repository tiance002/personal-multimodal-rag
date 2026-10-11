"""SIMULATED OS/fsync/fence failure injection; real temp files only."""
from dataclasses import replace,asdict
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,os
import pytest
import backend.app.application.session_attempts as sessions
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.application.validation_usd_budget import V6OwnerActivityGate,OWNER_V6_TOKEN_GRANT,OWNER_V6_TOKEN_GRANT_R2
from backend.tests.test_v6_token_grant import _gate,_budget
from backend.tests.test_v6_ledger_authority import setup
from backend.app.ports.session_attempts import AttemptDenied


def marker(path,status='FENCED',epoch=2):
    return dict(schema_version=1,status=status,epoch=epoch,authority_id='SIMULATED-ecs-authority',ledger_path=str(path.resolve()))


@pytest.mark.parametrize('failure',[None,'open','fsync'])
def test_posix_directory_fsync_order_and_failure_never_success(tmp_path,monkeypatch,failure):
    path=tmp_path/'SIMULATED-ledger.json';path.write_bytes(b'old');gate=SessionAttemptGate(path);events=[]
    original=sessions.os
    class OS:
        name='posix';O_RDONLY=0;O_DIRECTORY=65536
        def __getattr__(self,key):return getattr(original,key)
        def replace(self,*args):events.append('replace');return original.replace(*args)
        def fsync(self,fd):
            events.append('dir-fsync' if fd==123456 else 'file-fsync')
            if fd==123456 and failure=='fsync':raise OSError('SIMULATED directory fsync failed')
            if fd!=123456:original.fsync(fd)
        def open(self,*args):
            events.append('dir-open')
            if failure=='open':raise OSError('SIMULATED directory open failed')
            return 123456
        def close(self,fd):events.append('dir-close')
    monkeypatch.setattr(sessions,'os',OS())
    if failure:
        with pytest.raises(OSError):gate._write_bytes(b'new-reservation')
    else:gate._write_bytes(b'new-reservation')
    assert events[:3]==['file-fsync','replace','dir-open']
    assert path.read_bytes()==b'new-reservation' # rename may already be committed; never pretend it rolled back.
    if failure!='open':assert events[-1]=='dir-close'


def test_windows_keeps_supported_replace_without_posix_directory_open(tmp_path,monkeypatch):
    gate=SessionAttemptGate(tmp_path/'SIMULATED.json');original=sessions.os
    class OS:
        name='nt'
        def __getattr__(self,k):return getattr(original,k)
        def open(self,*args):pytest.fail('POSIX dir-open on Windows')
    monkeypatch.setattr(sessions,'os',OS());gate._write_bytes(b'Windows fixture')
    assert gate.path.read_bytes()==b'Windows fixture'


def test_fenced_source_restart_denies_even_with_original_real_authorization(tmp_path):
    gate,path=_gate(tmp_path);before=path.read_bytes()
    Path(str(path)+'.authority.json').write_text(json.dumps(marker(path)))
    restarted=V6OwnerActivityGate(path,grant=gate.grant)
    with pytest.raises(AttemptDenied,match='FENCED'):restarted.reserve(request_id='SIMULATED-no-send',budget=_budget(restarted))
    with pytest.raises(AttemptDenied,match='FENCED'):SessionAttemptGate(path).check_authority_active()
    assert path.read_bytes()==before


def test_fsync_failure_in_normal_reserve_returns_no_ticket_and_keeps_consumed_state(tmp_path,monkeypatch):
    gate,path=_gate(tmp_path)
    def failure():raise OSError('SIMULATED fsync failure after rename')
    monkeypatch.setattr(gate,'_fsync_parent',failure)
    with pytest.raises(AttemptDenied):gate.reserve(request_id='SIMULATED-fsync-no-ticket',budget=_budget(gate))
    data=json.loads(path.read_text())
    assert data['v6_owner_activity']['attempts'][0]['status']=='reserved'
    assert gate._used(data)==65569+620
    with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):
        gate.reserve(request_id='SIMULATED-fsync-no-ticket',budget=_budget(gate))


def test_new_revision_preserves_unknown_occupancy_and_old_request_identity(tmp_path):
    old,path=_gate(tmp_path)
    request='SIMULATED-old-UNKNOWN';ticket=old.reserve(request_id=request,budget=_budget(old));old.finish(ticket,'unknown')
    before=json.loads(path.read_text());used=old._used(before)
    grant=replace(old.grant,version=2,grant_id='SIMULATED-r2',scope='SIMULATED-six-hour-r2',deadline_utc='2026-10-10T07:44:02Z')
    new=V6OwnerActivityGate(path,grant=grant,clock=lambda:datetime(2026,10,10,5,tzinfo=timezone.utc))
    new.activate_revision(old.grant);data=new._read()
    assert new._used(data)==used and data['attempts']==before['attempts']
    assert data['v6_owner_activity']['attempts']==before['v6_owner_activity']['attempts']
    assert data['v6_owner_activity']['grant_history']==[asdict(old.grant)]
    with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):new.reserve(request_id=request,budget=_budget(new))
    ticket=new.reserve(request_id='SIMULATED-new-r2',budget=_budget(new));new.finish(ticket,'unknown')
    assert new._used(new._read())==used+620
    assert OWNER_V6_TOKEN_GRANT.deadline_utc=='2026-10-10T04:25:44+00:00'
    assert OWNER_V6_TOKEN_GRANT_R2.deadline_utc=='2026-10-10T07:44:02+00:00'


@pytest.mark.parametrize('fault',['missing_marker','epoch','retired','fence_hash'])
def test_pinned_epoch_and_source_fence_rechecked_before_gate_write(tmp_path,fault):
    ledger,grant,path,manifest,load=setup(tmp_path)
    fence_path=tmp_path/'SIMULATED-source-fence.json';fence_path.write_text(json.dumps(marker(tmp_path/'source.json')))
    fence_sha=hashlib.sha256(fence_path.read_bytes()).hexdigest()
    active_path=Path(str(ledger)+'.authority.json');active=marker(ledger,'ACTIVE_SINGLE_WRITER');active['source_fence_marker_sha256']=fence_sha
    active_path.write_text(json.dumps(active))
    section=manifest['v6_deepseek_authority'];section.update(authority_epoch=2,marker_sha256=hashlib.sha256(active_path.read_bytes()).hexdigest(),
        source_fence_marker_path=str(fence_path.resolve()),source_fence_marker_sha256=fence_sha)
    path.write_text(json.dumps(manifest));authority=load();gate=V6OwnerActivityGate(ledger,grant=grant,authority=authority)
    before=ledger.read_bytes()
    if fault=='missing_marker':active_path.unlink()
    if fault=='epoch':active['epoch']=3;active_path.write_text(json.dumps(active))
    if fault=='retired':active['status']='FENCED';active_path.write_text(json.dumps(active))
    if fault=='fence_hash':fence_path.write_text('SIMULATED ALTERED FENCE')
    with pytest.raises(AttemptDenied):gate.reserve(request_id='SIMULATED-never-write',budget=_budget(gate))
    assert ledger.read_bytes()==before
