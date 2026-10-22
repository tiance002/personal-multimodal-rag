"""Narrow existing-SSH current source readback; no application imports or writes."""
from pathlib import Path
import ast
import hashlib
import json
import subprocess
import time


ROOT=Path(__file__).resolve().parent
HELPER=Path('E:/RAG quention/var/reports/s8-ecs-finish-20261020/ssh_readonly.py')
FIXTURE=ROOT.parents[4]/'tests/fixtures/s8_6_4/retrieval_replays.json'

INNER=r'''
import hashlib,hmac,json,os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
def require(value,code):
 if not value:raise ValueError(code)
def main():
 principal=json.loads(Path(os.environ.get('S8_PRINCIPAL_FILE','/run/secrets/principal.json')).read_text())
 fingerprint=hashlib.sha256(principal.get('subject_id','').encode()).hexdigest()
 require(principal.get('tenant_id')==332 and hmac.compare_digest(fingerprint,EXPECTED['owner_sha256'])
  and hmac.compare_digest(fingerprint,principal.get('subject_sha256','')),'CURRENT_OWNER_TENANT_MISMATCH')
 name=os.environ.get('WEKNORA_PORT_DATABASE_NAME','')
 require(name=='weknora_s8' and os.environ.get('WEKNORA_PORT_DATABASE_SYSTEM_ID')=='7698420805889187877','ENV_DATABASE_IDENTITY_MISMATCH')
 password=Path(os.environ.get('S8_DB_PASSWORD_FILE','/run/secrets/database_password')).read_text().strip()
 with psycopg.connect(dbname=name,user=os.environ.get('S8_DB_USER','weknora'),password=password,
  host=os.environ.get('S8_DB_HOST','db'),port=int(os.environ.get('S8_DB_PORT','5432')),autocommit=True,row_factory=dict_row,
  options='-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000') as conn:
  conn.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
  try:
   identity=conn.execute('SELECT current_database() AS database,(pg_control_system()).system_identifier::text AS system_identifier').fetchone()
   require(identity['database']=='weknora_s8' and identity['system_identifier']=='7698420805889187877','PHYSICAL_DATABASE_IDENTITY_MISMATCH')
   readonly=conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
   require(readonly=='on','READ_ONLY_TRANSACTION_REQUIRED')
   kb=conn.execute('SELECT id,tenant_id,name,deleted_at FROM knowledge_bases WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL',(EXPECTED['kb'],332)).fetchall()
   require(len(kb)==1,'CURRENT_KB_SCOPE_UNAVAILABLE')
   docs=conn.execute("SELECT id,tenant_id,knowledge_base_id,title,file_name,parse_status,enable_status,deleted_at,metadata->>'source_version' AS source_version,metadata->>'sha256' AS source_sha256,metadata->>'index_enabled' AS index_enabled,metadata->>'chunk_count' AS chunk_count,metadata->>'indexed_count' AS indexed_count FROM knowledges WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL",(EXPECTED['doc'],332,EXPECTED['kb'])).fetchall()
   require(len(docs)==1,'CURRENT_DOCUMENT_SCOPE_UNAVAILABLE')
   doc=docs[0]
   require(doc['source_version']==EXPECTED['version'],'CURRENT_DOCUMENT_VERSION_MISMATCH')
   require(doc['enable_status']=='enabled' and doc['parse_status']=='completed' and doc['index_enabled']=='true','CURRENT_DOCUMENT_NOT_READY')
   rows=conn.execute('SELECT id,tenant_id,knowledge_base_id,knowledge_id,chunk_index,content,source_content,start_at,end_at,pre_chunk_id,next_chunk_id,chunk_type,parent_chunk_id,is_enabled,deleted_at,metadata,content_revision,index_status FROM chunks WHERE id=ANY(%s) AND tenant_id=%s AND knowledge_base_id=%s AND knowledge_id=%s AND deleted_at IS NULL ORDER BY chunk_index,id',(EXPECTED['ids'],332,EXPECTED['kb'],EXPECTED['doc'])).fetchall()
   require({r['id'] for r in rows}==set(EXPECTED['ids']),'CURRENT_CHUNKS_SCOPE_UNAVAILABLE')
   for row in rows:
    metadata=row.get('metadata') or {}
    if isinstance(metadata,str):metadata=json.loads(metadata)
    require(metadata.get('source_version')==EXPECTED['version'],'CURRENT_CHUNK_VERSION_MISMATCH')
    require(row['is_enabled'] is True and row['index_status']=='ready','CURRENT_CHUNK_NOT_READY')
   anchor=next(r for r in rows if r['id']==EXPECTED['anchor'])
   require(hashlib.sha256(anchor['content'].encode()).hexdigest()==EXPECTED['anchor_content_sha256'],'CURRENT_ANCHOR_CONTENT_MISMATCH')
   result={'status':'CURRENT_SOURCE_READBACK_VERIFIED','database_identity':identity,'transaction_read_only':readonly,
    'owner_sha256':fingerprint,'tenant_id':332,'knowledge_base':kb[0],'document':doc,'chunks':rows,
    'requested_chunk_ids':EXPECTED['ids'],'sql_select_count':6,'transaction_end':'ROLLBACK',
    'application_imports':0,'provider_calls':0,'database_writes':0}
  finally:conn.execute('ROLLBACK')
 return result
try:print(json.dumps(main(),ensure_ascii=False,default=str,sort_keys=True))
except Exception as exc:
 code=str(exc) if isinstance(exc,ValueError) and str(exc).replace('_','').isalnum() else 'READBACK_FAILED'
 print(json.dumps({'status':'BLOCKED','error_code':code,'error_type':type(exc).__name__,'provider_calls':0,'database_writes':0}));raise SystemExit(1)
'''

