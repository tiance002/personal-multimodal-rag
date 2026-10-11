"""SIMULATED migration manifests/ledgers only; no canonical source access."""
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json
import pytest
from backend.tests.test_v6_token_grant import _ledger,_budget
from backend.app.application.validation_usd_budget import V6OwnerActivityGate,OwnerActivityTokenGrant
from backend.app.ports.deepseek_gate_types import V6LedgerAuthority
from backend.app.ports.session_attempts import AttemptDenied


NOW=datetime(2026,10,10,2,0,tzinfo=timezone.utc)


def setup(tmp_path,*,unknown=False,limit=1000000):
    ledger=_ledger(tmp_path)
    grant=replace(OwnerActivityTokenGrant.simulated(token_limit=limit,baseline_used_tokens=65569),baseline_ledger_sha256='a'*64)
    gate=V6OwnerActivityGate(ledger,grant=grant,clock=lambda:NOW);gate.initialize_grant()
    if unknown:
        attempt=gate.reserve(request_id='SIMULATED-old-unknown',budget=_budget(gate));gate.finish(attempt,'unknown')
    data=json.loads(ledger.read_text(encoding='utf-8'))
    manifest={'v6_deepseek_authority':dict(schema_version=1,authority_id='SIMULATED-ecs-authority',status='ACTIVE_SINGLE_WRITER',
        platform='SIMULATED-WINDOWS',execution_kind='SIMULATED',source_writer_fenced=True,ledger_path=str(ledger.resolve()),
        source_snapshot_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),source_baseline_sha256=grant.baseline_ledger_sha256,
        legacy_state_sha256=gate.legacy_state_sha256(data),initial_v6_attempts=data['v6_owner_activity']['attempts'])}
    path=tmp_path/'SIMULATED-authority.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    def load():return V6LedgerAuthority.from_manifest(path,expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),expected_authority_id='SIMULATED-ecs-authority')
    return ledger,grant,path,manifest,load


def test_explicit_existing_authority_retains_history_and_unknown_on_rebuild(tmp_path):
    ledger,grant,path,manifest,load=setup(tmp_path,unknown=True)
    before=ledger.read_bytes();authority=load()
    gate=V6OwnerActivityGate(ledger,grant=grant,authority=authority,clock=lambda:NOW)
    gate.initialize_grant()
    assert ledger.read_bytes()==before and gate._used(gate._read())==65569+620
    with pytest.raises(AttemptDenied,match='ALREADY_RESERVED'):
        gate.reserve(request_id='SIMULATED-old-unknown',budget=_budget(gate))
    again=V6OwnerActivityGate(ledger,grant=grant,authority=load(),clock=lambda:NOW)
    assert again._used(again._read())==65569+620


@pytest.mark.parametrize('condition',['wrong_path','missing','empty','old_authority','unfenced','bad_pin','bool_authority','real_on_windows'])
def test_wrong_or_unready_authority_cannot_initialize_or_reset(tmp_path,condition):
    ledger,grant,path,manifest,load=setup(tmp_path)
    before=ledger.read_bytes();section=manifest['v6_deepseek_authority']
    if condition=='old_authority':section['status']='RETIRED'
    if condition=='unfenced':section['source_writer_fenced']=False
    if condition=='missing':section['ledger_path']=str(tmp_path/'missing.json')
    if condition=='empty':ledger.write_bytes(b'')
    if condition=='real_on_windows':section.update(execution_kind='REAL',platform='linux')
    path.write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(AttemptDenied):
        authority=load()
        if condition=='bad_pin':authority=replace(authority,manifest_sha256='0'*64)
        if condition=='bool_authority':authority=True
        target=tmp_path/'wrong.json' if condition=='wrong_path' else ledger
        V6OwnerActivityGate(target,grant=grant,authority=authority,clock=lambda:NOW).initialize_grant()
    assert not (tmp_path/'missing.json').exists() and not (tmp_path/'wrong.json').exists()
    if condition!='empty':assert ledger.read_bytes()==before


def test_manifest_change_rejected_without_any_new_ledger_write(tmp_path):
    ledger,grant,path,manifest,load=setup(tmp_path)
    gate=V6OwnerActivityGate(ledger,grant=grant,authority=load(),clock=lambda:NOW)
    before=ledger.read_bytes();manifest['v6_deepseek_authority']['status']='RETIRED';path.write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(AttemptDenied):gate.reserve(request_id='SIMULATED-new',budget=_budget(gate))
    assert ledger.read_bytes()==before


@pytest.mark.parametrize('mutation',['unknown_removed','legacy_changed','calls_enabled','larger_grant'])
def test_migrated_history_and_cap_cannot_be_reset(tmp_path,mutation):
    ledger,grant,path,manifest,load=setup(tmp_path,unknown=True,limit=66500)
    authority=load();data=json.loads(ledger.read_text(encoding='utf-8'))
    if mutation=='unknown_removed':data['v6_owner_activity']['attempts']=[]
    if mutation=='legacy_changed':data['attempts'][0]['validation_tokens']['input']-=1
    if mutation=='calls_enabled':data['calls_allowed_in_this_task']=True
    if mutation=='larger_grant':grant=replace(grant,token_limit=1000000)
    ledger.write_text(json.dumps(data),encoding='utf-8');before=ledger.read_bytes()
    gate=V6OwnerActivityGate(ledger,grant=grant,authority=authority,clock=lambda:NOW)
    with pytest.raises(AttemptDenied):gate.reserve(request_id='SIMULATED-new',budget=_budget(gate))
    assert ledger.read_bytes()==before


def test_retained_unknown_occupancy_still_blocks_next_reservation(tmp_path):
    ledger,grant,path,manifest,load=setup(tmp_path,unknown=True,limit=66500)
    gate=V6OwnerActivityGate(ledger,grant=grant,authority=load(),clock=lambda:NOW)
    before=ledger.read_bytes()
    with pytest.raises(AttemptDenied,match='V6_TOKEN_CAP'):gate.reserve(request_id='SIMULATED-new',budget=_budget(gate))
    assert ledger.read_bytes()==before
