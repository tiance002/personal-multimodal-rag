"""PostgreSQL persistence for the actual generation.types.AttemptLifecycle.

No provider or budget calls. The constructor is bound by trusted server scope to
one tenant/session/assistant message. Import never connects to a database.
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import asdict

from ..generation.types import AttemptRecord, GenerationError, GenerationReplay
from ..models.types import CallContext, TokenUsage
from .postgres import PostgresRetriever

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_TERMINAL = frozenset({"COMPLETED", "TRUNCATED", "FAILED", "CANCELLED"})
_IDENTITY = ("attempt_id", "ordinal", "role", "provider", "model_requested", "endpoint",
             "envelope_sha256", "prompt_sha256", "context_sha256", "upgrade_reason")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _object(value):
    return json.loads(value) if isinstance(value, str) else value


class PostgresGenerationLifecycle:
    def __init__(self, identity_guard: PostgresRetriever, *, tenant_id: int, session_id: str, message_id: str):
        if type(tenant_id) is not int or tenant_id <= 0 or not all(isinstance(v, str) and v for v in (session_id, message_id)):
            raise GenerationError("GENERATION_STORAGE_BINDING_REQUIRED")
        self.identity_guard = identity_guard
        self.connection = identity_guard.connection
        self.tenant_id, self.session_id, self.message_id = tenant_id, session_id, message_id

    def _context(self, context):
        self.identity_guard.require_verified()
        if not isinstance(context, CallContext) or not context.scope.owner_id or context.session_id != self.session_id:
            raise GenerationError("GENERATION_STORAGE_SCOPE_MISMATCH")
        if not isinstance(context.scope.knowledge_base_ids, tuple) or not isinstance(context.scope.document_ids, tuple):
            raise GenerationError("GENERATION_STORAGE_SCOPE_MISMATCH")
        return (self.tenant_id, context.scope.owner_id, self.session_id, self.message_id)

    async def _rows(self, sql, params):
        self.identity_guard.require_verified()
        return await (await self.connection.execute(sql, params)).fetchall()

    async def _one(self, sql, params):
        rows = await self._rows(sql, params)
        return rows[0] if rows else None

    @asynccontextmanager
    async def _locked_message(self, context):
        tenant, owner, session, message = self._context(context)
        async with self.connection.transaction():
            row = await self._one("SELECT id FROM sessions WHERE id=%s AND tenant_id=%s AND subject_id=%s AND deleted_at IS NULL FOR UPDATE", (session, tenant, owner))
            if row is None: raise GenerationError("GENERATION_STORAGE_SCOPE_DENIED")
            row = await self._one("SELECT id,role,is_completed FROM messages WHERE id=%s AND tenant_id=%s AND subject_id=%s AND session_id=%s AND deleted_at IS NULL FOR UPDATE", (message, tenant, owner, session))
            if row is None or row["role"] != "assistant": raise GenerationError("GENERATION_STORAGE_MESSAGE_DENIED")
            yield row

    async def _run(self, run_id, context):
        row = await self._one("SELECT * FROM generation_runs WHERE run_id=%s AND tenant_id=%s AND subject_id=%s AND session_id=%s AND message_id=%s FOR UPDATE", (run_id, *self._context(context)))
        if row is None: raise GenerationError("GENERATION_RUN_NOT_CLAIMED")
        if tuple(_object(row["knowledge_base_ids"])) != context.scope.knowledge_base_ids or tuple(_object(row["document_ids"])) != context.scope.document_ids:
            raise GenerationError("GENERATION_RUN_SCOPE_CONFLICT")
        return row

    async def claim_run(self, run_id: str, payload_sha256: str, context: CallContext):
        self._context(context)
        if not run_id or context.request_id != run_id or not _SHA.fullmatch(payload_sha256):
            raise GenerationError("GENERATION_RUN_IDENTITY_MISMATCH")
        async with self._locked_message(context) as message:
            existing = await self._one("SELECT * FROM generation_runs WHERE run_id=%s AND tenant_id=%s AND subject_id=%s AND session_id=%s AND message_id=%s FOR UPDATE", (run_id, *self._context(context)))
            if existing:
                if existing["payload_sha256"] != payload_sha256 or tuple(_object(existing["knowledge_base_ids"])) != context.scope.knowledge_base_ids or tuple(_object(existing["document_ids"])) != context.scope.document_ids:
                    raise GenerationError("GENERATION_RUN_PAYLOAD_CONFLICT")
                # Never proceed to another provider call/reservation on replay.
                raise GenerationReplay()
            if message["is_completed"]: raise GenerationError("GENERATION_MESSAGE_ALREADY_COMPLETED")
            saved = await self._one("INSERT INTO generation_runs(run_id,tenant_id,subject_id,session_id,message_id,payload_sha256,knowledge_base_ids,document_ids) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT DO NOTHING RETURNING run_id", (run_id, *self._context(context), payload_sha256, _json(context.scope.knowledge_base_ids), _json(context.scope.document_ids)))
            if saved is None: raise GenerationError("GENERATION_RUN_IDENTITY_CONFLICT")

    def _record(self, record, context, *, finishing=False):
        self._context(context)
        if not isinstance(record, AttemptRecord): raise GenerationError("GENERATION_ATTEMPT_RECORD_REQUIRED")
        if type(record.ordinal) is not int or record.ordinal not in (1, 2) or record.role not in ("cheap", "expensive", "baseline"):
            raise GenerationError("GENERATION_ATTEMPT_LIMIT")
        suffix = f"/answer/{record.ordinal}/{record.role}"
        if not record.attempt_id.endswith(suffix) or context.request_id != record.attempt_id:
            raise GenerationError("GENERATION_ATTEMPT_IDENTITY_MISMATCH")
        run_id = record.attempt_id[:-len(suffix)]
        if not run_id or not all(_SHA.fullmatch(getattr(record, name)) for name in ("envelope_sha256", "prompt_sha256", "context_sha256")):
            raise GenerationError("GENERATION_ATTEMPT_IDENTITY_MISMATCH")
        if not all(isinstance(getattr(record, name), str) and getattr(record, name) for name in ("provider", "model_requested", "endpoint")):
            raise GenerationError("GENERATION_ATTEMPT_MODEL_IDENTITY_REQUIRED")
        if record.upgrade_reason is not None and record.upgrade_reason not in ("MODEL_CALL_TIMEOUT", "TRUNCATION", "MODEL_EMPTY", "INVALID_HANDLES"):
            raise GenerationError("GENERATION_UPGRADE_REASON_INVALID")
        if finishing:
            if record.status not in _TERMINAL: raise GenerationError("GENERATION_ATTEMPT_TERMINAL_REQUIRED")
            if not isinstance(record.usage, TokenUsage): raise GenerationError("GENERATION_USAGE_INVALID")
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(record.usage, name)
                if value is not None and (type(value) is not int or value < 0): raise GenerationError("GENERATION_USAGE_INVALID")
            if record.usage.status not in ("UNKNOWN", "REPORTED"): raise GenerationError("GENERATION_USAGE_INVALID")
            if not isinstance(record.objective, tuple) or any(v not in ("TRUNCATED", "EMPTY", "INVALID_HANDLES") for v in record.objective):
                raise GenerationError("GENERATION_OBJECTIVE_INVALID")
            if record.model_reported is not None and not isinstance(record.model_reported, str): raise GenerationError("GENERATION_REPORTED_MODEL_INVALID")
            if record.error_code is not None and not _SAFE_CODE.fullmatch(record.error_code): raise GenerationError("GENERATION_ERROR_CODE_INVALID")
            if not _SAFE_CODE.fullmatch(record.cost_status): raise GenerationError("GENERATION_COST_STATUS_INVALID")
            if type(record.invalid_handle_count) is not int or record.invalid_handle_count < 0: raise GenerationError("GENERATION_INVALID_HANDLE_COUNT")
        elif record.status != "STARTED" or record.model_reported is not None:
            raise GenerationError("GENERATION_ATTEMPT_START_REQUIRED")
        return run_id

    async def begin_attempt(self, record: AttemptRecord, context: CallContext):
        run_id = self._record(record, context)
        async with self._locked_message(context) as message:
            await self._run(run_id, context)
            # Read all rows, not one: ordinal and two-attempt policy are enforced
            # under the locked run so concurrent duplicate begins cannot race.
            rows = await self._rows("SELECT * FROM generation_attempts WHERE run_id=%s AND tenant_id=%s AND subject_id=%s ORDER BY ordinal FOR UPDATE", (run_id, self.tenant_id, context.scope.owner_id))
            identity = {name: getattr(record, name) for name in _IDENTITY}
            duplicate = next((r for r in rows if r["attempt_id"] == record.attempt_id or r["ordinal"] == record.ordinal), None)
            if duplicate:
                if any(duplicate[name] != identity[name] for name in _IDENTITY): raise GenerationError("GENERATION_ATTEMPT_IDENTITY_CONFLICT")
                raise GenerationReplay()
            if message["is_completed"]: raise GenerationError("GENERATION_MESSAGE_ALREADY_COMPLETED")
            if record.ordinal != len(rows) + 1 or len(rows) >= 2: raise GenerationError("GENERATION_ATTEMPT_LIMIT")
            if record.ordinal == 1 and record.upgrade_reason is not None: raise GenerationError("GENERATION_UPGRADE_REASON_INVALID")
            if record.ordinal == 2:
                first = rows[0]
                if first["role"] != "cheap" or first["status"] not in _TERMINAL or record.role != "expensive" or record.upgrade_reason is None:
                    raise GenerationError("GENERATION_ATTEMPT_UPGRADE_DENIED")
                observed = set(_object(first["objective"]))
                allowed = ((record.upgrade_reason == "MODEL_CALL_TIMEOUT" and first["status"] == "FAILED" and first["error_code"] == "MODEL_CALL_TIMEOUT") or
                           (first["status"] in ("COMPLETED", "TRUNCATED") and
                            ((record.upgrade_reason == "TRUNCATION" and "TRUNCATED" in observed) or
                             (record.upgrade_reason == "MODEL_EMPTY" and "EMPTY" in observed) or
                             (record.upgrade_reason == "INVALID_HANDLES" and "INVALID_HANDLES" in observed))))
                if not allowed or (first["prompt_sha256"],first["context_sha256"]) != (record.prompt_sha256,record.context_sha256):
                    raise GenerationError("GENERATION_ATTEMPT_UPGRADE_DENIED")
            columns = ("attempt_id","run_id","tenant_id","subject_id","ordinal","role","provider","model_requested","endpoint","envelope_sha256","prompt_sha256","context_sha256","upgrade_reason","usage")
            params = (record.attempt_id,run_id,self.tenant_id,context.scope.owner_id,record.ordinal,record.role,record.provider,record.model_requested,record.endpoint,record.envelope_sha256,record.prompt_sha256,record.context_sha256,record.upgrade_reason,_json(asdict(record.usage)))
            await self.connection.execute("INSERT INTO generation_attempts(" + ",".join(columns) + ") VALUES(" + ",".join(["%s"] * (len(columns) - 1)) + ",%s::jsonb)", params)

    async def finish_attempt(self, record: AttemptRecord, context: CallContext):
        run_id = self._record(record, context, finishing=True)
        async with self._locked_message(context):
            await self._run(run_id, context)
            row = await self._one("SELECT * FROM generation_attempts WHERE attempt_id=%s AND run_id=%s AND tenant_id=%s AND subject_id=%s FOR UPDATE", (record.attempt_id,run_id,self.tenant_id,context.scope.owner_id))
            if row is None: raise GenerationError("GENERATION_ATTEMPT_NOT_STARTED")
            if any(row[name] != getattr(record, name) for name in _IDENTITY): raise GenerationError("GENERATION_ATTEMPT_IDENTITY_CONFLICT")
            terminal = {"model_reported":record.model_reported,"status":record.status,"finish_reason":record.finish_reason,
                        "usage":asdict(record.usage),"objective":list(record.objective),"cost_status":record.cost_status,
                        "error_code":record.error_code,"invalid_handle_count":record.invalid_handle_count}
            if row["status"] in _TERMINAL:
                stored = {name:_object(row[name]) if name in ("usage","objective") else row[name] for name in terminal}
                if stored != terminal: raise GenerationError("GENERATION_ATTEMPT_TERMINAL_CONFLICT")
                return  # persistence-only idempotency; never repeats settlement
            if row["status"] != "STARTED": raise GenerationError("GENERATION_ATTEMPT_STATE_INVALID")
            await self.connection.execute("UPDATE generation_attempts SET model_reported=%s,status=%s,finish_reason=%s,usage=%s::jsonb,objective=%s::jsonb,cost_status=%s,error_code=%s,invalid_handle_count=%s,finished_at=now() WHERE attempt_id=%s AND run_id=%s AND tenant_id=%s AND subject_id=%s AND status='STARTED'", (record.model_reported,record.status,record.finish_reason,_json(asdict(record.usage)),_json(list(record.objective)),record.cost_status,record.error_code,record.invalid_handle_count,record.attempt_id,run_id,self.tenant_id,context.scope.owner_id))
