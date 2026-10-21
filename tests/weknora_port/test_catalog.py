"""Fixed-source full directory/type/overlay tests; no network or credentials."""
import json
from pathlib import Path
from dataclasses import replace
import pytest
from backend.weknora_port.models import ProviderError, provider_config
from backend.weknora_port.models.catalog import (models, providers, find_model, detect_by_url,
    resolve_model_config, protocol_settings, provider_definition)

ROOT = Path(__file__).parents[2] / "backend/weknora_port/models/catalog_resources"
RAW = json.loads((ROOT / "models.generated.json").read_text(encoding="utf-8"))
ENTRIES = [(p, m) for p, ms in RAW["providers"].items() for m in ms]


def test_complete_fixed_source_registry_is_owned_and_ordered():
    values = providers()
    assert len(values) == 27 and len(ENTRIES) == 343
    assert {p["ID"] for p in values} == set(RAW["providers"])
    assert values == sorted(values,key=lambda p:(p.get("Order",0),p["ID"]))
    values[0]["Name"] = "mutated"
    assert providers()[0]["Name"] != "mutated"


@pytest.mark.parametrize("provider,entry", ENTRIES, ids=[p+":"+(m.get("id") or m.get("match")) for p,m in ENTRIES])
def test_every_source_catalog_id_alias_and_typed_pattern(provider, entry):
    kind=entry.get("type", "KnowledgeQA")
    if entry.get("id"):
        actual=find_model(provider, "  "+entry["id"].upper()+"  ",kind)
        assert actual == entry
        for alias in entry.get("aliases", ()):
            assert find_model(provider,alias,kind) == entry
    else:
        name=entry["match"].replace("*", "snapshot")
        actual=find_model(provider,name,kind)
        assert actual is not None
        assert actual == entry  # longest literal prefix, original source order ties


def test_type_separation_visible_entries_and_vision_picker():
    assert find_model("aliyun","qwen3.8-text-embedding","KnowledgeQA")["match"] == "qwen3.8*"
    assert find_model("aliyun","qwen3.8-text-embedding","Embedding") is None
    for p in providers():
        for m in models(p["ID"],"vlm"):
            assert m.get("id") and not m.get("deprecated") and "image" in m["input"]
    assert all(m.get("id") and not m.get("deprecated") for m in models("openai","KnowledgeQA"))


def test_source_capacity_and_model_overlay_without_invented_unknown_values():
    deepseek=resolve_model_config("deepseek","deepseek-flash",role="cheap")
    assert (deepseek.context_window,deepseek.max_output_tokens,deepseek.reasoning,deepseek.supports_vision)==(1_000_000,384_000,True,True)
    bge=resolve_model_config("siliconflow","BAAI/bge-m3",role="embedding")
    assert bge.dimensions == 1024 and bge.embedding.dimensions_field == ""
    unknown=resolve_model_config("siliconflow","Xing-unknown",role="expensive")
    assert not unknown.cataloged and unknown.context_window == unknown.max_output_tokens == 0
    assert unknown.chat.thinking_format == "enable-thinking"


def test_vendor_and_model_precedence_and_false_zero_row_overrides():
    config=resolve_model_config("aliyun","qwen3.5-unknown",role="cheap",internal_overrides={
        "spec_override":{"compat":{"thinking_always_send":False,"supports_seed":False,"extra_body":{"a":1}}}})
    assert not config.chat.thinking_always_send and not config.chat.supports_seed
    assert config.chat.thinking_disable_on_non_stream
    cfg=resolve_model_config("deepseek","deepseek-v4-pro",role="expensive")
    assert cfg.thinking_levels["low"] == "high"
    assert provider_config("siliconflow","BAAI/bge-m3",model_type="Embedding").chat.thinking_format == "enable-thinking"


def test_source_nonthinking_gate_and_typed_override_validation():
    cfg=provider_config("deepseek","deepseek-chat")
    assert cfg.chat.thinking_format == "none" and not cfg.chat.supports_reasoning_effort
    with pytest.raises(ProviderError,match="MODEL_PROTOCOL_CONFIGURATION_INVALID"):
        resolve_model_config("deepseek","deepseek-flash",role="cheap",internal_overrides={"spec_override":{"compat":{"made_up":1}}})
    cfg=resolve_model_config("gpustack","unknown",role="embedding")
    assert cfg.embedding.truncate_prompt_tokens == 511
    with pytest.raises(ProviderError,match="MODEL_TRUNCATE_CONFIGURATION_INVALID"):
        resolve_model_config("siliconflow","BAAI/bge-reranker-v2-m3",role="rerank",internal_overrides={"extra_config":{"truncate_prompt_tokens":"512"}})


def test_source_generic_and_legacy_url_inference():
    assert detect_by_url("https://api.deepseek.com/v1") == "deepseek"
    assert provider_definition("invented", "https://api.deepseek.com/v1")["ID"] == "generic"
    cfg=resolve_model_config("", "deepseek-flash",base_url="https://api.deepseek.com/v1",role="cheap")
    assert cfg.provider == "deepseek"
    unknown=resolve_model_config("invented","custom",base_url="https://custom.example/v1",role="cheap")
    assert unknown.provider == "generic" and unknown.chat.max_tokens_field == "max_tokens"
    assert not unknown.requires_auth and unknown.context_window == 0


def test_source_azure_paths_and_per_protocol_auth():
    cfg=resolve_model_config("azure_openai","deployment/a",base_url="https://resource.example",role="cheap")
    assert cfg.endpoint_url == "https://resource.example/openai/v1/chat/completions" and cfg.auth_style == "api-key"
    cfg=resolve_model_config("azure_openai","deployment/a",base_url="https://resource.example",role="embedding",
            internal_overrides={"extra_config":{"api_version":"2025-04-01-preview"}})
    assert cfg.endpoint_url.endswith("/openai/deployments/deployment%2Fa/embeddings")
    assert cfg.query == {"api-version":"2025-04-01-preview"}
    assert resolve_model_config("openai","gpt-4o",role="cheap").api == "openai-responses"
    assert resolve_model_config("openai","gpt-4o",base_url="https://relay.example/v1",role="cheap").api == "openai-completions"


def test_source_native_embedding_protocol_and_endpoint_selection():
    cfg=resolve_model_config("volcengine","doubao-embedding-vision-250615",role="embedding")
    assert cfg.embedding_api == "ark-embeddings" and cfg.endpoint_url.endswith("/api/v3/embeddings/multimodal")
    cfg=resolve_model_config("gemini","gemini-embedding-001",role="embedding")
    assert cfg.embedding_api == "google-embeddings"
