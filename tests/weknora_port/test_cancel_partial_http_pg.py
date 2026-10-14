"""Actual loopback HTTP/PG/Redis partial cancellation; provider stays closed mock."""
import asyncio,json,uuid,socket
from pathlib import Path
from dataclasses import replace
import httpx,uvicorn
from backend.weknora_port.http_api import create_app
from backend.weknora_port.generation import RouterPolicy
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE,stream_response


class PartialStream(httpx.AsyncByteStream):
    def __init__(self,control,model):self.control=control;self.model=model
    def frame(self,delta):return ('data: '+json.dumps({'model':self.model,'choices':[{'delta':delta,'finish_reason':None}]})+'\n\n').encode()
    async def __aiter__(self):
        yield self.frame({'reasoning_content':'Public thought'})
        yield self.frame({'content':'Visible partial <ref id="c1"'})
        yield self.frame({'content':'/> and held <ref id="c'})
        self.control['started'].set()
        await asyncio.Event().wait()
    async def aclose(self):self.control['closed']=True


def test_actual_http_partial_disconnect_hidden_draft_and_final_commit_race():
    async def scenario():
        control={'started':asyncio.Event(),'closed':False,'race':False,'models':[]}
        def stream_factory(model):
            control['models'].append(model)
            if control['race']:return stream_response(model,'Final winner <ref id="c1"/>','stop')
            return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=PartialStream(control,model))
        async with isolated_mock_application(answer_stream_factory=stream_factory) as (app,wire,root):
            kb=(await app.create_knowledge_base(app.principal,'Cancellation native fixture'))['id']
            await app.update_knowledge_base_consent(app.principal,kb,True)
            document=await app.upload_document(app.principal,kb,'handbook.txt','text/plain',SOURCE.encode());assert document['status']=='completed'
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            base=f'http://127.0.0.1:{port}'
            native=create_app(app,resolve_principal=lambda request:app.principal,allowed_origins=(base,))
            server=uvicorn.Server(uvicorn.Config(native,host='127.0.0.1',port=port,log_level='error',lifespan='off'))
            server_task=asyncio.create_task(server.serve())
            for _ in range(200):
                if server.started:break
                if server_task.done():await server_task
                await asyncio.sleep(.01)
            assert server.started
            receipts=[]
            try:
                async with httpx.AsyncClient(base_url=base,headers={'Origin':base},timeout=20) as client:
                    for label,mode,disconnect in [('partial','EXPENSIVE_ONLY',False),('disconnect','EXPENSIVE_ONLY',True),('hidden-cheap','DYNAMIC',True)]:
                        app.policy=RouterPolicy(mode=mode)
                        control.update(started=asyncio.Event(),closed=False,race=False,models=[])
                        session=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[document['id']]})).json()['data']['id']
                        request={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[document['id']]}
                        async with client.stream('POST',f'/api/v1/sessions/{session}/messages',json=request) as response:
                            assert response.status_code==200
                            await asyncio.wait_for(control['started'].wait(),5)
                            rows=(await client.get(f'/api/v1/sessions/{session}/messages')).json()['data']
                            assistant=next(r for r in rows if r['role']=='assistant')
                            before=len(control['models'])
                            if not disconnect:
                                stopped=await client.post(f'/api/v1/sessions/{session}/stop',json={'message_id':assistant['id']});assert stopped.status_code==200
                        if disconnect:
                            assert not control['closed'],'SSE disconnect incorrectly cancelled provider'
                            stopped=await client.post(f'/api/v1/sessions/{session}/stop',json={'message_id':assistant['id']});assert stopped.status_code==200
                        await asyncio.wait_for(app.drain_background(),8)
                        rows=(await client.get(f'/api/v1/sessions/{session}/messages')).json()['data'];saved=next(r for r in rows if r['id']==assistant['id'])
                        assert saved['is_completed'] and saved['turn_status']=='cancelled' and saved['turn_error_code']=='MODEL_CALL_CANCELLED'
                        assert (await app.turns.status(session,assistant['id'])).status=='cancelled'
                        events,_=await app.store.get_events(session,assistant['id'],0)
                        assert not any(e.type=='error' for e in events)
                        assert sum(e.type=='complete' for e in events)==1 and events[-1].data['status']=='cancelled'
                        assert len(control['models'])==before==1 and control['closed']
                        if mode=='DYNAMIC':
                            assert saved['content']=='' and saved['knowledge_references']==[] and saved['agent_steps']==[]
                            assert not any(e.type in ('answer','thinking','references') for e in events)
                        else:
                            assert saved['content'].startswith('Visible partial ') and '<ref' not in saved['content'] and saved['content'].endswith(' and held ')
                            assert len(saved['knowledge_references'])==1 and saved['agent_steps'][0]['reasoning_content']=='Public thought'
                            assert (await client.get('/api/v1/chunks/'+saved['knowledge_references'][0]['id'])).status_code==200
                        repeat=await client.post(f'/api/v1/sessions/{session}/stop',json={'message_id':assistant['id']});assert repeat.json()['message']=='Message already completed'
                        after,_=await app.store.get_events(session,assistant['id'],0);assert len(after)==len(events)
                        async with app.leases.lease() as guard:
                            ledger=await (await guard.connection.execute("SELECT state,reserved_microunits,actual_microunits FROM send_calls WHERE request_id LIKE %s",(saved['request_id']+'/answer/%',))).fetchall()
                            assert len(ledger)==1 and ledger[0]['state']=='unknown' and ledger[0]['reserved_microunits']>0 and ledger[0]['actual_microunits'] is None
                        receipts.append({'case':label,'request':saved['request_id'],'assistant':saved['id'],'partial_saved':bool(saved['content']),'references':len(saved['knowledge_references']),'lifecycle_completed':True,'status':'cancelled','error_toast_events':0,'answer_calls':1,'unknown_occupied':ledger[0]['reserved_microunits']})
                    # Final DB commit wins before a later stop; cancel must not overwrite or label it cancelled.
                    app.policy=RouterPolicy(mode='EXPENSIVE_ONLY');control.update(race=True,models=[])
                    original_commit=app.turns.commit_final;before_commit=asyncio.Event();let_commit=asyncio.Event();written=asyncio.Event();release=asyncio.Event();poll_resume=asyncio.Event();contexts={}
                    original_factory=app.ordinary.generation_factory
                    async def capture(turn,progress):contexts[turn.run_id]=turn.context;return await original_factory(turn,progress)
                    app.ordinary.generation_factory=capture
                    original_watcher=app.ordinary._watch_stop
                    async def delayed_poll(turn):await poll_resume.wait();await original_watcher(turn)
                    app.ordinary._watch_stop=delayed_poll
                    async def blocked_final(commit):before_commit.set();await let_commit.wait();await original_commit(commit);written.set();await release.wait()
                    app.turns.commit_final=blocked_final
                    session=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[document['id']]})).json()['data']['id']
                    request={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[document['id']]}
                    async with client.stream('POST',f'/api/v1/sessions/{session}/messages',json=request) as response:
                        await asyncio.wait_for(before_commit.wait(),5)
                        rows=(await client.get(f'/api/v1/sessions/{session}/messages')).json()['data'];saved=next(r for r in rows if r['role']=='assistant')
                        assert not saved['is_completed']
                        stopped=await client.post(f'/api/v1/sessions/{session}/stop',json={'message_id':saved['id']})
                        assert stopped.json()['message']=='Generation stopped'
                        let_commit.set();await asyncio.wait_for(written.wait(),5)
                        poll_resume.set()
                        await asyncio.wait_for(contexts[saved['request_id']].cancelled.wait(),3)
                        release.set();await asyncio.wait_for(app.drain_background(),8)
                    rows=(await client.get(f'/api/v1/sessions/{session}/messages')).json()['data'];saved=next(r for r in rows if r['role']=='assistant')
                    assert saved['content'].startswith('Final winner') and saved['is_completed'] and not saved['turn_error_code']
                    assert (await app.turns.status(session,saved['id'])).status=='completed'
                    events,_=await app.store.get_events(session,saved['id'],0)
                    assert not any(e.type=='error' for e in events) and sum(e.type=='complete' for e in events)==1 and events[-1].data['status']=='completed'
                    receipts.append({'case':'final-commit-wins','request':saved['request_id'],'assistant':saved['id'],'status':'completed','late_cancel_did_not_overwrite':True,'terminal_events':1,'interleaving':'HTTP stop queued before commit; controlled watcher poll after atomic DB commit'})
                    Path('docs/weknora-port/evidence/P6_CANCEL_PARTIAL_HTTP_20261014.json').write_text(json.dumps({'kind':'ACTUAL_LOOPBACK_HTTP_PG_REDIS_CLOSED_PROVIDER_STREAM','storage_root':str(root),'cases':receipts,'real_provider_calls':0,'old_data_changed':False},indent=2),encoding='utf-8')
            finally:
                if 'release' in locals():
                    let_commit.set();poll_resume.set();release.set()
                    await asyncio.wait_for(app.drain_background(),8)
                server.should_exit=True;await asyncio.wait_for(server_task,5)
    actual_pg_run(scenario())
