"""Actual isolated SQL check for GORM-equivalent soft-deletion exclusions."""
import json
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
import pytest

from tests.weknora_port.test_memory_storage_pg import approved_dsn,connection,actual_pg_run
from backend.weknora_port.storage.memory import PostgresMemoryRepository
from backend.weknora_port.storage.generation import PostgresGenerationLifecycle
from backend.weknora_port.conversation.types import MemoryScope
from backend.weknora_port.conversation.maintenance_types import MessageCursor
from backend.weknora_port.models.types import CallContext,TrustedScope
from backend.weknora_port.generation.types import GenerationError


def test_deleted_messages_not_recalled_and_deleted_session_cannot_claim_model_run():
    dsn = approved_dsn()
    async def run():
        conn,guard = await connection(dsn)
        subject = 'soft-delete-fixture-'+str(uuid4())
        session,alive,deleted = (str(uuid4()) for _ in range(3))
        tenant = None
        try:
            tenant = (await (await conn.execute("INSERT INTO tenants(name,business) VALUES('soft deletion fixture','isolated-test') RETURNING id")).fetchone())['id']
            await conn.execute("INSERT INTO sessions(id,tenant_id,subject_id) VALUES(%s,%s,%s)",(session,tenant,subject))
            for message,body,removed in ((alive,'visible',False),(deleted,'removed',True)):
                await conn.execute("INSERT INTO messages(id,request_id,session_id,tenant_id,subject_id,role,content,deleted_at) "
                    "VALUES(%s,%s,%s,%s,%s,'user',%s,CASE WHEN %s THEN now() ELSE NULL END)",
                    (message,str(uuid4()),session,tenant,subject,body,removed))
            repository = PostgresMemoryRepository(conn,guard.isolation,identity_guard=guard)
            scope = MemoryScope(tenant,subject)
            recent = await repository.recent_messages(scope,session,10)
            after = await repository.messages_after_cursor(scope,session,MessageCursor(),10)
            before = await repository.messages_before_time(scope,session,datetime(2100,1,1,tzinfo=timezone.utc),10)
            assert all([row.id for row in rows] == [alive] for rows in (recent,after,before))
            await conn.execute('UPDATE sessions SET deleted_at=now() WHERE id=%s',(session,))
            context = CallContext(str(uuid4()),TrustedScope(subject,('synthetic-kb',)),session_id=session)
            lifecycle = PostgresGenerationLifecycle(guard,tenant_id=tenant,session_id=session,message_id=deleted)
            with pytest.raises(GenerationError,match='GENERATION_STORAGE_SCOPE_DENIED'):
                await lifecycle.claim_run(context.request_id,'a'*64,context)
            target = Path('docs/weknora-port/evidence/soft-deletion-pg-20261013.json')
            target.write_text(json.dumps({'status':'PASS','kind':'ACTUAL_ISOLATED_PG',
                'system_identifier':guard.isolation.expected_system_identifier,'session':session,
                'alive_message':alive,'deleted_message':deleted,'recent_ids':[row.id for row in recent],
                'after_ids':[row.id for row in after],'before_ids':[row.id for row in before],
                'deleted_session_generation_claim':'DENIED','model_calls':0,
                'cleanup':'only these new synthetic message/session/tenant rows'},indent=2)+'\n',encoding='utf-8')
        finally:
            if tenant is not None:
                await conn.execute('DELETE FROM messages WHERE tenant_id=%s AND subject_id=%s',(tenant,subject))
                await conn.execute('DELETE FROM sessions WHERE tenant_id=%s AND subject_id=%s',(tenant,subject))
                await conn.execute('DELETE FROM tenants WHERE id=%s',(tenant,))
            await conn.close()
    actual_pg_run(run())
