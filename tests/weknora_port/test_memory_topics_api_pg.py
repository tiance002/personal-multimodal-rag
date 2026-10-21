"""Actual source-compatible scoped topic manager API; no model needed."""
import json,uuid
import httpx
from pathlib import Path
from backend.weknora_port.http_api import create_app
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.conversation.types import MemoryScope
from backend.weknora_port.conversation.management import fingerprint
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run


def test_actual_topic_view_paging_promotion_deletion_and_scope():
    async def scenario():
        async with isolated_mock_application() as (runtime,wire,root):
            who=runtime.principal;scope=MemoryScope(who.tenant_id,who.subject_id)
            async with runtime.leases.lease() as guard:
                await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',(json.dumps({'enabled':True,'interest_threshold':50}),who.tenant_id))
            first=await runtime.memory_repository.bump_topic(scope,'Synthetic first topic','first','First alias','seed-first')
            second=await runtime.memory_repository.bump_topic(scope,'Synthetic second topic','second','Second alias','seed-second')
            app=create_app(runtime,resolve_principal=lambda request:LocalPrincipal(who.tenant_id,'foreign-synthetic-owner') if request.headers.get('X-Test-Foreign') else who,allowed_origins=('http://fixture.invalid',))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture.invalid',headers={'Origin':'http://fixture.invalid'}) as client:
                before=len(wire);listed=await client.get('/api/v1/memory/topics?limit=1&offset=0');assert listed.status_code==200
                payload=listed.json();assert payload['total']==2 and len(payload['data'])==1
                assert all(r['threshold']==20 and r['last_seen_at'] and 'tenant_id' not in r and 'subject_id' not in r for r in payload['data'])
                assert (await client.get('/api/v1/memory/topics?limit=bad&offset=-5')).json()['total']==2
                assert (await client.post('/api/v1/memory/topics/'+first.id+'/promote',headers={'X-Test-Foreign':'1'})).status_code==403
                promoted=await client.post('/api/v1/memory/topics/'+first.id+'/promote');assert promoted.status_code==200 and promoted.json()['data']['kind']=='interest'
                assert (await client.get('/api/v1/memory/topics')).json()['total']==1
                assert (await client.post('/api/v1/memory/topics/'+first.id+'/promote')).status_code==404
                assert (await client.delete('/api/v1/memory/topics/'+second.id,headers={'X-Test-Foreign':'1'})).status_code==403
                assert (await client.delete('/api/v1/memory/topics/'+second.id)).status_code==200
                assert (await client.get('/api/v1/memory/topics')).json()['total']==0
                assert await runtime.memory_repository.has_tombstone(scope,fingerprint(second.topic))
                assert await runtime.memory_repository.has_tombstone(scope,fingerprint('Second alias'))
                failing=await runtime.memory_repository.bump_topic(scope,'Synthetic warning topic','warning','','seed-warning')
                original_mark=runtime.memory_repository.mark_topic_promoted
                async def failed_mark(*args):raise RuntimeError('synthetic mark failure')
                runtime.memory_repository.mark_topic_promoted=failed_mark
                warning=await client.post('/api/v1/memory/topics/'+failing.id+'/promote')
                runtime.memory_repository.mark_topic_promoted=original_mark
                assert warning.status_code==200 and warning.json()['data']['kind']=='interest'
                assert (await runtime.memory_repository.get_item(scope,warning.json()['data']['id'])).status=='active'
                assert (await runtime.memory_repository.topic_by_id(scope,failing.id)).promoted_at is None
                assert len(wire)==before
                Path('docs/weknora-port/evidence/P4_MEMORY_TOPICS_MARK_FAILURE_20261014.json').write_text(json.dumps({'kind':'ACTUAL_FASTAPI_ASGI_PG_TOPIC_MANAGER','tenant':who.tenant_id,'subject':who.subject_id,'promoted':first.id,'deleted':second.id,'threshold_clamped20':True,'no_owner_ids_in_topic_view':True,'foreign_operations_denied':True,'canonical_alias_tombstones':True,'mark_failure_preserves_created_interest':True,'model_sends':0,'real_provider_calls':0},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
