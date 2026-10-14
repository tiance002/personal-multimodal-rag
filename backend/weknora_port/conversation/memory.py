"""Scoped upstream memory read path. Persistence/egress are injected boundaries."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .ranking import KINDS, RESIDENT_KINDS, fuse_rankings, lexical_ranking, merge_vector_hits, render_memory_lines, sanitize_content, select_resident_interests, take_within_budget, time_key, wrap_memory_prompt
from .types import MemoryConfig, MemoryContext, MemoryEmbedder, MemoryItem, MemoryRecall, MemoryRepository, MemoryScope, MemorySearchResult, MemoryVectorQuery, RetrievalContext, resolve_scope


class MemoryService:
    def __init__(self, repository: MemoryRepository, embedder: MemoryEmbedder | None = None, *,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 on_recalled: Callable[[MemoryScope, tuple[MemoryItem, ...]], Awaitable[None]] | None = None):
        self.repository, self.embedder, self.now, self.on_recalled = repository, embedder, now, on_recalled
        self._background: set[asyncio.Task] = set()

    async def enabled_scope(self, context: MemoryContext) -> tuple[MemoryScope | None, MemoryConfig, object | None]:
        try:
            scope = resolve_scope(context)
            try:
                cfg = await self.repository.workspace_config(scope.tenant_id) or MemoryConfig()
            except Exception:
                cfg = MemoryConfig()
            if not cfg.enabled or not context.agent_memory_allowed:
                return None, cfg, None
            subject = await self.repository.get_subject(scope)
            if subject and ((subject.tenant_id, subject.subject_id) != (scope.tenant_id, scope.subject_id) or not subject.enabled):
                return None, cfg, None
            return scope, cfg, subject
        except Exception:
            return None, MemoryConfig(), None

    def active(self, item: MemoryItem, scope: MemoryScope) -> bool:
        return (item.tenant_id, item.subject_id) == (scope.tenant_id, scope.subject_id) and item.status == "active" and (
            item.expires_at is None or time_key(item.expires_at) > time_key(self.now()))

    async def _select(self, scope: MemoryScope, cfg: MemoryConfig, query: str, candidates: list[MemoryItem], kinds: tuple[str, ...], max_items: int, budget: int, exclude_ids: set[str]) -> tuple[tuple[MemoryItem, ...], str, str]:
        lexical = lexical_ranking(query, candidates)
        hits, reason = [], "vector_disabled"
        if cfg.vector_enabled() and cfg.embedding_model_id.strip() and self.embedder is not None:
            try:
                vector = tuple(await asyncio.wait_for(self.embedder.embed_query(scope, cfg.embedding_model_id.strip(), query), timeout=2))
                if not vector or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector):
                    reason = "embed_failed"
                else:
                    reason = "vector_search_failed"
                    rows = await self.repository.search_items_by_vector(scope, MemoryVectorQuery(cfg.embedding_model_id.strip(), vector, kinds, .5, max(20, max_items * 4)))
                    hits = [hit for hit in rows if self.active(hit.item, scope) and hit.item.kind in kinds and math.isfinite(hit.score) and hit.score >= .5]
                    reason = "" if hits else "no_vector_matches"
            except Exception:
                # No raw provider error, private query or fabricated zero usage.
                # Once embedding succeeded, retain the repository-failure reason.
                reason = reason if reason == "vector_search_failed" else "embed_failed"
        vector_rank, pool, _ = merge_vector_hits(candidates, hits, exclude_ids)
        if not vector_rank:
            return take_within_budget(lexical, candidates, max_items, budget), "lexical_only" if lexical else "no_matches", reason
        return take_within_budget(fuse_rankings(lexical, vector_rank), pool, max_items, budget), "hybrid", reason

    def _touch(self, scope: MemoryScope, items: tuple[MemoryItem, ...]) -> None:
        if not items:
            return
        async def touch():
            try:
                await self.repository.touch_used(scope, tuple(item.id for item in items))
            except Exception:
                pass
        task = asyncio.create_task(touch())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain_background(self) -> None:
        """Lifecycle hook: await best-effort touches without adding QA latency."""
        if self._background:
            await asyncio.gather(*tuple(self._background))

    async def recall(self, context: MemoryContext, query: str) -> MemoryRecall:
        scope, cfg, subject = await self.enabled_scope(context)
        if scope is None or subject is None:
            return MemoryRecall()
        try:
            subject = await self.repository.get_subject(scope)
            if subject is None or (subject.tenant_id, subject.subject_id) != (scope.tenant_id, scope.subject_id):
                return MemoryRecall()
        except Exception:
            return MemoryRecall()
        try:
            residents = [item for item in await self.repository.list_active_resident(scope, 60)
                         if self.active(item, scope) and (item.kind in RESIDENT_KINDS or item.origin == "explicit")][:60]
        except Exception:
            residents = []
        standing = [item for item in residents if item.kind != "interest"]
        selected, relevant = select_resident_interests(query, [item for item in residents if item.kind == "interest"])
        block = render_memory_lines((*standing, *selected), 900) or subject.block_text
        exclude = {item.id for item in residents}
        try:
            candidates = [item for item in await self.repository.list_active_by_kinds(scope, ("fact", "task"), cfg.effective_max_items())
                          if self.active(item, scope) and item.kind in ("fact", "task") and item.id not in exclude]
        except Exception:
            candidates = []
        matched, mode, reason = await self._select(scope, cfg, query, candidates, ("fact", "task"), 5, 600, exclude)
        prompt = wrap_memory_prompt(block, render_memory_lines(matched, 600))
        if not prompt:
            return MemoryRecall(ranking_mode=mode, vector_skip_reason=reason)
        used = tuple(item for item in (*standing, *relevant) if sanitize_content(item.content) in block) + matched
        self._touch(scope, used)
        if self.on_recalled and used:
            try:
                await self.on_recalled(scope, used)
            except Exception:
                pass
        return MemoryRecall(prompt, used, mode, reason)

    async def search(self, context: MemoryContext, query: str, limit: int = 0) -> MemorySearchResult:
        scope, cfg, _ = await self.enabled_scope(context)
        if scope is None:
            return MemorySearchResult()
        query = query.strip()
        if not query:
            return MemorySearchResult(available=True)
        limit = min(limit if limit > 0 else 10, 20)
        try:
            candidates = [item for item in await self.repository.list_active_by_kinds(scope, KINDS, cfg.effective_max_items())
                          if self.active(item, scope) and item.kind in KINDS]
        except Exception:
            return MemorySearchResult(available=True)
        items, mode, _ = await self._select(scope, cfg, query, candidates, KINDS, limit, 2000, set())
        self._touch(scope, items)
        return MemorySearchResult(True, items, mode)

    async def retrieval_context(self, context: MemoryContext) -> RetrievalContext:
        scope, cfg, _ = await self.enabled_scope(context)
        if scope is None or not cfg.retrieval_enabled():
            return RetrievalContext()
        try:
            rows = await self.repository.list_active_by_kinds(scope, ("profile", "interest"), 30)
        except Exception:
            return RetrievalContext()
        background, interests, used, budget = [], [], [], 0
        for item in rows:
            if not self.active(item, scope) or item.kind not in ("profile", "interest"):
                continue
            line = sanitize_content(item.content)
            if not line:
                continue
            cost = len(line) + 2
            if budget + cost > 240:
                break
            budget += cost; used.append(item)
            (interests if item.kind == "interest" else background).append(line)
        try:
            docs = tuple(row.title for row in await self.repository.top_doc_affinity(scope, 5) if row.title.strip() and row.hits >= 2)
        except Exception:
            docs = ()
        return RetrievalContext("；".join(background), tuple(interests), docs, tuple(used))

    async def document_affinity(self, context: MemoryContext, knowledge_ids: tuple[str, ...]) -> dict[str, int]:
        scope, cfg, _ = await self.enabled_scope(context)
        if scope is None or not cfg.retrieval_enabled() or not knowledge_ids:
            return {}
        try:
            return dict(await self.repository.doc_affinity(scope, knowledge_ids))
        except Exception:
            return {}
