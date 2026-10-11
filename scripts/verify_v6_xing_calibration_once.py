"""One fixed calibration task; identity admission precedes config/DB/network.

SIMULATED composition is exposed for directed contracts, never a live fallback.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import sqlite3
import time
from uuid import uuid4

from backend.app.adapters.models.cloud import (
    XING_CALIBRATION_DEADLINE, XING_CALIBRATION_REQUEST_ID, XING_CALIBRATION_SCOPE,
    XING_CALIBRATION_WIRE_SHA256, XingAuthorizedTransport, XingCalibrationPermit,
    XingChatGrant, XingCohortCalibrationClaimStore, XingZeroPriceContract, SimulatedXingSend,
    XingCalibrationIdentity, LEGACY_XING_CALIBRATION_IDENTITY, XING_OWNER_CALIBRATION_REQUEST_ID,
)
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.application.budget import InMemoryBudgetGate, PostgresBudgetGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.rule_router_v1 import V6XingGenerationBinding
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderRequestNotSent

TASK_ID = 'RAG-V6-XING-LIVE-CALIBRATION'
RUN_ID = 'v6-xing-calibration-20261009-r1'
PUBLIC_PROMPT = 'Reply exactly OK.'
REPORT_ROOT = Path(__file__).resolve().parents[1] / 'var/reports/v6-xing-live-calibration-r1'
REPAIR_ROOT = REPORT_ROOT / 'identity-repair1'
MEETING_ROOT = REPORT_ROOT / 'normal-config-meet1'
ORIGINAL_MAPPING_SHA256 = 'da2f8313741ebc74dc4ead830b482c6a8ddb31ddd9c3843ac0d84be2438aa96b'
EXISTING_V6_CENTER = REPORT_ROOT.parents[2] / 'var/reports/v6-first-evaluation-20261010/run_ledger.sqlite3'
SOURCE_MEETING_R2 = REPORT_ROOT / 'source-meeting-r2'
SOURCE_MEETING_R3 = REPORT_ROOT / 'source-meeting-r3'
R2_SOURCE_NAMES = frozenset({
    'backend/app/adapters/models/cloud.py','scripts/verify_v6_xing_calibration_once.py',
    'backend/app/adapters/models/siliconflow_xing_capacity.py','backend/app/ports/xing_zero_reservation.py',
    'backend/app/application/provider_usage.py','backend/app/application/budget.py',
    'backend/app/adapters/models/factory.py','backend/app/application/rule_router_v1.py',
    'backend/app/ports/provider_send_claims.py','backend/app/env_loader.py','scripts/with_clean_slate.py',
    'deploy/clean-slate/profile.json','scripts/verify_p4_cloud_embedding.py','scripts/verify_p4_rerank_once.py',
})


def identity_fields():
    return dict(requested_run_id=RUN_ID, frozen_run_id=XingCohortCalibrationClaimStore.run_id,
        frozen_request_id=XING_CALIBRATION_REQUEST_ID, frozen_scope=XING_CALIBRATION_SCOPE,
        frozen_wire_sha256=XING_CALIBRATION_WIRE_SHA256, deadline_utc=XING_CALIBRATION_DEADLINE,
        model='XingChenAGI/Xing4.0-29B', purpose='calibration', output_limit=32, timeout_seconds=10)


def admit_identity(identity=None, *, now=None):
    """The coordinator froze a request, not an equality between two ID types."""
    if type(identity) is not XingCalibrationIdentity or identity.request_id != XING_OWNER_CALIBRATION_REQUEST_ID:
        return 'CALIBRATION_IDENTITY_MAPPING_NOT_FROZEN'
    try: identity.validate()
    except Exception: return 'CALIBRATION_IDENTITY_MAPPING_INVALID'
    now = now or datetime.now(timezone.utc)
    deadline = datetime.fromisoformat(XING_CALIBRATION_DEADLINE.replace('Z', '+00:00'))
    if now.tzinfo is None or now.timestamp() + 10 > deadline.timestamp():
        return 'ABSOLUTE_DEADLINE_EXPIRED_OR_TIMEOUT_EXCEEDS_REMAINING'
    return None


def freeze_identity(path, *, state_path):
    """Create once; every restart reads the same UUID/request mapping."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        record = dict(task_id=TASK_ID, revision=1, attempt=1, request_id=XING_OWNER_CALIBRATION_REQUEST_ID,
            run_id=str(uuid4()), scope=XING_CALIBRATION_SCOPE, arm='CHEAP_ONLY',
            head=(REPORT_ROOT/'head.txt').read_text(encoding='utf-8-sig').strip(),
            grant_id='owner-v6-xing-calibration-20261009-r1', deadline_utc=XING_CALIBRATION_DEADLINE,
            wire_sha256=XING_CALIBRATION_WIRE_SHA256, state_path=str(Path(state_path).resolve()),
            source_hashes={name:hashlib.sha256((REPORT_ROOT.parents[2] / name).read_bytes()).hexdigest()
                for name in ('backend/app/adapters/models/cloud.py','scripts/verify_v6_xing_calibration_once.py',
                             'backend/app/adapters/models/siliconflow_xing_capacity.py')})
        try:
            with path.open('x',encoding='utf-8') as stream:
                json.dump(record,stream,indent=2); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError: pass
    record = json.loads(path.read_text(encoding='utf-8'))
    expected = dict(task_id=TASK_ID, revision=1, attempt=1, request_id=XING_OWNER_CALIBRATION_REQUEST_ID,
        scope=XING_CALIBRATION_SCOPE,arm='CHEAP_ONLY',grant_id='owner-v6-xing-calibration-20261009-r1',
        deadline_utc=XING_CALIBRATION_DEADLINE,wire_sha256=XING_CALIBRATION_WIRE_SHA256,
        state_path=str(Path(state_path).resolve()))
    if any(record.get(k) != v for k,v in expected.items()): raise ValueError('FROZEN_IDENTITY_MAPPING_MISMATCH')
    if set(record['source_hashes']) != {'backend/app/adapters/models/cloud.py','scripts/verify_v6_xing_calibration_once.py',
                                      'backend/app/adapters/models/siliconflow_xing_capacity.py'}:
        raise ValueError('FROZEN_SOURCE_PATH_INVALID')
    for name,digest in record['source_hashes'].items():
        if name not in ('backend/app/adapters/models/cloud.py','scripts/verify_v6_xing_calibration_once.py',
                        'backend/app/adapters/models/siliconflow_xing_capacity.py'):
            raise ValueError('FROZEN_SOURCE_PATH_INVALID')
        if hashlib.sha256((REPORT_ROOT.parents[2] / name).read_bytes()).hexdigest() != digest:
            raise ValueError('FROZEN_SOURCE_CHANGED')
    identity = XingCalibrationIdentity(record['run_id'],record['request_id'],record['scope'],record['arm'])
    identity.validate()
    return identity, record


