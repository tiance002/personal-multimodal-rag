"""Durable, serial controller for one frozen S8.3 normal-API question."""
from __future__ import annotations
import argparse, copy, fcntl, hashlib, json, os, re, subprocess, sys, time, uuid
from pathlib import Path

RUN=Path(os.environ.get('S8_3_RUN_ROOT','/opt/rag-s8/state/s8-3-r-20261020'))
PAYLOAD=RUN/'payload'
EXEC=Path('/opt/rag-s8/state/executions')
PLAN=json.loads((PAYLOAD/'authorized-plan.json').read_text(encoding='utf-8'))
OWNER=json.loads((PAYLOAD/'owner-authorization.json').read_text(encoding='utf-8'))
TEMPLATES=json.loads((PAYLOAD/'ledger-templates.json').read_text(encoding='utf-8'))['ledgers']
RESOURCE=json.loads((PAYLOAD/'resource-identity-freeze.json').read_text(encoding='utf-8'))
ORDER=[
 'miracl-zh-91294#0::source-off','miracl-zh-91294#0::source-bounded',
 'scifact-1101::source-off','scifact-1101::source-bounded',
 'scifact-241::source-off','scifact-241::source-bounded',
 'hotpotqa-5a7631cd5542994ccc91870c::source-off','hotpotqa-5a7631cd5542994ccc91870c::source-bounded',
 'hotpotqa-5a729fe55542991f9a20c53b::source-off','hotpotqa-5a729fe55542991f9a20c53b::source-bounded',
 'engineering-unanswerable-postcode::source-off','engineering-unanswerable-postcode::source-bounded']
COMPOSE=Path('/opt/rag-s8/current/deploy/s8/compose.yml')
IMAGES=Path('/opt/rag-s8/config/loaded-images.json')
ENVFILE=Path('/opt/rag-s8/config/runtime.env')
SOURCE_MANIFEST=PAYLOAD/'source-manifest.json'
REMOTE_MAX_CALLS=60
REMOTE_MAX_TOKENS=200000
MEMORY_LIMIT_BYTES=1073741824
HELPER=RUN/'tools'/'s8_3_r_ecs_acceptance.py'
PROGRESS=RUN/'parent-progress.json'
LOCK=RUN/'parent-progress.lock'
STEP='STARTING'
SECRET_VALUES=set()

