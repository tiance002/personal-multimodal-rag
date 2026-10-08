"""Scoped egress permission supplied by trusted repository/application boundaries."""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

_SCOPE = ContextVar("model_egress_scope", default=frozenset())


@contextmanager
def model_access(role: str, *, allowed: bool):
    token = _SCOPE.set(frozenset({role}) if allowed is True else frozenset())
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scope_allows(role):
    return role in _SCOPE.get()


class ModelUsageGuard(Protocol):
    """Role-specific durable budget reservation; sent unknown usage remains reserved.

    Production integration must enforce global/KB permission and conservative
    cost bounds. Merely supplying a key or a registry entry grants no permission.
    """
    def reserve(self, *, model_key: str, role: str, model_id: str, planned_tokens: int | None) -> str: ...
    def settle(self, reservation: str, usage: dict | None, *, sent: bool) -> None: ...
