"""Actual isolated PostgreSQL admission checks; no model/provider calls."""
import asyncio
import json
import uuid
from pathlib import Path

import pytest

from test_memory_storage_pg import approved_dsn, actual_pg_run, connection, EXPECTED, PROHIBITED
from backend.weknora_port.storage.connections import IsolatedConnectionFactory
from backend.weknora_port.storage.postgres import IsolationPolicy
from backend.weknora_port.storage.local_principal import LocalPrincipalStore
from backend.weknora_port.storage.turns import PostgresTurnRepository
from backend.weknora_port.storage.turns_migrate import apply_turn_schema
from backend.weknora_port.service import OrdinaryTurnRequest
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.streaming.replay import FinalCommit


def factory(dsn):
    import psycopg
    from psycopg.rows import dict_row
    async def acquire():
        return await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)
    async def release(conn):
        await conn.close()
    return IsolatedConnectionFactory(acquire, release, IsolationPolicy(EXPECTED, PROHIBITED))


def save(name, value):
    path = Path(__file__).resolve().parents[2] / 'docs/weknora-port/evidence/RESUME_NATIVE_TURN_TIMESTAMP_READBACK_20261013.json'
    content = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
        'kind': 'actual-isolated-pg-synthetic-records', 'system_identifier': EXPECTED,
        'database': 'weknora_python_port_20261013', 'model_calls': 0, 'old_database_writes': 0, 'cases': {}}
    content['cases'][name] = value
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False, default=str) + '\n', encoding='utf-8')


async def setup(dsn):
    conn, guard = await connection(dsn)
    try:
        digest = await apply_turn_schema(guard)
        principal = await LocalPrincipalStore(factory(dsn)).bootstrap()
        kb, doc, session = [str(uuid.uuid4()) for _ in range(3)]
        await conn.execute("INSERT INTO knowledge_bases(id,tenant_id,name,embedding_model_id,summary_model_id,rerank_model_id) VALUES(%s,%s,%s,'','','')", (kb, principal.tenant_id, 'Synthetic admission fixture'))
        await conn.execute("INSERT INTO knowledges(id,tenant_id,knowledge_base_id,type,title,source) VALUES(%s,%s,%s,'text','Synthetic admission document','fixture')", (doc, principal.tenant_id, kb))
        await conn.execute("INSERT INTO sessions(id,tenant_id,subject_id,knowledge_base_scope,document_scope) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)", (session, principal.tenant_id, principal.subject_id, json.dumps([kb]), json.dumps([doc])))
        return conn, principal, kb, doc, session, digest
    except BaseException:
        await conn.close()
        raise


def test_actual_pg_bootstrap_restart_and_concurrent_durable_admission():
    dsn = approved_dsn()
    async def run():
        conn, principal, kb, doc, session, digest = await setup(dsn)
        try:
            assert await LocalPrincipalStore(factory(dsn)).bootstrap() == principal
            repo = PostgresTurnRepository(factory(dsn), principal)
            request = OrdinaryTurnRequest(session, 'synthetic admission question', str(uuid.uuid4()))
            results = await asyncio.gather(repo.accept(request, principal), PostgresTurnRepository(factory(dsn), principal).accept(request, principal))
            assert sum(turn.is_new for turn in results) == 1
            assert results[0].run_id == results[1].run_id != request.request_id
            assert results[0].user_message_id == results[1].user_message_id
            assert results[0].assistant_message_id == results[1].assistant_message_id
            replay = await PostgresTurnRepository(factory(dsn), principal).accept(request, principal)
            assert replay.replay_only and not replay.is_new
            assert replay.request.request_id == replay.context.request_id == replay.run_id
            assert replay.context.scope.knowledge_base_ids == (kb,) and replay.context.scope.document_ids == (doc,)
            recovered = await repo.validate_run(session, replay.assistant_message_id, principal)
            assert recovered.replay_only and recovered.run_id == replay.run_id
            with pytest.raises(ProviderError, match='TURN_REQUEST_PAYLOAD_CONFLICT'):
                await repo.accept(OrdinaryTurnRequest(session, 'changed synthetic question', request.request_id), principal)
            rows = await (await conn.execute("SELECT id,role,request_id FROM messages WHERE session_id=%s ORDER BY role", (session,))).fetchall()
            assert len(rows) == 2 and {r['role'] for r in rows} == {'user', 'assistant'}
            ordered=await (await conn.execute('SELECT role,created_at FROM messages WHERE session_id=%s ORDER BY created_at,id',(session,))).fetchall()
            assert [row['role'] for row in ordered]==['user','assistant']
            assert ordered[0]['created_at']<ordered[1]['created_at']
            save('concurrent_restart', {'migration_sha256': digest, 'tenant_id': principal.tenant_id, 'session_id': session,
                'client_request_id': request.request_id, 'run_id': replay.run_id, 'messages': rows,
                'concurrent_new_count': 1, 'restart_replay_only': True, 'changed_payload_denied': True})
        finally:
            await conn.close()
    actual_pg_run(run())


