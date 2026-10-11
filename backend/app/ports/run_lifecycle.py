from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol
import re

execution_owner = ContextVar('rag_execution_owner', default=None)
current_execution = ContextVar('rag_current_execution', default=None)
current_attempt = ContextVar('rag_current_attempt', default=None)


class LifecycleDenied(RuntimeError):
    pass


def checked_retrieval_completion(completed_qids, native_receipts_sha256):
    # Preserve opaque QIDs verbatim; MIRACL additionally uses decimal document#passage.
    if (type(completed_qids) is not tuple or not completed_qids
            or any(type(qid) is not str or not 1 <= len(qid) <= 128
                   or not (re.fullmatch(r'[A-Za-z0-9_.:-]+', qid)
                           or re.fullmatch(r'[0-9]+#[0-9]+', qid))
                   for qid in completed_qids)
            or len(set(completed_qids)) != len(completed_qids)
            or type(native_receipts_sha256) is not str
            or not re.fullmatch(r'[0-9a-f]{64}', native_receipts_sha256)):
        raise LifecycleDenied('RETRIEVAL_COMPLETION_INPUT_INVALID')
    return dict(terminal_kind='retrieval_only', completed_qids=list(completed_qids),
                native_receipts_sha256=native_receipts_sha256, count=len(completed_qids), error_code=None)


@dataclass(frozen=True)
class RunClaim:
    run_id: str
    owner: str
    created: bool
    state: str


class RunLifecycleRepository(Protocol):
    lease_seconds: int
    def claim_run(self, session, kbs, docs, content, mode, request_id) -> RunClaim: ...
    def renew(self, run_id, owner): ...
    def claim_attempt(self, **identity): ...
    def finish_attempt(self, run_id, owner, attempt_id, state, diagnostics): ...
    def finish_run(self, run_id, owner, *, not_sent=False): ...
    def fail_run(self, run_id, owner): ...
    def cleanup_done(self, run_id, owner): ...
    def read_run(self, run_id, session, kbs, docs): ...
    def finalize_retrieval(self, *, run_id, owner, session, kbs, docs, content, mode,
                           completed_qids, native_receipts_sha256) -> bool: ...
