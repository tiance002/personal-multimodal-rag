from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from backend.app.domain.models import EvidenceSnapshot


class RunEventStore(Protocol):
    """Run, event, message and evidence persistence used by the answer pipeline.

    `persist_retrieval_hits` receives the ranked items of one retrieval pass; the
    implementation is expected to accept any object exposing `.chunk.chunk_id`,
    `.hit.rank`, `.hit.raw_score`, `.hit.fused_score` and `.hit.sources`.
    """

    def get_knowledge_base(self, kb_id: str) -> Any | None: ...

    def create_run(self, conversation_id: str | None, kb_scope: list[str], document_scope: list[str], q0: str) -> str: ...

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> Any: ...

    def append_message(self, conversation_id: str, role: str, content: str) -> Any: ...

    def persist_retrieval_hits(self, run_id: str, items: Sequence[Any]) -> None: ...

    def persist_evidence(self, run_id: str, snapshots: Sequence[EvidenceSnapshot]) -> None: ...

    def complete_run(self, run_id: str, status: str, error_code: str | None = None) -> None: ...


__all__ = ["RunEventStore"]
