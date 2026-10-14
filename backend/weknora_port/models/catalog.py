"""MIT semantic port of fixed catalog/catalog.go and runtime resolution.

Immutable resource data is read locally. No discovery or network request occurs.
Source facts here describe the fixed commit, not current vendor guarantees.
"""
from __future__ import annotations
import copy
import json
import re
from dataclasses import fields, replace
from pathlib import Path
from urllib.parse import quote, urlsplit
from .types import ChatSettings, EmbeddingSettings, RerankSettings, ModelConfig, ProviderError

UPSTREAM_SHA = "3e8b0bfc80b845b2d4b2ed683994748741450a97"
_ROOT = Path(__file__).with_name("catalog_resources")
_REGISTRY = json.loads((_ROOT / "providers.generated.json").read_text(encoding="utf-8"))
_MODELS = json.loads((_ROOT / "models.generated.json").read_text(encoding="utf-8"))
if _REGISTRY["upstream"] != UPSTREAM_SHA or _REGISTRY["version"] != 1 or _MODELS["version"] != 1:
    raise RuntimeError("unsupported fixed-source model catalogue")
_PROTOCOLS = {"openai-completions": "OpenAICompletions", "openai-responses": "OpenAIResponses",
              "anthropic-messages": "AnthropicMessages", "google-generative-ai": "GoogleGenerativeAI",
              "openai-embeddings": "Embeddings", "rerank": "Rerank", "transcriptions": "Transcriptions"}
_ALIASES = {"openai-compatible": "generic"}


def _kind(kind):
    return {"": "KnowledgeQA", "VLLM": "KnowledgeQA", "chat": "KnowledgeQA", "cheap": "KnowledgeQA",
            "expensive": "KnowledgeQA", "embedding": "Embedding", "rerank": "Rerank", "vlm": "KnowledgeQA"}.get(kind, kind)


def providers(model_type=None):
    values = sorted(_REGISTRY["providers"].values(), key=lambda p: (p.get("Order", 0), p["ID"]))
    if model_type:
        kind = {"vlm": "VLLM", "chat": "KnowledgeQA", "cheap": "KnowledgeQA", "expensive": "KnowledgeQA",
                "embedding": "Embedding", "rerank": "Rerank"}.get(model_type, model_type)
        values = [p for p in values if kind in p["ModelTypes"]]
    return copy.deepcopy(values)


def detect_by_url(base_url):
    matches = [(len(pattern), -p.get("Order", 0), p["ID"]) for p in providers()
               for pattern in p.get("URLPatterns", ()) if pattern and pattern.lower() in base_url.lower()]
    if not matches:
        return "generic"
    best = max(m[:2] for m in matches)
    return min(m[2] for m in matches if m[:2] == best)


def provider_definition(provider, base_url=""):
    name = _ALIASES.get(provider.strip().lower(), provider.strip().lower())
    if not name:
        name = detect_by_url(base_url)
    return copy.deepcopy(_REGISTRY["providers"].get(name, _REGISTRY["providers"]["generic"]))


def models(provider, model_type=None):
    entries = copy.deepcopy(_MODELS["providers"][provider_definition(provider)["ID"]])
    if model_type is None:
        return entries
    if model_type in ("VLLM", "vlm"):
        return [m for m in entries if m.get("id") and not m.get("deprecated")
                and _kind(m.get("type", "")) == "KnowledgeQA" and "image" in m.get("input", ())]
    return [m for m in entries if m.get("id") and not m.get("deprecated")
            and _kind(m.get("type", "")) == _kind(model_type)]


def find_model(provider, name, model_type="KnowledgeQA"):
    needle = name.strip().lower()
    if not needle:
        return None
    entries = [m for m in models(provider) if _kind(m.get("type", "")) == _kind(model_type)]
    for m in entries:
        if m.get("id", "").lower() == needle:
            return m
    for m in entries:
        if needle in [a.lower() for a in m.get("aliases", ())]:
            return m
    best, score = None, -1
    for m in entries:
        pattern = m.get("match", "")
        if pattern and re.fullmatch(".*".join(re.escape(part) for part in pattern.lower().split("*")), needle):
            prefix = len(pattern.split("*", 1)[0])
            if prefix > score:
                best, score = m, prefix
    return best


