"""SIMULATED cloud responses: no real HTTP, API keys, billing or model inference."""
from dataclasses import asdict, replace
from io import BytesIO
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PIL import Image

from backend.app.adapters.models.cloud import (
    CloudChat, DeepSeekVision, EmbeddingAdmission, SiliconFlowEmbedding, SiliconFlowRerank,
)
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.domain.adaptive_chunking import ChunkingConfig
from backend.app.domain.embedding_identity import EmbeddingIdentity, effective_identity
from backend.app.domain.model_registry import ModelRegistry, ModelSpec
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import EmbeddingResult, ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer


class SimulatedGuard:
    def __init__(self):
        self.reservations, self.settlements = [], []

    def reserve(self, **kwargs):
        self.reservations.append(kwargs)
        return str(len(self.reservations))

    def settle(self, reservation, usage, *, sent):
        self.settlements.append((reservation, usage, sent))


@pytest.fixture
def registry():
    return ModelRegistry.frozen_defaults()


@pytest.fixture
def guard():
    return SimulatedGuard()


@pytest.fixture(autouse=True)
def simulated_keys(monkeypatch):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "SIMULATED-NOT-A-KEY")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "SIMULATED-NOT-A-KEY")


def embedding(registry, guard, response=None, **kwargs):
    calls = []
    def send(request, timeout):
        calls.append(json.loads(request.data))
        if isinstance(response, Exception):
            raise response
        return response or dict(model="BAAI/bge-m3", data=[dict(index=0, embedding=[1.0]*1024)],
                                usage={"prompt_tokens": 3, "total_tokens": 3})
    adapter = SiliconFlowEmbedding(registry.select("embedding"), enabled=True, usage_guard=guard,
        chunking_index_identity=ChunkingConfig().identity, transport=send,
        admission=EmbeddingAdmission(lambda text: 3, tokenizer_identity="SIMULATED", non_truncating_verified=True), **kwargs)
    return adapter, calls


def test_frozen_roles_and_independent_instances(registry):
    factory = ProviderFactory(registry)
    assert [(registry.select(r).provider, registry.select(r).model_id) for r in registry.defaults] == [
        ("siliconflow", "XingChenAGI/Xing4.0-29B"), ("deepseek", "deepseek-flash"),
        ("siliconflow", "BAAI/bge-m3"), ("siliconflow", "BAAI/bge-reranker-v2-m3"), ("deepseek", "deepseek-flash")]
    assert isinstance(factory.build("rerank"), SiliconFlowRerank)
    assert isinstance(factory.build("vision"), DeepSeekVision)
    assert factory.build("chat_expensive").receipts is not factory.build("vision").receipts


@pytest.mark.parametrize("change", [dict(role="vision"), dict(provider="other"), dict(model_id="Pro/BAAI/bge-m3"),
    dict(base_url="http://127.0.0.1"), dict(base_url="https://api.siliconflow.cn/v1/"),
    dict(base_url="https://api.siliconflow.cn@evil.test/v1"), dict(api_key_env="OTHER_KEY"), dict(dimension=768)])
def test_registry_rejects_unsupported_or_untrusted(registry, change):
    with pytest.raises(ValueError):
        replace(registry.select("embedding"), **change)


def test_multiple_candidates_config_switch_and_disabled_role(registry):
    alternate = replace(registry.select("chat_expensive"), model_key="cheap_deepseek", role="chat_cheap")
    models = [*registry.models.values(), alternate]
    config = dict(models=[asdict(m) for m in models], defaults={**registry.defaults, "chat_cheap": alternate.model_key})
    configured = Settings(model_registry_json=json.dumps(config)).model_registry()
    assert ProviderFactory(configured).build("chat_cheap").spec.model_id == "deepseek-flash"
    with pytest.raises(ValueError):
        ModelRegistry(models, {**registry.defaults, "chat_cheap": "embedding_default"})
    with pytest.raises(ValueError):
        ModelRegistry([replace(m, enabled=False) if m.role == "vision" else m for m in registry.models.values()], registry.defaults)


@pytest.mark.parametrize("missing", ["scope", "key", "guard", "role"])
def test_pretransport_denial_no_calls_or_reservation(registry, guard, monkeypatch, missing):
    adapter, calls = embedding(registry, guard)
    if missing == "key":
        monkeypatch.delenv("SILICONFLOW_API_KEY")
    if missing == "guard":
        adapter.usage_guard = None
    if missing == "role":
        adapter.enabled = False
    with model_access("embedding", allowed=missing != "scope"), pytest.raises(ProviderRequestNotSent):
        adapter.embed(["synthetic"], 1)
    assert calls == [] and guard.reservations == []


