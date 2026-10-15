"""Executable new assembly, loopback API and actual worker queues; mock model wire."""
import asyncio,io,json,socket,time,uuid
from contextlib import asynccontextmanager
from pathlib import Path
import httpx,uvicorn
from PIL import Image
from backend.weknora_port.development import DevelopmentSettings,assemble
from backend.weknora_port.entrypoint import create_app
from backend.weknora_port.worker import run_once
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import approved_dsn,actual_pg_run,EXPECTED
from tests.weknora_port.test_runtime_pg import SOURCE
from tests.weknora_port.test_qa_followup_fallback_pg import REDIS_ID


def test_executable_api_and_worker_image_ingest_memory_and_replay():
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15) as (fixture,wire,unused):
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            origin=f'http://127.0.0.1:{port}'
            tag=uuid.uuid4().hex[:10];root=Path('E:/WK/run/p8-entry-'+tag);key=root.with_suffix('.key');key.write_bytes(b's'*32)
            cfg=DevelopmentSettings(approved_dsn(),EXPECTED,'weknora_python_port_20261013','redis://127.0.0.1:56424/15',REDIS_ID,
                'weknora-python-entry:'+tag,root,key,Path('D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken'),origins=(origin,))
            box={}
            @asynccontextmanager
            async def factory(settings):
                async with assemble(settings,model_factory=fixture.model_factory,permissions=fixture.permissions,costs=fixture.costs,principal=fixture.principal) as runtime:
                    box['runtime']=runtime;yield runtime
            api=create_app(settings=cfg,runtime_factory=factory)
            server=uvicorn.Server(uvicorn.Config(api,host='127.0.0.1',port=port,log_level='error',access_log=False))
            task=asyncio.create_task(server.serve())
            try:
                for _ in range(300):
                    if server.started:break
                    if task.done():await task
                    await asyncio.sleep(.01)
                assert server.started
                runtime=box['runtime']
                async with httpx.AsyncClient(base_url=origin,headers={'Origin':origin},timeout=30) as client:
                    assert (await client.get('/healthz')).json()['service']=='weknora-python-port'
                    assert (await client.get('/api/v1/runtime')).status_code==200
                    assert (await client.get('/readyz')).status_code==200
                    kb=(await client.post('/api/v1/knowledge-bases',json={'name':'new entry fixture'})).json()['data']['id']
                    await client.patch(f'/api/v1/knowledge-bases/{kb}/external-processing',json={'cloud_allowed':True})
                    upload=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',files={'file':('source.txt',SOURCE,'text/plain')})
                    assert upload.status_code==200 and upload.json()['data']['status']=='completed',upload.text
                    async with runtime.leases.lease() as guard:
                        roles=dict((await runtime._snapshot(guard)).model_ids)
                        await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',
                            (json.dumps({'enabled':True,'write_mode':'auto','extract_delay_seconds':5,'extract_min_interval_seconds':1,
                                'embedding_model_id':roles['embedding'],'extract_model_id':roles['cheap']}),runtime.principal.tenant_id))
                    sid=(await client.post('/api/v1/sessions',json={'title':'entry','knowledge_base_scope':[kb]})).json()['data']['id']
                    request={'content':'I use blue widgets. extract source','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb]}
                    answer=await client.post(f'/api/v1/sessions/{sid}/messages',json=request)
                    await runtime.drain_background()
                    messages=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data'];assistant=next(m for m in messages if m['role']=='assistant')
                    assert assistant['is_completed'] and assistant['knowledge_references'],answer.text
                    ref=assistant['knowledge_references'][0]
                    assert (await client.get('/api/v1/chunks/'+ref['id'])).status_code==200
                    before=len(wire);await client.post(f'/api/v1/sessions/{sid}/messages',json=request);assert len(wire)==before
                    assert await runtime.store.client.hlen(runtime.memory_enqueuer.keys[2])==1
                    assert runtime.image_tasks._pump is None and runtime.memory_enqueuer._pump is None
                    configured=await client.patch(f'/api/v1/knowledge-bases/{kb}/image-processing',json={'enable_multimodal':True,
                        'ocr_adapter':'vlm','ocr_model_id':roles['vlm'],'caption_mode':'explicit','caption_model_id':roles['vlm'],'custom_instructions':'BUSINESS_ONLY_CAPTION'})
                    assert configured.status_code==200,configured.text
                    image=io.BytesIO();Image.new('RGB',(40,40),'white').save(image,format='PNG')
                    reply=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',files={'file':('entry.png',image.getvalue(),'image/png')})
                    assert reply.status_code==200 and reply.json()['data']['status']=='pending',reply.text
                    did=reply.json()['data']['id']
                    runtime.memory_enqueuer.clock=lambda:time.time()+10
                    result=await run_once(runtime)
                    assert result=={'image_ingest':True,'memory_extract':True},result
                    rows=await runtime._rows('SELECT state FROM port_image_tasks WHERE tenant_id=%s AND knowledge_id=%s',(runtime.principal.tenant_id,did))
                    assert rows[0]['state']=='completed',rows
                    memories=await runtime._rows("SELECT id FROM memory_items WHERE tenant_id=%s AND subject_id=%s AND status='active'",(runtime.principal.tenant_id,runtime.principal.subject_id))
                    assert len(memories)==1,memories
                    before=len(wire);assert await run_once(runtime)=={'image_ingest':False,'memory_extract':False};assert len(wire)==before
                    output=Path('docs/weknora-port/evidence/P8_DEVELOPMENT_ENTRY_20261015/http.'+tag+'.json')
                    output.write_text(json.dumps({'root':str(root),'session':sid,'assistant':assistant,'worker_once':result,
                        'image_task':rows,'memory_items':memories,'replay_sends':0,'duplicate_worker_sends':0,'model_wire_count':len(wire),
                        'real_provider_calls':0,'API_consumer_pumps':False},indent=2,default=str)+'\n',encoding='utf-8')
            finally:server.should_exit=True;await task
    actual_pg_run(scenario())
