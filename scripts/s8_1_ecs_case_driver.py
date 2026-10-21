#!/usr/bin/env python3
"""Future memory-v4 driver. Default dry-run is read-only and zero-send.

The completed S8.1 used memory-v3 (SHA b963b26953d556329777073fe1a6994f05e33949f7a6adec99328881b922ee7f).
Its continuous MemAvailable minimum is NOT_MEASURED; v4 has not run that evaluation.

Only --execute creates a fresh child under the frozen parent and invokes the
existing normal-API controller once. No retries, renewal, global egress change,
image build/pull, old authorization reuse, or volume deletion.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading

R = Path('/opt/rag-s8')
D = R / 's8-1-20261020'
EXECUTION_ROOT = R / 'state/executions/s8-1-20261020'
PARENT = EXECUTION_ROOT / 'parent-v2.json'
PLAN = D / 'config/evaluation-plan-v2.json'
INSPECTION = D / 'config/aligned-inspection-v2.json'
NAME = 'rag-s8-s81-api'
ORIGINAL = 'rag-s8-api-1'
IMAGE = 'sha256:8e31042a532fc80be6f5595407fcaf30da37147a88edb1d998a773bda3a2ffa8'
COMPOSE = ['docker', 'compose', '--env-file', str(R/'config/runtime.env'),
    '-f', str(R/'current/deploy/s8/compose.yml'), '-f', str(R/'config/loaded-images.json')]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path, value, *, new=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n'
    if new:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
    else:
        temporary = path.with_name(path.name + '.driver.tmp')
        with temporary.open('x', encoding='utf-8') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
    path.chmod(0o600)


def case_key(case, arm):
    return sha(canonical(['S8', case['dataset'], case['qid'], case['knowledge_base_id'],
                          case.get('document_scope', []), arm]))


def select_case(plan, identifier, arm):
    selected = [case for case in plan['cases'] if case['case_id'] == identifier
                and arm in case.get('eligible_arms', ['A'])]
    if len(selected) != 1 or arm not in ('A', 'B', 'C'):
        raise ValueError('S8_1_EXACT_APPROVED_CASE_ARM_REQUIRED')
    if selected[0].get('document_scope'):
        raise ValueError('S8_1_FULL_FROZEN_KB_SCOPE_REQUIRED')
    return selected[0]


def parent_capacity(parent, plan, case, arm):
    if (parent.get('schema') != 's8-owner-authorization/v1' or parent.get('status') != 'OPEN'
        or parent.get('max_calls') != 143 or parent.get('max_tokens') != 400000
        or parent.get('end_to_end_total') != 13 or parent.get('max_case_arms') != {'A': 9, 'B': 2, 'C': 2}
        or not parent.get('owner_approval', {}).get('answer')
        or parent.get('plan_sha256') != sha(canonical(plan))
        or {'case_id': case['case_id'], 'arm': arm} not in parent.get('approved_case_arms', [])):
        raise ValueError('S8_1_FROZEN_PARENT_REQUIRED')
    calls = tokens = 0
    paths = [Path(path) for path in parent['ledgers']]
    if len(set(map(str, paths))) != len(paths): raise ValueError('S8_1_DUPLICATE_CHILD')
    for path in paths:
        if not path.resolve().is_relative_to(EXECUTION_ROOT.resolve()):
            raise ValueError('S8_1_CHILD_OUTSIDE_PARENT_ROOT')
        ledger = read(path)
        if ledger['status'] != 'CLOSED' or any(e['status'] != 'SETTLED' for e in ledger['entries']):
            raise ValueError('S8_1_PRIOR_UNCLOSED_OR_UNKNOWN_STOP')
        if any(e.get('usage', {}).get('total_tokens') != e['held_tokens'] for e in ledger['entries']):
            raise ValueError('S8_1_PRIOR_USAGE_INCONSISTENT')
        calls += len(ledger['entries']); tokens += sum(e['held_tokens'] for e in ledger['entries'])
    if calls + 11 > 143 or tokens + 100000 > 400000:
        raise ValueError('S8_1_PARENT_FULL_CHILD_CAPACITY_NOT_AVAILABLE')
    checkpoint = read(parent['checkpoint_path']) if Path(parent['checkpoint_path']).exists() else {'entries': {}}
    entries = checkpoint['entries']
    if case_key(case, arm) in entries: raise ValueError('S8_1_NO_RESUBMISSION_USE_READONLY_RESUME')
    if len(entries) >= 13 or sum(e.get('arm') == arm for e in entries.values()) >= parent['max_case_arms'][arm]:
        raise ValueError('S8_1_CASE_ARM_LIMIT')
    if arm != 'A':
        if not any(e.get('arm') == 'A' and e.get('status') == 'COMPLETE' for e in entries.values()):
            raise ValueError('S8_1_HEALTHY_A_REQUIRED')
        if case_key(case, 'A') not in entries: raise ValueError('S8_1_SAME_A_REQUIRED')
    return {'prior_calls': calls, 'prior_tokens': tokens, 'remaining_after_full_child':
            {'calls': 143-calls-11, 'tokens': 400000-tokens-100000}}


@contextmanager
def writer_lock():
    import fcntl
    # Lock path is separate from the atomically replaced parent JSON.
    with (EXECUTION_ROOT/'case-driver.lock').open('a+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


# Executed inside the existing image. Only explicit safe DB columns are saved.
COLLECT = r'''
import asyncio,json,os,sys,urllib.request,hashlib
from pathlib import Path
from scripts.s8_ecs_runtime import runtime_environment,trusted_principal,api_token
from backend.weknora_port.development import DevelopmentSettings
import psycopg
from psycopg.rows import dict_row
ledger={'entries':[]} if sys.argv[1]=='-' else json.loads(Path(sys.argv[1]).read_text());session=sys.argv[2]
async def collect():
 settings=DevelopmentSettings.from_environment(runtime_environment())
 principal=trusted_principal(os.environ.get('S8_PRINCIPAL_FILE','/run/secrets/principal.json'))
 conn=await psycopg.AsyncConnection.connect(settings.database_url,autocommit=True,row_factory=dict_row,connect_timeout=5)
 try:
  await conn.execute('BEGIN TRANSACTION READ ONLY')
  identity=await (await conn.execute('SELECT system_identifier::text AS system_id,current_database() AS database FROM pg_control_system()')).fetchone()
  assert identity['system_id']==settings.system_id and identity['database']==settings.database and principal.tenant_id==332
  async def rows(sql,args):return await (await conn.execute(sql,args)).fetchall()
  required={'port_turns':{'run_id','session_id','client_request_id','request_configuration','status','error_code'},'sessions':{'id','summary_parameters','context_config'},'messages':{'id','request_id','rendered_content','knowledge_references','is_completed','turn_status'},'port_provider_calls':{'request_id','session_id','finished_at','usage','outcome'},'generation_runs':{'run_id','message_id','payload_sha256','knowledge_base_ids','document_ids'},'generation_attempts':{'run_id','attempt_id','ordinal','status','finish_reason','usage','objective','error_code','prompt_sha256','context_sha256','started_at','finished_at'}}
  schema=await rows("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s)",(list(required),))
  actual={name:{row['column_name'] for row in schema if row['table_name']==name} for name in required}
  missing={name:sorted(fields-actual[name]) for name,fields in required.items() if fields-actual[name]}
  if missing:print(json.dumps({'status':'SCHEMA_BLOCKED','missing_columns':missing}));raise ValueError('READBACK_SCHEMA_MISSING')
  scope=(332,principal.subject_id,session)
  turns=await rows('SELECT run_id,session_id,client_request_id,user_message_id,assistant_message_id,status,error_code,knowledge_base_scope,document_scope,request_configuration,created_at,updated_at FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s',scope)
  sessions=await rows('SELECT id,summary_model_id,rerank_model_id,summary_parameters,context_config,knowledge_base_scope,document_scope,max_rounds,enable_rewrite,embedding_top_k,vector_threshold,keyword_threshold,rerank_top_k,rerank_threshold FROM sessions WHERE tenant_id=%s AND subject_id=%s AND id=%s',scope)
  messages=await rows('SELECT id,request_id,role,content,rendered_content,knowledge_references,is_completed,turn_status,turn_error_code FROM messages WHERE tenant_id=%s AND subject_id=%s AND session_id=%s ORDER BY created_at,id',scope)
  provider=await rows('SELECT request_id,capability,provider,model,model_id,wire_sha256,outcome,usage,created_at,finished_at FROM port_provider_calls WHERE tenant_id=%s AND subject_id=%s AND (session_id=%s OR request_id=ANY(%s)) ORDER BY created_at',(332,principal.subject_id,session,[e['request_id'] for e in ledger['entries']]))
  runs=await rows('SELECT run_id,message_id,payload_sha256,knowledge_base_ids,document_ids FROM generation_runs WHERE tenant_id=%s AND subject_id=%s AND session_id=%s',scope)
  attempts=await rows('SELECT a.run_id,a.attempt_id,a.ordinal,a.role,a.provider,a.model_requested,a.model_reported,a.status,a.finish_reason,a.usage,a.objective,a.error_code,a.prompt_sha256,a.context_sha256,a.started_at,a.finished_at FROM generation_attempts a JOIN generation_runs r ON r.run_id=a.run_id AND r.tenant_id=a.tenant_id AND r.subject_id=a.subject_id WHERE r.tenant_id=%s AND r.subject_id=%s AND r.session_id=%s ORDER BY a.ordinal',scope)
  await conn.execute('ROLLBACK')
 finally:await conn.close()
 print(json.dumps({'identity':identity,'schema_checked':{name:sorted(fields) for name,fields in actual.items()},'turns':turns,'sessions':sessions,'messages':messages,'provider_calls':provider,'generation_runs':runs,'generation_attempts':attempts},ensure_ascii=False,default=str))
try:asyncio.run(collect())
except Exception as e:print(json.dumps({'error_type':type(e).__name__}));sys.exit(2)
'''

READBACK = r'''
import json,sys,urllib.request,hashlib
from pathlib import Path
from scripts.s8_ecs_runtime import api_token
session=sys.argv[1];root=Path(sys.argv[2]);out=Path(sys.argv[3]);out.mkdir(exist_ok=True)
base='http://127.0.0.1:8000';token=api_token();records=[]
def get(path,name,binary=False):
 req=urllib.request.Request(base+path,headers={'Authorization':'Bearer '+token})
 try:
  with urllib.request.urlopen(req,timeout=20) as res:body=res.read();status=res.status
  with (out/name).open('xb') as f:f.write(body)
  records.append({'path':path,'status':status,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'saved':str(out/name)})
  return json.loads(body) if not binary else None
 except Exception as e:records.append({'path':path,'error_type':type(e).__name__});return None
get('/api/v1/sessions/'+session+'/messages','history-http.json')
chunks={};docs=set()
for source in root.glob('*.stages.json'):
 value=json.loads(source.read_text())
 if value.get('session_id')!=session:continue
 for stage in value.get('stages',[]):
  for rows in stage.get('state',{}).values():
   if isinstance(rows,list):
    for row in rows:
     if isinstance(row,dict) and row.get('id') and row.get('knowledge_base_id') and row.get('knowledge_id'):
      chunks[row['id']]=row
for identifier,row in chunks.items():
 get('/api/v1/chunks/'+identifier,'chunk-'+hashlib.sha256(identifier.encode()).hexdigest()+'.json')
 docs.add((row['knowledge_base_id'],row['knowledge_id']))
for kb,doc in sorted(docs):get('/api/v1/knowledge-bases/'+kb+'/documents/'+doc+'/source','source-'+doc+'.bin',True)
with (out/'manifest.json').open('x') as f:json.dump(records,f,ensure_ascii=False,indent=2)
print(json.dumps({'saved_chunks':len(chunks),'saved_documents':len(docs),'requests':len(records),'all_http_200':all(r.get('status')==200 for r in records)}))
'''


def execute_command(command, report, name, *, timeout=60, required=True, input_text=None):
    started = time.monotonic()
    result = subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=timeout,
                            cwd=str(R/'current'))
    (report/(name+'.stdout')).write_text(result.stdout, encoding='utf-8')
    (report/(name+'.stderr')).write_text(result.stderr, encoding='utf-8')
    save(report/(name+'.command.json'), {'argv': command, 'exit_code': result.returncode,
        'elapsed_seconds': time.monotonic()-started})
    if required and result.returncode: raise RuntimeError('S8_1_COMMAND_FAILED_'+name)
    return result


def container_safe_inspect(name):
    value = json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
    env = dict(item.split('=',1) for item in value['Config'].get('Env',[]) if '=' in item)
    return {'id':value['Id'],'image':value['Image'],'state':value['State'],
        'cloud_allowed':env.get('WEKNORA_PORT_CLOUD_ALLOWED'),
        'qu_model_id':env.get('WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID')}


def matching_checkpoint(parent, key):
    path=Path(parent['checkpoint_path'])
    return read(path).get('entries',{}).get(key,{}) if path.exists() else {}


def api_ready(report, name, timeout=60, *, closed=False):
    code="""import json,urllib.request
