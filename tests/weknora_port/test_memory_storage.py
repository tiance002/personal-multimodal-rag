"""Fake SQL-boundary checks. These do NOT execute PostgreSQL or prove locks."""
import asyncio
import hashlib
import inspect
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.weknora_port.conversation import MemoryConfig, MemoryItem, MemoryScope, MemoryScopeError, MemorySubject, MemoryTombstone, MemoryVectorQuery
from backend.weknora_port.conversation.maintenance_types import ExtractionSession, MaintenanceRepository, MessageCursor
from backend.weknora_port.conversation.management import ManagedMemoryRepository, MemoryWriteError
from backend.weknora_port.conversation.types import HistoryRepository, MemoryRepository
from backend.weknora_port.storage.memory import MemoryExtractionJob, PostgresExtractionEnqueuer, PostgresMemoryRepository, decode_vector, encode_vector
from backend.weknora_port.conversation.maintenance_types import ExtractionPayload
from backend.weknora_port.storage.postgres import IsolationPolicy

NOW = datetime.now(timezone.utc)
SCOPE = MemoryScope(7, "web_user:synthetic-owner")
POLICY = IsolationPolicy("new-system-fixture", frozenset({"old-system-fixture"}))


def subject(**kwargs):
    return dict(id="subject-fixture", tenant_id=7, subject_id=SCOPE.subject_id, enabled=True,
                block_text="private old cache", item_count=1, cache_revision=0, server_now=NOW, **kwargs)


def row(id="old", **kwargs):
    return asdict(MemoryItem(id, 7, SCOPE.subject_id, "fact", "old content", topic="topic", normalized_key="topic", valid_from=NOW, **kwargs))


class Cursor:
    def __init__(self, rows): self.rows = rows
    async def fetchall(self): return self.rows


class SQLFixture:
    """Scripted DB-API stand-in recording exact SQL, params and transaction order.
    It never parses/executes SQL and is not a production state backend.
    """
    def __init__(self, answers=(), *, identity=None, fail_contains=""):
        self.answers = list(answers); self.calls, self.events = [], []
        self.identity = identity or {"database": POLICY.database, "system_identifier": "new-system-fixture"}
        self.fail_contains = fail_contains
    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if self.fail_contains and self.fail_contains in sql: raise RuntimeError("SYNTHETIC_SQL_FAILURE")
        if "pg_control_system()" in sql: return Cursor([self.identity])
        if sql.startswith("SELECT *,now() AS server_now FROM memory_subjects"): return Cursor([subject()])
        # Explicit scripts intercept reads/RETURNING only; mutation order stays real.
        if sql.startswith("SELECT") or " RETURNING " in sql:
            if not self.answers: return Cursor([])
            return Cursor(self.answers.pop(0))
        return Cursor([])
    @asynccontextmanager
    async def transaction(self):
        self.events.append("BEGIN")
        try: yield self
        except BaseException:
            self.events.append("ROLLBACK"); raise
        else: self.events.append("COMMIT")


async def ready(fixture=None):
    fixture = fixture or SQLFixture()
    repo = PostgresMemoryRepository(fixture, POLICY)
    await repo.verify_identity()
    return repo, fixture


def test_all_frozen_repository_protocol_methods_have_concrete_adapter_implementations():
    for protocol in (MemoryRepository, ManagedMemoryRepository, MaintenanceRepository, HistoryRepository):
        for name, member in protocol.__dict__.items():
            if inspect.iscoroutinefunction(member):
                assert inspect.iscoroutinefunction(getattr(PostgresMemoryRepository, name, None)), name


def test_unverified_old_identity_and_invalid_scope_issue_no_memory_dml():
    async def run():
        db = SQLFixture(); repo = PostgresMemoryRepository(db, POLICY)
        with pytest.raises(PermissionError): await repo.ensure_subject(SCOPE)
        assert not db.calls
        db.identity["system_identifier"] = "old-system-fixture"
        with pytest.raises(PermissionError): await repo.verify_identity()
        with pytest.raises(PermissionError): await repo.delete_all(SCOPE, ())
        assert len(db.calls) == 1 and "pg_control_system" in db.calls[0][0]
        repo, db = await ready()
        with pytest.raises(MemoryScopeError): await repo.ensure_subject(MemoryScope(0, ""))
        assert len(db.calls) == 1 and not db.events
    asyncio.run(run())


