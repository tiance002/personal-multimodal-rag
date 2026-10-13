"""Fixed WeKnora scoped memory PostgreSQL repository (MIT; see manifest).

Injected psycopg-compatible connection only. Import never opens a database.
Every entry requires the separately verified new-instance identity. All mutation
callbacks serialize on one tenant+subject row and participate in its transaction.
"""
from __future__ import annotations

import json
import math
import struct
import uuid
from decimal import Decimal
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Mapping, Sequence

from ..conversation.types import Attachment, DocumentAffinity, MemoryConfig, MemoryItem, MemoryScope, MemoryScopeError, MemorySubject, MemoryTombstone, MemoryVectorHit, MemoryVectorQuery, StoredMessage
from ..conversation.management import MemoryWriteError, fingerprint, normalize_topic_key
from ..conversation.maintenance_types import ExtractionBatch, ExtractionPayload, ExtractionSession, MessageCursor, TopicStat
from .postgres import Connection, IsolationPolicy, PostgresRetriever

SCOPE_SQL = "tenant_id=%s AND subject_id=%s"
ACTIVE_SQL = "status='active' AND (expires_at IS NULL OR expires_at>now())"


def decode_json(value, default):
    if value is None: return default
    return json.loads(value) if isinstance(value, str) else value


def item_from_row(row):
    names = {field.name for field in fields(MemoryItem)}
    values = {key: value for key, value in row.items() if key in names}
    for name in ("replaces_id", "superseded_by", "source_message_id", "source_session_id", "operation_id"):
        if name in values and values[name] is None: values[name] = ""
    return MemoryItem(**values)


def subject_from_row(row):
    names = {field.name for field in fields(MemorySubject)}
    return MemorySubject(**{key: value for key, value in row.items() if key in names})


def topic_from_row(row):
    return TopicStat(row["id"], row["tenant_id"], row["subject_id"], row["topic"], row["normalized_key"],
                     tuple(decode_json(row.get("aliases"), [])), row.get("hits", 0), row.get("promoted_at"))


def encode_vector(vector):
    if not vector or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
        raise ValueError("INVALID_MEMORY_EMBEDDING")
    try: raw = struct.pack("<" + "f" * len(vector), *vector)
    except (OverflowError, struct.error) as error: raise ValueError("INVALID_MEMORY_EMBEDDING") from error
    # Go's []float32 is the source of truth, including literal conversion.
    values = struct.unpack("<" + "f" * len(vector), raw)
    if any(not math.isfinite(v) for v in values): raise ValueError("INVALID_MEMORY_EMBEDDING")
    literals = []
    for value in values:
        for precision in range(1, 10):
            candidate = format(value, f".{precision}g")
            if struct.pack("<f", float(candidate)) == struct.pack("<f", value): break
        literals.append(format(Decimal(candidate), "f"))
    return raw, "[" + ",".join(literals) + "]"


