"""Static, validated registrations. No discovery, secrets, or network access."""
from dataclasses import dataclass


ENDPOINTS = {"siliconflow": "https://api.siliconflow.cn/v1", "deepseek": "https://api.deepseek.com"}
KEY_ENVS = {"siliconflow": "SILICONFLOW_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
CAPABILITIES = {
    ("siliconflow", "XingChenAGI/Xing4.0-29B"): {"chat_cheap", "chat_expensive"},
    ("deepseek", "deepseek-flash"): {"chat_cheap", "chat_expensive", "vision"},
    ("siliconflow", "BAAI/bge-m3"): {"embedding"},
    ("siliconflow", "BAAI/bge-reranker-v2-m3"): {"rerank"},
}


@dataclass(frozen=True)
class ModelSpec:
    model_key: str
    role: str
    provider: str
    model_id: str
    api_key_env: str
    base_url: str
    dimension: int | None = None
    enabled: bool = True
    resolved_revision: str = "UNKNOWN"
    input_semantics_version: str = "context-header-child/v1"

    def __post_init__(self):
        if self.role not in CAPABILITIES.get((self.provider, self.model_id), set()):
            raise ValueError("MODEL_CAPABILITY_UNSUPPORTED")
        if self.base_url != ENDPOINTS[self.provider] or self.api_key_env != KEY_ENVS[self.provider]:
            raise ValueError("MODEL_ENDPOINT_OR_KEY_ENV_UNTRUSTED")
        if not self.model_key or type(self.enabled) is not bool:
            raise ValueError("MODEL_REGISTRATION_INVALID")
        if (self.role == "embedding" and self.dimension != 1024) or (self.role != "embedding" and self.dimension is not None):
            raise ValueError("MODEL_DIMENSION_UNSUPPORTED_BY_SCHEMA")
        if not self.resolved_revision or not self.input_semantics_version:
            raise ValueError("MODEL_IDENTITY_INVALID")


class ModelRegistry:
    def __init__(self, models, defaults):
        models = tuple(models)
        self.models = {model.model_key: model for model in models}
        self.defaults = dict(defaults)
        if len(self.models) != len(models):
            raise ValueError("DUPLICATE_MODEL_KEY")
        if set(self.defaults) != {"chat_cheap", "chat_expensive", "embedding", "rerank", "vision"}:
            raise ValueError("MODEL_DEFAULT_ROLES_INVALID")
        for role, key in self.defaults.items():
            self.select(role, key)

    def select(self, role, key=None):
        model = self.models.get(key or self.defaults.get(role))
        if model is None or model.role != role or not model.enabled:
            raise ValueError("MODEL_ROLE_SELECTION_INVALID")
        return model

    @classmethod
    def from_dict(cls, value):
        if set(value) != {"models", "defaults"}:
            raise ValueError("MODEL_REGISTRY_CONFIG_INVALID")
        return cls([ModelSpec(**row) for row in value["models"]], value["defaults"])

    @classmethod
    def frozen_defaults(cls):
        roles = [("chat_cheap", "siliconflow", "XingChenAGI/Xing4.0-29B"),
                 ("chat_expensive", "deepseek", "deepseek-flash"),
                 ("embedding", "siliconflow", "BAAI/bge-m3"),
                 ("rerank", "siliconflow", "BAAI/bge-reranker-v2-m3"),
                 ("vision", "deepseek", "deepseek-flash")]
        return cls([ModelSpec(role+"_default", role, provider, model, KEY_ENVS[provider],
                             ENDPOINTS[provider], 1024 if role == "embedding" else None)
                    for role, provider, model in roles], {role: role+"_default" for role, _, _ in roles})
