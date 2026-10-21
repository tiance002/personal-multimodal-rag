"""Actual isolated upload/index/QA for source's configured and absent summary-model branches."""
import asyncio,json,uuid
from pathlib import Path
import httpx,pytest
from backend.weknora_port.http_api import create_app
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_qa_followup_fallback_pg import REDIS_ID

@pytest.mark.parametrize('configured',[False,True])
def test_source_summary_configuration_terminal_state_and_actual_qa(configured):
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15) as (runtime,wire,root):
            origin='http://127.0.0.1:19993'
            async def principal(_):return runtime.principal
            api=create_app(runtime,resolve_principal=principal,allowed_origins=(origin,))
            async with api.router.lifespan_context(api):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url=origin,headers={'Origin':origin},timeout=30) as client:
                    kb=(await client.post('/api/v1/knowledge-bases',json={'name':'summary branch fixture'})).json()['data']['id']
                    await client.patch(f'/api/v1/knowledge-bases/{kb}/external-processing',json={'cloud_allowed':True})
                    if not configured:
                        async with runtime.leases.lease() as guard:
                            await guard.connection.execute("UPDATE knowledge_bases SET summary_model_id='' WHERE id=%s AND tenant_id=%s",(kb,runtime.principal.tenant_id))
                    before=len(wire)
                    source='The source handbook says blue widgets use protocol W-17. \u4e2d\U0001f600A.'
                    reply=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',files={'file':('summary-case.txt',source.encode('utf-8'),'text/plain')})
                    assert reply.status_code==200 and reply.json()['data']['status']=='completed',reply.text
                    did=reply.json()['data']['id']
                    rows=await runtime._rows('SELECT parse_status,summary_status,error_message FROM knowledges WHERE id=%s AND tenant_id=%s',(did,runtime.principal.tenant_id))
                    assert rows==[{'parse_status':'completed','summary_status':'completed' if configured else 'failed','error_message':''}],rows
                    summaries=await runtime._rows("SELECT id,parent_chunk_id,content FROM chunks WHERE knowledge_id=%s AND tenant_id=%s AND chunk_type='summary' AND deleted_at IS NULL",(did,runtime.principal.tenant_id))
                    assert len(summaries)==int(configured)
                    summaries_wire=[item for item in wire[before:] if item['path'].endswith('/chat/completions')]
                    assert len(summaries_wire)==int(configured),summaries_wire
                    indexed=await runtime._rows('SELECT chunk_id,is_enabled FROM embeddings WHERE knowledge_id=%s AND knowledge_base_id=%s',(did,kb))
                    assert indexed and all(row['is_enabled'] for row in indexed),indexed
                    sid=(await client.post('/api/v1/sessions',json={'title':'summary branch','knowledge_base_scope':[kb]})).json()['data']['id']
                    response=await client.post(f'/api/v1/sessions/{sid}/messages',json={'content':'extract source','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb]})
                    assert response.status_code==200,response.text
                    await runtime.drain_background()
                    messages=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                    answer=next(m for m in messages if m['role']=='assistant')
                    assert answer['is_completed'] and answer['knowledge_references'],answer
                    ref=answer['knowledge_references'][0]
                    assert ref['content']==source and (ref['start_at'],ref['end_at'])==(0,len(source))
                    assert (await client.get('/api/v1/chunks/'+ref['id'])).status_code==200
                    Path('docs/weknora-port/evidence/SUMMARY_CONFIGURATION_20261015/'+('configured' if configured else 'absent')+'.'+uuid.uuid4().hex[:8]+'.json').write_text(json.dumps({'kind':'ACTUAL_ASGI_PG_REDIS_MOCK_MODEL','configured':configured,'document':rows[0],'summary_chunks':summaries,'upload_chat_calls':len(summaries_wire),'indexed':indexed,'assistant':answer,'real_provider_calls':0},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    actual_pg_run(scenario())