def decode_vector(raw):
    if raw is None: return ()
    raw = bytes(raw)
    if len(raw) < 4: return ()
    result = struct.unpack("<" + "f" * (len(raw) // 4), raw[:len(raw) // 4 * 4])
    return result if all(math.isfinite(v) for v in result) else ()


class PostgresMemoryRepository:
    def __init__(self, connection: Connection, isolation: IsolationPolicy, *,
                 identity_guard: PostgresRetriever | None = None,
                 config_loader: Callable[[int], Awaitable[MemoryConfig | None]] | None = None):
        self.connection = connection
        self.identity_guard = identity_guard or PostgresRetriever(connection, isolation)
        if self.identity_guard.connection is not connection or self.identity_guard.isolation != isolation:
            raise PermissionError("DATABASE_IDENTITY_GUARD_MISMATCH")
        self.config_loader = config_loader

    async def verify_identity(self): return await self.identity_guard.verify_identity()

    def _scope(self, scope):
        self.identity_guard.require_verified()
        if not isinstance(scope, MemoryScope) or not scope.valid(): raise MemoryScopeError()
        return (scope.tenant_id, scope.subject_id)

    async def _rows(self, sql, params=()):
        self.identity_guard.require_verified()
        return await (await self.connection.execute(sql, params)).fetchall()

    async def _one(self, sql, params=()):
        rows = await self._rows(sql, params)
        return rows[0] if rows else None

    async def _execute(self, sql, params=()):
        self.identity_guard.require_verified()
        return await self.connection.execute(sql, params)

    @asynccontextmanager
    async def _locked(self, scope):
        args = self._scope(scope)
        async with self.connection.transaction():
            await self._execute("INSERT INTO memory_subjects(id,tenant_id,subject_id) VALUES(%s,%s,%s) ON CONFLICT(tenant_id,subject_id) DO NOTHING", (str(uuid.uuid4()), *args))
            row = await self._one("SELECT *,now() AS server_now FROM memory_subjects WHERE " + SCOPE_SQL + " FOR UPDATE", args)
            if row is None: raise MemoryWriteError("MEMORY_SUBJECT_UNAVAILABLE")
            if (row["tenant_id"], row["subject_id"]) != args: raise MemoryScopeError()
            yield row

    async def _invalidate(self, scope):
        await self._execute("UPDATE memory_subjects SET block_text='',cache_revision=cache_revision+1,block_updated_at=NULL,updated_at=now() WHERE " + SCOPE_SQL, self._scope(scope))

    async def _operation(self, scope, operation, kind):
        if not operation: return None
        row = await self._one("SELECT kind,result_id FROM memory_operations WHERE " + SCOPE_SQL + " AND operation_id=%s", (*self._scope(scope), operation))
        if row and row["kind"] != kind: raise MemoryWriteError("MEMORY_OPERATION_CONFLICT")
        return row

    async def _record_operation(self, scope, operation, kind, result=""):
        if operation:
            await self._execute("INSERT INTO memory_operations(tenant_id,subject_id,operation_id,kind,result_id) VALUES(%s,%s,%s,%s,%s)", (*self._scope(scope), operation, kind, result))

    async def workspace_config(self, tenant_id):
        self.identity_guard.require_verified()
        if type(tenant_id) is not int or tenant_id <= 0: raise MemoryScopeError()
        if self.config_loader: return await self.config_loader(tenant_id)
        row = await self._one("SELECT memory_config FROM tenants WHERE id=%s", (tenant_id,))
        value = decode_json(row.get("memory_config") if row else None, {})
        names = {field.name for field in fields(MemoryConfig)}
        return MemoryConfig(**{key: data for key, data in value.items() if key in names}) if value else None

    async def get_subject(self, scope):
        row = await self._one("SELECT * FROM memory_subjects WHERE " + SCOPE_SQL, self._scope(scope))
        return subject_from_row(row) if row else None

    async def ensure_subject(self, scope):
        async with self._locked(scope) as row: return subject_from_row(row)

    async def update_subject_enabled(self, scope, enabled):
        async with self._locked(scope):
            await self._execute("UPDATE memory_subjects SET enabled=%s,updated_at=now() WHERE " + SCOPE_SQL, (bool(enabled), *self._scope(scope)))

    async def update_subject_block(self, scope, block, item_count, expected_revision):
        async with self._locked(scope):
            row = await self._one("UPDATE memory_subjects SET block_text=%s,item_count=%s,block_updated_at=now(),updated_at=now() WHERE " + SCOPE_SQL + " AND cache_revision=%s RETURNING cache_revision", (block, item_count, *self._scope(scope), expected_revision))
            return row is not None

    async def get_item(self, scope, item_id):
        row = await self._one("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND id=%s", (*self._scope(scope), item_id))
        return item_from_row(row) if row else None

    async def list_active_by_kinds(self, scope, kinds, limit):
        if not kinds: self._scope(scope); return ()
        rows = await self._rows("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND " + ACTIVE_SQL + " AND kind=ANY(%s) ORDER BY importance DESC,valid_from DESC LIMIT %s", (*self._scope(scope), list(kinds), limit if limit > 0 else 2147483647))
        return tuple(map(item_from_row, rows))

    async def list_active_resident(self, scope, limit):
        rows = await self._rows("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND " + ACTIVE_SQL + " AND (kind=ANY(%s) OR origin='explicit') ORDER BY importance DESC,valid_from DESC LIMIT %s", (*self._scope(scope), ["profile", "preference", "interest"], limit if limit > 0 else 2147483647))
        return tuple(map(item_from_row, rows))

    async def list_items(self, scope, status, limit, offset):
        rows = await self._rows("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND (%s='' OR status=%s) ORDER BY valid_from DESC,id DESC LIMIT %s OFFSET %s", (*self._scope(scope), status, status, limit if limit > 0 else 50, max(offset, 0)))
        return tuple(map(item_from_row, rows))

    async def list_live(self, scope, kind, limit):
        rows = await self._rows("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND status IN ('active','pending') AND (expires_at IS NULL OR expires_at>now()) AND kind=%s ORDER BY importance DESC,valid_from DESC LIMIT %s", (*self._scope(scope), kind, limit if limit > 0 else 2147483647))
        return tuple(map(item_from_row, rows))

    async def find_active_by_key(self, scope, key):
        if not key: self._scope(scope); return None
        row = await self._one("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND normalized_key=%s AND status IN ('active','pending') ORDER BY valid_from DESC LIMIT 1", (*self._scope(scope), key))
        return item_from_row(row) if row else None

    async def count_active(self, scope):
        row = await self._one("SELECT count(*) AS count FROM memory_items WHERE " + SCOPE_SQL + " AND status='active'", self._scope(scope))
        return row["count"]

    async def touch_used(self, scope, ids):
        async with self._locked(scope):
            if ids: await self._execute("UPDATE memory_items SET last_used_at=now(),use_count=use_count+1 WHERE " + SCOPE_SQL + " AND id=ANY(%s)", (*self._scope(scope), list(ids)))

    async def save_item(self, scope, item, replaces_id):
        if (item.tenant_id, item.subject_id) != self._scope(scope): raise MemoryScopeError()
        async with self._locked(scope):
            replay = await self._operation(scope, item.operation_id, "save_item")
            if replay:
                saved = await self.get_item(scope, replay["result_id"])
                if saved is None: raise MemoryWriteError("MEMORY_CONFLICT")
                return saved
            target = await self.get_item(scope, replaces_id) if replaces_id else None
            if replaces_id and (target is None or target.status not in ("active", "pending")):
                old = await self.get_item(scope, target.superseded_by) if target and target.superseded_by else None
                if old and old.status == item.status and old.content == item.content:
                    await self._record_operation(scope, item.operation_id, "save_item", old.id); return old
                raise MemoryWriteError("MEMORY_CONFLICT")
            live = tuple(map(item_from_row, await self._rows("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND normalized_key=%s AND status IN ('active','pending')", (*self._scope(scope), item.normalized_key))))
            duplicate = next((r for r in live if r.content == item.content and r.status == item.status), None)
            if duplicate:
                await self._record_operation(scope, item.operation_id, "save_item", duplicate.id); return duplicate
            if item.status == "pending":
                link = target.id if target and target.status == "active" else target.replaces_id if target else ""
                link = link or next((r.id for r in live if r.status == "active"), "")
                item = replace(item, replaces_id=link)
            columns = ("id","tenant_id","subject_id","kind","content","topic","normalized_key","importance","origin","status","source_session_id","source_message_id","valid_from","expires_at","replaces_id","operation_id")
            await self._execute("INSERT INTO memory_items(" + ",".join(columns) + ") VALUES(" + ",".join(["%s"] * len(columns)) + ")", tuple(getattr(item, name) for name in columns))
            candidates = (*live, *((target,) if target else ()))
            ids = {r.id for r in candidates if item.status != "pending" or r.status != "active"}
            if target and target.replaces_id and item.status == "active": ids.add(target.replaces_id)
            if ids:
                await self._execute("UPDATE memory_items SET status='superseded',invalid_at=now(),superseded_by=%s,updated_at=now() WHERE " + SCOPE_SQL + " AND id<>%s AND (id=ANY(%s) OR replaces_id=ANY(%s)) AND status IN ('active','pending')", (item.id, *self._scope(scope), item.id, list(ids), list(ids)))
            await self._invalidate(scope)
            await self._record_operation(scope, item.operation_id, "save_item", item.id)
            return item

    async def confirm_pending_item(self, scope, item_id):
        async with self._locked(scope):
            item = await self.get_item(scope, item_id)
            if item and item.status == "active": return
            if item is None or item.status != "pending" or (item.expires_at and item.expires_at <= datetime.now(timezone.utc)): raise MemoryWriteError("MEMORY_CONFLICT")
            if item.replaces_id:
                target = await self.get_item(scope, item.replaces_id)
                if target is None or target.status != "active": raise MemoryWriteError("MEMORY_CONFLICT")
            await self._execute("UPDATE memory_items SET status='superseded',invalid_at=now(),superseded_by=%s,updated_at=now() WHERE " + SCOPE_SQL + " AND id<>%s AND (normalized_key=%s OR id=%s OR (replaces_id<>'' AND replaces_id=%s)) AND status IN ('active','pending')", (item_id, *self._scope(scope), item_id, item.normalized_key, item.replaces_id, item.replaces_id))
            await self._execute("UPDATE memory_items SET status='active',updated_at=now() WHERE " + SCOPE_SQL + " AND id=%s", (*self._scope(scope), item_id))
            await self._invalidate(scope)

    async def update_item_content(self, scope, item_id, content, key, importance):
        async with self._locked(scope):
            current = await self.get_item(scope, item_id)
            if current is None: raise MemoryWriteError("MEMORY_ITEM_NOT_FOUND")
            if current.content != content:
                await self._execute("DELETE FROM memory_item_embeddings WHERE " + SCOPE_SQL + " AND item_id=%s", (*self._scope(scope), item_id))
                await self._execute("UPDATE memory_items SET status='superseded',invalid_at=now(),superseded_by=%s WHERE " + SCOPE_SQL + " AND replaces_id=%s AND status='pending'", (item_id, *self._scope(scope), item_id))
            await self._execute("UPDATE memory_items SET content=%s,normalized_key=%s,importance=%s,origin='manual',updated_at=now() WHERE " + SCOPE_SQL + " AND id=%s", (content, key, importance, *self._scope(scope), item_id))
            await self._invalidate(scope)

    async def supersede_item(self, scope, item_id, replacement_id, operation_id=""):
        async with self._locked(scope):
            if await self._operation(scope, operation_id, "supersede"): return
            await self._execute("UPDATE memory_items SET status='superseded',invalid_at=now(),superseded_by=%s,updated_at=now() WHERE " + SCOPE_SQL + " AND ((id=%s AND status='active') OR (replaces_id=%s AND status='pending'))", (replacement_id, *self._scope(scope), item_id, item_id))
            await self._invalidate(scope); await self._record_operation(scope, operation_id, "supersede", item_id)

    async def archive_lowest_ranked(self, scope, keep):
        async with self._locked(scope):
            if keep <= 0: return
            await self._execute("UPDATE memory_items SET status='archived',updated_at=now() WHERE " + SCOPE_SQL + " AND status='active' AND id NOT IN (SELECT id FROM memory_items WHERE " + SCOPE_SQL + " AND status='active' ORDER BY importance DESC,COALESCE(last_used_at,valid_from) DESC,valid_from DESC LIMIT %s)", (*self._scope(scope), *self._scope(scope), keep))
            await self._invalidate(scope)

    async def expire_overdue(self, scope):
        async with self._locked(scope):
            rows = await self._rows("UPDATE memory_items SET status='archived',updated_at=now() WHERE " + SCOPE_SQL + " AND status='active' AND expires_at IS NOT NULL AND expires_at<=now() RETURNING id", self._scope(scope))
            if rows: await self._invalidate(scope)
            return len(rows)

    async def _tombstones(self, scope, tombstones):
        for row in tombstones:
            if row.fingerprint:
                await self._execute("INSERT INTO memory_tombstones(id,tenant_id,subject_id,topic,fingerprint,source_message_id) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,subject_id,fingerprint) DO NOTHING", (str(uuid.uuid4()), *self._scope(scope), row.topic, row.fingerprint, row.source_message_id))
        await self._execute("DELETE FROM memory_tombstones WHERE " + SCOPE_SQL + " AND id NOT IN(SELECT id FROM memory_tombstones WHERE " + SCOPE_SQL + " ORDER BY created_at DESC LIMIT 500)", (*self._scope(scope), *self._scope(scope)))

    async def delete_item(self, scope, item_id, tombstone):
        async with self._locked(scope):
            current = await self.get_item(scope, item_id)
            if current is None: raise MemoryWriteError("MEMORY_ITEM_NOT_FOUND")
            # The editor may have changed this item after manager's pre-read.
            # Suppress actual deleted content atomically, plus its older snapshot.
            actual = MemoryTombstone(current.topic, fingerprint(current.content), current.source_message_id)
            await self._tombstones(scope, (actual, tombstone))
            await self._execute("UPDATE memory_items SET status='superseded',invalid_at=now() WHERE " + SCOPE_SQL + " AND replaces_id=%s AND status='pending'", (*self._scope(scope), item_id))
            await self._execute("DELETE FROM memory_item_embeddings WHERE " + SCOPE_SQL + " AND item_id=%s", (*self._scope(scope), item_id))
            await self._execute("DELETE FROM memory_items WHERE " + SCOPE_SQL + " AND id=%s", (*self._scope(scope), item_id))
            await self._invalidate(scope)

    async def delete_all(self, scope, tombstones):
        async with self._locked(scope):
            rows = await self._rows("SELECT topic,content,source_message_id FROM memory_items WHERE " + SCOPE_SQL + " ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'pending' THEN 1 WHEN 'archived' THEN 2 ELSE 3 END,valid_from DESC,id DESC LIMIT 500", self._scope(scope))
            actual = tuple(MemoryTombstone(row["topic"], fingerprint(row["content"]), row.get("source_message_id") or "") for row in rows)
            suppression = {row.fingerprint: row for row in (*actual, *tombstones) if row.fingerprint}
            await self._tombstones(scope, tuple(suppression.values())[:500])
            await self._execute("DELETE FROM memory_item_embeddings WHERE " + SCOPE_SQL, self._scope(scope))
            rows = await self._rows("DELETE FROM memory_items WHERE " + SCOPE_SQL + " RETURNING id", self._scope(scope))
            await self._invalidate(scope)
            return len(rows)

    async def has_tombstone(self, scope, digest):
        return bool(await self._one("SELECT 1 FROM memory_tombstones WHERE " + SCOPE_SQL + " AND fingerprint=%s LIMIT 1", (*self._scope(scope), digest))) if digest else bool(self._scope(scope) and False)

    async def has_tombstone_for_message(self, scope, message_id, within_seconds):
        row = await self._one("SELECT 1 FROM memory_tombstones WHERE " + SCOPE_SQL + " AND source_message_id=%s AND (%s<=0 OR created_at>now()-(%s*interval '1 second')) LIMIT 1", (*self._scope(scope), message_id, within_seconds, within_seconds))
        return row is not None

    async def list_tombstones(self, scope, limit):
        rows = await self._rows("SELECT topic,fingerprint,source_message_id FROM memory_tombstones WHERE " + SCOPE_SQL + " ORDER BY created_at DESC LIMIT %s", (*self._scope(scope), limit if limit > 0 else 2147483647))
        return tuple(MemoryTombstone(r["topic"], r["fingerprint"], r.get("source_message_id") or "") for r in rows)

    async def search_items_by_vector(self, scope, query: MemoryVectorQuery):
        args = self._scope(scope)
        if not query.model_id or not query.vector: return ()
        _, literal = encode_vector(query.vector)
        # Exact scoped ordering: source deliberately has no global HNSW index.
        rows = await self._rows("SELECT i.*,1-(e.embedding::halfvec <=> %s::halfvec) AS score FROM memory_item_embeddings e JOIN memory_items i ON i.id=e.item_id AND i.tenant_id=e.tenant_id AND i.subject_id=e.subject_id WHERE e.tenant_id=%s AND e.subject_id=%s AND e.model_id=%s AND e.dims=%s AND e.embedding IS NOT NULL AND i.status='active' AND (i.expires_at IS NULL OR i.expires_at>now()) AND (%s OR i.kind=ANY(%s)) ORDER BY e.embedding::halfvec <=> %s::halfvec LIMIT %s", (literal, *args, query.model_id, len(query.vector), not bool(query.kinds), list(query.kinds), literal, query.limit if query.limit > 0 else 20))
        return tuple(MemoryVectorHit(item_from_row(r), r["score"]) for r in rows if r["score"] >= query.min_score)

    async def upsert_item_embedding(self, scope, item, model_id, vector):
        raw, literal = encode_vector(vector)
        if not model_id: raise ValueError("MEMORY_EMBEDDING_MODEL_REQUIRED")
        if (item.tenant_id, item.subject_id) != self._scope(scope): raise MemoryScopeError()
        async with self._locked(scope):
            current = await self.get_item(scope, item.id)
            if current is None or current.status not in ("active", "pending") or (current.content, current.topic) != (item.content, item.topic): return
            await self._execute("INSERT INTO memory_item_embeddings(item_id,tenant_id,subject_id,model_id,dims,vector,embedding) VALUES(%s,%s,%s,%s,%s,%s,%s::halfvec) ON CONFLICT(item_id) DO UPDATE SET model_id=excluded.model_id,dims=excluded.dims,vector=excluded.vector,embedding=excluded.embedding,updated_at=now() WHERE memory_item_embeddings.tenant_id=excluded.tenant_id AND memory_item_embeddings.subject_id=excluded.subject_id", (item.id, *self._scope(scope), model_id, len(vector), raw, literal))

    async def delete_item_embedding(self, scope, item_id):
        async with self._locked(scope):
            await self._execute("DELETE FROM memory_item_embeddings WHERE " + SCOPE_SQL + " AND item_id=%s", (*self._scope(scope), item_id))

    async def item_embeddings(self, scope, item_ids, model_id):
        args = self._scope(scope)
        if not item_ids or not model_id: return {}
        rows = await self._rows("SELECT item_id,vector FROM memory_item_embeddings WHERE " + SCOPE_SQL + " AND item_id=ANY(%s) AND model_id=%s", (*args, list(item_ids), model_id))
        return {row["item_id"]: vector for row in rows if (vector := decode_vector(row["vector"]))}

    async def items_missing_embeddings(self, scope, model_id, limit):
        rows = await self._rows("SELECT * FROM memory_items WHERE " + SCOPE_SQL + " AND status IN ('active','pending') AND id NOT IN(SELECT item_id FROM memory_item_embeddings WHERE " + SCOPE_SQL + " AND model_id=%s) ORDER BY valid_from DESC LIMIT %s", (*self._scope(scope), *self._scope(scope), model_id, limit if limit > 0 else 20))
        return tuple(map(item_from_row, rows))

    async def sync_vector_column(self, scope, limit):
        async with self._locked(scope):
            rows = await self._rows("SELECT item_id,vector FROM memory_item_embeddings WHERE " + SCOPE_SQL + " AND embedding IS NULL AND vector IS NOT NULL ORDER BY updated_at DESC LIMIT %s", (*self._scope(scope), limit if limit > 0 else 2000))
            moved = 0
            for row in rows:
                vector = decode_vector(row["vector"])
                if not vector: continue
                _, literal = encode_vector(vector)
                await self._execute("UPDATE memory_item_embeddings SET embedding=%s::halfvec WHERE " + SCOPE_SQL + " AND item_id=%s AND embedding IS NULL", (literal, *self._scope(scope), row["item_id"]))
                moved += 1
            return moved

    async def mark_consolidated(self, scope, forced):
        column = "forced_consolidated_at" if forced else "consolidated_at"
        async with self._locked(scope):
            await self._execute(f"UPDATE memory_subjects SET {column}=now(),updated_at=now() WHERE " + SCOPE_SQL, self._scope(scope))

    async def top_topics(self, scope, limit):
        rows = await self._rows("SELECT * FROM memory_topic_stats WHERE " + SCOPE_SQL + " ORDER BY hits DESC,last_seen_at DESC LIMIT %s", (*self._scope(scope), limit if limit > 0 else 20))
        return tuple(map(topic_from_row, rows))

    async def topic_by_id(self, scope, topic_id):
        row = await self._one("SELECT * FROM memory_topic_stats WHERE " + SCOPE_SQL + " AND id=%s", (*self._scope(scope), topic_id))
        return topic_from_row(row) if row else None

    async def topic_by_key(self, scope, key):
        row = await self._one("SELECT * FROM memory_topic_stats WHERE " + SCOPE_SQL + " AND normalized_key=%s", (*self._scope(scope), key))
        return topic_from_row(row) if row else None

    async def bump_topic(self, scope, topic, key, alias, operation_id):
        async with self._locked(scope):
            replay = await self._operation(scope, operation_id, "bump_topic")
            if replay:
                row = await self.topic_by_id(scope, replay["result_id"])
                if row is None: raise MemoryWriteError("MEMORY_OPERATION_CONFLICT")
                return row
            await self._execute("INSERT INTO memory_topic_stats(id,tenant_id,subject_id,topic,normalized_key) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,subject_id,normalized_key) DO NOTHING", (str(uuid.uuid4()), *self._scope(scope), topic, key))
            row = await self.topic_by_key(scope, key)
            aliases = list(row.aliases)
            if alias and normalize_topic_key(alias) != key and not any(normalize_topic_key(a) == normalize_topic_key(alias) for a in aliases):
                aliases = (*aliases, alias)[-12:]
            saved = await self._one("UPDATE memory_topic_stats SET hits=hits+1,aliases=%s::jsonb,last_seen_at=now(),updated_at=now() WHERE " + SCOPE_SQL + " AND normalized_key=%s RETURNING *", (json.dumps(aliases, ensure_ascii=False), *self._scope(scope), key))
            await self._record_operation(scope, operation_id, "bump_topic", row.id)
            return topic_from_row(saved)

    async def mark_topic_promoted(self, scope, key):
        async with self._locked(scope):
            await self._execute("UPDATE memory_topic_stats SET promoted_at=now(),updated_at=now() WHERE " + SCOPE_SQL + " AND normalized_key=%s", (*self._scope(scope), key))

    async def rename_topic(self, scope, old_key, new_key, label):
        async with self._locked(scope):
            if not old_key or not new_key or old_key == new_key: return False
            if await self.topic_by_key(scope, new_key): return False
            current = await self.topic_by_key(scope, old_key)
            if current is None: return False
            aliases = [a for a in current.aliases if normalize_topic_key(a) != new_key]
            if current.topic and not any(normalize_topic_key(a) == normalize_topic_key(current.topic) for a in aliases): aliases.append(current.topic)
            aliases = aliases[-12:]
            row = await self._one("UPDATE memory_topic_stats SET normalized_key=%s,topic=%s,aliases=%s::jsonb,updated_at=now() WHERE " + SCOPE_SQL + " AND normalized_key=%s RETURNING id", (new_key, label, json.dumps(aliases, ensure_ascii=False), *self._scope(scope), old_key))
            await self._invalidate(scope)
            return row is not None

    async def delete_topic(self, scope, topic_id, tombstones):
        async with self._locked(scope):
            if await self.topic_by_id(scope, topic_id) is None: raise MemoryWriteError("MEMORY_ITEM_NOT_FOUND")
            await self._tombstones(scope, tombstones)
            await self._execute("DELETE FROM memory_topic_stats WHERE " + SCOPE_SQL + " AND id=%s", (*self._scope(scope), topic_id))
            await self._invalidate(scope)

    async def delete_all_topics(self, scope):
        async with self._locked(scope):
            await self._execute("DELETE FROM memory_topic_stats WHERE " + SCOPE_SQL, self._scope(scope))
            await self._invalidate(scope)

    async def top_doc_affinity(self, scope, limit):
        rows = await self._rows("SELECT knowledge_id,title,hits,knowledge_base_id FROM memory_doc_affinity WHERE " + SCOPE_SQL + " ORDER BY hits DESC,last_used_at DESC LIMIT %s", (*self._scope(scope), limit if limit > 0 else 20))
        return tuple(DocumentAffinity(**row) for row in rows)

    async def doc_affinity(self, scope, knowledge_ids):
        rows = await self._rows("SELECT knowledge_id,hits FROM memory_doc_affinity WHERE " + SCOPE_SQL + " AND knowledge_id=ANY(%s)", (*self._scope(scope), list(knowledge_ids)))
        return {r["knowledge_id"]: r["hits"] for r in rows}

    async def bump_doc_affinity(self, scope, refs, operation_id):
        async with self._locked(scope):
            if await self._operation(scope, operation_id, "doc_affinity"): return
            seen = set()
            for ref in refs:
                if not ref.knowledge_id or ref.knowledge_id in seen: continue
                seen.add(ref.knowledge_id)
                await self._execute("INSERT INTO memory_doc_affinity(id,tenant_id,subject_id,knowledge_id,knowledge_base_id,title,hits,last_used_at) VALUES(%s,%s,%s,%s,%s,%s,1,now()) ON CONFLICT(tenant_id,subject_id,knowledge_id) DO UPDATE SET hits=memory_doc_affinity.hits+1,title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE memory_doc_affinity.title END,knowledge_base_id=CASE WHEN excluded.knowledge_base_id<>'' THEN excluded.knowledge_base_id ELSE memory_doc_affinity.knowledge_base_id END,last_used_at=now(),updated_at=now()", (str(uuid.uuid4()), *self._scope(scope), ref.knowledge_id, ref.knowledge_base_id, ref.title))
            await self._record_operation(scope, operation_id, "doc_affinity")

    async def delete_all_doc_affinity(self, scope):
        async with self._locked(scope):
            await self._execute("DELETE FROM memory_doc_affinity WHERE " + SCOPE_SQL, self._scope(scope))

    def _state(self, subject):
        value = decode_json(subject.get("extraction_state"), {})
        until = value.get("lease_until")
        return value.get("lease_id", ""), datetime.fromisoformat(until.replace("Z", "+00:00")) if until else None

    async def _state_write(self, scope, lease="", until=None, scheduled=None):
        state = {"lease_id": lease, "lease_until": until.isoformat()} if lease and until else {}
        await self._execute("UPDATE memory_subjects SET extraction_state=%s::jsonb,extract_scheduled_at=%s,pending_sessions='[]'::jsonb,updated_at=now() WHERE " + SCOPE_SQL, (json.dumps(state), scheduled, *self._scope(scope)))

    async def _enqueue_session(self, scope, subject, session_id, bump):
        if not session_id: return
        conflict = "DO UPDATE SET revision=memory_extraction_sessions.revision+1,pending=true,updated_at=now()" if bump else "DO NOTHING"
        await self._execute("INSERT INTO memory_extraction_sessions(tenant_id,subject_id,session_id,revision,pending,cursor_at,updated_at) VALUES(%s,%s,%s,1,true,%s,now()) ON CONFLICT(tenant_id,subject_id,session_id) " + conflict, (*self._scope(scope), session_id, subject.get("extract_cursor")))

    async def _legacy_queue(self, scope, subject):
        for session in decode_json(subject.get("pending_sessions"), []):
            await self._enqueue_session(scope, subject, session, False)
        await self._execute("UPDATE memory_subjects SET pending_sessions='[]'::jsonb WHERE " + SCOPE_SQL, self._scope(scope))

    async def has_pending_extraction(self, scope):
        return await self._one("SELECT 1 FROM memory_extraction_sessions WHERE " + SCOPE_SQL + " AND pending=true LIMIT 1", self._scope(scope)) is not None

    async def enqueue_pending_session(self, scope, session_id, timeout_seconds):
        async with self._locked(scope) as subject:
            await self._legacy_queue(scope, subject)
            await self._enqueue_session(scope, subject, session_id, True)
            now = subject["server_now"]
            lease, until = self._state(subject)
            scheduled = subject.get("extract_scheduled_at")
            queued = scheduled is not None and now - scheduled < timedelta(seconds=timeout_seconds)
            should = not (lease and until and until > now) and not queued and await self.has_pending_extraction(scope)
            if should:
                await self._state_write(scope, lease, until, now)
            return subject_from_row(subject), bool(should)

    async def claim_pending_sessions(self, scope, fallback_session, lease_id, ttl_seconds):
        if not lease_id: raise ValueError("MEMORY_EXTRACTION_LEASE_REQUIRED")
        async with self._locked(scope) as subject:
            now, (lease, until) = subject["server_now"], self._state(subject)
            if lease and until and until > now: return ExtractionBatch(retry_at=until)
            await self._legacy_queue(scope, subject)
            await self._enqueue_session(scope, subject, fallback_session, False)
            rows = await self._rows("SELECT * FROM memory_extraction_sessions WHERE " + SCOPE_SQL + " AND pending=true ORDER BY updated_at ASC,session_id ASC LIMIT 32", self._scope(scope))
            if not rows: return None
            await self._state_write(scope, lease_id, now + timedelta(seconds=ttl_seconds), subject.get("extract_scheduled_at"))
            return ExtractionBatch(tuple(ExtractionSession(row["session_id"], MessageCursor(row.get("cursor_at"), row.get("cursor_id") or ""), row["revision"]) for row in rows))

    def _lease(self, subject, lease_id, *, allow_expired=False):
        current, until = self._state(subject)
        if current != lease_id or not current or (not allow_expired and (until is None or until <= subject["server_now"])):
            raise MemoryWriteError("MEMORY_EXTRACTION_LEASE_LOST")

    async def release_extraction_slot(self, scope, lease_id):
        async with self._locked(scope) as subject:
            current, _ = self._state(subject)
            if current == lease_id: await self._state_write(scope)

    async def finish_extraction(self, scope, lease_id):
        async with self._locked(scope) as subject:
            self._lease(subject, lease_id, allow_expired=True)
            await self._state_write(scope)
            await self._execute("UPDATE memory_subjects SET last_extracted_at=now() WHERE " + SCOPE_SQL, self._scope(scope))

    async def _progress(self, scope, session_id):
        row = await self._one("SELECT * FROM memory_extraction_sessions WHERE " + SCOPE_SQL + " AND session_id=%s", (*self._scope(scope), session_id))
        if row is None: raise MemoryWriteError("MEMORY_EXTRACTION_SESSION_MISSING")
        return row

    async def checkpoint_extraction(self, scope, lease_id, session, end, complete):
        async with self._locked(scope) as subject:
            self._lease(subject, lease_id)
            row = await self._progress(scope, session.session_id)
            cursor = MessageCursor(row.get("cursor_at"), row.get("cursor_id") or "")
            if end.at is not None and (cursor.at is None or (end.at, end.id) > (cursor.at, cursor.id)): cursor = end
            pending = not complete or row["revision"] != session.revision
            await self._execute("UPDATE memory_extraction_sessions SET cursor_at=%s,cursor_id=%s,pending=%s,updated_at=now(),failure_count=CASE WHEN failed_at IS NULL THEN 0 ELSE failure_count END,failure_code=CASE WHEN failed_at IS NULL THEN '' ELSE failure_code END WHERE " + SCOPE_SQL + " AND session_id=%s", (cursor.at, cursor.id, pending, *self._scope(scope), session.session_id))

    async def record_extraction_failure(self, scope, lease_id, session, end, code):
        if code != "invalid_model_output": raise ValueError("INVALID_MEMORY_FAILURE_CODE")
        async with self._locked(scope) as subject:
            self._lease(subject, lease_id)
            row = await self._progress(scope, session.session_id)
            start = MessageCursor(row.get("cursor_at"), row.get("cursor_id") or "")
            same = row.get("failed_from_at") == start.at and (row.get("failed_from_id") or "") == start.id
            attempts = 1 + (row.get("failure_count", 0) if same else 0)
            skip = attempts >= 3
            await self._execute("UPDATE memory_extraction_sessions SET failure_count=%s,failure_code=%s,failed_from_at=%s,failed_from_id=%s,failed_to_at=%s,failed_to_id=%s,failed_at=%s,updated_at=now() WHERE " + SCOPE_SQL + " AND session_id=%s", (attempts, code, start.at, start.id, end.at, end.id, subject["server_now"] if skip else None, *self._scope(scope), session.session_id))
            return skip

    def _message(self, row):
        attachments = tuple(Attachment(**value) for value in decode_json(row.get("attachments"), []))
        return StoredMessage(row["id"], row.get("request_id") or "", row["session_id"], row["tenant_id"], row["subject_id"], row["role"], row.get("content") or "", row["created_at"], row.get("rendered_content") or "", tuple(decode_json(row.get("image_captions"), [])), attachments, tuple(decode_json(row.get("knowledge_references"), [])))

    async def recent_messages(self, scope, session_id, limit):
        rows = await self._rows("SELECT * FROM (SELECT * FROM messages WHERE " + SCOPE_SQL + " AND session_id=%s AND deleted_at IS NULL ORDER BY created_at DESC,id DESC LIMIT %s) recent ORDER BY created_at ASC,id ASC", (*self._scope(scope), session_id, limit))
        return tuple(map(self._message, rows))

    async def messages_after_cursor(self, scope, session_id, cursor, limit):
        rows = await self._rows("SELECT * FROM messages WHERE " + SCOPE_SQL + " AND session_id=%s AND deleted_at IS NULL AND (%s::timestamptz IS NULL OR created_at>%s OR (created_at=%s AND id>%s)) ORDER BY created_at ASC,id ASC LIMIT %s", (*self._scope(scope), session_id, cursor.at, cursor.at, cursor.at, cursor.id, limit))
        return tuple(map(self._message, rows))

    async def messages_before_time(self, scope, session_id, at, limit):
        rows = await self._rows("SELECT * FROM(SELECT * FROM messages WHERE " + SCOPE_SQL + " AND session_id=%s AND deleted_at IS NULL AND created_at<%s ORDER BY created_at DESC,id DESC LIMIT %s) previous ORDER BY created_at ASC,id ASC", (*self._scope(scope), session_id, at, limit))
        return tuple(map(self._message, rows))


@dataclass(frozen=True)
class MemoryExtractionJob:
    id: str
    payload: ExtractionPayload
    lease_id: str
    lease_until: datetime
    attempts: int
    max_retry: int


class PostgresExtractionEnqueuer:
    """Asynq scheduling contract on a new scoped PG durable-task backend.

    Subject pending/cursor/worker lease remains authoritative. No automatic
    polling/thread/provider call here. Runtime supplies worker identity, timeout
    and retry_at explicitly; this adapter adds no alternative backoff policy.
    Only queued duplicates coalesce: a running task may enqueue its successor.
    """
    def __init__(self, repository: PostgresMemoryRepository):
        self.repository = repository

    async def enqueue(self, payload: ExtractionPayload, delay_seconds: float, max_retry: int):
        if not isinstance(payload, ExtractionPayload): raise ValueError("INVALID_MEMORY_JOB_PAYLOAD")
        if max_retry != 2 or not math.isfinite(delay_seconds) or delay_seconds < 0:
            raise ValueError("INVALID_MEMORY_JOB_SCHEDULE")
        scope = payload.scope
        value = {"tenant_id": scope.tenant_id, "subject_id": scope.subject_id,
                 "session_id": payload.session_id, "message_id": payload.message_id,
                 "chat_model_id": payload.chat_model_id, "language": payload.language}
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib
        key = hashlib.sha256(text.encode()).hexdigest()
        async with self.repository._locked(scope):
            await self.repository._execute("INSERT INTO memory_extract_jobs(id,tenant_id,subject_id,job_key,payload,due_at,max_retry) VALUES(%s,%s,%s,%s,%s::jsonb,now()+(%s*interval '1 second'),%s) ON CONFLICT(tenant_id,subject_id,job_key) WHERE state='queued' DO NOTHING", (str(uuid.uuid4()), *self.repository._scope(scope), key, text, delay_seconds, max_retry))

    async def claim_due(self, scope: MemoryScope, lease_id: str, ttl_seconds: int = 600):
        if not lease_id or ttl_seconds <= 0: raise ValueError("INVALID_MEMORY_JOB_LEASE")
        async with self.repository._locked(scope):
            row = await self.repository._one("SELECT * FROM memory_extract_jobs WHERE " + SCOPE_SQL + " AND ((state='queued' AND due_at<=now()) OR (state='running' AND lease_until<=now())) ORDER BY due_at ASC,id ASC LIMIT 1 FOR UPDATE SKIP LOCKED", self.repository._scope(scope))
            if row is None: return None
            if row["attempts"] > row["max_retry"]:
                await self.repository._execute("UPDATE memory_extract_jobs SET state='failed',failure_code='delivery_exhausted',lease_id='',lease_until=NULL,payload='{}'::jsonb,updated_at=now() WHERE " + SCOPE_SQL + " AND id=%s", (*self.repository._scope(scope), row["id"]))
                return None
            # A crashed delivery may be reclaimed; successful completion is
            # fenced by lease_id, and worker subject leases fence side effects.
            claimed = await self.repository._one("UPDATE memory_extract_jobs SET state='running',lease_id=%s,lease_until=now()+(%s*interval '1 second'),attempts=attempts+1,updated_at=now() WHERE " + SCOPE_SQL + " AND id=%s RETURNING *", (lease_id, ttl_seconds, *self.repository._scope(scope), row["id"]))
            value = decode_json(claimed["payload"], {})
            if (value.get("tenant_id"), value.get("subject_id")) != self.repository._scope(scope): raise MemoryScopeError()
            payload = ExtractionPayload(scope, value["session_id"], value["message_id"], value.get("chat_model_id", ""), value.get("language", ""))
            return MemoryExtractionJob(claimed["id"], payload, lease_id, claimed["lease_until"], claimed["attempts"], claimed["max_retry"])

    async def complete_job(self, job: MemoryExtractionJob):
        async with self.repository._locked(job.payload.scope):
            row = await self.repository._one("UPDATE memory_extract_jobs SET state='completed',lease_id='',lease_until=NULL,payload='{}'::jsonb,updated_at=now() WHERE " + SCOPE_SQL + " AND id=%s AND state='running' AND lease_id=%s AND lease_until>now() RETURNING id", (*self.repository._scope(job.payload.scope), job.id, job.lease_id))
            if row is None: raise MemoryWriteError("MEMORY_JOB_LEASE_LOST")

    async def fail_job(self, job: MemoryExtractionJob, *, retry_at: datetime | None):
        """Runtime chooses source task retry time; only fixed safe code persists.
        Initial delivery plus max_retry2; exhausted job is terminal. The durable
        subject queue is retained, so a later user turn can schedule pending work.
        """
        async with self.repository._locked(job.payload.scope):
            current = await self.repository._one("SELECT * FROM memory_extract_jobs WHERE " + SCOPE_SQL + " AND id=%s AND state='running' AND lease_id=%s AND lease_until>now() FOR UPDATE", (*self.repository._scope(job.payload.scope), job.id, job.lease_id))
            if current is None: raise MemoryWriteError("MEMORY_JOB_LEASE_LOST")
            terminal = current["attempts"] > current["max_retry"] or retry_at is None
            if not terminal and (retry_at.tzinfo is None): raise ValueError("MEMORY_JOB_RETRY_TIME_REQUIRED")
            if terminal:
                await self.repository._execute("UPDATE memory_extract_jobs SET state='failed',failure_code='worker_failed',lease_id='',lease_until=NULL,payload='{}'::jsonb,updated_at=now() WHERE " + SCOPE_SQL + " AND id=%s", (*self.repository._scope(job.payload.scope), job.id))
            else:
                # A queued successor may already exist; do not violate the partial
                # unique key or discard its payload. Retire this delivery and let
                # the successor drain the authoritative subject queue.
                successor = await self.repository._one("SELECT id FROM memory_extract_jobs WHERE " + SCOPE_SQL + " AND job_key=%s AND state='queued' LIMIT 1", (*self.repository._scope(job.payload.scope), current["job_key"]))
                state = "completed" if successor else "queued"
                await self.repository._execute("UPDATE memory_extract_jobs SET state=%s,due_at=%s,lease_id='',lease_until=NULL,failure_code='worker_failed',updated_at=now() WHERE " + SCOPE_SQL + " AND id=%s", (state, retry_at, *self.repository._scope(job.payload.scope), job.id))