def test_actual_pg_scope_deletion_and_scoped_client_id():
    dsn = approved_dsn()
    async def run():
        conn, principal, kb, doc, session, _ = await setup(dsn)
        try:
            repo = PostgresTurnRepository(factory(dsn), principal)
            client = str(uuid.uuid4())
            first = await repo.accept(OrdinaryTurnRequest(session, 'fixture scope', client), principal)
            second_session = str(uuid.uuid4())
            await conn.execute("INSERT INTO sessions(id,tenant_id,subject_id,knowledge_base_scope,document_scope) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb)", (second_session, principal.tenant_id, principal.subject_id, json.dumps([kb]), json.dumps([doc])))
            second = await repo.accept(OrdinaryTurnRequest(second_session, 'fixture scope', client), principal)
            assert first.run_id != second.run_id
            for request in (
                OrdinaryTurnRequest(session, 'fixture scope', str(uuid.uuid4()), knowledge_base_scope=(str(uuid.uuid4()),)),
                OrdinaryTurnRequest(session, 'fixture scope', str(uuid.uuid4()), document_scope=(str(uuid.uuid4()),)),
                OrdinaryTurnRequest(session, 'fixture scope', 'x' * 37),
            ):
                with pytest.raises(ProviderError): await repo.accept(request, principal)
            await conn.execute("UPDATE sessions SET knowledge_base_scope='[]',knowledge_base_id=NULL WHERE id=%s", (second_session,))
            with pytest.raises(ProviderError, match='TURN_RAW_SCOPE_EXPANSION_DENIED'):
                await repo.validate_run(second_session, second.assistant_message_id, principal)
            await conn.execute('UPDATE knowledges SET deleted_at=now() WHERE id=%s', (doc,))
            with pytest.raises(ProviderError, match='TURN_DOCUMENT_SCOPE_DENIED'):
                await repo.validate_run(session, first.assistant_message_id, principal)
            await conn.execute('UPDATE sessions SET deleted_at=now() WHERE id=%s', (session,))
            with pytest.raises(ProviderError, match='TURN_SESSION_UNAVAILABLE'): await repo.accept(OrdinaryTurnRequest(session, 'fixture scope', client), principal)
            with pytest.raises(ProviderError, match='TURN_SESSION_UNAVAILABLE'): await repo.validate_run(session, first.assistant_message_id, principal)
            save('scope_deletion', {'session_id': session, 'other_session_id': second_session, 'client_request_id': client,
                'run_ids': [first.run_id, second.run_id], 'scope_expansion_denied': True, 'changed_session_scope_denied': True,
                'deleted_document_denied': True, 'deleted_session_denied': True, 'long_client_id_denied': True})
        finally: await conn.close()
    actual_pg_run(run())


def test_actual_pg_failure_receipt_and_atomic_final_authority():
    dsn = approved_dsn()
    async def run():
        conn, principal, kb, doc, session, _ = await setup(dsn)
        try:
            repo = PostgresTurnRepository(factory(dsn), principal)
            failed = await repo.accept(OrdinaryTurnRequest(session, 'fixture failure question', str(uuid.uuid4())), principal)
            assert await repo.commit_failure(failed, 'MODEL_USAGE_UNKNOWN') is True
            assert await repo.commit_failure(failed, 'MODEL_USAGE_UNKNOWN') is True
            with pytest.raises(ProviderError, match='TURN_ALREADY_FAILED'):
                await repo.commit_final(FinalCommit(session, failed.assistant_message_id, failed.run_id, 'late fixture answer', []))
            successful = await repo.accept(OrdinaryTurnRequest(session, 'fixture final question', str(uuid.uuid4())), principal)
            final = FinalCommit(session, successful.assistant_message_id, successful.run_id, 'fixture committed answer', [], None)
            await repo.commit_final(final)
            await repo.commit_final(final)
            assert (await repo.status(session, successful.assistant_message_id, principal)).status == 'completed'
            assert await repo.commit_failure(successful, 'MODEL_USAGE_UNKNOWN') is False
            rows = await (await conn.execute("SELECT id,role,content,is_completed,turn_status,turn_error_code FROM messages WHERE session_id=%s ORDER BY id", (session,))).fetchall()
            users = {row['content'] for row in rows if row['role'] == 'user'}
            assert users == {'fixture failure question', 'fixture final question'}
            success_row = next(row for row in rows if row['id'] == successful.assistant_message_id)
            failure_row = next(row for row in rows if row['id'] == failed.assistant_message_id)
            assert success_row['content'] == final.answer and success_row['is_completed'] and not success_row['turn_error_code']
            assert failure_row['content'] == '' and failure_row['turn_status'] == 'error' and not failure_row['is_completed']
            cancelled = await repo.accept(OrdinaryTurnRequest(session, 'fixture user cancellation', str(uuid.uuid4())), principal)
            assert await repo.commit_cancelled(cancelled,'public partial','public thought',[]) is True
            assert await repo.commit_cancelled(cancelled,'must not overwrite','',[]) is False
            assert (await repo.status(session,cancelled.assistant_message_id,principal)).status=='cancelled'
            cancelled_row=await (await conn.execute('SELECT content,is_completed,turn_status,agent_steps FROM messages WHERE id=%s',(cancelled.assistant_message_id,))).fetchone()
            assert cancelled_row['is_completed'] and cancelled_row['turn_status']=='cancelled' and cancelled_row['content']=='public partial'
            assert cancelled_row['agent_steps'][0]['reasoning_content']=='public thought'
            with pytest.raises(ProviderError,match='TURN_ALREADY_FAILED'):
                await repo.commit_final(FinalCommit(session,cancelled.assistant_message_id,cancelled.run_id,'late fake success',[]))
            save('failure_final', {'session_id': session, 'failed_run_id': failed.run_id, 'successful_run_id': successful.run_id,
                'messages': rows, 'late_failure_receipt': False, 'late_final_after_failure_denied': True,
                'budget_ledger_operations': 0})
        finally: await conn.close()
    actual_pg_run(run())
