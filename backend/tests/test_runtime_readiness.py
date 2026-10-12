"""Offline config and SIMULATED transport failures; no provider sends."""
import socket
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.api.routes import MessageIn
from pydantic import ValidationError
from backend.app.adapters.models.runtime_readiness import configured_embedding_admission
from backend.app.domain.model_registry import ModelRegistry
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.application.run_metrics import collect_metrics
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.app.ports.providers import EmbeddingResult, ProviderRequestNotSent, public_provider_reason


def test_embedding_selection_excludes_pro_and_has_no_silent_fallback():
    registry = ModelRegistry.frozen_defaults()
    ordinary = registry.select("embedding")
    assert ordinary.model_id == "BAAI/bge-m3"
    with pytest.raises(ValueError, match="MODEL_CAPABILITY_UNSUPPORTED"):
        replace(ordinary, model_id="Pro/BAAI/bge-m3")
    assert registry.select("embedding") == ordinary


def test_api_rejects_input_above_query_limit_before_execution():
    assert len(MessageIn(content="甲" * 4096).content) == 4096
    with pytest.raises(ValidationError) as failure:
        MessageIn(content="甲" * 4097)
    assert failure.value.errors()[0]["type"] == "string_too_long"


def test_ordinary_entry_is_explicitly_not_ready_and_does_not_probe(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network is forbidden in configuration checks")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", storage_root=tmp_path,
                        langfuse_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        readiness = client.get("/readyz")
        assert readiness.status_code == 503
        data = readiness.json()["data"]
        assert data["status"] == "NOT_READY" and data["runtime_verified"] is False
        models = client.get("/api/v1/settings/models").json()["data"]
        assert models["embedding"] == "BAAI/bge-m3" and models["provider"] == "siliconflow"
        assert "MODEL_CONTEXT_CAPACITY_UNKNOWN" in models["roles"]["chat"]["reasons"]
        assert "EMBEDDING_CAPACITY_GUARANTEE_UNKNOWN" in models["roles"]["embedding"]["reasons"]
    app.state.container.engine.dispose()


def test_tokenizer_path_is_configurable_but_not_permission(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_TOKENIZER_PATH", str(tmp_path / "missing.json"))
    settings = Settings.from_env()
    assert settings.embedding_tokenizer_path == tmp_path / "missing.json"
    admission, reason = configured_embedding_admission(settings.model_registry().select("embedding"), settings.embedding_tokenizer_path)
    assert admission is None and reason == "EMBEDDING_TOKENIZER_MISSING"


def test_arbitrary_tokenizer_file_cannot_claim_readiness(tmp_path):
    path = tmp_path / "tokenizer.json"
    path.write_text('{"sensitive":"do not expose"}')
    admission, reason = configured_embedding_admission(ModelRegistry.frozen_defaults().select("embedding"), path)
    assert admission is None and reason == "EMBEDDING_TOKENIZER_UNVERIFIED"
    assert "sensitive" not in reason and str(path) not in reason


class Provider:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0
    def embed(self, texts, timeout_seconds):
        self.calls += 1
        if self.error:
            raise self.error
        return EmbeddingResult([[1.0, 0.0]], "SIMULATED", 2, 0)


@pytest.mark.parametrize("error,status,reason", [
    (ProviderRequestNotSent("MODEL_ROLE_EGRESS_DENIED"), "NOT_SENT", "MODEL_ROLE_EGRESS_DENIED"),
    (RuntimeError("secret/raw SQL/private text"), "FAILED", "VECTOR_RETRIEVAL_FAILED"),
])
def test_embedding_failure_preserves_q0_fallback_and_safe_diagnostic(error, status, reason):
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord("local", "kb", "doc", "version", "needle"))
    repo.add(ChunkRecord("other", "other-kb", "other-doc", "version", "needle"))
    provider = Provider(error)
    with collect_metrics("SIMULATED-run") as metrics:
        result = HybridRetriever(repo, provider, mode="vector").retrieve(Scope.from_ids(["kb"]), "needle")
        metrics.record_retrieval(result)
        saved = metrics.snapshot(citations=[], error=None)
    assert provider.calls == 1
    assert [item.chunk.chunk_id for item in result.items] == ["local"]
    channel = saved["retrieval_diagnostics"][0]["channels"]["vector"]
    assert channel["status"] == status and channel["reason"] == reason
    assert channel["count"] is None
    assert "secret" not in repr(saved) and "private text" not in repr(saved)


def test_missing_profile_is_failure_after_embedding_not_empty_or_not_sent():
    class MissingProfile(InMemoryRetrievalRepository):
        def get_embedding_profile_id(self, model, dimensions):
            return None
    result = HybridRetriever(MissingProfile(), Provider(), mode="hybrid").retrieve(Scope.from_ids(["kb"]), "needle")
    assert result.diagnostic["channels"]["vector"]["status"] == "FAILED"
    assert result.diagnostic["channels"]["vector"]["reason"] == "EMBEDDING_PROFILE_UNAVAILABLE"
    assert result.diagnostic["channels"]["keyword"]["status"] == "EMPTY"


def test_provider_absent_does_not_report_zero_observed_vector_candidates():
    result = HybridRetriever(InMemoryRetrievalRepository(), None, mode="vector").retrieve(Scope.from_ids(["kb"]), "needle")
    assert result.diagnostic["channels"]["vector"]["status"] == "NOT_SENT"
    assert result.diagnostic["channels"]["vector"]["count"] is None
    assert result.diagnostic["channels"]["keyword"]["status"] == "EMPTY"


def test_unknown_provider_text_never_becomes_a_public_code():
    assert public_provider_reason(ProviderRequestNotSent("secret=abc")) == "MODEL_PROVIDER_FAILED"
    assert public_provider_reason(RuntimeError("MODEL_ROLE_EGRESS_DENIED")) == "MODEL_PROVIDER_FAILED"
