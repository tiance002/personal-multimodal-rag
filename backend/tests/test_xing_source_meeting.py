"""Source revision contracts, all isolated records/SIMULATED SQL/response."""
import hashlib,json
from pathlib import Path
import pytest
from scripts import verify_v6_xing_calibration_once as calibration


def meeting_fixture(tmp_path,monkeypatch):
    workspace=tmp_path/'SIMULATED-project'
    report=workspace/'var/reports/calibration';original_root=report/'identity-repair1'
    meeting_root=report/'source-meeting-r2'
    original_root.mkdir(parents=True);meeting_root.mkdir()
    center=workspace/'var/reports/existing-center.sqlite3';center.parent.mkdir(exist_ok=True);center.touch()
    source=workspace/'SIMULATED.py';source.write_text('# SIMULATED source\n')
    original=dict(run_id='b82329fe-b0ee-493c-9498-0aefdb5fc22e',request_id='v6-xing-calibration-20261009-r1',
        grant_id='owner-v6-xing-calibration-20261009-r1',scope=calibration.XING_CALIBRATION_SCOPE,
        arm='CHEAP_ONLY',deadline_utc=calibration.XING_CALIBRATION_DEADLINE,
        wire_sha256=calibration.XING_CALIBRATION_WIRE_SHA256,head='befd5478a680ffa779a1e8ede736260d9bac1060')
    raw=json.dumps(original).encode();(original_root/'identity-manifest.json').write_bytes(raw)
    digest=hashlib.sha256(raw).hexdigest()
    meeting=dict(original,revision=2,owner_total_deadline_utc='2026-10-10T07:44:02Z',
        state_path=str(center.resolve()),original_mapping_sha256=digest,
        budget=dict(monthly_cap_microunits=10,planned_reservation_microunits=0),
        source_hashes={'SIMULATED.py':hashlib.sha256(source.read_bytes()).hexdigest()})
    monkeypatch.setattr(calibration,'REPORT_ROOT',report)
    monkeypatch.setattr(calibration,'REPAIR_ROOT',original_root)
    monkeypatch.setattr(calibration,'SOURCE_MEETING_R2',meeting_root)
    monkeypatch.setattr(calibration,'EXISTING_V6_CENTER',center)
    monkeypatch.setattr(calibration,'ORIGINAL_MAPPING_SHA256',digest)
    monkeypatch.setattr(calibration,'R2_SOURCE_NAMES',frozenset({'SIMULATED.py'}))
    def save():
        raw=json.dumps(meeting).encode();(meeting_root/'runtime-binding.json').write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()
    return original,meeting,source,save


def test_new_source_revision_keeps_identity_old_deadline_and_exact_budget(tmp_path,monkeypatch):
    original,meeting,_,save=meeting_fixture(tmp_path,monkeypatch)
    identity,record,state=calibration.read_runtime_meeting(save(),revision=2)
    assert record['deadline_utc']==original['deadline_utc']=='2026-10-10T04:25:44Z'
    assert identity.run_id==original['run_id'] and identity.request_id==original['request_id']
    assert record['budget']==dict(monthly_cap_microunits=10,planned_reservation_microunits=0)


@pytest.mark.parametrize('condition',['changed_deadline','changed_request','changed_run','higher_cap','bare_revision','wrong_source','wrong_outer_hash'])
def test_source_revision_denials_precede_configuration_or_reserve(tmp_path,monkeypatch,condition):
    _,meeting,source,save=meeting_fixture(tmp_path,monkeypatch)
    if condition=='changed_deadline':meeting['deadline_utc']='2026-10-10T07:44:02Z'
    if condition=='changed_request':meeting['request_id']+='-retry'
    if condition=='changed_run':meeting['run_id']='d95a0082-20a2-43e8-97bc-881f6b83343e'
    if condition=='higher_cap':meeting['budget']['monthly_cap_microunits']=11
    if condition=='bare_revision':meeting['revision']=2.0
    digest=save()
    if condition=='wrong_source':source.write_text('# changed SIMULATED\n')
    if condition=='wrong_outer_hash':digest='0'*64
    with pytest.raises(ValueError):calibration.read_runtime_meeting(digest,revision=2)


def test_budget_snapshot_reads_fee_metadata_only_simulated_sql():
    calls=[]
    class Result:
        def mappings(self):return self
        def one(self):return dict(rows=10,unknown_rows=10,retained_reserved=10,calibration_rows=0,budget_history_fingerprint='SIMULATED')
    class DB:
        def execute(self,sql,params):calls.append((str(sql),params));return Result()
    snapshot=calibration.read_p4_budget_snapshot(DB())
    assert snapshot['retained_reserved']==10 and snapshot['calibration_rows']==0
    assert len(calls)==1 and 'FROM model_calls' in calls[0][0]
    assert not any(word in calls[0][0] for word in ('INSERT','UPDATE','DELETE','rag_runs','chunks'))


@pytest.mark.parametrize('value',['10','10.5'])
def test_postgres_numeric_budget_is_serializable_without_rounding(value):
    from decimal import Decimal
    class Result:
        def mappings(self):return self
        def one(self):return dict(rows=10,unknown_rows=10,retained_reserved=Decimal(value),calibration_rows=0,budget_history_fingerprint='SIMULATED')
    class DB:
        def execute(self,*args):return Result()
    if value=='10':
        snapshot=calibration.read_p4_budget_snapshot(DB())
        assert json.loads(json.dumps(snapshot))['retained_reserved']==10
    else:
        with pytest.raises(ValueError,match='P4_BUDGET_METADATA_INVALID'):calibration.read_p4_budget_snapshot(DB())


def test_response_observation_records_usage_and_finish_simulated():
    class Response:
        def getcode(self):return 200
        def read(self,limit):return json.dumps({'model':'XingChenAGI/Xing4.0-29B',
            'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12},
            'choices':[{'finish_reason':'stop'}]}).encode()
    observed={};calibration.RecordedResponse(Response(),observed).read(2000)
    assert observed['usage_actual']==dict(prompt_tokens=10,completion_tokens=2,total_tokens=12)
    assert observed['finish_reason']=='stop' and observed['response_actual_model']=='XingChenAGI/Xing4.0-29B'
