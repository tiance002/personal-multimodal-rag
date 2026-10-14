"""Source/provider representation plus actual HTTP post-turn automatic enqueue."""
import asyncio,json,uuid,hashlib
from dataclasses import replace
from pathlib import Path
import pytest,httpx
from backend.weknora_port.storage.configuration import ConfiguredRole,ModelConfigurationError
from backend.weknora_port.storage.models import SECRET_FIELDS,_json
from backend.weknora_port.model_registry import SourceCatalogue,NeutralModelRegistry
from backend.weknora_port.models.types import CallContext,TrustedScope,ProviderError,Message
from backend.weknora_port.http_api import create_app
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.generation import RouterPolicy
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import SOURCE,stream_response


def test_local_source_stays_local_and_only_needs_a_local_service():
    async def run():
        role=ConfiguredRole('cheap','deepseek','synthetic-local','http://fixture.invalid/ignored',source='local')
        class Store:
            async def resolve(self,id):return role
        from backend.weknora_port.models.provider import ProviderModel
        config=SourceCatalogue().resolve(role,'id')
        assert config.source=='local'
        seen=[]
        def factory(cfg,key):seen.append(cfg);return ProviderModel(cfg,None)
        registry=NeutralModelRegistry(Store(),factory,owner_id='owner')
        model=await registry.get('id',CallContext('run',TrustedScope('owner',())))
        # local stays local through the factory; it is never rewritten into a remote send.
        assert seen and seen[0].source=='local' and model.config.source=='local'
        # No injected local service is an explicit, readable refusal, not a silent remote call.
        with pytest.raises(ProviderError,match='MODEL_LOCAL_SERVICE_UNAVAILABLE'):
            await model.chat((Message('user',content='hi'),),None,CallContext('run',TrustedScope('owner',())))
    asyncio.run(run())


