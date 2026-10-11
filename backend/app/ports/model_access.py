"""Scoped egress permission supplied by trusted repository/application boundaries."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

_SCOPE = ContextVar("model_egress_scope", default=frozenset())
_REQUEST_SCOPE = ContextVar('model_request_scope',default=None)


@contextmanager
def model_access(role: str, *, allowed: bool, scope=None):
    token = _SCOPE.set(frozenset({role}) if allowed is True else frozenset())
    scope_token = _REQUEST_SCOPE.set((role,scope) if allowed is True else None)
    try:
        yield
    finally:
        _SCOPE.reset(token)
        _REQUEST_SCOPE.reset(scope_token)


def scope_allows(role):
    return role in _SCOPE.get()


def request_scope(role):
    context = _REQUEST_SCOPE.get()
    return context[1] if context is not None and context[0] == role else None


class ModelUsageGuard(Protocol):
    """Role-specific durable budget reservation; sent unknown usage remains reserved.

    Production integration must enforce global/KB permission and conservative
    cost bounds. Merely supplying a key or a registry entry grants no permission.
    """
    def reserve(self, *, model_key: str, role: str, model_id: str, planned_tokens: int | None) -> str: ...
    def settle(self, reservation: str, usage: dict | None, *, sent: bool) -> None: ...
