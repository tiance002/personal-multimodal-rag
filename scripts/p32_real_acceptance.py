"""P32 real browser and frozen-plan acceptance controller.

Actions are explicit and durable. Mutating HTTP requests are journaled before
send; UNKNOWN requests are never replayed. The controller never reads secrets.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, socket, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=Path('E:/WK/run/p32-20261017')
P25_STATE=Path('E:/WK/run/p25-20261017/runtime/state.json')
P31_RUNNER=ROOT/'scripts/p31_native_acceptance.py'
PG_ID='7696088403504697382'; DB='weknora_python_port_20261013'
REDIS_URL='redis://127.0.0.1:56424/15'; REDIS_ID='dcff9d65a25725574ba0cfbc743d409fac211ef0'
ORIGIN='http://127.0.0.1:56426'; API_HOST='127.0.0.1'

def now(): return datetime.now(timezone.utc).isoformat()
def sha(b:bytes): return hashlib.sha256(b).hexdigest()
def rows_sha(rows):
    ordered=sorted(rows,key=lambda x:json.dumps(x,sort_keys=True,ensure_ascii=False,default=str))
    return sha(json.dumps(ordered,sort_keys=True,ensure_ascii=False,default=str).encode())
def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8');tmp.replace(p)
def run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r: return r.run(coro)
def state():
    p=OUT/'state.json'
    if not p.exists(): raise RuntimeError('P32_NOT_PREPARED')
    return read(p)
def operation(op,method,url,body=None):
    p=OUT/'operations.json';d=read(p) if p.exists() else {}
    if op in d: raise RuntimeError('P32_NO_RETRY_'+op)
    d[op]={'method':method,'url':url,'body_sha256':sha(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()) if body is not None else None,'status':'STARTED_UNKNOWN_UNTIL_RESPONSE','at':now()};save(p,d)
def finish(op,status,response=None,http_status=None):
    p=OUT/'operations.json';d=read(p);d[op].update(status=status,http_status=http_status,response_sha256=sha(response) if response is not None else None,finished_at=now());save(p,d)
def check_port(port):
    with socket.socket() as s:
        if s.connect_ex((API_HOST,int(port)))==0: raise RuntimeError('P32_PORT_OCCUPIED_'+str(port))

async def pg():
    from scripts.weknora_port_mvp_real import connection
    return await connection()

async def preflight(plan):
    source=read(P25_STATE)
    if int(source.get('tenant',0))!=332: raise RuntimeError('P32_P25_TENANT_IDENTITY_MISMATCH')
    conn=await pg()
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        ident=await (await conn.execute('SELECT system_identifier,current_database() AS db FROM pg_control_system(),(SELECT current_database()) q')).fetchone()
        if str(ident['system_identifier'])!=PG_ID or ident['db']!=DB: raise RuntimeError('P32_POSTGRES_IDENTITY_MISMATCH')
        if not await (await conn.execute("SELECT 1 FROM port_schema_migrations WHERE version='015_provider_calls'")).fetchone(): raise RuntimeError('P32_MIGRATION_015_MISSING')
        role=await (await conn.execute('SELECT roles FROM port_model_configuration WHERE tenant_id=332 AND subject_id=%s',(source['subject'],))).fetchone()
        roles=role['roles'] if role else {}
        legacy={}
        for table in ('port_token_calls','port_token_budget_months','port_call_slots','send_calls','send_budget_months'):
            rows=await (await conn.execute(f'SELECT * FROM {table} ORDER BY 1')).fetchall()
            legacy[table]={'rows':len(rows),'sha256':rows_sha(rows)}
        tenant_ids={332}
        role_matrix={'332':{k:{'provider':(roles.get(k) or {}).get('provider'),'model':(roles.get(k) or {}).get('model'),'key_configured':bool((roles.get(k) or {}).get('api_key'))} for k in ('cheap','expensive','embedding','rerank')}}
        for item in plan.get('tenants',[]):
            tenant=int(item['tenant_id']);tenant_ids.add(tenant)
            if tenant==332: continue
            config=await (await conn.execute('SELECT roles FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s',(tenant,item['subject_id']))).fetchone()
            configured=config['roles'] if config else {}
            role_matrix[str(tenant)]={k:{'provider':(configured.get(k) or {}).get('provider'),'model':(configured.get(k) or {}).get('model'),'key_configured':bool((configured.get(k) or {}).get('api_key'))} for k in ('cheap','expensive','embedding','rerank')}
        before_calls=await (await conn.execute('SELECT tenant_id,subject_id,call_id FROM port_provider_calls WHERE tenant_id=ANY(%s) ORDER BY tenant_id,subject_id,call_id',(sorted(tenant_ids),))).fetchall()
        await conn.execute('ROLLBACK')
    finally: await conn.close()
    from redis.asyncio import Redis
    redis=Redis.from_url(REDIS_URL,decode_responses=True)
    try: rid=(await redis.info('server'))['run_id']
    finally: await redis.aclose()
    if rid!=REDIS_ID or source.get('redis_run_id')!=rid: raise RuntimeError('P32_REDIS_IDENTITY_MISMATCH')
    root=Path(source['storage_root']);marker=root/'.weknora-python-storage.json'
    if not marker.is_file(): raise RuntimeError('P32_SOURCE_STORAGE_MARKER_MISSING')
    marker_data=read(marker)
    if marker_data.get('database')!=DB or marker_data.get('system_id')!=PG_ID: raise RuntimeError('P32_SOURCE_STORAGE_IDENTITY_MISMATCH')
    return source,{'database':DB,'system_identifier':PG_ID,'migration_015':'present','redis_url':REDIS_URL,'redis_run_id':rid,
      'tenant_id':332,'subject_id':source['subject'],'storage_root':str(root),'role_availability':role_matrix,
      'dynamic_routing_readiness':'READY' if all((roles.get(k) or {}).get('model') for k in ('cheap','expensive')) else 'INCOMPLETE_CHEAP_OR_EXPENSIVE_ROLE_NO_MODEL_SUBSTITUTION',
      'legacy_history':legacy,'provider_calls_before':before_calls}

def freeze_plan(path):
    if not Path(path).exists(): return {'status':'NOT_PROVIDED','path':str(path),'cases':[],'tenants':[]}
    source=read(path)
    cases=source.get('cases') if isinstance(source,dict) else None
    if not isinstance(cases,list): raise RuntimeError('P32_PLAN_CASES_MUST_BE_LIST')
    sanitized=[];seen=set()
    for c in cases:
        if not isinstance(c,dict): raise RuntimeError('P32_PLAN_CASE_MUST_BE_OBJECT')
        required=('case_id','tenant_id','knowledge_base_id','question')
        if any(not c.get(k) for k in required): raise RuntimeError('P32_PLAN_REQUIRED_FIELD_MISSING')
        cid=str(c['case_id'])
        if cid in seen or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in cid): raise RuntimeError('P32_PLAN_CASE_ID_INVALID')
        seen.add(cid)
        # Gold, expected answer, labels, and scoring fields deliberately excluded from execution data.
        sanitized.append({k:c.get(k) for k in ('case_id','tenant_id','knowledge_base_id','document_scope','question','title','source_dataset','document_id','api_port','session_group') if k in c})
        sanitized[-1].setdefault('document_scope',[])
        if not isinstance(sanitized[-1]['document_scope'],list): raise RuntimeError('P32_PLAN_DOCUMENT_SCOPE_INVALID_'+cid)
    tenants=[]
    for row in source.get('tenants',[]):
        if not isinstance(row,dict) or not row.get('tenant_id') or not row.get('subject_id') or not row.get('api_port') or not row.get('storage_root'):
            raise RuntimeError('P32_PLAN_TENANT_IDENTITY_INCOMPLETE')
        tenants.append({k:row[k] for k in ('tenant_id','subject_id','api_port','storage_root','worker','allowed_document_ids') if k in row})
    return {'status':'FROZEN','path':str(Path(path).resolve()),'sha256':sha(Path(path).read_bytes()),'case_count':len(sanitized),'cases':sanitized,'tenants':tenants}

def prepare(plan_path):
    if (OUT/'state.json').exists(): raise RuntimeError('P32_EXISTING_STATE_NO_OVERWRITE')
    plan=freeze_plan(plan_path)
    source,identity=run(preflight(plan))
    OUT.mkdir(parents=True,exist_ok=True)
    state_obj={'schema':'p32-real-acceptance/v1','status':'PREPARED_NO_LIVE_ACTIONS','created_at':now(),
      'identity':identity,'origin':ORIGIN,'plan':plan,'processes':{},'cases':{},
      'primary_tenant':{'tenant_id':332,'subject_id':source['subject'],'port':56425,'storage_root':source['storage_root'],
        'redis_prefix':'weknora-python-p32-20261017:332:'+uuid.uuid4().hex,'api_evidence':str(OUT/'evidence-332')},
      'frontend':{'port':56426,'api_proxy_target':'http://127.0.0.1:56425','env_source_verified':True}}
    save(OUT/'state.json',state_obj);save(OUT/'identity-before.json',identity);save(OUT/'plan-frozen.json',plan)
    (OUT/'tmp').mkdir(exist_ok=True);(OUT/'cache').mkdir(exist_ok=True)
    print('P32_PREPARED_NO_PROCESSES',OUT,'route=DYNAMIC','cheap_expensive='+identity['dynamic_routing_readiness'])

def attach_plan(plan_path):
    s=state();plan=freeze_plan(plan_path)
    if plan['status']!='FROZEN': return s
    old=s.get('plan',{})
    if old.get('status')=='FROZEN':
        if old.get('sha256')!=plan['sha256']: raise RuntimeError('P32_PLAN_CHANGED_AFTER_FREEZE')
        return s
    if s['cases']: raise RuntimeError('P32_CANNOT_ADD_PLAN_AFTER_QUERY_STARTED')
    # New secondary tenants had no P32 calls before first plan binding; record
    # their existing call IDs now. Tenant 332's original prepare baseline stays intact.
    known={int(x['tenant_id']) for x in s['identity'].get('provider_calls_before',[])}
    add=sorted({int(x['tenant_id']) for x in plan.get('tenants',[])}-known)
    if add:
        async def read_ids():
            c=await pg()
            try:
                rows=await (await c.execute('SELECT tenant_id,subject_id,call_id FROM port_provider_calls WHERE tenant_id=ANY(%s) ORDER BY tenant_id,subject_id,call_id',(add,))).fetchall()
                return rows
            finally: await c.close()
        s['identity']['provider_calls_before'].extend(run(read_ids()))
    s['plan']=plan;save(OUT/'state.json',s);save(OUT/'plan-frozen.json',plan)
    return s

def child_env(tenant,subject,port,storage,prefix,evidence,role='api'):
    s=state();env={k:v for k,v in os.environ.items() if not k.startswith('WEKNORA_PORT_')}
    temp=OUT/('tmp-'+str(tenant));temp.mkdir(exist_ok=True)
    env.update(PYTHONPATH=';'.join([str(ROOT),'E:/WK/deps/reader',str(ROOT/'var/weknora-port/runtime-deps')]),
      PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp),XDG_CACHE_HOME=str(OUT/'cache'),
      RAG_TESSDATA_DIR='E:/WK/deps/tessdata',WEKNORA_PORT_DATABASE_URL=__import__('scripts.weknora_port_mvp_real',fromlist=['dsn']).dsn(),
      WEKNORA_PORT_DATABASE_SYSTEM_ID=PG_ID,WEKNORA_PORT_DATABASE_NAME=DB,WEKNORA_PORT_REDIS_URL=REDIS_URL,
      WEKNORA_PORT_REDIS_RUN_ID=s['identity']['redis_run_id'],WEKNORA_PORT_REDIS_PREFIX=prefix,
      WEKNORA_PORT_STORAGE_ROOT=storage,WEKNORA_PORT_SECRET_KEY_FILE='E:/WK/run/p8-20261016/master.key',
      WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',WEKNORA_PORT_ROUTER_MODE='DYNAMIC',
      WEKNORA_PORT_FALLBACK_STRATEGY='model',WEKNORA_PORT_ORIGINS=ORIGIN,WEKNORA_PORT_CLOUD_ALLOWED='true',
      P31_TENANT_ID=str(tenant),P31_SUBJECT_ID=subject,P31_EVIDENCE_DIR=str(evidence),P31_SERVICE_ROLE=role,P31_PORT=str(port))
    return env

def launch(key,tenant,subject,port,storage,prefix,evidence,role='api',frontend=False):
    check_port(port) if port else None
    if frontend:
        check_port(56426);env={k:v for k,v in os.environ.items() if not k.startswith('VITE_')}
        # vite.config.ts consumes VITE_API_PROXY_TARGET; api.ts uses relative /api/v1 unless VITE_WEKNORA_API_BASE is explicitly set.
        env.update(VITE_API_PROXY_TARGET='http://127.0.0.1:56425',VITE_RAG_IMPLEMENTATION='weknora_python')
        env.pop('VITE_WEKNORA_API_BASE',None)
        cmd=['C:/Program Files/nodejs/npm.cmd','run','dev','--','--host','127.0.0.1','--port','56426','--strictPort']
        cwd=ROOT/'frontend';log=OUT/'frontend.log'
    else:
        env=child_env(tenant,subject,port or 0,storage,prefix,evidence,role)
        cmd=[sys.executable,'-B',str(P31_RUNNER),'service'];cwd=ROOT;log=OUT/f'{key}.log'
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('ab') as f:
        proc=subprocess.Popen(cmd,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,
          creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
    s=state();s['processes'][key]={'pid':proc.pid,'cmd':cmd,'tenant_id':tenant,'port':port,'role':'frontend' if frontend else role,
      'log':str(log),'created_at':now(),'status':'STARTED'};save(OUT/'state.json',s)

def start(plan_path):
    s=attach_plan(plan_path);p=s['primary_tenant']
    # Stage A primary browser service; no requests are sent from this command.
    if 'api-332' not in s['processes']: launch('api-332',332,p['subject_id'],56425,p['storage_root'],p['redis_prefix'],p['api_evidence'])
    if 'frontend' not in state()['processes']: launch('frontend',332,p['subject_id'],56426,'','',OUT/'frontend-evidence',frontend=True)
    # Start additional API/Worker pairs only when present in the frozen plan.
    for row in s['plan'].get('tenants',[]):
        tenant=int(row['tenant_id'])
        if tenant==332: continue
        subject=str(row['subject_id']);port=int(row['api_port']);storage=str(row['storage_root'])
        prefix=f'weknora-python-p32-20261017:{tenant}:{uuid.uuid4().hex}'
        ev=OUT/f'evidence-{tenant}'
        if row.get('worker') is True:
            # Worker dequeue is tenant+subject scoped; require an explicit fresh task allowlist.
            allowed=set(row.get('allowed_document_ids',[]))
            if not allowed: raise RuntimeError('P32_WORKER_REQUIRES_FROZEN_DOCUMENT_ALLOWLIST_'+str(tenant))
            run(check_no_claimable_image_tasks(tenant,subject))
        if f'api-{tenant}' not in state()['processes']: launch(f'api-{tenant}',tenant,subject,port,storage,prefix,ev)
        if row.get('worker') is True and f'worker-{tenant}' not in state()['processes']:
            launch(f'worker-{tenant}',tenant,subject,None,storage,prefix,ev,'worker')
    print('P32_SERVICES_STARTED_NO_API_REQUESTS',json.dumps(state()['processes'],sort_keys=True))

async def check_no_claimable_image_tasks(tenant,subject):
    c=await pg()
    try:
        rows=await (await c.execute("SELECT id,knowledge_base_id,knowledge_id,state FROM port_image_tasks WHERE tenant_id=%s AND subject_id=%s AND ((state IN ('pending','retry') AND next_attempt_at<=now()) OR (state='processing' AND lease_until<now()))",(tenant,subject))).fetchall()
        if rows: raise RuntimeError('P32_WORKER_WOULD_CLAIM_PREEXISTING_TASKS_'+str(tenant))
    finally: await c.close()

async def http_request(method,url,*,body=None,op):
    import httpx
    operation(op,method,url,body)
    try:
        async with httpx.AsyncClient(headers={'Origin':ORIGIN},trust_env=False,timeout=240) as c:
            r=await c.request(method,url,json=body)
    except BaseException:
        finish(op,'UNKNOWN_NO_RETRY');raise
    finish(op,'RESPONSE_RECEIVED',r.content,r.status_code)
    if r.status_code not in (200,201,202): raise RuntimeError(f'P32_HTTP_{r.status_code}_{method}_{url.split("?")[0]}')
    return r

async def query_case(case_id,plan_path):
    s=attach_plan(plan_path);plan=s['plan']
    if plan['status']!='FROZEN': raise RuntimeError('P32_PLAN_REQUIRED')
    if plan['sha256']!=s['plan'].get('sha256'): raise RuntimeError('P32_PLAN_CHANGED_AFTER_PREPARE')
    found=next((x for x in plan['cases'] if x['case_id']==case_id),None)
    if not found: raise RuntimeError('P32_CASE_NOT_IN_FROZEN_PLAN')
    if case_id in s['cases']: raise RuntimeError('P32_CASE_ALREADY_ATTEMPTED_NO_RETRY')
    tenant=int(found['tenant_id']); base=f"http://127.0.0.1:{int(found.get('api_port') or (56425 if tenant==332 else next(int(x['api_port']) for x in plan['tenants'] if int(x['tenant_id'])==tenant))) }"
    kb=str(found['knowledge_base_id']);scope=found.get('document_scope',[])
    kbs=(await http_request('GET',base+'/api/v1/knowledge-bases',op='preflight-kbs-'+case_id)).json().get('data',[])
    if not any(str(x.get('id'))==kb for x in kbs): raise RuntimeError('P32_CASE_KB_NOT_OWNED_BY_SELECTED_TENANT')
    documents=(await http_request('GET',base+f'/api/v1/knowledge-bases/{kb}/documents',op='preflight-docs-'+case_id)).json().get('data',[])
    available={str(x.get('id')) for x in documents}
    if any(str(doc_id) not in available for doc_id in scope): raise RuntimeError('P32_CASE_DOCUMENT_SCOPE_MISMATCH')
    # Fresh isolated session; plan Gold/labels never enter requests.
    sess=(await http_request('POST',base+'/api/v1/sessions',body={'title':found.get('title') or ('P32 '+case_id),'knowledge_base_scope':[kb],'document_scope':scope},op='session-'+case_id)).json()['data']['id']
    rid=str(uuid.uuid4());s=state();s['cases'][case_id]={'status':'STARTED','tenant_id':tenant,'kb_id':kb,'document_scope':scope,'session_id':sess,'request_id':rid,'question':found['question'],'started_at':now()};save(OUT/'state.json',s)
    body={'content':found['question'],'request_id':rid,'knowledge_base_scope':[kb],'document_scope':scope,'reasoning_effort':'off'}
    response=await http_request('POST',base+f'/api/v1/sessions/{sess}/messages',body=body,op='query-'+case_id)
    raw=response.content;target=OUT/'cases'/case_id;target.mkdir(parents=True,exist_ok=True);(target/'answer.sse').write_bytes(raw)
    messages=(await http_request('GET',base+f'/api/v1/sessions/{sess}/messages',op='read-messages-'+case_id)).json()
    save(target/'messages.json',messages)
    s=state();s['cases'][case_id].update(status='RESPONSE_RECEIVED',http_status=response.status_code,sse_sha256=sha(raw),bytes=len(raw),completed_at=now());save(OUT/'state.json',s)
    save(target/'result.json',{'case_id':case_id,'tenant_id':tenant,'knowledge_base_id':kb,'document_scope':scope,'session_id':sess,'request_id':rid,'question':found['question'],'http_status':response.status_code,'sse_sha256':sha(raw),'bytes':len(raw),'message_readback_sha256':sha(json.dumps(messages,sort_keys=True,default=str).encode()),'gold_loaded':False})
    print('P32_CASE_RESPONSE_SAVED',case_id,sess,rid,'bytes='+str(len(raw)))

async def snapshot():
    s=state();c=await pg()
    try:
        await c.execute('BEGIN TRANSACTION READ ONLY')
        tables={}
        for table in ('port_token_calls','port_token_budget_months','port_call_slots','send_calls','send_budget_months'):
            rows=await (await c.execute(f'SELECT * FROM {table} ORDER BY 1')).fetchall();tables[table]={'rows':len(rows),'sha256':rows_sha(rows)}
        calls=[]
        for tenant in sorted({int(x['tenant_id']) for x in s['cases'].values()}|{332}):
            calls.extend(await (await c.execute('SELECT tenant_id,subject_id,call_id,request_id,capability,session_id,provider,model,model_id,wire_sha256,outcome,usage,created_at,finished_at FROM port_provider_calls WHERE tenant_id=%s ORDER BY created_at,call_id',(tenant,))).fetchall())
        await c.execute('ROLLBACK')
    finally: await c.close()
    out={'status':'READ_ONLY_SNAPSHOT','at':now(),'legacy_before':s['identity']['legacy_history'],'legacy_after':tables,'provider_calls':calls,'provider_call_count':len(calls),'cases':s['cases']}
    old={(int(x['tenant_id']),x['subject_id'],x['call_id']) for x in s['identity'].get('provider_calls_before',[])}
    out['provider_call_ids_before_count']=len(old)
    out['p32_provider_calls']=[x for x in calls if (int(x['tenant_id']),x['subject_id'],x['call_id']) not in old]
    out['p32_provider_call_count']=len(out['p32_provider_calls'])
    save(OUT/'snapshot.json',out);print('P32_SNAPSHOT_SAVED',OUT/'snapshot.json','provider_calls='+str(len(calls)))

def stop():
    s=state()
    for name,item in s['processes'].items():
        if item.get('status')=='STOPPED': continue
        pid=int(item['pid'])
        try:
            query=f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if ($p) {{ $p.CommandLine | ConvertTo-Json -Compress }}"
            check=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',query],capture_output=True,text=True,timeout=10)
            if check.returncode!=0: raise RuntimeError('P32_PROCESS_IDENTITY_QUERY_FAILED_'+name)
            command=json.loads(check.stdout) if check.stdout.strip() else None
            if not command: item['status']='ALREADY_EXITED';save(OUT/'state.json',s);continue
            expected=str(P31_RUNNER).lower() if item['role']!='frontend' else 'npm.cmd'
            if expected not in str(command).lower(): raise RuntimeError('P32_PID_COMMAND_MISMATCH_'+name)
            terminated=subprocess.run(['taskkill.exe','/PID',str(pid),'/T','/F'],capture_output=True,text=True,timeout=15)
            if terminated.returncode not in (0,128): raise RuntimeError('P32_TASKKILL_FAILED_'+name)
        except subprocess.TimeoutExpired: raise RuntimeError('P32_PROCESS_STOP_TIMEOUT_'+name)
        item['status']='STOPPED';item['stopped_at']=now();save(OUT/'state.json',s)
    print('P32_OWNED_PROCESSES_STOPPED')

def main():
    ap=argparse.ArgumentParser(description=__doc__);sp=ap.add_subparsers(dest='command',required=True)
    p=sp.add_parser('prepare');p.add_argument('--plan',default=str(OUT/'plan.json'))
    p=sp.add_parser('start');p.add_argument('--plan',default=str(OUT/'plan.json'))
    q=sp.add_parser('query-case');q.add_argument('case_id');q.add_argument('--plan',default=str(OUT/'plan.json'))
    sp.add_parser('snapshot');sp.add_parser('stop')
    a=ap.parse_args()
    if a.command=='prepare': prepare(a.plan)
    elif a.command=='start': start(a.plan)
    elif a.command=='query-case': run(query_case(a.case_id,a.plan))
    elif a.command=='snapshot': run(snapshot())
    elif a.command=='stop': stop()
if __name__=='__main__':main()
