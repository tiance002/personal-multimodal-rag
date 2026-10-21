"""Fixed-source topic resolution and promotion; model is only an injected port."""
import json
from dataclasses import replace

from .management import MemoryManager, MemoryWriteError, fingerprint, memory_key, normalize_topic_key
from .maintenance_types import MaintenanceModel, MaintenanceRepository, ModelRequest, TopicStat
from .prompts import TOPIC_SCHEMA_JSON, TOPIC_SYSTEM
from .ranking import sanitize_content
from .types import MemoryContext, MemoryItem, MemoryScopeError, MemoryTombstone, resolve_scope


def topic_key(label: str) -> str:
    return normalize_topic_key(label)


def topic_similarity(a: str, b: str) -> float:
    def grams(text):
        text = topic_key(text)
        return {text} if len(text) == 1 else {text[i:i + 2] for i in range(len(text) - 1)}
    left, right = grams(a), grams(b)
    return 2 * len(left & right) / (len(left) + len(right)) if left and right else 0


def improved_label(canonical: str, incoming: str, proposed: str) -> bool:
    key, old = topic_key(proposed), topic_key(canonical)
    return bool(key and key != old and len(proposed) <= 80 and key not in old and len(key) >= len(old)
                and topic_similarity(proposed, canonical) >= .30 and topic_similarity(proposed, incoming) >= .30)


def parse_object(content: str) -> dict:
    content = content.strip()
    if "```" in content:
        content = content.split("```", 1)[1]
        if "\n" in content: content = content.split("\n", 1)[1]
        content = content.split("```", 1)[0].strip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start: raise ValueError("INVALID_MODEL_OUTPUT")
    value = json.loads(content[start:end + 1])
    if not isinstance(value, dict): raise ValueError("INVALID_MODEL_OUTPUT")
    return value


