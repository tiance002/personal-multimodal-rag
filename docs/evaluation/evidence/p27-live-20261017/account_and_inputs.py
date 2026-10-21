import asyncio,csv,fnmatch,hashlib,io,json,shutil,sys,zipfile
from collections import defaultdict
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013');sys.path.insert(0,str(ROOT))
from scripts.weknora_port_mvp_real import connection
OUT=Path(__file__).parent
def save(name,value):
    with (OUT/name).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2,ensure_ascii=False,default=str)
def envkey(name):
    for line in Path('E:/RAG quention/.env.local').read_text(encoding='utf-8-sig').splitlines():
        k,sep,v=line.partition('=')
        if sep and k.strip()==name:return v.strip().strip('"').strip("'")
    raise RuntimeError('KEY_MISSING')
async def main():
    export=Path('C:/Users/22088/Downloads/usage_data_2026-10-11_2026-10-17.zip')
    raw=export.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    with (OUT/'provider-usage-original.zip').open('xb') as f:f.write(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        rows=list(csv.DictReader(io.StringIO(z.read(next(n for n in z.namelist() if n.startswith('amount-'))).decode('utf-8-sig'))))
    secret=envkey('DEEPSEEK_API_KEY');masks=set(r['api_key'] for r in rows)
    assert len(masks)==1 and all(fnmatch.fnmatchcase(secret,m.replace('...','*').replace('…','*')) for m in masks),'EXPORTED_KEY_DOES_NOT_MATCH'
    assert set(r['api_key_name'] for r in rows)=={'ccswitch-deepseek'}
    users=set(r['user_id'] for r in rows);assert len(users)==1 and next(iter(users))
    amounts=defaultdict(int)
    for r in rows:
        assert r['type'] in {'input_cache_hit_tokens','input_cache_miss_tokens','output_tokens','request_count'}
        assert r['amount'].isdigit()
        amounts[r['type']]+=int(r['amount'])
    total=sum(v for k,v in amounts.items() if k!='request_count')
    assert total==52796
    save('account-proof.json',dict(schema='p27-owner-window-account-proof/v1',
        export_name=export.name,export_sha256=sha,export_account_sha256=hashlib.sha256(next(iter(users)).encode()).hexdigest(),
        current_credential_sha256=hashlib.sha256(secret.encode()).hexdigest(),masked_export_credential_matches=True,
        account_alias_preserved='deepseek-owner-main',key_name='ccswitch-deepseek',
        owner_supplied_identifier_matches_export_user_id=next(iter(users))=='0b9fd6d5-8210-400d-94e6-4eae8b9143f6',
        owner_fact='不是本项目专用，但是最近只有这个项目在用。对就是这个，你就看这七天的，都是这个项目在用。',
        audit_window='2026-10-11 through 2026-10-17 export, +08:00; not a complete month audit',
        amounts=dict(amounts),observed_tokens=total,
        consumer_scope='Owner-attested all usage in this seven-day export belongs to this project; shared account, not technically exclusive',
        conservative_treatment='Keep entire export total in account-cap planning, in addition to old local held occupancy; do not net out or settle UNKNOWN'))
    frozen=json.loads(Path('E:/WK/run/p24-20261017/audit-result.json').read_text(encoding='utf-8'))
    cases=[]
    def walk(v):
        if isinstance(v,dict):
            if v.get('qid') in ('row-0958','row-0684'):cases.append(v)
            for x in v.values():walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(frozen)
    manifest=json.loads(Path('E:/WK/run/p27-20261017/offline_parser_asset_inventory.json').read_text())
    items=[]
    for qid,page in [('row-0958',9),('row-0684',10)]:
        case=next(c for c in cases if c['qid']==qid);source=next(c for c in manifest['cases'] if c['qid']==qid)
        assert hashlib.sha256(Path(source['source_path']).read_bytes()).hexdigest()==source['expected_sha256']
        items.append(dict(qid=qid,question=case['question'],physical_page=page,source=source['source_path'],filename=source['filename'],sha256=source['expected_sha256']))
    save('frozen-inputs.json',dict(items=items,gold_not_in_runtime_inputs=True))
    save('frozen-gold-offline-only.json',{c['qid']:c['gold_answers'] for c in cases})
    from redis.asyncio import Redis
    redis=Redis.from_url('redis://127.0.0.1:56424/15',decode_responses=True)
    try:rid=(await redis.info('server'))['run_id']
    finally:await redis.aclose()
    conn=await connection()
    try:
        tenant=(await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',('P27-page-scoped-vision-20261017','isolated-test'))).fetchone())['id']
    finally:await conn.close()
    import uuid
    tag=uuid.uuid4().hex
    save('state.json',dict(tenant=tenant,subject='p27-owner-'+tag,redis_run_id=rid,
        redis_prefix='weknora-python-p27:'+tag,storage_root=str(OUT/'assets'),
        secret_key_file='E:/WK/run/p8-20261016/master.key',port=56425,origin='http://127.0.0.1:56426',cases={}))
    print(json.dumps({'status':'PREPARED','tenant':tenant,'redis_run_id':rid,'export_tokens':total,'request_count':amounts['request_count'],'masked_credential_match':True}))
if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())
