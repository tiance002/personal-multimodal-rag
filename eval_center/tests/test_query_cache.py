import pytest

from backend.app.ports.providers import EmbeddingResult
from eval_center.query_cache import RunQueryEmbeddingCache


class Provider:
    embedding_model = "bge-m3:latest"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def embed(self, texts, timeout_seconds):
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return EmbeddingResult([[float(self.calls)] * 3], self.embedding_model, 3, 1.0)


def cache(provider, *, digest="a" * 64, profile="profile-1", scope="kb-1"):
    return RunQueryEmbeddingCache(provider, model_digest=digest, profile_id=profile,
                                  dimensions=3, scope_identity=scope)


def test_hit_for_same_normalized_query_and_no_second_provider_call():
    provider = Provider()
    cached = cache(provider)
    first = cached.embed(["  ABC  "], timeout_seconds=10)
    second = cached.embed(["abc"], timeout_seconds=10)
    assert first.vectors == second.vectors
    assert provider.calls == 1
    assert cached.stats() == {"cache_hits": 1, "cache_misses": 1,
                              "actual_embedding_calls": 1, "failed_embedding_calls": 0}


def test_changed_digest_profile_or_scope_does_not_share_cached_vector():
    provider = Provider()
    for options in ({}, {"digest": "b" * 64}, {"profile": "profile-2"}, {"scope": "kb-2"}):
        cache(provider, **options).embed(["abc"], timeout_seconds=10)
    assert provider.calls == 4


def test_changed_query_and_failure_are_cache_misses():
    provider = Provider()
    cached = cache(provider)
    provider.fail = True
    with pytest.raises(RuntimeError):
        cached.embed(["first"], timeout_seconds=10)
    provider.fail = False
    cached.embed(["first"], timeout_seconds=10)
    cached.embed(["second"], timeout_seconds=10)
    assert provider.calls == 3
    assert cached.stats()["failed_embedding_calls"] == 1
    assert cached.stats()["cache_hits"] == 0
    assert cached.stats()["actual_embedding_calls"] == 3
