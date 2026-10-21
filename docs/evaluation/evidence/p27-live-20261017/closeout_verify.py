import asyncio,json,socket,sys
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013');sys.path.insert(0,str(ROOT))
from scripts.weknora_port_mvp_real import connection
from scripts.weknora_port_p22_fence import digest
R=Path(__file__).parent
async def main():
    state=json.loads((R/'state.json').read_text(encoding='utf-8'))
    receipt=json.loads((R/'fence/close-after.json').read_text(encoding='utf-8'))['data']
    c=await connection();result={'status':'PASS','database':'weknora_python_port_20261013','system_identifier':'7696088403504697382','accounts':{},'provider_calls':0}
    try:
        async with c.transaction():
            await c.execute('SET TRANSACTION READ ONLY')
            for provider,entry in receipt['after'].items():
                old=entry['month'];key=(old['account_id'],provider,old['month'])
                row=await (await c.execute('SELECT * FROM port_token_budget_months WHERE account_id=%s AND provider=%s AND month=%s',key)).fetchone()
                rows=await (await c.execute('SELECT * FROM port_token_calls WHERE account_id=%s AND budget_provider=%s AND month=%s ORDER BY call_id',key)).fetchall()
                assert digest(row)==digest(old) and digest(rows)==digest(entry['calls'])
                result['accounts'][provider]=dict(rows=len(rows),calls_sha256=digest(rows),month_unchanged_since_close=True,headroom=entry['headroom'],reserved_count=sum(r['state']=='reserved' for r in rows),unknown_count=sum(r['state']=='unknown' for r in rows))
    finally:await c.close()
    from redis.asyncio import Redis
    redis=Redis.from_url('redis://127.0.0.1:56424/15',decode_responses=True)
    try:rid=(await redis.info('server'))['run_id']
    finally:await redis.aclose()
    assert rid==state['redis_run_id'];result.update(redis_run_id=rid,redis_database=15,redis_prefix=state['redis_prefix'])
    result['closed_ports']=[]
    for port in (56425,56426):
        with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',port))!=0
        result['closed_ports'].append(port)
    assert json.loads((R/'authority-bundle.json').read_text(encoding='utf-8'))['mode']=='disabled'
    result['authority_config_disabled']=True
    (R/'closeout-verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())
