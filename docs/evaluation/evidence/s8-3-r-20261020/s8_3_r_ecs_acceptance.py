"""One-question private FastAPI runner for the authorized S8.3 evidence A/B."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, sys, time, uuid, threading, re
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

RUN=Path(os.environ.get('S8_3_RUN_ROOT','/opt/rag-s8/state/s8-3-r-20261020'))
SOURCE=Path('/app')
PAYLOAD=RUN/'payload'
PLAN_PATH=PAYLOAD/'authorized-plan.json'
OWNER_PATH=PAYLOAD/'owner-authorization.json'
TEMPLATES_PATH=PAYLOAD/'ledger-templates.json'
RESOURCE_PATH=PAYLOAD/'resource-identity-freeze.json'
PLAN=json.loads(PLAN_PATH.read_text(encoding='utf-8'))
OWNER=json.loads(OWNER_PATH.read_text(encoding='utf-8'))
TEMPLATES=json.loads(TEMPLATES_PATH.read_text(encoding='utf-8'))['ledgers']
RESOURCE=json.loads(RESOURCE_PATH.read_text(encoding='utf-8'))
if str(SOURCE) not in sys.path:sys.path.insert(0,str(SOURCE))
SQL_OBSERVER=None

class PassiveSQLObserver:
 """Count execute calls in this private API process, without capturing SQL/params."""
 def __init__(self,connection_type):
  self.connection_type=connection_type;self.original=None;self.wrapper=None;self.active=False
  self.calls=self.completed=self.failures=0;self.elapsed=0.0;self.started=None
 def install(self):
  self.original=self.connection_type.execute;observer=self
  async def execute(connection,*args,**kwargs):
   observed=observer.active;started=time.monotonic()
   if observed:observer.calls+=1
   try:
    result=await observer.original(connection,*args,**kwargs)
    if observed:observer.completed+=1
    return result
   except BaseException:
    if observed:observer.failures+=1
    raise
   finally:
    if observed:observer.elapsed+=time.monotonic()-started
  self.wrapper=execute;self.connection_type.execute=execute
 def begin(self):
  self.calls=self.completed=self.failures=0;self.elapsed=0.0
  self.started=time.monotonic();self.active=True
 def finish(self):
  self.active=False
  return {'scope':'private_api_process_execute_calls_during_accepted_question_ASGI_window',
   'execute_calls':self.calls,'completed_execute_calls':self.completed,'failed_execute_calls':self.failures,
   'execute_elapsed_sum_ms':round(self.elapsed*1000,3),
   'window_elapsed_ms':round((time.monotonic()-self.started)*1000,3) if self.started is not None else None,
   'query_text_saved':False,'parameters_saved':False,
   'limitations':'Includes request persistence/provider accounting SQL; excludes fetch/decode time and other processes. Elapsed sum can overlap concurrent calls.'}
 def restore(self):
  if self.connection_type.execute is self.wrapper:self.connection_type.execute=self.original

def json_default(value):
 from datetime import date,datetime
 if isinstance(value,(date,datetime)):return value.isoformat()
 raise TypeError(type(value).__name__)

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False,default=json_default)
def sha(value):return hashlib.sha256(value.encode('utf-8') if isinstance(value,str) else value).hexdigest()
def write_new(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False,default=json_default);f.write('\n');f.flush();os.fsync(f.fileno())
def safe_write(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
 with tmp.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False,default=json_default);f.write('\n');f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)
def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def case_for(case_id):
 rows=[x for x in PLAN['cases'] if x.get('case_id')==case_id]
 if len(rows)!=1:raise RuntimeError('S8_3_CASE_NOT_UNIQUE')
 case=rows[0]
 if case.get('tenant_id')!=332 or case.get('document_scope')!=[]:raise RuntimeError('S8_3_SCOPE_INVALID')
 if case.get('knowledge_base_id') not in case.get('knowledge_base_scope',[case.get('knowledge_base_id')]):raise RuntimeError('S8_3_KB_SCOPE_INVALID')
 if sha(case['question'])!=case.get('frozen_query_sha256_utf8'):raise RuntimeError('S8_3_QUERY_HASH_MISMATCH')
 return case
def grant_checks():
 import re
 if OWNER.get('status')!='OPEN' or OWNER.get('max_calls')!=60 or OWNER.get('max_tokens')!=200000 or OWNER.get('max_question_posts')!=12:raise RuntimeError('S8_3_OWNER_GRANT_INVALID')
 if OWNER.get('renewal_allowed') is not False and OWNER.get('batch_contract',{}).get('renewal_allowed') is not False:raise RuntimeError('S8_3_RENEWAL_FORBIDDEN')
 if sha(canonical(PLAN))!=OWNER.get('plan_sha256'):raise RuntimeError('S8_3_PLAN_GRANT_HASH_MISMATCH')
 if not re.fullmatch('[0-9a-f]{40}',PLAN.get('source_commit','')):raise RuntimeError('S8_3_SOURCE_COMMIT_MISMATCH')
 return True
grant_checks()

def ledger_template(case_id):
 rows=[x for x in TEMPLATES if x['ledger'].get('case_id')==case_id]
 if len(rows)!=1:raise RuntimeError('S8_3_LEDGER_TEMPLATE_INVALID')
 return rows[0]

def verify_image(args):
 manifest=read_json(PAYLOAD/'source-manifest.json')
 if manifest.get('source_commit')!=PLAN['source_commit']:
  raise RuntimeError('S8_3_IMAGE_SOURCE_REVISION_MISMATCH')
 hashes=manifest.get('source_code_hashes')
 if not isinstance(hashes,dict) or not hashes:
  raise RuntimeError('S8_3_IMAGE_SOURCE_MANIFEST_EMPTY')
 for relative,digest in hashes.items():
  part=Path(relative)
  if part.is_absolute() or '..' in part.parts or not re.fullmatch('[0-9a-f]{64}',digest):
   raise RuntimeError('S8_3_IMAGE_SOURCE_MANIFEST_INVALID')
  target=SOURCE/part
  if not target.resolve().is_relative_to(SOURCE.resolve()) or not target.is_file() or sha(target.read_bytes())!=digest:
   raise RuntimeError('S8_3_IMAGE_SOURCE_HASH_MISMATCH')
 print(canonical({'status':'IMAGE_SOURCE_HASHES_PASS','files':len(hashes),'source_commit':manifest['source_commit']}))

def api_payload(case,request_id):
 return {'content':case['question'],'request_id':request_id,'mode':'quick','reasoning_effort':'off',
         'knowledge_base_scope':[case['knowledge_base_id']],'document_scope':[]}

def source_path_allowed(path,kb_id):
 prefix=f'/api/v1/knowledge-bases/{kb_id}/documents/'
 if not path.startswith(prefix) or not path.endswith('/source'):return False
 document_id=path[len(prefix):-len('/source')]
 try:return str(uuid.UUID(document_id))==document_id
 except (ValueError,AttributeError):return False

class SingleQuestionAdmission:
 def __init__(self,app,case,case_dir,request_id,title):
  self.app,self.case,self.case_dir,self.request_id,self.title=app,case,Path(case_dir),request_id,title
  self.session_id=None;self.session_claimed=False;self.question_claimed=False
 async def __call__(self,scope,receive,send):
  if scope['type']!='http':return await self.app(scope,receive,send)
  method,path=scope['method'],scope['path']
  if method in ('PUT','PATCH','DELETE'):return await self.denied(send,'S8_3_READ_ONLY_EXCEPT_FROZEN_QA')
  if method=='GET':
   allowed=(path in ('/readyz','/healthz','/api/v1/runtime','/api/v1/settings/model-configuration','/api/v1/knowledge-bases')
      or (self.session_id is not None and path==f'/api/v1/sessions/{self.session_id}/messages')
      or source_path_allowed(path,self.case['knowledge_base_id']))
   if not allowed:return await self.denied(send,'S8_3_GET_NOT_APPROVED')
   return await self.app(scope,receive,send)
  if method!='POST':return await self.denied(send,'S8_3_METHOD_NOT_APPROVED')
  frames=[]
  while True:
   frame=await receive();frames.append(frame)
   if frame['type']!='http.request' or not frame.get('more_body'):break
  try:body=json.loads(b''.join(x.get('body',b'') for x in frames))
  except Exception:return await self.denied(send,'S8_3_BODY_INVALID',400)
  if path=='/api/v1/sessions':
   expected={'knowledge_base_scope':[self.case['knowledge_base_id']],'document_scope':[],'title':self.title,'source_evidence_mode':self.case['source_evidence_mode']}
   if self.session_claimed or body!=expected:return await self.denied(send,'S8_3_SESSION_CREATE_DENIED',409)
   try:write_new(self.case_dir/'session-create-claim.json',{'case_id':self.case['case_id'],'kb':self.case['knowledge_base_id']})
   except FileExistsError:return await self.denied(send,'S8_3_SESSION_CREATE_REPLAY_DENIED',409)
   self.session_claimed=True
   queued=iter(frames)
   async def replay():
    try:return next(queued)
    except StopIteration:return await receive()
   async def capture(message):
    if message.get('type')=='http.response.body' and message.get('body'):
     try:
      obj=json.loads(message['body']);data=obj.get('data',{})
      sid=data.get('id') if isinstance(data,dict) else None
      if isinstance(sid,str) and sid:self.session_id=sid
     except Exception:pass
    await send(message)
   return await self.app(scope,replay,capture)
  prefix='/api/v1/sessions/'
  if path.startswith(prefix) and path.endswith('/messages'):
   sid=path[len(prefix):-len('/messages')]
   expected=api_payload(self.case,self.request_id)
   if (self.question_claimed or self.session_id is None or sid!=self.session_id or body!=expected):
    return await self.denied(send,'S8_3_FROZEN_QUESTION_DENIED',409)
   try:write_new(self.case_dir/'question-post-claim.json',{'case_id':self.case['case_id'],'session_id':sid,
       'request_id':self.request_id,'question_sha256':sha(self.case['question'])})
   except FileExistsError:return await self.denied(send,'S8_3_QUESTION_REPLAY_DENIED',409)
   self.question_claimed=True
   observer=SQL_OBSERVER
   if observer is not None:observer.begin()
   queued=iter(frames)
   async def replay():
    try:return next(queued)
    except StopIteration:return await receive()
   try:return await self.app(scope,replay,send)
   finally:
    if observer is not None:write_new(self.case_dir/'rag-sql-observation.json',observer.finish())
  return await self.denied(send,'S8_3_POST_NOT_APPROVED')
 @staticmethod
 async def denied(send,code,status=403):
  body=json.dumps({'error':{'code':code}},separators=(',',':')).encode()
  await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode()),(b'cache-control',b'no-store')]})
  await send({'type':'http.response.body','body':body})

def build_application(case_id, port):
 from scripts.s8_ecs_runtime import create_application, PrivateApiGuard, api_token
 case=case_for(case_id)
 # The normal guarded runtime performs normal assembly without factory injection.
 # The outer bearer guard prevents unauthenticated requests consuming admission claims.
 title=f"S8.3 {case['dataset']} {case['qid']} {case['source_evidence_mode']}"
 return PrivateApiGuard(SingleQuestionAdmission(create_application(),case,
     RUN/'cases'/sha(case_id),os.environ['S8_3_REQUEST_ID'],title),api_token(os.environ))


class MemoryCheckpointWriter:
 """Persist observed samples while alive; never replace an older capture."""
 def __init__(self,directory):
  directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
  self.capture_id=uuid.uuid4().hex
  self.path=directory/'memory.json'
  if self.path.exists():self.path=directory/('memory-'+self.capture_id+'.json')
  self.journal=directory/('memory-checkpoints-'+self.capture_id+'.jsonl')
  self.claimed=False
 def checkpoint(self,samples,*,finalized=False):
  if not samples:return
  record={'capture_id':self.capture_id,'checkpoint_status':'finalized' if finalized else 'periodic',
   'sample_count':len(samples),'sampling_interval_seconds':1,
   'host_mem_available_min_kib':min((x['host_mem_available_kib'] for x in samples if 'host_mem_available_kib' in x),default=None),
   'container_memory_peak_bytes':max((x['memory_peak'] for x in samples if isinstance(x.get('memory_peak'),int)),default=None),
   'container_memory_limit_bytes':next((x['memory_max'] for x in samples if isinstance(x.get('memory_max'),int)),None),
   'scope':'observed samples through last durable checkpoint; no claim about unsampled intervals',
   'samples':samples}
  # The append-only journal survives a native SIGTERM exit even if serve's
  # finally block is skipped; memory.json is the current durable snapshot.
  with self.journal.open('a' if self.claimed else 'x',encoding='utf-8') as output:
   output.write(canonical({'capture_id':self.capture_id,'sample_count':len(samples),
      'checkpoint_status':record['checkpoint_status'],'sample':samples[-1]})+'\n')
   output.flush();os.fsync(output.fileno())
  if self.claimed:safe_write(self.path,record)
  else:
   write_new(self.path,record);self.claimed=True


def serve(args):
 global SQL_OBSERVER
 import uvicorn
 import psycopg
 SQL_OBSERVER=PassiveSQLObserver(psycopg.AsyncConnection);SQL_OBSERVER.install()
 app=build_application(args.case_id,args.port)
 case_dir=RUN/'cases'/sha(args.case_id);case_dir.mkdir(parents=True,exist_ok=True)
 stop=threading.Event();samples=[];memory_writer=MemoryCheckpointWriter(case_dir)
 def sample_memory():
  while not stop.is_set():
   try:
    mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
     if line.startswith('MemAvailable:'):mem['host_mem_available_kib']=int(line.split()[1]);break
    for name in ('memory.current','memory.peak','memory.max'):
     p=Path('/sys/fs/cgroup')/name
     if p.exists():
      raw=p.read_text().strip();mem[name.replace('.','_')]=None if raw=='max' else int(raw)
    samples.append({'monotonic':time.monotonic(),**mem})
    memory_writer.checkpoint(samples)
   except (OSError,ValueError):pass
   stop.wait(1)
 thread=threading.Thread(target=sample_memory,name='s8-3-memory-sampler',daemon=True);thread.start()
 try:uvicorn.run(app,host='127.0.0.1',port=args.port,workers=1,log_level='warning',access_log=False)
 finally:
  SQL_OBSERVER.restore()
  stop.set();thread.join(timeout=3)
  if samples:memory_writer.checkpoint(samples,finalized=True)

def read_response(response):
 value=response.json()
 if not response.is_success:raise RuntimeError('HTTP_'+str(response.status_code)+'_'+str(value.get('error',{}).get('code','ERROR')))
 return value.get('data',value)

def ready(args):
 import httpx
 from scripts.s8_ecs_runtime import api_token
 token=api_token(os.environ)
 with httpx.Client(timeout=5) as client:
  r=client.get(f'http://127.0.0.1:{args.port}/readyz',headers={'Authorization':'Bearer '+token})
  if not r.is_success:raise SystemExit('API_NOT_READY')
  r.json()
 data=r.json()
 if data.get('tenant_id')!=332 or data.get('subject_sha256')!=PLAN['principal']['subject_sha256']:
  raise SystemExit('API_IDENTITY_MISMATCH')
 if (data.get('database')!=RESOURCE['database'] or data.get('postgres_system_id')!=RESOURCE['postgres_system_id']
     or data.get('redis_run_id')!=RESOURCE['redis_run_id'] or data.get('redis_prefix')!='weknora-python-s8:runtime'
     or data.get('graph_enabled') is not False or data.get('rerank_top1_floorless') is not False
     or data.get('models')!=PLAN['model_binding_snapshot']['effective_roles'] or data.get('cloud_allowed') is not True
     or data.get('model_requests_at_startup')!=0):
  raise SystemExit('API_RESOURCE_OR_FLAGS_IDENTITY_MISMATCH')
 write_new(RUN/'cases'/sha(os.environ['S8_3_CASE_ID'])/'runtime-identity.json',data)
 print(canonical({'status':'READY','http_status':r.status_code,'provider_calls':0}))


def context_sources(stage_record, kb_id, events=None, messages=None):
    """Project only final rendered result IDs from the final context snapshot."""
    final = stage_record.get('finalized_context')
    if not isinstance(final, dict) or not isinstance(final.get('result_ids'), list):
        completes=[e for e in (events or []) if e.get('response_type')=='complete']
        assistants=[m for m in (messages or []) if m.get('role')=='assistant']
        # SearchNothing is caught by OrdinaryTurnService outside the observed
        # pipeline: it constructs an empty FinalizedContext and clears evidence.
        # Require its independently persisted/SSE outcome, never a case ID/gold.
        if (final is None and stage_record.get('error_type')=='SearchNothing'
                and len(completes)==1 and (completes[0].get('data') or {}).get('is_fallback') is True
                and not any(s.get('stage')=='context' and s.get('status')=='completed' for s in stage_record.get('stages',[]))
                and len(assistants)==1 and assistants[0].get('is_completed') is True
                and assistants[0].get('knowledge_references')==[]):
            return []
        raise RuntimeError('S8_3_FINAL_CONTEXT_MISSING')
    ids = final['result_ids']
    if len(ids) != len(set(ids)) or any(not isinstance(x, str) or not x for x in ids):
        raise RuntimeError('S8_3_FINAL_CONTEXT_IDS_INVALID')
    if not ids:
        return []
    contexts = [row for row in stage_record.get('stages', [])
                if row.get('stage') == 'context' and row.get('status') == 'completed']
    if not contexts:
        raise RuntimeError('S8_3_CONTEXT_COMPLETED_MISSING')
    candidates = contexts[-1].get('state', {}).get('merge_results', [])
    result = {}
    for result_id in ids:
        matches = [row for row in candidates if isinstance(row, dict) and row.get('id') == result_id]
        if len(matches) != 1:
            raise RuntimeError('S8_3_FINAL_CONTEXT_RESULT_UNMAPPED')
        row = matches[0]
        metadata, chunk_metadata = row.get('metadata') or {}, row.get('chunk_metadata') or {}
        version = chunk_metadata.get('source_version') or metadata.get('source_version')
        digest, document_id = metadata.get('sha256'), row.get('knowledge_id')
        if (row.get('knowledge_base_id') != kb_id or not isinstance(version, str) or not version
                or not isinstance(document_id, str) or not source_path_allowed(
                    f'/api/v1/knowledge-bases/{kb_id}/documents/{document_id}/source', kb_id)
                or not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None
                or (chunk_metadata.get('source_version') and metadata.get('source_version')
                    and chunk_metadata['source_version'] != metadata['source_version'])):
            raise RuntimeError('S8_3_FINAL_CONTEXT_SOURCE_INVALID')
        descriptor = {'source_id': document_id, 'document_id': document_id, 'knowledge_base_id': kb_id,
                      'source_version': version, 'expected_sha256': digest, 'result_ids': []}
        key = (document_id, version)
        previous = result.setdefault(key, descriptor)
        if previous['expected_sha256'] != digest:
            raise RuntimeError('S8_3_FINAL_CONTEXT_SOURCE_HASH_CONFLICT')
        previous['result_ids'].append(result_id)
    return list(result.values())


async def database_snapshot(case_id, session_id, *, full=False, source_ids=None):
    """Direct read-only SQL and ledger reads; never assemble a runtime or provider."""
    import psycopg
    from psycopg.rows import dict_row
    from scripts.s8_ecs_runtime import runtime_environment, trusted_principal
    from backend.weknora_port.development import DevelopmentSettings
    from urllib.parse import urlsplit
    case = case_for(case_id)
    env = runtime_environment(os.environ)
    settings = DevelopmentSettings.from_environment(env)
    principal = trusted_principal(env.get('S8_PRINCIPAL_FILE', '/run/secrets/principal.json'))
    if (settings.database != RESOURCE['database'] or settings.system_id != RESOURCE['postgres_system_id']
            or settings.redis_run_id != RESOURCE['redis_run_id']
            or urlsplit(settings.redis_url).path != '/' + str(RESOURCE['redis_db'])
            or principal.tenant_id != 332 or sha(principal.subject_id) != PLAN['principal']['subject_sha256']):
        raise RuntimeError('S8_3_AUDIT_RESOURCE_IDENTITY_MISMATCH')
    conn = await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True,
                                               row_factory=dict_row, connect_timeout=5)
    try:
        await conn.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        identity = await (await conn.execute('SELECT system_identifier::text AS system_id,current_database() AS database FROM pg_control_system()')).fetchone()
        if identity['system_id'] != RESOURCE['postgres_system_id'] or identity['database'] != RESOURCE['database']:
            raise RuntimeError('S8_3_AUDIT_PHYSICAL_IDENTITY_MISMATCH')
        select_count = 1
        async def rows(sql, params):
            nonlocal select_count
            select_count += 1
            return await (await conn.execute(sql, params)).fetchall()
        scope = (332, principal.subject_id, session_id)
        providers = await rows('SELECT call_id,request_id,session_id,capability,provider,model,model_id,wire_sha256,outcome,usage,created_at,finished_at FROM port_provider_calls WHERE tenant_id=%s AND subject_id=%s AND session_id=%s ORDER BY created_at,call_id', scope)
        result = {'case_id': case_id, 'session_id': session_id,
                  'identity': {'tenant_id': 332, 'subject_sha256': sha(principal.subject_id), **identity},
                  'provider_calls': providers, 'ledger': read_json(ledger_template(case_id)['path']),
                  'read_only_select_count_scope': 'acceptance_audit_only',
                  'rag_database_query_count': 'NOT_MEASURED'}
        if full:
            result['sessions'] = await rows('SELECT id,tenant_id,knowledge_base_scope,document_scope,context_config,created_at FROM sessions WHERE tenant_id=%s AND subject_id=%s AND id=%s', scope)
            result['turns'] = await rows('SELECT run_id,session_id,client_request_id,user_message_id,assistant_message_id,status,error_code,knowledge_base_scope,document_scope,request_configuration,created_at,updated_at FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s', scope)
            result['messages'] = await rows('SELECT id,request_id,role,content,rendered_content,knowledge_references,is_completed,turn_status,turn_error_code,created_at FROM messages WHERE tenant_id=%s AND subject_id=%s AND session_id=%s ORDER BY created_at,id', scope)
            result['generation_runs'] = await rows('SELECT run_id,message_id,payload_sha256,knowledge_base_ids,document_ids FROM generation_runs WHERE tenant_id=%s AND subject_id=%s AND session_id=%s', scope)
            result['generation_attempts'] = await rows('SELECT a.run_id,a.attempt_id,a.ordinal,a.role,a.provider,a.model_requested,a.model_reported,a.status,a.finish_reason,a.usage,a.objective,a.error_code,a.prompt_sha256,a.context_sha256,a.started_at,a.finished_at FROM generation_attempts a JOIN generation_runs r ON r.run_id=a.run_id AND r.tenant_id=a.tenant_id AND r.subject_id=a.subject_id WHERE r.tenant_id=%s AND r.subject_id=%s AND r.session_id=%s ORDER BY a.ordinal', scope)
            result['kb'] = await rows('SELECT id,tenant_id,cloud_allowed FROM knowledge_bases WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL', (332, case['knowledge_base_id']))
        if source_ids is not None:
            result['source_documents'] = await rows("SELECT id,tenant_id,knowledge_base_id,file_hash,metadata->>'source_version' AS source_version,parse_status FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s AND id=ANY(%s) AND deleted_at IS NULL", (332, case['knowledge_base_id'], source_ids))
        await conn.execute('ROLLBACK')
        result['read_only_select_count'] = select_count
        return result
    finally:
        await conn.close()


def same_provider_ledger(before, after):
    return (canonical(before['provider_calls']) == canonical(after['provider_calls'])
            and canonical(before['ledger']) == canonical(after['ledger']))


def verify_source_documents(sources, snapshot):
    rows = snapshot['source_documents']
    if len(rows) != len(sources):
        raise RuntimeError('S8_3_CONTEXT_SOURCE_NOT_CURRENT')
    for source in sources:
        matching = [r for r in rows if r['id'] == source['document_id']]
        if (len(matching) != 1 or matching[0]['tenant_id'] != 332
                or matching[0]['knowledge_base_id'] != source['knowledge_base_id']
                or matching[0]['source_version'] != source['source_version']
                or matching[0]['file_hash'] != source['expected_sha256']
                or matching[0]['parse_status'] != 'completed'):
            raise RuntimeError('S8_3_CONTEXT_SOURCE_NOT_CURRENT')


def submit(args):
    import httpx
    from scripts.s8_ecs_runtime import api_token
    case = case_for(args.case_id)
    request_id = os.environ['S8_3_REQUEST_ID']
    case_dir = RUN / 'cases' / sha(args.case_id)
    identity = read_json(case_dir / 'runtime-identity.json')
    if (identity.get('rerank_top1_floorless') is not False or identity.get('graph_enabled') is not False):
        raise RuntimeError('S8_3_RUNTIME_IDENTITY_FILE_MISMATCH')
    write_new(case_dir / 'submit-claim.json', {'case_id': args.case_id, 'request_id': request_id})
    token = api_token(os.environ)
    origin = f'http://127.0.0.1:{args.port}'
    headers = {'Authorization': 'Bearer ' + token, 'Origin': origin, 'Accept-Language': 'zh-CN'}
    title = f"S8.3 {case['dataset']} {case['qid']} {case['source_evidence_mode']}"
    trace = case_dir / 'sse.jsonl'
    result = {'case_id': args.case_id, 'request_id': request_id, 'session_id': None,
              'http_status': None, 'sse_complete': False, 'events': [], 'history': [], 'histories': [],
              'sources': [], 'source_status': 'NOT_READ', 'checks': {}, 'retry_allowed': False,
              'stream_read_timeout_seconds': 1800, 'stream_elapsed_limit_seconds': 1800}
    started = time.monotonic()
    post_started = first_event = first_content = complete_at = None
    try:
        with httpx.Client(timeout=httpx.Timeout(1800, connect=15), follow_redirects=False, trust_env=False) as client:
            for path in ('/api/v1/runtime', '/api/v1/settings/model-configuration', '/api/v1/knowledge-bases'):
                value = read_response(client.get(origin + path, headers=headers, timeout=20))
                if path.endswith('/knowledge-bases'):
                    rows = value.get('items', value) if isinstance(value, dict) else value
                    visible = [x for x in rows if isinstance(x, dict) and (x.get('id') or x.get('knowledge_base_id')) == case['knowledge_base_id']] if isinstance(rows, list) else []
                    if len(visible) != 1 or visible[0].get('cloud_allowed') is not True:
                        raise RuntimeError('S8_3_KB_NOT_VISIBLE_OR_CLOUD_PERMISSION_MISSING')
            session = read_response(client.post(origin + '/api/v1/sessions', headers={**headers, 'Accept': 'application/json'},
                json={'knowledge_base_scope': [case['knowledge_base_id']], 'document_scope': [], 'title': title, 'source_evidence_mode': case['source_evidence_mode']}, timeout=30))
            if not isinstance(session, dict) or not isinstance(session.get('id'), str):
                raise RuntimeError('S8_3_SESSION_RESPONSE_INVALID')
            session_id = result['session_id'] = session['id']
            write_new(case_dir / 'session-id.json', {'case_id': args.case_id, 'session_id': session_id,
                'request_id': request_id, 'tenant_id': identity.get('tenant_id'), 'subject_sha256': identity.get('subject_sha256')})
            post_started = time.monotonic()
            result['question_post_started_monotonic'] = post_started
            with trace.open('x', encoding='utf-8') as output:
                with client.stream('POST', origin + f'/api/v1/sessions/{session_id}/messages',
                                   headers={**headers, 'Accept': 'text/event-stream'}, json=api_payload(case, request_id)) as response:
                    result['http_status'] = response.status_code
                    if not response.is_success:
                        raise RuntimeError('S8_3_QUESTION_HTTP_' + str(response.status_code))
                    pending = []
                    def consume():
                        nonlocal first_content, complete_at
                        event = json.loads('\n'.join(pending))
                        pending.clear()
                        result['events'].append(event)
                        if first_content is None and isinstance(event.get('content'), str) and event['content'].strip():
                            first_content = time.monotonic()
                        if event.get('response_type') == 'complete':
                            result['sse_complete'] = True
                            complete_at = time.monotonic()
                    for line in response.iter_lines():
                        if time.monotonic() - post_started > 1800:
                            raise RuntimeError('S8_3_STREAM_DEADLINE_NO_RETRY')
                        output.write(line + '\n'); output.flush()
                        if line.startswith('data:'):
                            if first_event is None:
                                first_event = time.monotonic()
                            pending.append(line[5:].lstrip())
                        elif not line and pending:
                            consume()
                            if result['sse_complete']:
                                break
                    if pending and not result['sse_complete']:
                        consume()
            if not result['sse_complete']:
                raise RuntimeError('S8_3_SSE_COMPLETE_NOT_OBSERVED_NO_RETRY')
            before = asyncio.run(database_snapshot(args.case_id, session_id, full=True))
            result['provider_before_history'] = before
            for index in range(2):
                history = read_response(client.get(origin + f'/api/v1/sessions/{session_id}/messages', headers=headers, timeout=30))
                result['histories'].append(history)
                result['provider_after_history_' + str(index + 1)] = asyncio.run(database_snapshot(args.case_id, session_id))
            history_complete_at = time.monotonic()
            result['post_to_history_total_ms'] = round((history_complete_at - post_started) * 1000, 2)
            result['history'] = result['histories'][-1]
            turns = before['turns']
            if len(turns) != 1 or turns[0]['client_request_id'] != request_id:
                raise RuntimeError('S8_3_SOURCE_TURN_NOT_UNIQUE')
            run_id = result['server_run_id'] = turns[0]['run_id']
            stage_path = Path(before['ledger']['evidence_dir']) / (sha(run_id) + '.stages.json')
            stage = read_json(stage_path)
            if stage.get('run_id') != run_id or stage.get('session_id') != session_id:
                raise RuntimeError('S8_3_CONTEXT_STAGE_IDENTITY_MISMATCH')
            result['trace_observation']=trace_observation(stage,before['ledger'],case,session_id,run_id)
            sources = context_sources(stage, case['knowledge_base_id'],result['events'],before['messages'])
            result['context_outcome']='SearchNothing_fallback_no_finalized_source_facts' if stage.get('error_type')=='SearchNothing' else 'finalized_context'
            result['sources'] = sources
            result['context_stage_file'] = str(stage_path)
            result['context_stage_sha256'] = sha(stage_path.read_bytes())
            source_ids = [source['document_id'] for source in sources]
            source_before = asyncio.run(database_snapshot(args.case_id, session_id, source_ids=source_ids))
            verify_source_documents(sources, source_before)
            result['source_database_before'] = source_before
            for source in sources:
                path = f"/api/v1/knowledge-bases/{case['knowledge_base_id']}/documents/{source['document_id']}/source"
                response = client.get(origin + path, headers=headers, timeout=60)
                source.update({'http_status': response.status_code, 'actual_sha256': sha(response.content), 'bytes': len(response.content)})
                if not response.is_success or source['actual_sha256'] != source['expected_sha256']:
                    raise RuntimeError('S8_3_CONTEXT_SOURCE_HTTP_OR_HASH_MISMATCH')
            source_after = asyncio.run(database_snapshot(args.case_id, session_id, source_ids=source_ids))
            result['source_database_after'] = source_after
            verify_source_documents(sources, source_after)
            result['source_status'] = 'PASS' if sources else 'none'
            rows = result['history'].get('items', result['history']) if isinstance(result['history'], dict) else result['history']
            assistants = [row for row in rows if isinstance(row, dict) and row.get('role') == 'assistant'] if isinstance(rows, list) else []
            persisted = assistants[0] if len(assistants) == 1 else None
            answer = ''.join(str(e.get('content', '')) for e in result['events'] if e.get('response_type') == 'answer')
            result.update({'sse_answer': answer, 'persisted_assistant_found': persisted is not None,
                'persisted_assistant_request_id': persisted.get('request_id') if persisted else None,
                'sse_history_content_match': bool(persisted is not None and persisted.get('content') == answer)})
            result['checks'] = {
                'passive_trace_and_wire': result.get('trace_observation',{}).get('status')=='PASS',
                'sse_complete': result['sse_complete'],
                'two_history_data_equal': canonical(result['histories'][0]) == canonical(result['histories'][1]),
                'history_one_no_provider_or_ledger_change': same_provider_ledger(before, result['provider_after_history_1']),
                'history_two_no_provider_or_ledger_change': same_provider_ledger(before, result['provider_after_history_2']),
                'source_reads_no_provider_or_ledger_change': same_provider_ledger(before, source_after),
                'source_documents_unchanged': canonical(source_before['source_documents']) == canonical(source_after['source_documents']),
                'source_hashes_match': all(x.get('http_status') == 200 and x.get('actual_sha256') == x['expected_sha256'] for x in sources),
                'sse_history_content_match': result['sse_history_content_match'],
            }
            if not all(result['checks'].values()):
                raise RuntimeError('S8_3_HTTP_ACCEPTANCE_CHECK_FAILED')
    except Exception as error:
        result['error_code'] = str(error) if isinstance(error, RuntimeError) and str(error).startswith('S8_3_') else type(error).__name__
        raise
    finally:
        result['event_count'] = len(result['events'])
        result['first_sse_event_ms'] = round((first_event-post_started)*1000, 2) if first_event is not None else None
        result['first_nonempty_content_ms'] = round((first_content-post_started)*1000, 2) if first_content is not None else None
        result['ttfc_ms'] = result['first_nonempty_content_ms']
        result['complete_event_ms'] = round((complete_at-post_started)*1000, 2) if complete_at is not None else None
        result['post_to_complete_ms'] = result['complete_event_ms']
        result['total_duration_ms'] = round((time.monotonic()-started)*1000, 2)
        result['evidence_file'] = str(trace)
        result['status'] = 'PASS' if result['checks'] and all(result['checks'].values()) and 'error_code' not in result else 'FAIL'
        write_new(case_dir / 'http-result.json', result)
    print(canonical({'status': 'QUESTION_COMPLETE', 'case_id': args.case_id, 'request_id': request_id,
        'session_id': result['session_id'], 'sse_complete': result['sse_complete'], 'event_count': result['event_count'],
        'post_to_history_total_ms': result['post_to_history_total_ms'], 'post_to_complete_ms': result['post_to_complete_ms'],
        'ttfc_ms': result['ttfc_ms'], 'provider_calls': None}))


def recover_readonly(args):
    import httpx
    from scripts.s8_ecs_runtime import api_token
    case=case_for(args.case_id);case_dir=RUN/'cases'/sha(args.case_id)
    original_path=case_dir/'http-result.json';original=read_json(original_path)
    session=read_json(case_dir/'session-id.json')
    session_id=session['session_id'];request_id=session['request_id']
    if (original.get('status')!='FAIL' or original.get('error_code')!='S8_3_WIRE_RESPONSE_INCOMPLETE'
            or original.get('sse_complete') is not True or original.get('session_id')!=session_id
            or original.get('request_id')!=request_id or args.base_url not in ('http://api:8000','http://127.0.0.1:18800')):
        raise RuntimeError('S8_3_READONLY_RECOVERY_NOT_ELIGIBLE')
    ledger=read_json(ledger_template(args.case_id)['path'])
    if (ledger.get('status')!='STOP' or not ledger.get('entries')
            or any(e.get('status')!='SETTLED' or (e.get('usage') or {}).get('status')!='REPORTED' for e in ledger['entries'])):
        raise RuntimeError('S8_3_READONLY_RECOVERY_LEDGER_NOT_SETTLED_STOP')
    saved_events=[];pending=[]
    for line in (case_dir/'sse.jsonl').read_text(encoding='utf-8').splitlines()+['']:
        if line.startswith('data:'):pending.append(line[5:].lstrip())
        elif not line and pending:
            saved_events.append(json.loads('\n'.join(pending)));pending=[]
    if canonical(saved_events)!=canonical(original.get('events')) or not any(e.get('response_type')=='complete' for e in saved_events):
        raise RuntimeError('S8_3_RECOVERY_SAVED_SSE_MISMATCH')
    result=json.loads(canonical(original));result.pop('error_code',None)
    result['original_failed_http_sha256']=sha(original_path.read_bytes())
    result['recovery_mode']='read_only_revalidation_no_question_retry'
    result['histories']=[];result['checks']={};result['retry_allowed']=False
    origin=args.base_url;headers={'Authorization':'Bearer '+api_token(os.environ)}
    post_started=time.monotonic()
    try:
        with httpx.Client(timeout=60,follow_redirects=False,trust_env=False) as client:
            before = asyncio.run(database_snapshot(args.case_id, session_id, full=True))
            result['provider_before_history'] = before
            for index in range(2):
                history = read_response(client.get(origin + f'/api/v1/sessions/{session_id}/messages', headers=headers, timeout=30))
                result['histories'].append(history)
                result['provider_after_history_' + str(index + 1)] = asyncio.run(database_snapshot(args.case_id, session_id))
            history_complete_at = time.monotonic()
            result['post_to_history_total_ms'] = round((history_complete_at - post_started) * 1000, 2)
            result['history'] = result['histories'][-1]
            turns = before['turns']
            if len(turns) != 1 or turns[0]['client_request_id'] != request_id:
                raise RuntimeError('S8_3_SOURCE_TURN_NOT_UNIQUE')
            run_id = result['server_run_id'] = turns[0]['run_id']
            stage_path = Path(before['ledger']['evidence_dir']) / (sha(run_id) + '.stages.json')
            stage = read_json(stage_path)
            if stage.get('run_id') != run_id or stage.get('session_id') != session_id:
                raise RuntimeError('S8_3_CONTEXT_STAGE_IDENTITY_MISMATCH')
            result['trace_observation']=trace_observation(stage,before['ledger'],case,session_id,run_id)
            sources = context_sources(stage, case['knowledge_base_id'],result['events'],before['messages'])
            result['context_outcome']='SearchNothing_fallback_no_finalized_source_facts' if stage.get('error_type')=='SearchNothing' else 'finalized_context'
            result['sources'] = sources
            result['context_stage_file'] = str(stage_path)
            result['context_stage_sha256'] = sha(stage_path.read_bytes())
            source_ids = [source['document_id'] for source in sources]
            source_before = asyncio.run(database_snapshot(args.case_id, session_id, source_ids=source_ids))
            verify_source_documents(sources, source_before)
            result['source_database_before'] = source_before
            for source in sources:
                path = f"/api/v1/knowledge-bases/{case['knowledge_base_id']}/documents/{source['document_id']}/source"
                response = client.get(origin + path, headers=headers, timeout=60)
                source.update({'http_status': response.status_code, 'actual_sha256': sha(response.content), 'bytes': len(response.content)})
                if not response.is_success or source['actual_sha256'] != source['expected_sha256']:
                    raise RuntimeError('S8_3_CONTEXT_SOURCE_HTTP_OR_HASH_MISMATCH')
            source_after = asyncio.run(database_snapshot(args.case_id, session_id, source_ids=source_ids))
            result['source_database_after'] = source_after
            verify_source_documents(sources, source_after)
            result['source_status'] = 'PASS' if sources else 'none'
            rows = result['history'].get('items', result['history']) if isinstance(result['history'], dict) else result['history']
            assistants = [row for row in rows if isinstance(row, dict) and row.get('role') == 'assistant'] if isinstance(rows, list) else []
            persisted = assistants[0] if len(assistants) == 1 else None
            answer = ''.join(str(e.get('content', '')) for e in result['events'] if e.get('response_type') == 'answer')
            result.update({'sse_answer': answer, 'persisted_assistant_found': persisted is not None,
                'persisted_assistant_request_id': persisted.get('request_id') if persisted else None,
                'sse_history_content_match': bool(persisted is not None and persisted.get('content') == answer)})
            result['checks'] = {
                'passive_trace_and_wire': result.get('trace_observation',{}).get('status')=='PASS',
                'sse_complete': result['sse_complete'],
                'two_history_data_equal': canonical(result['histories'][0]) == canonical(result['histories'][1]),
                'history_one_no_provider_or_ledger_change': same_provider_ledger(before, result['provider_after_history_1']),
                'history_two_no_provider_or_ledger_change': same_provider_ledger(before, result['provider_after_history_2']),
                'source_reads_no_provider_or_ledger_change': same_provider_ledger(before, source_after),
                'source_documents_unchanged': canonical(source_before['source_documents']) == canonical(source_after['source_documents']),
                'source_hashes_match': all(x.get('http_status') == 200 and x.get('actual_sha256') == x['expected_sha256'] for x in sources),
                'sse_history_content_match': result['sse_history_content_match'],
            }
            if not all(result['checks'].values()):
                raise RuntimeError('S8_3_HTTP_ACCEPTANCE_CHECK_FAILED')
        result['recovery_readonly_elapsed_ms']=round((time.monotonic()-post_started)*1000,2)
        result['post_to_history_total_ms']=original.get('post_to_history_total_ms')
        result['status']='PASS'
    except Exception as error:
        result['status']='FAIL';result['recovery_error_code']=str(error) if isinstance(error,RuntimeError) else type(error).__name__
        raise
    finally:
        write_new(case_dir/'recovered-http-result.json',result)
    followup=argparse.Namespace(case_id=args.case_id,recovered=True)
    audit(followup)
    close_ledger(followup)
    closed=read_json(ledger_template(args.case_id)['path'])
    summary={'status':'RECOVERED_CASE_CLOSED','case_id':args.case_id,'session_id':session_id,
             'request_id':request_id,'source_evidence_mode':case['source_evidence_mode'],
             'provider_calls':len(closed['entries']),
             'held_tokens':sum(e['held_tokens'] for e in closed['entries']),
             'reported_tokens':sum(e['usage']['total_tokens'] for e in closed['entries']),
             'question_posts':0,'provider_sends':0,'original_question_posts':1,
             'original_failed_http_sha256':result['original_failed_http_sha256'],
             'post_to_complete_ms':original.get('post_to_complete_ms'),
             'post_to_history_total_ms':original.get('post_to_history_total_ms'),
             'http_result':'recovered-http-result.json','postgres_audit':'postgres-audit.json',
             'parent_progress_modified':False,'ledger_status':closed['status']}
    write_new(case_dir/'recovered-case-summary.json',summary)
    print(canonical(summary))


def audit_checks(case, session_id, result, http):
    providers, entries = result['provider_calls'], result['ledger']['entries']
    turns, sessions, messages, kb = result['turns'], result['sessions'], result['messages'], result['kb']
    assistants = [m for m in messages if m.get('role') == 'assistant']
    users = [m for m in messages if m.get('role') == 'user']
    turn = turns[0] if len(turns) == 1 else None
    persisted = assistants[0] if len(assistants) == 1 else None
    run_id = turn.get('run_id') if turn else None
    fields = ('request_id', 'capability', 'provider', 'model', 'model_id', 'wire_sha256', 'outcome', 'usage')
    keys = lambda rows: sorted(canonical({key: row.get(key) for key in fields}) for row in rows)
    attempts, runs = result['generation_attempts'], result['generation_runs']
    checks = {
        'one_session': len(sessions) == 1 and sessions[0]['id'] == session_id and sessions[0]['tenant_id'] == 332,
        'persisted_source_mode': len(sessions)==1 and sessions[0].get('context_config',{}).get('conversation',{}).get('source_evidence_mode')==case['source_evidence_mode'],
        'session_scope': len(sessions) == 1 and sessions[0]['knowledge_base_scope'] == [case['knowledge_base_id']] and sessions[0]['document_scope'] == [],
        'kb_visible_in_scope': len(kb) == 1 and kb[0].get('tenant_id') == 332 and kb[0].get('id') == case['knowledge_base_id'],
        'kb_cloud_allowed': len(kb) == 1 and kb[0].get('cloud_allowed') is True,
        'one_turn': bool(turn and turn['client_request_id'] == http.get('request_id') and turn['session_id'] == session_id and turn['status'] == 'completed'),
        'turn_scope': bool(turn and turn['knowledge_base_scope'] == [case['knowledge_base_id']] and turn['document_scope'] == []),
        'one_original_question': bool(turn and len(users) == 1 and users[0]['content'] == case['question'] and users[0]['id'] == turn['user_message_id']),
        'assistant_completed': bool(persisted and turn and persisted['id'] == turn['assistant_message_id'] and persisted.get('is_completed') is True and persisted.get('turn_status') not in ('error', 'cancelled')),
        'sse_complete': http.get('sse_complete') is True,
        'sse_history_match': http.get('sse_history_content_match') is True,
        'sse_database_match': bool(persisted and persisted.get('content') == http.get('sse_answer')),
        'provider_rows_match_ledger': bool(providers) and keys(providers) == keys(entries),
        'provider_exact_ledger_match': bool(providers) and keys(providers) == keys(entries),
        'provider_server_run_scope': bool(run_id and providers) and all(row.get('session_id') == session_id and (row['request_id'] == run_id or row['request_id'].startswith(run_id + '/')) for row in providers),
        'all_provider_usage_reported': bool(providers) and all(row.get('outcome') == 'RESPONSE_RECEIVED' and row.get('finished_at') is not None and isinstance(row.get('usage'), dict) and row['usage'].get('status') == 'REPORTED' for row in providers),
        'ledger_all_settled': bool(entries) and all(row.get('status') == 'SETTLED' and isinstance(row.get('usage'), dict) and row['usage'].get('status') == 'REPORTED' for row in entries),
        'generation_run_scope': len(runs) == 1 and bool(turn) and runs[0]['run_id'] == run_id and runs[0]['message_id'] == turn['assistant_message_id'] and runs[0]['knowledge_base_ids'] == [case['knowledge_base_id']] and runs[0]['document_ids'] == [],
        'generation_completed': bool(attempts) and all(row['run_id'] == run_id and row.get('status') == 'COMPLETED' and bool(row.get('finish_reason')) and row.get('finished_at') is not None and isinstance(row.get('usage'), dict) and row['usage'].get('status') == 'REPORTED' for row in attempts),
        'http_checks_pass': http.get('status') == 'PASS' and bool(http.get('checks')) and all(http['checks'].values()),
        'context_source_readback': http.get('source_status') in ('PASS', 'none') and isinstance(http.get('sources'), list),
        'history_ledger_still_exact': canonical(result['ledger']) == canonical(http.get('provider_before_history', {}).get('ledger')),
    }
    return checks


async def _audit_case(case_id, session_id, recovered=False):
    case = case_for(case_id)
    result = await database_snapshot(case_id, session_id, full=True)
    http = read_json(RUN / 'cases' / sha(case_id) / ('recovered-http-result.json' if recovered else 'http-result.json'))
    result['checks'] = audit_checks(case, session_id, result, http)
    sql_path=RUN/'cases'/sha(case_id)/'rag-sql-observation.json'
    result['rag_sql_observation']=read_json(sql_path) if sql_path.is_file() else {'status':'NOT_MEASURED'}
    if 'execute_calls' in result['rag_sql_observation']:
        result['rag_database_query_count']=result['rag_sql_observation']['execute_calls']
    result['audit_status'] = 'PASS' if all(result['checks'].values()) else 'FAIL'
    return result

def audit(args):
 session_file=read_json(RUN/'cases'/sha(args.case_id)/'session-id.json')
 value=asyncio.run(_audit_case(args.case_id,session_file['session_id'],getattr(args,'recovered',False)))
 write_new(RUN/'cases'/sha(args.case_id)/'postgres-audit.json',value)
 print(json.dumps({'status':'POSTGRES_AUDIT_'+value['audit_status'],'case_id':args.case_id,'session_id':session_file['session_id'],
  'tenant_id':332,'provider_rows':len(value['provider_calls']),'message_rows':len(value['messages']),
  'turn_rows':len(value['turns']),'read_only_select_count':value['read_only_select_count'],'checks':value['checks']}))
 if value['audit_status']!='PASS':raise RuntimeError('S8_3_POSTGRES_AUDIT_FAILED')

def close_ledger(args):
    from backend.weknora_port.execution import _validate_ledger, _LOCK
    case = case_for(args.case_id)
    path = Path(ledger_template(args.case_id)['path'])
    case_dir = RUN / 'cases' / sha(args.case_id)
    session = read_json(case_dir / 'session-id.json')
    session_claim = read_json(case_dir / 'session-create-claim.json')
    claim = read_json(case_dir / 'question-post-claim.json')
    submit_claim = read_json(case_dir / 'submit-claim.json')
    recovered=getattr(args,'recovered',False)
    http = read_json(case_dir / ('recovered-http-result.json' if recovered else 'http-result.json'))
    if recovered:
        original=read_json(case_dir/'http-result.json')
        if (original.get('status')!='FAIL' or original.get('error_code')!='S8_3_WIRE_RESPONSE_INCOMPLETE'
                or http.get('original_failed_http_sha256')!=sha((case_dir/'http-result.json').read_bytes())):
            raise RuntimeError('S8_3_RECOVERY_PROVENANCE_MISMATCH')
    audit = read_json(case_dir / 'postgres-audit.json')
    request_id, session_id = session['request_id'], session['session_id']
    if (session.get('case_id') != args.case_id or session.get('tenant_id') != 332
            or session_claim != {'case_id': args.case_id, 'kb': case['knowledge_base_id']}
            or session.get('subject_sha256') != PLAN['principal']['subject_sha256']
            or claim != {'case_id': args.case_id, 'session_id': session_id, 'request_id': request_id,
                         'question_sha256': sha(case['question'])}
            or submit_claim != {'case_id': args.case_id, 'request_id': request_id}
            or http.get('case_id') != args.case_id or http.get('session_id') != session_id
            or http.get('request_id') != request_id or audit.get('case_id') != args.case_id
            or audit.get('session_id') != session_id or audit.get('audit_status') != 'PASS'
            or not audit.get('checks') or not all(audit['checks'].values())):
        raise RuntimeError('S8_3_LEDGER_CLOSE_CLAIM_MISMATCH')
    # The formal execution writer uses this same stable sidecar and process lock.
    with _LOCK, path.with_suffix(path.suffix + '.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            data = read_json(path)
            _validate_ledger(data)
            if (data.get('status') != ('STOP' if recovered else 'OPEN') or data.get('case_id') != args.case_id
                    or data.get('tenant_id') != 332 or data.get('allowed_kbs') != [case['knowledge_base_id']]
                    or sha(data.get('subject_id', '')) != PLAN['principal']['subject_sha256']
                    or canonical(data) != canonical(audit.get('ledger'))
                    or canonical(data) != canonical(http.get('provider_before_history', {}).get('ledger'))
                    or not all(audit_checks(case, session_id, {**audit, 'ledger': data}, http).values())):
                raise RuntimeError('S8_3_LEDGER_CLOSE_PRECONDITION_FAILED')
            original_entries = canonical(data['entries'])
            data['status'] = 'CLOSED'
            _validate_ledger(data)
            if canonical(data['entries']) != original_entries:
                raise RuntimeError('S8_3_LEDGER_ENTRIES_CHANGED')
            safe_write(path, data)
            closed = read_json(path)
            _validate_ledger(closed)
            if canonical(closed) != canonical(data) or canonical(closed['entries']) != original_entries:
                raise RuntimeError('S8_3_LEDGER_CLOSE_READBACK_FAILED')
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    print(canonical({'status': 'LEDGER_CLOSED', 'case_id': args.case_id, 'provider_calls': len(data['entries']),
                     'reported_tokens': sum(x['usage']['total_tokens'] for x in data['entries']),
                     'entries_unchanged': True}))



def response_terminal_proof(record, response, raw, entry):
    if response.get('status_code')!=200 or entry.get('status')!='SETTLED' or entry.get('outcome')!='RESPONSE_RECEIVED':
        raise RuntimeError('S8_3_WIRE_RESPONSE_INCOMPLETE')
    usage=entry.get('usage') or {}
    if usage.get('status')!='REPORTED':raise RuntimeError('S8_3_WIRE_RESPONSE_INCOMPLETE')
    if response.get('complete') is True:return {'transport_eof_observed':True,'protocol_terminal_proved':False}
    if response.get('complete') is not False or record.get('capability')!='chat' or record.get('body',{}).get('stream') is not True:
        raise RuntimeError('S8_3_WIRE_RESPONSE_INCOMPLETE')
    try:
        events=[];pending=[]
        for line in raw.decode('utf-8',errors='strict').splitlines()+['']:
            if line.startswith('data:'):pending.append(line[5:].lstrip())
            elif not line and pending:
                events.append('\n'.join(pending));pending=[]
        if not events or events[-1]!='[DONE]' or events.count('[DONE]')!=1:raise ValueError()
        frames=[json.loads(event) for event in events[:-1]]
        terminal=[c.get('finish_reason') for f in frames for c in f.get('choices',[]) if c.get('finish_reason')]
        reported=[f['usage'] for f in frames if isinstance(f.get('usage'),dict)]
        if not terminal or terminal[-1]!='stop' or not reported:raise ValueError()
        final=reported[-1]
        if any(type(final.get(k)) is not int or final[k]!=usage.get(k)
               for k in ('prompt_tokens','completion_tokens','total_tokens')):raise ValueError()
    except (ValueError,TypeError,KeyError,UnicodeError):
        raise RuntimeError('S8_3_WIRE_RESPONSE_INCOMPLETE') from None
    return {'transport_eof_observed':False,'protocol_terminal_proved':True,
            'terminal':'openai_sse_finish_stop_usage_then_DONE','raw_body_sha256':sha(raw)}

def trace_observation(stage, ledger, case, session_id, run_id):
    """Inspect normal captures without modifying model input or pipeline state."""
    if str(uuid.UUID(run_id))!=run_id or stage.get('run_id')!=run_id or stage.get('session_id')!=session_id:
        raise RuntimeError('S8_3_TRACE_RUN_IDENTITY_MISMATCH')
    completed=[x for x in stage.get('stages',[]) if x.get('status')=='completed' and isinstance(x.get('state'),dict)]
    if not completed or any(x['state'].get('source_evidence_mode')!=case['source_evidence_mode']
                            or x['state'].get('graph_enabled') is not False for x in completed):
        raise RuntimeError('S8_3_TRACE_EFFECTIVE_MODE_MISMATCH')
    records=[]
    for path in Path(ledger['evidence_dir']).glob('*.request.json'):
        row=read_json(path)
        if row.get('request_id')==run_id or str(row.get('request_id','')).startswith(run_id+'/'):
            if row.get('headers_saved') is not False:
                raise RuntimeError('S8_3_WIRE_HEADERS_NOT_REDACTED')
            wire=canonical(row['body']).encode('utf-8')
            response=path.with_name(path.name.replace('.request.json','.response.json'))
            raw=path.with_name(path.name.replace('.request.json','.response.body'))
            if not response.is_file() or not raw.is_file():
                raise RuntimeError('S8_3_WIRE_RESPONSE_INCOMPLETE')
            row['response_metadata']=read_json(response);row['raw_response_body']=raw.read_bytes()
            row['wire_observed_sha256']=sha(wire)
            records.append(row)
    entries=ledger['entries'];fields=('request_id','capability','provider','model','model_id')
    if len(records)!=len(entries) or not records:
        raise RuntimeError('S8_3_WIRE_LEDGER_COUNT_MISMATCH')
    for entry in entries:
        matches=[r for r in records if all(r.get(k)==entry.get(k) for k in fields)]
        if len(matches)!=1 or matches[0]['wire_observed_sha256']!=entry['wire_sha256']:
            raise RuntimeError('S8_3_WIRE_LEDGER_HASH_MISMATCH')
        matches[0]['terminal_proof']=response_terminal_proof(matches[0],matches[0]['response_metadata'],matches[0]['raw_response_body'],entry)
    return {'status':'PASS','server_run_id':run_id,'completed_stages':len(completed),'actual_wire_records':len(records),
            'source_evidence_mode':case['source_evidence_mode'],'graph_enabled':False,
            'wire_terminal_proofs':[{'request_id':r['request_id'],**r['terminal_proof']} for r in records],
            'evidence_trace':completed[-1]['state'].get('evidence_trace'),
            'finalized_context':stage.get('finalized_context')}


def main():
 p=argparse.ArgumentParser();sp=p.add_subparsers(dest='cmd',required=True)
 i=sp.add_parser('verify-image');i.set_defaults(fn=verify_image)
 x=sp.add_parser('recover-readonly');x.add_argument('--case-id',required=True);x.add_argument('--base-url',required=True);x.set_defaults(fn=recover_readonly)
 s=sp.add_parser('serve');s.add_argument('--case-id',required=True);s.add_argument('--port',type=int,default=18000);s.set_defaults(fn=serve)
 r=sp.add_parser('ready');r.add_argument('--port',type=int,default=18000);r.set_defaults(fn=ready)
 q=sp.add_parser('submit');q.add_argument('--case-id',required=True);q.add_argument('--port',type=int,default=18000);q.set_defaults(fn=submit)
 z=sp.add_parser('audit');z.add_argument('--case-id',required=True);z.add_argument('--recovered',action='store_true');z.set_defaults(fn=audit)
 c=sp.add_parser('close-ledger');c.add_argument('--case-id',required=True);c.add_argument('--recovered',action='store_true');c.set_defaults(fn=close_ledger)
 a=p.parse_args();a.fn(a)
if __name__=='__main__':main()
