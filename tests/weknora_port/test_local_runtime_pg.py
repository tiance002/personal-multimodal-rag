"""Actual HTTP/PG/Redis Local MockTransport; no real provider/Ollama sends."""
import asyncio,json,uuid
from pathlib import Path
import httpx,pytest
from backend.weknora_port.http_api import create_app
from backend.weknora_port.models.local_ollama import HTTPLocalService
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.transport import AsyncHTTPTransport,endpoint_urls
from backend.weknora_port.models.types import CallContext,TrustedScope,Message,ChatOptions
from backend.weknora_port.storage.configuration import ConfiguredRole
from backend.weknora_port.runtime import ConversationSettings,LeasedSendGate
from backend.weknora_port.generation import RouterPolicy
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE

OUT=Path('docs/weknora-port/evidence/LOCAL_TAKEOVER_20261015')
BASE='http://127.0.0.1:12499'

async def prepare(app,fail):
    app.policy=RouterPolicy(mode='OFF')
    app.conversation=ConversationSettings(max_rounds=0,enable_rewrite=False,enable_query_expansion=False,enable_rerank=False)
    kb=(await app.create_knowledge_base(app.principal,'Local synthetic fixture'))['id']
    await app.update_knowledge_base_consent(app.principal,kb,True)
    doc=await app.upload_document(app.principal,kb,'local.txt','text/plain',SOURCE.encode());assert doc['status']=='completed'
    local_id=await app.models.persist(ConfiguredRole('cheap','ollama','tiny:1b','https://inline.invalid/ignored','synthetic-inline-key',source='local'))
    async with app.leases.lease() as guard:
        await guard.connection.execute('UPDATE knowledge_bases SET summary_model_id=%s WHERE tenant_id=%s AND id=%s',(local_id,app.principal.tenant_id,kb))
        await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':True,'write_mode':'auto','vector_recall':False,'retrieval_conditioning':False}),app.principal.tenant_id))
    await app.update_memory_settings(app.principal,True)
    app.local_service=HTTPLocalService(BASE,allow_pull=False)
    original=app.model_factory;requests=[];made=[]
    def handler(request):
        requests.append({'method':request.method,'path':request.url.path,'authorization_present':'authorization' in request.headers})
        assert request.url.host=='127.0.0.1' and request.url.port==12499
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'tiny:1b'}]})
        assert request.url.path=='/api/chat' and request.method=='POST'
        body=json.loads(request.content);assert body['model']=='tiny:1b' and body['options']['num_predict']>0
        if fail:return httpx.Response(200,content=b'{"error":"synthetic generation failure"}\n')
        if body['stream']:return httpx.Response(200,content=b'{"message":{"content":"Blue widgets use W-17. <ref id=\\"c1\\"/>"}}\n{"done":true,"prompt_eval_count":4,"eval_count":3}\n')
        return httpx.Response(200,json={'message':{'content':'synthetic local aux'},'done':True,'prompt_eval_count':2,'eval_count':1})
    def factory(config,key,gate):
        if config.source!='local':return original(config,key,gate)
        assert config.base_url==BASE and config.auth_style=='none'
        transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(config),gate=gate,credential=lambda _:(_ for _ in ()).throw(AssertionError('inline local key read')),http_transport=httpx.MockTransport(handler))
        model=ProviderModel(config,transport);made.append((model,gate));return model
    app.model_factory=factory
    return kb,doc,local_id,requests,made

