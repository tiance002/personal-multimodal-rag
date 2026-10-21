"""Deterministic two-claim PG interleavings; no sleeps/model requests."""
import asyncio,hashlib,json,uuid
from pathlib import Path
import pytest
import psycopg
from psycopg.rows import dict_row
from backend.weknora_port.documents import DocumentLifecycleService,DocumentLifecycleError,KnowledgeBaseSettings
from backend.weknora_port.image_tasks import ImageTaskQueue
from backend.weknora_port.storage.connections import IsolatedConnectionFactory
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.storage.postgres import IsolationPolicy
from tests.weknora_port.test_image_tasks_pg import DB,SYSTEM,PROHIBITED,dsn,run

async def setup():
 async def acquire():return await psycopg.AsyncConnection.connect(dsn(),autocommit=True,row_factory=dict_row,connect_timeout=5)
 async def release(conn):await conn.close()
 leases=IsolatedConnectionFactory(acquire,release,IsolationPolicy(SYSTEM,PROHIBITED,database=DB))
 async with leases.lease() as guard:
  tenant=(await (await guard.connection.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',('atomic-fence-'+uuid.uuid4().hex,'isolated-mock'))).fetchone())['id']
 principal=LocalPrincipal(tenant,'atomic-owner:'+str(uuid.uuid4()))
 async def settings(_):return KnowledgeBaseSettings('emb','sum','rerank',{'chunk_size':128,'chunk_overlap':0},{'vector_enabled':False,'keyword_enabled':True,'wiki_enabled':False,'graph_enabled':False},{'enable_multimodal':False})
 async def postprocess(*args):return True
 root=Path('E:/WK/run/atomic-image-'+uuid.uuid4().hex[:12])
 docs=DocumentLifecycleService(leases,root,principal=principal,resolve_kb_settings=settings,post_processor=postprocess)
 kb=(await docs.create_knowledge_base(principal,'Atomic image claim fixture'))['id']
 doc,version,tid=(str(uuid.uuid4()) for _ in range(3))
 raw=b'The immutable source says blue widgets use protocol W-17.'
 original=root/str(tenant)/kb/doc/version/'original/source.txt';docs._write_immutable(original,raw)
 meta={'source_version':version,'sha256':hashlib.sha256(raw).hexdigest(),'media_type':'text/plain','assets':[]}
 async with leases.lease() as guard:
  await guard.connection.execute("INSERT INTO knowledges(id,tenant_id,knowledge_base_id,type,title,source,parse_status,enable_status,file_name,file_type,file_path,file_hash,metadata) VALUES(%s,%s,%s,'document','Atomic fixture','source.txt','pending','enabled','source.txt','txt',%s,%s,%s::jsonb)",(doc,tenant,kb,str(original),meta['sha256'],json.dumps(meta)))
  await guard.connection.execute('INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',(tid,tenant,principal.subject_id,kb,doc,version,json.dumps({'selection':{},'override':{'enable_multimodal':False}})))
 queue=ImageTaskQueue(leases,principal,docs,None,None,randbelow=lambda bound:0)
 task=await queue._claim();assert task and task.id==tid
 return leases,principal,docs,queue,kb,doc,task

async def reclaim(leases,queue,task):
 async with leases.lease() as guard:
  await guard.connection.execute("UPDATE port_image_tasks SET lease_until=now()-interval '1 second' WHERE id=%s AND claim_token=%s",(task.id,task.claim_token))
 other=await queue._claim();assert other and other.id==task.id and other.claim_token!=task.claim_token
 return other

async def state(leases,doc,tid):
 async with leases.lease() as guard:
  document=(await (await guard.connection.execute('SELECT to_jsonb(k) AS row FROM knowledges k WHERE id=%s',(doc,))).fetchone())['row']
  task=(await (await guard.connection.execute('SELECT to_jsonb(t) AS row FROM port_image_tasks t WHERE id=%s',(tid,))).fetchone())['row']
  chunks=await (await guard.connection.execute('SELECT to_jsonb(c) AS row FROM chunks c WHERE knowledge_id=%s ORDER BY id',(doc,))).fetchall()
  indices=await (await guard.connection.execute('SELECT to_jsonb(e) AS row FROM embeddings e WHERE knowledge_id=%s ORDER BY id',(doc,))).fetchall()
 return {'document':document,'task':task,'chunks':chunks,'indices':indices}

def test_stale_cleanup_cannot_delete_reclaimed_completed_worker_results():
 async def scenario():
  leases,owner,docs,queue,kb,doc,a=await setup()
  ready,resume=asyncio.Event(),asyncio.Event();original=docs._prepare_image_task
  async def pause(principal,task):
   if task.claim_token==a.claim_token:ready.set();await resume.wait()
   return await original(principal,task)
  docs._prepare_image_task=pause
  running=asyncio.create_task(docs.process_image_task(owner,a,queue))
  await asyncio.wait_for(ready.wait(),5)
  b=await reclaim(leases,queue,a)
  result=await docs.process_image_task(owner,b,queue);assert result['status']=='completed'
  before=await state(leases,doc,a.id)
  assert before['chunks'] and before['indices'] and all(row['row']['is_enabled'] for row in before['indices'])
  resume.set()
  with pytest.raises(DocumentLifecycleError,match='IMAGE_SOURCE_VERSION_CHANGED|IMAGE_TASK_CLAIM_LOST'):await running
  assert await state(leases,doc,a.id)==before
 run(scenario())

@pytest.mark.parametrize('interleave',['cancel','new-version','deleted','reclaimed','completed'])
def test_late_failure_mark_is_fenced_in_same_transaction(interleave):
 async def scenario():
  leases,owner,docs,queue,kb,doc,a=await setup()
  ready,resume=asyncio.Event(),asyncio.Event();original=docs._lock_current_image_document
  async def pause(guard,principal,task,**kwargs):
   if task.claim_token==a.claim_token:ready.set();await resume.wait()
   return await original(guard,principal,task,**kwargs)
  docs._lock_current_image_document=pause
  running=asyncio.create_task(docs.fail_image_task(owner,a,'LATE_SYNTHETIC_FAILURE'))
  await asyncio.wait_for(ready.wait(),5)
  if interleave=='cancel':await queue.cancel(owner,kb,doc)
  elif interleave in ('reclaimed','completed'):
   b=await reclaim(leases,queue,a)
   if interleave=='completed':assert (await docs.process_image_task(owner,b,queue))['status']=='completed'
  else:
   async with leases.lease() as guard:
    if interleave=='new-version':await guard.connection.execute("UPDATE knowledges SET metadata=jsonb_set(metadata,'{source_version}',%s::jsonb) WHERE id=%s",(json.dumps(str(uuid.uuid4())),doc))
    else:await guard.connection.execute('UPDATE knowledges SET deleted_at=now() WHERE id=%s',(doc,))
  before=await state(leases,doc,a.id);resume.set()
  assert await running is False
  assert await state(leases,doc,a.id)==before
 run(scenario())

def test_async_pending_mark_cannot_overwrite_cancelled_current_claim():
 async def scenario():
  leases,owner,docs,queue,kb,doc,a=await setup()
  ready,resume=asyncio.Event(),asyncio.Event();original=docs._lock_current_image_document
  async def pause(guard,principal,task,**kwargs):ready.set();await resume.wait();return await original(guard,principal,task,**kwargs)
  docs._lock_current_image_document=pause
  running=asyncio.create_task(docs._mark(owner,kb,doc,'pending','POSTPROCESS_PENDING',{'source_version':a.source_version},image_task=a))
  await asyncio.wait_for(ready.wait(),5);await queue.cancel(owner,kb,doc);before=await state(leases,doc,a.id);resume.set()
  with pytest.raises(DocumentLifecycleError,match='IMAGE_SOURCE_VERSION_CHANGED|IMAGE_TASK_CLAIM_LOST'):await running
  assert await state(leases,doc,a.id)==before
 run(scenario())
