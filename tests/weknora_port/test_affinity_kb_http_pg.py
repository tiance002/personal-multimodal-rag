"""Actual ordinary HTTP + isolated PG/Redis: KB model order and affinity consumer."""
import asyncio,json,uuid
from pathlib import Path
from dataclasses import replace
import httpx
from backend.weknora_port.http_api import create_app
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.storage.configuration import ConfiguredRole
from backend.weknora_port.conversation.types import MemoryContext,MemoryScope
from backend.weknora_port.service import OrdinaryTurnRequest
from backend.weknora_port.storage.turns import ScopeSnapshot
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE,stream_response


def test_native_model_priority_order_replay_and_affinity_delete_entry():
    async def scenario():
        async with isolated_mock_application(answer_stream_factory=lambda model:stream_response(model,'Fixture answer <ref id="c1"/>','stop')) as (app,wire,root):
            app.policy=RouterPolicy(mode='OFF')
            app.conversation=ConversationSettings(max_rounds=0,enable_rewrite=False,enable_query_expansion=False,enable_rerank=False)
            bases=[];documents=[]
            for label in ('Alpha','Beta'):
                kb=(await app.create_knowledge_base(app.principal,label))['id'];await app.update_knowledge_base_consent(app.principal,kb,True)
                doc=await app.upload_document(app.principal,kb,label+'.txt','text/plain',(SOURCE+' '+label).encode());assert doc['status']=='completed'
                bases.append(kb);documents.append(doc)
            upper,lower=sorted(bases,reverse=True)
            async with app.leases.lease() as guard:
                ids=dict((await app._snapshot(guard)).model_ids)
                await guard.connection.execute('UPDATE knowledge_bases SET summary_model_id=%s WHERE tenant_id=%s AND id=%s',(ids['expensive'],app.principal.tenant_id,upper))
            remote=await app.models.persist(ConfiguredRole('expensive','remote','fixture-remote','http://fixture.invalid/v1','synthetic-not-secret'))
            api=create_app(app,resolve_principal=lambda request:app.principal,allowed_origins=('http://127.0.0.1:56425',))
            receipts=[]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://127.0.0.1:56425',headers={'Origin':'http://127.0.0.1:56425'},timeout=20) as client:
                last=None
                for label,request_order,override,expected in [
                    ('first-kb-order',[upper,lower],'','deepseek-flash'),
                    ('reversed-first-kb',[lower,upper],'','XingChenAGI/Xing4.0-29B'),
                    ('literal-remote-priority',[upper,lower],'','fixture-remote'),
                    ('valid-request-overrides-kb',[upper,lower],ids['cheap'],'XingChenAGI/Xing4.0-29B')]:
                    if label=='literal-remote-priority':
                        async with app.leases.lease() as guard:await guard.connection.execute('UPDATE knowledge_bases SET summary_model_id=%s WHERE tenant_id=%s AND id=%s',(remote,app.principal.tenant_id,lower))
                    session=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':request_order,'document_scope':[]})).json()['data']['id']
                    body={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':request_order,'document_scope':[],'summary_model_id':override}
                    before=len(wire)
                    response=await client.post('/api/v1/sessions/'+session+'/messages',json=body);assert response.status_code==200
                    await app.drain_background()
                    streams=[r['body'] for r in wire[before:] if r['body'].get('stream')]
                    assert len(streams)==1 and streams[0]['model']==expected
                    saved=next(r for r in (await client.get('/api/v1/sessions/'+session+'/messages')).json()['data'] if r['role']=='assistant');assert saved['is_completed']
                    restored=await app.validate_run(session,saved['id'],app.principal)
                    assert list(restored.request.knowledge_base_scope)==request_order
                    count=len(wire);repeat=await client.post('/api/v1/sessions/'+session+'/messages',json=body);assert repeat.status_code==200 and len(wire)==count
                    changed=await client.post('/api/v1/sessions/'+session+'/messages',json={**body,'knowledge_base_scope':list(reversed(request_order))});assert changed.status_code==409 and len(wire)==count
                    receipts.append({'case':label,'run_id':saved['request_id'],'requested_order':request_order,'model':expected,'replay_no_send':True,'changed_order_conflict':True})
                    last=(session,body,saved,restored)
                # Synthetic pre-versioned v1 completed turn: preserve its old hash and replay, no new send.
                session,body,saved,restored=last
                legacy=app.turns._payload(restored.request,ScopeSnapshot(body['content'],tuple(sorted(bases)),()),include_order=False)
                # This row had a source-model override, so legacy v1 must also omit that new option.
                clean=replace(restored.request,summary_model_id='')
                legacy=app.turns._payload(clean,ScopeSnapshot(body['content'],tuple(sorted(bases)),()),include_order=False)
                async with app.leases.lease() as guard:await guard.connection.execute("UPDATE port_turns SET request_configuration='{}'::jsonb,payload_sha256=%s WHERE tenant_id=%s AND subject_id=%s AND run_id=%s",(legacy,app.principal.tenant_id,app.principal.subject_id,saved['request_id']))
                count=len(wire)
                old=await client.post('/api/v1/sessions/'+session+'/messages',json={**body,'summary_model_id':''});assert old.status_code==200 and len(wire)==count
                # Enable source conditioning only for this new owner. It is an explicit_only memory setup.
                async with app.leases.lease() as guard:await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':True,'write_mode':'explicit_only','retrieval_conditioning':True}),app.principal.tenant_id))
                await app.update_memory_settings(app.principal,True)
                familiar=documents[1]
                async with app.leases.lease() as guard:
                    await guard.connection.execute('INSERT INTO memory_doc_affinity(id,tenant_id,subject_id,knowledge_id,knowledge_base_id,title,hits,last_used_at) VALUES(%s,%s,%s,%s,%s,%s,8,now())',(str(uuid.uuid4()),app.principal.tenant_id,app.principal.subject_id,familiar['id'],familiar['knowledge_base_id'],'Familiar fixture'))
                app.conversation=replace(app.conversation,enable_rerank=True)
                observed=[];original=app.ordinary.pipeline_factory
                async def pipeline(turn,progress):
                    pipe=await original(turn,progress);real=pipe.affinity_resolver
                    async def tracked(ids):
                        values=await real(ids);observed.append({'ids':list(ids),'values':values});return values
                    pipe.affinity_resolver=tracked;return pipe
                app.ordinary.pipeline_factory=pipeline
                async def ask():
                    sid=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[familiar['knowledge_base_id']],'document_scope':[familiar['id']]})).json()['data']['id']
                    result=await client.post('/api/v1/sessions/'+sid+'/messages',json={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[familiar['knowledge_base_id']],'document_scope':[familiar['id']]});assert result.status_code==200
                    await app.drain_background();return sid
                sid=await ask();assert observed[-1]['values'][familiar['id']]==8
                listed=(await client.get('/api/v1/memory/documents')).json();row=next(r for r in listed['data'] if r['knowledge_id']==familiar['id'])
                original_bytes=(await client.get('/api/v1/knowledge-bases/'+familiar['knowledge_base_id']+'/documents/'+familiar['id']+'/source')).content
                deleted=await client.delete('/api/v1/memory/documents/'+row['id']);assert deleted.status_code==200
                assert not (await client.get('/api/v1/memory/documents')).json()['data']
                after=(await client.get('/api/v1/knowledge-bases/'+familiar['knowledge_base_id']+'/documents/'+familiar['id']+'/source')).content;assert after==original_bytes
                history=(await client.get('/api/v1/sessions/'+sid+'/messages')).json()['data'];assert any(r['role']=='assistant' and r['is_completed'] for r in history)
                await ask();assert familiar['id'] not in observed[-1]['values']
                await app.update_memory_settings(app.principal,False);await ask();assert observed[-1]['values']=={}
                receipts.append({'case':'formal-affinity-consumer','observations':observed,'stop_tracking_removed_signal':True,'source_and_history_preserved':True,'disabled_user_no_boost':True,'legacy_v1_replay_no_send':True})
            Path('docs/weknora-port/evidence/P4_AFFINITY_KB_HTTP_20261014.json').write_text(json.dumps({'kind':'ACTUAL_ASGI_HTTP_PG_REDIS_NATIVE_RAG_PROVIDER_MOCK','root':str(root),'cases':receipts,'real_provider_calls':0,'existing_history_rewritten':False,'legacy_fixture_only':True},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
