from __future__ import annotations

import copy

import pytest

from backend.app.ports.providers import EmbeddingResult
from eval_center.query_embedding_cache import (
    EmbeddingCacheIdentity,
    RunScopedQueryEmbeddingCache,
    query_embedding_cache_key,
)


class CountingProvider:
    embedding_model = "bge-m3:latest"

    def __init__(self, dimension: int = 2) -> None:
        self.dimension = dimension
        self.calls: list[list[str]] = []

    def embed(self, texts, timeout_seconds):
        self.calls.append(list(texts))
        return EmbeddingResult(
            [[float(len(text)), 1.0] + [0.0] * (self.dimension - 2) for text in texts],
            self.embedding_model,
            self.dimension,
            12.5,
        )


def identity(*, digest="a" * 64, profile="profile-a", dimension=2):
    return EmbeddingCacheIdentity(digest, profile, dimension)


def test_repeated_query_across_variants_calls_provider_once_and_returns_profile():
    provider = CountingProvider()
    cache = RunScopedQueryEmbeddingCache(provider, identity())

    first = cache.embed(["  Alpha  "], timeout_seconds=4)
    second = cache.embed(["  Alpha  "], timeout_seconds=4)

    assert len(provider.calls) == 1
    assert first.vectors == second.vectors
    assert first.profile_id == second.profile_id == "profile-a"
    assert cache.snapshot() == {
        "scope": "run",
        "storage": "memory",
        "raw_query_retained": False,
        "requests": 2,
        "provider_calls": 1,
        "provider_input_items": 1,
        "cache_hits": 1,
        "unique_query_keys": 1,
    }


def test_batch_duplicates_are_embedded_once_and_restored_in_original_order():
    provider = CountingProvider()
    cache = RunScopedQueryEmbeddingCache(provider, identity())

    result = cache.embed(["same", "other", "same"], timeout_seconds=4)

    assert provider.calls == [["same", "other"]]
    assert result.vectors[0] == result.vectors[2]
    assert cache.snapshot()["provider_input_items"] == 2
    assert cache.snapshot()["cache_hits"] == 1


def test_exact_query_hash_prevents_aliasing_normalized_equivalents():
    provider = CountingProvider()
    cache = RunScopedQueryEmbeddingCache(provider, identity())

    cache.embed(["Alpha"], timeout_seconds=4)
    cache.embed([" alpha "], timeout_seconds=4)

    assert len(provider.calls) == 2


@pytest.mark.parametrize(
    "other_identity",
    [
        identity(digest="b" * 64),
        identity(profile="profile-b"),
        identity(dimension=3),
    ],
)
def test_cache_key_changes_with_model_profile_or_dimension(other_identity):
    query = "same query"

    assert query_embedding_cache_key(query, identity()) != query_embedding_cache_key(query, other_identity)


def test_invalid_provider_shape_is_not_cached():
    class BadProvider(CountingProvider):
        def embed(self, texts, timeout_seconds):
            self.calls.append(list(texts))
            return EmbeddingResult([[1.0]], self.embedding_model, 1, 1.0)

    provider = BadProvider()
    cache = RunScopedQueryEmbeddingCache(provider, identity())

    with pytest.raises(ValueError, match="embedding_cache_dimension_mismatch"):
        cache.embed(["query"], timeout_seconds=4)
    with pytest.raises(ValueError, match="embedding_cache_dimension_mismatch"):
        cache.embed(["query"], timeout_seconds=4)

    assert len(provider.calls) == 2


def test_cache_keys_and_summary_contain_no_raw_query():
    key = query_embedding_cache_key("private query text", identity())
    provider = CountingProvider()
    cache = RunScopedQueryEmbeddingCache(provider, identity())
    cache.embed(["private query text"], timeout_seconds=4)

    assert "private query text" not in repr(key)
    assert "private query text" not in repr(cache.snapshot())
