"""Scoped repository contracts for the fixed-WeKnora conversation port."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence


class MemoryScopeError(ValueError):
    def __init__(self):
        super().__init__("MEMORY_SCOPE_UNAVAILABLE")


@dataclass(frozen=True)
class MemoryScope:
    tenant_id: int
    subject_id: str

    def valid(self) -> bool:
        return isinstance(self.tenant_id, int) and not isinstance(self.tenant_id, bool) and self.tenant_id > 0 and isinstance(self.subject_id, str) and bool(self.subject_id)


@dataclass(frozen=True)
class MemoryContext:
    """Construct only from the authenticated server context, never request JSON."""
    tenant_id: int
    principal_storage_id: str
    agent_memory_allowed: bool = True


def resolve_scope(context: MemoryContext) -> MemoryScope:
    if not isinstance(context, MemoryContext):
        raise MemoryScopeError()
    scope = MemoryScope(context.tenant_id, context.principal_storage_id)
    if not scope.valid():
        raise MemoryScopeError()
    return scope


@dataclass(frozen=True)
class Attachment:
    file_name: str
    file_type: str = ""
    file_size: int = 0
    content: str = field(default="", repr=False)
    is_truncated: bool = False
    line_count: int = 0
    content_mode: str = ""
    selected_chunks: int = 0
    total_chunks: int = 0


@dataclass(frozen=True)
class StoredMessage:
    id: str
    request_id: str
    session_id: str
    tenant_id: int
    subject_id: str
    role: str
    content: str = field(repr=False)
    created_at: datetime
    rendered_content: str = field(default="", repr=False)
    image_captions: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    knowledge_references: tuple[Mapping[str, Any], ...] = ()
    # Existing messages already persist these fields. Legacy in-memory callers
    # represent complete rows; the DB adapter always supplies the stored values.
    is_completed: bool = True
    turn_status: str = 'pending'
    knowledge_base_scope: tuple[str, ...] | None = None
    document_scope: tuple[str, ...] | None = None


@dataclass(frozen=True)
class HistoryRound:
    request_id: str
    query: str = field(repr=False)
    answer: str = field(repr=False)
    created_at: datetime
    knowledge_references: tuple[Mapping[str, Any], ...] = ()
    knowledge_base_scope: tuple[str, ...] | None = None
    document_scope: tuple[str, ...] | None = None


class HistoryRepository(Protocol):
    async def recent_messages(self, scope: MemoryScope, session_id: str, limit: int) -> Sequence[StoredMessage]: ...


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = False
    write_mode: str = "explicit_only"
    max_items: int = 200
    embedding_model_id: str = ""
    vector_recall: bool | None = None
    retrieval_conditioning: bool | None = None
    extract_model_id: str = ""
    extract_instructions: str = ""
    extract_delay_seconds: int = 90
    extract_min_interval_seconds: int = 300
    interest_threshold: int = 3

    def extract_delay(self) -> int:
        return min(3600, max(5, self.extract_delay_seconds if self.extract_delay_seconds > 0 else 90))

    def extract_min_interval(self) -> int:
        return min(86400, self.extract_min_interval_seconds if self.extract_min_interval_seconds > 0 else 300)

    def effective_max_items(self) -> int:
        return min(self.max_items if self.max_items > 0 else 200, 2000)

    def vector_enabled(self) -> bool:
        return self.enabled and self.vector_recall is not False

    def retrieval_enabled(self) -> bool:
        return self.enabled and self.retrieval_conditioning is not False


@dataclass(frozen=True)
class MemorySubject:
    tenant_id: int
    subject_id: str
    enabled: bool = True
    block_text: str = field(default="", repr=False)
    item_count: int = 0
    cache_revision: int = 0
    consolidated_at: datetime | None = None
    forced_consolidated_at: datetime | None = None
    last_extracted_at: datetime | None = None


@dataclass(frozen=True)
class MemoryItem:
    id: str
    tenant_id: int
    subject_id: str
    kind: str
    content: str = field(repr=False)
    topic: str = ""
    importance: int = 3
    status: str = "active"
    origin: str = "extracted"
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    source_session_id: str = ""
    source_message_id: str = ""
    normalized_key: str = ""
    inferred: bool = False
    replaces_id: str = ""
    superseded_by: str = ""
    last_used_at: datetime | None = None
    operation_id: str = ""


@dataclass(frozen=True)
class MemoryTombstone:
    """Suppression metadata only; never retains forgotten raw content."""
    topic: str
    fingerprint: str
    source_message_id: str = ""


@dataclass(frozen=True)
class MemoryVectorQuery:
    model_id: str
    vector: tuple[float, ...] = field(repr=False)
    kinds: tuple[str, ...]
    min_score: float
    limit: int


@dataclass(frozen=True)
class MemoryVectorHit:
    item: MemoryItem
    score: float


@dataclass(frozen=True)
class DocumentAffinity:
    knowledge_id: str
    title: str
    hits: int
    knowledge_base_id: str = ""


class MemoryRepository(Protocol):
    """Queries MUST filter tenant+subject, lifecycle, kind and embedding space.

    Returned rows preserve the upstream importance DESC/valid_from DESC order.
    search_items_by_vector searches the entire subject, not the lexical window.
    """
    async def workspace_config(self, tenant_id: int) -> MemoryConfig | None: ...
    async def get_subject(self, scope: MemoryScope) -> MemorySubject | None: ...
    async def list_active_resident(self, scope: MemoryScope, limit: int) -> Sequence[MemoryItem]: ...
    async def list_active_by_kinds(self, scope: MemoryScope, kinds: tuple[str, ...], limit: int) -> Sequence[MemoryItem]: ...
    async def search_items_by_vector(self, scope: MemoryScope, query: MemoryVectorQuery) -> Sequence[MemoryVectorHit]: ...
    async def touch_used(self, scope: MemoryScope, ids: tuple[str, ...]) -> None: ...
    async def top_doc_affinity(self, scope: MemoryScope, limit: int) -> Sequence[DocumentAffinity]: ...
    async def doc_affinity(self, scope: MemoryScope, knowledge_ids: tuple[str, ...]) -> Mapping[str, int]: ...


class MemoryEmbedder(Protocol):
    """Adapter enforces real egress/cost/model identity before any network call."""
    async def embed_query(self, scope: MemoryScope, model_id: str, text: str) -> Sequence[float]: ...


@dataclass(frozen=True)
class MemoryRecall:
    prompt: str = field(default="", repr=False)
    items: tuple[MemoryItem, ...] = ()
    ranking_mode: str = "no_matches"
    vector_skip_reason: str = ""


@dataclass(frozen=True)
class MemorySearchResult:
    available: bool = False
    items: tuple[MemoryItem, ...] = ()
    ranking_mode: str = "no_matches"


@dataclass(frozen=True)
class RetrievalContext:
    background: str = field(default="", repr=False)
    interests: tuple[str, ...] = ()
    documents: tuple[str, ...] = ()
    items: tuple[MemoryItem, ...] = ()


@dataclass(frozen=True)
class MemorySettings:
    workspace_enabled: bool
    user_enabled: bool
    effective: bool
    write_mode: str
    max_items: int
    item_count: int
