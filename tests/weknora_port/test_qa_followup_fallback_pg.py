"""Formal loopback HTTP/SSE, actual isolated PG/Redis, provider wire mocked only."""
import asyncio,json,uuid,socket,os
from pathlib import Path
from contextlib import asynccontextmanager
import httpx,uvicorn
from backend.weknora_port.http_api import create_app
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run

from tests.weknora_port.redis_fixture_identity import expected_redis_run_id
REDIS_ID=expected_redis_run_id()
MODEL_FALLBACK_REPLY='GENERAL_FALLBACK_REPLY (general knowledge, not from this knowledge base).'


def evidence_path(prefix):
    root=Path(os.environ.get('P28_EVIDENCE_DIR','docs/weknora-port/evidence/QA_ENTRY_20261015'))
    root.mkdir(parents=True,exist_ok=True)
    output=root/(prefix+uuid.uuid4().hex+'.json')
    assert not output.exists(),'Preserve previous result; use a new evidence filename'
    return output

@asynccontextmanager
async def http_client(runtime):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    origin=f'http://127.0.0.1:{port}'
    app=create_app(runtime,resolve_principal=lambda _:runtime.principal,allowed_origins=(origin,))
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    task=asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:break
            if task.done():await task
            await asyncio.sleep(.01)
        assert server.started
        async with httpx.AsyncClient(base_url=origin,headers={'Origin':origin},timeout=30) as client:yield client
    finally:
        server.should_exit=True;await task

async def kb(client,name):
    reply=await client.post('/api/v1/knowledge-bases',json={'name':name})
    assert reply.status_code==200,reply.text
    identifier=reply.json()['data']['id']
    reply=await client.patch(f'/api/v1/knowledge-bases/{identifier}/external-processing',json={'cloud_allowed':True})
    assert reply.status_code==200,reply.text
    return identifier

async def session(client,kbid):
    reply=await client.post('/api/v1/sessions',json={'title':'QA isolated source','knowledge_base_scope':[kbid]})
    assert reply.status_code==200,reply.text
    return reply.json()['data']['id']

async def ask(client,runtime,sid,kbid,question):
    request={'content':question,'request_id':str(uuid.uuid4()),'knowledge_base_scope':[kbid]}
    reply=await client.post(f'/api/v1/sessions/{sid}/messages',json=request)
    assert reply.status_code==200,reply.text
    await runtime.drain_background()
    messages=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
    assistants=[row for row in messages if row['role']=='assistant']
    return reply,assistants[-1],request

def frames(reply):
    return [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith('data: ')]