def test_scope_and_secret_text_use_bound_parameters_never_sql_interpolation():
    async def run():
        repo, db = await ready(SQLFixture([[row()]]))
        await repo.find_active_by_key(SCOPE, "key');DELETE FROM memory_items;--")
        sql, params = db.calls[-1]
        assert "tenant_id=%s AND subject_id=%s" in sql and "status IN ('active','pending')" in sql
        assert params == (7, SCOPE.subject_id, "key');DELETE FROM memory_items;--")
        assert "DELETE FROM" not in sql
    asyncio.run(run())


def test_delete_tombstone_vector_cleanup_item_delete_cache_revision_share_transaction():
    async def run():
        repo, db = await ready(SQLFixture([[row()]]))
        await repo.delete_item(SCOPE, "old", MemoryTombstone("topic", "f" * 64, "source"))
        statements = [sql for sql, _ in db.calls]
        positions = [next(i for i, s in enumerate(statements) if part in s) for part in
            ("FOR UPDATE", "INSERT INTO memory_tombstones", "DELETE FROM memory_item_embeddings", "DELETE FROM memory_items", "cache_revision=cache_revision+1")]
        assert positions == sorted(positions) and db.events == ["BEGIN", "COMMIT"]
        assert all("tenant_id" in sql and "subject_id" in sql for sql in statements[1:])
        assert all("old content" not in str(params) for sql, params in db.calls if "tombstones" in sql)
        repo, db = await ready(SQLFixture([[row()]], fail_contains="INSERT INTO memory_tombstones"))
        with pytest.raises(RuntimeError): await repo.delete_item(SCOPE, "old", MemoryTombstone("topic", "f" * 64))
        assert db.events == ["BEGIN", "ROLLBACK"]
        assert not any(sql.startswith("DELETE FROM memory_items") or "cache_revision=cache_revision+1" in sql for sql, _ in db.calls)
    asyncio.run(run())


def test_stale_cache_cas_returns_false_without_unconditional_cache_write():
    async def run():
        repo, db = await ready(SQLFixture([[]]))
        assert not await repo.update_subject_block(SCOPE, "stale private", 1, 0)
        sql, params = db.calls[-1]
        assert "cache_revision=%s RETURNING" in sql and params[-1] == 0
        assert db.events == ["BEGIN", "COMMIT"]
    asyncio.run(run())


def test_pending_replacement_preserves_active_target_and_carries_link():
    async def run():
        repo, db = await ready(SQLFixture([[row()], [row()]]))
        incoming = MemoryItem("proposal", 7, SCOPE.subject_id, "fact", "new proposal", topic="topic", normalized_key="topic", status="pending", valid_from=NOW)
        saved = await repo.save_item(SCOPE, incoming, "old")
        assert saved.replaces_id == "old"
        assert not any("status='superseded'" in sql for sql, _ in db.calls)
        assert db.events == ["BEGIN", "COMMIT"]
        assert any("cache_revision=cache_revision+1" in sql for sql, _ in db.calls)
    asyncio.run(run())


def test_saved_operation_replay_returns_actual_identity_without_second_insert():
    async def run():
        repo, db = await ready(SQLFixture([[{"kind":"save_item","result_id":"already-saved"}], [row("already-saved")]]))
        incoming = MemoryItem("new-id", 7, SCOPE.subject_id, "fact", "new content", operation_id="stable-decision")
        saved = await repo.save_item(SCOPE, incoming, "")
        assert saved.id == "already-saved"
        assert not any(sql.startswith("INSERT INTO memory_items") for sql, _ in db.calls)
    asyncio.run(run())


def test_operation_record_after_effects_and_failure_rolls_back_without_success_record():
    async def run():
        incoming = MemoryItem("new", 7, SCOPE.subject_id, "fact", "new content", normalized_key="new", valid_from=NOW, operation_id="stable-decision")
        repo, db = await ready(SQLFixture([[], []]))
        await repo.save_item(SCOPE, incoming, "")
        statements = [s for s, _ in db.calls]
        assert next(i for i,s in enumerate(statements) if s.startswith("INSERT INTO memory_items")) < next(i for i,s in enumerate(statements) if s.startswith("INSERT INTO memory_operations"))
        repo, db = await ready(SQLFixture([[], []], fail_contains="INSERT INTO memory_items"))
        with pytest.raises(RuntimeError): await repo.save_item(SCOPE, incoming, "")
        assert db.events == ["BEGIN", "ROLLBACK"] and not any(s.startswith("INSERT INTO memory_operations") for s,_ in db.calls)
    asyncio.run(run())


