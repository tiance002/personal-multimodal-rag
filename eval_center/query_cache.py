"""Short-lived query embedding cache for one evaluation process."""
from __future__ import annotations

import hashlib
from typing import Any

from backend.app.domain.text_normalization import normalize_query
from backend.app.ports.providers import EmbeddingResult


class RunQueryEmbeddingCache:
    """Cache successful query vectors without persisting text or vectors."""

    def __init__(self, provider: Any, *, model_digest: str, profile_id: str,
                 dimensions: int, scope_identity: str) -> None:
        self.provider = provider
        self.model_digest = model_digest
        self.model_name = provider.embedding_model
        self.profile_id = profile_id
        self.dimensions = dimensions
        self.scope_identity = scope_identity
        self._values: dict[str, EmbeddingResult] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._actual_calls = 0
        self._failed_calls = 0

    def _key(self, text: str) -> str:
        normalized = normalize_query(text).normalized
        payload = "\0".join((hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                             "text-normalization-v1", self.model_name, self.model_digest,
                             self.profile_id, str(self.dimensions), self.scope_identity)).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def embed(self, texts, timeout_seconds: float):
        values = list(texts)
        if len(values) != 1:
            # The production retriever sends one query at a time; avoid silently
            # inventing a multi-query cache contract.
            return self.provider.embed(values, timeout_seconds)
        if self.provider.embedding_model != self.model_name:
            raise ValueError("embedding model changed during cached run")
        key = self._key(values[0])
        cached = self._values.get(key)
        if cached is not None:
            self._cache_hits += 1
            return EmbeddingResult([list(cached.vectors[0])], cached.model,
                                   cached.dimensions, 0.0, cached.profile_id)
        self._cache_misses += 1
        self._actual_calls += 1
        try:
            result = self.provider.embed(values, timeout_seconds)
        except Exception:
            self._failed_calls += 1
            raise
        if result.model != self.model_name or result.dimensions != self.dimensions or len(result.vectors) != 1:
            self._failed_calls += 1
            raise ValueError("embedding result identity mismatch")
        if result.profile_id is not None and result.profile_id != self.profile_id:
            self._failed_calls += 1
            raise ValueError("embedding profile mismatch")
        verified = EmbeddingResult([list(result.vectors[0])], result.model,
                                   result.dimensions, result.latency_ms, self.profile_id)
        self._values[key] = verified
        return verified

    @property
    def embedding_model(self):
        return self.provider.embedding_model

    def stats(self) -> dict[str, int]:
        return {
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "actual_embedding_calls": self._actual_calls,
            "failed_embedding_calls": self._failed_calls,
        }
