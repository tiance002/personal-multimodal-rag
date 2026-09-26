from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    latency_ms: float
    raw: dict[str, Any]


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimensions: int
    latency_ms: float


class ChatProvider(Protocol):
    def complete_json(self, messages: Sequence[dict[str, str]], schema: dict[str, Any], timeout_seconds: float) -> ChatResult: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str], timeout_seconds: float) -> EmbeddingResult: ...


__all__ = ["ChatProvider", "ChatResult", "EmbeddingProvider", "EmbeddingResult", "ProviderUnavailable"]
