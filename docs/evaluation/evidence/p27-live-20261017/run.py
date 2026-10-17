"""Resumable bounded P27 via actual HTTP, no model mocks or Gold input."""
import asyncio,hashlib,json,os,socket,subprocess,sys,time,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013');sys.path.insert(0,str(ROOT))
import httpx
from account_and_inputs import envkey
from scripts.weknora_port_mvp_real import connection,dsn
OUT=Path(__file__).parent;BASE='http://127.0.0.1:56425'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def save(name,value):
    path=OUT/name;path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8');temp.replace(path)
def client():return httpx.AsyncClient(base_url=BASE,headers={'Origin':read('state.json')['origin']},trust_env=False,timeout=600)
async def request(c,op,method,path,*,binary=False,**kwargs):
    ledger=read('http-operations.json') if (OUT/'http-operations.json').exists() else {}
    assert op not in ledger,'DO_NOT_REPEAT_UNCERTAIN_HTTP:'+op
    ledger[op]=dict(method=method,path=path,status='STARTED');save('http-operations.json',ledger)
    try:r=await c.request(method,path,**kwargs)
    except BaseException:
        ledger[op]['status']='UNKNOWN';save('http-operations.json',ledger);raise
    ledger[op].update(status='RECEIVED',http_status=r.status_code,response_sha256=hashlib.sha256(r.content).hexdigest());save('http-operations.json',ledger)
    if r.status_code!=200:raise RuntimeError('HTTP_'+str(r.status_code)+':'+op)
    return r if binary else r.json()
def environment():
    s=read('state.json');e=dict(os.environ)
    for k in list(e):
        if k.startswith('WEKNORA_PORT_'):e.pop(k)
    tmp=OUT/'tmp';tmp.mkdir(exist_ok=True)
    e.update(PYTHONPATH=';'.join([str(ROOT),'E:/WK/deps/reader',str(ROOT/'var/weknora-port/runtime-deps')]),
        PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',TEMP=str(tmp),TMP=str(tmp),
        WEKNORA_PORT_DATABASE_URL=dsn(),WEKNORA_PORT_DATABASE_SYSTEM_ID='7696088403504697382',WEKNORA_PORT_DATABASE_NAME='weknora_python_port_20261013',
        WEKNORA_PORT_REDIS_URL='redis://127.0.0.1:56424/15',WEKNORA_PORT_REDIS_RUN_ID=s['redis_run_id'],WEKNORA_PORT_REDIS_PREFIX=s['redis_prefix'],
        WEKNORA_PORT_STORAGE_ROOT=s['storage_root'],WEKNORA_PORT_SECRET_KEY_FILE=s['secret_key_file'],
        WEKNORA_PORT_TOKENIZER_ASSET='D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken',
        WEKNORA_PORT_ROUTER_MODE='CHEAP_ONLY',WEKNORA_PORT_FALLBACK_STRATEGY='fixed',WEKNORA_PORT_ORIGINS=s['origin'],RAG_TESSDATA_DIR='E:/WK/deps/tessdata')
    return e
async def configs():
    s=read('state.json');receipt=read('fence/open-after.json')['data'];meter=ROOT/'backend/weknora_port/source_facts.py'
    for provider in ('deepseek','siliconflow'):
        m=receipt['after'][provider]['month']
        cfg=dict(schema='weknora-token-authority/v1',mode='approved',tenant_id=s['tenant'],subject_id=s['subject'],
            account_id=m['account_id'],provider=provider,expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            approval_source=m['approval_source'],month=m['month'],carry_in_tokens=m['carry_in_tokens'],carry_status=m['carry_status'],carry_source=m['carry_source'],
            limit_tokens=m['limit_tokens'],input_limit=500000 if provider=='deepseek' else 200000,output_limit=8192,
            allowed_models=[[provider,x] for x in (['deepseek-flash'] if provider=='deepseek' else ['BAAI/bge-m3','BAAI/bge-reranker-v2-m3','XingChenAGI/Xing4.0-29B'])],
            meter=dict(recipe='deepseek-inline-image-1024/v1' if provider=='deepseek' else 'provider-completion-cap/v2',path=str(meter),sha256=hashlib.sha256(meter.read_bytes()).hexdigest()))
        assert not (OUT/(provider+'-authority.json')).exists(),'DO_NOT_REOPEN_AUTHORITY'
        save(provider+'-authority.json',cfg)
    save('authority-bundle.json',dict(schema='weknora-token-authority-bundle/v1',authorities=[str(OUT/(p+'-authority.json')) for p in ('deepseek','siliconflow')]))
