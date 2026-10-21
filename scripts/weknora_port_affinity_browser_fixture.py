"""Fresh owned browser fixture for document-affinity controls; real providers impossible."""
import asyncio,json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import uvicorn
from backend.weknora_port.http_api import create_app
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE,stream_response,EXPECTED

async def run():
    work=ROOT/'var/weknora-port/affinity-browser';work.mkdir(parents=True,exist_ok=True)
    stop=work/('stop-'+uuid.uuid4().hex)
    async with isolated_mock_application(answer_stream_factory=lambda model:stream_response(model,'Fixture answer <ref id="c1"/>','stop')) as (runtime,wire,root):
        kb=(await runtime.create_knowledge_base(runtime.principal,'Affinity browser fixture'))['id']
        await runtime.update_knowledge_base_consent(runtime.principal,kb,True)
        doc=await runtime.upload_document(runtime.principal,kb,'Familiar fixture.txt','text/plain',SOURCE.encode())
        assert doc['status']=='completed'
        async with runtime.leases.lease() as guard:
            await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':True,'write_mode':'explicit_only','retrieval_conditioning':True}),runtime.principal.tenant_id))
            for title,hits,base in [('Familiar fixture',8,kb),('Hidden one hit',1,kb),('Missing KB fixture',3,'')]:
                await guard.connection.execute('INSERT INTO memory_doc_affinity(id,tenant_id,subject_id,knowledge_id,knowledge_base_id,title,hits,last_used_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now())',(str(uuid.uuid4()),runtime.principal.tenant_id,runtime.principal.subject_id,doc['id'] if hits==8 else str(uuid.uuid4()),base,title,hits))
        await runtime.update_memory_settings(runtime.principal,True)
        app=create_app(runtime,resolve_principal=lambda request:runtime.principal,allowed_origins=('http://127.0.0.1:56436',))
        requests=[];wrong_identity=False
        @app.middleware('http')
        async def record(request,call_next):
            response=await call_next(request);requests.append({'method':request.method,'path':request.url.path,'status':response.status_code})
            return response
        @app.get('/_fixture/metrics')
        async def metrics():return {'requests':requests,'real_provider_calls':0,'wire_calls':len(wire),'system_identifier':EXPECTED}
        @app.post('/_fixture/deny-identity')
        async def deny():
            nonlocal wrong_identity
            wrong_identity=True
            return {'fixture_only':True}
        original=runtime.runtime_identity
        async def identity(principal):
            result=await original(principal)
            if wrong_identity:result={**result,'implementation':'fixture-wrong'}
            return result
        runtime.runtime_identity=identity
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=56435,log_level='warning'))
        task=asyncio.create_task(server.serve())
        try:
            for _ in range(200):
                if server.started:break
                if task.done():await task
                await asyncio.sleep(.01)
            assert server.started
            ready={'kind':'NEW_SYNTHETIC_AFFINITY_BROWSER_FIXTURE','stop_file':str(stop),'kb':kb,'document_id':doc['id'],'source_text':SOURCE,'system_identifier':EXPECTED,'root':str(root),'tenant':runtime.principal.tenant_id,'subject':runtime.principal.subject_id}
            (work/'ready.json').write_text(json.dumps(ready,indent=2),encoding='utf-8');print(json.dumps(ready),flush=True)
            while not stop.exists():await asyncio.sleep(.25)
        finally:
            server.should_exit=True;await task
            (work/'stopped.json').write_text(json.dumps({'stopped':True,'real_provider_calls':0,'requests':requests}),encoding='utf-8')
actual_pg_run(run())