def test_followup_and_source_fallback_through_formal_http(monkeypatch):
    from backend.weknora_port.runtime import LeasedSendGate
    from backend.weknora_port.models.types import ProviderError
    denied_codes=[]
    original_authorize=LeasedSendGate.authorize
    async def observed_authorize(self,call):
        try:return await original_authorize(self,call)
        except ProviderError as exc:
            denied_codes.append(exc.code)
            raise
    monkeypatch.setattr(LeasedSendGate,'authorize',observed_authorize)
    from dataclasses import replace
    from tests.weknora_port.test_runtime_pg import SOURCE,stream_response
    def response(request,body):
        if body.get('stream'):
            system=body['messages'][0]['content']
            text=MODEL_FALLBACK_REPLY if 'QA_FALLBACK' in system or 'No content directly matched' in system else 'Blue widgets use W-17. <ref id="c1"/>'
            return stream_response(body['model'],text,'stop')
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15,response_handler=response,citations_enabled=True) as (runtime,wire,root):
            async with http_client(runtime) as client:
                kbid=await kb(client,'QA source history')
                upload=await client.post(f'/api/v1/knowledge-bases/{kbid}/documents',files={'file':('source.txt',SOURCE,'text/plain')})
                assert upload.status_code==200 and upload.json()['data']['status']=='completed',upload.text
                sid=await session(client,kbid)
                first,assistant,request1=await ask(client,runtime,sid,kbid,'Which protocol do blue widgets use?')
                assert assistant['is_completed'] and not assistant['turn_error_code'] and assistant['knowledge_references'],first.text
                ref=assistant['knowledge_references'][0]
                readback=await client.get('/api/v1/chunks/'+ref['id'])
                assert readback.status_code==200 and SOURCE in readback.text
                start=len(wire)
                second,answer2,request2=await ask(client,runtime,sid,kbid,'And how should I keep them?')
                assert request1['request_id']!=request2['request_id'] and assistant['id']!=answer2['id']
                assert answer2['is_completed'] and not answer2['turn_error_code'],second.text
                new_wire=wire[start:]
                rewrite=[r['body'] for r in new_wire if r['body'].get('messages') and not r['body'].get('stream')]
                assert rewrite and any('Which protocol do blue widgets use?' in json.dumps(r) and 'Blue widgets use W-17.' in json.dumps(r) for r in rewrite)
                final=[r['body'] for r in new_wire if r['body'].get('stream')]
                assert final and any(m['role']=='assistant' and 'Blue widgets use W-17.' in m['content'] for m in final[0]['messages'])
                all_messages=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                assert len(all_messages)==4
                before=len(wire)
                replay=await client.post(f'/api/v1/sessions/{sid}/messages',json=request2)
                assert replay.status_code==200 and len(wire)==before
                empty=await kb(client,'QA empty fallback')
                original=runtime.conversation
                rows=[]
                # P28 restores the native configured strategy, including its
                # fixed response when a model prompt is empty or strategy unknown.
                assert original.fallback_strategy=='model'
                cases=[('default',original,MODEL_FALLBACK_REPLY,1),
                    ('fixed',replace(original,fallback_strategy='fixed',fallback_response='FIXED_SOURCE_REPLY'),'FIXED_SOURCE_REPLY',0),
                    ('empty_prompt',replace(original,fallback_prompt='',fallback_response='EMPTY_PROMPT_FIXED'),'EMPTY_PROMPT_FIXED',0),
                    ('unknown_strategy',replace(original,fallback_strategy='unknown',fallback_response='OTHER_FIXED'),'OTHER_FIXED',0),
                    ('model',replace(original,fallback_prompt='QA_FALLBACK {{language}} {{query}} {{kb_documents}}'),MODEL_FALLBACK_REPLY,1)]
                for name,cfg,expected,stream_calls in cases:
                    runtime.conversation=cfg
                    target=await session(client,empty)
                    offset=len(wire)
                    reply,message,request=await ask(client,runtime,target,empty,'What is available in this knowledge base?')
                    assert message['is_completed'] and not message['turn_error_code'],reply.text
                    assert message['content'] and not message['knowledge_references'],message
                    sent=[r['body'] for r in wire[offset:] if r['body'].get('stream')]
                    assert len(sent)==stream_calls,(name,sent)
                    if stream_calls:
                        marker='No content directly matched' if name=='default' else 'QA_FALLBACK'
                        assert marker in sent[0]['messages'][0]['content']
                        assert '{{kb_documents}}' not in sent[0]['messages'][0]['content']
                        assert SOURCE not in json.dumps(sent)
                    event_frames=frames(reply)
                    assert event_frames[-1]['response_type']=='complete'
                    assert sum(f['response_type']=='complete' for f in event_frames)==1
                    assert any(f.get('data',{}).get('is_fallback') for f in event_frames if f['response_type']=='answer')
                    assert message['content']==expected
                    assert ''.join(f['content'] for f in event_frames if f['response_type']=='answer')==message['content']
                    refreshed=(await client.get(f'/api/v1/sessions/{target}/messages')).json()['data']
                    assert next(m for m in refreshed if m['id']==message['id'])['content']==message['content']
                    replay_start=len(wire)
                    replay=await client.post(f'/api/v1/sessions/{target}/messages',json=request)
                    assert replay.status_code==200 and len(wire)==replay_start
                    assert ''.join(f['content'] for f in frames(replay) if f['response_type']=='answer')==message['content']
                    durable=await runtime._rows('SELECT status,error_code FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND assistant_message_id=%s',
                        (runtime.principal.tenant_id,runtime.principal.subject_id,message['id']))
                    assert durable[0]['status']=='completed' and not durable[0]['error_code']
                    rows.append({'case':name,'session':target,'assistant':message['id'],'content':message['content'],'stream_calls':len(sent),'port_status':durable[0]['status'],'events':event_frames,'replay_new_sends':0,'sse_history_refreshed_equal':True})
                runtime.conversation=original
                # No fallback success may soften a private KB or failed budget authority.
                await client.patch(f'/api/v1/knowledge-bases/{empty}/external-processing',json={'cloud_allowed':False})
                denied_session=await session(client,empty);offset=len(wire)
                denied,denied_message,_=await ask(client,runtime,denied_session,empty,'What is here?')
                assert denied_message['turn_error_code'] and len(wire)==offset,denied.text
                assert 'MODEL_ORIGIN_KB_EGRESS_DENIED' in denied_codes
                denied_codes.clear()
                await client.patch(f'/api/v1/knowledge-bases/{empty}/external-processing',json={'cloud_allowed':True})
                runtime.costs.authority=replace(runtime.costs.authority,account_id='qa-zero-budget-'+str(uuid.uuid4()),monthly_budget_microunits=0)
                budget_session=await session(client,empty);offset=len(wire)
                budget,budget_message,_=await ask(client,runtime,budget_session,empty,'What is here?')
                assert budget_message['turn_error_code'] and len(wire)==offset,budget.text
                assert 'MONTHLY_BUDGET_EXCEEDED' in denied_codes,denied_codes
                evidence={'kind':'actual-loopback-http-postgres-redis-mock-provider-only','real_provider_calls':0,
                    'redis_db':15,'redis_run_id':REDIS_ID,'redis_prefix':runtime.store.prefix,
                    'history':{'session':sid,'first_request_id':request1['request_id'],'second_request_id':request2['request_id'],
                        'first_assistant':assistant['id'],'second_assistant':answer2['id'],'message_count':len(all_messages),
                        'rewrite_contains_first_round':True,'final_contains_first_round':True,'citation_readback_http':readback.status_code,'replay_new_sends':0},
                    'fallback':rows,'private_error':denied_message['turn_error_code'],'budget_error':budget_message['turn_error_code'],'observed_budget_gate_codes':list(denied_codes),
                    'observed_private_gate_code':'MODEL_ORIGIN_KB_EGRESS_DENIED'}
                output=evidence_path('http.result.')
                assert not output.exists(),'Preserve previous result; use a new evidence filename'
                output.write_text(json.dumps(evidence,indent=2,default=str)+'\n',encoding='utf-8')
    actual_pg_run(scenario())


