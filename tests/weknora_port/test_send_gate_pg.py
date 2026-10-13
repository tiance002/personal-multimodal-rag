"""Actual isolated PG ledger, synthetic authorities, HTTP/model calls always0."""
import json,uuid
from dataclasses import asdict,replace
from datetime import datetime,timezone
from pathlib import Path
import pytest

from test_memory_storage_pg import approved_dsn,actual_pg_run,connection
from test_send_gate import Permissions,Costs,call
from backend.weknora_port.models.types import TokenUsage,ProviderError
from backend.weknora_port.storage.send_gate import P1SendGate,MonthlyAuthority,ActualCost
from backend.weknora_port.storage.send_gate_migrate import apply_send_gate_schema


def save_evidence(case,value):
    path=Path(__file__).resolve().parents[2]/'docs/weknora-port/evidence/RESUME_P1_SEND_GATE_EVIDENCE.json'
    saved=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'kind':'actual-isolated-pg-synthetic-send-gate','model_calls':0,'http_sends':0,'old_database_writes':0,'cases':{}}
    saved['cases'][case]=value
    path.write_text(json.dumps(saved,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')


async def setup(dsn,budget=100,carry=10):
    conn,guard=await connection(dsn)
    digest=await apply_send_gate_schema(guard)
    month=(await (await conn.execute("SELECT (date_trunc('month',now() AT TIME ZONE 'UTC'))::date AS month")).fetchall())[0]['month']
    account='fixture-send-gate-'+str(uuid.uuid4())
    costs=Costs(authority=MonthlyAuthority(account,month,budget,carry,'KNOWN','explicit-isolated-fixture-carry','fixture-monthly-no-http'))
    gate=P1SendGate(guard,tenant_id=2000000002,permissions=Permissions(),costs=costs)
    return conn,guard,gate,digest


def test_actual_pg_monthly_reserved_unknown_settled_release_replay_and_carry():
    dsn=approved_dsn()
    async def run():
        conn,guard,gate,digest=await setup(dsn)
        ids=[]
        try:
            first=call('fixture-'+str(uuid.uuid4()));ids.append(first.context.request_id)
            await gate.authorize(first)
            with pytest.raises(ProviderError,match='REUSE_DENIED'):await gate.authorize(first)
            await gate.finish(first,outcome='UNKNOWN',usage=TokenUsage())
            with pytest.raises(ProviderError,match='CANNOT_RELEASE'):await gate.finish(first,outcome='NOT_SENT',usage=TokenUsage())
            second=call('fixture-'+str(uuid.uuid4()));ids.append(second.context.request_id)
            await gate.authorize(second);await gate.finish(second,outcome='RESPONSE_RECEIVED',usage=TokenUsage(1,1,2,'REPORTED'))
            await gate.finish(second,outcome='RESPONSE_RECEIVED',usage=TokenUsage(1,1,2,'REPORTED'))
            third=call('fixture-'+str(uuid.uuid4()));ids.append(third.context.request_id)
            await gate.authorize(third);await gate.finish(third,outcome='NOT_SENT',usage=TokenUsage())
            fourth=call('fixture-'+str(uuid.uuid4()));ids.append(fourth.context.request_id)
            await gate.authorize(fourth)
            # 10 carry +30UNKNOWN +12actual +30reserved=82; another30 denied.
            denied=call('fixture-'+str(uuid.uuid4()))
            with pytest.raises(ProviderError,match='BUDGET_EXCEEDED'):await gate.authorize(denied)
            rows=await (await conn.execute('SELECT * FROM send_calls WHERE account_id=%s ORDER BY created_at',(gate.costs.authority.account_id,))).fetchall()
            assert [row['state'] for row in rows]==['unknown','settled','released','reserved']
            assert rows[0]['reserved_microunits']==30 and rows[0]['actual_microunits'] is None and rows[0]['usage']['prompt_tokens'] is None
            assert rows[1]['actual_microunits']==12 and rows[2]['actual_microunits'] is None
            identity=await guard.verify_identity()
            save_evidence('ledger_states',{'database':identity.database,'system_identifier':identity.system_identifier,'migration_sha256':digest,'account_id':gate.costs.authority.account_id,'carry_in':10,'used_with_carry':82,'records':rows,'duplicate_authorize':'denied','unknown_release':'denied','next_call_exceeds_budget':'denied','assertions':'PASS','retained':'synthetic UNKNOWN records in new DB; no model/HTTP send'})
        finally:await conn.close()
    actual_pg_run(run())


def test_actual_pg_overrun_remains_unknown_committed_and_blocks_further_admission():
    dsn=approved_dsn()
    async def run():
        conn,guard,gate,digest=await setup(dsn)
        try:
            prepared=call('fixture-'+str(uuid.uuid4()));await gate.authorize(prepared)
            gate.costs.actual_quote=ActualCost(31,'fixture-price-v1')
            with pytest.raises(ProviderError,match='EXCEEDS_RESERVATION'):await gate.finish(prepared,outcome='RESPONSE_RECEIVED',usage=TokenUsage(1,1,2,'REPORTED'))
            row=(await (await conn.execute('SELECT state,reserved_microunits,actual_microunits FROM send_calls WHERE request_id=%s',(prepared.context.request_id,))).fetchall())[0]
            assert row==dict(state='unknown',reserved_microunits=30,actual_microunits=None)
            with pytest.raises(ProviderError,match='RECONCILIATION_REQUIRED'):await gate.authorize(call('fixture-'+str(uuid.uuid4())))
            budget=(await (await conn.execute('SELECT * FROM send_budget_months WHERE account_id=%s',(gate.costs.authority.account_id,))).fetchall())[0]
            save_evidence('overrun_block',{'system_identifier':(await guard.verify_identity()).system_identifier,'account_id':gate.costs.authority.account_id,'request_id':prepared.context.request_id,'record':row,'budget':budget,'assertions':'PASS','retained':'synthetic fixture; no model/HTTP call'})
        finally:await conn.close()
    actual_pg_run(run())


def test_actual_pg_month_lock_serializes_two_reservations_before_any_send():
    dsn=approved_dsn()
    async def run():
        import asyncio
        conn,guard,gate,digest=await setup(dsn,budget=50,carry=10)
        other,other_guard=await connection(dsn)
        other_gate=P1SendGate(other_guard,tenant_id=2000000002,permissions=Permissions(),costs=gate.costs)
        try:
            prepared=(call('fixture-'+str(uuid.uuid4())),call('fixture-'+str(uuid.uuid4())))
            outcomes=await asyncio.gather(gate.authorize(prepared[0]),other_gate.authorize(prepared[1]),return_exceptions=True)
            assert sum(value is None for value in outcomes)==1
            assert sum(isinstance(value,ProviderError) and value.code=='MONTHLY_BUDGET_EXCEEDED' for value in outcomes)==1
            count=(await (await conn.execute('SELECT count(*) AS count FROM send_calls WHERE account_id=%s',(gate.costs.authority.account_id,))).fetchall())[0]['count']
            assert count==1
            save_evidence('month_lock_two_connections',{'system_identifier':(await guard.verify_identity()).system_identifier,'account_id':gate.costs.authority.account_id,'budget':50,'carry_in':10,'each_reservation':30,'actual_reserved_rows':count,'admitted':1,'denied':1,'assertions':'PASS','http_sends':0})
        finally:await other.close();await conn.close()
    actual_pg_run(run())
