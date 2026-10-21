"""Synthetic durable-store and model fixtures; no PG/provider/service calls."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import re

import pytest

from test_conversation import CONTEXT, NOW, SCOPE, Embedder, Repository, item, message
from backend.weknora_port.conversation import ExtractionBatch, ExtractionPayload, ExtractionSession, ExtractionWorker, InvalidExtractionOutput, MemoryConfig, MemoryManager, MemoryMaintenance, MemoryService, MessageCursor, ModelResponse, TopicService, TopicStat
from backend.weknora_port.conversation.extraction import build_extraction_prompt, expiry
from backend.weknora_port.conversation.maintenance import embeddable_text, merge_candidates
from backend.weknora_port.conversation.prompts import EXTRACTION_SYSTEM, EXTRACTION_SCHEMA_JSON, CONSOLIDATION_SYSTEM, CONSOLIDATION_SCHEMA_JSON, TOPIC_SYSTEM, TOPIC_SCHEMA_JSON
from backend.weknora_port.conversation.topics import improved_label, topic_key, topic_similarity
from backend.weknora_port.conversation.turns import detect_explicit_memory, record_turn_memory


class DurableFixture(Repository):
    def __init__(self, rows=(), cfg=None):
        super().__init__(rows, cfg or MemoryConfig(enabled=True, write_mode="auto"))
        self.sessions, self.pending, self.lease, self.scheduled = {}, set(), "", False
        self.lease_until = NOW
        self.failures, self.operations, self.topics, self.vectors = {}, {}, {}, {}

    async def enqueue_pending_session(self, scope, session_id, timeout):
        self.record("enqueue_pending", scope, session_id, timeout)
        if session_id:
            old = self.sessions.get(session_id, ExtractionSession(session_id, revision=0))
            self.sessions[session_id] = replace(old, revision=old.revision + 1); self.pending.add(session_id)
        should = bool(self.pending) and not self.scheduled and not self.lease
        if should: self.scheduled = True
        return self.subject, should

    async def claim_pending_sessions(self, scope, fallback, lease, ttl):
        if self.lease and self.lease_until > NOW: return ExtractionBatch(retry_at=self.lease_until)
        if fallback not in self.sessions:
            self.sessions[fallback] = ExtractionSession(fallback); self.pending.add(fallback)
        if not self.pending: return None
        self.lease, self.lease_until = lease, NOW + timedelta(seconds=ttl)
        return ExtractionBatch(tuple(self.sessions[id] for id in sorted(self.pending)))

    def assert_lease(self, lease):
        if lease != self.lease or self.lease_until <= NOW: raise RuntimeError("LEASE_LOST")

    async def release_extraction_slot(self, scope, lease):
        self.record("release", scope, lease)
        if lease == self.lease: self.lease, self.scheduled = "", False

    async def checkpoint_extraction(self, scope, lease, session, end, complete):
        self.assert_lease(lease); self.record("checkpoint", scope, session.session_id, end, complete)
        current = self.sessions[session.session_id]
        self.sessions[session.session_id] = replace(current, cursor=end)
        if complete and current.revision == session.revision: self.pending.discard(session.session_id)

    async def record_extraction_failure(self, scope, lease, session, end, code):
        self.assert_lease(lease)
        key = (session.session_id, session.cursor)
        self.failures[key] = self.failures.get(key, 0) + 1
        self.record("failure", code, self.failures[key])
        return self.failures[key] >= 3

    async def finish_extraction(self, scope, lease):
        self.assert_lease(lease); self.lease, self.scheduled = "", False
        self.subject = replace(self.subject, last_extracted_at=NOW)

    async def has_pending_extraction(self, scope): return bool(self.pending)

    async def messages_after_cursor(self, scope, session, cursor, limit):
        self.record("after", scope, session, cursor, limit)
        return sorted([r for r in self.messages if r.session_id == session and (cursor.at is None or (r.created_at, r.id) > (cursor.at, cursor.id))], key=lambda r: (r.created_at, r.id))[:limit]

    async def messages_before_time(self, scope, session, at, limit):
        return [r for r in self.messages if r.session_id == session and r.created_at < at][-limit:]

    async def list_tombstones(self, scope, limit): return []

    async def save_item(self, scope, row, target):
        if row.operation_id and row.operation_id in self.operations: return self.rows[self.operations[row.operation_id]]
        saved = await super().save_item(scope, row, target)
        if row.operation_id: self.operations[row.operation_id] = saved.id
        return saved

    async def supersede_item(self, scope, id, replacement, operation_id=""):
        if operation_id and operation_id in self.operations: return
        row = await self.get_item(scope, id)
        self.rows[id] = replace(row, status="superseded", superseded_by=replacement)
        if operation_id: self.operations[operation_id] = id
        self.invalidate_block()

    async def expire_overdue(self, scope):
        expired = [r for r in self.scoped(scope) if r.status == "active" and r.expires_at and r.expires_at <= NOW]
        for row in expired: self.rows[row.id] = replace(row, status="archived")
        if expired: self.invalidate_block()
        return len(expired)

    async def mark_consolidated(self, scope, forced):
        self.subject = replace(self.subject, **{"forced_consolidated_at" if forced else "consolidated_at": NOW})

    async def item_embeddings(self, scope, ids, model): return {id: self.vectors[(id, model)] for id in ids if (id, model) in self.vectors}
    async def items_missing_embeddings(self, scope, model, limit):
        self.record("missing", scope, model, limit)
        return [r for r in self.active(scope) if (r.id, model) not in self.vectors][:limit]
    async def upsert_item_embedding(self, scope, row, model, vector):
        current = await self.get_item(scope, row.id)
        if current is None or current.status not in ("active", "pending") or (current.content, current.topic) != (row.content, row.topic): return
        self.vectors[(row.id, model)] = vector
    async def sync_vector_column(self, scope, limit): self.record("sync", scope, limit); return 0
    async def delete_item_embedding(self, scope, id):
        self.vectors = {key: value for key, value in self.vectors.items() if key[0] != id}
    async def top_topics(self, scope, limit):
        return [r for r in self.topics.values() if (r.tenant_id, r.subject_id) == (scope.tenant_id, scope.subject_id)][:limit]
    async def topic_by_id(self, scope, id): return next((r for r in await self.top_topics(scope, 40) if r.id == id), None)
    async def topic_by_key(self, scope, key): return next((r for r in await self.top_topics(scope, 40) if r.normalized_key == key), None)
    async def bump_topic(self, scope, label, key, alias, operation):
        if operation and operation in self.operations: return await self.topic_by_key(scope, key)
        old = await self.topic_by_key(scope, key) or TopicStat(key, scope.tenant_id, scope.subject_id, label, key)
        aliases = tuple(dict.fromkeys((*old.aliases, alias)))[:12]
        row = replace(old, hits=old.hits + 1, aliases=aliases)
        self.topics[row.id] = row
        if operation: self.operations[operation] = key
        return row
    async def mark_topic_promoted(self, scope, key):
        row = await self.topic_by_key(scope, key); self.topics[row.id] = replace(row, promoted_at=NOW)
    async def rename_topic(self, scope, old_key, new_key, label):
        row = await self.topic_by_key(scope, old_key)
        if await self.topic_by_key(scope, new_key): return False
        self.topics[row.id] = replace(row, topic=label, normalized_key=new_key); return True
    async def delete_topic(self, scope, id, tombstones):
        for row in tombstones: self.tombstones.add((scope, row.fingerprint))
        self.topics.pop(id); self.invalidate_block()
    async def bump_doc_affinity(self, scope, refs, operation):
        self.record("record_sources", scope, refs, operation)


class Model:
    def __init__(self, responses=()): self.responses, self.calls = list(responses), []
    async def workspace_chat_model(self, scope): return "workspace-model"
    async def complete(self, request):
        self.calls.append(request)
        response = self.responses.pop(0) if self.responses else ModelResponse('{"memories":[],"topics":[]}')
        if isinstance(response, Exception): raise response
        return response


class Queue:
    def __init__(self, fail=False): self.calls, self.fail = [], fail
    async def enqueue(self, payload, delay, max_retry):
        self.calls.append((payload, delay, max_retry))
        if self.fail: raise RuntimeError("QUEUE_UNAVAILABLE")


def setup(repo=None, model=None, queue=None):
    repo = repo or DurableFixture(); reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
    topics = TopicService(repo, manager, model)
    return repo, manager, ExtractionWorker(repo, manager, model, queue, topics), topics


def test_extraction_schedule_coalesces_delays_without_losing_turn_and_enqueue_failure_releases():
    async def run():
        repo, _, worker, _ = setup(queue=Queue())
        repo.subject = replace(repo.subject, last_extracted_at=NOW - timedelta(seconds=20))
        assert await worker.schedule(CONTEXT, "session", "first", "conversation-model")
        assert worker.enqueuer.calls[0][1:] == (280, 2)
        assert not await worker.schedule(CONTEXT, "session", "second")
        assert repo.sessions["session"].revision == 2
        repo.scheduled = False; worker.enqueuer = Queue(fail=True)
        assert not await worker.schedule(CONTEXT, "session", "third")
        assert not repo.scheduled and "session" in repo.pending
    asyncio.run(run())


def test_extraction_user_only_segments_prior_context_timestamp_id_and_assistant_checkpoint():
    async def run():
        repo, _, worker, _ = setup()
        repo.messages = [message("user", "prior", "context", minutes=-1), message("user", "one", "a" * 1100), message("assistant", "two", "never extract", minutes=2), message("user", "three", "later", minutes=61)]
        segments, more = await worker.collect(SCOPE, ExtractionSession("session", MessageCursor(NOW - timedelta(seconds=30), "")))
        assert not more and len(segments) == 2
        assert segments[0].context == ("context",) and len(segments[0].lines[0].content) == 1000
        assert segments[0].end.id == "two-assistant" and segments[1].context == ("a" * 1000,)
        assert "never extract" not in build_extraction_prompt(segments[0], [], [], [])
        repo.messages = [message("assistant", "only", "assistant")]
        segments, _ = await worker.collect(SCOPE, ExtractionSession("session"))
        assert len(segments) == 1 and not segments[0].lines and segments[0].end.id == "only-assistant"
    asyncio.run(run())


def test_extraction_truncated_once_larger_budget_pending_source_provenance_and_duplicate_task():
    async def run():
        model = Model([ModelResponse("", "length"), ModelResponse('{"memories":[{"action":"add","kind":"profile","topic":"role","content":"user manages projects","importance":4,"source":1,"inferred":true}],"topics":[]}')])
        repo, _, worker, _ = setup(model=model, queue=Queue())
        repo.messages = [message("user", "one", "source user")]
        await worker.schedule(CONTEXT, "session", "one-user", "conversation-model")
        payload = worker.enqueuer.calls[0][0]; await worker.handle(payload)
        assert [r.max_completion_tokens for r in model.calls] == [1200, 4000]
        assert all(r.model_id == "conversation-model" and r.thinking is False and r.temperature == 0 for r in model.calls)
        row = next(iter(repo.rows.values()))
        assert row.status == "pending" and row.source_message_id == "one-user" and row.operation_id
        assert not repo.pending and repo.sessions["session"].cursor.id == "one-user"
        await worker.handle(payload)
        assert len(model.calls) == 2 and len(repo.rows) == 1
    asyncio.run(run())


def test_extraction_failures_keep_cursor_until_recorded_third_invalid_skip_and_no_general_retry():
    async def run():
        model = Model([ModelResponse("bad")] * 3)
        repo, _, worker, _ = setup(model=model)
        repo.messages = [message("user", "one", "source")]
        payload = ExtractionPayload(SCOPE, "session", "one-user")
        for _ in range(2):
            with pytest.raises(InvalidExtractionOutput): await worker.handle(payload)
            assert repo.sessions["session"].cursor.at is None
        await worker.handle(payload)
        assert repo.sessions["session"].cursor.id == "one-user" and len(model.calls) == 3
        repo, _, worker, _ = setup(model=Model([RuntimeError("provider failed")]))
        repo.messages = [message("user", "one", "source")]
        with pytest.raises(RuntimeError): await worker.handle(payload)
        assert repo.sessions["session"].cursor.at is None and len(worker.model.calls) == 1 and not repo.lease
    asyncio.run(run())


def test_extraction_three_segment_cap_followup_and_changed_revision_keeps_pending():
    async def run():
        repo, _, worker, _ = setup(model=Model(), queue=Queue())
        repo.messages = [message("user", str(i), "source", minutes=i * 61) for i in range(4)]
        await worker.handle(ExtractionPayload(SCOPE, "session", "0-user"))
        assert len(worker.model.calls) == 3 and "session" in repo.pending and worker.enqueuer.calls[-1][1] == 15
        assert repo.sessions["session"].cursor.id == "2-user"
        repo.lease = "held"; repo.lease_until = NOW + timedelta(seconds=60)
        await worker.handle(ExtractionPayload(SCOPE, "session", "0-user"))
        assert worker.enqueuer.calls[-1][1] == 61 and len(worker.model.calls) == 3
        session = repo.sessions["session"]
        await repo.enqueue_pending_session(SCOPE, "session", 990)
        await repo.checkpoint_extraction(SCOPE, "held", session, session.cursor, True)
        assert "session" in repo.pending
    asyncio.run(run())


def test_consolidation_requires_model_judgement_and_backfills_in_pinned_space():
    async def run():
        rows = [item(str(i), "database common " + str(i), topic="database") for i in range(6)]
        repo, manager, _, _ = setup(DurableFixture(rows, MemoryConfig(enabled=True, write_mode="auto", embedding_model_id="embed-model")))
        manager.reader.embedder = Embedder()
        maintenance = MemoryMaintenance(repo, manager)
        result = await maintenance.review(CONTEXT, force=True)
        assert result.skipped == "model_unavailable" and result.merged == 0 and len(repo.active(SCOPE)) == 6
        assert result.backfilled == 6 and ("missing", SCOPE, "embed-model", 200) in repo.calls and ("sync", SCOPE, 2000) in repo.calls
        assert all(model == "embed-model" for _, model in repo.vectors)
        assert (await maintenance.review(CONTEXT, force=True)).skipped == "too_soon"
    asyncio.run(run())


def test_consolidation_model_merge_provenance_expiry_demotion_and_two_clocks():
    async def run():
        rows = [item("one", "database common alpha", topic="database"), item("two", "database common beta", topic="database"), item("expired", "expired", expires_at=NOW), replace(item("stale", "stale task", kind="task"), valid_from=NOW - timedelta(days=46))]
        repo, manager, _, _ = setup(DurableFixture(rows))
        model = Model([ModelResponse('{"statement":"database common merged"}')])
        maintenance = MemoryMaintenance(repo, manager, model)
        result = await maintenance.review(CONTEXT, "chat", force=True)
        assert (result.expired, result.demoted, result.merged) == (1, 1, 1)
        assert repo.rows["expired"].status == "archived" and repo.rows["stale"].importance == 1
        assert all(repo.rows[id].status == "superseded" for id in ("one", "two"))
        assert model.calls[0].max_completion_tokens == 600 and model.calls[0].system == CONSOLIDATION_SYSTEM
        assert model.calls[0].user.startswith("- (2026-10-13)")
        assert repo.subject.consolidated_at == NOW and repo.subject.forced_consolidated_at == NOW
        assert (await maintenance.review(CONTEXT)).reviewed == 0
    asyncio.run(run())


def test_topic_resolution_exact_fuzzy_model_guard_promotion_replay_and_forgetting_aliases():
    async def run():
        model = Model([ModelResponse('{"resolutions":[{"index":0,"same_as":0,"label":"平台"}]}')])
        repo, manager, _, topics = setup(model=model)
        label = "门店排班管理"; key = topic_key(label)
        repo.topics[key] = TopicStat(key, 1, SCOPE.subject_id, label, key, aliases=("店员班次安排",))
        resolutions = await topics.resolve(SCOPE, "chat", ["店员班次安排", "门店排班管理问题", "安排工作人员轮班"])
        assert resolutions[0][2] == "exact" and resolutions[1][2] == "exact"
        assert resolutions[2][1] is None  # model index0 cannot overwrite an earlier exact match
        for i in range(3): await topics.observe(CONTEXT, [label], operation_id=f"run{i}")
        row = await repo.topic_by_key(SCOPE, key)
        assert row.hits == 3 and row.promoted_at == NOW and any(r.kind == "interest" for r in repo.rows.values())
        await topics.observe(CONTEXT, [label], operation_id="run2")
        assert (await repo.topic_by_key(SCOPE, key)).hits == 3
        new = TopicStat("other", 1, SCOPE.subject_id, "PostgreSQL备份恢复", topic_key("PostgreSQL备份恢复"), ("数据库恢复流程",))
        repo.topics[new.id] = new
        await topics.delete(CONTEXT, new.id)
        await topics.observe(CONTEXT, ["数据库恢复流程"], operation_id="forgotten")
        assert not any(r.topic == "数据库恢复流程" for r in repo.topics.values())
    asyncio.run(run())


def test_source_prompts_are_exact_and_candidate_grouping_is_primary_not_transitive():
    root = Path(r"E:\RAG-reference\WeKnora-3e8b0bfc\internal\application\service\memory")
    if not root.exists(): pytest.skip("Optional fixed-source reference unavailable")
    for file, variable, actual in [("extract.go", "extractionSystemPrompt", EXTRACTION_SYSTEM), ("extract.go", "extractionSchema", EXTRACTION_SCHEMA_JSON), ("consolidate.go", "consolidationSystemPrompt", CONSOLIDATION_SYSTEM), ("consolidate.go", "consolidationSchema", CONSOLIDATION_SCHEMA_JSON), ("topic_resolve.go", "topicAdjudicationPrompt", TOPIC_SYSTEM), ("topic_resolve.go", "topicAdjudicationSchema", TOPIC_SCHEMA_JSON)]:
        source = (root / file).read_text(encoding="utf-8")
        assert re.search(r"(?:const|var) " + variable + r" = (?:json.RawMessage\()?`([\s\S]*?)`", source).group(1) == actual
    rows = [item("a", "alpha beta"), item("b", "alpha beta gamma"), item("c", "beta gamma")]
    assert [[r.id for r in cluster] for cluster in merge_candidates(rows, {}, .6, .86)] == [["a", "b"]]
    assert not improved_label("门店排班管理", "店员班次安排", "门店")
    assert topic_similarity("门店的排班管理", "门店排班管理") == 1
    assert not topic_key("的了")
    assert expiry("2026-10-20", NOW) and expiry("2026-10-20T00:00:00Z", NOW)
    assert expiry("20261020", NOW) is None and expiry("2026-10-20 00:00:00+00:00", NOW) is None
    assert expiry("2026-10-12", NOW) is None
    assert embeddable_text(item("interest", "topic", kind="interest", topic="topic"), ("topic", "alias", "alias")) == "topic；alias"


def test_post_turn_explicit_memory_uses_user_provenance_and_auto_gate():
    async def run():
        repo, manager, worker, _ = setup(DurableFixture(cfg=MemoryConfig(enabled=True, write_mode="explicit_only")), queue=Queue())
        await record_turn_memory(worker, CONTEXT, "session", "assistant", "请记住：生产数据库使用 PostgreSQL", "user-message", "chat")
        row = next(iter(repo.rows.values()))
        assert row.origin == "explicit" and row.importance == 4 and row.source_message_id == "user-message"
        assert row.source_session_id == "session" and row.content == "生产数据库使用 PostgreSQL"
        assert not worker.enqueuer.calls and not repo.sessions
        assert detect_explicit_memory("Remember that I use Go") == "I use Go"
        assert not detect_explicit_memory("记住：") and not detect_explicit_memory("general question")
    asyncio.run(run())


def test_extraction_partial_replay_deduplicates_writes_and_cancel_releases_lease():
    async def run():
        repo, manager, worker, topics = setup(model=Model())
        repo.messages = [message("user", "one", "user manages projects")]
        segments, _ = await worker.collect(SCOPE, ExtractionSession("session"))
        decision = [{"action":"add", "kind":"profile", "topic":"role", "content":"user manages projects", "source":1}]
        await worker.apply(CONTEXT, segments[0], [], decision, "same-run")
        await worker.apply(CONTEXT, segments[0], [], decision, "same-run")
        assert len(repo.rows) == 1
        class CancelModel(Model):
            async def complete(self, request): raise asyncio.CancelledError()
        worker.model = CancelModel()
        with pytest.raises(asyncio.CancelledError): await worker.handle(ExtractionPayload(SCOPE, "session", "one-user"))
        assert not repo.lease and repo.sessions["session"].cursor.at is None
    asyncio.run(run())


def test_backfill_failure_stops_batch_and_late_embedding_cannot_restore_deleted_content():
    async def run():
        repo, manager, _, _ = setup(DurableFixture([item("a", "original"), item("b", "next")], MemoryConfig(enabled=True, embedding_model_id="pinned")))
        manager.reader.embedder = Embedder(error=True)
        assert await MemoryMaintenance(repo, manager).backfill(SCOPE, repo.cfg) == 0
        assert len(manager.reader.embedder.calls) == 1 and not repo.vectors
        snapshot = repo.rows["a"]
        await manager.delete(CONTEXT, snapshot.id)
        await repo.upsert_item_embedding(SCOPE, snapshot, "pinned", (.1,.2))
        assert not repo.vectors
    asyncio.run(run())


def test_extraction_semantic_candidates_extend_past_importance_prefix_and_missing_model_preserves_cursor():
    async def run():
        from backend.weknora_port.conversation import MemoryVectorHit
        rows = [item(str(i), "prefix " + str(i)) for i in range(16)]
        outside = item("outside", "relevant statement")
        repo, manager, worker, _ = setup(DurableFixture(rows, MemoryConfig(enabled=True, write_mode="auto", embedding_model_id="pinned")))
        manager.reader.embedder = Embedder(); repo.hits = [MemoryVectorHit(outside, .9)]
        repo.messages = [message("user", "one", "relevant")]
        segments, _ = await worker.collect(SCOPE, ExtractionSession("session"))
        relevant = await worker.relevant(SCOPE, repo.cfg, segments[0])
        assert [row.id for row in relevant] == ["outside"]
        query = next(call[2] for call in repo.calls if call[0] == "vector")
        assert query.model_id == "pinned" and query.limit == 15 and query.min_score == .5
        with pytest.raises(RuntimeError, match="MODEL_UNAVAILABLE"):
            await worker.handle(ExtractionPayload(SCOPE, "session", "one-user"))
        assert repo.sessions["session"].cursor.at is None and not repo.lease
    asyncio.run(run())


def test_background_rechecks_user_workspace_gates_and_reclaims_stale_lease():
    async def run():
        repo, _, worker, _ = setup(model=Model(), queue=Queue())
        await worker.schedule(CONTEXT, "session", "one-user")
        repo.subject = replace(repo.subject, enabled=False)
        await worker.handle(ExtractionPayload(SCOPE, "session", "one-user"))
        assert not worker.model.calls and not repo.scheduled and "session" in repo.pending
        repo.subject = replace(repo.subject, enabled=True)
        repo.lease, repo.lease_until = "dead-worker", NOW - timedelta(seconds=1)
        repo.messages = [message("assistant", "only", "answer is never extracted")]
        await worker.handle(ExtractionPayload(SCOPE, "session", "one-user"))
        assert not worker.model.calls and repo.sessions["session"].cursor.id == "only-assistant"
        assert not repo.lease and not repo.pending
    asyncio.run(run())