async def start():
    assert not (OUT/'processes.json').exists()
    for port in (56425,56426):
        with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',port))!=0,'PORT_OCCUPIED'
    procs={}
    for kind in ('api','worker'):
        cmd=[sys.executable,'-B',str(OUT/'live_service.py'),kind]
        p=subprocess.Popen(cmd,cwd=ROOT,env=environment(),stdout=(OUT/(kind+'.stdout')).open('ab'),stderr=(OUT/(kind+'.stderr')).open('ab'))
        procs[kind]=dict(pid=p.pid,command=cmd);save('processes.json',procs)
    async with client() as c:
        for _ in range(30):
            try:
                r=await c.get('/readyz')
                if r.status_code==200:save('ready.json',r.json());print('READY');return
            except httpx.HTTPError:pass
            await asyncio.sleep(1)
    raise RuntimeError('API_NOT_READY')
async def configure():
    s=read('state.json')
    async with client() as c:
        roles=[]
        for role,model in [('embedding','BAAI/bge-m3'),('rerank','BAAI/bge-reranker-v2-m3'),('cheap','XingChenAGI/Xing4.0-29B'),('expensive',''),('vlm','')]:
            roles.append(dict(role=role,provider='siliconflow' if model else '',model=model,source='remote' if model else '',base_url='https://api.siliconflow.cn/v1' if model else '',api_key=envkey('SILICONFLOW_API_KEY') if model else ''))
        save('roles-public.json',await request(c,'configure-roles','PUT','/api/v1/settings/model-configuration',json=dict(revision='0',roles=roles)))
        vm=await request(c,'register-vision','POST','/api/v1/settings/vision-models',json=dict(source='remote',provider='deepseek',model='deepseek-flash',base_url='https://api.deepseek.com',api_key=envkey('DEEPSEEK_API_KEY'),vision_user_declared=False));save('vision-model-public.json',vm)
        for item in read('frozen-inputs.json')['items']:
            q=item['qid'];kb=(await request(c,'kb-'+q,'POST','/api/v1/knowledge-bases',json=dict(name='P27 '+q+' original PDF')))['data']['id']
            s['cases'][q]=dict(kb=kb);save('state.json',s)
            await request(c,'consent-'+q,'PATCH',f'/api/v1/knowledge-bases/{kb}/external-processing',json=dict(cloud_allowed=True))
            cfg=await request(c,'image-config-'+q,'PATCH',f'/api/v1/knowledge-bases/{kb}/image-processing',json=dict(enable_multimodal=True,ocr_adapter='tesseract',caption_mode='explicit',caption_model_id=vm['data']['id']))
            save(q+'/image-config.json',cfg)
    print('CONFIGURED')