def test_confirmation_detects_changed_active_target_and_edit_invalidates_proposals_vectors():
    async def run():
        proposal = row("proposal", status="pending", replaces_id="old")
        repo, db = await ready(SQLFixture([[proposal], [row(status="superseded")]]))
        with pytest.raises(MemoryWriteError, match="CONFLICT"): await repo.confirm_pending_item(SCOPE, "proposal")
        assert db.events[-1] == "ROLLBACK" and not any("SET status='active'" in s for s,_ in db.calls)
        repo, db = await ready(SQLFixture([[row()]]))
        await repo.update_item_content(SCOPE, "old", "updated", "topic", 3)
        assert any("DELETE FROM memory_item_embeddings" in s for s,_ in db.calls)
        assert any("replaces_id=%s AND status='pending'" in s for s,_ in db.calls)
        assert any("origin='manual'" in s for s,_ in db.calls)
    asyncio.run(run())


def test_pgvector_query_joins_owner_tenant_pinned_model_dims_lifecycle_and_threshold():
    async def run():
        high, low = row("high"), row("low"); high["score"], low["score"] = .9, .4
        repo, db = await ready(SQLFixture([[high, low]]))
        hits = await repo.search_items_by_vector(SCOPE, MemoryVectorQuery("pinned-model", (.1,.2), ("fact",), .5, 15))
        assert [hit.item.id for hit in hits] == ["high"]
        sql, params = db.calls[-1]
        assert "i.tenant_id=e.tenant_id AND i.subject_id=e.subject_id" in sql
        assert "e.model_id=%s AND e.dims=%s" in sql and "i.status='active'" in sql and "expires_at>now()" in sql
        assert params[1:5] == (7, SCOPE.subject_id, "pinned-model", 2) and params[-1] == 15
        assert "ORDER BY e.embedding::halfvec <=>" in sql and "importance" not in sql
    asyncio.run(run())


def test_late_vector_upsert_rejects_deleted_edited_or_superseded_snapshots():
    async def run():
        original = MemoryItem(**row())
        for current in ([], [dict(row(), content="edited")], [row(status="superseded")]):
            repo, db = await ready(SQLFixture([current]))
            await repo.upsert_item_embedding(SCOPE, original, "pinned-model", (.1,.2))
            assert not any(s.startswith("INSERT INTO memory_item_embeddings") for s,_ in db.calls)
        repo, db = await ready(SQLFixture([[row()]]))
        await repo.upsert_item_embedding(SCOPE, original, "pinned-model", (.1,.2))
        sql, params = db.calls[-1]
        assert "embedding=excluded.embedding" in sql and params[3:5] == ("pinned-model", 2)
        assert decode_vector(params[5]) == decode_vector(encode_vector((.1,.2))[0])
    asyncio.run(run())


def test_little_endian_float32_source_encoding_and_shortest_literal():
    raw, literal = encode_vector((.1, 1e-8, -0.0, 12345678))
    assert raw[:4] == b'\xcd\xcc\xcc=' and literal == '[0.1,0.00000001,-0,12345678]'
    assert len(decode_vector(raw)) == 4 and decode_vector(b'bad') == ()
    with pytest.raises(ValueError): encode_vector((float('nan'),))


def test_extraction_checkpoint_lease_revision_timestamp_id_and_failure_evidence():
    async def run():
        progress = dict(session_id="session", revision=2, cursor_at=NOW, cursor_id="a", failure_count=2, failed_from_at=NOW, failed_from_id="a", failed_at=None)
        db = SQLFixture([[progress]])
        original_execute = db.execute
        async def execute(sql, params=()):
            if sql.startswith("SELECT *,now() AS server_now"):
                db.calls.append((sql, tuple(params)))
                return Cursor([subject(extraction_state={"lease_id":"worker", "lease_until":(NOW+timedelta(seconds=600)).isoformat()})])
            return await original_execute(sql,params)
        db.execute = execute; repo, db = await ready(db)
        await repo.checkpoint_extraction(SCOPE, "worker", ExtractionSession("session", MessageCursor(NOW,"a"),1), MessageCursor(NOW,"b"),True)
        sql, params = db.calls[-1]
        assert params[:3] == (NOW,"b",True) and "failure_count=CASE WHEN failed_at IS NULL" in sql
        db.answers = [[progress]]
        assert await repo.record_extraction_failure(SCOPE,"worker",ExtractionSession("session"),MessageCursor(NOW,"b"),"invalid_model_output")
        assert db.calls[-1][1][:2] == (3,"invalid_model_output") and db.calls[-1][1][6] == NOW
        with pytest.raises(MemoryWriteError, match="LEASE_LOST"):
            await repo.checkpoint_extraction(SCOPE,"other",ExtractionSession("session"),MessageCursor(NOW,"b"),True)
    asyncio.run(run())


