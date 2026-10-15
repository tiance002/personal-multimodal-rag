"""Carry-in reconciliation from the existing token ledger; real isolated PG, read-only.

No provider call. Proves the conservative carry-in never under-counts prior usage,
treats a prior UNKNOWN row as UNKNOWN (never 0), excludes the target month and
counts released rows as 0.
"""
import uuid

from backend.weknora_port.storage.token_budget import ledger_carry_in
from backend.weknora_port.storage.token_migrate import apply_token_schema
from tests.weknora_port.test_memory_storage_pg import actual_pg_run, approved_dsn, connection


async def _month_row(conn, account, month):
    await conn.execute("INSERT INTO port_token_budget_months(account_id,provider,month,limit_tokens,"
        "carry_in_tokens,carry_status,carry_source,approval_source) VALUES(%s,'deepseek',%s,1000000,0,'KNOWN',"
        "'LEDGER:test','OWNER:test') ON CONFLICT DO NOTHING", (account, month))


async def _call(conn, account, month, state, *, held, actual=None, reserved=10):
    call_id = 'call-' + uuid.uuid4().hex
    await conn.execute("INSERT INTO port_call_slots(call_id,unit,tenant_id,subject_id,fingerprint) "
        "VALUES(%s,'tokens',1,'s','fp')", (call_id,))
    await conn.execute("INSERT INTO port_token_calls(call_id,request_id,tenant_id,subject_id,session_id,"
        "account_id,provider,budget_provider,month,fingerprint,wire_sha256,wire_bytes,model,model_id,capability,"
        "endpoint,retrieval_kbs,origin_kbs,reserved_input,reserved_output,known_lower_bound,held_tokens,actual_total,"
        "bound_source,permission_source,state,outcome) VALUES(%s,'req',1,'s','sess',%s,'deepseek','deepseek',%s,"
        "'fp','w',1,'m','mid','chat','e','[]'::jsonb,'[]'::jsonb,%s,0,%s,%s,%s,'bound','perm',%s,'UNKNOWN')",
        (call_id, account, month, reserved, max(0, held), held, actual, state))


def test_ledger_carry_in_is_conservative_and_read_only():
    async def scenario():
        conn, guard = await connection(approved_dsn())
        await apply_token_schema(guard)
        try:
            account = 'deepseek-carry-' + uuid.uuid4().hex[:10]
            for month in ('2026-08-01', '2026-09-01', '2026-10-01'):
                await _month_row(conn, account, month)
            await _call(conn, account, '2026-08-01', 'settled', held=1000, actual=1000)
            await _call(conn, account, '2026-09-01', 'reserved', held=200)
            await _call(conn, account, '2026-09-01', 'unknown', held=300)
            await _call(conn, account, '2026-09-01', 'released', held=0)
            await _call(conn, account, '2026-10-01', 'settled', held=99999, actual=99999)  # target month excluded
            result = await ledger_carry_in(conn, account, 'deepseek', '2026-10-01')
            assert result['carry_in_tokens'] == 1500, result              # 1000 + 200 + 300; released 0; target excluded
            assert result['carry_status'] == 'UNKNOWN', result            # a prior UNKNOWN row exists
            assert result['carry_source'].startswith('LEDGER:'), result

            clean = 'deepseek-clean-' + uuid.uuid4().hex[:10]
            await _month_row(conn, clean, '2026-09-01')
            await _call(conn, clean, '2026-09-01', 'settled', held=42, actual=42)
            known = await ledger_carry_in(conn, clean, 'deepseek', '2026-10-01')
            assert known['carry_in_tokens'] == 42 and known['carry_status'] == 'KNOWN', known

            fresh = await ledger_carry_in(conn, 'deepseek-fresh-' + uuid.uuid4().hex[:8], 'deepseek', '2026-10-01')
            assert fresh['carry_in_tokens'] == 0 and fresh['carry_status'] == 'KNOWN', fresh
        finally:
            await conn.close()
    actual_pg_run(scenario())