async def ask(app,kb,doc,content):
    api=create_app(app,resolve_principal=lambda request:app.principal,allowed_origins=('http://127.0.0.1:56425',))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://127.0.0.1:56425',headers={'Origin':'http://127.0.0.1:56425'}) as client:
        sid=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[doc['id']]})).json()['data']['id']
        body={'content':content,'request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[doc['id']]}
        response=await client.post('/api/v1/sessions/'+sid+'/messages',json=body)
        assert response.status_code==200
        await app.ordinary.drain_background()
        messages=(await client.get('/api/v1/sessions/'+sid+'/messages')).json()['data']
        answer=next(r for r in messages if r['role']=='assistant')
        before=await app._rows('SELECT count(*) AS n FROM send_calls WHERE tenant_id=%s AND subject_id=%s',(app.principal.tenant_id,app.principal.subject_id))
        jobs=await app.memory_enqueuer.client.hlen(app.memory_enqueuer.keys[2])
        replay=await client.post('/api/v1/sessions/'+sid+'/messages',json=body)
        assert replay.status_code==200
        await app.ordinary.drain_background()
        after=await app._rows('SELECT count(*) AS n FROM send_calls WHERE tenant_id=%s AND subject_id=%s',(app.principal.tenant_id,app.principal.subject_id))
        assert after==before and await app.memory_enqueuer.client.hlen(app.memory_enqueuer.keys[2])==jobs

    rows=await app._rows('SELECT status,error_code FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s',(app.principal.tenant_id,app.principal.subject_id,sid))
    return answer,rows[0],response.text

def test_native_local_both_runtime_factories_share_gate_and_complete():
    async def run():
        async with isolated_mock_application(expected_redis_run_id=json.loads((OUT/"redis-identity.json").read_text())["run_id"],redis_db=15) as (app,wire,root):
            kb,doc,local_id,requests,made=await prepare(app,False)
            registry=app.registry_for_context()
            try:
                model=await registry.get(local_id,CallContext('local-aux-'+uuid.uuid4().hex,TrustedScope(app.principal.subject_id,(kb,))))
                assert model.local_service is app.local_service
                aux=await model.chat((Message('user','synthetic aux'),),ChatOptions(max_tokens=8),CallContext('local-aux-call-'+uuid.uuid4().hex,TrustedScope(app.principal.subject_id,(kb,))))
                assert aux.content=='synthetic local aux'
            finally:await registry.close()
            answer,row,stream=await ask(app,kb,doc,'What protocol do blue widgets use?')
            assert row['status']=='completed' and not row['error_code'] and answer['is_completed']
            assert 'W-17' in answer['content']
            assert len(made)==2 and all(isinstance(gate,LeasedSendGate) and model.transport.gate is gate and model.local_service is app.local_service for model,gate in made)
            assert len(await app.memory_enqueuer.client.hgetall(app.memory_enqueuer.keys[2]))==1
            assert not any(r['authorization_present'] for r in requests)
            (OUT/'runtime-success.json').write_text(json.dumps({'root':str(root),'turn':row,'requests':requests,'both_factories':True,'automatic_queue':1,'real_calls':0},indent=2),encoding='utf-8')
    actual_pg_run(run())

@pytest.mark.parametrize('failure_receipt',[False,True])
def test_failed_remember_http_turn_never_writes_memory_or_queues(monkeypatch,failure_receipt):
    receipts=[]
    if failure_receipt:
        from backend.weknora_port.service import RuntimeService
        from types import SimpleNamespace
        original=RuntimeService.run_turn
        async def with_receipt(self,turn):
            result=await original(self,turn)
            if result is None:
                result=SimpleNamespace(references=(),failure_receipt=True)
                receipts.append(result)
            return result
        monkeypatch.setattr(RuntimeService,'run_turn',with_receipt)

    async def run():
        async with isolated_mock_application(expected_redis_run_id=json.loads((OUT/"redis-identity.json").read_text())["run_id"],redis_db=15) as (app,wire,root):
            kb,doc,local_id,requests,made=await prepare(app,True)
            assert len(await app.memory_enqueuer.client.hgetall(app.memory_enqueuer.keys[2]))==0
            answer,row,stream=await ask(app,kb,doc,'remember that synthetic blue widgets use protocol W-17')
            assert row['status']=='error' and row['error_code']
            if failure_receipt:assert len(receipts)==1 and receipts[0] is not None
            assert not answer['is_completed']
            counts=await app._rows('SELECT count(*) AS n FROM memory_items WHERE tenant_id=%s AND subject_id=%s',(app.principal.tenant_id,app.principal.subject_id))
            assert counts[0]['n']==0
            assert len(await app.memory_enqueuer.client.hgetall(app.memory_enqueuer.keys[2]))==0
            ledger=await app._rows('SELECT capability,state,reserved_microunits,actual_microunits FROM send_calls WHERE tenant_id=%s AND subject_id=%s AND model_id=%s',(app.principal.tenant_id,app.principal.subject_id,local_id))
            assert len(ledger)==2 and all(r['state']=='unknown' and r['reserved_microunits']>0 and r['actual_microunits'] is None for r in ledger)
            (OUT/('runtime-failed-object.json' if failure_receipt else 'runtime-failed-remember.json')).write_text(json.dumps({'root':str(root),'turn':row,'non_none_failure_receipt':failure_receipt,'memory_items':0,'queue_jobs':0,'ledger':ledger,'requests':requests,'real_calls':0},indent=2),encoding='utf-8')
    actual_pg_run(run())