def test_queue_claim_uses_source_batch_order_and_no_bootstrap_reactivation_on_replay():
    async def run():
        progress = dict(session_id="session", revision=1, cursor_at=None, cursor_id="")
        repo, db = await ready(SQLFixture([[progress]]))
        batch = await repo.claim_pending_sessions(SCOPE,"session","worker",600)
        assert batch.sessions == (ExtractionSession("session"),)
        assert any("ORDER BY updated_at ASC,session_id ASC LIMIT 32" in sql for sql,_ in db.calls)
        assert any("ON CONFLICT(tenant_id,subject_id,session_id) DO NOTHING" in sql for sql,_ in db.calls)
        assert any("extraction_state=%s::jsonb" in sql and json.loads(params[0])["lease_id"] == "worker" for sql,params in db.calls)
    asyncio.run(run())


def test_history_owner_session_filters_before_limit_and_cursor_uses_both_fields():
    async def run():
        repo, db = await ready(SQLFixture([[], [], []]))
        await repo.recent_messages(SCOPE,"session",20)
        await repo.messages_after_cursor(SCOPE,"session",MessageCursor(NOW,"cursor-id"),41)
        await repo.messages_before_time(SCOPE,"session",NOW,16)
        for sql, params in db.calls[1:]:
            assert "tenant_id=%s AND subject_id=%s AND session_id=%s" in sql and params[:3] == (7,SCOPE.subject_id,"session")
        assert "created_at=%s AND id>%s" in db.calls[2][0] and db.calls[2][1][-2:] == ("cursor-id",41)
    asyncio.run(run())


