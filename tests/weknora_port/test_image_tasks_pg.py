"""New logical DB, new owner and Redis prefix; every model response is closed mock."""
import asyncio
import io
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from PIL import Image,ImageDraw,ImageFont
from redis.asyncio import Redis

from backend.weknora_port.crypto import SecretCipher
from backend.weknora_port.documents import DocumentLifecycleService,KnowledgeBaseSettings
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.http_api import create_app
from backend.weknora_port.image_tasks import ImageTask,ImageTaskQueue
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.transport import AsyncHTTPTransport,resolve_endpoint
from backend.weknora_port.runtime import ApplicationRuntime
from backend.weknora_port.storage.connections import IsolatedConnectionFactory
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.storage.postgres import IsolationPolicy
from backend.weknora_port.streaming.manager import RedisListStreamStore
from tests.weknora_port.test_runtime_pg import FixtureCosts,FixturePermissions


DB='wk_image_20261014'
SYSTEM='7696088403504697382'
PROHIBITED=frozenset({'7691227493754040358','7689510505384681511','7696038373595934758'})


def dsn():
    value=os.environ.get('WEKNORA_IMAGE_TEST_DSN','')
    parsed=urlsplit(value)
    if (parsed.hostname,parsed.port,parsed.path)!=('127.0.0.1',56423,'/'+DB):
        pytest.skip('explicit new image-only database DSN required')
    return value


def run(coroutine):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


