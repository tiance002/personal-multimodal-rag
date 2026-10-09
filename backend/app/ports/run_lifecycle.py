from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

execution_owner = ContextVar('rag_execution_owner', default=None)
current_execution = ContextVar('rag_current_execution', default=None)
current_attempt = ContextVar('rag_current_attempt', default=None)


class LifecycleDenied(RuntimeError):
    pass


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
