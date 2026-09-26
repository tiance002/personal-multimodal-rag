from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class ProviderUnavailable(RuntimeError):
    """A provider could not be reached, timed out, or returned an invalid shape."""


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dimensions: int
    latency_ms: float
    profile_id: str | None = None


@dataclass(frozen=True)
class QueryGatewayResult:
    """Output of the optional L1 local query-understanding pass."""

    expansions: tuple[str, ...] = ()


class LocalQueryProvider(Protocol):
    """Optional L1 query understanding. Failure must never block baseline RAG."""

    def query_expand(self, question: str, timeout_seconds: float) -> QueryGatewayResult: ...


class AnswerProvider(Protocol):
    """Answer generation. Implemented by the local Ollama adapter in V1."""

    def answer(self, prompt: str, timeout_seconds: float) -> str: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str], timeout_seconds: float) -> EmbeddingResult: ...


__all__ = [
    "AnswerProvider",
    "EmbeddingProvider",
    "EmbeddingResult",
    "LocalQueryProvider",
    "ProviderUnavailable",
    "QueryGatewayResult",
]
