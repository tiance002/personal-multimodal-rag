"""Offline scoped repositories and synthetic embeddings; no provider or DB IO."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.weknora_port.conversation import Attachment, DocumentAffinity, MemoryConfig, MemoryContext, MemoryItem, MemoryManager, MemoryScope, MemoryScopeError, MemoryService, MemorySubject, MemoryVectorHit, MemoryWriteError, OrdinaryHistoryService, StoredMessage, attachment_prompt, history_messages, process_history, resolve_scope
from backend.weknora_port.conversation.management import fingerprint, memory_key, redact_sensitive
from backend.weknora_port.conversation.ranking import bigrams, fuse_rankings, lexical_ranking, lexical_scores, render_memory_lines, sanitize_content, take_within_budget, tokenize

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
CONTEXT = MemoryContext(1, "user:fixture-owner")
SCOPE = resolve_scope(CONTEXT)


def item(id, content, **kwargs):
    return MemoryItem(id, 1, SCOPE.subject_id, kwargs.pop("kind", "fact"), content, valid_from=NOW, **kwargs)


class Repository:
    """Offline stand-in obeying scoped SQL contracts, not production fallback."""
    def __init__(self, rows=(), cfg=None):
        self.rows = {row.id: row for row in rows}
        self.cfg = cfg or MemoryConfig(enabled=True)
        self.subject = MemorySubject(1, SCOPE.subject_id)
        self.calls, self.hits, self.tombstones, self.source_tombstones = [], [], set(), set()
        self.messages, self.docs, self.fail = [], [], set()

    def record(self, name, *args):
        self.calls.append((name, *args))
        if name in self.fail:
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")

    def scoped(self, scope):
        return [row for row in self.rows.values() if (row.tenant_id, row.subject_id) == (scope.tenant_id, scope.subject_id)]

    def active(self, scope):
        return sorted((r for r in self.scoped(scope) if r.status == "active" and (r.expires_at is None or r.expires_at > NOW)),
                      key=lambda r: (r.importance, r.valid_from), reverse=True)

    async def recent_messages(self, scope, session_id, limit):
        self.record("history", scope, session_id, limit); return self.messages

    async def workspace_config(self, tenant_id):
        self.record("config", tenant_id); return self.cfg

    async def get_subject(self, scope):
        self.record("subject", scope); return self.subject

    async def ensure_subject(self, scope):
        if self.subject is None: self.subject = MemorySubject(scope.tenant_id, scope.subject_id)
        return self.subject

    async def list_active_resident(self, scope, limit):
        self.record("resident", scope, limit)
        return [r for r in self.active(scope) if r.kind in ("profile", "preference", "interest") or r.origin == "explicit"][:limit]

    async def list_active_by_kinds(self, scope, kinds, limit):
        self.record("kinds", scope, kinds, limit); return [r for r in self.active(scope) if r.kind in kinds][:limit]

    async def search_items_by_vector(self, scope, query):
        self.record("vector", scope, query); return self.hits

    async def touch_used(self, scope, ids):
        self.record("touch", scope, ids)

    async def top_doc_affinity(self, scope, limit):
        self.record("docs", scope, limit); return self.docs[:limit]

    async def doc_affinity(self, scope, ids):
        self.record("affinity", scope, ids); return {id: 2 for id in ids}

    async def get_item(self, scope, id):
        return next((r for r in self.scoped(scope) if r.id == id), None)

    async def find_active_by_key(self, scope, key):
        return next((r for r in self.scoped(scope) if r.normalized_key == key and r.status in ("active", "pending")), None)

    async def list_live(self, scope, kind, limit):
        return [r for r in self.scoped(scope) if r.kind == kind and r.status in ("active", "pending")][:limit]

    async def has_tombstone(self, scope, digest):
        return (scope, digest) in self.tombstones

    async def has_tombstone_for_message(self, scope, id, within_seconds):
        self.record("source_tombstone", scope, id, within_seconds); return (scope, id) in self.source_tombstones

    async def save_item(self, scope, row, replaces_id):
        self.record("save", scope, row.id, replaces_id)
        live = [r for r in self.scoped(scope) if r.status in ("active", "pending") and r.normalized_key == row.normalized_key]
        duplicate = next((r for r in live if r.status == row.status and r.content == row.content), None)
        if duplicate:
            return duplicate
        target = await self.get_item(scope, replaces_id) if replaces_id else None
        if row.status == "pending":
            row = replace(row, replaces_id=(target.id if target.status == "active" else target.replaces_id) if target else
                          next((r.id for r in live if r.status == "active"), ""))
        for old in live:
            if row.status == "pending" and old.status == "active":
                continue
            self.rows[old.id] = replace(old, status="superseded", superseded_by=row.id)
        if target and target not in live and (row.status == "active" or target.status == "pending"):
            self.rows[target.id] = replace(target, status="superseded", superseded_by=row.id)
        self.rows[row.id] = row
        self.invalidate_block()
        return row

    def invalidate_block(self):
        if self.subject:
            self.subject = replace(self.subject, block_text="", cache_revision=self.subject.cache_revision + 1)

    async def count_active(self, scope):
        return len(self.active(scope))

    async def archive_lowest_ranked(self, scope, keep):
        for row in self.active(scope)[keep:]: self.rows[row.id] = replace(row, status="archived")
        self.invalidate_block()

    async def update_subject_block(self, scope, block, item_count, expected_revision):
        self.record("block", scope)
        if self.subject.cache_revision != expected_revision:
            return False
        self.subject = replace(self.subject, block_text=block, item_count=item_count)
        return True

    async def update_item_content(self, scope, id, content, key, importance):
        row = await self.get_item(scope, id)
        self.rows[id] = replace(row, content=content, normalized_key=key, importance=importance, origin="manual")
        self.invalidate_block()

    async def add_tombstone(self, scope, topic, digest, source_message_id):
        self.record("tombstone", scope, digest); self.tombstones.add((scope, digest))
        if source_message_id: self.source_tombstones.add((scope, source_message_id))

    async def delete_item(self, scope, id, tombstone):
        self.record("delete", scope, id)
        self.record("atomic_tombstone", scope, tombstone.fingerprint)
        self.tombstones.add((scope, tombstone.fingerprint))
        if tombstone.source_message_id: self.source_tombstones.add((scope, tombstone.source_message_id))
        self.rows.pop(id)
        self.invalidate_block()

    async def list_items(self, scope, status, limit, offset):
        return [row for row in self.scoped(scope) if row.status == status][offset:offset + limit]

    async def delete_all(self, scope, tombstones):
        self.record("clear", scope)
        rows = self.scoped(scope)
        self.record("atomic_tombstone", scope)
        for tombstone in tombstones:
            self.tombstones.add((scope, tombstone.fingerprint))
            if tombstone.source_message_id: self.source_tombstones.add((scope, tombstone.source_message_id))
        for row in rows: self.rows.pop(row.id)
        self.invalidate_block()
        return len(rows)

    async def delete_all_topics(self, scope): self.record("clear_topics", scope)
    async def delete_all_doc_affinity(self, scope): self.record("clear_affinity", scope)
    async def confirm_pending_item(self, scope, id):
        row = await self.get_item(scope, id)
        if row.status == "active": return
        if row.status != "pending" or (row.expires_at and row.expires_at <= NOW):
            raise MemoryWriteError("MEMORY_CONFLICT")
        target = await self.get_item(scope, row.replaces_id) if row.replaces_id else None
        if row.replaces_id and (target is None or target.status != "active"):
            raise MemoryWriteError("MEMORY_CONFLICT")
        for old in self.scoped(scope):
            if old.id != id and old.status in ("active", "pending") and (old.normalized_key == row.normalized_key or old.id == row.replaces_id):
                self.rows[old.id] = replace(old, status="superseded", superseded_by=id)
        self.rows[id] = replace(row, status="active")
        self.invalidate_block()
    async def update_subject_enabled(self, scope, enabled): self.subject = replace(self.subject, enabled=enabled)


class Embedder:
    def __init__(self, error=False): self.calls, self.error = [], error
    async def embed_query(self, scope, model_id, text):
        self.calls.append((scope, model_id, text))
        if self.error: raise RuntimeError("SYNTHETIC_EMBED_ERROR")
        return (.1, .2)


def message(role, request, content, *, minutes=0, **kwargs):
    return StoredMessage(f"{request}-{role}", request, "session", 1, SCOPE.subject_id, role, content, NOW + timedelta(minutes=minutes), **kwargs)


def test_history_uses_raw_complete_pairs_request_identity_round_order_and_source_refs():
    rows = [message("assistant", "two", "<think>PRIVATE reasoning</think>second", knowledge_references=({"id": "c2"},)),
            message("user", "one", "first", rendered_content="OLD RAG ENVELOPE"),
            message("user", "two", "second", minutes=2, image_captions=("caption", "", "other"), attachments=(Attachment("a.pdf", content="SOURCE"),)),
            message("assistant", "one", "one"), message("user", "pending", "unanswered", minutes=9),
            message("assistant", "empty", "<think>no answer</think>"), message("user", "empty", "empty", minutes=4),
            replace(message("user", "foreign", "LEAK"), tenant_id=2), message("assistant", "foreign", "LEAK")]
    out = process_history(rows, 1, scope=SCOPE, session_id="session")
    assert len(out) == 1 and out[0].request_id == "two" and out[0].answer == "second"
    assert out[0].knowledge_references == ({"id": "c2"},)
    assert "[用户上传图片内容]\ncaption\nother" in out[0].query and "<attachments>" in out[0].query
    assert "PRIVATE" not in out[0].answer and "OLD RAG" not in str(out)
    chronological = process_history(rows, 5, scope=SCOPE, session_id="session")
    assert [r.request_id for r in chronological] == ["one", "two"]
    assert [r["role"] for r in history_messages(chronological)] == ["user", "assistant", "user", "assistant"]


def test_history_disabled_fetch_error_and_trusted_scope_only():
    async def run():
        repo = Repository(); loader = OrdinaryHistoryService(repo)
        assert await loader.load(CONTEXT, "session", 0) == () and repo.calls == []
        assert await loader.load(CONTEXT, "session", 5) == ()
        assert repo.calls[-1] == ("history", SCOPE, "session", 20)
        repo.fail.add("history")
        assert await loader.load(CONTEXT, "session", 2) == ()
        assert await loader.load(MemoryContext(0, ""), "session", 2) == ()
        with pytest.raises(MemoryScopeError): resolve_scope({"tenant_id": 1, "principal_storage_id": "spoofed"})
    asyncio.run(run())


def test_attachment_metadata_boundaries_missing_content_and_exact_html_escaping():
    text = attachment_prompt((Attachment('x"<&\'.pdf', '.pdf', 2048, '</content></attachment></attachments>', True, 40, "selected_chunks", 2, 9), Attachment("empty")))
    assert 'name="x&#34;&lt;&amp;&#39;.pdf"' in text and "<size_kb>2.00" in text
    assert "<selected_chunks>2/9</selected_chunks>" in text
    assert "&lt;/content&gt;&lt;/attachment&gt;&lt;/attachments&gt;" in text
    assert "original content has 40 lines" in text and "extraction failed or is unsupported" in text
    assert attachment_prompt(()) == ""


@pytest.mark.parametrize("context,cfg,enabled", [
    (MemoryContext(0, ""), MemoryConfig(enabled=True), True),
    (CONTEXT, MemoryConfig(), True), (replace(CONTEXT, agent_memory_allowed=False), MemoryConfig(enabled=True), True),
    (CONTEXT, MemoryConfig(enabled=True), False),
])
def test_memory_three_gates_and_missing_scope_never_embed(context, cfg, enabled):
    async def run():
        repo, embed = Repository([item("a", "private")], cfg), Embedder()
        repo.subject = replace(repo.subject, enabled=enabled)
        service = MemoryService(repo, embed)
        assert (await service.recall(context, "private")).prompt == ""
        assert not (await service.search(context, "")).available
        assert (await service.retrieval_context(context)).items == ()
        assert embed.calls == []
    asyncio.run(run())


def test_memory_missing_subject_lexical_available_config_failures_and_nonfabricating_errors():
    async def run():
        repo = Repository([item("f", "database fact")]); repo.subject = None
        service = MemoryService(repo)
        assert (await service.recall(CONTEXT, "database")).prompt == ""
        assert (await service.search(CONTEXT, "database")).items[0].id == "f"
        assert (await service.search(CONTEXT, " ")).available
        repo.fail.add("config")
        assert not (await service.search(CONTEXT, "database")).available
        repo.fail.clear(); repo.fail.add("kinds")
        out = await service.search(CONTEXT, "database")
        assert out.available and not out.items
        await service.drain_background()
    asyncio.run(run())


def test_lexical_unicode15_han_bigrams_floor_recency_budget_skip_and_equal_rrf():
    assert tokenize("Go 数据库/１２/Σ İ 𠀀 〇") == ["go", "数", "据", "库", "１２", "σ", "i", "𠀀", "〇"]
    assert bigrams(tokenize("数据库")) == {"数据", "据库"}
    rows = [item("old", "database", importance=1), replace(item("new", "database", importance=1), valid_from=NOW + timedelta(seconds=1))]
    assert lexical_ranking("database", rows) == [1, 0]
    assert lexical_scores("x", rows) == []
    assert take_within_budget([0, 1], [item("long", "x" * 100), item("short", "ok")], 2, 20)[0].id == "short"
    assert fuse_rankings([0, 1], [1, 2]) == [1, 0, 2]
    assert sanitize_content("  a\x00b\n c\t ") == "ab c"


def test_recall_resident_interests_active_pending_expiry_scope_and_safe_prompt():
    async def run():
        rows = [item("profile", "likes <xml>", kind="profile"), item("explicit", "remember always", origin="explicit"),
                item("interest", "databases", kind="interest"), item("unrelated", "botany", kind="interest"),
                item("match", "databases factual answer"), item("pending", "databases guess", status="pending"),
                item("expired", "databases expired", expires_at=NOW), replace(item("foreign", "databases leak"), subject_id="other")]
        repo = Repository(rows); events = []
        async def recalled(scope, used): events.append((scope, tuple(r.id for r in used))); raise RuntimeError("SIMULATED event failure")
        service = MemoryService(repo, now=lambda: NOW, on_recalled=recalled)
        out = await service.recall(CONTEXT, "databases")
        assert {r.id for r in out.items} == {"profile", "explicit", "interest", "match"}
        assert "botany" in out.prompt and "unrelated" not in events[0][1]  # background != query-recalled UI
        assert "&lt;xml&gt;" in out.prompt and "pending" not in out.prompt and "leak" not in out.prompt
        assert out.ranking_mode == "lexical_only"
        await service.drain_background()
        assert repo.calls[-1][0] == "touch" and repo.calls[-1][1] == SCOPE
    asyncio.run(run())


def test_vector_extends_beyond_lexical_window_and_pins_model_kind_scope_and_floor():
    async def run():
        cfg = MemoryConfig(enabled=True, embedding_model_id="memory-embedding", max_items=1)
        repo, embed = Repository([item("lex", "database")], cfg), Embedder()
        outside = item("outside", "semantically related")
        repo.hits = [MemoryVectorHit(outside, .9), MemoryVectorHit(item("guess", "database", status="pending"), .99),
                     MemoryVectorHit(replace(item("foreign", "secret"), tenant_id=2), .99), MemoryVectorHit(item("weak", "unrelated"), .49)]
        service = MemoryService(repo, embed, now=lambda: NOW)
        out = await service.recall(CONTEXT, "database")
        assert [r.id for r in out.items] == ["lex", "outside"] and out.ranking_mode == "hybrid"
        call = next(row for row in repo.calls if row[0] == "vector")
        assert call[1] == SCOPE and call[2].model_id == "memory-embedding" and call[2].vector == (.1, .2)
        assert call[2].kinds == ("fact", "task") and call[2].min_score == .5 and call[2].limit == 20
        assert embed.calls == [(SCOPE, "memory-embedding", "database")]
        await service.drain_background()
    asyncio.run(run())


def test_embedding_timeout_and_repository_failure_degrade_to_lexical_without_retry(monkeypatch):
    async def run():
        repo = Repository([item("lex", "database")], MemoryConfig(enabled=True, embedding_model_id="embed"))
        embed = Embedder(error=True); service = MemoryService(repo, embed, now=lambda: NOW)
        out = await service.recall(CONTEXT, "database")
        assert out.vector_skip_reason == "embed_failed" and len(embed.calls) == 1 and [r.id for r in out.items] == ["lex"]
        embed.error = False; repo.fail.add("vector")
        assert (await service.recall(CONTEXT, "database")).vector_skip_reason == "vector_search_failed"
        observed = []
        async def timeout(awaitable, timeout):
            awaitable.close(); observed.append(timeout); raise asyncio.TimeoutError()
        monkeypatch.setattr("backend.weknora_port.conversation.memory.asyncio.wait_for", timeout)
        assert (await service.recall(CONTEXT, "database")).vector_skip_reason == "embed_failed"
        assert observed == [2]
        await service.drain_background()
    asyncio.run(run())


def test_retrieval_conditioning_limits_and_affinity_threshold_do_not_require_embedding():
    async def run():
        repo = Repository([item("p", "database engineer", kind="profile"), item("i", "database", kind="interest")])
        repo.docs = [DocumentAffinity("one", "one sighting", 1), DocumentAffinity("two", "habit", 2)]
        service = MemoryService(repo, Embedder(), now=lambda: NOW)
        ctx = await service.retrieval_context(CONTEXT)
        assert ctx.background == "database engineer" and ctx.interests == ("database",) and ctx.documents == ("habit",)
        assert await service.document_affinity(CONTEXT, ("two",)) == {"two": 2}
        repo.cfg = replace(repo.cfg, retrieval_conditioning=False)
        assert (await service.retrieval_context(CONTEXT)).background == "" and await service.document_affinity(CONTEXT, ("two",)) == {}
        assert service.embedder.calls == []
    asyncio.run(run())


def test_recall_search_item_and_rune_caps_are_independent():
    async def run():
        rows = [item(f"row-{i}", "database " + "字" * 100) for i in range(30)]
        repo = Repository(rows); service = MemoryService(repo, now=lambda: NOW)
        out = await service.recall(CONTEXT, "database")
        assert len(out.items) == 5 and sum(len(r.content) + 3 for r in out.items) <= 600
        search = await service.search(CONTEXT, "database", 100)
        assert len(search.items) == 17 and sum(len(r.content) + 3 for r in search.items) <= 2000
        repo.rows = {r.id: replace(r, content="database short") for r in rows}
        assert len((await service.search(CONTEXT, "database", 100)).items) == 20
        assert len((await service.search(CONTEXT, "database")).items) == 10
        await service.drain_background()
    asyncio.run(run())


def test_untrusted_repository_rows_and_subject_cannot_cross_owner_workspace():
    async def run():
        class LeakyRepository(Repository):
            async def list_active_resident(self, scope, limit): return list(self.rows.values())
            async def list_active_by_kinds(self, scope, kinds, limit): return list(self.rows.values())
        rows = [replace(item("tenant", "TENANT LEAK", kind="profile"), tenant_id=2),
                replace(item("owner", "OWNER LEAK", kind="profile"), subject_id="other-owner")]
        repo = LeakyRepository(rows); service = MemoryService(repo, now=lambda: NOW)
        assert not (await service.recall(CONTEXT, "LEAK")).items
        assert not (await service.search(CONTEXT, "LEAK")).items
        assert not (await service.retrieval_context(CONTEXT)).items
        repo.subject = MemorySubject(2, SCOPE.subject_id, block_text="CACHE LEAK")
        assert (await service.recall(CONTEXT, "LEAK")).prompt == ""
        repo.subject = MemorySubject(1, "other-owner", block_text="CACHE LEAK")
        assert (await service.recall(CONTEXT, "LEAK")).prompt == ""
    asyncio.run(run())


def test_run_cancellation_propagates_instead_of_degrading_into_another_model_call():
    async def run():
        class CancelledEmbedder(Embedder):
            async def embed_query(self, scope, model_id, text): raise asyncio.CancelledError()
        repo = Repository([item("f", "database")], MemoryConfig(enabled=True, embedding_model_id="embed"))
        service = MemoryService(repo, CancelledEmbedder(), now=lambda: NOW)
        with pytest.raises(asyncio.CancelledError): await service.recall(CONTEXT, "database")
        assert not any(call[0] in ("vector", "touch") for call in repo.calls)
    asyncio.run(run())


def test_management_scoped_manual_pending_confirm_edit_delete_and_forgetting():
    async def run():
        repo = Repository(); reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
        inferred = await manager.remember(CONTEXT, item("untrusted-id", "database old", inferred=True, topic="Database", source_message_id="source"))
        assert inferred.status == "pending" and inferred.id != "untrusted-id"
        assert not (await reader.recall(CONTEXT, "database")).items
        confirmed = await manager.confirm(CONTEXT, inferred.id)
        assert confirmed.status == "active"
        updated = await manager.update(CONTEXT, inferred.id, "database corrected", 7)
        assert updated.origin == "manual" and updated.importance == 5 and updated.topic == "Database"
        await manager.delete(CONTEXT, inferred.id)
        assert inferred.id not in repo.rows
        assert not (await reader.recall(CONTEXT, "database")).items
        with pytest.raises(MemoryWriteError, match="PREVIOUSLY_FORGOTTEN"):
            await manager.remember(CONTEXT, item("new", "database corrected", source_message_id="source"))
        foreign = replace(item("foreign", "foreign fact"), subject_id="other"); repo.rows[foreign.id] = foreign
        with pytest.raises(MemoryWriteError, match="NOT_FOUND"): await manager.delete(CONTEXT, foreign.id)
        assert foreign.id in repo.rows
        await reader.drain_background()
    asyncio.run(run())


def test_management_replacement_dedup_sensitive_redaction_and_capacity():
    async def run():
        repo = Repository(cfg=MemoryConfig(enabled=True, max_items=1)); reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
        first = await manager.create(CONTEXT, "fact", "database original", 1)
        duplicate = await manager.create(CONTEXT, "fact", "database original", 1)
        assert first.id == duplicate.id
        replacement = await manager.remember(CONTEXT, item("candidate", "database longer original", topic="database", importance=5))
        assert len(repo.active(SCOPE)) == 1
        with pytest.raises(MemoryWriteError, match="SENSITIVE"):
            await manager.create(CONTEXT, "fact", "sk-SYNTHETICabcdefghijklmnop")
        sanitized = await manager.create(CONTEXT, "invalid-kind", "production database uses password: fake-secret, rotate weekly")
        assert "fake-secret" not in sanitized.content and "【已隐藏】" in sanitized.content and sanitized.kind == "fact"
        assert fingerprint(" Database, FACT ") == fingerprint("database fact")
        assert memory_key("门店的排班管理问题", "ignored") == "门店排班管理"
        assert replacement.tenant_id == 1 and replacement.subject_id == SCOPE.subject_id
    asyncio.run(run())


def test_management_clear_removes_only_owner_and_disabled_allows_user_forgetting():
    async def run():
        repo = Repository([item("one", "one fact"), replace(item("foreign", "other fact"), tenant_id=2)])
        reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
        await manager.set_enabled(CONTEXT, False)
        settings = await manager.settings(CONTEXT)
        assert settings.workspace_enabled and not settings.effective and settings.item_count == 1
        with pytest.raises(MemoryWriteError, match="DISABLED"): await manager.create(CONTEXT, "fact", "new fact")
        assert await manager.clear(CONTEXT) == 1 and list(repo.rows) == ["foreign"]
        assert ("clear_topics", SCOPE) in repo.calls and ("clear_affinity", SCOPE) in repo.calls
    asyncio.run(run())


def test_atomic_replacement_and_scope_bound_post_write_callback():
    async def run():
        repo = Repository(); reader = MemoryService(repo, now=lambda: NOW); calls = []
        async def written(scope, cfg, row): calls.append((scope, cfg, row.id))
        manager = MemoryManager(repo, reader, after_write=written)
        old = await manager.remember(CONTEXT, item("candidate", "database version one", topic="database", origin="explicit"))
        new = await manager.remember(CONTEXT, item("candidate", "database version two", topic="database", origin="explicit"))
        assert repo.rows[old.id].status == "superseded" and repo.rows[new.id].status == "active"
        assert any(call == ("save", SCOPE, new.id, old.id) for call in repo.calls)
        assert [call[0] for call in calls] == [SCOPE, SCOPE]
        assert [call[2] for call in calls] == [old.id, new.id]
        assert "version one" not in (await reader.recall(CONTEXT, "database")).prompt
        await reader.drain_background()
    asyncio.run(run())


def test_pending_replacement_keeps_active_fact_until_confirmation_and_returns_saved_identity():
    async def run():
        repo = Repository(); reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
        old = await manager.remember(CONTEXT, item("ignored", "database original policy", topic="database", origin="explicit"))
        proposal = await manager.remember(CONTEXT, item("ignored", "database inferred policy", topic="database", inferred=True))
        assert proposal.status == "pending" and proposal.replaces_id == old.id
        assert repo.rows[old.id].status == "active"
        prompt = (await reader.recall(CONTEXT, "database")).prompt
        assert "original policy" in prompt and "inferred policy" not in prompt
        duplicate = await manager.remember(CONTEXT, item("ignored", "database inferred policy", topic="database", inferred=True))
        assert duplicate.id == proposal.id
        confirmed = await manager.confirm(CONTEXT, proposal.id)
        assert confirmed.status == "active" and repo.rows[old.id].superseded_by == proposal.id
        assert "original policy" not in (await reader.recall(CONTEXT, "database")).prompt
        await reader.drain_background()
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["delete", "clear", "update", "replace"])
def test_atomic_cache_invalidation_prevents_private_text_after_failed_rebuild(operation):
    async def run():
        old = item("old", "private old content", kind="profile", topic="profile", normalized_key="profile", origin="explicit")
        repo = Repository([old]); repo.subject = replace(repo.subject, block_text="private old content")
        reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
        # Both cache rebuild and live resident fetch fail. Recall takes the source
        # cached-block fallback, which MUST have been invalidated by the mutation.
        repo.fail.add("resident")
        if operation == "delete":
            await manager.delete(CONTEXT, old.id)
        elif operation == "clear":
            assert await manager.clear(CONTEXT) == 1
        elif operation == "update":
            await manager.update(CONTEXT, old.id, "safe new content", 3)
        else:
            await manager.remember(CONTEXT, item("ignored", "safe new content", kind="profile", topic="profile", origin="explicit"))
        assert repo.subject.block_text == ""
        assert "private old content" not in (await reader.recall(CONTEXT, "private")).prompt
        if operation in ("delete", "clear"):
            assert (SCOPE, fingerprint(old.content)) in repo.tombstones
            with pytest.raises(MemoryWriteError, match="PREVIOUSLY_FORGOTTEN"):
                await manager.create(CONTEXT, "profile", old.content)
        await reader.drain_background()
    asyncio.run(run())


def test_atomic_delete_failure_does_not_claim_success_or_partially_delete():
    async def run():
        old = item("old", "private old content", kind="profile")
        repo = Repository([old]); repo.subject = replace(repo.subject, block_text=old.content)
        reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
        repo.fail.add("atomic_tombstone")
        with pytest.raises(RuntimeError): await manager.delete(CONTEXT, old.id)
        assert repo.rows[old.id] == old and repo.subject.block_text == old.content
        assert not repo.tombstones
    asyncio.run(run())


def test_stale_rebuild_cas_cannot_restore_content_after_interleaved_deletion():
    async def run():
        old = item("old", "private old content", kind="profile", origin="explicit")
        repo = Repository([old]); repo.subject = replace(repo.subject, block_text=old.content)
        entered, release = asyncio.Event(), asyncio.Event()
        original_update = repo.update_subject_block
        async def delayed_update(scope, block, item_count, expected_revision):
            entered.set()
            await release.wait()
            return await original_update(scope, block, item_count, expected_revision)
        repo.update_subject_block = delayed_update
        reader = MemoryService(repo, now=lambda: NOW); manager = MemoryManager(repo, reader)
        stale = asyncio.create_task(manager._rebuild(SCOPE))
        await entered.wait()  # old rows already rendered, revision 0 captured
        repo.fail.add("resident")  # deletion's fresh rebuild fails
        await manager.delete(CONTEXT, old.id)
        assert repo.subject.cache_revision == 1 and repo.subject.block_text == ""
        release.set(); await stale
        assert repo.subject.block_text == "" and repo.subject.cache_revision == 1
        assert "private old content" not in (await reader.recall(CONTEXT, "private")).prompt
        await reader.drain_background()
    asyncio.run(run())


@pytest.mark.parametrize("text", ["password：fake", "API_KEY=fake", "密码是fake", "123456199901011234", "13800138000", "4111 1111 1111 1111"])
def test_fixed_source_redaction_patterns_and_ascii_boundaries(text):
    assert "【已隐藏】" in redact_sensitive(text)
