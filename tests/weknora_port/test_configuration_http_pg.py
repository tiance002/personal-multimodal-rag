"""Actual ordinary HTTP/PG/Redis/config/provider messages, fully closed mock wire."""
import json,uuid
from pathlib import Path
from dataclasses import replace
import httpx
from backend.weknora_port.http_api import create_app
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.transport import AsyncHTTPTransport,resolve_endpoint
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE,stream_response


def test_actual_configuration_priority_independent_auxiliary_and_request_replay():
    async def scenario():
        async with isolated_mock_application() as (app,oldwire,root):
            kb=(await app.create_knowledge_base(app.principal,'Config ordinary fixture'))['id']
            await app.update_knowledge_base_consent(app.principal,kb,True)
            doc=await app.upload_document(app.principal,kb,'handbook.txt','text/plain',SOURCE.encode());assert doc['status']=='completed'
            async with app.leases.lease() as guard:
                snapshot=await app._snapshot(guard)
                migration=(await (await guard.connection.execute("SELECT sha256 FROM port_schema_migrations WHERE version='011_turn_configuration'")).fetchall())[0]['sha256']
            ids=dict(snapshot.model_ids);wire=[];intent={'value':'kb_search'}
            def handler(request):
                assert request.url.host=='fixture.invalid'
                body=json.loads(request.content);wire.append({'path':request.url.path,'body':body})
                if request.url.path.endswith('/embeddings'):
                    return httpx.Response(200,json={'model':body['model'],'data':[{'index':i,'embedding':[.1,.2,.3]} for i,_ in enumerate(body['input'])],'usage':{'prompt_tokens':10,'total_tokens':10}})
                if request.url.path.endswith('/rerank'):
                    return httpx.Response(200,json={'results':[{'index':i,'relevance_score':.9} for i,_ in enumerate(body['documents'])]})
                if body.get('stream'):return stream_response(body['model'],'Configuration answer. <ref id="c1"/>','stop')
                return httpx.Response(200,json={'model':body['model'],'choices':[{'message':{'content':json.dumps({'rewrite_query':'blue widgets protocol','intent':intent['value'],'image_description':''})},'finish_reason':'stop'}],'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}})
            def factory(config,key,gate):
                transport=AsyncHTTPTransport(allowed_endpoints=[resolve_endpoint(config,p) for p in ('/chat/completions','/embeddings','/rerank')],gate=gate,credential=lambda _:key,http_transport=httpx.MockTransport(handler))
                return ProviderModel(config,transport)
            app.model_factory=factory
            source=ConversationSettings.from_mapping({'language_locale':'','conversation':{
                'max_rounds':0,'enable_rewrite':True,'enable_query_expansion':False,'enable_rerank':False,
                'rewrite_prompt_system':'STARTUP REWRITE {{language}}','rewrite_prompt_user':'STARTUP USER {{query}}',
                'intent_system_prompts':{'greeting':'GLOBAL GREETING {{language}}'},
                'summary':{'prompt':'STARTUP FINAL {{language}}','context_template':'STARTUP CONTEXT {{query}} {{contexts}}','temperature':.3,'max_completion_tokens':111,'thinking':True}}},prompt_templates={})
            app.conversation=source
            api=create_app(app,resolve_principal=lambda request:app.principal,allowed_origins=('http://127.0.0.1:56425',))
            receipts=[]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://127.0.0.1:56425',headers={'Origin':'http://127.0.0.1:56425'},timeout=20) as client:
                for label,mode,locale,override,profile in [
                    ('request-baseline','OFF','en-US,en;q=0.8',ids['expensive'],False),
                    ('dedicated-vs-cheap','CHEAP_ONLY','ko-KR',ids['expensive'],True),
                    ('invalid-model-fallback','OFF','zh-TW','missing',False),
                    ('non-chat-model-fallback','OFF','',ids['embedding'],False),
                    ('global-intent','OFF','en-US','',True),
                    ('startup-locale-wins','OFF','en-US','',False)]:
                    app.policy=RouterPolicy(mode=mode)
                    app.conversation=replace(source,language_locale='fr-FR' if label=='startup-locale-wins' else '')
                    intent['value']='greeting' if label=='global-intent' else 'kb_search'
                    session=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[doc['id']]})).json()['data']['id']
                    if profile:
                        saved={'conversation':{'query_understand_model_id':ids['expensive'],'rewrite_prompt_system':'SESSION REWRITE {{language}}','rewrite_prompt_user':'SESSION USER {{query}}','intent_prompts':{'greeting':'  '},'embedding_top_k':0,'rerank_threshold':0,'enable_rewrite':None,'summary':{'temperature':0,'max_completion_tokens':0,'thinking':False,'prompt':'SESSION FINAL {{language}}','context_template':'SESSION CONTEXT {{query}} {{contexts}}'}}}
                        async with app.leases.lease() as guard:
                            await guard.connection.execute('UPDATE sessions SET context_config=%s::jsonb WHERE id=%s AND tenant_id=%s AND subject_id=%s',(json.dumps(saved),session,app.principal.tenant_id,app.principal.subject_id))
                    body={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[doc['id']],'summary_model_id':override,'reasoning_effort':'off'}
                    before=len(wire)
                    response=await client.post('/api/v1/sessions/'+session+'/messages',json=body,headers={'Accept-Language':locale});assert response.status_code==200,response.text
                    await app.drain_background()
                    calls=wire[before:]
                    auxiliary=[r['body'] for r in calls if r['path'].endswith('/chat/completions') and not r['body'].get('stream')]
                    answers=[r['body'] for r in calls if r['body'].get('stream')]
                    assert len(auxiliary)==len(answers)==1
                    aux,answer=auxiliary[0],answers[0]
                    expected_language={'request-baseline':'English','dedicated-vs-cheap':'Korean','invalid-model-fallback':'Chinese (Traditional)','non-chat-model-fallback':'Chinese (Simplified)','global-intent':'English','startup-locale-wins':'French'}[label]
                    assert expected_language in aux['messages'][0]['content'] and expected_language in answer['messages'][0]['content']
                    assert answer.get('max_tokens',answer.get('max_completion_tokens'))==111
                    if profile:
                        assert aux['messages'][0]['content'].startswith('SESSION REWRITE')
                        assert 'temperature' not in answer  # source applySampling omits zero
                    else:assert aux['messages'][0]['content'].startswith('STARTUP REWRITE')
                    if label=='dedicated-vs-cheap':
                        assert aux['model']=='deepseek-flash' and answer['model']=='XingChenAGI/Xing4.0-29B'
                    elif label=='request-baseline':assert aux['model']==answer['model']=='deepseek-flash'
                    elif label=='global-intent':assert answer['messages'][0]['content'].startswith('GLOBAL GREETING English')
                    else:assert answer['model']=='XingChenAGI/Xing4.0-29B'
                    if 'Xing' in answer['model']:assert answer['enable_thinking'] is False
                    else:assert answer['thinking']['type']=='disabled'
                    saved=next(r for r in (await client.get('/api/v1/sessions/'+session+'/messages')).json()['data'] if r['role']=='assistant')
                    assert saved['is_completed'] and (await app.turns.status(session,saved['id'])).status=='completed'
                    restored=await app.validate_run(session,saved['id'],app.principal)
                    assert restored.request.summary_model_id==override and restored.request.reasoning_effort=='off' and restored.request.accept_language==locale
                    replay_count=len(wire)
                    repeat=await client.post('/api/v1/sessions/'+session+'/messages',json=body,headers={'Accept-Language':locale});assert repeat.status_code==200 and len(wire)==replay_count
                    conflict=await client.post('/api/v1/sessions/'+session+'/messages',json={**body,'reasoning_effort':'high'},headers={'Accept-Language':locale})
                    assert conflict.status_code==409 and len(wire)==replay_count
                    async with app.leases.lease() as guard:
                        row=(await (await guard.connection.execute('SELECT request_configuration FROM port_turns WHERE run_id=%s',(saved['request_id'],))).fetchall())[0]
                    assert row['request_configuration']['reasoning_effort']=='off'
                    receipts.append({'case':label,'run_id':saved['request_id'],'aux_model':aux['model'],'answer_model':answer['model'],'language':expected_language,'max_tokens':111,'replay_no_sends':True,'changed_options_conflict':True,'request_configuration':row['request_configuration'],'aux_prompt':aux['messages'][0]['content'],'final_prompt_prefix':answer['messages'][0]['content'].split('\n')[0]})
                # Go JSON string fields accept null as unset; prove the real HTTP adapter.
                session=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[doc['id']]})).json()['data']['id']
                result=await client.post('/api/v1/sessions/'+session+'/messages',json={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[doc['id']],'summary_model_id':None,'reasoning_effort':None})
                assert result.status_code==200
                await app.drain_background()
                saved=next(r for r in (await client.get('/api/v1/sessions/'+session+'/messages')).json()['data'] if r['role']=='assistant')
                restored=await app.validate_run(session,saved['id'],app.principal)
                assert saved['is_completed'] and restored.request.summary_model_id==restored.request.reasoning_effort==''
                answer=next(r['body'] for r in reversed(wire) if r['body'].get('stream'))
                assert answer['enable_thinking'] is True and 'French' in answer['messages'][0]['content']
                receipts.append({'case':'request-null-unset','run_id':saved['request_id'],'default_thinking_preserved':True,'language':'French','null_accepted':True})
            Path('docs/weknora-port/evidence/P4_CONFIGURATION_HTTP_PG_20261014.json').write_text(json.dumps({'kind':'ACTUAL_ASGI_HTTP_PG_REDIS_GATED_MOCK_WIRE','schema_011_sha256':migration,'root':str(root),'cases':receipts,'real_provider_calls':0,'historical_rows_rewritten':False},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
