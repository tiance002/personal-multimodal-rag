"""Actual HTTP/PG/Redis + gated mock ProviderModel; source channel notice injection."""
import asyncio,json,uuid
from pathlib import Path
import httpx
from backend.weknora_port.http_api import create_app
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.models.types import StreamResponse
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE,stream_response


def test_actual_http_same_channel_recovery_and_error_eof_unknown():
    async def scenario():
        mode={'fail':False}
        def stream_factory(model):
            if mode['fail']:
                rows=[{'model':model,'choices':[{'delta':{'reasoning_content':'thought re','content':'answer re'},'finish_reason':None}]}]
                return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(rows[0])+'\n\ndata: {invalid\n\n')
            return stream_response(model,'Recovered W-17. <ref id="c1"/>','stop')
        async with isolated_mock_application(answer_stream_factory=stream_factory) as (app,wire,root):
            app.policy=RouterPolicy(mode='EXPENSIVE_ONLY')
            original_factory=app.model_factory
            def factory(config,key,gate):
                model=original_factory(config,key,gate)
                underlying=model.chat_stream
                async def with_notice(messages,options,context):
                    yield StreamResponse('error','PRIVATE MOCK PROVIDER BODY',True,data={'error_code':'PROVIDER_RESPONSE_INVALID'})
                    async for value in underlying(messages,options,context):yield value
                model.chat_stream=with_notice
                return model
            app.model_factory=factory
            kb=(await app.create_knowledge_base(app.principal,'Stream continuation fixture'))['id']
            await app.update_knowledge_base_consent(app.principal,kb,True)
            doc=await app.upload_document(app.principal,kb,'handbook.txt','text/plain',SOURCE.encode());assert doc['status']=='completed'
            api=create_app(app,resolve_principal=lambda request:app.principal,allowed_origins=('http://127.0.0.1:56425',))
            receipts=[]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://127.0.0.1:56425',headers={'Origin':'http://127.0.0.1:56425'},timeout=20) as client:
                for failed in (False,True):
                    mode['fail']=failed
                    created=await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[doc['id']]});assert created.status_code==200,created.json();session=created.json()['data']['id']
                    result=await client.post('/api/v1/sessions/'+session+'/messages',json={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[doc['id']]})
                    assert result.status_code==200
                    await app.drain_background()
                    saved=next(r for r in (await client.get('/api/v1/sessions/'+session+'/messages')).json()['data'] if r['role']=='assistant')
                    events,_=await app.store.get_events(session,saved['id'],0)
                    assert 'PRIVATE MOCK' not in result.text and sum(x.type=='complete' for x in events)==1
                    async with app.leases.lease() as guard:
                        ledger=await (await guard.connection.execute("SELECT state,reserved_microunits,actual_microunits FROM send_calls WHERE request_id LIKE %s",(saved['request_id']+'/answer/%',))).fetchall()
                        attempts=await (await guard.connection.execute('SELECT role,status FROM generation_attempts WHERE run_id=%s',(saved['request_id'],))).fetchall()
                    assert len(ledger)==len(attempts)==1
                    if failed:
                        assert saved['turn_status']=='error' and not saved['is_completed'] and events[-1].data['status']=='failed'
                        assert ledger[0]['state']=='unknown' and ledger[0]['reserved_microunits']>0
                        assert ''.join(x.content for x in events if x.type=='answer')=='answer re'
                        assert ''.join(x.content for x in events if x.type=='thinking')=='thought re'
                        assert attempts[0]['status']=='FAILED'
                    else:
                        assert saved['is_completed'] and saved['content'].startswith('Recovered W-17.') and len(saved['knowledge_references'])==1
                        assert events[-1].type=='complete' and attempts[0]['status']=='COMPLETED'
                        assert len([x for x in events if x.type=='error' and x.data.get('non_terminal')])==1
                        ref=saved['knowledge_references'][0];assert (await client.get('/api/v1/chunks/'+ref['id'])).status_code==200
                    receipts.append({'request_id':saved['request_id'],'case':'error-eof' if failed else 'same-channel-recovered','answer_attempts':1,'ledger':ledger,'attempt':attempts[0],'terminal_events':1})
            Path('docs/weknora-port/evidence/P1_STREAM_ERROR_HTTP_PG_20261014.json').write_text(json.dumps({'kind':'ACTUAL_ASGI_HTTP_PG_REDIS_GATED_MOCK_PROVIDER','scope':'one source-style channel notice injected before unchanged real ProviderModel mock-wire; malformed JSON failure uses actual parser','root':str(root),'cases':receipts,'real_provider_calls':0},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