def _merge(settings, overlay, *, allowed):
    # Match executable ApplyCompat: pointers set false/zero; nonempty maps merge.
    for key, value in overlay.items():
        if key not in allowed:
            raise ProviderError("MODEL_PROTOCOL_CONFIGURATION_INVALID")
        if value is None or isinstance(value, (list, dict)) and not value:
            continue
        if isinstance(value, dict):
            settings[key] = {**settings.get(key, {}), **copy.deepcopy(value)}
        else:
            settings[key] = copy.deepcopy(value)
    return settings


def protocol_settings(protocol, vendor, spec=None, row_compat=None):
    name = _PROTOCOLS[protocol]
    schema = _REGISTRY["compat_fields"][name + "Compat"]
    base = _REGISTRY["protocol_defaults"]["Default" + name]
    out = {schema.get(key, key): copy.deepcopy(value) for key, value in base.items()}
    overlay = vendor.get("Compat", {}).get(name, {})
    _merge(out, {schema[key]: value for key, value in overlay.items()}, allowed=schema.values())
    if spec:
        _merge(out, spec.get("compat", {}), allowed=schema.values())
    if row_compat:
        _merge(out, row_compat, allowed=schema.values())
    return out


def _settings(cls, raw):
    aliases = {"request_timeout_seconds": "request_timeout"}
    allowed = {f.name for f in fields(cls)}
    value = {}
    for key, item in raw.items():
        key = aliases.get(key, key)
        if key in allowed:
            value[key] = tuple(item) if isinstance(item, list) else item
    return cls(**value)