def canonical(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(x): return hashlib.sha256(x.encode('utf-8') if isinstance(x,str) else x).hexdigest()
def case_hash(case_id): return sha(case_id)
def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write_new(path,value):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def atomic(path,value):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name(p.name+'.tmp-'+uuid.uuid4().hex)
 with tmp.open('x',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 os.replace(tmp,p)
def state_path(case_id): return RUN/'cases'/case_hash(case_id)/'controller-status.json'
def set_status(case_id,status,**extra):
 atomic(state_path(case_id),{'case_id':case_id,'status':status,'step':STEP,'updated_at':time.time(),**extra})
def case_row(case_id):
 rows=[x for x in PLAN.get('cases',[]) if x.get('case_id')==case_id]
 if len(rows)!=1: raise RuntimeError('CASE_NOT_UNIQUE')
 c=rows[0]
 if c.get('tenant_id')!=332 or c.get('document_scope')!=[]: raise RuntimeError('CASE_SCOPE_INVALID')
 if c.get('knowledge_base_id') not in c.get('knowledge_base_scope',[c.get('knowledge_base_id')]): raise RuntimeError('CASE_KB_SCOPE_INVALID')
 if sha(c['question'])!=c.get('frozen_query_sha256_utf8'): raise RuntimeError('QUESTION_HASH_INVALID')
 return c
def template(case_id):
 rows=[x for x in TEMPLATES if x['ledger'].get('case_id')==case_id]
 if len(rows)!=1: raise RuntimeError('LEDGER_TEMPLATE_NOT_UNIQUE')
 return rows[0]
def ledger_path(case_id): return Path(template(case_id)['path'])
def case_dir(case_id): return RUN/'cases'/case_hash(case_id)
def grant_check(case_id):
 if OWNER.get('status')!='OPEN' or OWNER.get('max_calls')!=60 or OWNER.get('max_tokens')!=200000 or OWNER.get('max_question_posts')!=12:
  raise RuntimeError('OWNER_GRANT_MISMATCH')
 if (OWNER.get('renewal_allowed') is not False
     and OWNER.get('batch_contract',{}).get('renewal_allowed') is not False):
  raise RuntimeError('RENEWAL_NOT_ALLOWED')
 if sha(canonical(PLAN))!=OWNER.get('plan_sha256'): raise RuntimeError('PLAN_GRANT_HASH_MISMATCH')
 if not re.fullmatch('[0-9a-f]{40}',PLAN.get('source_commit','')): raise RuntimeError('SOURCE_COMMIT_MISMATCH')
 if [c.get('case_id') for c in PLAN.get('cases',[])]!=ORDER:
  raise RuntimeError('PLAN_ORDER_OR_CASE_SET_MISMATCH')
 if (RESOURCE.get('database')!='weknora_s8' or RESOURCE.get('postgres_system_id')!='7698420805889187877'
     or RESOURCE.get('redis_db')!=8 or not re.fullmatch('sha256:[0-9a-f]{64}',RESOURCE.get('candidate_image_id',''))):
  raise RuntimeError('RESOURCE_IDENTITY_FREEZE_INVALID')
 c=case_row(case_id)
 approved={x['case_id']:x['arm'] for x in OWNER.get('approved_case_arms',[])}
 modes={x['case_id']:x['mode'] for x in OWNER.get('approved_source_modes',[])}
 if approved.get(case_id)!=('A' if c.get('source_evidence_mode')=='off' else 'B') or modes.get(case_id)!=c.get('source_evidence_mode'): raise RuntimeError('CASE_NOT_AUTHORIZED')
 if c.get('source_evidence_mode') not in ('off','bounded_source_v1'): raise RuntimeError('SOURCE_MODE_NOT_AUTHORIZED')
 if case_id not in ORDER: raise RuntimeError('CASE_NOT_IN_FROZEN_ORDER')
 return c
def reported_usage(entry):
 usage=entry.get('usage')
 if not isinstance(usage,dict) or not isinstance(usage.get('total_tokens'),int): return None
 return usage['total_tokens']
def local_proof():
 proof=load(SOURCE_MANIFEST)
 if proof.get('source_commit')!=PLAN.get('source_commit'):
  raise RuntimeError('SOURCE_MANIFEST_REVISION_MISMATCH')
 return proof


def source_check(proof):
 expected=proof.get('source_code_hashes')
 if not isinstance(expected,dict) or not expected: raise RuntimeError('LOCAL_SOURCE_HASH_MANIFEST_INVALID')
 for relative,digest in expected.items():
  p=Path(relative)
  if (not isinstance(digest,str) or re.fullmatch('[a-f0-9]{64}',digest) is None
      or p.is_absolute() or '..' in p.parts):
   raise RuntimeError('LOCAL_SOURCE_HASH_PATH_INVALID')
 return {'source_commit':proof['source_commit'],'verified_source_files':len(expected),
         'manifest_sha256':sha(canonical(expected)),'helper_sha256':sha(HELPER.read_bytes()),
         'source_manifest_sha256':sha(SOURCE_MANIFEST.read_bytes())}

def remaining_check(acc):
 if (acc['question_posts']>=12 or acc['calls']>=60 or acc['held_tokens']>=200000 or acc['unreported_usage_calls']):
  raise RuntimeError('PARENT_REMAINING_AUTHORIZATION_INSUFFICIENT')
 return min(12,60-acc['calls']),min(100000,200000-acc['held_tokens'])


def accounting():
 calls=held=reported=unreported=posts=0;completed=[];first_missing=len(ORDER)
 for i,cid in enumerate(ORDER):
  lp=ledger_path(cid);cd=case_dir(cid);claim=cd/'question-post-claim.json'
  if not lp.exists():
   if first_missing==len(ORDER): first_missing=i
   continue
  if first_missing!=len(ORDER): raise RuntimeError('LEDGER_GAP_OR_OUT_OF_ORDER_CASE')
  row=load(lp);entries=row.get('entries',[])
  if row.get('status')!='CLOSED' or any(e.get('status')!='SETTLED' for e in entries): raise RuntimeError('PRIOR_LEDGER_UNSETTLED')
  if not claim.is_file() or not (cd/'http-result.json').is_file() or not (cd/'postgres-audit.json').is_file(): raise RuntimeError('PRIOR_CASE_EVIDENCE_INCOMPLETE')
  calls+=len(entries);held+=sum(e.get('held_tokens',0) for e in entries)
  known=[reported_usage(e) for e in entries]
  reported+=sum(x for x in known if x is not None);unreported+=sum(x is None for x in known)
  posts+=1;completed.append(cid)
 if calls>60 or held>200000 or posts>12 or unreported:
  raise RuntimeError('PARENT_AUTHORIZATION_LIMIT_EXCEEDED_OR_UNKNOWN')
 return {'calls':calls,'held_tokens':held,'reported_tokens':reported,'unreported_usage_calls':unreported,
         'question_posts':posts,'completed_case_ids':completed,'next_index':first_missing}


def original_api_check():
 saved=load(RUN/'original-api-identity.json')
 info=json.loads(subprocess.check_output(['docker','inspect','rag-s8-api-1'],text=True))[0]
 env={x.split('=',1)[0]:x.split('=',1)[1] for x in info.get('Config',{}).get('Env',[]) if '=' in x}
 remember_secrets(env)
 labels=info.get('Config',{}).get('Labels',{})
 if (saved.get('id')!=info.get('Id') or not info.get('State',{}).get('Running')
     or env.get('WEKNORA_PORT_CLOUD_ALLOWED','').lower()!='false'
     or labels.get('com.docker.compose.project')!='rag-s8' or labels.get('com.docker.compose.service')!='api'):
  raise RuntimeError('ORIGINAL_DAILY_API_IDENTITY_OR_CLOUD_STATE_CHANGED')
 ports=info.get('NetworkSettings',{}).get('Ports',{}).get('8000/tcp') or []
 if not any(x.get('HostIp')=='127.0.0.1' and x.get('HostPort')=='18800' for x in ports):
  raise RuntimeError('ORIGINAL_DAILY_API_PORT_CHANGED')
 return {'container_id':info['Id'],'image_id':info['Image'],'running':True,'cloud_allowed':False,'host_port':'127.0.0.1:18800'}
def subprocess_run(command,timeout=120):
 return subprocess.run(command,capture_output=True,text=True,timeout=timeout,check=False)
def remember_secrets(env):
 for key,value in env.items():
  if value and re.search(r'secret|password|passwd|token|api.?key|credential|authorization|database_url',key,re.I):
   SECRET_VALUES.add(str(value))
   if key.upper().endswith('_FILE'):
    try:
     content=Path(value).read_text(encoding='utf-8').strip()
     if content: SECRET_VALUES.add(content)
    except (OSError,UnicodeError): pass

def redact(value):
 remember_secrets(os.environ)
 try:
  env={}
  for line in ENVFILE.read_text(encoding='utf-8').splitlines():
   if '=' in line and not line.lstrip().startswith('#'):
    key,val=line.split('=',1);env[key.strip()]=val.strip().strip('\"\'')
  remember_secrets(env)
 except (OSError,UnicodeError): pass
 result=value.decode('utf-8',errors='replace') if isinstance(value,bytes) else str(value or '')
 for secret in sorted(SECRET_VALUES,key=len,reverse=True):
  result=result.replace(secret,'[REDACTED]')
 result=re.sub(r'(?i)((?:secret|password|passwd|token|api[_-]?key|authorization)[\w-]*[\"\']?\s*[:=]\s*)(?:\"[^\"]*\"|\'[^\']*\'|[^\s,;}]+)',r'\1[REDACTED]',result)
 result=re.sub(r'(?i)(https?://|postgresql(?:\+\w+)?://|redis://)([^/@\s]+)@',r'\1[REDACTED]@',result)
 return result

def save_failed_output(stdout,stderr,returncode=None,error_type=None):
 path=RUN/'control'/('failed-step-'+str(time.time_ns())+'-'+uuid.uuid4().hex+'.json')
 write_new(path,{'step':STEP,'returncode':returncode,'error_type':error_type,
                 'stdout':redact(stdout),'stderr':redact(stderr)})

def execute(command,timeout=120):
 try: p=subprocess_run(command,timeout)
 except subprocess.TimeoutExpired as exc:
  save_failed_output(exc.stdout,exc.stderr,error_type='TimeoutExpired')
  raise RuntimeError('STEP_'+STEP+'_TIMEOUT') from None
 except OSError as exc:
  save_failed_output('',str(exc),error_type=type(exc).__name__)
  raise RuntimeError('STEP_'+STEP+'_EXECUTION_FAILED') from None
 if p.returncode:
  save_failed_output(p.stdout,p.stderr,returncode=p.returncode)
  raise RuntimeError('STEP_'+STEP+'_EXIT_'+str(p.returncode))
 return p.stdout.strip()
def docker_inspect(name):
 p=execute(['docker','inspect',name],60)
 info=json.loads(p)[0]
 remember_secrets(dict(x.split('=',1) for x in info.get('Config',{}).get('Env',[]) if '=' in x))
 return info
def child_totals(data):
 known=[reported_usage(e) for e in data.get('entries',[])]
 return {'calls':len(data.get('entries',[])),
  'held_tokens':sum(e.get('held_tokens',0) for e in data.get('entries',[])),
  'reported_tokens':sum(x for x in known if x is not None),
  'unreported_usage_calls':sum(x is None for x in known),
  'unknown':sum(1 for e in data.get('entries',[]) if e.get('status')=='UNKNOWN'),
  'unsettled':sum(1 for e in data.get('entries',[]) if e.get('status')!='SETTLED')}
def candidate_env(case,lp,prefix,request_id,log_dir):
 exp=PLAN['model_binding_snapshot'];roles=exp['effective_roles']
 return {
  'WEKNORA_PORT_DATABASE_NAME':RESOURCE['database'],
  'WEKNORA_PORT_DATABASE_SYSTEM_ID':RESOURCE['postgres_system_id'],
  'WEKNORA_PORT_REDIS_URL':'redis://redis:6379/8',
  'WEKNORA_PORT_REDIS_RUN_ID':RESOURCE['redis_run_id'],
  'WEKNORA_PORT_REDIS_PREFIX':'weknora-python-s8:runtime',
  'WEKNORA_PORT_CLOUD_ALLOWED':'true','WEKNORA_PORT_ROUTER_MODE':'DYNAMIC',
  'WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID':exp['query_understand_model_id'],
  # Both experiment arms retain the normal graph/floorless/global source defaults.
  'WEKNORA_PORT_GRAPH_ENABLED':'false','WEKNORA_PORT_RERANK_TOP1_FLOORLESS':'false',
  'WEKNORA_PORT_SOURCE_EVIDENCE_MODE':'off',
  'WEKNORA_PORT_EXECUTION_ROOT':str(EXEC),'WEKNORA_PORT_EXECUTION_FILE':str(lp),
  'WEKNORA_PORT_ORIGINS':'http://127.0.0.1:18000','PYTHONDONTWRITEBYTECODE':'1',
  'S8_3_CASE_ID':case['case_id'],'S8_3_REQUEST_PREFIX':prefix,'S8_3_REQUEST_ID':request_id,
  'S8_3_RUN_ROOT':str(RUN),'S8_COMPONENT':'s8-3-r-private-acceptance','S8_LOG_DIR':str(log_dir),
  'S8_DB_HOST':'db','S8_DB_PORT':'5432','S8_DB_USER':'weknora'}
def candidate_command(case,container,lp,prefix,request_id,log_dir):
 cmd=['docker','compose','--env-file',str(ENVFILE),'-p','rag-s8','-f',str(COMPOSE),'-f',str(IMAGES),'run','--pull','never','--entrypoint','python','--no-deps','-d','--name',container,
      '--volume',f'{RUN / "tools"}:{RUN / "tools"}:ro']
 for key,value in candidate_env(case,lp,prefix,request_id,log_dir).items(): cmd.extend(['-e',key+'='+value])
 cmd.extend(['api','-B',str(HELPER),'serve','--case-id',case['case_id'],'--port','18000'])
 return cmd
def _close_container(name):
 info=docker_inspect(name)
 if info.get('State',{}).get('Running'):
  p=subprocess_run(['docker','stop','--time','15',name],45)
  if p.returncode: raise RuntimeError('CANDIDATE_STOP_FAILED_'+str(p.returncode))
 p=subprocess_run(['docker','rm',name],45)
 if p.returncode: raise RuntimeError('CANDIDATE_REMOVE_FAILED_'+str(p.returncode))
def run_one(case_id):
 global STEP
 STEP='GRANT_AND_SOURCE_PREFLIGHT'
 case=grant_check(case_id);idx=ORDER.index(case_id);cd=case_dir(case_id);lp=ledger_path(case_id)
 source_identity=source_check(local_proof())
 RUN.mkdir(parents=True,exist_ok=True);(RUN/'control').mkdir(exist_ok=True)
 with LOCK.open('a+b') as lock:
  fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
  acc=accounting()
  progress=load(PROGRESS) if PROGRESS.exists() else {
   'schema':'s8-3-parent-progress/v1','status':'OPEN','plan_sha256':OWNER['plan_sha256'],
   'max_calls':60,'max_tokens':200000,'max_question_posts':12,'next_index':0,
   'active_case':None,'completed_case_ids':[],'history':[]}
  if progress.get('status')!='OPEN' or progress.get('active_case') is not None: raise RuntimeError('PARENT_NOT_OPEN_OR_CASE_ALREADY_ACTIVE')
  if progress.get('plan_sha256')!=OWNER['plan_sha256'] or progress.get('max_calls')!=60 or progress.get('max_tokens')!=200000:
   raise RuntimeError('PARENT_PROGRESS_GRANT_MISMATCH')
  if acc['next_index']!=idx or progress.get('next_index')!=idx: raise RuntimeError('CASE_NOT_NEXT_IN_SERIAL_ORDER')
  child_calls,child_tokens=remaining_check(acc)
  if cd.exists() or lp.exists(): raise RuntimeError('CASE_ARTIFACT_EXISTS_NO_OVERWRITE')
  if not HELPER.is_file() or COMPOSE!=Path('/opt/rag-s8/current/deploy/s8/compose.yml') or not IMAGES.is_file() or not ENVFILE.is_file(): raise RuntimeError('REMOTE_RUNTIME_ARTIFACT_MISSING')
  original=original_api_check()
  active=[n for n in subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).splitlines() if n.startswith('s83-r-')]
  if active: raise RuntimeError('PRIOR_CANDIDATE_CONTAINER_REMAINS')
  case_dir_path=cd;case_dir_path.mkdir(parents=True,exist_ok=False)
  log_dir=case_dir_path/'logs';log_dir.mkdir()
  prefix='s83r_'+case_hash(case_id)[:16]+'_'
  request_id=str(uuid.uuid4())
  principal_result=subprocess_run(['docker','exec','rag-s8-api-1','cat','/run/secrets/principal.json'],30)
  if principal_result.returncode!=0:raise RuntimeError('TRUSTED_SUBJECT_FILE_UNAVAILABLE')
  source_subject=json.loads(principal_result.stdout)
  subject=source_subject.get('subject_id','')
  if (source_subject.get('tenant_id')!=332 or sha(subject)!=PLAN['principal']['subject_sha256']
      or source_subject.get('subject_sha256')!=PLAN['principal']['subject_sha256']):
   raise RuntimeError('TRUSTED_SUBJECT_IDENTITY_MISMATCH')
  item=template(case_id);ledger=copy.deepcopy(item['ledger'])
  if ledger.get('status')!='CLOSED' or ledger.get('entries')!=[] or ledger.get('max_calls')!=12 or ledger.get('max_tokens')!=100000:
   raise RuntimeError('CHILD_TEMPLATE_INVALID')
  # The one-question helper admits one frozen API POST. Production generates
  # a distinct server run UUID; the gate still enforces principal/KB/model/caps.
  ledger.update({'status':'OPEN','subject_id':subject,'allowed_request_prefixes':['*'],
                 'max_calls':child_calls,'max_tokens':child_tokens})
  # Run IDs are server generated. Admission permits one question and audit binds all
  # actual provider calls to the one persisted UUID; '*' is never a caller ID override.
  if (ledger.get('capture_evidence') is not True or len(prefix)>36
      or not lp.is_relative_to(EXEC/'s8-3-r-20261020')
      or Path(ledger.get('evidence_dir',''))!=lp.parent/'evidence'):
   raise RuntimeError('CHILD_EVIDENCE_OR_ROOT_INVALID')
  evidence=Path(ledger['evidence_dir']);evidence.mkdir(parents=True,exist_ok=False)
  lp.parent.mkdir(parents=True,exist_ok=True)
  write_new(lp,ledger)
  container='s83-r-'+case_hash(case_id)[:12]
  write_new(cd/'case-preflight.json',{'case_id':case_id,'batch':'batch1_core' if idx<6 else 'batch2_regression_boundary',
   'source_mode':case['source_evidence_mode'],'tenant_id':332,'subject_sha256':PLAN['principal']['subject_sha256'],
   'knowledge_base_id':case['knowledge_base_id'],'document_scope':[],'question_sha256':case['frozen_query_sha256_utf8'],
   'request_id':request_id,'request_prefix':prefix,'original_api':original,'provider_calls':0,
   'child_max_calls':child_calls,'child_max_tokens':child_tokens,
   'source_identity':source_identity,'cumulative_accounting_before_case':acc,
   'remote_max_calls':REMOTE_MAX_CALLS,'remote_max_tokens':REMOTE_MAX_TOKENS})
  progress.update({'active_case':case_id,'next_index':idx,'started_at':time.time(),
   'provider_calls':acc['calls'],'held_tokens':acc['held_tokens'],'reported_tokens':acc['reported_tokens'],
   'question_posts':acc['question_posts'],'completed_case_ids':acc['completed_case_ids']})
  progress['history'].append({'case_id':case_id,'state':'STARTING','at':time.time()})
  atomic(PROGRESS,progress)
  fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

 STEP='CANDIDATE_START';set_status(case_id,'ACTIVE',container=container,request_id=request_id,provider_calls=0)
 started=time.time();start_out=execute(candidate_command(case,container,lp,prefix,request_id,log_dir),120)
 info=docker_inspect(container)
 cenv={x.split('=',1)[0]:x.split('=',1)[1] for x in info.get('Config',{}).get('Env',[]) if '=' in x}
 ports=info.get('NetworkSettings',{}).get('Ports',{})
 published=any(v for vs in ports.values() if isinstance(vs,list) for v in vs)
 source_shadow=any(m.get('Destination')=='/app' or str(m.get('Destination','')).startswith('/app/')
                   for m in info.get('Mounts',[]))
 expected_env={'WEKNORA_PORT_DATABASE_NAME':RESOURCE['database'],'WEKNORA_PORT_DATABASE_SYSTEM_ID':RESOURCE['postgres_system_id'],
  'WEKNORA_PORT_REDIS_RUN_ID':RESOURCE['redis_run_id'],'WEKNORA_PORT_CLOUD_ALLOWED':'true','WEKNORA_PORT_GRAPH_ENABLED':'false',
  'WEKNORA_PORT_RERANK_TOP1_FLOORLESS':'false','WEKNORA_PORT_SOURCE_EVIDENCE_MODE':'off',
  'WEKNORA_PORT_ROUTER_MODE':'DYNAMIC','WEKNORA_PORT_EXECUTION_FILE':str(lp)}
 if (info.get('Image')!=RESOURCE.get('candidate_image_id') or not info.get('State',{}).get('Running') or published or source_shadow or any(cenv.get(k)!=v for k,v in expected_env.items())
     or info.get('HostConfig',{}).get('Memory')!=MEMORY_LIMIT_BYTES):
  raise RuntimeError('CANDIDATE_CONTAINER_CONFIG_MISMATCH')
 write_new(cd/'candidate-container.json',{'name':container,'id':info['Id'],'image_id':info['Image'],
  'running':True,'published_ports':False,'memory_limit_bytes':info.get('HostConfig',{}).get('Memory'),
  'project':info.get('Config',{}).get('Labels',{}).get('com.docker.compose.project'),
  'service':info.get('Config',{}).get('Labels',{}).get('com.docker.compose.service')})
 STEP='IMAGE_SOURCE_HASH_VERIFICATION'
 verified=json.loads(execute(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',container,'python',str(HELPER),'verify-image'],60).splitlines()[-1])
 if verified.get('status')!='IMAGE_SOURCE_HASHES_PASS':raise RuntimeError('IMAGE_SOURCE_HASH_VERIFICATION_FAILED')
 write_new(cd/'image-source-verification.json',verified)
 STEP='READY_ZERO_PROVIDER'
 ready=None
 for _ in range(60):
  p=subprocess_run(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',container,'python',str(HELPER),'ready','--port','18000'],10)
  if p.returncode==0:
   ready=json.loads(p.stdout.strip().splitlines()[-1]);break
  time.sleep(1)
 if ready is None: raise RuntimeError('CANDIDATE_READINESS_TIMEOUT_ZERO_PROVIDER')
 expected_roles=PLAN['model_binding_snapshot']['effective_roles']
 identity=load(cd/'runtime-identity.json')
 if (identity.get('models')!=expected_roles or identity.get('tenant_id')!=332 or identity.get('subject_sha256')!=PLAN['principal']['subject_sha256']
     or identity.get('database')!=RESOURCE['database'] or identity.get('postgres_system_id')!=RESOURCE['postgres_system_id']
     or identity.get('redis_run_id')!=RESOURCE['redis_run_id'] or identity.get('graph_enabled') is not False
     or identity.get('rerank_top1_floorless') is not False or identity.get('cloud_allowed') is not True
     or identity.get('model_requests_at_startup')!=0): raise RuntimeError('CANDIDATE_RUNTIME_IDENTITY_MISMATCH')
 write_new(cd/'zero-call-readiness.json',{'ready':ready,'runtime_identity':identity,'provider_calls':0,
  'elapsed_ms':round((time.time()-started)*1000,2)})
 STEP='NORMAL_API_QUESTION_POST'
 set_status(case_id,'ACTIVE',container=container,request_id=request_id,provider_calls=0,question_post='STARTING')
 submit_stdout=execute(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',container,'python',str(HELPER),'submit','--case-id',case_id],None)
 submit_result=json.loads(submit_stdout.splitlines()[-1])
 if submit_result.get('status')!='QUESTION_COMPLETE' or submit_result.get('case_id')!=case_id or submit_result.get('request_id')!=request_id:
  raise RuntimeError('NORMAL_API_SUBMIT_RESULT_MISMATCH')
 ledger_data=load(lp);totals=child_totals(ledger_data)
 if ledger_data.get('status')!='OPEN' or totals['calls']<1 or totals['unsettled'] or totals['unknown']:
  raise RuntimeError('CHILD_LEDGER_NOT_FULLY_SETTLED')
 STEP='POSTGRES_READ_ONLY_AUDIT'
 audit_out=execute(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',container,'python',str(HELPER),'audit','--case-id',case_id],120)
 audit_result=json.loads(audit_out.splitlines()[-1])
 if audit_result.get('status')!='POSTGRES_AUDIT_PASS': raise RuntimeError('POSTGRES_AUDIT_FAILED')
 STEP='LEDGER_CLOSE'
 close_out=execute(['docker','exec','-e','PYTHONDONTWRITEBYTECODE=1',container,'python',str(HELPER),'close-ledger','--case-id',case_id],60)
 close_result=json.loads(close_out.splitlines()[-1])
 if close_result.get('status')!='LEDGER_CLOSED' or close_result.get('provider_calls')!=totals['calls']:
  raise RuntimeError('CHILD_LEDGER_CLOSE_MISMATCH')
 STEP='CANDIDATE_STOP'
 _close_container(container)
 http=load(cd/'http-result.json');audit=load(cd/'postgres-audit.json');ledger_data=load(lp)
 totals=child_totals(ledger_data)
 memory=load(cd/'memory.json') if (cd/'memory.json').is_file() else {'sample_count':0,'host_mem_available_min_kib':None,'container_memory_peak_bytes':None}
 if not http.get('sse_complete') or audit.get('audit_status')!='PASS' or ledger_data.get('status')!='CLOSED':
  raise RuntimeError('FINAL_CASE_EVIDENCE_CHECK_FAILED')
 summary={'case_id':case_id,'dataset':case['dataset'],'qid':case['qid'],'source_evidence_mode':case['source_evidence_mode'],
  'arm':'A' if case['source_evidence_mode']=='off' else 'B','native_experiment_arm':'A' if case['source_evidence_mode']=='off' else 'B',
  'graph_enabled':False,'rerank_top1_floorless':False,'tenant_id':332,
  'knowledge_base_id':case['knowledge_base_id'],'request_id':request_id,'session_id':submit_result.get('session_id'),
  'provider_calls':totals['calls'],'reported_tokens':totals['reported_tokens'],
  'unreported_usage_calls':totals['unreported_usage_calls'],'held_tokens':totals['held_tokens'],
  'provider_by_capability':{},'provider_by_model':{},'http':http,'audit_status':audit.get('audit_status'),
  'audit_checks':audit.get('checks'),'sse_history_match':http.get('sse_history_content_match'),
  'latency_ms':http.get('post_to_history_total_ms'),'first_event_ms':http.get('first_sse_event_ms'),
  'first_nonempty_ms':http.get('first_nonempty_content_ms'),'memory':memory,'evidence_path':str(cd)}
 for e in ledger_data['entries']:
  tokens=reported_usage(e)
  key=e['capability'];summary['provider_by_capability'].setdefault(key,{'calls':0,'reported_tokens':0,'unreported_usage_calls':0})
  summary['provider_by_capability'][key]['calls']+=1
  if tokens is None:summary['provider_by_capability'][key]['unreported_usage_calls']+=1
  else:summary['provider_by_capability'][key]['reported_tokens']+=tokens
  key=e['provider']+'/'+e['model'];summary['provider_by_model'].setdefault(key,{'calls':0,'reported_tokens':0,'unreported_usage_calls':0})
  summary['provider_by_model'][key]['calls']+=1
  if tokens is None:summary['provider_by_model'][key]['unreported_usage_calls']+=1
  else:summary['provider_by_model'][key]['reported_tokens']+=tokens
 write_new(cd/'case-summary.json',summary)
 with LOCK.open('a+b') as lock:
  fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
  acc=accounting()
  progress=load(PROGRESS)
  if progress.get('active_case')!=case_id or progress.get('status')!='OPEN': raise RuntimeError('PARENT_PROGRESS_ACTIVE_CASE_MISMATCH')
  progress.update({'active_case':None,'next_index':idx+1,'provider_calls':acc['calls'],'held_tokens':acc['held_tokens'],
   'reported_tokens':acc['reported_tokens'],'question_posts':acc['question_posts'],
   'completed_case_ids':acc['completed_case_ids'],'updated_at':time.time()})
  progress['history'].append({'case_id':case_id,'state':'COMPLETE','at':time.time(),
   'calls':totals['calls'],'tokens':totals['reported_tokens']})
  if idx+1==len(ORDER):progress['status']='COMPLETE'
  atomic(PROGRESS,progress)
  fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
 STEP='COMPLETE';set_status(case_id,'COMPLETE',provider_calls=totals['calls'],reported_tokens=totals['reported_tokens'],
  held_tokens=totals['held_tokens'],session_id=submit_result.get('session_id'))
 return {'status':'CASE_COMPLETE','case_id':case_id,'provider_calls':totals['calls'],
  'reported_tokens':totals['reported_tokens'],'held_tokens':totals['held_tokens'],
  'latency_ms':http.get('post_to_history_total_ms'),'host_mem_available_min_kib':memory.get('host_mem_available_min_kib'),
  'container_memory_peak_bytes':memory.get('container_memory_peak_bytes'),'sse_history_match':http.get('sse_history_content_match'),
  'audit_status':audit.get('audit_status')}

def fail(case_id,exc):
 global STEP
 message=str(exc)
 safe_code=message if re.fullmatch('[A-Z][A-Z0-9_]{0,159}',message) else 'UNEXPECTED_'+type(exc).__name__.upper()
 lp=ledger_path(case_id)
 submitted=(case_dir(case_id)/'question-post-claim.json').is_file()
 container='s83-r-'+case_hash(case_id)[:12]
 fence_error=False
 try:fence_child(lp)
 except Exception:fence_error=True
 candidate_stopped=None
 # Stop the admitted process before taking final failure accounting. Even an
 # interrupted client must not leave a generator able to send further calls.
 try:
  names=subprocess.check_output(['docker','ps','-a','--format','{{.Names}}'],text=True).splitlines()
  if container in names:
   candidate_stopped=shutdown_candidate(container)
   if candidate_stopped and not submitted:
    subprocess_run(['docker','rm',container],45)
 except Exception:
  try:
   candidate_stopped=shutdown_candidate(container)
  except Exception:candidate_stopped=False
 ledger=load(lp) if lp.is_file() else None
 totals=child_totals(ledger) if ledger else {'calls':0,'held_tokens':0,'reported_tokens':0,'unknown':0,'unsettled':0}
 status='STOP_NO_RESEND' if submitted or totals['calls'] else 'STOP_ZERO_PROVIDER'
 set_status(case_id,status,step=STEP,error_type=type(exc).__name__,error_code=safe_code,
  question_post_claimed=submitted,provider_calls=totals['calls'],held_tokens=totals['held_tokens'],
  reported_tokens=totals['reported_tokens'],unknown=totals['unknown'],unsettled=totals['unsettled'])
 try:
  with LOCK.open('a+b') as lock:
   fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
   if PROGRESS.exists():
    progress=load(PROGRESS);progress['status']='STOP';progress['stop_reason']=safe_code
    progress['active_case']=case_id;progress['updated_at']=time.time()
    prior=progress.get('provider_calls',0);prior_held=progress.get('held_tokens',0)
    progress['active_case_totals']=totals
    progress['provider_calls_including_active']=prior+totals['calls']
    progress['held_tokens_including_active']=prior_held+totals['held_tokens']
    atomic(PROGRESS,progress)
   fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
 except Exception:pass
 return {'status':status,'case_id':case_id,'step':STEP,'provider_calls':totals['calls'],
  'reported_tokens':totals['reported_tokens'],'unknown':totals['unknown'],'question_post_claimed':submitted,
  'error_type':type(exc).__name__,'error_code':safe_code,'candidate_left_for_read_only_inspection':bool(submitted),
  'candidate_stopped':candidate_stopped,'child_stop_fence_failed':fence_error,
  'shutdown_status':'FAILED_SHUTDOWN' if candidate_stopped is False else 'STOPPED_OR_ABSENT'}

def shutdown_candidate(name):
 try:
  stopped=subprocess_run(['docker','stop','--time','15',name],45)
  if stopped.returncode==0 and not docker_inspect(name).get('State',{}).get('Running',True):return True
 except Exception:pass
 try:
  subprocess_run(['docker','kill',name],30)
  return not docker_inspect(name).get('State',{}).get('Running',True)
 except Exception:return False

def fence_child(path):
 """Stop only a fresh child under this execution root, preserving every entry."""
 path=Path(path)
 if not path.is_relative_to(EXEC/'s8-3-r-20261020') or not path.is_file():return
 with path.with_suffix(path.suffix+'.lock').open('a+b') as lock:
  fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
  data=load(path)
  if data.get('status')=='OPEN':
   data['status']='STOP';atomic(path,data)
  fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--case-id',required=True);args=parser.parse_args()
 case_id=args.case_id
 try: result=run_one(case_id)
 except Exception as exc:
  result=fail(case_id,exc)
  print(json.dumps(result,ensure_ascii=False));return 1
 print(json.dumps(result,ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