def test_model_fallback_stop_and_scope_failures_remain_terminal():
    from dataclasses import replace
    entered=asyncio.Event()
    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b''
        async def aclose(self):pass
    def response(request,body):
        if body.get('stream'):
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=BlockingStream())
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15,response_handler=response) as (runtime,wire,root):
            runtime.conversation=replace(runtime.conversation,fallback_prompt='QA_FALLBACK {{query}}')
            async with http_client(runtime) as client:
                kbid=await kb(client,'QA stop source KB')
                # With model fallback restored, exercise cancellation on a truly
                # empty KB while retaining the original UNKNOWN and replay checks.
                sid=await session(client,kbid)
                request={'content':'What is here?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kbid]}
                pending=asyncio.create_task(client.post(f'/api/v1/sessions/{sid}/messages',json=request))
                try:
                    await asyncio.wait_for(entered.wait(),12)
                    generations=[r['body'] for r in wire if r['body'].get('stream')]
                    assert len(generations)==1 and 'QA_FALLBACK' in generations[0]['messages'][0]['content']
                    messages=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                    assistant=next(m for m in messages if m['role']=='assistant')
                    stopped=await client.post(f'/api/v1/sessions/{sid}/stop',json={'message_id':assistant['id']})
                    assert stopped.status_code==200,stopped.text
                    reply=await asyncio.wait_for(pending,12)
                finally:
                    if not pending.done():pending.cancel();await asyncio.gather(pending,return_exceptions=True)
                await runtime.drain_background()
                state=await runtime.turns.status(sid,assistant['id'])
                assert state.status=='cancelled' and state.error_code=='MODEL_CALL_CANCELLED',reply.text
                assert sum(frame['response_type']=='complete' for frame in frames(reply))==1
                before=len(wire)
                replay=await client.post(f'/api/v1/sessions/{sid}/messages',json=request)
                assert replay.status_code==200 and len(wire)==before
                sibling=await kb(client,'QA outside session scope')
                before=len(wire)
                denied=await client.post(f'/api/v1/sessions/{sid}/messages',json={**request,'request_id':str(uuid.uuid4()),'knowledge_base_scope':[sibling]})
                assert denied.status_code==403 and len(wire)==before,denied.text
                ledger=await runtime._rows('SELECT capability,state,usage FROM send_calls WHERE account_id=%s ORDER BY created_at',
                    (runtime.costs.authority.account_id,))
                assert any(row['state']=='unknown' for row in ledger)
                output=evidence_path('cancel-scope.result.')
                assert not output.exists()
                output.write_text(json.dumps({'session':sid,'assistant':assistant['id'],'status':state.status,
                    'error':state.error_code,'scope_http':denied.status_code,'ledger':ledger,'events':frames(reply),
                    'cancel_replay_sends':0,'scope_denied_sends':0,'real_provider_calls':0},indent=2,default=str)+'\n',encoding='utf-8')
    actual_pg_run(scenario())


def test_fallback_listing_preserves_document_scope_through_http():
    from dataclasses import replace
    from tests.weknora_port.test_runtime_pg import SOURCE,stream_response
    captured=[]
    def response(request,body):
        if body.get('stream'):
            captured.append(body)
            return stream_response(body['model'],'GENERAL_SCOPE_REPLY','stop')
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15,response_handler=response) as (runtime,wire,root):
            runtime.conversation=replace(runtime.conversation,fallback_prompt='QA_FALLBACK {{kb_documents}} {{query}}')
            async with http_client(runtime) as client:
                kbid=await kb(client,'QA scoped listing')
                docs=[]
                for name in ('selected-public.txt','SIBLING_SCOPE_SENTINEL.txt'):
                    reply=await client.post(f'/api/v1/knowledge-bases/{kbid}/documents',files={'file':(name,SOURCE,'text/plain')})
                    assert reply.status_code==200 and reply.json()['data']['status']=='completed',reply.text
                    docs.append(reply.json()['data']['id'])
                reply=await client.post('/api/v1/sessions',json={'title':'bounded fallback listing','knowledge_base_scope':[kbid],'document_scope':[docs[0]]})
                sid=reply.json()['data']['id']
                async with runtime.leases.lease() as guard:
                    # Source direct-document recall bypasses score thresholds; disable only
                    # this new fixture document's index, preserving all chunks and originals.
                    await guard.connection.execute('UPDATE embeddings SET is_enabled=FALSE WHERE knowledge_id=%s AND knowledge_base_id=%s',
                        (docs[0],kbid))
                reply,assistant,_=await ask(client,runtime,sid,kbid,'List available documents.')
                assert assistant['is_completed'] and not assistant['turn_error_code'],reply.text
                assert len(captured)==1 and assistant['content']=='GENERAL_SCOPE_REPLY'
                assert 'selected-public' in captured[0]['messages'][0]['content']
                assert not assistant['knowledge_references']
                assert 'SIBLING_SCOPE_SENTINEL' not in json.dumps(captured)
                assert ''.join(f['content'] for f in frames(reply) if f['response_type']=='answer')==assistant['content']
                refreshed=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                assert next(m for m in refreshed if m['id']==assistant['id'])['content']==assistant['content']
                output=evidence_path('listing.scope.')
                output.write_text(json.dumps({'session':sid,'allowed_document':docs[0],'excluded_document':docs[1],
                    'assistant':assistant['id'],'messages':captured,'model_fallback':assistant['content'],'real_provider_calls':0,
                    'scope':'actual PG disabled-index empty search; configured model fallback lists only admitted document metadata'},indent=2)+'\n',encoding='utf-8')
    actual_pg_run(scenario())
