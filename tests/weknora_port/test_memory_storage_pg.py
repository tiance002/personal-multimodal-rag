"""Actual isolated PostgreSQL opt-in checks; never a SQL double.

Requires explicit WEKNORA_PORT_TEST_DSN for the approved localhost new database.
No provider calls. Identity verified on each real connection before any write.
"""
import asyncio
import os
import json
import uuid
from pathlib import Path
from dataclasses import replace
from urllib.parse import urlsplit

import pytest

from backend.weknora_port.conversation import MemoryContext, MemoryItem, MemoryManager, MemoryScope, MemoryService, MemoryVectorQuery
from backend.weknora_port.storage.memory import PostgresExtractionEnqueuer, PostgresMemoryRepository
from backend.weknora_port.storage.postgres import IsolationPolicy, PostgresRetriever
from backend.weknora_port.conversation.maintenance_types import ExtractionPayload, ExtractionSession, MessageCursor

EXPECTED = '7696088403504697382'
PROHIBITED = frozenset({'7691227493754040358','7689510505384681511','7696038373595934758'})


def approved_dsn():
    value = os.environ.get('WEKNORA_PORT_TEST_DSN')
    if not value: pytest.skip('Explicit isolated PostgreSQL DSN not supplied')
    parsed = urlsplit(value)
    if parsed.hostname != '127.0.0.1' or parsed.port != 56423 or parsed.path != '/weknora_python_port_20261013':
        pytest.fail('DSN is not the approved isolated target')
    return value


def actual_pg_run(awaitable):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(awaitable)


async def connection(dsn):
    import psycopg
    from psycopg.rows import dict_row
    conn = await psycopg.AsyncConnection.connect(dsn,autocommit=True,row_factory=dict_row,connect_timeout=5)
    guard = PostgresRetriever(conn,IsolationPolicy(EXPECTED,PROHIBITED))
    try: await guard.verify_identity()
    except BaseException:
        await conn.close();raise
    return conn,guard