def test_batch_order_full_header_input_no_ollama_parameters(registry, guard):
    adapter, calls = embedding(registry, guard, dict(model="BAAI/bge-m3", data=[
        dict(index=1, embedding=[2.0]*1024), dict(index=0, embedding=[1.0]*1024)]))
    inputs = ["ContextHeader: Alpha\n\nExact child table |  42  |", "Second child"]
    with model_access("embedding", allowed=True):
        result = adapter.embed(inputs, 10)
    assert result.vectors[0][0] == 1 and result.vectors[1][0] == 2
    assert calls == [dict(model="BAAI/bge-m3", input=inputs, encoding_format="float")]
    assert result.identity_fingerprint == adapter.identity.fingerprint
    assert result.usage_actual is None
    assert guard.settlements[-1][1] is None and adapter.receipts[-1]["settlement"] == "UNKNOWN"


@pytest.mark.parametrize("bad", ["missing", "extra", "duplicate", "negative", "bool_index", "dimension", "nan", "infinity", "huge", "bool", "text", "zero", "model"])
def test_invalid_vectors_never_succeed(registry, guard, bad):
    response = dict(model="BAAI/bge-m3", data=[dict(index=0, embedding=[1.0]*1024)])
    if bad == "missing": response["data"] = []
    elif bad in {"extra", "duplicate"}: response["data"] *= 2
    elif bad == "negative": response["data"][0]["index"] = -1
    elif bad == "bool_index": response["data"][0]["index"] = True
    elif bad == "dimension": response["data"][0]["embedding"] = [1.0]
    elif bad == "model": response["model"] = "Pro/BAAI/bge-m3"
    elif bad == "zero": response["data"][0]["embedding"] = [0.0]*1024
    else: response["data"][0]["embedding"][0] = {"nan": float("nan"), "infinity": float("inf"), "huge": 10**1000, "bool": True, "text": "1"}[bad]
    adapter, calls = embedding(registry, guard, response)
    with model_access("embedding", allowed=True), pytest.raises(ProviderUnavailable):
        adapter.embed(["synthetic"], 10)
    assert len(calls) == 1 and adapter.receipts[-1]["status"] != "ok"


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 503, 504])
def test_http_errors_no_retry_no_input_shortening(registry, guard, code):
    adapter, calls = embedding(registry, guard, HTTPError("https://invalid", code, "sensitive", {}, None))
    with model_access("embedding", allowed=True), pytest.raises(ProviderUnavailable, match=f"MODEL_HTTP_{code}"):
        adapter.embed(["full exact input"], 10)
    assert len(calls) == 1 and calls[0]["input"] == ["full exact input"]
    assert guard.settlements[-1][1] is None


def test_timeout_and_request_cap(registry, guard):
    adapter, calls = embedding(registry, guard, TimeoutError("sensitive"), max_requests=1)
    with model_access("embedding", allowed=True):
        with pytest.raises(ProviderUnavailable, match="MODEL_REQUEST_FAILED"):
            adapter.embed(["synthetic"], 1)
        with pytest.raises(ProviderRequestNotSent, match="BUDGET"):
            adapter.embed(["synthetic"], 1)
    assert len(calls) == 1


@pytest.mark.parametrize("count,allowed", [(8192, True), (8193, False), (True, False)])
def test_token_boundary_and_unknown_guarantee(registry, guard, count, allowed):
    adapter, calls = embedding(registry, guard)
    adapter.admission = EmbeddingAdmission(lambda _: count, tokenizer_identity="SIMULATED", non_truncating_verified=True)
    with model_access("embedding", allowed=True):
        if allowed:
            adapter.embed(["header and atomic body"], 1)
        else:
            with pytest.raises(ProviderRequestNotSent): adapter.embed(["header and atomic body"], 1)
    assert bool(calls) is allowed
    with pytest.raises(ProviderRequestNotSent, match="UNKNOWN"):
        EmbeddingAdmission().count(["even short input has no verified token contract"])


