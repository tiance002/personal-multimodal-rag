"""Provider-send persistence port; the backend does not own evaluator storage."""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProviderSendClaims(Protocol):
    def __call__(self, *, request_id: str, purpose: str, provider: str,
                 model: str, role: str, request_identity_sha256: str,
                 call_ordinal: int = 1) -> Any: ...

    def settle(self, *, request_id: str, status: str,
               result: dict | None = None, error_code: str | None = None) -> None: ...

    def check_before_send(self, *, request_id: str) -> None: ...


def claim_matches(claim, *, request_id, purpose, provider, model, role, identity_sha256,call_ordinal=1):
    expected = dict(request_id=request_id, purpose=purpose, provider=provider,
        model=model, role=role, request_identity_sha256=identity_sha256, call_ordinal=call_ordinal)
    return (claim is not None and all(type(getattr(claim, name, None)) is type(value)
        and getattr(claim, name) == value for name, value in expected.items())
        and all(type(getattr(claim, name, None)) is str and getattr(claim, name)
                for name in ('run_id', 'qid', 'arm', 'claimed_at_utc')))
