"""P27 paired finite allocation using existing month locks and P22 receipts."""
import asyncio,json,sys
from datetime import date
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013');sys.path.insert(0,str(ROOT))
from scripts import weknora_port_p22_fence as p22
from scripts.weknora_port_mvp_real import connection
from backend.weknora_port.storage.token_budget import encode
OUT=Path(__file__).parent;R=OUT/'fence';R.mkdir(exist_ok=True)
KEYS=[('deepseek-owner-main','deepseek',date(2026,10,1)),('siliconflow-owner-main','siliconflow',date(2026,10,1))]
ALLOC={'deepseek':750000,'siliconflow':250000}
def require(v,code):
    if not v:raise RuntimeError('P27_'+code)
async def snap(c):
    result={}
    for key in KEYS:
        m=await (await c.execute('SELECT * FROM port_token_budget_months WHERE account_id=%s AND provider=%s AND month=%s FOR UPDATE',key)).fetchone()
        require(m is not None and not m['blocked_reason'],'ACCOUNT_NOT_AVAILABLE')
        rows=await (await c.execute('SELECT * FROM port_token_calls WHERE account_id=%s AND budget_provider=%s AND month=%s ORDER BY call_id',key)).fetchall()
        used=sum(int(r['actual_total'] or 0) if r['state']=='settled' else int(r['held_tokens']) if r['state'] in ('reserved','unknown') else 0 for r in rows)+int(m['carry_in_tokens'])
        result[key[1]]=dict(month=m,calls=rows,occupied=used,headroom=m['limit_tokens']-used)
    return p22.plain(result)
def preserved(before,after):
    for provider in before:
        old={r['call_id']:r for r in before[provider]['calls']};new={r['call_id']:r for r in after[provider]['calls']}
        require(all(new.get(k)==v for k,v in old.items()),'HISTORICAL_ROW_CHANGED')
        a=dict(before[provider]['month']);b=dict(after[provider]['month']);a.pop('limit_tokens');b.pop('limit_tokens')
        require(a==b,'ACCOUNT_AUTHORITY_CHANGED')
async def change(c,current,targets):
    for key in KEYS:
        m=current[key[1]]['month']
        r=await (await c.execute('UPDATE port_token_budget_months SET limit_tokens=%s WHERE account_id=%s AND provider=%s AND month=%s AND limit_tokens=%s AND blocked_reason=%s RETURNING account_id',(targets[key[1]],*key,m['limit_tokens'],''))).fetchone()
        require(r is not None,'FENCE_CAS_CONFLICT')
    after=await snap(c);preserved(current,after)
    return after
async def run(action):
    proof=json.loads((OUT/'account-proof.json').read_text(encoding='utf-8'))
    intent=dict(schema='p27-paired-fence/v1',allocation=ALLOC,total_increment=1000000,
        account_proof_sha256=p22.digest(proof),scope='P27 only, selected full PDFs, image pages9/10, max4 DeepSeek vision and max2 final Cheap, no retries of uncertain sends; close unused space',
        owner_approval='Continue P27, new finite total1,000,000 tokens; Owner confirms only this project used the seven-day exported credential.')
    p22._write(R,'intent',intent)
    c=await connection()
    try:
        await p22._locked(c)
        async with c.transaction():
            await c.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            for key in KEYS:await c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('weknora-token-month:'+encode([str(v) for v in key]),))
            current=await snap(c)
            before=p22._read(R,action+'-before');prepared=p22._read(R,action+'-precommit');done=p22._read(R,action+'-after')
            if done:
                require(current==done['after'],'ALREADY_COMMITTED_STATE_CHANGED');return done
            if prepared and current==prepared['after']:
                receipt=prepared
            else:
                if before is not None:require(before==current,'BASELINE_CHANGED')
                before=p22._write(R,action+'-before',current)
                if action=='open':
                    require(p22._read(R,'close-before') is None,'FENCE_ALREADY_CLOSING')
                    require(current['siliconflow']['headroom']==0,'OLD_SF_FENCE_NOT_CLOSED')
                    require(current['deepseek']['month']['limit_tokens']==1000000,'DS_ORIGINAL_CEILING_CHANGED')
                    require(all(r['state']!='reserved' for v in current.values() for r in v['calls']),'OTHER_INFLIGHT_CALLS')
                    targets={p:current[p]['occupied']+ALLOC[p] for p in ALLOC}
                    require(targets['deepseek']+proof['observed_tokens']<=1000000,'DS_ACCOUNT_CEILING_INSUFFICIENT')
                else:
                    opened=p22._read(R,'open-after');require(opened is not None,'OPEN_RECEIPT_REQUIRED');preserved(opened['before'],current)
                    require(all(current[p]['month']['limit_tokens']==opened['after'][p]['month']['limit_tokens'] for p in ALLOC),'OPEN_LIMIT_CHANGED')
                    targets={p:current[p]['occupied'] for p in ALLOC}
                after=await change(c,current,targets)
                require(sum(max(0,after[p]['headroom']) for p in ALLOC)==(1000000 if action=='open' else 0),'TOTAL_FENCE_MISMATCH')
                receipt=p22._write(R,action+'-precommit',dict(action=action,intent_sha256=p22.digest(intent),before=before,after=after))
        result=p22._write(R,action+'-after',dict(receipt,commit_confirmed=True))
        print(json.dumps({'status':'PASS','action':action,'accounts':{p:{'limit':v['month']['limit_tokens'],'occupied':v['occupied'],'headroom':v['headroom']} for p,v in result['after'].items()}}))
        return result
    finally:await c.close()
if __name__=='__main__':
    require(len(sys.argv)==2 and sys.argv[1] in ('open','close'),'ACTION_REQUIRED')
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(run(sys.argv[1]))
