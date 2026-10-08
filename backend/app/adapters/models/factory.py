"""Composition-only provider construction; no network or secret reads at startup."""
from backend.app.adapters.models.cloud import CloudChat, DeepSeekVision, SiliconFlowEmbedding, SiliconFlowRerank
from backend.app.domain.adaptive_chunking import ChunkingConfig


class ProviderFactory:
    def __init__(self, registry, *, enabled_roles=(), usage_guards=None, embedding_admission=None,
                 chunking_config=None):
        self.registry = registry
        self.enabled_roles = frozenset(enabled_roles)
        if not self.enabled_roles <= set(registry.defaults):
            raise ValueError("EGRESS_ROLE_UNSUPPORTED")
        self.usage_guards = dict(usage_guards or {})
        self.embedding_admission = embedding_admission
        self.chunking_config = chunking_config or ChunkingConfig()

    def build(self, role, key=None):
        spec = self.registry.select(role, key)
        kwargs = dict(enabled=role in self.enabled_roles, usage_guard=self.usage_guards.get(role))
        if role == "embedding":
            return SiliconFlowEmbedding(spec, chunking_index_identity=self.chunking_config.identity,
                                        admission=self.embedding_admission, **kwargs)
        adapter = DeepSeekVision if role == "vision" else SiliconFlowRerank if role == "rerank" else CloudChat
        return adapter(spec, **kwargs)
