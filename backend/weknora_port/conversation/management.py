"""Scoped explicit/manual writes and forgetting; automatic distillation pending.

Atomic replacement/vector cleanup belong to the injected repository, not an
in-memory production fallback. See fixed-source mapping in the P4 report.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import replace
from typing import Awaitable, Callable, Protocol, Sequence

from .memory import MemoryService
from .ranking import KINDS, render_memory_lines, sanitize_content, tokenize
from .types import MemoryConfig, MemoryContext, MemoryItem, MemoryRepository, MemoryScope, MemoryScopeError, MemorySettings, MemorySubject, MemoryTombstone, resolve_scope
from .unicode15 import HAN, L, ND, contains, lower

PLACEHOLDER = "【已隐藏】"
_PATTERNS = (
    r"\bsk-[A-Za-z0-9_\-]{16,}", r"\bsk_(live|test)_[A-Za-z0-9]{16,}",
    r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}", r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\b(AKIA|ASIA)[0-9A-Z]{16}", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}",
    r"\bAIza[0-9A-Za-z_\-]{35}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\b(password|passwd|pwd|secret|token|api[_\- ]?key|access[_\- ]?key)\b\s*[:=＝：]\s*[^\s，。、；：！？,;]+",
    r"(密码|口令|密钥|秘钥)\s*[:=＝：是为]?\s*[^\s，。、；：！？,;]+",
    r"\b[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
    r"\b\d{4}[ \-]?\d{4}[ \-]?\d{4}[ \-]?\d{2,7}\b", r"\b1[3-9]\d{9}\b",
    r"\b[A-Za-z0-9_\-]{40,}\b",
)
def _go_pattern(pattern: str) -> str:
    # RE2 uses ASCII \b/\s/\d. Preserve them inside character classes too.
    insensitive = pattern.startswith("(?i)")
    if insensitive:
        pattern = pattern[4:]
    output, index, in_class = [], 0, False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            code = pattern[index + 1]
            if code == "b":
                output.append(r"(?a:\b)")
            elif code == "d":
                output.append("0-9" if in_class else "[0-9]")
            elif code == "s":
                output.append(r"\t\n\f\r " if in_class else r"[\t\n\f\r ]")
            else:
                output.append(pattern[index:index + 2])
            index += 2; continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        if insensitive and "a" <= char <= "z" and not in_class:
            # Go simple-fold cycles for the ASCII literals in this pattern.
            extras = "K" if char == "k" else "ſ" if char == "s" else ""
            output.append("[" + char + char.upper() + extras + "]")
        else:
            output.append(char)
        index += 1
    return "".join(output)


_SENSITIVE = tuple(re.compile(_go_pattern(pattern)) for pattern in _PATTERNS)


class MemoryWriteError(ValueError):
    pass


def redact_sensitive(content: str) -> str:
    for pattern in _SENSITIVE:
        content = pattern.sub(PLACEHOLDER, content)
    return content


def comparable(content: str) -> str:
    return "".join(char for char in lower(sanitize_content(content)) if contains(L, char) or contains(ND, char))


def fingerprint(content: str) -> str:
    normalized = comparable(content)
    return hashlib.sha256(normalized.encode()).hexdigest() if normalized else ""


def normalize_topic_key(topic: str) -> str:
    noise = set("的了地得之与和及在是有个等对于")
    key = "".join(c for c in lower(topic.strip()) if c not in noise and (contains(HAN, c) or contains(L, c) or contains(ND, c)))
    for suffix in ("相关问题", "相关", "问题", "方面", "情况", "事宜", "工作", "方向"):
        if key.endswith(suffix) and key != suffix:
            key = key[:-len(suffix)]; break
    return key[:120]


def memory_key(topic: str, content: str) -> str:
    key = normalize_topic_key(topic)
    if key:
        return key
    return "-".join(sorted(set(tokenize(topic.strip() or content))))[:200]


class ManagedMemoryRepository(MemoryRepository, Protocol):
    """Mutation callbacks MUST lock the scoped subject and invalidate block_text
    and increment cache_revision in the SAME transaction as changing
    content/lifecycle/deleting rows. Deletion
    additionally persists supplied suppression metadata atomically. An adapter
    failure rolls back both; best-effort later rebuild cannot preserve old text.
    Replacement follows upstream SaveItem/ConfirmPendingItem: pending proposals
    retain active facts until confirmation, with replaces_id/superseded_by links.
    save_item returns the actual saved row, including idempotent replay identity.
    update_subject_block writes only when cache_revision == expected_revision;
    false means a newer mutation won and MUST leave the cache unchanged.
    Vector cleanup and embedding-space isolation belong to this adapter too.
    """
    async def ensure_subject(self, scope: MemoryScope) -> MemorySubject: ...
    async def get_item(self, scope: MemoryScope, item_id: str) -> MemoryItem | None: ...
    async def find_active_by_key(self, scope: MemoryScope, key: str) -> MemoryItem | None: ...
    async def list_live(self, scope: MemoryScope, kind: str, limit: int) -> Sequence[MemoryItem]: ...
    async def has_tombstone(self, scope: MemoryScope, digest: str) -> bool: ...
    async def has_tombstone_for_message(self, scope: MemoryScope, message_id: str, within_seconds: int) -> bool: ...
    async def save_item(self, scope: MemoryScope, item: MemoryItem, replaces_id: str) -> MemoryItem: ...
    async def count_active(self, scope: MemoryScope) -> int: ...
    async def archive_lowest_ranked(self, scope: MemoryScope, keep: int) -> None: ...
    async def update_subject_block(self, scope: MemoryScope, block: str, item_count: int, expected_revision: int) -> bool: ...
    async def update_item_content(self, scope: MemoryScope, item_id: str, content: str, key: str, importance: int) -> None: ...
    async def delete_item(self, scope: MemoryScope, item_id: str, tombstone: MemoryTombstone) -> None: ...
    async def list_items(self, scope: MemoryScope, status: str, limit: int, offset: int) -> Sequence[MemoryItem]: ...
    async def delete_all(self, scope: MemoryScope, tombstones: tuple[MemoryTombstone, ...]) -> int: ...
    async def delete_all_topics(self, scope: MemoryScope) -> None: ...
    async def delete_all_doc_affinity(self, scope: MemoryScope) -> None: ...
    async def confirm_pending_item(self, scope: MemoryScope, item_id: str) -> None: ...
    async def update_subject_enabled(self, scope: MemoryScope, enabled: bool) -> None: ...


class MemoryManager:
    def __init__(self, repository: ManagedMemoryRepository, reader: MemoryService, *,
                 after_write: Callable[[MemoryScope, MemoryConfig, MemoryItem], Awaitable[None]] | None = None):
        self.repository, self.reader, self.after_write = repository, reader, after_write

    async def _item(self, scope: MemoryScope, item_id: str) -> MemoryItem:
        row = await self.repository.get_item(scope, item_id)
        if row is None:
            raise MemoryWriteError("MEMORY_ITEM_NOT_FOUND")
        if (row.tenant_id, row.subject_id) != (scope.tenant_id, scope.subject_id):
            raise MemoryScopeError()
        return row

    async def _rebuild(self, scope: MemoryScope) -> None:
        try:
            subject = await self.repository.get_subject(scope)
            if subject is None or (subject.tenant_id, subject.subject_id) != (scope.tenant_id, scope.subject_id):
                return
            revision = subject.cache_revision
            rows = [item for item in await self.repository.list_active_resident(scope, 60) if self.reader.active(item, scope)]
            count = await self.repository.count_active(scope)
            await self.repository.update_subject_block(scope, render_memory_lines(rows, 900), count, revision)
        except Exception:
            pass  # upstream cached block rebuild is best effort

    async def _capacity(self, scope: MemoryScope, cfg: MemoryConfig) -> None:
        try:
            if await self.repository.count_active(scope) > cfg.effective_max_items():
                await self.repository.archive_lowest_ranked(scope, cfg.effective_max_items())
        except Exception:
            pass

    @staticmethod
    def _clean(content: str) -> str:
        sanitized = sanitize_content(content)
        if not sanitized:
            raise MemoryWriteError("MEMORY_CONTENT_EMPTY")
        redacted = redact_sensitive(sanitized)
        if redacted != sanitized:
            if len(redacted.replace(PLACEHOLDER, "").strip()) < 6:
                raise MemoryWriteError("MEMORY_SENSITIVE_CONTENT")
            sanitized = sanitize_content(redacted)
        return sanitized

    async def create(self, context: MemoryContext, kind: str, content: str, importance: int = 3) -> MemoryItem:
        return await self.remember(context, MemoryItem("", 0, "", kind if kind in KINDS else "fact", content,
                                                    importance=importance if importance > 0 else 3, origin="manual"))

    async def remember(self, context: MemoryContext, item: MemoryItem, *, replaces_id: str = "") -> MemoryItem:
        scope, cfg, _ = await self.reader.enabled_scope(context)
        if scope is None:
            raise MemoryWriteError("MEMORY_DISABLED")
        content = self._clean(item.content)
        if await self.repository.has_tombstone(scope, fingerprint(content)) or (
            item.origin == "extracted" and item.source_message_id and
            await self.repository.has_tombstone_for_message(scope, item.source_message_id, 3600)):
            raise MemoryWriteError("MEMORY_PREVIOUSLY_FORGOTTEN")
        subject = await self.repository.ensure_subject(scope)
        if (subject.tenant_id, subject.subject_id) != (scope.tenant_id, scope.subject_id):
            raise MemoryScopeError()
        topic = sanitize_content(item.topic)[:80].strip()
        key = memory_key(topic, content)
        existing = await self._item(scope, replaces_id) if replaces_id else await self.repository.find_active_by_key(scope, key)
        if replaces_id:
            topic = existing.topic
            key = memory_key(topic, content)
        if existing is not None and (existing.tenant_id, existing.subject_id) != (scope.tenant_id, scope.subject_id):
            raise MemoryScopeError()
        if existing and existing.status == "active" and sanitize_content(existing.content) == content:
            return existing
        kind = item.kind if item.kind in KINDS else "fact"
        if existing is None:
            incoming = comparable(content)
            for row in await self.repository.list_live(scope, kind, 200):
                if (row.tenant_id, row.subject_id) != (scope.tenant_id, scope.subject_id) or row.status not in ("active", "pending"):
                    continue
                old = comparable(row.content)
                if old and (incoming in old or old in incoming):
                    if incoming in old and (row.status == "active" or (item.inferred and item.origin not in ("explicit", "manual"))):
                        return row
                    existing = row; break
        stored = replace(item, id=str(uuid.uuid4()), tenant_id=scope.tenant_id, subject_id=scope.subject_id, kind=kind,
                         content=content, topic=topic, normalized_key=key, importance=max(1, min(item.importance, 5)),
                         origin=item.origin or "extracted", valid_from=self.reader.now(),
                         status="pending" if item.inferred and item.origin not in ("manual", "explicit") else "active")
        stored = await self.repository.save_item(scope, stored, existing.id if existing else "")
        if not isinstance(stored, MemoryItem) or (stored.tenant_id, stored.subject_id) != (scope.tenant_id, scope.subject_id):
            raise MemoryScopeError()
        await self._capacity(scope, cfg); await self._rebuild(scope)
        if self.after_write:
            try:
                await self.after_write(scope, cfg, stored)
            except Exception:
                pass  # best-effort embedding can be supplied here; never fake a vector
        return stored

    async def update(self, context: MemoryContext, item_id: str, content: str, importance: int) -> MemoryItem:
        scope = resolve_scope(context)
        existing = await self._item(scope, item_id)
        sanitized = self._clean(content)
        await self.repository.update_item_content(scope, item_id, sanitized, memory_key(existing.topic, sanitized), max(1, min(importance, 5)))
        await self._rebuild(scope)
        updated = await self._item(scope, item_id)
        if self.after_write:
            try:
                cfg = await self.repository.workspace_config(scope.tenant_id) or MemoryConfig()
                await self.after_write(scope, cfg, updated)
            except Exception:
                pass
        return updated

    async def delete(self, context: MemoryContext, item_id: str) -> None:
        scope = resolve_scope(context)
        row = await self._item(scope, item_id)
        await self.repository.delete_item(scope, item_id, MemoryTombstone(row.topic, fingerprint(row.content), row.source_message_id))
        await self._rebuild(scope)

    async def clear(self, context: MemoryContext) -> int:
        scope, budget = resolve_scope(context), 500
        tombstones = []
        for status in ("active", "pending", "archived", "superseded"):
            if budget <= 0:
                break
            try:
                rows = await self.repository.list_items(scope, status, budget, 0)
            except Exception:
                continue
            for row in rows:
                if (row.tenant_id, row.subject_id) != (scope.tenant_id, scope.subject_id):
                    continue
                tombstones.append(MemoryTombstone(row.topic, fingerprint(row.content), row.source_message_id))
                budget -= 1
        removed = await self.repository.delete_all(scope, tuple(tombstones))
        await self.repository.delete_all_topics(scope)
        await self.repository.delete_all_doc_affinity(scope)
        await self._rebuild(scope)
        return removed

    async def confirm(self, context: MemoryContext, item_id: str) -> MemoryItem:
        scope = resolve_scope(context)
        await self._item(scope, item_id)
        await self.repository.confirm_pending_item(scope, item_id)
        try:
            cfg = await self.repository.workspace_config(scope.tenant_id) or MemoryConfig()
        except Exception:
            cfg = MemoryConfig()
        await self._capacity(scope, cfg); await self._rebuild(scope)
        return await self._item(scope, item_id)

    async def set_enabled(self, context: MemoryContext, enabled: bool) -> None:
        await self.repository.update_subject_enabled(resolve_scope(context), enabled)

    async def reject(self, context: MemoryContext, item_id: str) -> None:
        await self.delete(context, item_id)

    async def settings(self, context: MemoryContext) -> MemorySettings:
        scope = resolve_scope(context)
        try:
            cfg = await self.repository.workspace_config(scope.tenant_id) or MemoryConfig()
        except Exception:
            cfg = MemoryConfig()
        subject = await self.repository.get_subject(scope)
        if subject and (subject.tenant_id, subject.subject_id) != (scope.tenant_id, scope.subject_id):
            raise MemoryScopeError()
        enabled = subject.enabled if subject else True
        count = subject.item_count if subject else 0
        try:
            count = await self.repository.count_active(scope)
        except Exception:
            pass
        return MemorySettings(cfg.enabled, enabled, cfg.enabled and enabled,
                              "auto" if cfg.write_mode == "auto" else "explicit_only", cfg.effective_max_items(), count)
