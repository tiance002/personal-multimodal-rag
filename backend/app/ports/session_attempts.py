"""Provider-facing contract for durable request quota reservation."""
from typing import Protocol


class AttemptDenied(RuntimeError):
    pass


class AttemptGate(Protocol):
    def reserve(self, *, request_id: str | None = None) -> str: ...
    def finish(self, attempt_id: str, status: str) -> None: ...


def request_identity(run_id: str, purpose: str, attempt: int = 1) -> str:
    """Stable logical identity; attempt is explicit, never advanced automatically."""
    import hashlib
    import json
    import re
    if (not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 256
            or not isinstance(purpose, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", purpose)
            or type(attempt) is not int or not 1 <= attempt <= 50):
        raise ValueError("REQUEST_IDENTITY_INVALID")
    canonical = json.dumps(["rag-request-v1", run_id, purpose, attempt],
                           ensure_ascii=False, separators=(",", ":"))
    return "rag-request-v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