class TopicService:
    def __init__(self, repository: MaintenanceRepository, manager: MemoryManager, model: MaintenanceModel | None = None):
        self.repository, self.manager, self.model = repository, manager, model

    def scoped(self, row, scope):
        if row and (row.tenant_id, row.subject_id) != (scope.tenant_id, scope.subject_id):
            raise MemoryScopeError()
        return row

    async def resolve(self, scope, model_id, surfaces, operation_id=""):
        try: existing = [self.scoped(r, scope) for r in await self.repository.top_topics(scope, 40)]
        except Exception: existing = []
        resolutions, unresolved = [], []
        for surface in surfaces:
            key = topic_key(surface)
            target = next((r for r in existing if r.normalized_key == key or any(topic_key(a) == key for a in r.aliases)), None)
            tier = "reused" if target and target.topic == surface else "exact" if target else ""
            if target is None and len(key) >= 4:
                candidates = [(topic_similarity(surface, r.topic), r) for r in existing if len(topic_key(r.topic)) >= 4]
                if candidates:
                    score, best = max(candidates, key=lambda pair: pair[0])
                    if score >= .8: target, tier = best, "fuzzy"
            if target is None: unresolved.append(len(resolutions))
            resolutions.append([surface, target, tier, ""])
        if unresolved and existing and model_id and self.model:
            prompt = "已有主题：\n" + "".join(f"[{i}] {r.topic}\n" for i, r in enumerate(existing))
            prompt += "\n新出现的说法：\n" + "".join(f"[{i}] {resolutions[i][0]}\n" for i in unresolved)
            try:
                response = await self.model.complete(ModelRequest(scope, model_id, TOPIC_SYSTEM, prompt, TOPIC_SCHEMA_JSON, 800, operation_id=operation_id))
                decisions = parse_object(response.content).get("resolutions", []) if response else []
                for decision in decisions:
                    i, target = decision.get("index"), decision.get("same_as")
                    if type(i) is not int or i not in unresolved or type(target) is not int or not 0 <= target < len(existing): continue
                    resolutions[i][1:3] = [existing[target], "model"]
                    label = sanitize_content(decision.get("label") or "")[:80]
                    if improved_label(existing[target].topic, resolutions[i][0], label): resolutions[i][3] = label
            except Exception: pass
        for i, resolution in enumerate(resolutions):
            if resolution[1]: continue
            for earlier in resolutions[:i]:
                if earlier[1] is None and topic_key(earlier[0]) == topic_key(resolution[0]):
                    resolution[0] = earlier[0]; break
        return resolutions

    async def _invalidate_interest(self, scope, label, new_label=""):
        try:
            for item in await self.repository.list_active_by_kinds(scope, ("interest",), 100):
                self.scoped(item, scope)
                if item.content != label: continue
                if new_label:
                    await self.repository.update_item_content(scope, item.id, new_label, memory_key(new_label, new_label), item.importance)
                await self.repository.delete_item_embedding(scope, item.id)
                if new_label: await self.manager._rebuild(scope)
                break
        except Exception: pass

    async def observe(self, context: MemoryContext, labels, model_id="", operation_id=""):
        scope, cfg, _ = await self.manager.reader.enabled_scope(context)
        if scope is None or cfg.write_mode != "auto": return ()
        await self.repository.ensure_subject(scope)
        surfaces = [sanitize_content(label)[:80] for label in labels if isinstance(label, str) and sanitize_content(label)]
        promoted = []
        for index, (surface, canonical, tier, proposed) in enumerate(await self.resolve(scope, model_id, surfaces, operation_id)):
            label = canonical.topic if canonical else surface
            key = topic_key(label)
            if not key: continue
            if any([await self.repository.has_tombstone(scope, fingerprint(v)) for v in (label, surface)]): continue
            try:
                old = self.scoped(await self.repository.topic_by_key(scope, key), scope)
                row = self.scoped(await self.repository.bump_topic(scope, label, key, surface, f"{operation_id}:topic:{index}" if operation_id else ""), scope)
                if len(row.aliases) > (len(old.aliases) if old else 0): await self._invalidate_interest(scope, label)
                if proposed and await self.repository.rename_topic(scope, key, topic_key(proposed), proposed):
                    await self._invalidate_interest(scope, label, proposed)
                    label, key = proposed, topic_key(proposed)
                if row.promoted_at or row.hits < min(20, cfg.interest_threshold if cfg.interest_threshold > 0 else 3): continue
                try:
                    await self.manager.remember(context, MemoryItem("", 0, "", "interest", label, topic=label, origin="extracted", operation_id=f"{operation_id}:promote:{key}" if operation_id else ""))
                except MemoryWriteError as error:
                    if str(error) not in ("MEMORY_PREVIOUSLY_FORGOTTEN", "MEMORY_SENSITIVE_CONTENT"): raise
                await self.repository.mark_topic_promoted(scope, key)
                promoted.append(label)
            except Exception: continue
        return tuple(promoted)

    async def _unpromoted(self, scope, id):
        row = self.scoped(await self.repository.topic_by_id(scope, id), scope)
        if row is None or row.promoted_at: raise MemoryWriteError("MEMORY_ITEM_NOT_FOUND")
        return row

    async def promote(self, context: MemoryContext, id: str):
        scope, _, _ = await self.manager.reader.enabled_scope(context)
        if scope is None: raise MemoryWriteError("MEMORY_DISABLED")
        row = await self._unpromoted(scope, id)
        item = await self.manager.remember(context, MemoryItem("", 0, "", "interest", row.topic, topic=row.topic, origin="manual"))
        try:await self.repository.mark_topic_promoted(scope, row.normalized_key)
        except Exception:
            # Fixed Service.PromoteTopic warns after a successful write; the
            # created interest remains the result even if marking fails.
            import logging
            logging.getLogger(__name__).warning('MEMORY_TOPIC_MARK_PROMOTED_FAILED')
        return item

    async def delete(self, context: MemoryContext, id: str):
        scope = resolve_scope(context); row = await self._unpromoted(scope, id)
        labels = dict.fromkeys((row.topic, *row.aliases))
        tombstones = tuple(MemoryTombstone(row.topic, fingerprint(label)) for label in labels if fingerprint(label))
        await self.repository.delete_topic(scope, id, tombstones)