def test_native_legacy_source_compatibility_and_http_automatic_queue():
    async def run():
        async with isolated_mock_application(answer_stream_factory=lambda model:stream_response(model,'Blue widgets use W-17. <ref id="c1"/>','stop')) as (app,wire,root):
            # Synthesize an actual v1 row only for this new owner. No existing business row migrated.
            role=ConfiguredRole('cheap','deepseek','legacy-synthetic','http://fixture.invalid/v1','synthetic-key')
            old_id,identity,physical,parameters=app.models._identity(role)
            stored={k:app.cipher.encrypt(v) if k in SECRET_FIELDS else v for k,v in parameters.items()}
            async with app.leases.lease() as guard:
                await guard.connection.execute('INSERT INTO models(id,tenant_id,subject_id,name,type,source,parameters,role,identity_sha256,physical_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)',(old_id,app.principal.tenant_id,app.principal.subject_id,role.model,'KnowledgeQA',role.provider,_json(stored),role.role,identity,physical))
            loaded=await app.models.resolve(old_id)
            assert loaded.source=='' and loaded.provider=='deepseek'
            assert SourceCatalogue().resolve(loaded,old_id).source==''
            assert await app.models.persist(replace(loaded,api_key='replacement-synthetic-key'))==old_id
            with pytest.raises(ModelConfigurationError,match='MODEL_SOURCE_REQUIRED'):
                await app.models.persist(replace(role,model='new-undeclared-source'))
            new_id=await app.models.persist(replace(loaded,source='remote'))
            assert new_id!=old_id
            local_id=await app.models.persist(replace(loaded,source='local'))
            assert local_id not in (old_id,new_id)
            with pytest.raises(ModelConfigurationError,match='MODEL_SOURCE_INVALID'):
                await app.models.persist(replace(loaded,source='vendor'))
            row,params,new_role=await app.models._load(new_id)
            assert row['source']=='remote' and params['provider']=='deepseek' and new_role.source=='remote'
            old_row,_,_=await app.models._load(old_id)
            assert old_row['source']=='deepseek' and old_row['physical_sha256']==physical and old_row['identity_sha256']==identity
            # Tenant disable/delete protection still applies to both formats.
            async with app.leases.lease() as guard:await guard.connection.execute('UPDATE tenants SET deleted_at=now() WHERE id=%s',(app.principal.tenant_id,))
            try:
                for model in (old_id,new_id):
                    with pytest.raises(ModelConfigurationError,match='MODEL_NOT_FOUND'):await app.models.resolve(model)
                with pytest.raises(ModelConfigurationError,match='MODEL_TENANT_UNAVAILABLE'):await app.models.persist(replace(loaded,source='remote'))
            finally:
                async with app.leases.lease() as guard:await guard.connection.execute('UPDATE tenants SET deleted_at=NULL WHERE id=%s',(app.principal.tenant_id,))
            app.policy=RouterPolicy(mode='OFF');app.conversation=ConversationSettings(max_rounds=0,enable_rewrite=False,enable_query_expansion=False,enable_rerank=False)
            kb=(await app.create_knowledge_base(app.principal,'Automatic memory queue fixture'))['id']
            await app.update_knowledge_base_consent(app.principal,kb,True)
            doc=await app.upload_document(app.principal,kb,'automatic.txt','text/plain',SOURCE.encode());assert doc['status']=='completed'
            async with app.leases.lease() as guard:await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':True,'write_mode':'auto','retrieval_conditioning':False,'vector_recall':False}),app.principal.tenant_id))
            await app.update_memory_settings(app.principal,True)
            api=create_app(app,resolve_principal=lambda request:app.principal,allowed_origins=('http://127.0.0.1:56425',))
            queue=app.memory_enqueuer;ready,leased,jobs,dead=queue.keys
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://127.0.0.1:56425',headers={'Origin':'http://127.0.0.1:56425'}) as client:
                async def ask():
                    sid=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[doc['id']]})).json()['data']['id']
                    body={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[doc['id']]}
                    response=await client.post('/api/v1/sessions/'+sid+'/messages',json=body);assert response.status_code==200
                    await app.ordinary.drain_background()
                    rows=(await client.get('/api/v1/sessions/'+sid+'/messages')).json()['data'];answer=next(r for r in rows if r['role']=='assistant');assert answer['is_completed']
                    return sid,body,answer
                public=(await client.get('/api/v1/settings/model-configuration')).json()['data']
                assert all(v['source']=='remote' for v in public['roles'])
                vision={'source':'remote','provider':'siliconflow','model':'Qwen/Qwen3-VL-32B-Instruct','base_url':'https://fixture.invalid/v1','api_key':'synthetic-vision-key'}
                registered=await client.post('/api/v1/settings/vision-models',json=vision)
                assert registered.status_code==200 and registered.json()['data']['source']=='remote'
                missing=await client.post('/api/v1/settings/vision-models',json={k:v for k,v in vision.items() if k!='source'})
                assert missing.status_code==422
                before=queue.clock();sid,body,answer=await ask()
                values=await queue.client.hgetall(jobs);assert len(values)==1
                job=json.loads(next(iter(values.values())));payload=job['payload']
                assert payload['session_id']==sid and payload['message_id']==answer['id']
                assert payload['scope']['tenant_id']==app.principal.tenant_id and payload['scope']['subject_id']==app.principal.subject_id
                async with app.leases.lease() as guard:
                    selected=(await app._snapshot(guard)).model_id('cheap')
                assert payload['chat_model_id']==selected and job['max_retry']==2
                due=await queue.client.zscore(ready,job['id']);assert before+90<=due<=queue.clock()+90
                # Change current candidate role source explicitly; completed request stays pinned/no send.
                write_roles=[{k:v for k,v in r.items() if k in ('role','provider','model','base_url','source')} for r in public['roles']]
                next(r for r in write_roles if r['role']=='cheap').update(source='local',api_key='synthetic-explicit-source-rebind')
                changed=await client.put('/api/v1/settings/model-configuration',json={'revision':public['revision'],'roles':write_roles})
                assert changed.status_code==200 and next(r for r in changed.json()['data']['roles'] if r['role']=='cheap')['source']=='local'
                sends=len(wire);replay=await client.post('/api/v1/sessions/'+sid+'/messages',json=body);assert replay.status_code==200
                await app.ordinary.drain_background();assert len(wire)==sends and len(await queue.client.hgetall(jobs))==1
                next(r for r in write_roles if r['role']=='cheap').update(source='remote',api_key='synthetic-explicit-source-rebind')
                restored=await client.put('/api/v1/settings/model-configuration',json={'revision':changed.json()['data']['revision'],'roles':write_roles})
                assert restored.status_code==200
                async with app.leases.lease() as guard:await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':True,'write_mode':'explicit_only','retrieval_conditioning':False,'vector_recall':False}),app.principal.tenant_id))
                await ask();assert len(await queue.client.hgetall(jobs))==1
                async with app.leases.lease() as guard:await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':False,'write_mode':'auto','retrieval_conditioning':False,'vector_recall':False}),app.principal.tenant_id))
                await ask();assert len(await queue.client.hgetall(jobs))==1
                assert not app.memory_hook_errors
            Path('docs/weknora-port/evidence/P1_SOURCE_PROVIDER_AUTO_QUEUE_20261014.json').write_text(json.dumps({'kind':'NATIVE_ASGI_ISOLATED_PG_REDIS_MOCK','root':str(root),'old_id':old_id,'new_id':new_id,'old_source':'deepseek','new_source':'remote','new_provider':'deepseek','legacy_hashes_preserved':True,'legacy_credential_update_same_id':True,'tenant_deleted_blocks_both':True,'auto_post_turn_job':job,'due_seconds':due-before,'replay_no_send_no_duplicate':True,'explicit_only_no_queue':True,'tenant_memory_disabled_no_queue':True,'schedule_called_manually':False,'real_provider_calls':0},indent=2),encoding='utf-8')
    actual_pg_run(run())

def test_fixed_source_field_and_factory_counterexamples():
    fixture=json.loads((Path(__file__).parent/'fixtures/source_provider_go_oracle.json').read_text(encoding='utf-8'))
    assert fixture['exit_code']==0
    for case,row in zip(fixture['cases'],fixture['rows']):
        if case is None:assert row is None;continue
        assert row['source']==case['Source'] and row['provider']==case['Parameters']['Provider']
        if case['Source']=='remote':
            role=ConfiguredRole('cheap',row['provider'],row['model'],case['Parameters']['BaseURL'],source='remote')
            config=SourceCatalogue().resolve(role,case['ID'])
            assert config.source==row['source'] and config.provider==row['provider']
        elif case['Source']=='local':assert row['dispatch'].startswith('{local ')
        else:assert 'unsupported chat model source' in row['error']

def test_legacy_vendor_named_remote_is_identified_by_v1_hash_not_enum_guess():
    from tests.weknora_port.test_model_store import store,row_for,ModelDB,ROLE
    async def run():
        db=ModelDB();repo=store(db);role=replace(ROLE,provider='remote',source='')
        row=row_for(repo,role);db.answers=[[row]]
        restored=await repo.resolve(row['id'])
        assert restored.source=='' and restored.provider=='remote'
        assert repo._identity(restored)[0]==row['id']
    asyncio.run(run())