def fixture_png():
    image=Image.new('RGB',(900,200),'white')
    ImageDraw.Draw(image).text((35,55),'OCR TEST 1234',fill='black',
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',72))
    output=io.BytesIO();image.save(output,format='PNG')
    return output.getvalue()


def test_real_isolated_http_image_task_local_ocr_explicit_caption(monkeypatch):
    path=Path('E:/RAG quention/var/tessdata')
    assert (path/'eng.traineddata').is_file() and (path/'chi_sim.traineddata').is_file()
    monkeypatch.setenv('RAG_TESSDATA_DIR',str(path))
    async def scenario():
        import psycopg
        from psycopg.rows import dict_row
        async def acquire():
            return await psycopg.AsyncConnection.connect(dsn(),autocommit=True,row_factory=dict_row,connect_timeout=5)
        async def release(conn):await conn.close()
        leases=IsolatedConnectionFactory(acquire,release,IsolationPolicy(SYSTEM,PROHIBITED,database=DB))
        async with leases.lease() as guard:
            schema=await (await guard.connection.execute(
                "SELECT version FROM port_schema_migrations WHERE version='010_image_tasks'" )).fetchall()
            assert len(schema)==1
            tenant=(await (await guard.connection.execute(
                'INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
                ('image-fixture-'+str(uuid.uuid4()),'isolated-mock'))).fetchall())[0]['id']
        principal=LocalPrincipal(tenant,'image-owner:'+str(uuid.uuid4()))
        redis=Redis.from_url('redis://127.0.0.1:56424/0',decode_responses=True)
        assert await redis.ping()
        prefix='wk-image:20261014:'+str(uuid.uuid4())
        stream=RedisListStreamStore(redis,prefix=prefix)
        calls=[]
        def handler(request):
            assert request.url.host=='fixture.invalid'
            body=json.loads(request.content)
            parts=body['messages'][0]['content']
            assert any(item['type']=='image_url' for item in parts)
            prompt=next(item['text'] for item in parts if item['type']=='text')
            kind='ocr' if 'OCR assistant' in prompt else 'caption'
            calls.append({'model':body['model'],'parts':len(parts),'kind':kind})
            return httpx.Response(200,json={'model':body['model'],'choices':[
                {'message':{'content':'OCR TEST 1234' if kind=='ocr' else
                  'A white image with printed OCR TEST 1234.'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}})
        def factory(config,key,gate):
            transport=AsyncHTTPTransport(
                allowed_endpoints=[resolve_endpoint(config,'/chat/completions')],
                gate=gate,credential=lambda _:key,http_transport=httpx.MockTransport(handler))
            return ProviderModel(config,transport)
        async def settings(_):
            return KnowledgeBaseSettings('fixture-embedding','fixture-summary','fixture-rerank',
                {'chunk_size':128,'chunk_overlap':0},
                {'vector_enabled':False,'keyword_enabled':True,'wiki_enabled':False,'graph_enabled':False},
                {'enable_multimodal':False})
        async def postprocess(*_):return True
        storage=Path('E:/WK/run/image-'+uuid.uuid4().hex[:12])
        documents=DocumentLifecycleService(leases,storage,principal=principal,
            resolve_kb_settings=settings,post_processor=postprocess)
        runtime=ApplicationRuntime(leases,principal,stream,documents,model_factory=factory,
            cipher=SecretCipher(b'i'*32),policy=RouterPolicy(mode='OFF'),
            permissions=FixturePermissions(leases,principal),costs=FixtureCosts())
        app=create_app(runtime,resolve_principal=lambda request:principal,
                       allowed_origins=('http://image.test',))
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://image.test',
                                         headers={'Origin':'http://image.test'}) as client:
                created=await client.post('/api/v1/knowledge-bases',json={'name':'image synthetic'})
                assert created.status_code==200,created.text
                kb=created.json()['data']['id']
                consent=await client.patch(f'/api/v1/knowledge-bases/{kb}/external-processing',
                                           json={'cloud_allowed':True})
                assert consent.status_code==200,consent.text
                registered=await client.post('/api/v1/settings/vision-models',json={
                    'provider':'siliconflow','model':'Qwen/Qwen3-VL-32B-Instruct',
                    'base_url':'https://fixture.invalid/v1','api_key':'SYNTHETIC-NOT-A-KEY'})
                assert registered.status_code==200,registered.text
                caption_id=registered.json()['data']['id']
                configured=await client.patch(f'/api/v1/knowledge-bases/{kb}/image-processing',json={
                    'enable_multimodal':True,'ocr_adapter':'tesseract',
                    'caption_mode':'explicit','caption_model_id':caption_id})
                assert configured.status_code==200,configured.text
                uploaded=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',
                    files={'file':('synthetic.png',fixture_png(),'image/png')})
                assert uploaded.status_code==200,uploaded.text
                doc=uploaded.json()['data'];assert doc['status']=='pending',doc
                for _ in range(100):
                    async with leases.lease() as guard:
                        task=await (await guard.connection.execute(
                            'SELECT state,attempt,outcomes,error_code FROM port_image_tasks WHERE knowledge_id=%s',
                            (doc['id'],))).fetchone()
                    if task['state'] in ('completed','failed','unknown','orphaned'):break
                    await asyncio.sleep(.1)
                if task['state']!='completed':
                    if runtime.image_tasks._pump.done():
                        print('IMAGE_PUMP_ERROR',repr(runtime.image_tasks._pump.exception()))
                assert task['state']=='completed',task
                assert task['attempt']==1 and len(calls)==1 and calls[0]['model']=='Qwen/Qwen3-VL-32B-Instruct'
                source=await client.get(f'/api/v1/knowledge-bases/{kb}/documents/{doc["id"]}/source')
                assert source.status_code==200 and source.content==fixture_png()
                async with leases.lease() as guard:
                    chunks=await (await guard.connection.execute(
                        'SELECT c.id,c.chunk_type,c.content,c.parent_chunk_id,c.image_info,e.is_enabled '
                        'FROM chunks c LEFT JOIN embeddings e ON e.chunk_id=c.id '
                        'WHERE c.tenant_id=%s AND c.knowledge_base_id=%s AND c.knowledge_id=%s',
                        (tenant,kb,doc['id']))).fetchall()
                kinds={row['chunk_type'] for row in chunks}
                assert {'text','image_ocr','image_caption'}<=kinds
                assert all(row['parent_chunk_id'] and row['is_enabled'] for row in chunks
                           if row['chunk_type'].startswith('image_'))
                assert any('OCR TEST 1234' in row['content'] for row in chunks if row['chunk_type']=='image_ocr')
                caption_chunk=next(row for row in chunks if row['chunk_type']=='image_caption')
                readback=await client.get(f'/api/v1/chunks/{caption_chunk["id"]}')
                assert readback.status_code==200 and 'white image' in readback.text
                async with leases.lease() as guard:
                    await guard.connection.execute(
                        "UPDATE knowledges SET metadata=jsonb_set(metadata,'{source_version}',to_jsonb(%s::text)) "
                        "WHERE id=%s AND tenant_id=%s",(str(uuid.uuid4()),doc['id'],tenant))
                stale_readback=await client.get(f'/api/v1/chunks/{caption_chunk["id"]}')
                assert stale_readback.status_code==404
                async with leases.lease() as guard:
                    await guard.connection.execute(
                        "UPDATE knowledges SET metadata=jsonb_set(metadata,'{source_version}',to_jsonb(%s::text)) "
                        "WHERE id=%s AND tenant_id=%s",
                        ((await (await guard.connection.execute(
                            'SELECT source_version FROM port_image_tasks WHERE knowledge_id=%s',
                            (doc['id'],))).fetchone())['source_version'],doc['id'],tenant))
                assert task['outcomes']['0']['ocr_text']['status']=='completed'
                assert task['outcomes']['0']['caption']['status']=='completed'
                status=await client.get(f'/api/v1/knowledge-bases/{kb}/documents/{doc["id"]}/image-task')
                assert status.status_code==200
                assert status.json()['data']['selection']['caption']['model_id']==caption_id
                assert calls[0]['kind']=='caption'
                # Mixed native text plus a scanned page uses the same durable
                # upload entry and keeps its page and source metadata.
                import fitz
                pdf=fitz.open()
                pdf.new_page(width=900,height=200).insert_text((35,55),'Native source page.')
                scanned=pdf.new_page(width=900,height=200)
                scanned.insert_image(scanned.rect,stream=fixture_png())
                pdf_data=pdf.tobytes();pdf.close()
                pdf_upload=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',
                    files={'file':('mixed.pdf',pdf_data,'application/pdf')})
                assert pdf_upload.status_code==200 and pdf_upload.json()['data']['status']=='pending'
                pdf_id=pdf_upload.json()['data']['id']
                for _ in range(100):
                    async with leases.lease() as guard:
                        pdf_task=await (await guard.connection.execute(
                            'SELECT state,outcomes FROM port_image_tasks WHERE knowledge_id=%s',
                            (pdf_id,))).fetchone()
                    if pdf_task['state'] in ('completed','failed','unknown'):break
                    await asyncio.sleep(.1)
                assert pdf_task['state']=='completed',pdf_task
                async with leases.lease() as guard:
                    pdf_chunks=await (await guard.connection.execute(
                        'SELECT chunk_type,image_info FROM chunks WHERE knowledge_id=%s',
                        (pdf_id,))).fetchall()
                pdf_images=[row for row in pdf_chunks if row['chunk_type'].startswith('image_')]
                assert {row['chunk_type'] for row in pdf_images}=={'image_ocr','image_caption'}
                assert all(json.loads(row['image_info'])[0]['page_number']==2 for row in pdf_images)
                assert all(json.loads(row['image_info'])[0]['source_type']=='scanned_pdf' for row in pdf_images)
                assert any(call['kind']=='caption' for call in calls[1:])
                # The same cataloged VLM may be selected for two distinct
                # OCR/Caption requests; no combined call or OCR-as-caption.
                changed=await client.patch(f'/api/v1/knowledge-bases/{kb}/image-processing',json={
                    'enable_multimodal':True,'ocr_adapter':'vlm','ocr_model_id':caption_id,
                    'caption_mode':'follow_ocr'})
                assert changed.status_code==200,changed.text
                inherited=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',
                    files={'file':('inherit.png',fixture_png(),'image/png')})
                assert inherited.status_code==200 and inherited.json()['data']['status']=='pending'
                inherited_id=inherited.json()['data']['id']
                for _ in range(100):
                    async with leases.lease() as guard:
                        second=await (await guard.connection.execute(
                            'SELECT state,selected_config,outcomes FROM port_image_tasks WHERE knowledge_id=%s',
                            (inherited_id,))).fetchone()
                    if second['state'] in ('completed','failed','unknown'):break
                    await asyncio.sleep(.1)
                assert second['state']=='completed',second
                assert second['selected_config']['selection']['caption']['source']=='ocr_inherited'
                assert [call['kind'] for call in calls[-2:]]==['ocr','caption']
                alternative=await client.post('/api/v1/settings/vision-models',json={
                    'provider':'siliconflow','model':'Qwen/Qwen3.5-35B-A3B',
                    'base_url':'https://fixture.invalid/v1','api_key':'SYNTHETIC-NOT-A-KEY'})
                assert alternative.status_code==200,alternative.text
                alternative_id=alternative.json()['data']['id']
                override=json.dumps({'caption_mode':'explicit','caption_model_id':alternative_id})
                third=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',data={
                    'process_overrides':override},files={'file':('override.png',fixture_png(),'image/png')})
                assert third.status_code==200 and third.json()['data']['status']=='pending'
                third_id=third.json()['data']['id']
                for _ in range(100):
                    async with leases.lease() as guard:
                        selected=await (await guard.connection.execute(
                            'SELECT state,selected_config FROM port_image_tasks WHERE knowledge_id=%s',
                            (third_id,))).fetchone()
                    if selected['state'] in ('completed','failed','unknown'):break
                    await asyncio.sleep(.1)
                assert selected['state']=='completed',selected
                assert selected['selected_config']['selection']['ocr']['model_id']==caption_id
                assert selected['selected_config']['selection']['caption']['model_id']==alternative_id
                assert selected['selected_config']['selection']['caption']['source']=='upload'
                assert [(call['kind'],call['model']) for call in calls[-2:]]==[
                    ('ocr','Qwen/Qwen3-VL-32B-Instruct'),('caption','Qwen/Qwen3.5-35B-A3B')]
                async with leases.lease() as guard:
                    await guard.connection.execute(
                        "UPDATE models SET status='disabled' WHERE id=%s AND tenant_id=%s",
                        (alternative_id,tenant))
                before=len(calls)
                denied=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',data={
                    'process_overrides':override},files={'file':('disabled.png',fixture_png(),'image/png')})
                assert denied.status_code==200
                assert denied.json()['data']['status']=='failed'
                assert len(calls)==before  # invalid explicit Caption never inherits OCR
        finally:
            await runtime.stop_background_workers()
            await redis.aclose()
    run(scenario())


