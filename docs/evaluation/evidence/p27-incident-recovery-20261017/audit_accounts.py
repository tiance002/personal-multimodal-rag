"""P27 read-only account audit. No credentials, model calls, or DB writes."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(r'E:/RAG-Worktrees/weknora-python-port-20261013')
sys.path[:0] = [str(ROOT), str(ROOT/'var/weknora-port/runtime-deps'), r'E:/WK/deps/reader']
from scripts.weknora_port_mvp_real import connection

OUT = Path(__file__).parent
ACCOUNTS = ['deepseek-owner-main', 'siliconflow-owner-main']

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()

async def main():
    result = {'at': datetime.now(timezone.utc).isoformat(), 'access': 'read-only', 'real_provider_requests': 0}
    conn = await connection()
    try:
        async with conn.transaction():
            await conn.execute('SET TRANSACTION READ ONLY')
            identity = await (await conn.execute('SELECT current_database() AS database, system_identifier::text FROM pg_control_system()')).fetchone()
            assert identity['database'] == 'weknora_python_port_20261013'
            assert identity['system_identifier'] == '7696088403504697382'
            result['postgres_identity'] = identity
            months = await (await conn.execute('SELECT * FROM port_token_budget_months WHERE account_id=ANY(%s) ORDER BY account_id,provider,month', (ACCOUNTS,))).fetchall()
            result['months'] = months
            result['token_calls'] = await (await conn.execute("SELECT account_id,budget_provider,provider,month,model,capability,state,COUNT(*) AS rows,SUM(reserved_input) AS reserved_input,SUM(reserved_output) AS reserved_output,SUM(held_tokens) AS held_tokens,SUM(actual_total) AS actual_total,SUM(known_lower_bound) AS known_lower_bound FROM port_token_calls WHERE account_id=ANY(%s) GROUP BY account_id,budget_provider,provider,month,model,capability,state ORDER BY account_id,budget_provider,provider,month,model,capability,state", (ACCOUNTS,))).fetchall()
            snapshots = {}
            for account in ACCOUNTS:
                rows = await (await conn.execute('SELECT * FROM port_token_calls WHERE account_id=%s ORDER BY call_id', (account,))).fetchall()
                snapshots[account] = {'rows': len(rows), 'sha256': digest(rows)}
            result['preservation_hashes'] = snapshots
            result['monetary_history_schema'] = await (await conn.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('send_calls','send_budget_months') ORDER BY table_name,ordinal_position")).fetchall()
            result['monetary_send_groups'] = await (await conn.execute("SELECT account_id,provider,model,capability,state,COUNT(*) AS rows FROM send_calls WHERE account_id=ANY(%s) GROUP BY account_id,provider,model,capability,state ORDER BY account_id,provider,model,capability,state", (ACCOUNTS,))).fetchall()
    finally:
        await conn.close()
    result['shared_account_consumers'] = 'NOT_VERIFIED: local ledger cannot establish provider-account-wide external consumers'
    target = OUT/'account-audit.json'
    with target.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, default=str)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

if __name__ == '__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main())
