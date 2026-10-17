"""P31 narrowly scoped real-API acceptance controller.

This file only defines the actions. Parent/operator invokes commands explicitly.
Mutating HTTP requests are journaled before send and never automatically retried.
"""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, signal, subprocess, sys, time, uuid, inspect
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=Path('E:/WK/run/p31-20261017')
P25=Path('E:/WK/run/p25-20261017/runtime/state.json')
P27=Path('E:/WK/run/p27-live-20261017/state.json')
PG_ID='7696088403504697382'; DB='weknora_python_port_20261013'
ORIGIN='http://127.0.0.1:56426'; API={332:'http://127.0.0.1:56425',344:'http://127.0.0.1:56427'}
Q0959='What are used for long-distance communication as introduced in this slide?'
QIMAGE='What color lenses does the EviRoute lantern use?'
TAG='P31-20261017'

def now(): return datetime.now(timezone.utc).isoformat()
def digest(b): return hashlib.sha256(b).hexdigest()
def readj(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,x):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp')
    t.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8');t.replace(p)
def run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: return runner.run(coro)
def serial(value):
    if is_dataclass(value): return asdict(value)
    if isinstance(value,dict): return {str(k):serial(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [serial(v) for v in value]
    return value
def state():
    p=OUT/'state.json'
    if not p.exists(): raise RuntimeError('P31_NOT_PREPARED')
    return readj(p)
def journal(op,method,url,body=None):
    p=OUT/'operations.json'; d=readj(p) if p.exists() else {}
    if op in d: raise RuntimeError('P31_NO_RETRY_'+op)
    d[op]={'method':method,'url':url,'body_sha256':digest(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()) if body is not None else None,'status':'STARTED_UNKNOWN_UNTIL_RESPONSE','at':now()};save(p,d)
def finish(op,status,response=None,http=None):
    p=OUT/'operations.json';d=readj(p);d[op].update(status=status,http_status=http,response_sha256=digest(response) if response is not None else None,finished_at=now());save(p,d)

def dsn():
    from scripts.weknora_port_mvp_real import dsn as get
    return get()
async def pg_connect():
    from scripts.weknora_port_mvp_real import connection
    return await connection()

async def start():
    if (OUT/'state.json').exists(): raise RuntimeError('P31_EXISTING_STATE_NO_OVERWRITE')
    OUT.mkdir(parents=True,exist_ok=True)
    p25,p27=readj(P25),readj(P27)
    if (p25['tenant'],p27['tenant'])!=(332,344): raise RuntimeError('P31_SOURCE_TENANT_MISMATCH')
    if p25['redis_run_id']!=p27['redis_run_id']: raise RuntimeError('P31_REDIS_SOURCE_ID_MISMATCH')
    conn=await pg_connect()
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        ident=await (await conn.execute('SELECT system_identifier,current_database() AS db FROM pg_control_system(),(SELECT current_database()) q')).fetchone()
        if str(ident['system_identifier'])!=PG_ID or ident['db']!=DB: raise RuntimeError('P31_PG_IDENTITY_MISMATCH')
        migration=await (await conn.execute("SELECT version FROM port_schema_migrations WHERE version='015_provider_calls'")).fetchone()
        if not migration: raise RuntimeError('P31_MIGRATION_015_MISSING')
        subjects={int(p25['tenant']):p25['subject'],int(p27['tenant']):p27['subject']}
        for tenant,subject in subjects.items():
            row=await (await conn.execute('SELECT id FROM tenants WHERE id=%s AND deleted_at IS NULL',(tenant,))).fetchone()
            if not row: raise RuntimeError('P31_EXPLICIT_TENANT_UNAVAILABLE_'+str(tenant))
            role_row=await (await conn.execute('SELECT roles FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s',(tenant,subject))).fetchone()
            roles=role_row['roles'] if role_row else {}
            if not all((roles.get(r) or {}).get('model') and (roles.get(r) or {}).get('api_key') for r in ('embedding','cheap')):
                raise RuntimeError('P31_NORMAL_PROVIDER_CONFIG_MISSING_'+str(tenant))
        old_tasks=await (await conn.execute("SELECT id,state FROM port_image_tasks WHERE tenant_id=344 AND subject_id=%s AND ((state IN ('pending','retry') AND next_attempt_at<=now()) OR (state='processing' AND lease_until<now()))",(p27['subject'],))).fetchall()
        if old_tasks: raise RuntimeError('P31_TENANT344_OLD_IMAGE_TASKS_CLAIMABLE')
        # Existing account/system ledgers are snapshotted by digest only; never edited.
        tables=['port_token_calls','port_token_budget_months','port_call_slots','send_calls','send_budget_months']
        hist={}
        for table in tables:
            try:
                rows=await (await conn.execute(f'SELECT * FROM {table} ORDER BY 1')).fetchall()
                packed=json.dumps(rows,sort_keys=True,default=str).encode();hist[table]={'rows':len(rows),'sha256':digest(packed)}
            except Exception as e:
                hist[table]={'status':'unavailable','error_type':type(e).__name__}
    finally: await conn.close()
    # Snapshot lifecycle: API subject is frozen source subject; storage roots are old P25 read-only source and fresh P31 tenant344.
    p25root=Path(p25['storage_root']); marker=p25root/'.weknora-python-storage.json'
    if not marker.is_file(): raise RuntimeError('P31_P25_STORAGE_MARKER_MISSING')
    marker_obj=readj(marker)
    if marker_obj.get('database')!=DB or marker_obj.get('system_id')!=PG_ID: raise RuntimeError('P31_P25_STORAGE_MARKER_ID_MISMATCH')
    # API + worker are launched only after this command; parent owns live execution.
    redis_id=p25['redis_run_id']
    s={'schema':'p31-native-acceptance/v1','created_at':now(),'pg':{'database':DB,'system_identifier':PG_ID,'migration_015':'present'},
       'redis':{'url':'redis://127.0.0.1:56424/15','run_id':redis_id},'origin':ORIGIN,
       'tenants':{'332':{'subject':p25['subject'],'port':56425,'storage_root':str(p25root),'source':'P25 read-only'},
                  '344':{'subject':p27['subject'],'port':56427,'storage_root':str(OUT/'assets-344'),'source':'P31 fresh'}},
       'sources':{'p25_state_sha256':digest(P25.read_bytes()),'p27_state_sha256':digest(P27.read_bytes()),
          'row0959_kb':'7f1d720d-9561-446f-a8cc-6a562c339d23','row0959_doc':'8fdbe2af-9cc0-4b4a-a554-352a61ca5252',
          'row0959_question':Q0959,'row0959_document_scope':[],'vlm_model_id':'mdl3fc917e2fa4a9429ed45c96ea06cb1feeceb1c19c574aeefd52985a7e7eeb'},
       'legacy_history_before':hist,'processes':{},'cases':{},'status':'PREPARED_NO_CALLS'}
    save(OUT/'state.json',s);save(OUT/'legacy-before.json',hist)
    (OUT/'tmp').mkdir(exist_ok=True);(OUT/'tmp-344').mkdir(exist_ok=True)
    # Existing asset root is checked, not modified by this harness. New tenant root may be initialized by API.
    print('P31_PREPARED',OUT,'tenants=332,344','provider_calls=0')

def _child(tenant,role):
    s=state();item=s['tenants'][str(tenant)]
    if tenant==344 and item['storage_root']==str(OUT/'assets-344'):
        Path(item['storage_root']).mkdir(parents=True,exist_ok=True)
    env=dict(os.environ);tmp=OUT/('tmp' if tenant==332 else 'tmp-344');tmp.mkdir(exist_ok=True)
    prefix='weknora-python-p31-20261017-'+str(tenant)
    env.update(WEKNORA_PORT_DATABASE_URL=dsn(),WEKNORA_PORT_DATABASE_SYSTEM_ID=PG_ID,WEKNORA_PORT_DATABASE_NAME=DB,
      WEKNORA_PORT_REDIS_URL=s['redis']['url'],WEKNORA_PORT_REDIS_RUN_ID=s['redis']['run_id'],WEKNORA_PORT_REDIS_PREFIX=prefix,
      WEKNORA_PORT_STORAGE_ROOT=item['storage_root'],WEKNORA_PORT_SECRET_KEY_FILE='E:/WK/run/p8-20261016/master.key',
      WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',WEKNORA_PORT_ROUTER_MODE='CHEAP_ONLY',
      WEKNORA_PORT_FALLBACK_STRATEGY='model',WEKNORA_PORT_ORIGINS=ORIGIN,WEKNORA_PORT_CLOUD_ALLOWED='true',
      PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',TEMP=str(tmp),TMP=str(tmp),TMPDIR=str(tmp),XDG_CACHE_HOME=str(OUT/'cache'))
    env['PYTHONPATH']=';'.join([str(ROOT),'E:/WK/deps/reader',str(ROOT/'var/weknora-port/runtime-deps')])
    env['RAG_TESSDATA_DIR']='E:/WK/deps/tessdata'
    for name in list(env):
        if name.startswith('WEKNORA_PORT_') and name not in {'WEKNORA_PORT_DATABASE_URL','WEKNORA_PORT_DATABASE_SYSTEM_ID','WEKNORA_PORT_DATABASE_NAME','WEKNORA_PORT_REDIS_URL','WEKNORA_PORT_REDIS_RUN_ID','WEKNORA_PORT_REDIS_PREFIX','WEKNORA_PORT_STORAGE_ROOT','WEKNORA_PORT_SECRET_KEY_FILE','WEKNORA_PORT_TOKENIZER_ASSET','WEKNORA_PORT_ROUTER_MODE','WEKNORA_PORT_FALLBACK_STRATEGY','WEKNORA_PORT_ORIGINS','WEKNORA_PORT_CLOUD_ALLOWED'}:
            env.pop(name,None)
    env['P31_TENANT_ID']=str(tenant);env['P31_SUBJECT_ID']=item['subject'];env['P31_EVIDENCE_DIR']=str(OUT/('evidence-'+str(tenant)))
    env['P31_SERVICE_ROLE']=role;env['P31_PORT']=str(item['port'])
    if role=='worker': env['P31_PORT']='0'
    return env

def start_services():
    s=state()
    for tenant,roles in ((332,('api',)),(344,('api','worker'))):
        for role in roles:
            key=f'{tenant}-{role}'
            if key in s['processes']: raise RuntimeError('P31_PROCESS_ALREADY_RECORDED_'+key)
            port=s['tenants'][str(tenant)]['port'] if role=='api' else None
            if port:
                import socket
                with socket.socket() as sock:
                    if sock.connect_ex(('127.0.0.1',port))==0: raise RuntimeError('P31_PORT_OCCUPIED_'+str(port))
            log=OUT/f'{key}.log';f=log.open('ab')
            cmd=[sys.executable,'-B',str(OUT/'service.py')]
            proc=subprocess.Popen(cmd,cwd=ROOT,env=_child(tenant,role),stdout=f,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
            f.close();s['processes'][key]={'pid':proc.pid,'role':role,'tenant':tenant,'port':port,'command_sha256':digest(' '.join(cmd).encode()),'log':str(log),'started_at':now()};save(OUT/'state.json',s)
    print('P31_SERVICES_STARTED',json.dumps(s['processes'],sort_keys=True))

async def request(method,url,*,json_body=None,files=None,data=None,op=None):
    import httpx
    if method.upper() not in ('GET','HEAD'): journal(op,method,url,json_body)
    try:
        async with httpx.AsyncClient(headers={'Origin':ORIGIN},trust_env=False,timeout=240) as c:
            r=await c.request(method,url,json=json_body,files=files,data=data)
    except BaseException:
        if op: finish(op,'UNKNOWN_NO_RETRY')
        raise
    if op: finish(op,'RESPONSE_RECEIVED',r.content,r.status_code)
    if r.status_code not in (200,201,202): raise RuntimeError(f'P31_HTTP_{r.status_code}_{method}_{url.split("?")[0]}')
    return r

async def query_text():
    s=state();case='row-0959';key='query-'+case
    if case in s['cases']: raise RuntimeError('P31_CASE_ALREADY_ATTEMPTED_NO_RETRY')
    base=API[332];kb=s['sources']['row0959_kb'];doc=s['sources']['row0959_doc'];ev=OUT/'evidence-332';ev.mkdir(parents=True,exist_ok=True)
    # preflight confirms ordinary service/account configuration but never prints key material
    ready=await request('GET',base+'/readyz');save(OUT/'ready-332.json',ready.json())
    roles=await request('GET',base+'/api/v1/settings/model-configuration');save(OUT/'roles-332-public.json',roles.json())
    mem=await request('GET',base+'/api/v1/memory/settings');md=mem.json().get('data',{})
    if md.get('effective') is not False: raise RuntimeError('P31_TENANT332_MEMORY_NOT_DISABLED')
    # Real HTTP ownership check: tenant 344 cannot create a session over tenant 332's KB.
    conn=await pg_connect()
    try: before=int((await (await conn.execute('SELECT count(*) AS n FROM port_provider_calls WHERE tenant_id=344')).fetchone())['n'])
    finally: await conn.close()
    import httpx
    op='scope-denial-344-to-332'
    denied_body={'title':'P31 cross-tenant negative scope check','knowledge_base_scope':[kb],'document_scope':[]}
    url=API[344]+'/api/v1/sessions';journal(op,'POST',url,denied_body)
    async with httpx.AsyncClient(headers={'Origin':ORIGIN},trust_env=False,timeout=30) as client:
        denied=await client.post(url,json=denied_body)
    finish(op,'RESPONSE_RECEIVED',denied.content,denied.status_code)
    if denied.status_code not in (403,404): raise RuntimeError('P31_CROSS_TENANT_SESSION_WAS_NOT_DENIED')
    conn=await pg_connect()
    try: after=int((await (await conn.execute('SELECT count(*) AS n FROM port_provider_calls WHERE tenant_id=344')).fetchone())['n'])
    finally: await conn.close()
    if after!=before: raise RuntimeError('P31_PROVIDER_SENDS_OCCURRED_DURING_SCOPE_CHECK')
    save(OUT/'scope-isolation.json',{'tenant332_kb_used_by':344,'http_status':denied.status_code,'provider_calls_before':before,'provider_calls_after':after,'provider_delta':after-before,'expected':'403_or_404_and_zero_send_delta'})
    src=await request('GET',base+f'/api/v1/knowledge-bases/{kb}/documents');docs=src.json().get('data',[])
    if not any(x.get('id')==doc and x.get('status')=='completed' for x in docs): raise RuntimeError('P31_P25_SOURCE_DOCUMENT_NOT_READY')
    session_r=await request('POST',base+'/api/v1/sessions',json_body={'title':'P31 row-0959 native acceptance','knowledge_base_scope':[kb],'document_scope':[]},op='session-row-0959')
    session=session_r.json()['data']['id']
    conn=await pg_connect()
    try:
        await conn.execute('BEGIN')
        row=await (await conn.execute('SELECT tenant_id,knowledge_base_scope,document_scope,context_config FROM sessions WHERE id=%s AND tenant_id=332 FOR UPDATE',(session,))).fetchone()
        if not row or row['knowledge_base_scope']!=[kb] or row['document_scope']!=[]: raise RuntimeError('P31_SESSION_SCOPE_INVALID')
        # rerank remains the existing default; do not alter the freshly-created session configuration.
        await conn.execute('ROLLBACK')
    finally: await conn.close()
    request_id=str(uuid.uuid4());s['cases'][case]={'status':'STARTED','tenant_id':332,'kb_id':kb,'document_id':doc,'session_id':session,'request_id':request_id,'question':Q0959,'document_scope':[],'started_at':now()};save(OUT/'state.json',s)
    body={'content':Q0959,'request_id':request_id,'knowledge_base_scope':[kb],'document_scope':[],'reasoning_effort':'off'}
    response=await request('POST',base+f'/api/v1/sessions/{session}/messages',json_body=body,op=key)
    raw=response.content;(OUT/'row-0959.sse').write_bytes(raw)
    msgs=await request('GET',base+f'/api/v1/sessions/{session}/messages');save(OUT/'row-0959-messages.json',msgs.json())
    s=state();s['cases'][case].update(status='RESPONSE_RECEIVED',http_status=response.status_code,sse_sha256=digest(raw),completed_at=now());save(OUT/'state.json',s)
    save(OUT/'row-0959-result.json',{'case':case,'tenant_id':332,'session_id':session,'request_id':request_id,'kb_id':kb,'document_id':doc,'document_scope':[],'sse_sha256':digest(raw),'bytes':len(raw),'status':'RESPONSE_RECEIVED','message_readback_sha256':digest(json.dumps(msgs.json(),sort_keys=True,default=str).encode())})
    print('P31_QUERY_TEXT_RESPONSE_SAVED',session,request_id,'bytes='+str(len(raw)))

async def upload_image():
    from PIL import Image, ImageDraw, ImageFont
    s=state();case='image-document'
    if s['cases'].get(case): raise RuntimeError('P31_IMAGE_UPLOAD_ALREADY_ATTEMPTED_NO_RETRY')
    work=OUT/'synthetic-image';work.mkdir(parents=True,exist_ok=True)
    png=work/'eviroute-scan.png';pdf=work/'eviroute-one-page.pdf'
    image=Image.new('RGB',(1600,1000),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((35,35,1565,965),outline='black',width=5)
    draw.text((120,180),'EviRoute lantern uses amber lenses',fill='black',font=ImageFont.truetype('arial.ttf',64))
    image.save(png,format='PNG');image.save(pdf,format='PDF',resolution=144)
    raw=pdf.read_bytes()
    import fitz
    with fitz.open(stream=raw,filetype='pdf') as parsed:
        if parsed.page_count != 1 or any(p.get_text().strip() for p in parsed):
            raise RuntimeError('P31_SYNTHETIC_PDF_NOT_SINGLE_SCAN')
    s['cases'][case]={'status':'PREPARED','pdf':str(pdf),'pdf_sha256':digest(raw),'png_sha256':digest(png.read_bytes()),'bytes':len(raw),'pages':1,'text':'EviRoute lantern uses amber lenses'};save(OUT/'state.json',s)
    base=API[344];role=await request('GET',base+'/api/v1/settings/model-configuration');save(OUT/'roles-344-public.json',role.json())
    vm=s['sources']['vlm_model_id']
    conn=await pg_connect()
    try:
        models=await (await conn.execute(
            "SELECT id,name,status,parameters->>'provider' AS provider,coalesce(length(parameters->>'api_key'),0)>0 AS key_configured FROM models WHERE tenant_id=344 AND subject_id=%s AND id=%s AND deleted_at IS NULL",
            (s['tenants']['344']['subject'],vm))).fetchall()
    finally: await conn.close()
    save(OUT/'vision-model-344-public.json',models)
    if len(models)!=1 or models[0]['status']!='active' or not models[0]['key_configured']:
        raise RuntimeError('P31_PRECONFIGURED_VLM_UNAVAILABLE')
    kb_resp=await request('POST',base+'/api/v1/knowledge-bases',json_body={'name':'P31 synthetic one-page multimodal acceptance'},op='create-image-kb')
    kb=kb_resp.json()['data']['id'];s=state();s['cases'][case].update(kb_id=kb,status='KB_CREATED');save(OUT/'state.json',s)
    await request('PATCH',base+f'/api/v1/knowledge-bases/{kb}/external-processing',json_body={'cloud_allowed':True},op='consent-image-kb')
    await request('PATCH',base+f'/api/v1/knowledge-bases/{kb}/image-processing',json_body={'enable_multimodal':True,'ocr_adapter':'tesseract','caption_mode':'explicit','caption_model_id':vm},op='configure-image-kb')
    s=state();s['cases'][case].update(status='CONFIGURED_NEW_KB');save(OUT/'state.json',s)
    import httpx
    op='upload-image-pdf';url=base+f'/api/v1/knowledge-bases/{kb}/documents';journal(op,'POST',url,{'filename':pdf.name,'sha256':digest(raw),'bytes':len(raw),'page_count':1,'image_pages_field':False})
    try:
        async with httpx.AsyncClient(headers={'Origin':ORIGIN},trust_env=False,timeout=240) as client:
            with pdf.open('rb') as stream: response=await client.post(url,files={'file':(pdf.name,stream,'application/pdf')})
    except BaseException: finish(op,'UNKNOWN_NO_RETRY');raise
    finish(op,'RESPONSE_RECEIVED',response.content,response.status_code)
    if response.status_code not in (200,201,202): raise RuntimeError('P31_IMAGE_UPLOAD_HTTP_'+str(response.status_code))
    doc=response.json()['data']['id'];s=state();s['cases'][case].update(status='UPLOAD_ACCEPTED',document_id=doc,upload_status=response.status_code);save(OUT/'state.json',s)
    print('P31_IMAGE_UPLOAD_ACCEPTED',kb,doc,'sha256='+digest(raw))

async def query_image():
    s=state();case=s['cases'].get('image-document')
    if not case or case.get('status') not in ('UPLOAD_ACCEPTED','INDEXED'): raise RuntimeError('P31_IMAGE_DOCUMENT_NOT_READY')
    import asyncio
    base=API[344];kb=case['kb_id'];doc=case['document_id'];deadline=time.monotonic()+900;last={}
    while time.monotonic()<deadline:
        docs=(await request('GET',base+f'/api/v1/knowledge-bases/{kb}/documents')).json().get('data',[])
        d=next((x for x in docs if x.get('id')==doc),None)
        if not d: raise RuntimeError('P31_IMAGE_DOCUMENT_DISAPPEARED')
        task=(await request('GET',base+f'/api/v1/knowledge-bases/{kb}/documents/{doc}/image-task')).json().get('data',{})
        last={'document':d,'image_task':task};save(OUT/'image-worker-state.json',last)
        if d.get('status')=='failed' or task.get('state') in ('failed','unknown','orphaned','cancelled'):
            raise RuntimeError('P31_IMAGE_INGEST_TERMINAL_FAILURE')
        if d.get('status')=='completed' and task.get('state')=='completed': break
        await asyncio.sleep(3)
    else: raise RuntimeError('P31_IMAGE_INGEST_POLL_TIMEOUT')
    session=(await request('POST',base+'/api/v1/sessions',json_body={'title':'P31 one-page synthetic image acceptance','knowledge_base_scope':[kb],'document_scope':[]},op='session-image-query')).json()['data']['id']
    request_id=str(uuid.uuid4());s=state();s['cases']['image-query']={'status':'STARTED','tenant_id':344,'kb_id':kb,'document_id':doc,'session_id':session,'request_id':request_id,'question':QIMAGE,'document_scope':[],'started_at':now()};save(OUT/'state.json',s)
    rawr=await request('POST',base+f'/api/v1/sessions/{session}/messages',json_body={'content':QIMAGE,'request_id':request_id,'knowledge_base_scope':[kb],'document_scope':[],'reasoning_effort':'off'},op='query-image')
    raw=rawr.content;(OUT/'image-query.sse').write_bytes(raw)
    msgs=(await request('GET',base+f'/api/v1/sessions/{session}/messages')).json();save(OUT/'image-query-messages.json',msgs)
    s=state();s['cases']['image-query'].update(status='RESPONSE_RECEIVED',http_status=rawr.status_code,sse_sha256=digest(raw),completed_at=now());save(OUT/'state.json',s)
    print('P31_IMAGE_QUERY_RESPONSE_SAVED',session,request_id,'bytes='+str(len(raw)))

async def snapshot():
    s=state();conn=await pg_connect()
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        hist={}
        for table in ('port_token_calls','port_token_budget_months','port_call_slots','send_calls','send_budget_months'):
            try:
                rows=await (await conn.execute(f'SELECT * FROM {table} ORDER BY 1')).fetchall();hist[table]={'rows':len(rows),'sha256':digest(json.dumps(rows,sort_keys=True,default=str).encode())}
            except Exception as e: hist[table]={'status':'unavailable','error_type':type(e).__name__}
        provider=[]
        for t in (332,344):
            provider.extend(await (await conn.execute('SELECT tenant_id,subject_id,call_id,request_id,capability,session_id,provider,model,model_id,wire_sha256,outcome,usage,created_at,finished_at FROM port_provider_calls WHERE tenant_id=%s ORDER BY created_at,call_id',(t,))).fetchall())
        tasks=await (await conn.execute('SELECT tenant_id,subject_id,knowledge_base_id,knowledge_id,state,attempt,error_code,created_at,updated_at FROM port_image_tasks WHERE tenant_id=344 AND subject_id=%s ORDER BY created_at',(s['tenants']['344']['subject'],))).fetchall()
        sessions=[]
        for tenant,subject in ((332,s['tenants']['332']['subject']),(344,s['tenants']['344']['subject'])):
            sessions.extend(await (await conn.execute('SELECT id,tenant_id,subject_id,knowledge_base_scope,document_scope,context_config FROM sessions WHERE tenant_id=%s AND subject_id=%s AND id IN (SELECT session_id FROM port_provider_calls WHERE tenant_id=%s)',(tenant,subject,tenant))).fetchall())
        await conn.execute('ROLLBACK')
    finally: await conn.close()
    p={'status':'READ_ONLY_SNAPSHOT','legacy_tables':hist,'legacy_before':s.get('legacy_history_before'),'provider_calls':provider,'provider_call_count':len(provider),'image_tasks':tasks,'sessions':sessions,'captured_at':now()};save(OUT/'snapshot.json',p);print('P31_READ_ONLY_SNAPSHOT',OUT/'snapshot.json','provider_calls='+str(len(provider)))

def service_main():
    import os,asyncio
    from contextlib import asynccontextmanager
    from backend.weknora_port.development import DevelopmentSettings,assemble
    from backend.weknora_port.entrypoint import create_app
    from backend.weknora_port.storage.history import LocalPrincipal
    from scripts.weknora_port_p9_observed_api import install_wire_observer
    from scripts.weknora_port_p8_observed_api import capture
    tenant=int(os.environ['P31_TENANT_ID']);subject=os.environ['P31_SUBJECT_ID'];output=Path(os.environ['P31_EVIDENCE_DIR']);output.mkdir(parents=True,exist_ok=True)
    @asynccontextmanager
    async def factory(settings):
        async with assemble(settings,principal=LocalPrincipal(tenant,subject)) as runtime:
            model_factory=runtime.model_factory
            async def observed_model(config,key,gate):
                model=model_factory(config,key,gate)
                if inspect.isawaitable(model): model=await model
                return install_wire_observer(model,output)
            runtime.model_factory=observed_model
            ordinary=runtime.ordinary;orig_config=ordinary.configuration;orig_pipe=ordinary.pipeline_factory;orig_prepare=ordinary.prepare_turn;states={}
            async def config(turn):
                v=orig_config(turn);v=await v if hasattr(v,'__await__') else v;states[turn.run_id]=v.pipeline_state;return v
            stage_records={}
            async def pipe(turn,progress):
                p=orig_pipe(turn,progress);p=await p if hasattr(p,'__await__') else p;old=p.on_progress
                async def obs(stage,status,**fields):
                    if status=='completed':
                        st=states.get(turn.run_id); obj=stage_records.setdefault(turn.run_id,{'run_id':turn.run_id,'session_id':turn.session_id,'question':turn.request.content,'gold_loaded':False,'stages':[]})
                        obj['stages'].append({'stage':stage,'status':status,'fields':serial(fields),'search_results':serial(getattr(st,'search_results',[]) or []),'rerank_results':serial(getattr(st,'rerank_results',[]) or []),'merge_results':serial(getattr(st,'merge_results',[]) or [])})
                        save(output/'stages'/(hashlib.sha256(turn.run_id.encode()).hexdigest()+'.json'),obj)
                    if old: return await old(stage,status,**fields)
                p.on_progress=obs;return p
            async def prepare(turn,progress=None):
                try:
                    result=orig_prepare(turn,progress);result=await result if hasattr(result,'__await__') else result
                    save(output/'captures'/(hashlib.sha256(turn.run_id.encode()).hexdigest()+'.json'),capture(result));return result
                finally: states.pop(turn.run_id,None)
            ordinary.configuration=config;ordinary.pipeline_factory=pipe;ordinary.prepare_turn=prepare
            yield runtime
    role=os.environ['P31_SERVICE_ROLE']
    if role=='worker':
        from backend.weknora_port.worker import serve
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(serve(DevelopmentSettings.from_environment(),runtime_factory=factory))
    else:
        import uvicorn
        app=create_app(runtime_factory=factory)
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(os.environ['P31_PORT']),log_level='info')).serve())

def stop():
    s=state()
    for key,p in list(s['processes'].items()):
        if p.get('status') == 'STOP_REQUESTED': continue
        pid=int(p['pid'])
        try:
            os.kill(pid,signal.SIGTERM)
        except ProcessLookupError: pass
        p['stop_requested_at']=now();p['status']='STOP_REQUESTED';save(OUT/'state.json',s)
    print('P31_STOP_REQUESTED_OWN_RECORDED_PIDS')

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='command',required=True)
    for c in ('prepare','start','query-text','upload-image','query-image','snapshot','stop'): sub.add_parser(c)
    sub.add_parser('service')
    a=ap.parse_args()
    if a.command=='service': service_main()
    elif a.command=='prepare': run(start())
    elif a.command=='start': start_services()
    elif a.command=='query-text': run(query_text())
    elif a.command=='upload-image': run(upload_image())
    elif a.command=='query-image': run(query_image())
    elif a.command=='snapshot': run(snapshot())
    elif a.command=='stop': stop()
if __name__=='__main__': main()