async def snapshot(label):
    s=read('state.json');c=await connection()
    try:
        async with c.transaction():
            await c.execute('SET TRANSACTION READ ONLY')
            result={}
            for name,sql in [
                ('documents','SELECT * FROM knowledges WHERE tenant_id=%s ORDER BY id'),
                ('chunks','SELECT * FROM chunks WHERE tenant_id=%s ORDER BY id'),
                ('tasks','SELECT * FROM port_image_tasks WHERE tenant_id=%s ORDER BY id'),
                ('vectors','SELECT e.id,e.source_id,e.source_type,e.dimension,e.is_enabled,e.knowledge_id,e.knowledge_base_id FROM embeddings e JOIN knowledge_bases kb ON kb.id=e.knowledge_base_id WHERE kb.tenant_id=%s ORDER BY e.id'),
                ('calls','SELECT * FROM port_token_calls WHERE tenant_id=%s ORDER BY call_id')]:
                result[name]=await (await c.execute(sql,(s['tenant'],))).fetchall()
            save('snapshots/'+label+'.json',result)
            return result
    finally:await c.close()
async def run_case(q):
    s=read('state.json');entry=s['cases'][q];kb=entry['kb'];item=next(x for x in read('frozen-inputs.json')['items'] if x['qid']==q)
    async with client() as c:
        if 'document' not in entry:
            raw=Path(item['source']).read_bytes();assert hashlib.sha256(raw).hexdigest()==item['sha256']
            doc=(await request(c,'upload-'+q,'POST',f'/api/v1/knowledge-bases/{kb}/documents',files={'file':(item['filename'],raw,'application/pdf')},data={'process_overrides':json.dumps({'image_pages':[item['physical_page']]})}))['data']
            entry['document']=doc['id'];save('state.json',s);save(q+'/upload.json',doc)
        docid=entry['document'];deadline=time.monotonic()+600
        while True:
            statuses=await request(c,'poll-'+q+'-'+uuid.uuid4().hex,'GET',f'/api/v1/knowledge-bases/{kb}/documents')
            row=next(x for x in statuses['data'] if x['id']==docid);save(q+'/document-final.json',row)
            task=await request(c,'task-'+q+'-'+uuid.uuid4().hex,'GET',f'/api/v1/knowledge-bases/{kb}/documents/{docid}/image-task');save(q+'/image-task-final.json',task)
            if row['status'] not in ('pending','processing'):break
            if time.monotonic()>deadline:raise RuntimeError('INGEST_TIMEOUT')
            await asyncio.sleep(2)
        original=await request(c,'source-'+q,'GET',f'/api/v1/knowledge-bases/{kb}/documents/{docid}/source',binary=True)
        assert hashlib.sha256(original.content).hexdigest()==item['sha256'],'ORIGINAL_READBACK_MISMATCH'
        save(q+'/source-readback.json',dict(sha256=item['sha256'],bytes=len(original.content),matches=True))
        await snapshot(q+'-ingested')
        if row['status']!='completed':print('INGEST_FAILED',q,row['status']);return
        outcomes=task.get('data',{}).get('outcomes',{})
        if not any(v.get('caption_result') for v in outcomes.values()):print('NO_CAPTION',q);return
        session=(await request(c,'session-'+q,'POST','/api/v1/sessions',json=dict(title='P27 '+q,knowledge_base_scope=[kb],document_scope=[])))['data']['id']
        entry.update(session=session,request_id=str(uuid.uuid4()));save('state.json',s)
        started=time.monotonic()
        response=await request(c,'query-'+q,'POST',f'/api/v1/sessions/{session}/messages',binary=True,json=dict(content=item['question'],request_id=entry['request_id'],knowledge_base_scope=[kb],document_scope=[],reasoning_effort='off'))
        (OUT/q/'answer.sse').write_bytes(response.content)
        messages=await request(c,'messages-'+q,'GET',f'/api/v1/sessions/{session}/messages');save(q+'/messages.json',messages)
        entry['qa_elapsed_seconds']=time.monotonic()-started;save('state.json',s)
        await snapshot(q+'-queried');print('QUERY_DONE',q,entry['qa_elapsed_seconds'])
async def main():
    stage=sys.argv[1]
    if stage=='configs':await configs()
    elif stage=='start':await start()
    elif stage=='configure':await configure()
    elif stage=='case':await run_case(sys.argv[2])
    elif stage=='snapshot':await snapshot(sys.argv[2])
if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())