OUTER=r'''
import json,subprocess
def require(value,code):
 if not value:raise ValueError(code)
def command(args,data=None):
 p=subprocess.run(args,input=data,capture_output=True,timeout=55)
 require(p.returncode==0,'READ_ONLY_DOCKER_COMMAND_FAILED')
 return p.stdout
try:
 ids=command(['docker','ps','-q','--filter','label=com.docker.compose.project=rag-s8','--filter','label=com.docker.compose.service=api']).decode().split()
 require(len(ids)==1,'RUNNING_API_IDENTITY_NOT_UNIQUE')
 container=json.loads(command(['docker','inspect',ids[0]]))[0]
 labels=container['Config']['Labels']
 require(container['State']['Running'] and labels.get('com.docker.compose.project')=='rag-s8' and labels.get('com.docker.compose.service')=='api','CURRENT_CONTAINER_IDENTITY_MISMATCH')
 files=labels.get('com.docker.compose.project.config_files','').split(',')
 require(files==['/opt/rag-s8/current/deploy/s8/compose.yml','/opt/rag-s8/config/loaded-images.json'],'CURRENT_COMPOSE_FILE_CHAIN_MISMATCH')
 response=json.loads(command(['docker','exec','-i',container['Id'],'python','-B','-'],INNER.encode()))
 require(response.get('status')=='CURRENT_SOURCE_READBACK_VERIFIED','CURRENT_SOURCE_READBACK_NOT_VERIFIED')
 response['container_identity']={'id':container['Id'],'image_id':container['Image'],'running':container['State']['Running'],
  'compose_project':labels['com.docker.compose.project'],'compose_service':labels['com.docker.compose.service'],'compose_files':files}
 print(json.dumps(response,ensure_ascii=False,sort_keys=True))
except Exception as exc:
 code=str(exc) if isinstance(exc,ValueError) and str(exc).replace('_','').isalnum() else 'REMOTE_READBACK_FAILED'
 print(json.dumps({'status':'BLOCKED','error_code':code,'error_type':type(exc).__name__,'provider_calls':0,'database_writes':0}));raise SystemExit(1)
'''