@pytest.mark.parametrize("role", ["chat_cheap", "chat_expensive"])
@pytest.mark.parametrize("finish", ["stop", "length", "content_filter"])
def test_chat_contract_and_truncation(registry, guard, role, finish):
    calls = []
    def send(request, timeout):
        calls.append(json.loads(request.data))
        return dict(model=registry.select(role).model_id, choices=[dict(message=dict(content="synthetic answer"), finish_reason=finish)],
                    usage=dict(prompt_tokens=5, completion_tokens=2, total_tokens=7))
    adapter = CloudChat(registry.select(role), enabled=True, usage_guard=guard, transport=send)
    with model_access(role, allowed=True):
        if finish == "stop":
            result = adapter.generate([dict(role="user", content="synthetic prompt")], timeout_seconds=1)
            assert result.text == "synthetic answer" and result.usage_actual["total_tokens"] == 7
        else:
            with pytest.raises(TruncatedAnswer if finish == "length" else ProviderUnavailable):
                adapter.answer("synthetic prompt", 1)
    assert calls[0]["model"] == registry.select(role).model_id
    assert ("thinking" in calls[0]) == (role == "chat_expensive")
    assert guard.reservations[0]["role"] == role


@pytest.mark.parametrize("bad", [None, "duplicate", "range", "score", "source"])
def test_rerank_mapping_retains_original_hits(registry, guard, bad):
    hits = [RankedHit(chunk_id="a", rank=1, raw_score=3, sources=("vector",)), RankedHit(chunk_id="b", rank=2)]
    chunks = {key: ChunkRecord(key, "kb", "doc", "v", key+" content", {}) for key in ("a", "b")}
    rows = [dict(index=1, relevance_score=.9), dict(index=0, relevance_score=.1)]
    if bad == "duplicate": rows[1]["index"] = 1
    if bad == "range": rows[0]["index"] = 2
    if bad == "score": rows[0]["relevance_score"] = float("nan")
    if bad == "source": rows[0]["document"] = {"text": "altered"}
    adapter = SiliconFlowRerank(registry.select("rerank"), enabled=True, usage_guard=guard,
                               transport=lambda *_: {"results": rows})
    with model_access("rerank", allowed=True):
        if bad:
            with pytest.raises(ProviderUnavailable): adapter.rank("synthetic", hits, chunks)
        else:
            ranked = adapter.rank("synthetic", hits, chunks)
            assert ranked == (hits[1], hits[0]) and ranked[1] is hits[0]
            assert adapter.last_result.relevance_scores == (.9, .1)
            assert chunks["a"].content == "a content"