def read_runtime_meeting(expected_sha256, *, revision=1):
    """Explicit reviewed source/config attachment; never replaces the old mapping.

    Only this already-recorded public center is allowed. The operator pins the
    complete attachment bytes; source hashes and the original mapping are then
    checked again before any registration, reserve or network operation.
    """
    if type(revision) is not int or revision not in (1,2,3):
        raise ValueError('RUNTIME_MEETING_REVISION_INVALID')
    root = SOURCE_MEETING_R3 if revision == 3 else SOURCE_MEETING_R2 if revision == 2 else MEETING_ROOT
    raw = (root/'runtime-binding.json').read_bytes()
    if (type(expected_sha256) is not str or not re_sha(expected_sha256)
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise ValueError('RUNTIME_MEETING_HASH_MISMATCH')
    mapping_raw=(REPAIR_ROOT/'identity-manifest.json').read_bytes()
    if hashlib.sha256(mapping_raw).hexdigest()!=ORIGINAL_MAPPING_SHA256:
        raise ValueError('ORIGINAL_CALIBRATION_MAPPING_CHANGED')
    original=json.loads(mapping_raw)
    meeting=json.loads(raw)
    for name in ('run_id','request_id','grant_id','scope','arm','deadline_utc','wire_sha256','head'):
        if meeting.get(name)!=original[name]:raise ValueError('RUNTIME_MEETING_IDENTITY_MISMATCH')
    if revision >= 2 and (type(meeting.get('revision')) is not int or meeting['revision']!=revision
            or meeting.get('budget')!=dict(monthly_cap_microunits=10,planned_reservation_microunits=0)
            or meeting.get('owner_total_deadline_utc')!='2026-10-10T07:44:02Z'):
        raise ValueError('RUNTIME_MEETING_REVISION_INVALID')
    if (meeting.get('original_mapping_sha256')!=ORIGINAL_MAPPING_SHA256
            or meeting.get('state_path')!=str(EXISTING_V6_CENTER.resolve())
            or not EXISTING_V6_CENTER.is_file()):
        raise ValueError('RUNTIME_MEETING_CENTER_MISMATCH')
    names={'backend/app/adapters/models/cloud.py','scripts/verify_v6_xing_calibration_once.py',
           'backend/app/adapters/models/siliconflow_xing_capacity.py'}
    if revision >= 2: names=R2_SOURCE_NAMES
    if set(meeting.get('source_hashes',{}))!=names:raise ValueError('FROZEN_SOURCE_PATH_INVALID')
    for name,digest in meeting['source_hashes'].items():
        if hashlib.sha256((REPORT_ROOT.parents[2]/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('FROZEN_SOURCE_CHANGED')
    identity=XingCalibrationIdentity(original['run_id'],original['request_id'],original['scope'],original['arm'])
    identity.validate()
    return identity,meeting,EXISTING_V6_CENTER.resolve()


def re_sha(value):
    return len(value)==64 and all(c in '0123456789abcdef' for c in value)


def compose_simulated(*, counter, callback, budget_gate, sender, price_html, clock, identity=LEGACY_XING_CALIBRATION_IDENTITY):
    """Use the frozen normal Factory/UsageGuard/Transport and native claim ABI.

    The old Run/request are fixtures only, explicitly SIMULATED. There is no
    boolean or sender injection which changes this grant to REAL.
    """
    if type(sender) is not SimulatedXingSend or type(budget_gate) is not InMemoryBudgetGate:
        raise ValueError('SIMULATED_FIXTURE_TYPES_REQUIRED')
    return compose_calibration(counter=counter,callback=callback,budget_gate=budget_gate,sender=sender,
        price_html=price_html,clock=clock,identity=identity,estimate_microunits=3)


def compose_calibration(*, counter, callback, budget_gate, price_html, clock, identity,
                        estimate_microunits, sender=None):
    simulated = type(sender) is SimulatedXingSend
    if not simulated and type(budget_gate) is not PostgresBudgetGate:
        raise ValueError('NORMAL_POSTGRES_MONTHLY_GATE_REQUIRED')
    if not simulated and admit_identity(identity,now=clock()) is not None:
        raise ValueError('OWNER_CALIBRATION_IDENTITY_REQUIRED')
    store = XingCohortCalibrationClaimStore(callback,identity=identity)
    permit = XingCalibrationPermit(XingZeroPriceContract.from_frozen_html(price_html), store)
    grant_id = ('owner-v6-xing-calibration-20261009-r1' if identity.request_id == XING_OWNER_CALIBRATION_REQUEST_ID
                else 'owner-v6-xing-calibration-r1')
    grant = XingChatGrant(grant_id, identity.scope,
        identity.scope, XING_CALIBRATION_DEADLINE, ('calibration',), 'SIMULATED' if simulated else 'REAL')
    transport = XingAuthorizedTransport(grant, sender=sender, clock=clock,
        calibration_permit=permit, send_claim_callback=callback)
    registry = ModelRegistry.frozen_defaults()
    guard = BudgetUsageGuard(budget_gate, registry.select('chat_cheap'), estimate_microunits,
        zero_price_permit=permit if estimate_microunits == 0 else None)
    binding = V6XingGenerationBinding(grant, counter, transport)
    gateway = ProviderFactory(registry, enabled_roles=('chat_cheap',),
        usage_guards={'chat_cheap': guard}).build('chat_cheap', xing_binding=binding)
    return gateway, transport


def invoke_simulated(gateway):
    if gateway.execution_kind != 'SIMULATED':
        raise ValueError('SIMULATED_FIXTURE_REQUIRED')
    with model_access('chat_cheap', allowed=True):
        return gateway.generate_with_xing_scope([{'role':'user','content':PUBLIC_PROMPT}],
            timeout_seconds=10, max_tokens=32, scope=XING_CALIBRATION_SCOPE,
            purpose='calibration', request_id=gateway.transport.calibration_permit.claim_store.identity.request_id)


def verify_frozen_sources():
    workspace = REPORT_ROOT.parents[2]
    manifest = workspace / 'var/reports/v6-xing-binding-r1/meet4/source-hashes.json'
    names = {'backend/app/adapters/models/factory.py',
        'backend/app/adapters/models/siliconflow_xing_capacity.py',
        'backend/app/ports/provider_send_claims.py'}
    rows = json.loads(manifest.read_text(encoding='utf-8'))
    return all(hashlib.sha256((workspace / row['path']).read_bytes()).hexdigest() == row['after_sha256']
               for row in rows if row['path'] in names) and names <= {row['path'] for row in rows}


def calibration_ledger_identity(database_url):
    """Existing P4 clean fee ledger, never a fresh A/B corpus database.

    The profile is a non-secret normal configuration source. Preserve its
    original identity and verify the actual server again before registering.
    Do not redirect an unrelated configured URL into this ledger.
    """
    from sqlalchemy.engine import make_url
    profile_path = REPORT_ROOT.parents[2] / 'deploy/clean-slate/profile.json'
    raw = profile_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != '17dc627ee3f2255f3c6b233ad0f574ebf5424d2875aefb0dcecbc994da552da0':
        raise ValueError('AUTHORITATIVE_CALIBRATION_LEDGER_PROFILE_CHANGED')
    profile = json.loads(raw)
    db = profile['database']
    parsed = make_url(database_url)
    if (parsed.get_backend_name() != 'postgresql'
            or (parsed.host, parsed.port, parsed.database, parsed.username) !=
                (db['host'], db['port'], db['name'], db['user'])):
        raise ValueError('AUTHORITATIVE_CALIBRATION_LEDGER_MISMATCH')
    return dict(host=db['host'], port=db['port'], database=db['name'], role=db['user'],
        oid=db['oid'], system_identifier=db['system_identifier'],
        migration_revision=profile['migration_revision'], profile_sha256=hashlib.sha256(raw).hexdigest())


def configuration_admission(settings, *, key_present, state_path, zero_price_permit=None):
    """Existing loader/guard; no default business-DB connection or quota reset."""
    missing = []
    if not settings.cloud_enabled: missing.append('GLOBAL_CLOUD_EGRESS_DISABLED')
    if not settings.chat_egress_enabled: missing.append('CHAT_EGRESS_DISABLED')
    if not key_present: missing.append('CONFIGURED_SILICONFLOW_CREDENTIAL_MISSING')
    zero_verified = False
    if settings.cloud_cost_estimate_microunits == 0 and zero_price_permit is not None:
        try:
            from backend.app.ports.xing_zero_reservation import validate_zero_permit
            validate_zero_permit(zero_price_permit, settings.model_registry().select('chat_cheap'))
            zero_verified = admit_identity(zero_price_permit.claim_store.identity) is None
        except Exception: pass
    if settings.cloud_cost_estimate_microunits <= 0 and not zero_verified:
        missing.append('POSITIVE_MONTHLY_RESERVATION_NOT_CONFIGURED')
    if settings.monthly_cloud_budget_microunits < 0 or settings.monthly_cloud_budget_microunits == 0 and not zero_verified:
        missing.append('MONTHLY_BUDGET_ZERO')
    spec=settings.model_registry().select('chat_cheap')
    if (spec.provider,spec.model_id,spec.base_url,spec.api_key_env) != (
            'siliconflow','XingChenAGI/Xing4.0-29B','https://api.siliconflow.cn/v1','SILICONFLOW_API_KEY'):
        missing.append('CONFIGURED_CHEAP_MODEL_NOT_OWNER_XING')
    try:
        safe_db = calibration_ledger_identity(settings.database_url)
    except Exception:
        safe_db = dict(identity='REJECTED_BY_EXISTING_PUBLIC_ISOLATION_GUARD')
        missing.append('NORMAL_BUDGET_DATABASE_NOT_PUBLIC_ISOLATED')
    if not Path(state_path).is_file(): missing.append('EXISTING_EVALUATION_CENTER_NOT_FOUND')
    return missing, safe_db


def prior_claim(state_path, request_id):
    if not Path(state_path).is_file(): return None
    with sqlite3.connect(Path(state_path).as_uri()+'?mode=ro',uri=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_provider_calls'").fetchone() is None: return None
        row = db.execute('SELECT run_id,status FROM paired_provider_calls WHERE request_id=?',(request_id,)).fetchone()
    return dict(run_id=row[0],status=row[1]) if row else None


def historical_runs_digest(state_path, own_run):
    with sqlite3.connect(Path(state_path).as_uri()+'?mode=ro',uri=True) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_runs'").fetchone() is None: rows=[]
        else:
            rows=db.execute('SELECT run_id,status,cohort_sha256 FROM paired_runs WHERE run_id<>? ORDER BY run_id',(own_run,)).fetchall()
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()


class RecordedResponse:
    def __init__(self,response,observed): self.response,self.observed=response,observed
    def __enter__(self): self.response.__enter__(); return self
    def __exit__(self,*args): return self.response.__exit__(*args)
    @property
    def headers(self): return self.response.headers
    def getcode(self): return self.response.getcode()
    def read(self,limit):
        raw=self.response.read(limit)
        self.observed.update(http_status=self.getcode(),response_bytes=len(raw),response_sha256=hashlib.sha256(raw).hexdigest())
        try:
            payload=json.loads(raw)
            self.observed['response_structure']=dict(top_type=type(payload).__name__,
                model_present=isinstance(payload,dict) and 'model' in payload,
                usage_present=isinstance(payload,dict) and isinstance(payload.get('usage'),dict),
                choices_count=len(payload['choices']) if isinstance(payload,dict) and isinstance(payload.get('choices'),list) else None)
            if isinstance(payload,dict):
                from backend.app.adapters.models.cloud import _usage
                self.observed['usage_actual']=_usage(payload)
                self.observed['response_actual_model']=payload.get('model','UNKNOWN')
                choices=payload.get('choices')
                finish=choices[0].get('finish_reason') if isinstance(choices,list) and choices and isinstance(choices[0],dict) else None
                self.observed['finish_reason']=finish if finish in ('stop','length','content_filter','tool_calls') else 'UNKNOWN'
        except Exception: self.observed['response_structure']={'json':'INVALID'}
        return raw


def execute_live(identity, manifest, state_path, settings, report):
    """One normal sender call; registry claim and the monthly gate remain owners."""
    from sqlalchemy import create_engine, text
    from eval_center.paired_state import register_run,SQLiteProviderSendClaims,CurrentProviderSendClaims,provider_send_claim_scope,provider_send_claim_record
    from backend.app.adapters.models.siliconflow_xing_capacity import SiliconFlowXingCapacityCounter
    from backend.app.adapters.models import cloud
    engine=create_engine(settings.database_url,connect_args={'connect_timeout':5})
    old_opener=cloud.build_opener
    observed=dict(provider_send_count=0,http_status=None,response_bytes=None,response_sha256=None)
    try:
        expected=calibration_ledger_identity(settings.database_url)
        with engine.connect() as db:
            actual=db.execute(text("SELECT current_database(),current_user,(SELECT oid FROM pg_database WHERE datname=current_database()),system_identifier::text FROM pg_control_system()")).one()
            if tuple(actual)!=(expected['database'],expected['role'],expected['oid'],expected['system_identifier']):
                raise ValueError('PUBLIC_MONTHLY_DATABASE_IDENTITY_MISMATCH')
            if db.execute(text('SELECT version_num FROM alembic_version')).scalar_one()!=expected['migration_revision']:
                raise ValueError('PUBLIC_MONTHLY_DATABASE_IDENTITY_MISMATCH')
            if db.execute(text("SELECT to_regclass('public.model_calls')")).scalar_one() is None:
                raise ValueError('NORMAL_MONTHLY_LEDGER_SCHEMA_MISSING')
            report['budget_database_identity']=expected
            if manifest.get('revision') in (2,3):
                snapshot=read_p4_budget_snapshot(db)
                report['budget_before']=snapshot
                if (settings.monthly_cloud_budget_microunits!=10 or settings.cloud_cost_estimate_microunits!=0
                        or snapshot['month']!='2026-10-01' or snapshot['unknown_rows']!=10
                        or snapshot['retained_reserved']!=10 or snapshot['calibration_rows']!=0
                        or snapshot['budget_history_fingerprint']!='47fd28298d1d3a7ac984d2e00469b7c8'):
                    raise ValueError('P4_EXISTING_BUDGET_HISTORY_MISMATCH')
        if prior_claim(state_path,identity.request_id) is not None:
            raise ValueError('FIXED_REQUEST_CLAIM_ALREADY_CONSUMED_NO_RETRY')
        counter=SiliconFlowXingCapacityCounter.from_directory('D:/RAG-v6-xing-capacity-r2')
        before=historical_runs_digest(state_path,identity.run_id)
        mapping_sha=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        register_run(state_path,run_id=identity.run_id,cohort_sha256=XING_CALIBRATION_WIRE_SHA256,
            source_manifest_sha256=mapping_sha,code_sha=manifest['head'],deadline_utc=XING_CALIBRATION_DEADLINE,
            expected_qids=[identity.request_id],execution_purpose='CALIBRATION')
        report['evaluation_center_registration']='CALIBRATION'
        if before!=historical_runs_digest(state_path,identity.run_id): raise ValueError('HISTORICAL_RUN_CHANGED')
        bound=SQLiteProviderSendClaims(state_path,identity.run_id,identity.request_id,identity.arm,XING_CALIBRATION_WIRE_SHA256)
        gateway,transport=compose_calibration(counter=counter,callback=CurrentProviderSendClaims(),
            budget_gate=PostgresBudgetGate(engine,settings.monthly_cloud_budget_microunits),
            price_html='D:/RAG-v6-xing-capacity-r2/siliconflow-models.html',clock=lambda:datetime.now(timezone.utc),
            identity=identity,estimate_microunits=settings.cloud_cost_estimate_microunits)
        class Recorder:
            def __init__(self,original): self.original=original
            def open(self,request,*,timeout):
                if observed['provider_send_count'] != 0: raise ProviderRequestNotSent('CALIBRATION_SECOND_SEND_DENIED')
                if hashlib.sha256(request.data).hexdigest()!=XING_CALIBRATION_WIRE_SHA256:
                    raise ProviderRequestNotSent('CALIBRATION_FINAL_WIRE_MISMATCH')
                transport.check_scope(scope=identity.scope,purpose='calibration',timeout_seconds=timeout)
                CurrentProviderSendClaims().check_before_send(request_id=identity.request_id)
                observed['provider_send_count']+=1
                observed['actual_wire_sha256']=hashlib.sha256(request.data).hexdigest()
                try: return RecordedResponse(self.original.open(request,timeout=timeout),observed)
                except Exception as exc:
                    from urllib.error import HTTPError
                    if isinstance(exc,HTTPError):
                        raw=exc.read(2_000_001)
                        observed.update(http_status=exc.code,response_bytes=len(raw),response_sha256=hashlib.sha256(raw).hexdigest(),
                            response_structure={'http_error':True})
                    raise
        cloud.build_opener=lambda *a,**k:Recorder(old_opener(*a,**k))
        started=time.perf_counter()
        try:
            with provider_send_claim_scope(bound),model_access('chat_cheap',allowed=True):
                result=gateway.generate_with_xing_scope([{'role':'user','content':PUBLIC_PROMPT}],
                    timeout_seconds=10,max_tokens=32,scope=identity.scope,purpose='calibration',request_id=identity.request_id)
            report.update(status='RESPONSE_VALIDATED',answer=result.text)
        except Exception as exc:
            report.update(status='NOT_SENT' if isinstance(exc,ProviderRequestNotSent) else 'UNKNOWN',error_type=type(exc).__name__)
        finally:
            report['elapsed_seconds']=time.perf_counter()-started
            report.update(observed,real_provider_calls=observed['provider_send_count'])
            report['provider_receipts']=gateway.receipts
            row=provider_send_claim_record(state_path,request_id=identity.request_id)
            report['provider_claim']=row
            report['transport_observation']=transport.attempts.get(identity.request_id)
            report['historical_runs_unchanged']=before==historical_runs_digest(state_path,identity.run_id)
            with engine.connect() as db:
                report['budget_after']=read_p4_budget_snapshot(db)
                reservation_id=row.get('result',{}).get('budget_reservation_id') if row else None
                if reservation_id:
                    budget_row=db.execute(text('''SELECT id::text,run_id::text,provider,model_name,purpose,
                        budget_month,reserved_cost_microunits,settled_cost_microunits,reservation_state
                        FROM model_calls WHERE id=CAST(:id AS uuid)'''),{'id':reservation_id}).mappings().first()
                    report['normal_budget_new_record']={k:(str(v) if k=='budget_month' else v) for k,v in budget_row.items()} if budget_row else None
    finally:
        cloud.build_opener=old_opener
        engine.dispose()


def read_p4_budget_snapshot(db):
    """Only fee metadata, never business data or a replacement ledger."""
    from sqlalchemy import text
    month=datetime.now(timezone.utc).date().replace(day=1)
    row=db.execute(text('''SELECT COUNT(*) AS rows,
        COUNT(*) FILTER (WHERE reservation_state='unknown') AS unknown_rows,
        COALESCE(SUM(CASE WHEN reservation_state IN ('reserved','unknown') THEN reserved_cost_microunits
            WHEN reservation_state='settled' THEN COALESCE(settled_cost_microunits,0) ELSE 0 END),0) AS retained_reserved,
        COUNT(*) FILTER (WHERE model_name='XingChenAGI/Xing4.0-29B' AND purpose='chat_cheap') AS calibration_rows,
        MD5(COALESCE(STRING_AGG(id::text || ':' || COALESCE(reservation_state,'') || ':' ||
            COALESCE(reserved_cost_microunits::text,'') || ':' || COALESCE(settled_cost_microunits::text,'NULL'),
            ',' ORDER BY id),'')) AS budget_history_fingerprint
        FROM model_calls WHERE budget_month=:month'''),{'month':month}).mappings().one()
    from decimal import Decimal
    fields={}
    for name in ('rows','unknown_rows','retained_reserved','calibration_rows'):
        value=row[name]
        if type(value) not in (int,Decimal) or int(value)!=value or value<0:
            raise ValueError('P4_BUDGET_METADATA_INVALID')
        fields[name]=int(value)
    return dict(month=month.isoformat(),**fields,budget_history_fingerprint=row['budget_history_fingerprint'])


def main(argv=None):
    from backend.app.config import Settings
    from backend.app.ports.providers import ProviderRequestNotSent
    from eval_center.cli import _database_path
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--runtime-binding-sha256')
    parser.add_argument('--runtime-revision',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--owner-calibration-egress',action='store_true',
        help='Owner-authorized process-only cloud/chat settings for this fixed calibration only')
    args=parser.parse_args(argv)
    output_root=(SOURCE_MEETING_R3 if args.runtime_revision==3 else SOURCE_MEETING_R2 if args.runtime_revision==2 else MEETING_ROOT) if args.runtime_binding_sha256 else REPAIR_ROOT
    output_root.mkdir(parents=True,exist_ok=True)
    report=dict(task_id=TASK_ID,revision=1,attempt=1,repair=1,status='BLOCKED',real_provider_calls=0,
        provider_send_count=0,actual_charge='UNKNOWN',production_ready=False,deadline_utc=XING_CALIBRATION_DEADLINE,
        http='NOT RUN',evaluation_center_registration='NOT RUN',provider_claim='NOT RUN',budget_database_identity='NOT RUN',
        supervision='MANUALLY_SUPERVISED_TRIAL',observed_at_utc=datetime.now(timezone.utc).isoformat())
    try:
        if args.runtime_revision>=2 and not args.runtime_binding_sha256:
            raise ValueError('RUNTIME_MEETING_HASH_REQUIRED')
        if args.runtime_binding_sha256:
            identity,manifest,state=read_runtime_meeting(args.runtime_binding_sha256,revision=args.runtime_revision)
        else:
            state=(_database_path() if _database_path().is_absolute() else REPORT_ROOT.parents[2]/_database_path()).resolve()
            if not state.is_relative_to(REPORT_ROOT.parents[2]): raise ValueError('EVALUATION_CENTER_OUTSIDE_WORKSPACE')
            identity,manifest=freeze_identity(REPAIR_ROOT/'identity-manifest.json',state_path=state)
        report['identity']=manifest
        if args.runtime_revision>=2:
            report.update(revision=args.runtime_revision,owner_total_deadline_utc='2026-10-10T07:44:02Z',
                original_started_utc='2026-10-09T20:25:44Z',permit_deadline_unchanged=True)
        blocked=admit_identity(identity)
        if not verify_frozen_sources(): blocked='PROTECTED_CALIBRATION_SOURCE_CHANGED'
        consumed=prior_claim(state,identity.request_id)
        if consumed is not None:
            report['existing_consumed_claim']=consumed
            blocked='FIXED_REQUEST_CLAIM_ALREADY_CONSUMED_NO_RETRY'
        if blocked is None:
            from backend.app.env_loader import load_backend_env
            load_backend_env()
            settings=Settings.from_env()
            if args.runtime_revision>=2:
                from dataclasses import replace
                from scripts.with_clean_slate import resolve_environment
                profile=json.loads((REPORT_ROOT.parents[2]/'deploy/clean-slate/profile.json').read_bytes())
                child=resolve_environment(profile,os.environ.copy())
                if not os.environ.get('SILICONFLOW_API_KEY','').strip() and child.get('SILICONFLOW_API_KEY','').strip():
                    os.environ['SILICONFLOW_API_KEY']=child['SILICONFLOW_API_KEY']
                settings=replace(settings,database_url=child['RAG_DATABASE_URL'],
                    monthly_cloud_budget_microunits=10,cloud_cost_estimate_microunits=0)
                report['process_only_p4_budget_reuse']=True
            if args.owner_calibration_egress:
                from dataclasses import replace
                settings=replace(settings,cloud_enabled=True,chat_egress_enabled=True)
            # The normal SF adapter reads this same configured env; never print the value.
            key_present=bool(os.environ.get('SILICONFLOW_API_KEY','').strip())
            zero_permit = None
            if settings.cloud_cost_estimate_microunits == 0:
                from eval_center.paired_state import CurrentProviderSendClaims
                zero_permit = XingCalibrationPermit(
                    XingZeroPriceContract.from_frozen_html('D:/RAG-v6-xing-capacity-r2/siliconflow-models.html'),
                    XingCohortCalibrationClaimStore(CurrentProviderSendClaims(), identity=identity))
            missing,safe_db=configuration_admission(settings,key_present=key_present,state_path=state,
                zero_price_permit=zero_permit)
            report.update(configuration_loader='load_backend_env -> Settings.from_env',
                process_only_owner_calibration_egress=args.owner_calibration_egress,
                configured_sf_credential_present=key_present,
                missing=missing,configured_public_database=safe_db,
                configured_monthly_budget_microunits=settings.monthly_cloud_budget_microunits,
                configured_reservation_microunits=settings.cloud_cost_estimate_microunits)
            if missing: blocked=missing[0]
            elif args.live:
                execute_live(identity,manifest,state,settings,report)
            else: blocked='LIVE_NOT_REQUESTED'
        report['blocked_field']=blocked
    except Exception as exc:
        safe_codes={'EVALUATION_CENTER_OUTSIDE_WORKSPACE','FROZEN_IDENTITY_MAPPING_MISMATCH','FROZEN_SOURCE_CHANGED',
            'FROZEN_SOURCE_PATH_INVALID','PUBLIC_MONTHLY_DATABASE_IDENTITY_MISMATCH','NORMAL_MONTHLY_LEDGER_SCHEMA_MISSING',
            'HISTORICAL_RUN_CHANGED','RUNTIME_MEETING_HASH_MISMATCH','ORIGINAL_CALIBRATION_MAPPING_CHANGED',
            'RUNTIME_MEETING_IDENTITY_MISMATCH','RUNTIME_MEETING_CENTER_MISMATCH'}
        report.update(status='BLOCKED',blocked_field=str(exc) if str(exc) in safe_codes else type(exc).__name__)
    with (output_root/'executions.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(report)+'\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps(dict(status=report['status'],blocked_field=report.get('blocked_field'),
        real_provider_calls=report['real_provider_calls'],report=str(output_root/'executions.jsonl'))))
    return 0 if report['status']=='RESPONSE_VALIDATED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
