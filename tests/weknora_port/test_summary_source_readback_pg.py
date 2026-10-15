"""Real isolated SQL + ASGI citation readback; provider transport is closed mock."""
import json
import os
import uuid
from pathlib import Path

import httpx

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.http_api import create_app
from backend.weknora_port.runtime import ConversationSettings
from scripts.weknora_port_six_mock import prepare_public_fixture
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import stream_response


def test_actual_generated_summary_citation_and_parent_version_boundaries():
    async def scenario():
        async with isolated_mock_application(expected_redis_run_id=os.environ['WEKNORA_SEQ_IMAGE_REDIS_ID'],
                redis_db=15, answer_stream_factory=lambda model: stream_response(
                    model, 'Blue widgets use W-17. <ref id="c1"/>', 'stop')) as (runtime, wire, root):
            runtime.policy = RouterPolicy(mode='CHEAP_ONLY')
            runtime.conversation = ConversationSettings(max_rounds=0, enable_rewrite=False,
                enable_query_expansion=False, enable_rerank=False)
            origin = 'http://127.0.0.1:56426'
            async def principal(_): return runtime.principal
            api = create_app(runtime, resolve_principal=principal, allowed_origins=(origin,))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url=origin,
                    headers={'Origin': origin}, timeout=60) as client:
                kb = (await client.post('/api/v1/knowledge-bases', json={'name': 'summary source regression'})).json()['data']['id']
                await prepare_public_fixture(runtime, kb, 'summary-readback')
                uploaded = await client.post(f'/api/v1/knowledge-bases/{kb}/documents',
                    files={'file': ('protocol.txt', b'Blue widgets use protocol W-17. Follow the blue widget handbook.', 'text/plain')})
                assert uploaded.status_code == 200, uploaded.text
                doc = uploaded.json()['data']; assert doc['status'] == 'completed', doc
                tenant = runtime.principal.tenant_id
                async def rows():
                    return await runtime._rows('SELECT id,tenant_id,knowledge_base_id,knowledge_id,chunk_type,parent_chunk_id,metadata,content,deleted_at FROM chunks WHERE tenant_id=%s AND knowledge_id=%s ORDER BY id', (tenant, doc['id']))
                before = await rows()
                summary = next(r for r in before if r['chunk_type'] == 'summary')
                parent = next(r for r in before if r['id'] == summary['parent_chunk_id'])
                assert parent['chunk_type'] == 'text' and 'source_version' not in summary['metadata']
                version = parent['metadata']['source_version']
                source = await client.get('/api/v1/chunks/' + summary['id'])
                assert source.status_code == 200, source.text
                assert source.json()['data']['text'] == summary['content']
                assert source.json()['data']['source_version'] == version
                checks = []
                async def reject(label, sql, changed, restore, target=None):
                    # Autocommit only fresh fixture rows; restore even on assertion failure.
                    async with runtime.leases.lease() as guard:
                        await guard.connection.execute(sql, changed)
                    try:
                        response = await client.get('/api/v1/chunks/' + (target or summary['id']))
                        assert response.status_code == 404, (label, response.text)
                        checks.append({'case': label, 'status': response.status_code})
                    finally:
                        async with runtime.leases.lease() as guard:
                            await guard.connection.execute(sql, restore)
                for field, value, original in (
                    ('parent_chunk_id', str(uuid.uuid4()), summary['parent_chunk_id']),
                    ('parent_chunk_id', summary['id'], summary['parent_chunk_id'])):
                    await reject('missing-parent' if value != summary['id'] else 'self-parent',
                        'UPDATE chunks SET parent_chunk_id=%s WHERE id=%s AND tenant_id=%s',
                        (value, summary['id'], tenant), (original, summary['id'], tenant))
                for field, value in (('tenant_id', tenant + 1000000),
                        ('knowledge_base_id', str(uuid.uuid4())), ('knowledge_id', str(uuid.uuid4())),
                        ('chunk_type', 'summary'), ('deleted_at', '2026-10-15T00:00:00Z')):
                    # Field names are this fixed test tuple, never runtime/request input.
                    sql = f'UPDATE chunks SET {field}=%s WHERE id=%s'
                    await reject('parent-' + field, sql, (value, parent['id']), (parent[field], parent['id']))
                sql = 'UPDATE chunks SET metadata=%s::json WHERE id=%s AND tenant_id=%s'
                await reject('old-parent-version', sql,
                    (json.dumps({**parent['metadata'], 'source_version': 'old-fixture-version'}), parent['id'], tenant),
                    (json.dumps(parent['metadata']), parent['id'], tenant))
                await reject('ordinary-missing-version', sql, ('{}', parent['id'], tenant),
                    (json.dumps(parent['metadata']), parent['id'], tenant), target=parent['id'])
                await reject('explicit-wrong-summary-version', sql,
                    (json.dumps({**summary['metadata'], 'source_version': 'old-fixture-version'}), summary['id'], tenant),
                    (json.dumps(summary['metadata']), summary['id'], tenant))
                for status in ('pending', 'processing', 'failed'):
                    await reject('document-' + status,
                        'UPDATE knowledges SET parse_status=%s WHERE id=%s AND tenant_id=%s',
                        (status, doc['id'], tenant), ('completed', doc['id'], tenant))
                assert await rows() == before
                # Force an actual searchable summary citation, not a helper-created reference.
                async with runtime.leases.lease() as guard:
                    await guard.connection.execute('UPDATE embeddings SET is_enabled=false WHERE knowledge_base_id=%s AND knowledge_id=%s AND chunk_id<>%s', (kb, doc['id'], summary['id']))
                sid = (await client.post('/api/v1/sessions', json={'knowledge_base_scope': [kb], 'document_scope': [doc['id']]})).json()['data']['id']
                response = await client.post(f'/api/v1/sessions/{sid}/messages', json={
                    'content': 'What protocol do blue widgets use?', 'request_id': str(uuid.uuid4()),
                    'knowledge_base_scope': [kb], 'document_scope': [doc['id']]})
                assert response.status_code == 200, response.text
                await runtime.drain_background()
                messages = (await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                answer = next(m for m in messages if m['role'] == 'assistant')
                assert answer['is_completed'] and answer['knowledge_references'], answer
                ref = answer['knowledge_references'][0]
                assert ref['id'] == summary['id'] and ref['content'] == summary['content'], ref
                source = await client.get('/api/v1/chunks/' + ref['id'])
                assert source.status_code == 200 and source.json()['data']['text'] == summary['content']
                assert await rows() == before
                receipt = dict(kind='ACTUAL_ASGI_PG_REDIS_CLOSED_MOCK_MODELS', root=str(root), tenant=tenant,
                    document=doc['id'], summary=summary, parent=parent, verified_version=version,
                    negatives=checks, reference=ref, source=source.json()['data'],
                    chunk_rows_restored=True, real_provider_calls=0, mock_wire_count=len(wire))
                Path(os.environ['WEKNORA_SEQ_IMAGE_RECEIPT']).write_text(json.dumps(receipt, ensure_ascii=False,
                    indent=2, default=str) + '\n', encoding='utf-8')
    actual_pg_run(scenario())