def resolve_config(provider, model, *, model_id=None, base_url=None, model_type="KnowledgeQA",
                   extra_config=None, spec_override=None, **overrides):
    extra, override = dict(extra_config or {}), dict(spec_override or {})
    vendor, kind = provider_definition(provider, base_url or ""), _kind(model_type)
    spec = find_model(vendor["ID"], model, kind)
    cataloged = spec is not None
    spec = spec or {"id": model, "type": kind}
    original_api = spec.get("api") or vendor.get("API", "openai-completions")
    api = override.get("api") or original_api
    for key in ("reasoning", "input", "context_window", "max_output_tokens"):
        if key in override and override[key] is not None and (key in ("reasoning", "input") or override[key] > 0):
            spec[key] = override[key]
    url = (base_url or vendor.get("DefaultBaseURLs", {}).get(kind)
           or vendor.get("DefaultBaseURLs", {}).get("KnowledgeQA", "")).strip().rstrip("/")
    if not url:
        raise ProviderError("MODEL_ENDPOINT_REQUIRED")
    if kind == "KnowledgeQA":
        if extra.get("api", "").strip():
            api = extra["api"].strip()
        elif not override.get("api"):
            lower = url.lower()
            if lower.endswith(("/anthropic", "/anthropic/v1")):
                api = "anthropic-messages"
            elif api == "google-generative-ai" and (lower.endswith("/openai") or "/openai/" in lower):
                api = "openai-completions"
            if vendor["ID"] == "openai" and urlsplit(url).hostname == "api.openai.com":
                api = "openai-responses"
        if api not in ("openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai", "ollama"):
            raise ProviderError("MODEL_PROTOCOL_UNSUPPORTED")
    levels = {**vendor.get("ThinkingLevels", {}), **spec.get("thinking_levels", {}), **override.get("thinking_levels", {})}
    all_settings = {}
    for protocol in ("openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai"):
        layer = spec if kind == "KnowledgeQA" and original_api == protocol else None
        all_settings[protocol] = protocol_settings(protocol, vendor, layer,
                override.get("compat") if kind == "KnowledgeQA" and api == protocol else None)
    chat = _settings(ChatSettings, all_settings["openai-completions"])
    control = extra.get("thinking_control", "").strip()
    if control:
        controls = {"none":"none", "enable_thinking":"enable-thinking", "thinking_type":"thinking-type"}
        chat = replace(chat, thinking_format=controls.get(control.lower(), "chat-template-kwargs"))
    elif kind == "KnowledgeQA" and cataloged and not spec.get("reasoning", False):
        chat = replace(chat, thinking_format="none", thinking_budget_field="", supports_reasoning_effort=False)
        all_settings["google-generative-ai"]["thinking_mode"] = "none"
        levels = {k: None for k in ("off", "auto", "minimal", "low", "medium", "high", "xhigh", "max")}
    eraw = protocol_settings("openai-embeddings", vendor, spec if kind == "Embedding" else None,
                             override.get("compat") if kind == "Embedding" else None)
    rraw = protocol_settings("rerank", vendor, spec if kind == "Rerank" else None,
                             override.get("compat") if kind == "Rerank" else None)
    embedding_api, rerank_api = eraw.get("api") or vendor.get("EmbeddingAPI") or "openai-embeddings", vendor.get("RerankAPI") or "cohere-rerank"
    embedding, rerank = _settings(EmbeddingSettings, eraw), _settings(RerankSettings, rraw)
    if embedding.accepts_truncate_prompt_tokens:
        embedding = replace(embedding, truncate_prompt_tokens=overrides.pop("truncate_prompt_tokens", 0) or 511)
    if kind == "Rerank":
        if rerank.unsupported_reason:
            raise ProviderError("MODEL_PROTOCOL_UNSUPPORTED")
        if extra.get("score_scale", "").strip():
            scale = extra["score_scale"].strip().lower()
            if scale not in ("probability", "logit"):
                raise ProviderError("MODEL_RERANK_SCORE_SCALE_INVALID")
            rerank = replace(rerank, score_scale=scale)
        if extra.get("truncate_prompt_tokens", "").strip():
            try:
                budget = int(extra["truncate_prompt_tokens"])
            except ValueError:
                raise ProviderError("MODEL_TRUNCATE_CONFIGURATION_INVALID") from None
            if not rerank.accepts_truncate_prompt_tokens or budget <= 0:
                raise ProviderError("MODEL_TRUNCATE_CONFIGURATION_INVALID")
            rerank = replace(rerank, truncate_prompt_tokens=budget)
    endpoint, query = "", {}
    if vendor["ID"] == "azure_openai":
        path = "/embeddings" if kind == "Embedding" else "/responses" if api == "openai-responses" else "/chat/completions"
        version = extra.get("api_version", "").strip()
        if version:
            endpoint, query = url + "/openai/deployments/" + quote(model, safe="") + path, {"api-version": version}
        else:
            endpoint = url + ("" if url.endswith("/openai/v1") else "/openai/v1") + path
    elif vendor["ID"] == "aliyun" and kind == "Embedding":
        root = re.split(r"/compatible-mode|/api/v1", url, maxsplit=1)[0]
        endpoint = root + ("/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding" if embedding_api == "dashscope-embeddings" else "/compatible-mode/v1/embeddings")
    elif vendor["ID"] == "volcengine" and kind == "Embedding":
        endpoint = url.split("/api/", 1)[0] + ("/api/v3/embeddings" if embedding_api == "openai-embeddings" else "/api/v3/embeddings/multimodal")
    elif vendor["ID"] == "weknoracloud" and kind == "KnowledgeQA":
        endpoint = url + "/api/v1/chat/completions"
    common = dict(provider=vendor["ID"], model=extra.get("remote_model_name", "").strip() or model,
                  model_id=model_id or model, base_url=url, context_window=spec.get("context_window", 0),
                  max_output_tokens=spec.get("max_output_tokens", 0), reasoning=spec.get("reasoning", False),
                  supports_vision="image" in spec.get("input", ()), dimensions=spec.get("dimension", 0),
                  chat=chat, embedding=embedding, reranking=rerank, thinking_levels=levels, api=api,
                  embedding_api=embedding_api, rerank_api=rerank_api,
                  auth_style=vendor.get("AuthByAPI", {}).get(api) or vendor.get("Auth", "bearer"),
                  protocol_settings=all_settings, extra_config=extra, cataloged=cataloged,
                  endpoint_url=endpoint, query=query, custom_headers=vendor.get("Headers", {}),
                  requires_auth=vendor.get("RequiresAuth", False),
                  model_type="VLLM" if model_type in ("VLLM","vlm") else kind)
    return ModelConfig(**(common | overrides))


def resolve_model_config(provider, model, *, base_url=None, model_id=None, role=None,
                         model_type=None, internal_overrides=None):
    """Role store adapter; types are source capability names, roles only bindings."""
    return resolve_config(provider, model, base_url=base_url, model_id=model_id,
                          model_type=model_type or {"vlm":"VLLM","embedding":"Embedding","rerank":"Rerank"}.get(role,"KnowledgeQA"),
                          **dict(internal_overrides or {}))
