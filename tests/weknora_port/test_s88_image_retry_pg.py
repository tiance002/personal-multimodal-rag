"""Opt-in real PostgreSQL check, rolls all schema/data back; zero Provider calls.

S88_TEST_DSN must name a disposable s88_* database. S88_TEST_SYSTEM_ID must
match pg_control_system().system_identifier obtained by the integration owner.
No historical hardcoded cluster identity, dataset, font, Redis or paid model.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest


def test_s88_image_retry_migration_real_postgresql():
    dsn=os.environ.get('S88_TEST_DSN','')
    expected=os.environ.get('S88_TEST_SYSTEM_ID','')
    if not dsn or not expected:pytest.skip('S88_TEST_DSN and S88_TEST_SYSTEM_ID required')
    if not urlsplit(dsn).path.lstrip('/').startswith('s88_'):
        pytest.fail('disposable s88_* database required')
    async def scenario():
        import psycopg
        from psycopg.rows import dict_row
        from backend.weknora_port.storage.image_tasks_migrate import apply_image_selected_retry_schema
        async with await psycopg.AsyncConnection.connect(dsn,autocommit=True,row_factory=dict_row) as connection:
            actual=(await (await connection.execute('SELECT system_identifier::text AS id FROM pg_control_system()')).fetchone())['id']
            assert actual==expected
            class Guard:
                async def verify_identity(self):
                    current=(await (await connection.execute('SELECT system_identifier::text AS id FROM pg_control_system()')).fetchone())['id']
                    assert current==expected
            guard=Guard();guard.connection=connection
            guard.require_verified=lambda:None  # Actual cluster identity verified above and again by migration.
            schema='s88_retry_'+uuid.uuid4().hex
            # Force outer rollback even on success; nested migration transaction uses savepoints.
            async with connection.transaction(force_rollback=True):
                await connection.execute('CREATE SCHEMA '+schema)
                await connection.execute('SET LOCAL search_path TO '+schema)
                await connection.execute('CREATE TABLE port_schema_migrations(version text PRIMARY KEY,sha256 text NOT NULL)')
                sql=(Path(__file__).parents[2]/'backend/weknora_port/storage/migrations/010_image_tasks.sql').read_text(encoding='utf8')
                await connection.execute(sql)
                base=(str(uuid.uuid4()),7,'owner','kb','doc','v','{}')
                await connection.execute('INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',base)
                before=await (await connection.execute('SELECT id,state,attempt,selected_config,outcomes FROM port_image_tasks')).fetchall()
                digest=await apply_image_selected_retry_schema(guard)
                assert len(digest)==64 and await apply_image_selected_retry_schema(guard)==digest
                assert await (await connection.execute('SELECT id,state,attempt,selected_config,outcomes FROM port_image_tasks')).fetchall()==before
                for retry_key in ('a'*64,'b'*64):
                    await connection.execute('INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config,retry_key) VALUES(%s,7,\'owner\',\'kb\',\'doc\',\'v\',\'{}\',%s)',(str(uuid.uuid4()),retry_key))
                for retry_key in ('','a'*64):
                    with pytest.raises(psycopg.errors.UniqueViolation):
                        async with connection.transaction():
                            await connection.execute('INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config,retry_key) VALUES(%s,7,\'owner\',\'kb\',\'doc\',\'v\',\'{}\',%s)',(str(uuid.uuid4()),retry_key))
                assert (await (await connection.execute('SELECT count(*) AS n FROM port_image_tasks')).fetchone())['n']==3
                # Execute the actual queue cancellation SQL against real PG. The
                # completed source represents an old usable selected-image version.
                from backend.weknora_port.image_tasks import ImageTaskQueue
                await connection.execute("CREATE TABLE knowledges(id text PRIMARY KEY,tenant_id integer,knowledge_base_id text,metadata jsonb,parse_status text,error_message text DEFAULT '',updated_at timestamptz DEFAULT now(),deleted_at timestamptz)")
                class Leases:
                    @asynccontextmanager
                    async def lease(self,**kwargs):yield guard
                principal=SimpleNamespace(tenant_id=7,subject_id='owner')
                queue=ImageTaskQueue(Leases(),principal,None,None,None)
                for kind in ('caption','embedding','unsent'):
                    document='doc-'+kind
                    task_id=str(uuid.uuid4())
                    outcomes={'0':{kind:{'status':'sending'}}} if kind!='unsent' else {}
                    await connection.execute("INSERT INTO knowledges(id,tenant_id,knowledge_base_id,metadata,parse_status) VALUES(%s,7,'kb','{\"source_version\":\"v\"}','completed')",(document,))
                    await connection.execute("INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config,state,claim_token,outcomes) VALUES(%s,7,'owner','kb',%s,'v','{}','processing',%s,%s::jsonb)",(task_id,document,str(uuid.uuid4()),json.dumps(outcomes)))
                    result=await queue.cancel(principal,'kb',document)
                    expected_state='cancelled' if kind=='unsent' else 'unknown'
                    assert result=={'id':task_id,'state':expected_state}
                    stored=await (await connection.execute('SELECT state,error_code,claim_token,outcomes FROM port_image_tasks WHERE id=%s',(task_id,))).fetchone()
                    assert stored['state']==expected_state and stored['claim_token'] is None
                    assert stored['outcomes']==outcomes
                    assert stored['error_code']==('IMAGE_TASK_CANCELLED' if kind=='unsent' else 'IMAGE_REQUEST_OUTCOME_UNKNOWN')
                    source=await (await connection.execute('SELECT parse_status,error_message FROM knowledges WHERE id=%s',(document,))).fetchone()
                    assert source=={'parse_status':'completed','error_message':''}

            assert (await (await connection.execute('SELECT count(*) AS n FROM pg_namespace WHERE nspname=%s',(schema,))).fetchone())['n']==0
    asyncio.run(scenario())
