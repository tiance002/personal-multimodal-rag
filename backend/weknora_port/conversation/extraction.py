"""Durable extraction worker orchestration matching fixed upstream mechanics."""
import asyncio
import hashlib
import math
import re
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .maintenance_types import ExtractionEnqueuer, ExtractionPayload, MaintenanceModel, MaintenanceRepository, MessageCursor, ModelRequest, TranscriptLine, TranscriptSegment
from .management import MemoryManager, MemoryWriteError, memory_key
from .prompts import EXTRACTION_SCHEMA_JSON, EXTRACTION_SYSTEM
from .ranking import KINDS, sanitize_content
from .topics import parse_object
from .types import MemoryConfig, MemoryContext, MemoryItem, MemoryScopeError, MemoryVectorQuery


class InvalidExtractionOutput(ValueError):
    pass


def build_extraction_prompt(segment, existing, forgotten, topics, instructions=""):
    out = []
    if segment.context:
        out.append("Earlier in this conversation (context only, do not record from these):\n")
        out.extend(f"- {line}\n" for line in segment.context); out.append("\n")
    out.append("Existing notes:\n")
    if not existing: out.append("(none)\n")
    out.extend(f"[{i}] [{row.kind}] (topic: {row.topic}) {row.content}\n" for i, row in enumerate(existing))
    if forgotten:
        out.append("\nThe user deleted notes about these topics. Do not re-add them unless this transcript says something genuinely new about them:\n")
        out.extend(f"- {row.topic}\n" for row in forgotten if row.topic)
    if topics:
        out.append("\nSubjects already tracked for this user:\n")
        out.extend(f"- {row.topic}\n" for row in [r for r in topics if r.topic][:12])
        out.append('Reuse one of these labels EXACTLY only when the transcript is about the SAME subject,\njust worded differently. Being in the same domain is not enough: if\n"门店排班管理" is tracked and the user asks how a shift swap gets approved, that\nis a different subject ("排班审批流程") — building the roster and approving\nchanges to it are different things this person does.\nWhen nothing above names the same subject, write a new label at the same level of\ngenerality as these. Do not force a fit, and do not name the individual question.\n')
    if instructions:
        out.append("\nWorkspace rules (follow these in addition to the above):\n<rules>\n" + instructions + "\n</rules>\n")
    out.append("\nWhat the user said:\n<transcript>\n")
    out.extend(f"[{i}] ({line.at.strftime('%Y-%m-%d %H:%M')}) {line.content}\n" for i, line in enumerate(segment.lines, 1))
    out.append("</transcript>\n")
    return "".join(out)


def expiry(value, now):
    if not isinstance(value, str) or value.strip().lower() in ("", "null"): return None
    try:
        value = value.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?", value): return None
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc) if len(value) == 10 else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None and parsed > now else None
    except ValueError: return None


