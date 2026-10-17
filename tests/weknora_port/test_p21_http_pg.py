"""Normal HTTP + actual isolated PG/Redis; deterministic model transport only."""
import hashlib
import json
import os
from pathlib import Path

import httpx

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.service import KNOWLEDGE_EVIDENCE_INSUFFICIENT
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.redis_fixture_identity import expected_redis_run_id
from tests.weknora_port.test_memory_storage_pg import actual_pg_run, approved_dsn, connection
from tests.weknora_port.test_qa_followup_fallback_pg import http_client, kb, session, ask, frames
from tests.weknora_port.test_runtime_pg import SOURCE


def test_normal_http_citations_off_retrieval_refusal_history_and_gates():
    async def scenario():
        approved_dsn()
        conn, _ = await connection(approved_dsn())
        async def owner_ledger_digest():
            data = {}
            for table in ('port_token_calls', 'port_token_budget_months'):
                rows = await (await conn.execute(
                    f"SELECT to_jsonb(t) AS row FROM {table} t WHERE account_id IN ('siliconflow-owner-main','deepseek-owner-main') ORDER BY to_jsonb(t)::text")).fetchall()
                data[table] = rows
            return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
        before = await owner_ledger_digest()
        def response(request, body):
            if body.get('stream'):
                assert 'Source citations are disabled' in body['messages'][0]['content']
                parts = ['Blue widgets use W-17. <re', 'f><ref id="c1"/>', '</ref> [manual](https://example.test) ![figure](https://example.test/i.png)']
                lines = [{'model': body['model'], 'choices': [{'delta': {'content': p}, 'finish_reason': None}]} for p in parts]
                lines.append({'model': body['model'], 'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                              'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}})
                return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                    content=''.join('data: '+json.dumps(line)+'\n\n' for line in lines)+'data: [DONE]\n\n')
            user = body.get('messages', [{}])[-1].get('content', '')
            if isinstance(user, str) and '## JSON Output' in user:
                if 'utc时间转换北京时间' in user: intent = 'general_qa'
                elif '你好' in user: intent = 'greeting'
                elif '上面第二点' in user: intent = 'follow_up'
                elif '知识库' in user: intent = 'chitchat'  # deliberate wrong classification
                else: intent = 'kb_search'
                return httpx.Response(200, json={'model': body['model'], 'choices': [{'message': {
                    'content': json.dumps({'rewrite_query': 'blue widgets protocol', 'intent': intent, 'image_description': ''})},
                    'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}})
        try:
            async with isolated_mock_application(expected_redis_run_id=expected_redis_run_id(), redis_db=15,
                                                 response_handler=response) as (runtime, wire, root):
                runtime.policy = RouterPolicy(mode='CHEAP_ONLY')
                checks = []
                async with http_client(runtime) as client:
                    kbid = await kb(client, 'P21 bounded synthetic sources')
                    upload = await client.post(f'/api/v1/knowledge-bases/{kbid}/documents',
                        files={'file': ('p21.txt', SOURCE, 'text/plain')})
                    assert upload.status_code == 200 and upload.json()['data']['status'] == 'completed'
                    sid = await session(client, kbid)
                    start = len(wire)
                    reply, answer, req = await ask(client, runtime, sid, kbid, '请依据知识库回答蓝色部件的协议')
                    actual = wire[start:]
                    generations = [r['body'] for r in actual if r['body'].get('stream')]
                    assert len(generations) == 1 and SOURCE in json.dumps(generations[0])
                    chunks = await runtime._rows("SELECT id FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s AND chunk_type='text' AND deleted_at IS NULL",
                                                 (runtime.principal.tenant_id, kbid))
                    assert chunks
                    readback = await client.get('/api/v1/chunks/'+chunks[0]['id'])
                    assert readback.status_code == 200
                    source = readback.json()['data']
                    assert source['text'] == SOURCE and source['source_version']
                    assert source['knowledge_base_id'] == kbid and source['document_id'] == upload.json()['data']['id']
                    assert any(r['path'].endswith('/embeddings') for r in actual)
                    assert any(r['path'].endswith('/rerank') for r in actual)
                    assert answer['is_completed'] and not answer['turn_error_code'] and not answer['knowledge_references']
                    assert '<ref' not in reply.text and '</ref' not in reply.text and '<kb ' not in reply.text
                    assert '<ref' not in answer['content'] and '[manual](https://example.test)' in answer['content']
                    fresh = (await client.get(f'/api/v1/sessions/{sid}/messages')).json()['data']
                    assert next(m for m in fresh if m['id'] == answer['id'])['content'] == answer['content']
                    replay_start = len(wire)
                    replay = await client.post(f'/api/v1/sessions/{sid}/messages', json=req)
                    assert replay.status_code == 200 and len(wire) == replay_start
                    checks.append('wrong_chitchat_explicit_kb_retrieves_context_and_clean_sse_history_replay')
                    for question in ('你好', '上面第二点展开讲讲'):
                        start = len(wire)
                        _, follow, _ = await ask(client, runtime, sid, kbid, question)
                        sent = wire[start:]
                        assert not any(r['path'].endswith(('/embeddings', '/rerank')) for r in sent)
                        final = [r['body'] for r in sent if r['body'].get('stream')]
                        assert len(final) == 1 and follow['is_completed']
                        if '上面' in question:
                            assert any(m['role'] == 'assistant' and 'W-17' in m['content'] for m in final[0]['messages'])
                    checks.append('greeting_and_history_followup_no_retrieval_with_history_to_provider')
                    empty = await kb(client, 'P21 empty evidence')
                    empty_sid = await session(client, empty)
                    start = len(wire)
                    refusal, message, _ = await ask(client, runtime, empty_sid, empty, 'utc时间转换北京时间')
                    assert message['content'] == KNOWLEDGE_EVIDENCE_INSUFFICIENT
                    assert not any(r['body'].get('stream') for r in wire[start:])
                    assert any(r['path'].endswith('/embeddings') for r in wire[start:])
                    assert frames(refusal)[-1]['response_type'] == 'complete'
                    checks.append('unknown_utc_retrieves_and_empty_evidence_fixed_refusal_no_generation')
                    before_scope = len(wire)
                    denied = await client.post(f'/api/v1/sessions/{sid}/messages', json={**req,
                        'request_id': 'p21-cross-kb', 'knowledge_base_scope': [empty]})
                    assert denied.status_code == 403 and len(wire) == before_scope
                    await client.patch(f'/api/v1/knowledge-bases/{empty}/external-processing', json={'cloud_allowed': False})
                    private_sid = await session(client, empty)
                    start = len(wire)
                    _, private, _ = await ask(client, runtime, private_sid, empty, 'utc时间转换北京时间')
                    assert private['turn_error_code'] and len(wire) == start
                    checks.append('scope_and_egress_denials_prevent_model_transport')
                    ledger = await runtime._rows('SELECT state,usage FROM send_calls WHERE account_id=%s ORDER BY created_at',
                                                 (runtime.costs.authority.account_id,))
                    assert any(r['state'] == 'settled' and r['usage'] for r in ledger)
                    assert await owner_ledger_digest() == before
                    report = {'kind': 'actual-http-pg-redis-mock-model-transport', 'real_cloud_calls': 0,
                              'checks': checks, 'tenant': runtime.principal.tenant_id, 'kb': kbid,
                              'empty_kb': empty, 'redis_prefix': runtime.store.prefix, 'redis_run_id': expected_redis_run_id(),
                              'pg_system_id': '7696088403504697382', 'owner_ledger_unchanged_sha256': before,
                              'internal_source_readback': source,
                              'mock_transport_requests': len(wire), 'synthetic_usage_rows': ledger,
                              'events': frames(reply), 'fixed_refusal': frames(refusal)}
                    out = Path(os.environ['P21_EVIDENCE_DIR']) / 'http-pg-result.json'
                    assert not out.exists()
                    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        finally:
            await conn.close()
    actual_pg_run(scenario())
