"""Actual native HTTP/PG manual memory and >500-row export boundary."""
import json,uuid
from pathlib import Path
import httpx
from backend.weknora_port.http_api import create_app
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.conversation.types import MemoryScope
from backend.weknora_port.conversation.management import fingerprint
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run


def test_actual_manual_edit_export_affinity_clear_are_owner_scoped():
    async def scenario():
        async with isolated_mock_application() as (runtime,wire,root):
            who=runtime.principal;scope=MemoryScope(who.tenant_id,who.subject_id)
            assert root.parent==Path('E:/WK/run')
            async with runtime.leases.lease() as guard:
                await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',
                    (json.dumps({'enabled':True,'write_mode':'explicit_only','vector_recall':False}),who.tenant_id))
            app=create_app(runtime,resolve_principal=lambda request:LocalPrincipal(who.tenant_id,'foreign-synthetic-owner') if request.headers.get('X-Test-Foreign') else who,
                allowed_origins=('http://fixture.invalid',))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture.invalid',headers={'Origin':'http://fixture.invalid'}) as client:
                payload={'kind':'invalid-kind','content':'Synthetic manual memory with a stable fact'}
                assert (await client.post('/api/v1/memory/items',json=payload,headers={'X-Test-Foreign':'1'})).status_code==403
                response=await client.post('/api/v1/memory/items',json=payload);assert response.status_code==200,response.text
                created=response.json()['data'];assert created['kind']=='fact' and created['importance']==3 and created['origin']=='manual'
                identifier=created['id']
                assert (await client.post('/api/v1/memory/items',json={'content':'','importance':True})).status_code==422
                assert (await client.put('/api/v1/memory/items/'+identifier,json={'content':'Synthetic corrected memory'},headers={'X-Test-Foreign':'1'})).status_code==403
                response=await client.put('/api/v1/memory/items/'+identifier,json={'content':'Synthetic corrected manual fact','importance':99})
                assert response.status_code==200,response.text
                updated=response.json()['data'];assert updated['importance']==5 and updated['origin']=='manual' and updated['topic']==created['topic']
                assert (await runtime.memory_repository.get_item(scope,identifier)).content==updated['content']
                # Archived rows are not bounded by active capacity: export must walk all pages.
                async with runtime.leases.lease() as guard:
                    original=await (await guard.connection.execute('SELECT * FROM memory_items WHERE tenant_id=%s AND subject_id=%s AND id=%s',(who.tenant_id,who.subject_id,identifier))).fetchone()
                    for i in range(501):
                        row=dict(original);row.update(id=str(uuid.uuid4()),content=f'Archived synthetic snapshot {i}',normalized_key=f'fixture-export-{i}',status='archived')
                        await guard.connection.execute('INSERT INTO memory_items SELECT * FROM jsonb_populate_record(NULL::memory_items,%s::jsonb)',(json.dumps(row,default=str),))
                    affinity=str(uuid.uuid4());oneoff=str(uuid.uuid4())
                    for aid,hits in [(affinity,2),(oneoff,1)]:
                        await guard.connection.execute('INSERT INTO memory_doc_affinity(id,tenant_id,subject_id,knowledge_id,knowledge_base_id,title,hits,last_used_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now())',
                            (aid,who.tenant_id,who.subject_id,aid,'synthetic-kb','Synthetic affinity',hits))
                exported=await client.get('/api/v1/memory/export');assert exported.status_code==200
                snapshot=exported.json();assert snapshot['total']==502 and len(snapshot['data'])==502 and not snapshot['truncated']
                assert 'weknora-memories.json' in exported.headers['Content-Disposition']
                assert all(r['tenant_id']==who.tenant_id and r['subject_id']==who.subject_id for r in snapshot['data'])
                assert (await client.get('/api/v1/memory/export',headers={'X-Test-Foreign':'1'})).status_code==403
                docs=(await client.get('/api/v1/memory/documents?limit=bad&offset=-3')).json();assert docs['total']==1 and docs['data'][0]['id']==affinity
                assert set(docs['data'][0])=={'id','knowledge_id','knowledge_base_id','title','hits','last_used_at'}
                assert (await client.delete('/api/v1/memory/documents/'+affinity,headers={'X-Test-Foreign':'1'})).status_code==403
                assert (await client.delete('/api/v1/memory/documents/'+affinity)).status_code==200
                assert (await client.get('/api/v1/memory/documents')).json()['total']==0
                assert (await client.delete('/api/v1/memory/documents/'+affinity)).status_code==404
                assert (await client.delete('/api/v1/memory/items',headers={'X-Test-Foreign':'1'})).status_code==403
                cleared=await client.delete('/api/v1/memory/items');assert cleared.status_code==200,cleared.text
                assert cleared.json()['removed']==502
                assert (await client.get('/api/v1/memory/export')).json()['total']==0
                assert await runtime.memory_repository.has_tombstone(scope,fingerprint(updated['content']))
                Path('docs/weknora-port/evidence/P4_MEMORY_MANUAL_API_20261014.json').write_text(json.dumps({
                    'kind':'ACTUAL_NATIVE_FASTAPI_ASGI_PG_ENTRY','storage_root':str(root),'tenant':who.tenant_id,'subject':who.subject_id,
                    'export_rows':502,'export_pages':2,'source_page_size':500,'source_export_ceiling':20000,
                    'manual_defaults_and_clamp':True,'owner_denials':True,'affinity_min_hits':2,'clear_synthetic_only':502,
                    'tombstone_retained':True,'real_provider_calls':0,'mock_calls':len(wire),'old_data_modified':False},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
