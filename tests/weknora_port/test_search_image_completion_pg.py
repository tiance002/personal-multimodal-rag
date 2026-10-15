"""Actual ASGI upload/chat/reference + PG full hybrid; closed mock models only."""
import os,io,json,uuid,asyncio,re
from pathlib import Path
import httpx,pytest
from PIL import Image
from backend.weknora_port.http_api import create_app
from backend.weknora_port.models.types import CallContext,TrustedScope
from backend.weknora_port.retrieval.search import SearchParams,SearchTarget,RetrievalScopeError
from backend.weknora_port.storage.hybrid import PostgresKnowledgeRepository
from backend.weknora_port.generation import Evidence,RouterPolicy
from backend.weknora_port.runtime import ConversationSettings
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import stream_response
from scripts.weknora_port_six_mock import prepare_public_fixture


def test_actual_complete_search_context_and_http_reference_readback():
    redis_id=os.environ['WEKNORA_SEQ_IMAGE_REDIS_ID']
    chosen_handles=[]
    def select_original_reference(request,body):
        if body.get('stream') and request.url.path.endswith('/chat/completions'):
            prompt='\n'.join(str(m.get('content','')) for m in body.get('messages',[]))
            match=re.search(r'<chunk id="([^"]+)" index="7"',prompt)
            assert match,'Target original text must be present in actual model Context'
            chosen_handles.append(match[1])
            return stream_response(body['model'],'Blue widgets use W-17. <ref id="'+match[1]+'"/>','stop')
        return None
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=redis_id,redis_db=15,
                answer_stream_factory=lambda model:stream_response(model,'Blue widgets use W-17. <ref id="c1"/>','stop'),response_handler=select_original_reference) as (runtime,wire,root):
            runtime.policy=RouterPolicy(mode='CHEAP_ONLY')
            runtime.conversation=ConversationSettings(max_rounds=0,enable_rewrite=False,enable_query_expansion=False,enable_rerank=False)
            origin='http://127.0.0.1:56426'
            async def principal(_):return runtime.principal
            api=create_app(runtime,resolve_principal=principal,allowed_origins=(origin,))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url=origin,headers={'Origin':origin},timeout=60) as client:
                response=await client.post('/api/v1/knowledge-bases',json={'name':'seq/image complete search fixture'})
                assert response.status_code==200,response.text
                kb=response.json()['data']['id'];await prepare_public_fixture(runtime,kb,'seq-image')
                documents=[]
                for i,color in enumerate(('white','blue')):
                    buffer=io.BytesIO();Image.new('RGB',(48,48),color).save(buffer,format='PNG')
                    response=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',files={'file':(f'scan-{i}.png',buffer.getvalue(),'image/png')})
                    assert response.status_code==200,response.text
                    doc=response.json()['data']
                    assert doc['status'] in ('pending','completed'),doc
                    if doc['status']=='pending':
                        # HTTP background processing may already hold the task.
                        # Wait for persisted terminal state, not a second claim result.
                        await runtime.image_tasks.start()
                        for _ in range(200):
                            tasks=await runtime._rows('SELECT state,error_code FROM port_image_tasks WHERE tenant_id=%s AND knowledge_id=%s',(runtime.principal.tenant_id,doc['id']))
                            if tasks[0]['state'] in ('completed','failed','unknown','cancelled','orphaned'):break
                            await asyncio.sleep(.05)
                        assert tasks[0]['state']=='completed',tasks
                    documents.append(doc['id'])
                    assert (await runtime.read_document(runtime.principal,kb,doc['id'])).content==buffer.getvalue()
                tenant=runtime.principal.tenant_id;owner=runtime.principal.subject_id
                context=CallContext('seq-image-pg',TrustedScope(owner,(kb,),tuple(documents)))
                target=SearchTarget(kb,'knowledge',tuple(documents))
                async def resolve(request_owner):
                    if request_owner!=owner:raise PermissionError('fixture server identity')
                    return tenant
                async with runtime.leases.lease() as guard:
                    chunks=await (await guard.connection.execute('SELECT id,knowledge_id,chunk_type,chunk_index,content,parent_chunk_id,image_info,metadata FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s AND knowledge_id=ANY(%s) AND deleted_at IS NULL ORDER BY knowledge_id,id',(tenant,kb,documents))).fetchall()
                    children=[c for c in chunks if c['chunk_type'] in ('image_ocr','image_caption')]
                    assert len(children)==4,children
                    parents={c['parent_chunk_id']:next(p for p in chunks if p['id']==c['parent_chunk_id']) for c in children}
                    assert len(parents)==2 and all(p['chunk_type']=='text' for p in parents.values()),parents
                    before={c['id']:c['image_info'] for c in children}
                    assert all(not p['image_info'] for p in parents.values())
                    for p in parents.values():
                        index=7 if p['knowledge_id']==documents[0] else 0
                        await guard.connection.execute('UPDATE chunks SET chunk_index=%s WHERE id=%s AND tenant_id=%s',(index,p['id'],tenant));p['chunk_index']=index
                    # Fixture candidate set: searchable original text; enabled OCR/caption
                    # source children stay readable for completion but their index hits are off.
                    await guard.connection.execute('UPDATE embeddings SET is_enabled=false WHERE knowledge_base_id=%s AND knowledge_id=ANY(%s) AND chunk_id=ANY(%s)',(kb,documents,list(before)))
                    repo=PostgresKnowledgeRepository(guard,resolve)
                    results=await repo.hybrid_search((target,),SearchParams('scan protocol',disable_keywords=True,skip_context_enrichment=True),context,(.1,.2,.3))
                    selected={r.id:r for r in results}
                    assert set(parents)<=set(selected),[r.id for r in results]
                    checked=[]
                    for id,p in parents.items():
                        row=selected[id];info=json.loads(row.image_info)
                        assert len(info)==1 and set(info[0])=={'url','original_url','start_pos','end_pos','caption','ocr_text'}
                        assert info[0]['caption'] and info[0]['ocr_text']
                        assert row.seq==row.chunk_index==p['chunk_index']
                        assert row.content==p['content']
                        evidence=Evidence.from_result(row)
                        assert info[0]['caption'] in evidence.content and info[0]['ocr_text'] in evidence.content
                        assert evidence.source_content==p['content'] and evidence.seq==row.seq
                        checked.append({'id':id,'document':row.knowledge_id,'seq':row.seq,'image_info':info,'context':evidence.content,'source_content':evidence.source_content})
                    read=await repo.get_chunks(tuple(before),context)
                    assert {id:r.image_info for id,r in read.items()}==before
                    assert all(r.seq==r.chunk_index==0 for r in read.values())
                    # Real SQL and collector reject an allowed-document child attached
                    # to a parent from a different version. Restore this fresh fixture row.
                    child=children[0];wrong={**child['metadata'],'source_version':'different-fixture-version'}
                    await guard.connection.execute('UPDATE chunks SET metadata=%s::jsonb WHERE id=%s AND tenant_id=%s',(json.dumps(wrong),child['id'],tenant))
                    try:
                        with pytest.raises(RetrievalScopeError):
                            await repo.hybrid_search((target,),SearchParams('scan',disable_keywords=True,skip_context_enrichment=True),context,(.1,.2,.3))
                    finally:
                        await guard.connection.execute('UPDATE chunks SET metadata=%s::jsonb WHERE id=%s AND tenant_id=%s',(json.dumps(child['metadata']),child['id'],tenant))
                sid=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[documents[0]]})).json()['data']['id']
                response=await client.post(f'/api/v1/sessions/{sid}/messages',json={'content':'What protocol do blue widgets use?','request_id':str(uuid.uuid4()),'knowledge_base_scope':[kb],'document_scope':[documents[0]]})
                assert response.status_code==200,response.text
                await runtime.drain_background()
                messages=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                answer=next(m for m in messages if m['role']=='assistant')
                assert answer['is_completed'] and answer['knowledge_references'],answer
                ref=answer['knowledge_references'][0]
                expected=next(p for p in parents.values() if p['knowledge_id']==documents[0])
                assert ref['id']==expected['id'] and ref['seq']==ref['chunk_index']==7,ref
                assert ref['content']==expected['content']
                assert json.loads(ref['image_info'])==json.loads(selected[ref['id']].image_info)
                source=await client.get('/api/v1/chunks/'+ref['id']);assert source.status_code==200,source.text
                assert source.json()['data']['text']==expected['content']
                async with runtime.leases.lease() as guard:
                    saved=await (await guard.connection.execute('SELECT knowledge_references FROM messages WHERE tenant_id=%s AND id=%s',(tenant,answer['id']))).fetchone()
                assert saved['knowledge_references']==answer['knowledge_references']
                history=(await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                assert next(m for m in history if m['id']==answer['id'])['knowledge_references']==saved['knowledge_references']
                prompts=[m['content'] for call in wire if call['path'].endswith('/chat/completions') for m in call['body'].get('messages',[]) if isinstance(m.get('content'),str)]
                expected_info=json.loads(ref['image_info'])[0]
                assert any(expected_info['ocr_text'] in p and expected_info['caption'] in p for p in prompts)
                receipt={'kind':'ACTUAL_ASGI_HTTP_PG_REDIS_CLOSED_MOCK_MODELS','root':str(root),'tenant':tenant,'documents':documents,'complete_search':checked,'child_image_info_exact':before,'child_strings_unchanged':True,'version_mismatch_rejected':True,'references':answer['knowledge_references'],'reference_http_status':source.status_code,'jsonb_and_history_equal':True,'context_visible_to_mock_model':True,'selected_actual_context_handles':chosen_handles,'mock_wire_count':len(wire),'real_provider_calls':0,'ranking_ties':'UNDEFINED_FUSION_TIE_ORDER; no comparison of tied order'}
                Path(os.environ['WEKNORA_SEQ_IMAGE_RECEIPT']).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    actual_pg_run(scenario())
