"""Run-scoped, in-memory reuse of identical query embeddings."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from threading import RLock
from typing import Any

from backend.app.domain.text_normalization import normalize_query
from backend.app.ports.providers import EmbeddingResult


@dataclass(frozen=True)
class EmbeddingCacheIdentity:
    model_digest: str
    profile: str
    dimension: int

    def __post_init__(self) -> None:
        if (not isinstance(self.model_digest, str) or len(self.model_digest) != 64
                or any(char not in "0123456789abcdef" for char in self.model_digest)):
            raise ValueError("embedding_cache_model_digest_invalid")
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise ValueError("embedding_cache_profile_invalid")
        if type(self.dimension) is not int or self.dimension <= 0:
            raise ValueError("embedding_cache_dimension_invalid")


@dataclass(frozen=True)
class QueryEmbeddingCacheKey:
    normalized_query_sha256: str
    exact_query_sha256: str
    model_digest: str
    profile: str
    dimension: int


def query_embedding_cache_key(query: str, identity: EmbeddingCacheIdentity) -> QueryEmbeddingCacheKey:
    if not isinstance(query, str):
        raise TypeError("embedding_cache_query_must_be_text")
    normalized = normalize_query(query).normalized
    return QueryEmbeddingCacheKey(
        normalized_query_sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        exact_query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        model_digest=identity.model_digest,
        profile=identity.profile,
        dimension=identity.dimension,
    )


class RunScopedQueryEmbeddingCache:
    """Memoize query vectors for one evaluation run without retaining query text."""

    def __init__(self, provider: Any, identity: EmbeddingCacheIdentity) -> None:
        self.provider = provider
        self.identity = identity
        self._vectors: dict[QueryEmbeddingCacheKey, tuple[float, ...]] = {}
        self._lock = RLock()
        self._requests = 0
        self._provider_calls = 0
        self._provider_input_items = 0
        self._cache_hits = 0

    @property
    def embedding_model(self) -> str:
        return self.provider.embedding_model

    def embed(self, texts, timeout_seconds: float) -> EmbeddingResult:
        queries = list(texts)
        if any(not isinstance(query, str) for query in queries):
            raise TypeError("embedding_cache_query_must_be_text")
        if not queries:
            return EmbeddingResult([], self.embedding_model, self.identity.dimension, 0.0,
                                   profile_id=self.identity.profile)

        with self._lock:
            self._requests += len(queries)
            keys = [query_embedding_cache_key(query, self.identity) for query in queries]
            output: list[tuple[float, ...] | None] = [None] * len(queries)
            pending: dict[QueryEmbeddingCacheKey, tuple[str, list[int]]] = {}
            for index, (query, key) in enumerate(zip(queries, keys, strict=True)):
                cached = self._vectors.get(key)
                if cached is not None:
                    output[index] = cached
                    continue
                if key in pending:
                    pending[key][1].append(index)
                else:
                    pending[key] = (query, [index])

            if pending:
                miss_keys = list(pending)
                miss_queries = [pending[key][0] for key in miss_keys]
                self._provider_calls += 1
                self._provider_input_items += len(miss_queries)
                result = self.provider.embed(miss_queries, timeout_seconds=timeout_seconds)
                if result.dimensions != self.identity.dimension:
                    raise ValueError("embedding_cache_dimension_mismatch")
                if len(result.vectors) != len(miss_queries):
                    raise ValueError("embedding_cache_batch_size_mismatch")
                if result.profile_id is not None and result.profile_id != self.identity.profile:
                    raise ValueError("embedding_cache_profile_mismatch")

                validated: list[tuple[float, ...]] = []
                for vector in result.vectors:
                    if len(vector) != self.identity.dimension:
                        raise ValueError("embedding_cache_vector_dimension_mismatch")
                    values = tuple(float(value) for value in vector)
                    if any(not math.isfinite(value) for value in values):
                        raise ValueError("embedding_cache_vector_invalid")
                    validated.append(values)

                for key, vector in zip(miss_keys, validated, strict=True):
                    self._vectors[key] = vector
                    for index in pending[key][1]:
                        output[index] = vector
                latency_ms = result.latency_ms
                model = result.model
            else:
                latency_ms = 0.0
                model = self.embedding_model

            self._cache_hits += len(queries) - len(pending)
            if any(vector is None for vector in output):
                raise RuntimeError("embedding_cache_result_incomplete")
            return EmbeddingResult(
                vectors=[list(vector) for vector in output if vector is not None],
                model=model,
                dimensions=self.identity.dimension,
                latency_ms=latency_ms,
                profile_id=self.identity.profile,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "scope": "run",
                "storage": "memory",
                "raw_query_retained": False,
                "requests": self._requests,
                "provider_calls": self._provider_calls,
                "provider_input_items": self._provider_input_items,
                "cache_hits": self._cache_hits,
                "unique_query_keys": len(self._vectors),
            }


__all__ = [
    "EmbeddingCacheIdentity",
    "QueryEmbeddingCacheKey",
    "RunScopedQueryEmbeddingCache",
    "query_embedding_cache_key",
]
