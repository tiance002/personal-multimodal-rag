"""Opt-in real PG preferences/admission; disposable s88 DB, full rollback, no models."""
import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest


def test_s88_generation_preferences_and_frozen_turn_real_postgresql():
    dsn=os.environ.get('S88_TEST_DSN',''); expected=os.environ.get('S88_TEST_SYSTEM_ID','')
    if not dsn or not expected: pytest.skip('S88_TEST_DSN and S88_TEST_SYSTEM_ID required')
    if not urlsplit(dsn).path.lstrip('/').startswith('s88_'): pytest.fail('disposable s88_* database required')
    async def scenario():
        import psycopg
        from psycopg.rows import dict_row
        from backend.weknora_port.runtime import ApplicationRuntime, ConversationSettings
        from backend.weknora_port.storage.history import LocalPrincipal
        from backend.weknora_port.storage.turns import PostgresTurnRepository
        from backend.weknora_port.service import OrdinaryTurnRequest
        async with await psycopg.AsyncConnection.connect(dsn,autocommit=True,row_factory=dict_row) as conn:
            class Guard:
                connection=conn
                async def verify_identity(self):
                    row=await (await conn.execute('SELECT current_database() AS db,system_identifier::text AS id FROM pg_control_system()')).fetchone()
                    assert row['id']==expected and row['db'].startswith('s88_')
                    self.verified=True
                def require_verified(self): assert self.verified
            guard=Guard(); await guard.verify_identity()
            class Leases:
                @asynccontextmanager
                async def lease(self):
                    await guard.verify_identity(); yield guard
            schema='s88_generation_'+uuid.uuid4().hex
            async with conn.transaction(force_rollback=True):
                await conn.execute('CREATE SCHEMA '+schema)
                await conn.execute('SET LOCAL search_path TO '+schema)
                await conn.execute('CREATE TABLE tenants(id integer PRIMARY KEY)')
                await conn.execute("CREATE TABLE sessions(id varchar(36) PRIMARY KEY,tenant_id integer,subject_id text,title text DEFAULT '',knowledge_base_scope jsonb,document_scope jsonb,context_config jsonb DEFAULT '{}',summary_parameters jsonb DEFAULT '{}',deleted_at timestamptz,updated_at timestamptz DEFAULT now(),UNIQUE(tenant_id,subject_id,id))")
                await conn.execute("CREATE TABLE messages(id varchar(36) PRIMARY KEY,request_id varchar(36),session_id varchar(36),tenant_id integer,subject_id text,role text,content text,knowledge_base_scope jsonb,document_scope jsonb,is_completed boolean,created_at timestamptz,deleted_at timestamptz)")
                await conn.execute('CREATE TABLE knowledge_bases(id text,tenant_id integer,deleted_at timestamptz)')
                await conn.execute('CREATE TABLE knowledges(id text,tenant_id integer,knowledge_base_id text,deleted_at timestamptz)')
                for name in ('007_turns.sql','011_turn_configuration.sql'):
                    await conn.execute((Path(__file__).parents[2]/'backend/weknora_port/storage/migrations'/name).read_text(encoding='utf8'))
                await conn.execute('INSERT INTO tenants VALUES(7)')
                await conn.execute("INSERT INTO knowledge_bases(id,tenant_id) VALUES('kb',7)")
                ids=[str(uuid.uuid4()) for _ in range(2)]
                for sid in ids:
                    await conn.execute("INSERT INTO sessions(id,tenant_id,subject_id,knowledge_base_scope,document_scope) VALUES(%s,7,'owner','[\"kb\"]','[]')",(sid,))
                principal=LocalPrincipal(7,'owner');leases=Leases()
                runtime=object.__new__(ApplicationRuntime)
                runtime.principal=principal;runtime.leases=leases;runtime.conversation=ConversationSettings()
                runtime.turns=PostgresTurnRepository(leases,principal,generation_defaults=runtime.conversation.summary)
                # Disposable schema tests PG preferences, with no model tables.
                async def capabilities(session, maximum):
                    from backend.weknora_port.models.generation_capabilities import describe_generation_capabilities
                    return describe_generation_capabilities(None,maximum,error_code='CHAT_MODEL_CONFIGURATION_REQUIRED')
                runtime._answer_generation_capabilities=capabilities
                settings=await runtime.update_generation_settings(principal,ids[0],{'reasoning_mode':'smart','max_output_tokens':1600})
                request=OrdinaryTurnRequest(ids[0],'synthetic source question',str(uuid.uuid4()))
                first=await runtime.turns.accept(request,principal)
                assert first.request.generation_settings['effective']==settings['effective']
                await runtime.update_generation_settings(principal,ids[0],{'reasoning_mode':'quick','max_output_tokens':2500})
                replay=await PostgresTurnRepository(leases,principal).accept(request,principal)
                assert replay.replay_only and replay.request.generation_settings==first.request.generation_settings
                second=await runtime.turns.accept(OrdinaryTurnRequest(ids[0],'next question',str(uuid.uuid4())),principal)
                assert second.request.generation_settings['effective']=={'reasoning_mode':'quick','max_output_tokens':2500}
                assert (await runtime.read_generation_settings(principal,ids[1]))['effective']=={'reasoning_mode':'quick','max_output_tokens':2048}
                stored=await (await conn.execute('SELECT request_configuration FROM port_turns WHERE run_id=%s',(first.run_id,))).fetchone()
                assert stored['request_configuration']['generation_settings']==first.request.generation_settings
            assert (await (await conn.execute('SELECT count(*) AS n FROM pg_namespace WHERE nspname=%s',(schema,))).fetchone())['n']==0
    asyncio.run(scenario())
