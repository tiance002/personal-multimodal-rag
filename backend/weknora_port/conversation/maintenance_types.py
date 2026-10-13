"""Injected maintenance contracts; durable adapter, no process-local DB fallback."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, Sequence

from .management import ManagedMemoryRepository
from .types import DocumentAffinity, MemoryItem, MemoryScope, MemorySubject, MemoryTombstone, StoredMessage


@dataclass(frozen=True)
class MessageCursor:
    at: datetime | None = None
    id: str = ""


@dataclass(frozen=True)
class ExtractionSession:
    session_id: str
    cursor: MessageCursor = MessageCursor()
    revision: int = 1


@dataclass(frozen=True)
class ExtractionBatch:
    sessions: tuple[ExtractionSession, ...] = ()
    retry_at: datetime | None = None


@dataclass(frozen=True)
class ExtractionPayload:
    scope: MemoryScope
    session_id: str
    message_id: str
    chat_model_id: str = ""
    language: str = ""


@dataclass(frozen=True)
class TranscriptLine:
    session_id: str
    message_id: str
    at: datetime
    content: str = field(repr=False)


@dataclass(frozen=True)
class TranscriptSegment:
    session_id: str
    lines: tuple[TranscriptLine, ...]
    end: MessageCursor
    context: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class TopicStat:
    id: str
    tenant_id: int
    subject_id: str
    topic: str
    normalized_key: str
    aliases: tuple[str, ...] = ()
    hits: int = 0
    promoted_at: datetime | None = None


@dataclass(frozen=True)
class ModelRequest:
    scope: MemoryScope
    model_id: str
    system: str = field(repr=False)
    user: str = field(repr=False)
    schema_json: str
    max_completion_tokens: int
    temperature: int = 0
    thinking: bool = False
    operation_id: str = ""
    language: str = ""


@dataclass(frozen=True)
class ModelResponse:
    content: str = field(repr=False)
    finish_reason: str = ""


@dataclass(frozen=True)
class ConsolidationResult:
    reviewed: int = 0
    expired: int = 0
    demoted: int = 0
    candidates: int = 0
    merged: int = 0
    backfilled: int = 0
    skipped: str = ""


class MaintenanceModel(Protocol):
    """Actual adapter MUST enforce scope/model/egress/budget before every call.
    workspace_chat_model returns the first usable knowledge-QA model in actual
    workspace order, or empty; never an unscoped arbitrary provider default.
    """
    async def workspace_chat_model(self, scope: MemoryScope) -> str: ...
    async def complete(self, request: ModelRequest) -> ModelResponse | None: ...


class ExtractionEnqueuer(Protocol):
    async def enqueue(self, payload: ExtractionPayload, delay_seconds: float, max_retry: int) -> None: ...


class MaintenanceRepository(ManagedMemoryRepository, Protocol):
    """Every operation checks tenant+subject. Lease ownership gates checkpoints.
    Enqueue increments per-session revision; checkpoint clears pending ONLY when
    captured revision still matches. Cursor compares (timestamp,id), never time
    alone. Expired leases can be reclaimed; release cannot clear another lease.
    Mutating memory callbacks use the existing subject lock/cache-revision CAS
    contract. operation_id deduplicates partially replayed decisions/topic bumps
    atomically with their side effects, never suppresses failed operations.
    Claimed sessions sort updated_at ASC, session_id ASC and cap at source32.
    Invalid-output failure count is keyed to the current start cursor; only
    the third recorded failure returns true to permit advancing with evidence.
    Embedding upsert compare-checks source_content/topic and current live row;
    late vectors never revive a deleted, superseded or edited memory.
    """
    async def enqueue_pending_session(self, scope: MemoryScope, session_id: str, timeout_seconds: int) -> tuple[MemorySubject, bool]: ...
    async def claim_pending_sessions(self, scope: MemoryScope, fallback_session: str, lease_id: str, ttl_seconds: int) -> ExtractionBatch | None: ...
    async def release_extraction_slot(self, scope: MemoryScope, lease_id: str) -> None: ...
    async def checkpoint_extraction(self, scope: MemoryScope, lease_id: str, session: ExtractionSession, end: MessageCursor, complete: bool) -> None: ...
    async def record_extraction_failure(self, scope: MemoryScope, lease_id: str, session: ExtractionSession, end: MessageCursor, code: str) -> bool: ...
    async def finish_extraction(self, scope: MemoryScope, lease_id: str) -> None: ...
    async def has_pending_extraction(self, scope: MemoryScope) -> bool: ...
    async def messages_after_cursor(self, scope: MemoryScope, session_id: str, cursor: MessageCursor, limit: int) -> Sequence[StoredMessage]: ...
    async def messages_before_time(self, scope: MemoryScope, session_id: str, at: datetime, limit: int) -> Sequence[StoredMessage]: ...
    async def list_tombstones(self, scope: MemoryScope, limit: int) -> Sequence[MemoryTombstone]: ...
    async def supersede_item(self, scope: MemoryScope, item_id: str, replacement_id: str, operation_id: str = "") -> None: ...
    async def expire_overdue(self, scope: MemoryScope) -> int: ...
    async def mark_consolidated(self, scope: MemoryScope, forced: bool) -> None: ...
    async def item_embeddings(self, scope: MemoryScope, item_ids: tuple[str, ...], model_id: str) -> Mapping[str, Sequence[float]]: ...
    async def items_missing_embeddings(self, scope: MemoryScope, model_id: str, limit: int) -> Sequence[MemoryItem]: ...
    async def upsert_item_embedding(self, scope: MemoryScope, item: MemoryItem, model_id: str, vector: tuple[float, ...]) -> None: ...
    async def sync_vector_column(self, scope: MemoryScope, limit: int) -> int: ...
    async def delete_item_embedding(self, scope: MemoryScope, item_id: str) -> None: ...
    async def top_topics(self, scope: MemoryScope, limit: int) -> Sequence[TopicStat]: ...
    async def topic_by_id(self, scope: MemoryScope, topic_id: str) -> TopicStat | None: ...
    async def topic_by_key(self, scope: MemoryScope, key: str) -> TopicStat | None: ...
    async def bump_topic(self, scope: MemoryScope, topic: str, key: str, alias: str, operation_id: str) -> TopicStat: ...
    async def mark_topic_promoted(self, scope: MemoryScope, key: str) -> None: ...
    async def rename_topic(self, scope: MemoryScope, old_key: str, new_key: str, label: str) -> bool: ...
    async def delete_topic(self, scope: MemoryScope, topic_id: str, tombstones: tuple[MemoryTombstone, ...]) -> None: ...
    async def bump_doc_affinity(self, scope: MemoryScope, refs: tuple[DocumentAffinity, ...], operation_id: str) -> None: ...
