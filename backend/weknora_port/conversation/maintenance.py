"""Whole-store maintenance: local candidates, model judgement, bounded backfill."""
import asyncio
import math
from dataclasses import replace
from datetime import timedelta

from .maintenance_types import ConsolidationResult, MaintenanceModel, MaintenanceRepository, ModelRequest
from .management import MemoryManager
from .prompts import CONSOLIDATION_SCHEMA_JSON, CONSOLIDATION_SYSTEM
from .ranking import sanitize_content, tokenize
from .topics import parse_object, topic_key
from .types import MemoryContext, MemoryItem, MemoryScopeError


def embeddable_text(item, aliases=()):
    topic, content = sanitize_content(item.topic)[:80], sanitize_content(item.content)
    text = topic + "：" + content if topic and topic != content else content
    if not text: return ""
    seen = {text, topic, content}
    for alias in aliases:
        alias = sanitize_content(alias)[:80]
        if alias and alias not in seen:
            text += "；" + alias; seen.add(alias)
    return text


def cosine(a, b):
    if not a or len(a) != len(b): return 0
    denominator = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    return sum(x * y for x, y in zip(a, b)) / denominator if denominator else 0


def merge_candidates(items, vectors, min_overlap, min_cosine):
    terms = {row.id: set(tokenize(row.topic + " " + row.content)) for row in items}
    taken, clusters = set(), []
    for index, row in enumerate(items):
        if row.id in taken: continue
        cluster = [row]
        for other in items[index + 1:]:
            if other.id in taken or other.kind != row.kind: continue
            a, b = terms[row.id], terms[other.id]
            overlap = len(a & b) / len(a | b) if a and b else 0
            if overlap >= min_overlap or cosine(vectors.get(row.id, ()), vectors.get(other.id, ())) >= min_cosine:
                cluster.append(other); taken.add(other.id)
        if len(cluster) > 1:
            clusters.append(tuple(cluster)); taken.add(row.id)
    return clusters


class MemoryMaintenance:
    def __init__(self, repository: MaintenanceRepository, manager: MemoryManager, model: MaintenanceModel | None = None):
        self.repository, self.manager, self.model = repository, manager, model

    async def backfill(self, scope, cfg):
        embedder = self.manager.reader.embedder
        if not cfg.embedding_model_id or embedder is None: return 0
        try: rows = await self.repository.items_missing_embeddings(scope, cfg.embedding_model_id, 200)
        except Exception: return 0
        filled = 0
        for row in rows[:200]:
            if (row.tenant_id, row.subject_id) != (scope.tenant_id, scope.subject_id): continue
            aliases = ()
            if row.kind == "interest":
                try:
                    topic = await self.repository.topic_by_key(scope, topic_key(row.topic))
                    if topic and (topic.tenant_id, topic.subject_id) == (scope.tenant_id, scope.subject_id): aliases = topic.aliases
                except Exception: pass
            try:
                vector = tuple(await asyncio.wait_for(embedder.embed_query(scope, cfg.embedding_model_id, embeddable_text(row, aliases)), 10))
                if not vector or not all(type(v) in (int, float) and math.isfinite(v) for v in vector): break
            except Exception: break  # failing model stops this batch, never fake vectors
            try:
                # Adapter compare-checks source topic/content and lifecycle before
                # upsert so a late vector cannot revive deleted/replaced content.
                await self.repository.upsert_item_embedding(scope, row, cfg.embedding_model_id, vector)
                filled += 1
            except Exception: continue
        return filled

    async def review(self, context: MemoryContext, model_id="", *, force=False):
        scope, cfg, subject = await self.manager.reader.enabled_scope(context)
        if scope is None: return ConsolidationResult(skipped="memory_disabled")
        if subject is None: subject = await self.repository.ensure_subject(scope)
        now = self.manager.reader.now()
        last = subject.forced_consolidated_at if force else subject.consolidated_at
        if last and now - last < timedelta(seconds=60 if force else 86400):
            return ConsolidationResult(skipped="too_soon" if force else "")
        if force:
            try: await self.repository.mark_consolidated(scope, True)
            except Exception: pass
        try: expired = await self.repository.expire_overdue(scope)
        except Exception: expired = 0
        try:
            items = [row for row in await self.repository.list_items(scope, "active", cfg.effective_max_items(), 0) if self.manager.reader.active(row, scope)]
        except Exception: return ConsolidationResult(expired=expired)
        demoted = 0
        for row in items:
            last_used = max(v for v in (row.valid_from, row.last_used_at) if v is not None) if row.valid_from or row.last_used_at else now
            if row.kind == "task" and row.importance > 1 and last_used <= now - timedelta(days=45):
                try:
                    await self.repository.update_item_content(scope, row.id, row.content, row.normalized_key, 1); demoted += 1
                except Exception: pass
        merged, candidates, skipped = 0, 0, "too_few_items"
        if force or len(items) >= 6:
            vectors = {}
            if cfg.embedding_model_id:
                try: vectors = await self.repository.item_embeddings(scope, tuple(row.id for row in items), cfg.embedding_model_id)
                except Exception: pass
            clusters = merge_candidates(items, vectors, .3 if force else .55, .75 if force else .86)
            candidates, skipped, declined = len(clusters), "no_candidates" if not clusters else "", 0
            if not model_id:
                model_id = cfg.extract_model_id or (await self.model.workspace_chat_model(scope) if self.model else "")
            for cluster in clusters[:8 if force else 3]:
                if not model_id or self.model is None:
                    skipped = "model_unavailable"; break
                user = "".join(f"- ({row.valid_from.strftime('%Y-%m-%d')}) {sanitize_content(row.content)}\n" for row in cluster)
                try:
                    response = await self.model.complete(ModelRequest(scope, model_id, CONSOLIDATION_SYSTEM, user, CONSOLIDATION_SCHEMA_JSON, 600))
                    if response is None:
                        skipped = "model_unavailable"; break
                except Exception:
                    skipped = "model_unavailable"; break
                try: statement = sanitize_content(parse_object(response.content).get("statement") or "")
                except Exception: statement = ""
                if not statement: declined += 1; continue
                primary = cluster[0]
                try:
                    replacement = await self.manager.remember(context, replace(primary, id="", content=statement, inferred=False, expires_at=None, replaces_id="", superseded_by="", operation_id=""))
                except Exception: continue
                for row in cluster:
                    if row.id != replacement.id:
                        try: await self.repository.supersede_item(scope, row.id, replacement.id)
                        except Exception: pass
                merged += 1
            if not merged and declined and not skipped: skipped = "model_declined"
        backfilled = await self.backfill(scope, cfg)
        try: await self.repository.sync_vector_column(scope, 2000)
        except Exception: pass
        try: await self.repository.mark_consolidated(scope, False)
        except Exception: pass
        if merged or demoted or expired: await self.manager._rebuild(scope)
        return ConsolidationResult(len(items), expired, demoted, candidates, merged, backfilled, skipped)