class ExtractionWorker:
    def __init__(self, repository: MaintenanceRepository, manager: MemoryManager, model: MaintenanceModel | None = None, enqueuer: ExtractionEnqueuer | None = None, topics=None, maintenance=None):
        self.repository, self.manager, self.model, self.enqueuer = repository, manager, model, enqueuer
        self.topics, self.maintenance = topics, maintenance

    async def model_id(self, scope, cfg, payload):
        return cfg.extract_model_id or payload.chat_model_id or (await self.model.workspace_chat_model(scope) if self.model else "")

    async def enqueue(self, payload, delay):
        try:
            await self.enqueuer.enqueue(payload, delay, 2)
        except Exception:
            await self.repository.release_extraction_slot(payload.scope, "")
            raise

    async def schedule(self, context, session_id, message_id, chat_model_id="", language=""):
        scope, cfg, _ = await self.manager.reader.enabled_scope(context)
        if scope is None or cfg.write_mode != "auto" or not session_id or not message_id or self.enqueuer is None: return False
        try:
            subject = await self.repository.ensure_subject(scope)
            if not subject.enabled or (subject.tenant_id, subject.subject_id) != (scope.tenant_id, scope.subject_id): return False
            previous, should = await self.repository.enqueue_pending_session(scope, session_id, cfg.extract_min_interval() + cfg.extract_delay() + 600)
            if not should: return False
            delay = cfg.extract_delay()
            if previous.last_extracted_at:
                delay = max(delay, cfg.extract_min_interval() - (self.manager.reader.now() - previous.last_extracted_at).total_seconds())
            await self.enqueue(ExtractionPayload(scope, session_id, message_id, chat_model_id, language), delay)
            return True
        except Exception: return False

    def scoped_message(self, row, scope, session):
        return (row.tenant_id, row.subject_id, row.session_id) == (scope.tenant_id, scope.subject_id, session)

    async def collect(self, scope, session):
        rows = await self.repository.messages_after_cursor(scope, session.session_id, session.cursor, 41)
        # The adapter must page/filter before LIMIT; defense rejects stray rows.
        rows = [row for row in rows if self.scoped_message(row, scope, session.session_id)]
        more, rows = len(rows) > 40, rows[:40]
        segments, lines, last, end = [], [], None, None
        for row in rows:
            content = row.content.strip(); user = row.role == "user" and bool(content)
            if user and last and row.created_at - last > timedelta(hours=1) and end:
                segments.append(TranscriptSegment(session.session_id, tuple(lines), end)); lines, end = [], None
            end = MessageCursor(row.created_at, row.id)
            if user:
                last = row.created_at
                lines.append(TranscriptLine(session.session_id, row.id, row.created_at, content[:1000]))
        if end: segments.append(TranscriptSegment(session.session_id, tuple(lines), end))
        for index, segment in enumerate(segments):
            if not segment.lines: continue
            if index == 0:
                try:
                    before = await self.repository.messages_before_time(scope, session.session_id, segment.lines[0].at, 16)
                    context = tuple(row.content.strip()[:1000] for row in before if self.scoped_message(row, scope, session.session_id) and row.role == "user" and row.content.strip())[-4:]
                except Exception: context = ()
            else: context = tuple(line.content for line in segments[index - 1].lines[-4:])
            segments[index] = replace(segment, context=context)
        return segments, more

    async def relevant(self, scope, cfg, segment):
        existing = [r for r in await self.repository.list_active_by_kinds(scope, KINDS, 16) if self.manager.reader.active(r, scope)]
        if len(existing) <= 15: return existing
        if cfg.vector_enabled() and cfg.embedding_model_id and self.manager.reader.embedder:
            try:
                vector = tuple(await asyncio.wait_for(self.manager.reader.embedder.embed_query(scope, cfg.embedding_model_id, "".join(line.content + "\n" for line in segment.lines)), 2))
                if not vector or not all(type(v) in (int, float) and math.isfinite(v) for v in vector): return existing[:15]
                hits = await self.repository.search_items_by_vector(scope, MemoryVectorQuery(cfg.embedding_model_id, vector, KINDS, .5, 15))
                rows = [h.item for h in hits if math.isfinite(h.score) and h.score >= .5 and self.manager.reader.active(h.item, scope) and h.item.kind in KINDS][:15]
                if rows: return rows
            except Exception: pass
        return existing[:15]

    async def extract_segment(self, context, cfg, payload, segment):
        scope = payload.scope
        existing = await self.relevant(scope, cfg, segment)
        forgotten = await self.repository.list_tombstones(scope, 30)
        topics = await self.repository.top_topics(scope, 40)
        if any((r.tenant_id, r.subject_id) != (scope.tenant_id, scope.subject_id) for r in topics): raise MemoryScopeError()
        model_id = await self.model_id(scope, cfg, payload)
        if not model_id or self.model is None: raise RuntimeError("MEMORY_EXTRACTION_MODEL_UNAVAILABLE")
        identity = hashlib.sha256(f"{scope.tenant_id}:{scope.subject_id}:{segment.session_id}:{segment.end.at.isoformat()}:{segment.end.id}".encode()).hexdigest()
        prompt = build_extraction_prompt(segment, existing, forgotten, topics, cfg.extract_instructions)
        response = await self.model.complete(ModelRequest(scope, model_id, EXTRACTION_SYSTEM, prompt, EXTRACTION_SCHEMA_JSON, 1200, operation_id=identity + ":extract:0", language=payload.language))
        if response is None: raise InvalidExtractionOutput()
        if not response.content.strip() or response.finish_reason == "length":
            response = await self.model.complete(ModelRequest(scope, model_id, EXTRACTION_SYSTEM, prompt, EXTRACTION_SCHEMA_JSON, 4000, operation_id=identity + ":extract:1", language=payload.language))
            if response is None or not response.content.strip() or response.finish_reason == "length": raise InvalidExtractionOutput()
        try:
            parsed = parse_object(response.content)
            decisions = parsed.get("memories") or []
            if not isinstance(decisions, list): raise ValueError()
            labels = parsed.get("topics") or []
            if not isinstance(labels, list) or any(not isinstance(v, str) for v in labels): raise ValueError()
            for decision in decisions:
                if not isinstance(decision, dict): raise ValueError()
                for field in ("action", "kind", "topic", "content", "expires_at"):
                    if decision.get(field) is not None and not isinstance(decision[field], str): raise ValueError()
                for field in ("target", "source", "importance"):
                    if decision.get(field) is not None and type(decision[field]) is not int: raise ValueError()
                if decision.get("inferred") is not None and type(decision["inferred"]) is not bool: raise ValueError()
        except (ValueError, TypeError, KeyError) as error:
            raise InvalidExtractionOutput() from error
        await self.apply(context, segment, existing, decisions, identity)
        if self.topics: await self.topics.observe(context, labels, model_id, identity)

    async def apply(self, context, segment, existing, decisions, identity):
        scope = resolve_worker_scope(context)
        seen, applied = set(), 0
        for index, decision in enumerate(decisions):
            if applied >= 8: break
            if not isinstance(decision, dict): continue
            action = str(decision.get("action", "")).strip().lower()
            if action not in ("add", "update", "delete"): continue
            topic = sanitize_content(decision.get("topic") or "")[:80]; content = decision.get("content") or ""
            target = None
            if action in ("update", "delete"):
                address = decision.get("target")
                if address is not None:
                    if type(address) is not int or not 0 <= address < len(existing): continue
                    target = existing[address]
                else: target = await self.repository.find_active_by_key(scope, memory_key(topic, content))
                if target is None: continue
                if (target.tenant_id, target.subject_id) != (scope.tenant_id, scope.subject_id): raise MemoryScopeError()
                topic = target.topic
            key = memory_key(topic, content)
            if key and key in seen: continue
            operation = f"{identity}:decision:{index}"
            if action == "delete":
                await self.repository.supersede_item(scope, target.id, "", operation)
                await self.manager._rebuild(scope)
            else:
                if not isinstance(content, str) or not sanitize_content(content): continue
                source_index = decision.get("source")
                source = segment.lines[source_index - 1] if type(source_index) is int and 1 <= source_index <= len(segment.lines) else segment.lines[0]
                importance = decision.get("importance") or 0
                if type(importance) is not int: continue
                row = MemoryItem("", 0, "", decision.get("kind") or "fact", content, topic=topic, importance=importance,
                                 source_session_id=source.session_id, source_message_id=source.message_id,
                                 expires_at=expiry(decision.get("expires_at"), self.manager.reader.now()), inferred=decision.get("inferred") is True, operation_id=operation)
                try: await self.manager.remember(context, row, replaces_id=target.id if target else "")
                except MemoryWriteError as error:
                    if str(error) in ("MEMORY_PREVIOUSLY_FORGOTTEN", "MEMORY_SENSITIVE_CONTENT", "MEMORY_CONFLICT"): continue
                    raise
            seen.add(key); applied += 1

    async def handle(self, payload: ExtractionPayload):
        if not payload.scope.valid(): return
        scope = payload.scope
        cfg = await self.repository.workspace_config(scope.tenant_id) or MemoryConfig()
        if not cfg.enabled or cfg.write_mode != "auto":
            await self.repository.release_extraction_slot(scope, ""); return
        subject = await self.repository.ensure_subject(scope)
        if (subject.tenant_id, subject.subject_id) != (scope.tenant_id, scope.subject_id): raise MemoryScopeError()
        if not subject.enabled:
            await self.repository.release_extraction_slot(scope, ""); return
        lease = str(uuid.uuid4())
        batch = await self.repository.claim_pending_sessions(scope, payload.session_id, lease, 600)
        if batch is None: return
        if batch.retry_at:
            if self.enqueuer is None: raise RuntimeError("MEMORY_EXTRACTION_LEASED")
            await self.enqueue(payload, (batch.retry_at - self.manager.reader.now()).total_seconds() + 1); return
        try:
            async with asyncio.timeout(540):
                await self.repository.expire_overdue(scope)
                context = MemoryContext(scope.tenant_id, scope.subject_id)
                processed, invalid = 0, False
                for session in batch.sessions:
                    segments, more = await self.collect(scope, session)
                    if not segments: await self.repository.checkpoint_extraction(scope, lease, session, session.cursor, True)
                    for index, segment in enumerate(segments):
                        if segment.lines:
                            try: await self.extract_segment(context, cfg, payload, segment)
                            except InvalidExtractionOutput:
                                skip = await self.repository.record_extraction_failure(scope, lease, session, segment.end, "invalid_model_output")
                                if not skip:
                                    invalid = True; processed += 1; break
                        await self.repository.checkpoint_extraction(scope, lease, session, segment.end, not more and index == len(segments) - 1)
                        session = replace(session, cursor=segment.end); processed += 1
                        if processed >= 3: break
                    if processed >= 3: break
                await self.repository.finish_extraction(scope, lease)
                if self.enqueuer and await self.repository.has_pending_extraction(scope):
                    _, should = await self.repository.enqueue_pending_session(scope, "", cfg.extract_min_interval() + cfg.extract_delay() + 600)
                    if should: await self.enqueue(payload, 15)
                if invalid and self.enqueuer is None: raise InvalidExtractionOutput()
                if self.maintenance: await self.maintenance.review(context, await self.model_id(scope, cfg, payload))
        finally:
            # Cleanup survives cancellation and is bounded like source WithoutCancel.
            cleanup = asyncio.create_task(self.repository.release_extraction_slot(scope, lease))
            try: await asyncio.wait_for(asyncio.shield(cleanup), 5)
            except Exception: cleanup.cancel()


def resolve_worker_scope(context):
    from .types import resolve_scope
    return resolve_scope(context)
