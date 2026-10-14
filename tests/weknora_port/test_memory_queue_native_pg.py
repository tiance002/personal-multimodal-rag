"""Formal memory schedule/extract/model/send-gate with actual PG/Redis; no provider network."""
import json,time,uuid
from pathlib import Path
from types import SimpleNamespace
import httpx
from backend.weknora_port.http_api import create_app
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.conversation.types import MemoryContext
from backend.weknora_port.storage.memory_queue import RedisMemoryExtractionQueue
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.transport import AsyncHTTPTransport,resolve_endpoint
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE


def test_native_memory_timeout_retry_archive_preserves_unknown_without_second_send():
    async def scenario():
        async with isolated_mock_application() as (app,wire,root):
            app.policy=RouterPolicy(mode='EXPENSIVE_ONLY')
            kb=(await app.create_knowledge_base(app.principal,'MQ native memory fixture'))['id']
            await app.update_knowledge_base_consent(app.principal,kb,True)
            doc=await app.upload_document(app.principal,kb,'handbook.txt','text/plain',SOURCE.encode());assert doc['status']=='completed'
            api=create_app(app,resolve_principal=lambda request:app.principal,allowed_origins=('http://127.0.0.1:56425',))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://127.0.0.1:56425',headers={'Origin':'http://127.0.0.1:56425'},timeout=20) as client:
                session=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[doc['id']]})).json()['data']['id']
                response=await client.post('/api/v1/sessions/'+session+'/messages',json={'content':'I use blue widgets. extract source','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[doc['id']]})
                assert response.status_code==200
                await app.drain_background()
                assistant=next(r for r in (await client.get('/api/v1/sessions/'+session+'/messages')).json()['data'] if r['role']=='assistant')
                assert assistant['is_completed']
            async with app.leases.lease() as guard:
                ids=dict((await app._snapshot(guard)).model_ids)
                await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':True,'write_mode':'auto','extract_delay_seconds':5,'extract_min_interval_seconds':1,'extract_model_id':ids['cheap'],'vector_recall':False}),app.principal.tenant_id))
            await app.update_memory_settings(app.principal,True)
            clock=[time.time()]
            queue=RedisMemoryExtractionQueue(app.store.client,app.store.prefix+':mq-native:'+str(uuid.uuid4()),app.principal,clock=lambda:clock[0],randbelow=lambda n:0)
            app.memory_enqueuer=queue;app.memory_worker.enqueuer=queue
            context=MemoryContext(app.principal.tenant_id,app.principal.subject_id)
            # Keep pump unstarted while formal schedule creates its PG pending row.
            assert await app.memory_worker.schedule(context,session,assistant['id'],ids['cheap'],'en-US')
            assert not queue.tasks
            rows=await app.store.client.hgetall(queue.keys[2]);assert len(rows)==1
            initial=json.loads(next(iter(rows.values())))
            assert initial['payload']['scope']['subject_id']==app.principal.subject_id
            timeout_calls=[]
            def handler(request):
                assert request.url.host=='fixture.invalid'
                body=json.loads(request.content)
                assert body['messages'][0]['content'].startswith('You maintain a small set of long-term notes')
                timeout_calls.append(body)
                raise httpx.ReadTimeout('synthetic memory response lost',request=request)
            def factory(config,key,gate):
                return ProviderModel(config,AsyncHTTPTransport(allowed_endpoints=[resolve_endpoint(config,'/chat/completions')],gate=gate,credential=lambda _:key,http_transport=httpx.MockTransport(handler)))
            app.model_factory=factory
            queue.worker=app.memory_worker
            clock[0]+=5
            ledgers=[]
            for attempt in range(3):
                assert await queue.run_due()
                async with app.leases.lease() as guard:
                    ledger=await (await guard.connection.execute("SELECT request_id,state,reserved_microunits,actual_microunits,wire_sha256 FROM send_calls WHERE subject_id=%s AND request_id LIKE 'memory/%%' ORDER BY created_at",(app.principal.subject_id,))).fetchall()
                    assert len(ledger)==1 and ledger[0]['state']=='unknown' and ledger[0]['reserved_microunits']>0 and ledger[0]['actual_microunits'] is None
                    ledgers.append(ledger[0])
                assert len(timeout_calls)==1,'UNKNOWN recovery sent memory request again'
                if attempt<2:
                    assert not await queue.run_due()
                    clock[0]+=15+attempt**4
            assert ledgers[0]==ledgers[1]==ledgers[2]
            assert not await app.store.client.hgetall(queue.keys[2])
            archive=json.loads((await app.store.client.lrange(queue.keys[3],0,-1))[0])
            assert archive['attempts']==3 and archive['retry']==2 and archive['payload']==initial['payload']
            items,total=await app.list_memory(app.principal,'',50,0)
            assert not items and total==0
            await queue.stop()
            # A post-stop follow-up is durable but must not start/send itself.
            from backend.weknora_port.conversation.maintenance_types import ExtractionPayload
            from backend.weknora_port.conversation.types import MemoryScope
            payload=ExtractionPayload(MemoryScope(app.principal.tenant_id,app.principal.subject_id),session,assistant['id'],ids['cheap'],'en-US')
            await queue.enqueue(payload,0,2)
            assert queue._stopped and not queue.tasks and len(timeout_calls)==1
            Path('docs/weknora-port/evidence/P4_MQ_NATIVE_UNKNOWN_20261014.json').write_text(json.dumps({'kind':'ACTUAL_HTTP_TRANSCRIPT_PG_MEMORY_SCHEDULE_REDIS_DELIVERY_NATIVE_EXTRACTION_GATED_MOCK_TIMEOUT','root':str(root),'prefix':queue.prefix,'session':session,'assistant':assistant['id'],'owner':app.principal.subject_id,'payload':initial['payload'],'archive':archive,'ledger_snapshots':ledgers,'delivery_attempts':3,'actual_mock_memory_wire_sends':1,'real_provider_calls':0,'post_stop_enqueue_did_not_start':True,'ledger_rewritten_or_released':False,'queue_records_retained':True},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
