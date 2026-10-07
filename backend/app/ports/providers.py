from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class ProviderUnavailable(RuntimeError):
    """A provider could not be reached, timed out, or returned an invalid shape."""


class ProviderRequestNotSent(ProviderUnavailable):
    """Explicit pretransport rejection; this request incurred no provider cost."""


class TruncatedAnswer(ProviderUnavailable):
    """Candidate stays on the local call stack; exception text contains no data."""
    def __init__(self, candidate: str) -> None:
        super().__init__("MODEL_OUTPUT_TRUNCATED")
        self.candidate = candidate


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


@dataclass(frozen=True)
class CaptionResult:
    """Observed local generation; never original document evidence."""

    text: str
    model_requested: str
    model_reported: str
    model_digest: str | None
    input_image_sha256: str
    finish_reason: str
    usage_actual: dict[str, int | None]
    latency_ms: float
    prompt_version: str = "local-figure-caption/v1"


class LocalCaptionProvider(Protocol):
    provider_kind: str

    def caption_preflight(self, timeout_seconds: float) -> None: ...

    def caption_image(self, image_bytes: bytes, timeout_seconds: float) -> CaptionResult: ...


class CaptionProvider(Protocol):
    """Model-neutral VLM port. Preflight must not perform paid generation."""
    provider_kind: str

    def caption_preflight(self, timeout_seconds: float) -> None: ...

    def caption_image(self, image_bytes: bytes, timeout_seconds: float) -> CaptionResult: ...


class CaptionUsageGuard(Protocol):
    """Trusted cloud boundary: global/KB egress, reserve budget, settle usage.

    Unknown usage must retain a conservative reservation, never count as free.
    This port does not authorize unregistered providers or supply credentials.
    """
    def allowed(self, document_id: str, version_id: str) -> bool: ...

    def reserve(self, document_id: str, version_id: str) -> str: ...

    def settle(self, reservation: str, usage_actual: dict | None) -> None: ...


class LocalQueryProvider(Protocol):
    """Optional L1 query understanding. Failure must never block baseline RAG."""

    def query_expand(self, question: str, timeout_seconds: float) -> QueryGatewayResult: ...


class AnswerProvider(Protocol):
    """Answer generation. Implemented by the local Ollama adapter in V1."""

    def answer(self, prompt: str, timeout_seconds: float) -> str: ...


class LocalFollowUpProvider(Protocol):
    """One local-only structured attempt; no retry, cloud routing or evidence."""
    provider_kind: str

    def resolve_history_json(self, q0: str, history: tuple[dict[str, str], ...], *,
                             timeout_seconds: float, max_output_tokens: int) -> str: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str], timeout_seconds: float) -> EmbeddingResult: ...


__all__ = [
    "AnswerProvider",
    "CaptionResult",
    "LocalCaptionProvider",
    "EmbeddingProvider",
    "EmbeddingResult",
    "LocalQueryProvider",
    "LocalFollowUpProvider",
    "ProviderUnavailable",
    "ProviderRequestNotSent",
    "QueryGatewayResult",
]
