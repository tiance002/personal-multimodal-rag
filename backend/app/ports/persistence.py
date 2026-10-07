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

    def completed_history_context(self, conversation_id: str, kb_scope: list[str],
                                  document_scope: list[str], *, current_run_id: str,
                                  limit: int = 3) -> dict[str, Any]: ...

    def create_run(self, conversation_id: str | None, kb_scope: list[str], document_scope: list[str], q0: str) -> str: ...

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> Any: ...

    def append_message(self, conversation_id: str, role: str, content: str, run_id: str | None = None) -> Any: ...

    def persist_retrieval_hits(self, run_id: str, items: Sequence[Any]) -> None: ...

    def persist_evidence(self, run_id: str, snapshots: Sequence[EvidenceSnapshot]) -> None: ...

    def complete_run(self, run_id: str, status: str, error_code: str | None = None) -> None: ...

    def is_cancelled(self, run_id: str) -> bool: ...


def bounded_completed_history(rows: Sequence[Any], *, conversation_id: str,
                              kb_scope: list[str], document_scope: list[str],
                              current_run_id: str, cutoff: Any, limit: int = 3) -> dict[str, Any]:
    """Pure snapshot selection contract. Never extracts assistant content.

    Precise scope arrays retain existing persistence semantics. No-run scope
    switches cannot be detected without a stored epoch; this adds no schema.
    """
    limit = min(3, max(0, limit))
    eligible, blocked, omitted = [], None, 0
    candidates = [row for row in rows if str(row["conversation_id"]) == conversation_id
                  and str(row["run_id"]) != current_run_id and row["created_at"] < cutoff]
    candidates.sort(key=lambda row: (row["created_at"], str(row["run_id"])), reverse=True)
    for row in candidates[:16]:
        if row["knowledge_base_scope"] != kb_scope or row["document_scope"] != document_scope:
            blocked = "SCOPE_HISTORY_BARRIER"
            break
        # A later terminal state cannot erase the pending barrier at cutoff.
        if (row["status"] in {"created", "running"}
                or (row.get("completed_at") is not None and row["completed_at"] > cutoff)):
            blocked = "PENDING_HISTORY"
            break
        if (row["status"] != "completed" or row.get("error_code") is not None
                or row.get("completed_at") is None or row["completed_at"] > cutoff
                or row.get("answer_completed") is not True):
            continue
        q0 = row["q0"]
        if not isinstance(q0, str) or not q0.strip() or len(q0) > 512:
            omitted += 1
            continue
        eligible.append({"run_id": str(row["run_id"]), "q0": q0})
        if len(eligible) >= limit:
            break
    turns = tuple({"turn_id": f"H{i}", **item} for i, item in enumerate(reversed(eligible), 1))
    return {"turns": turns, "blocked_reason": blocked, "omitted_count": omitted,
            "knowledge_base_scope": list(kb_scope), "document_scope": list(document_scope),
            "snapshot_at": str(cutoff)}


__all__ = ["RunEventStore", "bounded_completed_history"]
