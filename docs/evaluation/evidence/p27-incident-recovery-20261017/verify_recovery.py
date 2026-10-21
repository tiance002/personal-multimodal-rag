import asyncio,json,sys
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013')
sys.path.insert(0,str(ROOT))
from audit_accounts import digest,ACCOUNTS
from scripts.weknora_port_mvp_real import connection

async def main():
    out=Path(__file__).parent
    before=json.loads((out/'account-audit.json').read_text())
    conn=await connection()
    result={'provider_generation_calls':0,'account_calls':{}}
    try:
        async with conn.transaction():
            await conn.execute('SET TRANSACTION READ ONLY')
            months=await (await conn.execute('SELECT * FROM port_token_budget_months WHERE account_id=ANY(%s) ORDER BY account_id,provider,month',(ACCOUNTS,))).fetchall()
            expected=before['months']
            expected[0]['blocked_reason']=''
            result['only_expected_month_change']=digest(months)==digest(expected)
            for account in ACCOUNTS:
                rows=await (await conn.execute('SELECT * FROM port_token_calls WHERE account_id=%s ORDER BY call_id',(account,))).fetchall()
                old=before['preservation_hashes'][account]
                result['account_calls'][account]={'rows':len(rows),'sha256':digest(rows),'unchanged':len(rows)==old['rows'] and digest(rows)==old['sha256']}
    finally: await conn.close()
    result['status']='PASS' if result['only_expected_month_change'] and all(r['unchanged'] for r in result['account_calls'].values()) else 'FAIL'
    with (out/'recovery-preservation.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))
    assert result['status']=='PASS'

if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(main())