from scripts.s8_ecs_runtime import api_token
req=urllib.request.Request('http://127.0.0.1:8000/readyz',headers={'Authorization':'Bearer '+api_token()})
with urllib.request.urlopen(req,timeout=5) as response:value=json.load(response)
assert value.get('tenant_id')==332
assert value.get('subject_sha256')=='00f2fd3dad2b741924376adab7fd29c12a93bc7a1fe4e8e1f0f97cacd418fa83'
assert value.get('model_binding_snapshot',{}).get('effective_roles',{}).get('cheap',{}).get('model')=='Qwen/Qwen3.5-4B' or value.get('models',{}).get('cheap',{}).get('model')=='Qwen/Qwen3.5-4B'
print(json.dumps(value))
"""
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=subprocess.run(['docker','exec',name,'python','-c',code],capture_output=True,text=True,timeout=12)
        if result.returncode==0:
            state=container_safe_inspect(name)
            if closed and state['cloud_allowed']!='false':raise ValueError('S8_1_RESTORED_API_NOT_CLOSED')
            save(report/('ready-'+name+'.json'),state);return
        time.sleep(1)
    raise TimeoutError('S8_1_API_READINESS_TIMEOUT')



def parse_meminfo(text):
    fields = {}
    unavailable = {}
    for name in ('MemAvailable', 'SwapFree'):
        rows = [line.split(':', 1)[1].split() for line in text.splitlines()
                if line.startswith(name + ':')]
        if len(rows) != 1 or len(rows[0]) != 2 or not rows[0][0].isdigit() or rows[0][1] != 'kB':
            fields[name + '_bytes'] = None
            unavailable[name] = 'MISSING_OR_INVALID'
        else:
            fields[name + '_bytes'] = int(rows[0][0]) * 1024
    return {'status': 'UNAVAILABLE' if unavailable else 'MEASURED', **fields,
            'unavailable_fields': unavailable}


def summarize_memory(samples):
    def extrema(section, field, lowest=True):
        valid = [row for row in samples if type(row.get(section, {}).get(field)) is int]
        if not valid: return {'bytes': None, 'at_utc': None, 'valid_samples': 0, 'status': 'UNAVAILABLE'}
        row = (min if lowest else max)(valid, key=lambda value: value[section][field])
        return {'bytes': row[section][field], 'at_utc': row['at_utc'],
                'valid_samples': len(valid), 'status': 'MEASURED'}
    available = extrema('host', 'MemAvailable_bytes')
    return {'status': 'MEASURED' if available['valid_samples'] else 'UNAVAILABLE',
        'sample_count': len(samples), 'host_MemAvailable_min': available,
        'host_SwapFree_min': extrema('host', 'SwapFree_bytes'),
        'container_memory_current_max': extrema('container', 'memory_current_bytes', False),
        'container_memory_peak_max': extrema('container', 'memory_peak_bytes', False),
        'container_memory_limit_max': extrema('container', 'memory_limit_bytes', False)}


class MemorySampler:
    """Passive bounded-run diagnostics; no Docker polling or admission changes."""
    def __init__(self, report, *, proc_root=Path('/proc'), cgroup_root=Path('/sys/fs/cgroup'), interval=1.0):
        self.report, self.proc_root, self.cgroup_root = Path(report), Path(proc_root), Path(cgroup_root)
        self.interval = interval
        self.samples, self.errors, self.summary = [], [], None
        self.binding = None
        self.binding_error = 'CONTROLLED_PID_NOT_BOUND'
        self.stop_event = threading.Event()
        self.thread = None

    def _start_token(self, pid):
        value = (self.proc_root / str(pid) / 'stat').read_text()
        # /proc/<pid>/stat comm is parenthesized and may contain spaces.
        return value[value.rfind(')') + 2:].split()[19]

    def bind_pid(self, pid):
        try:
            if type(pid) is not int or pid <= 0: raise ValueError('INVALID_PID')
            token = self._start_token(pid)
            rows = (self.proc_root / str(pid) / 'cgroup').read_text().splitlines()
            selected = None
            for row in rows:
                hierarchy, controllers, relative = row.split(':', 2)
                if hierarchy == '0' and controllers == '':
                    selected = (self.cgroup_root, relative, 'v2'); break
                if 'memory' in controllers.split(','):
                    selected = (self.cgroup_root / 'memory', relative, 'v1')
            if selected is None: raise ValueError('MEMORY_CGROUP_NOT_FOUND')
            mount, relative, version = selected
            if not relative.startswith('/') or '..' in Path(relative).parts:
                raise ValueError('UNSAFE_CGROUP_PATH')
            mount = mount.resolve()
            directory = (mount / relative.lstrip('/')).resolve()
            if not directory.is_relative_to(mount): raise ValueError('CGROUP_PATH_ESCAPES_MOUNT')
            self.binding = {'pid': pid, 'start_token': token, 'directory': directory, 'version': version}
        except Exception as exc:
            self.binding = None
            self.binding_error = 'UNAVAILABLE_' + type(exc).__name__

    def _container(self):
        binding = self.binding
        if binding is None: return {'status': 'UNAVAILABLE', 'reason': self.binding_error,
            'memory_current_bytes': None, 'memory_peak_bytes': None, 'memory_limit_bytes': None}
        try:
            if self._start_token(binding['pid']) != binding['start_token']:
                raise ValueError('CONTROLLED_PID_IDENTITY_CHANGED')
        except Exception as exc:
            return {'status': 'UNAVAILABLE', 'reason': type(exc).__name__,
                'memory_current_bytes': None, 'memory_peak_bytes': None, 'memory_limit_bytes': None}
        names = (('memory.current', 'memory.peak', 'memory.max') if binding['version'] == 'v2'
                 else ('memory.usage_in_bytes', 'memory.max_usage_in_bytes', 'memory.limit_in_bytes'))
        result = {'status': 'MEASURED', 'cgroup_version': binding['version'], 'pid': binding['pid'],
                  'unavailable_fields': {}}
        for field, name in zip(('memory_current_bytes', 'memory_peak_bytes', 'memory_limit_bytes'), names):
            try:
                value = (binding['directory'] / name).read_text().strip()
                if not value.isdigit(): raise ValueError('UNBOUNDED' if value == 'max' else 'INVALID')
                result[field] = int(value)
            except Exception as exc:
                result[field] = None
                result['unavailable_fields'][field] = 'UNBOUNDED' if str(exc) == 'UNBOUNDED' else type(exc).__name__
                result['status'] = 'UNAVAILABLE'
        return result

    def sample_once(self):
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        try: host = parse_meminfo((self.proc_root / 'meminfo').read_text())
        except Exception as exc:
            host = {'status': 'UNKNOWN', 'error_type': type(exc).__name__,
                    'MemAvailable_bytes': None, 'SwapFree_bytes': None}
        try: container = self._container()
        except Exception as exc:
            container = {'status': 'UNKNOWN', 'error_type': type(exc).__name__,
                         'memory_current_bytes': None, 'memory_peak_bytes': None, 'memory_limit_bytes': None}
        self.samples.append({'at_utc': stamp, 'host': host, 'container': container})

    def start(self):
        try:
            self.sample_once()  # Initial sample precedes the controlled start.
            def loop():
                while not self.stop_event.wait(self.interval):
                    try: self.sample_once()
                    except Exception as exc: self.errors.append('SAMPLE_UNKNOWN_' + type(exc).__name__)
            self.thread = threading.Thread(target=loop, name='s8-1-memory-sampler', daemon=True)
            self.thread.start()
        except Exception as exc: self.errors.append('START_UNKNOWN_' + type(exc).__name__)

    def stop(self):
        # Diagnostics never prevent process cleanup or ledger settlement.
        try:
            self.stop_event.set()
            if self.thread is not None:
                self.thread.join(timeout=2)
                if self.thread.is_alive(): self.errors.append('STOP_UNKNOWN_THREAD_NOT_JOINED')
            self.sample_once()
            samples = list(self.samples)
            self.summary = {**summarize_memory(samples), 'interval_seconds': self.interval,
                'window': 'before controlled start through controlled stop; before original API restore',
                'interpretation': 'minimum of recorded samples; sub-second continuous minimum is not measured',
                'started_at_utc': samples[0]['at_utc'] if samples else None,
                'ended_at_utc': samples[-1]['at_utc'] if samples else None,
                'diagnostic_errors': list(self.errors), 'real_docker_polling': False}
            save(self.report / 'memory-samples.json', {'schema': 's8-1-memory-samples/v1', 'samples': samples}, new=True)
            save(self.report / 'memory-summary.json', self.summary, new=True)
        except Exception as exc:
            self.errors.append('SAVE_UNKNOWN_' + type(exc).__name__)
            self.summary = {'status': 'UNKNOWN', 'diagnostic_errors': list(self.errors)}
        return self.summary


def run(args):
    global NAME
    os.umask(0o077)
    if args.collect_session:
        original=container_safe_inspect(ORIGINAL)
        if original['image']!=IMAGE or original['cloud_allowed']!='false' or not original['state']['Running']:
            raise ValueError('S8_1_ZERO_CLOUD_ORIGINAL_REQUIRED')
        report=D/'readback-preflight'/(args.collect_session+'-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        report.mkdir(parents=True,exist_ok=False,mode=0o700)
        result=execute_command(['docker','exec',ORIGINAL,'python','-c',COLLECT,'-',args.collect_session],report,'zero-question-pg-readback',timeout=30)
        value=json.loads(result.stdout)
        assert value['sessions'] and not value['turns'] and not value['provider_calls']
        print(json.dumps({'status':'ZERO_QUESTION_SCHEMA_AND_READBACK_PASS','report':str(report),'provider_requests':0,'session_id':args.collect_session}));return 0
    plan=read(PLAN);parent=read(PARENT);inspection=read(INSPECTION)
    local=D/'config/local-effective-config-parameters-v2.json'
    if not local.is_file():
        candidates=list((D/'config').glob('*local*alignment*.json'))
        if len(candidates)!=1:raise ValueError('S8_1_EXACT_LOCAL_ALIGNMENT_FILE_REQUIRED')
        local=candidates[0]
    case=select_case(plan,args.case_id,args.arm);key=case_key(case,args.arm)
    NAME='rag-s8-s81-'+args.arm.lower()+'-'+key[:12]
    capacity=parent_capacity(parent,plan,case,args.arm)
    child_dir=EXECUTION_ROOT/'cases'/(args.arm+'-'+key)
    child=child_dir/'execution.json';report=D/'cases'/(args.arm+'-'+key)
    if child_dir.exists() or report.exists():raise ValueError('S8_1_CASE_PATH_EXISTS_NO_RESEND')
    tool=D/'tools/s8_1_ecs_acceptance.py'
    if sha(tool.read_bytes())!=plan['acceptance_script_sha256']:raise ValueError('S8_1_FROZEN_TOOL_CHANGED')
    serve=['python','-m','scripts.s8_1_ecs_acceptance','serve','--plan',str(PLAN),
        '--inspection',str(INSPECTION),'--local-alignment',str(local),'--authorization',str(PARENT),
        '--execution',str(child),'--case-id',args.case_id,'--arm',args.arm,'--port','8000',
        '--output',str(report/'startup.json')]
    start=COMPOSE+['run','-d','--no-deps','--service-ports','--pull','never','--name',NAME,
        '-v',str(D)+':'+str(D),'-v',str(tool)+':/app/scripts/s8_1_ecs_acceptance.py:ro',
        '--entrypoint','python','api',*serve[1:]]
    question=['docker','exec',NAME,'python','-m','scripts.s8_1_ecs_acceptance','run',
        '--plan',str(PLAN),'--identity',str(report/'startup.json'),'--checkpoint',parent['checkpoint_path'],
        '--authorization',str(PARENT),'--execution',str(child),'--case-id',args.case_id,'--arm',args.arm,
        '--output',str(report/'api-cases')]
    if not args.execute:
        print(json.dumps({'status':'DRY_RUN_ZERO_SEND_NO_AUTHORIZATION_CREATED','case_id':args.case_id,
            'arm':args.arm,'capacity':capacity,'new_child':str(child),'report':str(report),
            'start_argv':start,'sole_question_argv':question,'max_api_window_seconds':900,
            'tool_sha256':sha(tool.read_bytes())},ensure_ascii=False,indent=2));return 0
    with writer_lock():
        parent=read(PARENT);capacity=parent_capacity(parent,plan,case,args.arm)
        original=container_safe_inspect(ORIGINAL)
        if original['image']!=IMAGE or original['cloud_allowed']!='false' or not original['state']['Running']:
            raise ValueError('S8_1_ORIGINAL_API_IDENTITY_CLOSED_RUNNING_REQUIRED')
        existing=subprocess.run(['docker','container','inspect',NAME],capture_output=True)
        if existing.returncode==0:raise ValueError('S8_1_DIAGNOSTIC_CONTAINER_ALREADY_EXISTS')
        principal=read(R/'secrets/principal.json')
        if principal['tenant_id']!=332 or sha(principal['subject_id'])!=plan['principal']['subject_sha256']:
            raise ValueError('S8_1_TRUSTED_PRINCIPAL_CHANGED')
        # Preserve only the registered four text-QA roles; no VLM authorization.
        models=parent['allowed_models']
        if len(models)!=4:raise ValueError('S8_1_FOUR_FORMAL_TEXT_MODELS_REQUIRED')
        child_dir.mkdir(parents=True,mode=0o700,exist_ok=False)
        report.mkdir(parents=True,mode=0o700,exist_ok=False)
        ledger={'status':'OPEN','tenant_id':332,'subject_id':principal['subject_id'],
            'max_calls':11,'max_tokens':100000,'allowed_kbs':[case['knowledge_base_id']],
            'allowed_models':models,'allowed_request_prefixes':['*'],'capture_evidence':True,
            'evidence_dir':str(child_dir/'evidence'),'entries':[]}
        save(child,ledger,new=True)
        parent['ledgers'].append(str(child));save(PARENT,parent)
        save(report/'driver-start.json',{'case_id':args.case_id,'arm':args.arm,'capacity':capacity,
            'parent_sha256_after_register':sha(canonical(parent)),'original_api':original,
            'tool_sha256':sha(tool.read_bytes()),'driver_sha256':sha(Path(__file__).read_bytes())},new=True)
        child_closed=False;controlled_started=False;original_stopped=False;error=None
        api_deadline=time.monotonic()+900
        memory_sampler=MemorySampler(report)
        memory_sampler.start()
        try:
            original_stopped=True  # restore even if stop command itself times out
            execute_command(COMPOSE+['stop','api'],report,'stop-original-api',timeout=45)
            controlled_started=True  # cleanup even when docker run times out
            execute_command(start,report,'start-controlled-api',timeout=60)
            # Same temporary container/image. Reserve headroom for its API,
            # normal-HTTP client and healthcheck processes before any question.
            execute_command(['docker','update','--memory','1g','--memory-swap','1g',NAME],
                report,'controlled-memory-headroom',timeout=30)
            memory_readback=execute_command(['docker','inspect','--format','{{json .HostConfig}}',NAME],
                report,'controlled-memory-readback',timeout=30)
            host_config=json.loads(memory_readback.stdout)
            limits={field:host_config.get(field) for field in ('Memory','MemorySwap')}
            save(report/'controlled-memory-confirmed.json',limits,new=True)
            if limits!={'Memory':1073741824,'MemorySwap':1073741824}:
                raise ValueError('S8_1_CONTROLLED_MEMORY_LIMIT_NOT_CONFIRMED')
            controlled_identity=container_safe_inspect(NAME)
            if controlled_identity['image']!=IMAGE:raise ValueError('S8_1_IMAGE_CHANGED')
            memory_sampler.bind_pid(controlled_identity['state']['Pid'])
            api_ready(report,NAME,60)
            execute_command(question,report,'normal-api-question-once',timeout=min(660,max(1,int(api_deadline-time.monotonic()-120))))
            record=matching_checkpoint(parent,key)
            if not record.get('session_id'):raise ValueError('S8_1_SESSION_UNCERTAIN_NO_RESEND')
            session=record['session_id'];last=None;stable=0;poll=0
            while time.monotonic()<api_deadline-40:
                current=read(child)
                poll+=1
                result=execute_command(['docker','exec',NAME,'python','-c',COLLECT,str(child),session],
                    report,'pg-poll-'+str(poll).zfill(3),timeout=25)
                pg=json.loads(result.stdout)
                if any(e['status'] not in ('SETTLED','INFLIGHT') for e in current['entries']):
                    raise ValueError('S8_1_NEW_UNKNOWN_STOP')
                terminal=bool(pg['turns']) and all(t['status'] in ('completed','error') for t in pg['turns'])
                settled=all(e['status']=='SETTLED' for e in current['entries'])
                signature=sha(canonical({'ledger':current,'turns':pg['turns'],'provider':pg['provider_calls']}))
                stable=stable+1 if settled and terminal and signature==last else 0;last=signature
                if stable>=3:break
                time.sleep(2)
            else:raise TimeoutError('S8_1_SETTLEMENT_OR_PG_NOT_STABLE_NO_RESEND')
            save(report/'pg-final-before-stop.json',pg,new=True)
            execute_command(['docker','exec',NAME,'python','-c',READBACK,session,
                str(child_dir/'evidence'),str(report/'readback')],report,'source-http-readback',timeout=90)
            save(report/'ledger-before-stop.json',read(child),new=True)
        except BaseException as exc:
            error=type(exc).__name__+':'+(str(exc) if isinstance(exc,(ValueError,RuntimeError,TimeoutError)) else '')
        finally:
            # Each diagnostic step is isolated: a collection error never skips
            # the attempt to stop the controlled process and restore the API.
            cleanup_errors=[]
            def cleanup_step(label, callback):
                try:return callback()
                except BaseException as exc:
                    cleanup_errors.append(label+":"+type(exc).__name__)
                    return None
            controlled_state=None
            if controlled_started:
                cleanup_step('logs',lambda:execute_command(['docker','logs',NAME],report,'controlled-container-logs',required=False))
                controlled_state=cleanup_step('inspect',lambda:container_safe_inspect(NAME))
                if controlled_state:cleanup_step('save-inspect',lambda:save(report/'controlled-container-inspect.json',controlled_state,new=True))
                cleanup_step('stop-controlled',lambda:execute_command(['docker','stop','-t','45',NAME],report,'stop-controlled-api',timeout=60,required=False))
                controlled_state=cleanup_step('inspect-stopped',lambda:container_safe_inspect(NAME))
                if controlled_state:cleanup_step('save-stopped',lambda:save(report/'controlled-container-after-stop.json',controlled_state,new=True))
            memory_sampler.stop()
            # Restore is attempted independently even when diagnostics failed.
            if original_stopped:
                cleanup_step('restore-original',lambda:execute_command(['docker','start',ORIGINAL],report,'restore-original-api',timeout=30))
                cleanup_step('restored-readiness',lambda:api_ready(report,ORIGINAL,60,closed=True))
            current=read(child)
            process_stopped=(not controlled_started or bool(controlled_state and not controlled_state['state']['Running']))
            record=matching_checkpoint(parent,key)
            pg_after=None
            if record.get('session_id') and process_stopped:
                collected=cleanup_step('final-pg-after-stop',lambda:execute_command(
                    ['docker','exec',ORIGINAL,'python','-c',COLLECT,str(child),record['session_id']],
                    report,'pg-after-process-stop',timeout=30))
                if collected is not None and collected.returncode==0:
                    pg_after=json.loads(collected.stdout)
                    cleanup_step('save-final-pg',lambda:save(report/'pg-final-after-stop.json',pg_after,new=True))
            terminal=(pg_after is not None and bool(pg_after['turns']) and all(t['status'] in ('completed','error') for t in pg_after['turns']))
            all_settled=all(e['status']=='SETTLED' for e in current['entries'])
            if process_stopped and current['status']=='OPEN' and all_settled and (terminal or not current['entries']):
                current['status']='CLOSED';child_closed=True
            elif current['status']=='OPEN':
                current['status']='STOP'
                current['stop_reason']='PROCESS_OR_SETTLEMENT_NOT_PROVEN_NO_RELEASE'
            # Preserve every original entry/status/usage/held reservation.
            save(child,current)
            cleanup_step('ledger-final',lambda:save(report/'ledger-final.json',current,new=True))
            error=error or ('S8_1_CLEANUP_REQUIRES_REVIEW' if cleanup_errors else None)
            final={'status':'FINISHED_REQUIRES_EVIDENCE_REVIEW' if not error else 'STOPPED_NO_RESEND',
                'case_id':args.case_id,'arm':args.arm,'error':error,'cleanup_errors':cleanup_errors,
                'child_closed':child_closed,'child':str(child),'report':str(report),'checkpoint_entry':record,
                'parent_status':read(PARENT)['status'],'no_automatic_resend':True,
                'background_tasks_observed_directly':False,
                'background_settlement_evidence':'PG terminal plus stable settled ledger; controlled process stopped; final PG readback before closing child',
                'diagnostic_container_retained':True,'diagnostic_container_name':NAME,
                'memory_monitor':memory_sampler.summary,'driver_variant':'memory-v4-future-passive-sampling'}
            save(report/'driver-result.json',final,new=True)
            print(json.dumps(final,ensure_ascii=False,default=str))
        return 0 if not error else 2


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-id')
    parser.add_argument('--collect-session',help='read-only preflight on an existing zero-question alignment session; no child/API start')
    parser.add_argument('--arm',choices=('A','B','C'))
    parser.add_argument('--execute',action='store_true',help='explicit real execution; absent means read-only dry-run')
    args=parser.parse_args(argv)
    if not args.collect_session and not (args.case_id and args.arm):parser.error('--case-id/--arm required unless --collect-session')
    if args.collect_session and args.execute:parser.error('--collect-session is read-only and cannot combine --execute')
    try:return run(args)
    except Exception as exc:
        print(json.dumps({'status':'PRE_SEND_BLOCKED','error_type':type(exc).__name__,
            'code':str(exc) if isinstance(exc,ValueError) else 'SEE_PRIVATE_DIAGNOSTICS_NO_RESEND'}));return 2


if __name__=='__main__':raise SystemExit(main())