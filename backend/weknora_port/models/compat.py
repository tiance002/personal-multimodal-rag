"""Source-derived configuration, independent of configured role identities."""
from .catalog import resolve_config, find_model
from .types import ModelConfig


def provider_config(provider: str, model: str, *, model_id: str | None = None,
                    base_url: str | None = None, **overrides) -> ModelConfig:
    if "dimensions" in overrides:
        overrides.setdefault("supports_dimension_override",True)
    if "model_type" not in overrides and not find_model(provider, model, "KnowledgeQA"):
        # Backward-compatible convenience for an exact non-chat model identity.
        # Application role binding always supplies its source ModelType.
        for kind in ("Embedding", "Rerank"):
            spec = find_model(provider, model, kind)
            if spec and (spec.get("id", "").lower() == model.strip().lower() or
                         model.strip().lower() in [a.lower() for a in spec.get("aliases", ())]):
                overrides["model_type"] = kind
                break
    return resolve_config(provider, model, model_id=model_id, base_url=base_url, **overrides)
