"""Actual API -> retrieval -> generation -> JSONB -> HTTP/history source metadata."""
import asyncio,json,socket,uuid
from pathlib import Path
import httpx,uvicorn
from backend.weknora_port.http_api import create_app
from backend.weknora_port.storage.history import PostgresHistoryRepository
from backend.weknora_port.streaming.replay import FinalCommit
from backend.weknora_port.models.types import TrustedScope
from backend.weknora_port.conversation.types import MemoryScope
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_qa_followup_fallback_pg import REDIS_ID


def test_real_http_commit_and_history_preserve_source_offsets_metadata_and_legacy_missing():
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15,citations_enabled=True) as (runtime,wire,root):
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            origin=f'http://127.0.0.1:{port}'
            async def principal(_):return runtime.principal
            app=create_app(runtime,resolve_principal=principal,allowed_origins=(origin,))
            server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
            task=asyncio.create_task(server.serve())
            try:
                for _ in range(300):
                    if server.started:break
                    if task.done():await task
                    await asyncio.sleep(.01)
                assert server.started
                async with httpx.AsyncClient(base_url=origin,headers={'Origin':origin},timeout=30) as client:
                    kb=(await client.post('/api/v1/knowledge-bases',json={'name':'reference metadata'})).json()['data']['id']
                    await client.patch(f'/api/v1/knowledge-bases/{kb}/external-processing',json={'cloud_allowed':True})
                    source='Blue widgets use W-17. \u4e2d\U0001f600A.'
                    reply=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',files={'file':('source.txt',source.encode('utf-8'),'text/plain')})
                    assert reply.status_code==200 and reply.json()['data']['status']=='completed',reply.text
                    doc=reply.json()['data']['id']
                    async with runtime.leases.lease() as guard:
                        await guard.connection.execute('UPDATE knowledges SET description=%s WHERE id=%s AND tenant_id=%s',('Blue widgets use W-17.',doc,runtime.principal.tenant_id))
                        chunks=await (await guard.connection.execute("SELECT id,content,start_at,end_at FROM chunks WHERE knowledge_id=%s AND tenant_id=%s AND chunk_type='text' AND deleted_at IS NULL",(doc,runtime.principal.tenant_id))).fetchall()
                    indexed=await runtime._rows('SELECT content FROM embeddings WHERE chunk_id=%s AND knowledge_base_id=%s AND knowledge_id=%s',(chunks[0]['id'],kb,doc))
                    assert len(indexed)==1 and indexed[0]['content']=='source\n'+source
                    assert len(chunks)==1 and chunks[0]['content']==source
                    assert (chunks[0]['start_at'],chunks[0]['end_at'])==(0,len(source))
                    assert len(source.encode('utf-8'))!=len(source)
                    sid=(await client.post('/api/v1/sessions',json={'title':'references','knowledge_base_scope':[kb]})).json()['data']['id']
                    saved=[]
                    for question in ('extract source','What protocol do blue widgets use?'):
                        response=await client.post(f'/api/v1/sessions/{sid}/messages',json={'content':question,'request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb]})
                        assert response.status_code==200,response.text
                        await runtime.drain_background()
                        messages=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                        answers=[m for m in messages if m['role']=='assistant'];current=answers[-1]
                        assert current['is_completed'] and current['knowledge_references'],current
                        refs=current['knowledge_references'];ref=refs[0]
                        assert (ref['start_at'],ref['end_at'],ref['content'])==(0,len(source),source)
                        assert ref['knowledge_description']=='Blue widgets use W-17.'
                        assert ref['matched_content']==indexed[0]['content'] and ref['match_type'] in (0,1,3)
                        assert (await client.get('/api/v1/chunks/'+ref['id'])).status_code==200
                        raw=await runtime._rows('SELECT knowledge_references FROM messages WHERE id=%s AND tenant_id=%s',(current['id'],runtime.principal.tenant_id))
                        assert raw[0]['knowledge_references']==refs
                        saved.append({'message_id':current['id'],'references':refs})
                    messages=await runtime.list_messages(runtime.principal,sid)
                    assert next(m for m in messages if m['id']==saved[0]['message_id'])['knowledge_references']==saved[0]['references']
                    history=await runtime.memory_repository.recent_messages(MemoryScope(runtime.principal.tenant_id,runtime.principal.subject_id),sid,100)
                    for row in saved:assert next(m for m in history if m.id==row['message_id']).knowledge_references==tuple(row['references'])
                    # Existing legacy history is opaque JSON; absence must stay absent.
                    run_id,user_id,answer_id=[str(uuid.uuid4()) for _ in range(3)]
                    legacy=[{'id':chunks[0]['id'],'content':source,'knowledge_id':doc,'knowledge_base_id':kb}]
                    async with runtime.leases.lease() as guard:
                        repo=PostgresHistoryRepository(guard,runtime.principal)
                        await repo.create_turn(sid,run_id,user_id,answer_id,'legacy history',TrustedScope(runtime.principal.subject_id,(kb,)))
                        await repo.commit_final(FinalCommit(sid,answer_id,run_id,'legacy answer',legacy))
                    old=next(m for m in await runtime.list_messages(runtime.principal,sid) if m['id']==answer_id)
                    assert old['knowledge_references']==legacy and 'start_at' not in old['knowledge_references'][0]
                    old_history=await runtime.memory_repository.recent_messages(MemoryScope(runtime.principal.tenant_id,runtime.principal.subject_id),sid,100)
                    assert next(m for m in old_history if m.id==answer_id).knowledge_references==tuple(legacy)
                    Path('docs/weknora-port/evidence/REFERENCE_METADATA_20261015/http.'+uuid.uuid4().hex[:10]+'.json').write_text(json.dumps({'kind':'ACTUAL_HTTP_PG_REDIS_MOCK_MODEL','source':source,'source_chunk':chunks[0],'turns':saved,'legacy_missing_preserved':True,'model_wire_count':len(wire),'real_provider_calls':0},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            finally:server.should_exit=True;await task
    actual_pg_run(scenario())
