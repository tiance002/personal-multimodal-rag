"""Focused actual PG transaction, cross-unit claim and UNKNOWN contracts."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import uuid

import pytest
from backend.weknora_port.authority import AuthoritySettings,ConfiguredTokenAuthority,ServerPermissionResolver
from backend.weknora_port.models.types import CallContext,TrustedScope,ModelConfig,PreparedCall,TokenUsage,ProviderError
from backend.weknora_port.models.usage_facts import capture_usage
from backend.weknora_port.source_facts import bind_synthetic,sha
from backend.weknora_port.storage.send_gate import P1SendGate,MonthlyAuthority
from backend.weknora_port.storage.token_migrate import apply_token_schema
from tests.weknora_port.test_memory_storage_pg import approved_dsn,connection,actual_pg_run
from tests.weknora_port.test_send_gate import Costs


def save_case(name,values):
    target=Path(os.environ['WEKNORA_SEQ_IMAGE_RECEIPT']).with_suffix('.token-cases.jsonl')
    with target.open('a',encoding='utf-8') as f:
        f.write(json.dumps({'case':name,'actual_pg':True,'real_provider_calls':0,**values},default=str)+'\n')


class Leases:
    @asynccontextmanager
    async def lease(self):
        conn,guard=await connection(approved_dsn())
        try:yield guard
        finally:await conn.close()


@asynccontextmanager
async def setup():
    conn,guard=await connection(approved_dsn())
    try:
        await apply_token_schema(guard)
        tag=uuid.uuid4().hex
        tenant=(await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',('authority-'+tag,'isolated-test'))).fetchone())['id']
        kb=str(uuid.uuid4())
        await conn.execute("INSERT INTO knowledge_bases(id,tenant_id,name,embedding_model_id,summary_model_id,rerank_model_id,cloud_allowed) VALUES(%s,%s,'authority','','','',true)",(kb,tenant))
        cfg=AuthoritySettings('synthetic','a'*64,tenant,'synthetic-'+tag,'synthetic-'+tag,
            datetime.now(timezone.utc)+timedelta(hours=1),'SYNTHETIC:pg-boundaries',datetime.now(timezone.utc).date().replace(day=1),
            0,'KNOWN','SYNTHETIC:new-account',(('deepseek','model-a'),('deepseek','model-b')),1000000,8192)
        permissions=ServerPermissionResolver(Leases(),cfg)
        authority=ConfiguredTokenAuthority(cfg)
        gate=P1SendGate(guard,tenant_id=tenant,permissions=permissions,token_authority=authority)
        yield conn,guard,cfg,kb,gate
    finally:await conn.close()


def make_call(cfg,kb,id=None,*,model='model-a',input_bound=90,output_bound=10):
    context=CallContext(id or str(uuid.uuid4()),TrustedScope(cfg.subject_id,(kb,)),session_id='synthetic-session')
    config=ModelConfig('deepseek',model,'snapshot-'+model,'https://fixture.invalid/v1')
    body={'model':model,'messages':[{'role':'user','content':'closed synthetic fixture'}],'max_tokens':output_bound}
    wire=json.dumps(body,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()
    facts=bind_synthetic(config,context,body,wire,cfg.config_sha,capability='chat')
    facts=replace(facts,bound=replace(facts.bound,input_upper=input_bound))
    return PreparedCall(context,'deepseek',model,config.model_id,'chat','https://fixture.invalid/v1/chat/completions',sha(wire),len(wire),output_bound,facts)


def test_unknown_overrun_holds_known_lower_and_block_commits_atomically():
    async def run():
        async with setup() as (conn,guard,cfg,kb,gate):
            call=make_call(cfg,kb)
            await gate.authorize(call)
            # Another real connection observes committed reservation before send.
            other,unused=await connection(approved_dsn())
            try:
                assert (await (await other.execute('SELECT held_tokens FROM port_token_calls WHERE call_id=%s',(gate._slot_id(call),))).fetchone())['held_tokens']==100
            finally:await other.close()
            usage=TokenUsage(provider_usage_facts=capture_usage({'prompt_tokens':150},simulated=True))
            with pytest.raises(ProviderError,match='EXCEEDS_RESERVATION'):
                await gate.finish(call,outcome='UNKNOWN',usage=usage)
            row=await (await conn.execute('SELECT * FROM port_token_calls WHERE call_id=%s',(gate._slot_id(call),))).fetchone()
            assert row['state']=='unknown' and row['held_tokens']==row['known_lower_bound']==150
            assert row['reserved_input']+row['reserved_output']==100 and row['actual_total'] is None
            with pytest.raises(ProviderError,match='EXCEEDS_RESERVATION'):
                await gate.finish(call,outcome='UNKNOWN',usage=TokenUsage())
            repeated=await (await conn.execute('SELECT held_tokens,usage_facts FROM port_token_calls WHERE call_id=%s',(gate._slot_id(call),))).fetchone()
            assert repeated['held_tokens']==150 and repeated['usage_facts']==row['usage_facts']
            blocked=await (await conn.execute('SELECT blocked_reason FROM port_token_budget_months WHERE account_id=%s',(cfg.account_id,))).fetchone()
            assert blocked['blocked_reason']=='USAGE_EXCEEDS_RESERVATION'
            save_case('partial-actual-overrun',{'row':row,'month':blocked})
            with pytest.raises(ProviderError,match='TOKEN_MONTH_BLOCKED'):
                await gate.authorize(make_call(cfg,kb))
    actual_pg_run(run())


def test_global_claim_released_and_changed_model_role_cannot_resend_or_switch_unit():
    async def run():
        async with setup() as (conn,guard,cfg,kb,gate):
            id=str(uuid.uuid4())+'/answer/1/cheap'
            first=make_call(cfg,kb,id)
            await gate.authorize(first)
            await gate.finish(first,outcome='NOT_SENT',usage=TokenUsage())
            await gate.finish(first,outcome='NOT_SENT',usage=TokenUsage())
            changed=make_call(cfg,kb,id.replace('/cheap','/expensive'),model='model-b')
            for call in (first,changed):
                with pytest.raises(ProviderError,match='REUSE_DENIED'):await gate.authorize(call)
            money=P1SendGate(guard,tenant_id=cfg.tenant_id,permissions=gate.permissions,costs=Costs())
            with pytest.raises(ProviderError,match='REUSE_DENIED'):await money.authorize(changed)
            # Reverse direction, existing money row (including released) denies Token.
            second=make_call(cfg,kb)
            cost=Costs(authority=MonthlyAuthority('synthetic-money-'+uuid.uuid4().hex,cfg.month,100,0,'KNOWN','SYNTHETIC:new','SYNTHETIC:old-unit'))
            money=P1SendGate(guard,tenant_id=cfg.tenant_id,permissions=gate.permissions,costs=cost)
            await money.authorize(second)
            await money.finish(second,outcome='NOT_SENT',usage=TokenUsage())
            with pytest.raises(ProviderError,match='REUSE_DENIED'):await gate.authorize(second)
            save_case('cross-unit-and-role-replay',{'first_slot':gate._slot_id(first),'changed_slot':gate._slot_id(changed),'money_request':second.context.request_id,'all_replays_denied':True})
    actual_pg_run(run())


def test_two_connections_cannot_oversubscribe_month_and_outer_transaction_denies():
    async def run():
        async with setup() as (conn,guard,cfg,kb,gate):
            async with conn.transaction():
                with pytest.raises(ProviderError,match='OWN_TRANSACTION'):await gate.authorize(make_call(cfg,kb))
            async def contender():
                other,repo=await connection(approved_dsn())
                try:
                    g=P1SendGate(repo,tenant_id=cfg.tenant_id,permissions=gate.permissions,token_authority=gate.token_authority)
                    try:await g.authorize(make_call(cfg,kb,input_bound=600000));return 'reserved'
                    except ProviderError as e:return e.code
                finally:await other.close()
            results=await asyncio.gather(contender(),contender())
            assert sorted(results)==['MONTHLY_TOKEN_LIMIT_EXCEEDED','reserved']
            save_case('two-connection-month-race',{'account':cfg.account_id,'results':results})
    actual_pg_run(run())


def test_cancel_consent_and_missing_usage_preserve_admission_boundaries():
    async def run():
        async with setup() as (conn,guard,cfg,kb,gate):
            call=make_call(cfg,kb)
            cancelled=replace(call,context=replace(call.context,cancelled=asyncio.Event()))
            cancelled.context.cancelled.set()
            with pytest.raises(ProviderError,match='CANCELLED'):await gate.authorize(cancelled)
            await conn.execute('UPDATE knowledge_bases SET cloud_allowed=false WHERE id=%s',(kb,))
            with pytest.raises(ProviderError,match='EGRESS_DENIED'):await gate.authorize(call)
            await conn.execute('UPDATE knowledge_bases SET cloud_allowed=true WHERE id=%s',(kb,))
            await gate.authorize(call)
            await gate.finish(call,outcome='UNKNOWN',usage=TokenUsage())
            row=await (await conn.execute('SELECT state,held_tokens FROM port_token_calls WHERE call_id=%s',(gate._slot_id(call),))).fetchone()
            assert row=={'state':'unknown','held_tokens':100}
            with pytest.raises(ProviderError,match='CANNOT_RELEASE'):await gate.finish(call,outcome='NOT_SENT',usage=TokenUsage())
            # Fresh process-equivalent connection has no in-memory claim state.
            other,repo=await connection(approved_dsn())
            try:
                restarted=P1SendGate(repo,tenant_id=cfg.tenant_id,permissions=gate.permissions,token_authority=gate.token_authority)
                with pytest.raises(ProviderError,match='REUSE_DENIED'):await restarted.authorize(call)
            finally:await other.close()
            save_case('cancel-unknown-restart',{'row':row,'restarted_replay_denied':True})
    actual_pg_run(run())


def test_consent_revocation_while_waiting_for_month_lock_is_rechecked():
    async def run():
        from backend.weknora_port.storage.token_budget import encode
        async with setup() as (conn,guard,cfg,kb,gate):
            blocker,unused=await connection(approved_dsn())
            call=make_call(cfg,kb)
            try:
                async with blocker.transaction():
                    await blocker.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                        ('weknora-token-month:'+encode([cfg.account_id,'deepseek',str(cfg.month)]),))
                    task=asyncio.create_task(gate.authorize(call))
                    for _ in range(100):
                        wait=await (await blocker.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",(conn.info.backend_pid,))).fetchone()
                        if wait and wait['wait_event_type']=='Lock':break
                        if task.done():await task
                        await asyncio.sleep(.01)
                    assert wait['wait_event_type']=='Lock'
                    await blocker.execute('UPDATE knowledge_bases SET cloud_allowed=false WHERE id=%s',(kb,))
                with pytest.raises(ProviderError,match='EGRESS_DENIED'):await task
                count=await (await conn.execute('SELECT count(*) AS n FROM port_token_calls WHERE tenant_id=%s',(cfg.tenant_id,))).fetchone()
                assert count['n']==0
                save_case('post-lock-consent-revocation',{'waiting':wait,'new_calls':count['n']})
            finally:
                if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
                await blocker.close()
    actual_pg_run(run())


def test_cross_unit_concurrent_claim_has_only_one_winner():
    async def run():
        async with setup() as (conn,guard,cfg,kb,gate):
            call=make_call(cfg,kb)
            cost=Costs(authority=MonthlyAuthority('synthetic-money-'+uuid.uuid4().hex,cfg.month,100,0,'KNOWN','SYNTHETIC:new','SYNTHETIC:unit-race'))
            async def contender(unit):
                other,repo=await connection(approved_dsn())
                try:
                    g=P1SendGate(repo,tenant_id=cfg.tenant_id,permissions=gate.permissions,
                        token_authority=gate.token_authority if unit=='tokens' else None,costs=cost if unit=='money' else None)
                    try:await g.authorize(call);return unit+'-reserved'
                    except ProviderError as e:return e.code
                finally:await other.close()
            results=await asyncio.gather(contender('tokens'),contender('money'))
            assert results.count('MODEL_CALL_ID_REUSE_DENIED')==1
            assert sum(r.endswith('-reserved') for r in results)==1
            save_case('cross-unit-simultaneous-claim',{'results':results,'slot':gate._slot_id(call)})
    actual_pg_run(run())


def test_component_bound_breach_and_expired_deadline_remain_conservative():
    async def run():
        async with setup() as (conn,guard,cfg,kb,gate):
            call=make_call(cfg,kb)
            expired=replace(call,context=replace(call.context,deadline=asyncio.get_running_loop().time()-1))
            with pytest.raises(ProviderError,match='ADMISSION_TIMEOUT'):await gate.authorize(expired)
            await gate.authorize(call)
            usage=TokenUsage(provider_usage_facts=capture_usage({'prompt_tokens':95,'completion_tokens':0,'total_tokens':95},simulated=True))
            with pytest.raises(ProviderError,match='EXCEEDS_RESERVATION'):await gate.finish(call,outcome='RESPONSE_RECEIVED',usage=usage)
            row=await (await conn.execute('SELECT state,held_tokens,known_lower_bound,reserved_input,reserved_output FROM port_token_calls WHERE call_id=%s',(gate._slot_id(call),))).fetchone()
            assert row=={'state':'unknown','held_tokens':100,'known_lower_bound':95,'reserved_input':90,'reserved_output':10}
            save_case('component-overrun-deadline',{'row':row,'expired_admission_denied':True})
    actual_pg_run(run())