def save_pg_evidence(name, value):
    path=Path(__file__).resolve().parents[2]/'docs/weknora-port/evidence/RESUME_P5_GENERATION_STORAGE_EVIDENCE.json'
    content=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'kind':'actual-isolated-pg-synthetic-records','model_calls':0,'budget_calls':0,'cases':{}}
    content['cases'][name]=value
    path.write_text(json.dumps(content,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')


def test_actual_pg_memory_crud_pending_vector_delete_and_stale_cache_cas():
    dsn=approved_dsn()
    async def run():
        conn,guard=await connection(dsn)
        tenant=None;owner='web_user:pg-memory-'+str(uuid.uuid4())
        try:
            tenant=(await (await conn.execute("INSERT INTO tenants(name,business,memory_config) VALUES(%s,%s,%s::jsonb) RETURNING id",('P4 storage fixture','isolated-test','{"enabled":true,"write_mode":"auto","embedding_model_id":"synthetic-space"}'))).fetchall())[0]['id']
            repo=PostgresMemoryRepository(conn,guard.isolation,identity_guard=guard)
            context=MemoryContext(tenant,owner);scope=MemoryScope(tenant,owner)
            reader=MemoryService(repo);manager=MemoryManager(repo,reader)
            original=await manager.remember(context,MemoryItem('',0,'','profile','private original fixture',topic='role',origin='explicit'))
            assert (await repo.get_subject(scope)).block_text
            proposal=await manager.remember(context,MemoryItem('',0,'','profile','proposed fixture',topic='role',inferred=True))
            assert proposal.status=='pending' and proposal.replaces_id==original.id
            assert (await repo.get_item(scope,original.id)).status=='active'
            confirmed=await manager.confirm(context,proposal.id)
            assert confirmed.status=='active' and (await repo.get_item(scope,original.id)).status=='superseded'
            await repo.upsert_item_embedding(scope,confirmed,'synthetic-space',(.1,.2))
            hits=await repo.search_items_by_vector(scope,MemoryVectorQuery('synthetic-space',(.1,.2),('profile',),.5,5))
            assert [hit.item.id for hit in hits]==[confirmed.id]
            before=await repo.get_subject(scope)
            updated=await manager.update(context,confirmed.id,'private updated fixture',4)
            assert not await repo.item_embeddings(scope,(updated.id,),'synthetic-space')
            # Delayed old vector cannot repopulate after edit.
            await repo.upsert_item_embedding(scope,confirmed,'synthetic-space',(.1,.2))
            assert not await repo.item_embeddings(scope,(updated.id,),'synthetic-space')
            stale_revision=(await repo.get_subject(scope)).cache_revision
            await manager.delete(context,updated.id)
            assert await repo.get_item(scope,updated.id) is None
            assert not await repo.update_subject_block(scope,'private updated fixture',1,stale_revision)
            assert 'private updated fixture' not in (await reader.recall(context,'private')).prompt
            assert (await repo.get_subject(scope)).cache_revision>stale_revision>before.cache_revision
            assert await manager.clear(context)==1  # original superseded provenance row
            assert not await repo.list_items(scope,'',50,0)
            await reader.drain_background()
            identity=await guard.verify_identity()
            cache=await repo.get_subject(scope)
            save_pg_evidence('memory_crud_delete_cas',{'database':identity.database,'system_identifier':identity.system_identifier,'tenant_id':tenant,'subject_id':owner,
                'source_item_id':original.id,'proposal_id':proposal.id,'proposal_replaces_id':proposal.replaces_id,'confirmed_id':confirmed.id,
                'vector_hit_ids':[h.item.id for h in hits],'deleted_item_absent':await repo.get_item(scope,updated.id) is None,
                'stale_cache_revision':stale_revision,'final_cache_revision':cache.cache_revision,'final_block_empty':cache.block_text=='',
                'late_vector_rejected':not bool(await repo.item_embeddings(scope,(updated.id,),'synthetic-space')),'clear_remaining_count':0,
                'assertions':'PASS','fixture_cleanup':'scoped fixture rows deleted after capture','source_content':'synthetic fixture only; no original data read'})
        finally:
            if tenant is not None:
                async with conn.transaction():
                    for table in ('memory_item_embeddings','memory_items','memory_topic_stats','memory_doc_affinity','memory_tombstones','memory_operations','memory_extract_jobs','memory_extraction_sessions','memory_subjects'):
                        await conn.execute(f'DELETE FROM {table} WHERE tenant_id=%s AND subject_id=%s',(tenant,owner))
                    await conn.execute('DELETE FROM tenants WHERE id=%s',(tenant,))
            await conn.close()
    actual_pg_run(run())


def test_actual_pg_extraction_revision_lease_failures_and_task_delivery():
    dsn=approved_dsn()
    async def run():
        conn,guard=await connection(dsn)
        scope=MemoryScope(2000000001,'web_user:pg-queue-'+str(uuid.uuid4()))
        session=str(uuid.uuid4())
        try:
            repo=PostgresMemoryRepository(conn,guard.isolation,identity_guard=guard)
            _,should=await repo.enqueue_pending_session(scope,session,990)
            assert should
            _,should=await repo.enqueue_pending_session(scope,session,990)
            assert not should
            batch=await repo.claim_pending_sessions(scope,session,'worker',600)
            captured=batch.sessions[0]
            assert captured.revision==2
            assert (await repo.claim_pending_sessions(scope,session,'other',600)).retry_at
            await repo.enqueue_pending_session(scope,session,990)
            end=MessageCursor((await repo.get_subject(scope)).last_extracted_at,str(uuid.uuid4()))
            await repo.checkpoint_extraction(scope,'worker',captured,end,True)
            assert await repo.has_pending_extraction(scope)
            for index in (1,2,3):
                skip=await repo.record_extraction_failure(scope,'worker',captured,end,'invalid_model_output')
                assert skip==(index==3)
            await repo.finish_extraction(scope,'worker')
            batch=await repo.claim_pending_sessions(scope,session,'next',600)
            await repo.checkpoint_extraction(scope,'next',batch.sessions[0],end,True)
            await repo.finish_extraction(scope,'next')
            assert not await repo.has_pending_extraction(scope)
            queue=PostgresExtractionEnqueuer(repo);payload=ExtractionPayload(scope,session,str(uuid.uuid4()))
            await queue.enqueue(payload,0,2);await queue.enqueue(payload,0,2)
            job=await queue.claim_due(scope,'delivery')
            assert job and job.attempts==1
            assert await queue.claim_due(scope,'other') is None
            await queue.enqueue(payload,0,2)  # running task may queue successor
            await queue.complete_job(job)
            successor=await queue.claim_due(scope,'successor')
            assert successor and successor.id!=job.id
            await queue.complete_job(successor)
            identity=await guard.verify_identity()
            save_pg_evidence('memory_queue_lease_revision',{'database':identity.database,'system_identifier':identity.system_identifier,'tenant_id':scope.tenant_id,'subject_id':scope.subject_id,
                'session_id':session,'captured_revision':captured.revision,'changed_revision_remained_pending':True,'invalid_output_skip_on_third':True,
                'pending_after_final_checkpoint':await repo.has_pending_extraction(scope),'delivery_id':job.id,'successor_id':successor.id,
                'assertions':'PASS','fixture_cleanup':'scoped fixture rows deleted after capture','model_calls':0})
        finally:
            async with conn.transaction():
                for table in ('memory_extract_jobs','memory_extraction_sessions','memory_subjects'):
                    await conn.execute(f'DELETE FROM {table} WHERE tenant_id=%s AND subject_id=%s',(scope.tenant_id,scope.subject_id))
            await conn.close()
    actual_pg_run(run())