def test_real_isolated_image_queue_claim_retry_restart_unknown_and_duplicate():
    async def scenario():
        import psycopg
        from psycopg.rows import dict_row
        async def acquire():
            return await psycopg.AsyncConnection.connect(dsn(),autocommit=True,row_factory=dict_row,connect_timeout=5)
        async def release(conn):await conn.close()
        leases=IsolatedConnectionFactory(acquire,release,IsolationPolicy(SYSTEM,PROHIBITED,database=DB))
        async with leases.lease() as guard:
            tenant=(await (await guard.connection.execute(
                'INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
                ('image-queue-'+str(uuid.uuid4()),'isolated-mock'))).fetchall())[0]['id']
        owner=LocalPrincipal(tenant,'queue-owner:'+str(uuid.uuid4()))
        class FixtureDocuments:
            async def _assert_current_image_task(self,*args):pass
        queue=ImageTaskQueue(leases,owner,FixtureDocuments(),None,None,randbelow=lambda bound:0)
        task_id=str(uuid.uuid4())
        kb,doc,version=str(uuid.uuid4()),str(uuid.uuid4()),str(uuid.uuid4())
        config={'selection':{'ocr':{'adapter':'tesseract','model_id':'','source':'knowledge_base',
                 'capability':'LOCAL_ENGINE'},'caption':None,'caption_error':'IMAGE_CAPTION_VISION_MODEL_REQUIRED',
                 'config_version':1},'override':{'enable_multimodal':True}}
        async with leases.lease() as guard:
            await guard.connection.execute(
                'INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                (task_id,tenant,owner.subject_id,kb,doc,version,json.dumps(config)))
        first=await queue._claim()
        assert first.id==task_id and first.attempt==1
        assert await queue._claim() is None  # duplicate delivery cannot own it
        await queue._update(first,state='retry',error='SYNTHETIC_RETRY',delay=0)
        second=await queue._claim()
        assert second.id==task_id and second.attempt==2 and second.claim_token!=first.claim_token
        with pytest.raises(Exception,match='IMAGE_TASK_CLAIM_LOST'):
            await queue._update(first,state='completed')
        # A worker restart with no recorded send can reclaim an expired lease.
        async with leases.lease() as guard:
            await guard.connection.execute(
                "UPDATE port_image_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(task_id,))
        third=await queue._claim()
        assert third.attempt==3
        await queue.before(third,0,'caption')
        async with leases.lease() as guard:
            await guard.connection.execute(
                "UPDATE port_image_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",(task_id,))
        assert await queue._claim() is None  # uncertain external result is not resent
        async with leases.lease() as guard:
            row=await (await guard.connection.execute(
                'SELECT state,attempt,error_code,outcomes FROM port_image_tasks WHERE id=%s',(task_id,))).fetchone()
        assert row['state']=='unknown' and row['attempt']==3
        assert row['error_code']=='IMAGE_REQUEST_OUTCOME_UNKNOWN'
        assert row['outcomes']['0']['caption']['status']=='sending'
    run(scenario())


def test_real_isolated_image_retry_bound_and_orphan_terminal():
    async def scenario():
        import psycopg
        from psycopg.rows import dict_row
        from backend.weknora_port.documents import DocumentLifecycleError
        async def acquire():
            return await psycopg.AsyncConnection.connect(dsn(),autocommit=True,row_factory=dict_row,connect_timeout=5)
        async def release(conn):await conn.close()
        leases=IsolatedConnectionFactory(acquire,release,IsolationPolicy(SYSTEM,PROHIBITED,database=DB))
        async with leases.lease() as guard:
            tenant=(await (await guard.connection.execute(
                'INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
                ('image-bound-'+str(uuid.uuid4()),'isolated-mock'))).fetchall())[0]['id']
        owner=LocalPrincipal(tenant,'bound-owner:'+str(uuid.uuid4()))
        class Orphan:
            async def process_image_task(self,*args):raise DocumentLifecycleError('IMAGE_TASK_ORPHANED')
        queue=ImageTaskQueue(leases,owner,Orphan(),None,None,randbelow=lambda bound:0)
        ids=[str(uuid.uuid4()),str(uuid.uuid4())]
        config={'selection':{'ocr':{'adapter':'tesseract'},'caption':None,
                 'caption_error':'IMAGE_CAPTION_VISION_MODEL_REQUIRED','config_version':1},
                'override':{'enable_multimodal':True}}
        async with leases.lease() as guard:
            for task_id in ids:
                await guard.connection.execute(
                    'INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (task_id,tenant,owner.subject_id,str(uuid.uuid4()),str(uuid.uuid4()),str(uuid.uuid4()),json.dumps(config)))
        first=await queue._claim()
        for expected in range(1,5):
            assert first.attempt==expected
            await queue._update(first,state='retry',error='SYNTHETIC_RETRY',delay=0)
            first=await queue._claim()
            if expected<3:assert first is not None
        assert first is None
        async with leases.lease() as guard:
            rows=await (await guard.connection.execute(
                'SELECT state,attempt,error_code FROM port_image_tasks WHERE id=%s',(ids[0],))).fetchall()
        # The oldest task is exhausted after initial delivery plus 3 retries.
        assert rows[0]['state']=='failed' and rows[0]['attempt']==4
        assert rows[0]['error_code']=='IMAGE_RETRY_EXHAUSTED'
        assert await queue.run_due()
        async with leases.lease() as guard:
            orphan=await (await guard.connection.execute(
                'SELECT state,attempt FROM port_image_tasks WHERE id=%s',(ids[1],))).fetchone()
        assert orphan['state']=='orphaned' and orphan['attempt']==1
    run(scenario())


def test_real_isolated_image_final_commit_version_delete_and_duplicate_fence():
    async def scenario():
        import psycopg
        from psycopg.rows import dict_row
        from backend.weknora_port.documents import DocumentLifecycleError
        async def acquire():
            return await psycopg.AsyncConnection.connect(dsn(),autocommit=True,row_factory=dict_row,connect_timeout=5)
        async def release(conn):await conn.close()
        leases=IsolatedConnectionFactory(acquire,release,IsolationPolicy(SYSTEM,PROHIBITED,database=DB))
        async with leases.lease() as guard:
            tenant=(await (await guard.connection.execute(
                'INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
                ('image-fence-'+str(uuid.uuid4()),'isolated-mock'))).fetchall())[0]['id']
        owner=LocalPrincipal(tenant,'fence-owner:'+str(uuid.uuid4()))
        async def settings(_):
            return KnowledgeBaseSettings('emb','sum','rerank',{},
                {'vector_enabled':False,'keyword_enabled':False,'wiki_enabled':False,'graph_enabled':False},{})
        docs=DocumentLifecycleService(leases,Path('E:/WK/run/image-fence-'+uuid.uuid4().hex[:12]),
            principal=owner,resolve_kb_settings=settings)
        kb=str(uuid.uuid4())
        async with leases.lease() as guard:
            await guard.connection.execute(
                'INSERT INTO knowledge_bases(id,name,tenant_id,embedding_model_id,summary_model_id,rerank_model_id) '
                'VALUES(%s,%s,%s,%s,%s,%s)',(kb,'image-fence',tenant,'emb','sum','rerank'))
        async def seed(*,deleted=False,version_change=False):
            doc,version,task_id,token=(str(uuid.uuid4()) for _ in range(4))
            metadata={'source_version':str(uuid.uuid4()) if version_change else version}
            async with leases.lease() as guard:
                await guard.connection.execute(
                    "INSERT INTO knowledges(id,tenant_id,knowledge_base_id,type,title,source,parse_status,"
                    "enable_status,metadata,deleted_at) VALUES(%s,%s,%s,'document','fixture','fixture',"
                    "'processing','enabled',%s::jsonb,CASE WHEN %s THEN now() ELSE NULL END)",
                    (doc,tenant,kb,json.dumps(metadata),deleted))
                await guard.connection.execute(
                    "INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,"
                    "source_version,selected_config,state,attempt,claim_token) VALUES(%s,%s,%s,%s,%s,%s,"
                    "%s::jsonb,'processing',1,%s)",
                    (task_id,tenant,owner.subject_id,kb,doc,version,
                     json.dumps({'selection':{},'override':{}}),token))
            return ImageTask(task_id,tenant,owner.subject_id,kb,doc,version,{}, {},1,token,{})
        good=await seed()
        await docs._finalize(owner,kb,good.knowledge_id,good.source_version,
                             {'source_version':good.source_version},enabled=False,image_task=good)
        with pytest.raises(DocumentLifecycleError,match='IMAGE_SOURCE_VERSION_CHANGED'):
            await docs._finalize(owner,kb,good.knowledge_id,good.source_version,
                                 {'source_version':good.source_version},enabled=False,image_task=good)
        for flags in ({'deleted':True},{'version_change':True}):
            stale=await seed(**flags)
            with pytest.raises(DocumentLifecycleError,match='IMAGE_SOURCE_VERSION_CHANGED'):
                await docs._finalize(owner,kb,stale.knowledge_id,stale.source_version,
                                     {'source_version':stale.source_version},enabled=False,image_task=stale)
        cancelled=await seed()
        queue=ImageTaskQueue(leases,owner,docs,None,None)
        result=await queue.cancel(owner,kb,cancelled.knowledge_id)
        assert result['state']=='cancelled'
        with pytest.raises(DocumentLifecycleError,match='IMAGE_SOURCE_VERSION_CHANGED'):
            await docs._finalize(owner,kb,cancelled.knowledge_id,cancelled.source_version,
                                 {'source_version':cancelled.source_version},enabled=False,image_task=cancelled)
        async with leases.lease() as guard:
            row=await (await guard.connection.execute(
                'SELECT state FROM port_image_tasks WHERE id=%s',(good.id,))).fetchone()
        assert row['state']=='completed'
        outsider=LocalPrincipal(tenant,'different-owner')
        with pytest.raises(PermissionError):
            await docs._finalize(outsider,kb,good.knowledge_id,good.source_version,
                                 {'source_version':good.source_version},enabled=False,image_task=good)
        other_tenant=LocalPrincipal(tenant+100000,'different-tenant')
        with pytest.raises(PermissionError):
            await docs._finalize(other_tenant,kb,good.knowledge_id,good.source_version,
                                 {'source_version':good.source_version},enabled=False,image_task=good)
        with pytest.raises(DocumentLifecycleError,match='IMAGE_SOURCE_VERSION_CHANGED'):
            await docs._finalize(owner,str(uuid.uuid4()),good.knowledge_id,good.source_version,
                                 {'source_version':good.source_version},enabled=False,image_task=good)
    run(scenario())