def test_vision_inline_bytes_and_caption_identity(registry, guard):
    buffer = BytesIO()
    Image.new("RGB", (64,64), "white").save(buffer, format="PNG")
    calls = []
    def send(request, timeout):
        calls.append(json.loads(request.data))
        return {"choices": [{"message": {"content": "synthetic figure"}, "finish_reason": "stop"}]}
    adapter = DeepSeekVision(registry.select("vision"), enabled=True, usage_guard=guard, transport=send)
    with model_access("vision", allowed=True):
        adapter.caption_preflight(1)
        assert calls == []
        result = adapter.caption_image(buffer.getvalue(), 1)
    import hashlib
    assert result.input_image_sha256 == hashlib.sha256(buffer.getvalue()).hexdigest()
    assert result.prompt_version == "deepseek-figure-caption/v1" and result.model_digest is None
    assert result.model_reported == "UNKNOWN"  # Supplier response omitted it.
    assert calls[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert guard.reservations[0]["role"] == "vision"


@pytest.mark.parametrize("field,value", [("provider", "ollama"), ("model_id", "other"), ("resolved_revision_or_unknown", "new"),
    ("dimension", 768), ("distance_metric", "l2"), ("chunking_index_identity", "old"), ("embedding_input_semantics_version", "other")])
def test_every_identity_component_isolates_vectors(field, value):
    identity = EmbeddingIdentity("siliconflow", "BAAI/bge-m3", "UNKNOWN", 1024, "cosine", ChunkingConfig().identity)
    assert replace(identity, **{field:value}).fingerprint != identity.fingerprint


def test_default_wiring_independent_embedding_and_closed_egress(tmp_path):
    container = build_container(Settings(database_url="sqlite+pysqlite:///:memory:", storage_root=tmp_path), agent_model=None)
    assert isinstance(container.store.embedding_provider, SiliconFlowEmbedding)
    assert container.store.embedding_provider is not container.ollama
    assert container.knowledge_gateway.retriever.embedding_provider is container.store.embedding_provider
    assert not container.store.embedding_provider.enabled
    assert container.provider_factory.build("rerank").enabled is False
    assert container.knowledge_gateway.retriever.ranker is None
    container.engine.dispose()


def test_repository_checks_scope_and_response_identity(registry, guard):
    adapter, _ = embedding(registry, guard)
    repo = object.__new__(PostgresKnowledgeRepository)
    repo.embedding_provider, repo.chunking_config = adapter, ChunkingConfig()
    repo.get_knowledge_base = lambda kb: dict(cloud_allowed=kb == "public")
    assert repo.embedding_scope_allowed(Scope.from_ids(["public"]))
    assert not repo.embedding_scope_allowed(Scope.from_ids(["public", "private"]))
    with pytest.raises(RuntimeError, match="IDENTITY"):
        repo.validate_embedding_result(EmbeddingResult([[1]*1024], "other", 1024, 0))
    with pytest.raises(RuntimeError, match="FINGERPRINT"):
        repo.validate_embedding_result(EmbeddingResult([[1]*1024], adapter.embedding_model, 1024, 0))
    assert repo.get_embedding_profile_id("other", 1024) is None
    assert repo.vector_candidates(Scope.from_ids(["public"]), [1.0], 1, profile_id="x") == []


@pytest.mark.parametrize('identity',[None,{},'not-an-identity'])
def test_atomic_profile_rejects_incomplete_identity_before_sql(identity):
    with pytest.raises(ValueError,match='EMBEDDING_PROFILE_IDENTITY_INVALID'):
        PostgresKnowledgeRepository._get_or_create_embedding_profile(None,identity)


@pytest.mark.parametrize('change',[
    {'name':'rag'}, {'name':'rag_clean_dev_unregistered'}, {'oid':21279},
    {'system_identifier':'unregistered'}, {'port':5432}, {'host':'localhost'},
])
def test_clean_slate_rejects_other_database_identities_before_connection(change):
    from pathlib import Path
    from scripts.with_clean_slate import resolve_environment
    profile = json.loads(Path('deploy/clean-slate/profile.json').read_text())
    profile['database'].update(change)
    with pytest.raises(ValueError,match='CLEAN_SLATE_DATABASE_IDENTITY_INVALID'):
        resolve_environment(profile,{})


def test_existing_budget_gate_unknown_usage_and_separate_roles(registry):
    from backend.app.application.budget import InMemoryBudgetGate
    from backend.app.application.provider_usage import BudgetUsageGuard
    ledger = InMemoryBudgetGate(20)
    chat = BudgetUsageGuard(ledger, registry.select("chat_expensive"), 7)
    vision = BudgetUsageGuard(ledger, registry.select("vision"), 9)
    ids = []
    for spec, guard in [(registry.select("chat_expensive"), chat), (registry.select("vision"), vision)]:
        reservation = guard.reserve(model_key=spec.model_key, role=spec.role, model_id=spec.model_id, planned_tokens=None)
        guard.settle(reservation, {"total_tokens": 3}, sent=True)
        ids.append(reservation)
    assert ids[0] != ids[1]
    assert {r["capability"] for r in ledger.reservations.values()} == {"chat_expensive", "vision"}
    assert all(r["state"] == "unknown" for r in ledger.reservations.values())
    assert ledger._used(next(iter(ledger.reservations.values()))["month"]) == 16
    with pytest.raises(ValueError):
        vision.reserve(model_key="chat_expensive_default", role="chat_expensive", model_id="deepseek-flash", planned_tokens=1)


def test_usage_capture_provider_role_and_unknown_settlement(registry, guard):
    from backend.app.ports.model_usage import capture_usage
    adapter, _ = embedding(registry, guard)
    with capture_usage() as usage, model_access("embedding", allowed=True):
        adapter.embed(["synthetic"], 1)
    assert usage.calls[0].capability == "embedding" and usage.calls[0].provider == "siliconflow"
    assert usage.calls[0].input_tokens == 3 and usage.calls[0].settlement == "UNKNOWN"


def test_budget_and_batch_limits_are_local_policy_not_api_capacity(registry, guard):
    adapter, calls = embedding(registry, guard, max_planned_tokens=2)
    with model_access("embedding", allowed=True), pytest.raises(ProviderRequestNotSent, match="BUDGET"):
        adapter.embed(["synthetic"], 1)
    assert calls == []
    with pytest.raises(ProviderRequestNotSent):
        EmbeddingAdmission().count(["x"]*33)


def test_chat_rerank_vision_missing_credentials_or_scope_send_nothing(registry, guard, monkeypatch):
    for role in ("chat_cheap", "chat_expensive", "rerank", "vision"):
        spec = registry.select(role)
        factory = ProviderFactory(registry, enabled_roles=[role], usage_guards={role:guard})
        adapter = factory.build(role)
        with model_access("embedding", allowed=True), pytest.raises(ProviderRequestNotSent, match="EGRESS"):
            adapter.preflight(1)
        monkeypatch.delenv(spec.api_key_env, raising=False)
        with model_access(role, allowed=True), pytest.raises(ProviderRequestNotSent, match="CREDENTIAL"):
            adapter.preflight(1)
    assert guard.reservations == []