def test_memory_migration_checksum_source_manifest_and_no_common_table_creation():
    root = Path(__file__).resolve().parents[2] / 'backend/weknora_port/storage/migrations'
    sql = (root/'002_memory.sql').read_text(encoding='utf-8')
    manifest = json.loads((root/'source_manifest_memory.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(sql.encode()).hexdigest() == manifest['generated_sha256']
    assert manifest['upstream'] == '3e8b0bfc80b845b2d4b2ed683994748741450a97' and manifest['database_writes'] == 0
    assert len(manifest['blocks']) == 3 and 'memory_extraction_sessions' in sql
    assert 'cache_revision' in sql and 'memory_operations' in sql
    assert 'CREATE TABLE IF NOT EXISTS tenants' not in sql and 'CREATE TABLE IF NOT EXISTS messages' not in sql
    assert "to_regclass('public.tenants')" in sql and 'HNSW' in sql and 'CREATE INDEX' in sql


def test_task_enqueuer_bound_scope_maxretry_partial_queued_dedup_and_running_successor():
    async def run():
        repo, db = await ready()
        queue = PostgresExtractionEnqueuer(repo)
        payload = ExtractionPayload(SCOPE,"session","message","model","Chinese")
        await queue.enqueue(payload,15,2)
        sql, params = db.calls[-1]
        assert "WHERE state='queued' DO NOTHING" in sql and params[1:3] == (7,SCOPE.subject_id)
        value = json.loads(params[4]); assert value['tenant_id'] == 7 and value['subject_id'] == SCOPE.subject_id
        assert params[-2:] == (15,2) and db.events == ['BEGIN','COMMIT']
        with pytest.raises(ValueError): await queue.enqueue(payload,15,3)
        assert len(db.events) == 2
    asyncio.run(run())


def test_task_claim_owner_scope_lease_and_ack_fencing_and_payload_discard():
    async def run():
        value = dict(tenant_id=7,subject_id=SCOPE.subject_id,session_id='session',message_id='message')
        first = dict(id='job',attempts=0,max_retry=2,payload=value)
        claimed = dict(first,attempts=1,lease_until=NOW+timedelta(seconds=600))
        repo, db = await ready(SQLFixture([[first],[claimed],[{'id':'job'}],[]]))
        queue = PostgresExtractionEnqueuer(repo)
        job = await queue.claim_due(SCOPE,'worker')
        assert job.payload == ExtractionPayload(SCOPE,'session','message') and job.attempts == 1
        assert any('FOR UPDATE SKIP LOCKED' in sql and 'lease_until<=now()' in sql for sql,_ in db.calls)
        await queue.complete_job(job)
        assert "payload='{}'::jsonb" in db.calls[-1][0] and 'lease_until>now()' in db.calls[-1][0]
        with pytest.raises(MemoryWriteError,match='LEASE_LOST'): await queue.complete_job(job)
        assert db.events[-1] == 'ROLLBACK'
    asyncio.run(run())


def test_task_retry_time_supplied_by_runtime_bounded_budget_and_successor_preservation():
    async def run():
        payload = ExtractionPayload(SCOPE,'session','message')
        job = MemoryExtractionJob('job',payload,'worker',NOW+timedelta(seconds=600),1,2)
        current = dict(id='job',attempts=1,max_retry=2,job_key='key')
        repo, db = await ready(SQLFixture([[current],[]]))
        queue = PostgresExtractionEnqueuer(repo)
        retry_at = NOW+timedelta(seconds=30)
        await queue.fail_job(job,retry_at=retry_at)
        assert db.calls[-1][1][:2] == ('queued',retry_at)
        db.answers = [[current],[{'id':'successor'}]]
        await queue.fail_job(job,retry_at=retry_at)
        assert db.calls[-1][1][0] == 'completed'
        db.answers = [[dict(current,attempts=3)]]
        await queue.fail_job(job,retry_at=retry_at)
        assert "state='failed'" in db.calls[-1][0] and "payload='{}'::jsonb" in db.calls[-1][0]
        db.answers = [[dict(current,attempts=3)]]
        assert await queue.claim_due(SCOPE,'next-worker') is None
        assert "delivery_exhausted" in db.calls[-1][0]
    asyncio.run(run())


def test_topic_aliases_keep_last_twelve_normalized_distinct_and_rename_removes_self_alias():
    async def run():
        stat = dict(id='topic-id',tenant_id=7,subject_id=SCOPE.subject_id,topic='old label',normalized_key='oldlabel',hits=2,aliases=['alias'+str(i) for i in range(12)],promoted_at=None)
        saved = dict(stat,hits=3,aliases=stat['aliases'][1:]+['new wording'])
        repo, db = await ready(SQLFixture([[],[stat],[saved]]))
        result = await repo.bump_topic(SCOPE,'old label','oldlabel','new wording','op')
        assert result.aliases[0] == 'alias1' and result.aliases[-1] == 'new wording'
        params = next(params for sql,params in db.calls if sql.startswith('UPDATE memory_topic_stats SET hits'))
        assert json.loads(params[0]) == saved['aliases']
        assert db.calls[-1][1][-2:] == ('bump_topic','topic-id')
    asyncio.run(run())


def test_deletion_suppresses_actual_locked_content_when_pre_read_snapshot_is_stale():
    async def run():
        from backend.weknora_port.conversation.management import fingerprint
        repo, db = await ready(SQLFixture([[dict(row(),content='edited private content')]]))
        await repo.delete_item(SCOPE,'old',MemoryTombstone('topic',fingerprint('old content'),'source'))
        hashes = [params[4] for sql,params in db.calls if sql.startswith('INSERT INTO memory_tombstones')]
        assert hashes == [fingerprint('edited private content'),fingerprint('old content')]
        assert db.events == ['BEGIN','COMMIT']
    asyncio.run(run())


def test_bulk_clear_collects_latest_priority_tombstones_under_same_lock_then_deletes_vectors():
    async def run():
        from backend.weknora_port.conversation.management import fingerprint
        repo, db = await ready(SQLFixture([[{'topic':'topic','content':'latest private','source_message_id':'user'}],[{'id':'one'},{'id':'two'}]]))
        assert await repo.delete_all(SCOPE,()) == 2
        statements = [sql for sql,_ in db.calls]
        locked = next(i for i,sql in enumerate(statements) if 'FOR UPDATE' in sql)
        current = next(i for i,sql in enumerate(statements) if 'SELECT topic,content' in sql)
        suppressed = next(i for i,sql in enumerate(statements) if sql.startswith('INSERT INTO memory_tombstones'))
        assert locked < current < suppressed
        assert "WHEN 'active' THEN 0 WHEN 'pending' THEN 1" in statements[current]
        assert next(params for sql,params in db.calls if sql.startswith('INSERT INTO memory_tombstones'))[4] == fingerprint('latest private')
        assert any(sql.startswith('DELETE FROM memory_item_embeddings') for sql in statements)
        assert db.events == ['BEGIN','COMMIT']
    asyncio.run(run())
