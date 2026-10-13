"""Resolved protocol baseline + pinned vendor overlays, not model-role branches."""
from dataclasses import replace
from .types import ChatSettings, EmbeddingSettings, ModelConfig, ProviderError


def provider_config(provider: str, model: str, *, model_id: str | None = None,
                    base_url: str | None = None, **overrides) -> ModelConfig:
    """Construct documented provider defaults; caller owns per-model overrides.

    Xing is absent from the pinned generated catalog: do not invent its context
    or output ceiling. DeepSeek Flash capacity comes from that catalog entry.
    """
    common = dict(provider=provider, model=model, model_id=model_id or model)
    if provider == "siliconflow":
        chat = replace(ChatSettings(), max_tokens_field="max_tokens", thinking_format="enable-thinking",
                       thinking_budget_field="thinking_budget", supports_reasoning_effort=True,
                       prompt_cache_accounting=True)
        embedding = EmbeddingSettings(send_encoding_format=True, dimensions_field="dimensions", max_batch_size=32)
        if model == "BAAI/bge-m3":
            embedding = replace(embedding, dimensions_field="")
        common.update(base_url=base_url or "https://api.siliconflow.cn/v1", chat=chat, embedding=embedding)
    elif provider == "deepseek":
        chat = replace(ChatSettings(), max_tokens_field="max_tokens", thinking_format="thinking-type",
                       supports_reasoning_effort=True, prompt_cache_accounting=True,
                       tool_choice_modes=("none", "auto"))
        common.update(base_url=base_url or "https://api.deepseek.com/v1", chat=chat,
                      thinking_levels={"minimal": "low", "low": "low", "medium": "high", "high": "high",
                                       "xhigh": "max", "max": "max"})
        if model == "deepseek-flash":
            common.update(context_window=1_000_000, max_output_tokens=384_000, reasoning=True, supports_vision=True)
    elif provider == "openai-compatible":
        if base_url is None:
            raise ProviderError("MODEL_ENDPOINT_REQUIRED")
        common.update(base_url=base_url)
    else:
        raise ProviderError("MODEL_PROVIDER_UNSUPPORTED")
    return ModelConfig(**(common | overrides))
