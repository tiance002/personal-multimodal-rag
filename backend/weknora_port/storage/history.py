"""Local-owner adapter for fixed WeKnora messages and ContinueStream identity.

Every statement uses the already verified, dedicated physical connection.
The ownership and immutable Scope columns are minimal privacy wrappers around
the upstream message repository, not a different history selection algorithm.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models.types import TrustedScope
from ..streaming.replay import AssistantRun, FinalCommit
from .postgres import PostgresRetriever


@dataclass(frozen=True)
class LocalPrincipal:
    tenant_id: int
    subject_id: str

    def __post_init__(self):
        if type(self.tenant_id) is not int or self.tenant_id <= 0 or not self.subject_id:
            raise PermissionError("LOCAL_PRINCIPAL_REQUIRED")


class PostgresHistoryRepository:
    def __init__(self, repository: PostgresRetriever, principal: LocalPrincipal):
        self.repository, self.principal = repository, principal

    async def _rows(self, sql, args=()):
        self.repository.require_verified()
        return await (await self.repository.connection.execute(sql, args)).fetchall()

    def _owner(self):
        return self.principal.tenant_id, self.principal.subject_id

    async def validate_session(self, session_id: str, principal: Any) -> None:
        if principal != self.principal:
            raise PermissionError("SESSION_OWNER_MISMATCH")
        rows = await self._rows("SELECT id FROM sessions WHERE id=%s AND tenant_id=%s "
                                "AND subject_id=%s AND deleted_at IS NULL", (session_id, *self._owner()))
        if len(rows) != 1:
            raise LookupError("SESSION_NOT_FOUND")

    async def validate_assistant_message(self, session_id: str, message_id: str) -> AssistantRun:
        await self.validate_session(session_id, self.principal)
        rows = await self._rows("SELECT request_id,id FROM messages WHERE session_id=%s AND id=%s "
                                "AND tenant_id=%s AND subject_id=%s AND role='assistant' "
                                "AND deleted_at IS NULL", (session_id, message_id, *self._owner()))
        if len(rows) != 1:
            raise LookupError("ASSISTANT_MESSAGE_NOT_FOUND")
        return AssistantRun(rows[0]["request_id"], rows[0]["id"])

    async def create_turn(self, session_id: str, request_id: str, user_message_id: str,
                          assistant_message_id: str, content: str, scope: TrustedScope) -> AssistantRun:
        if scope.owner_id != self.principal.subject_id or not content.strip():
            raise PermissionError("MESSAGE_SCOPE_OR_CONTENT_INVALID")
        self.repository.require_verified()
        async with self.repository.connection.transaction():
            rows = await self._rows("SELECT id FROM sessions WHERE id=%s AND tenant_id=%s "
                                    "AND subject_id=%s AND deleted_at IS NULL FOR UPDATE",
                                    (session_id, *self._owner()))
            if len(rows) != 1:
                raise LookupError("SESSION_NOT_FOUND")
            existing = await self._rows("SELECT id,role,content,knowledge_base_scope,document_scope "
                "FROM messages WHERE session_id=%s AND request_id=%s AND tenant_id=%s AND subject_id=%s "
                "AND deleted_at IS NULL", (session_id, request_id, *self._owner()))
            if existing:
                questions = [row for row in existing if row["role"] == "user"]
                answers = [row for row in existing if row["role"] == "assistant"]
                if (len(questions) != 1 or len(answers) != 1 or questions[0]["content"] != content
                        or tuple(questions[0]["knowledge_base_scope"]) != scope.knowledge_base_ids
                        or tuple(questions[0]["document_scope"]) != scope.document_ids):
                    raise ValueError("REQUEST_PAYLOAD_CONFLICT")
                return AssistantRun(request_id, answers[0]["id"])
            for message_id, role, body in ((user_message_id, "user", content),
                                           (assistant_message_id, "assistant", "")):
                await self.repository.connection.execute("INSERT INTO messages "
                    "(id,request_id,session_id,tenant_id,subject_id,role,content,knowledge_base_scope,document_scope) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                    (message_id, request_id, session_id, *self._owner(), role, body,
                     json.dumps(scope.knowledge_base_ids), json.dumps(scope.document_ids)))
        return AssistantRun(request_id, assistant_message_id)

    async def commit_final(self, commit: FinalCommit) -> None:
        self.repository.require_verified()
        references = json.dumps(commit.references, ensure_ascii=False, allow_nan=False)
        usage = json.dumps(commit.usage, allow_nan=False)
        async with self.repository.connection.transaction():
            # Serialize with session deletion as well as repeated final callbacks.
            sessions = await self._rows("SELECT id FROM sessions WHERE id=%s AND tenant_id=%s "
                "AND subject_id=%s AND deleted_at IS NULL FOR UPDATE", (commit.session_id, *self._owner()))
            if len(sessions) != 1:
                raise LookupError("SESSION_NOT_FOUND")
            rows = await self._rows("SELECT content,knowledge_references,usage,is_completed FROM messages "
                "WHERE session_id=%s AND id=%s AND request_id=%s AND tenant_id=%s AND subject_id=%s "
                "AND role='assistant' AND deleted_at IS NULL FOR UPDATE",
                (commit.session_id, commit.message_id, commit.request_id, *self._owner()))
            if len(rows) != 1:
                raise LookupError("ASSISTANT_MESSAGE_NOT_FOUND")
            previous = rows[0]
            if previous["is_completed"]:
                if (previous["content"] != commit.answer or previous["knowledge_references"] != json.loads(references)
                        or previous["usage"] != commit.usage):
                    raise ValueError("FINAL_COMMIT_CONFLICT")
                return
            await self.repository.connection.execute("UPDATE messages SET content=%s,rendered_content=%s,"
                "knowledge_references=%s::jsonb,usage=%s::jsonb,is_completed=true,updated_at=now() "
                "WHERE id=%s AND session_id=%s AND request_id=%s AND tenant_id=%s AND subject_id=%s",
                (commit.answer, commit.answer, references, usage, commit.message_id, commit.session_id,
                 commit.request_id, *self._owner()))

    async def message_origin_scopes(self, session_id: str) -> tuple[tuple[str, ...], ...]:
        """Origins for outbound checks; never broaden the retrieval Scope."""
        await self.validate_session(session_id, self.principal)
        rows = await self._rows("SELECT knowledge_base_scope FROM messages WHERE session_id=%s "
            "AND tenant_id=%s AND subject_id=%s AND deleted_at IS NULL ORDER BY created_at,id",
            (session_id, *self._owner()))
        return tuple(tuple(row["knowledge_base_scope"]) for row in rows)
