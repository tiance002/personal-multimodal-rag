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
                                  limit: int = 3, purpose: str = 'follow_up') -> dict[str, Any]: ...

    def create_run(self, conversation_id: str | None, kb_scope: list[str], document_scope: list[str], q0: str) -> str: ...

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> Any: ...

    def append_message(self, conversation_id: str, role: str, content: str, run_id: str | None = None) -> Any: ...

    def persist_retrieval_hits(self, run_id: str, items: Sequence[Any]) -> None: ...

    def persist_evidence(self, run_id: str, snapshots: Sequence[EvidenceSnapshot]) -> None: ...

    def complete_run(self, run_id: str, status: str, error_code: str | None = None) -> None: ...

    def is_cancelled(self, run_id: str) -> bool: ...


def bounded_completed_history(rows: Sequence[Any], *, conversation_id: str,
                              kb_scope: list[str], document_scope: list[str],
                              current_run_id: str, cutoff: Any, limit: int = 3,
                              purpose: str = 'follow_up') -> dict[str, Any]:
    """Pure snapshot selection contract. Never extracts assistant content.

    Precise scope arrays retain existing persistence semantics. No-run scope
    switches cannot be detected without a stored epoch; this adds no schema.
    """
    if purpose not in {'follow_up', 'context'}:
        raise ValueError('HISTORY_PURPOSE_INVALID')
    limit = min(512 if purpose == 'context' else 3, max(0, limit))
    eligible, blocked, omitted = [], None, 0
    candidates = [row for row in rows if str(row["conversation_id"]) == conversation_id
                  and str(row["run_id"]) != current_run_id and row["created_at"] < cutoff]
    candidates.sort(key=lambda row: (row["created_at"], str(row["run_id"])), reverse=True)
    for row in candidates[:2048 if purpose == 'context' else 16]:
        kb_match = (set(row['knowledge_base_scope']) == set(kb_scope) if purpose == 'context' else row['knowledge_base_scope'] == kb_scope)
        docs_match = (set(row['document_scope']) == set(document_scope) if purpose == 'context' else row['document_scope'] == document_scope)
        if not kb_match or not docs_match:
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
        if not isinstance(q0, str) or not q0.strip() or (purpose == 'follow_up' and len(q0) > 512):
            omitted += 1
            continue
        if purpose == 'context':
            if (row.get('user_content') != q0 or not isinstance(row.get('answer'), str)
                    or not row['answer'].strip() or row.get('message_count') != 2
                    or not isinstance(row.get('citations'), list) or not row.get('citation_valid')):
                omitted += 1
                continue
            eligible.append({'run_id': str(row['run_id']), 'q0': q0, 'answer': row['answer'],
                'citations': row['citations'], 'evidence': row.get('evidence', []),
                'protocol': row.get('protocol', []), 'created_at': str(row['created_at']),
                'completed_at': str(row['completed_at'])})
        else:
            eligible.append({"run_id": str(row["run_id"]), "q0": q0})
        if len(eligible) >= limit:
            break
    turns = tuple(item if purpose == 'context' else {"turn_id": f"H{i}", **item}
                  for i, item in enumerate(reversed(eligible), 1))
    return {"turns": turns, "blocked_reason": blocked, "omitted_count": omitted,
            "knowledge_base_scope": list(kb_scope), "document_scope": list(document_scope),
            "snapshot_at": str(cutoff)}


__all__ = ["RunEventStore", "bounded_completed_history"]