def main():
 raw=FIXTURE.read_bytes();fixture=json.loads(raw)
 case=next(c for c in fixture['cases'] if c['case_key']=='mml_0434')
 anchor=next(r for r in case['source_search_results'] if r['id']=='10ff171b-a054-4cbc-9ab4-4c52b8c3022f')
 assert case['scope']['knowledge_base_ids']==['7f1d720d-9561-446f-a8cc-6a562c339d23']
 assert case['scope']['saved_pg_turn_scope']['knowledge_base_scope']==case['scope']['knowledge_base_ids']
 assert anchor['knowledge_base_id']==case['scope']['knowledge_base_ids'][0]
 assert anchor['knowledge_id']=='0b9850f8-6a5e-4855-91e4-7ac745eecaea'
 assert anchor['metadata']['source_version']==anchor['chunk_metadata']['source_version']=='10bce0ac-2fa8-46c1-8fc4-067d575d7370'
 expected={'kb':anchor['knowledge_base_id'],'doc':anchor['knowledge_id'],'version':anchor['metadata']['source_version'],
  'anchor':anchor['id'],'ids':[anchor['pre_chunk_id'],anchor['id'],anchor['next_chunk_id']],
  'owner_sha256':hashlib.sha256(case['scope']['owner_id'].encode()).hexdigest(),
  'anchor_content_sha256':hashlib.sha256(anchor['content'].encode()).hexdigest()}
 tree=ast.parse(HELPER.read_text(encoding='utf-8-sig'))
 ssh=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
  and any(isinstance(t,ast.Name) and t.id=='SSH' for t in node.targets))
 assert 'StrictHostKeyChecking=yes' in ssh and 'BatchMode=yes' in ssh
 assert Path(ssh[ssh.index('-i')+1]).is_file() and (Path.home()/'.ssh/known_hosts').is_file()
 inner='EXPECTED='+repr(expected)+'\n'+INNER
 remote='INNER='+repr(inner)+'\n'+OUTER
 (ROOT/'remote-readonly.payload.py').write_text(remote,encoding='utf-8')
 start=time.time()
 completed=subprocess.run(ssh+['python3 -'],input=remote.encode(),capture_output=True,timeout=90)
 # SSH diagnostics may contain the private host. Store only a sanitized status
 # and the original stderr digest; exact source-row stdout has no host/secret.
 (ROOT/'current-source.stdout.json').write_bytes(completed.stdout)
 stderr_status={'bytes':len(completed.stderr),'sha256':hashlib.sha256(completed.stderr).hexdigest(),
  'status':'EMPTY' if not completed.stderr else 'PRESENT_PRIVATE_DIAGNOSTIC_NOT_COPIED'}
 receipt={'schema':'s864-readonly-neighbor-command/v1','command':['existing-pinned-ssh','-T','BatchMode=yes','StrictHostKeyChecking=yes','IdentitiesOnly=yes','<existing-key-file>','<verified-private-target>','python3 -'],
  'exit_code':completed.returncode,'elapsed_seconds':round(time.time()-start,3),'ssh_helper_sha256':hashlib.sha256(HELPER.read_bytes()).hexdigest(),
  'fixture_sha256':hashlib.sha256(raw).hexdigest(),'remote_payload_sha256':hashlib.sha256(remote.encode()).hexdigest(),
  'stdout_sha256':hashlib.sha256(completed.stdout).hexdigest(),'stderr':stderr_status,
  'provider_calls':0,'database_writes':0,'parent_scope_correction':'Initial KB parameter typo corrected to independently agreeing frozen case/anchor/saved-PG scope; blocked.json preserved.'}
 (ROOT/'readback.command.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'status':'READBACK_COMMAND_COMPLETED','exit_code':completed.returncode,'stdout_bytes':len(completed.stdout),'stderr_status':stderr_status['status'],'provider_calls':0,'database_writes':0}))
 raise SystemExit(completed.returncode)


if __name__=='__main__':main()
