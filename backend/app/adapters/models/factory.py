"""Composition-only provider construction; no network or secret reads at startup."""
from backend.app.adapters.models.cloud import (
    CloudChat, DeepSeekVision, SiliconFlowEmbedding, SiliconFlowRerank, XingChatAdmission,
)
from backend.app.domain.adaptive_chunking import ChunkingConfig
from backend.app.adapters.models.deepseek import DeepSeekGateway
from backend.app.ports.service_policy import ServiceModelBinding


class ProviderFactory:
    def __init__(self, registry, *, enabled_roles=(), usage_guards=None, embedding_admission=None,
                 chunking_config=None,free_model_bindings=None, service_bindings=None):
        self.registry = registry
        self.enabled_roles = frozenset(enabled_roles)
        if not self.enabled_roles <= set(registry.defaults):
            raise ValueError("EGRESS_ROLE_UNSUPPORTED")
        self.usage_guards = dict(usage_guards or {})
        self.api_keys = {}
        self.embedding_admission = embedding_admission
        self.chunking_config = chunking_config or ChunkingConfig()
        from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding
        self.free_model_bindings = dict(free_model_bindings or {})
        self.service_bindings = dict(service_bindings or {})
        for role, binding in self.service_bindings.items():
            if type(binding) is not ServiceModelBinding or role in self.free_model_bindings:
                raise ValueError('SERVICE_MODEL_BINDING_INVALID')
            binding.validate(registry.select(role))
            if role in self.usage_guards and self.usage_guards[role] is not binding.usage_guard:
                raise ValueError('SERVICE_USAGE_GUARD_MISMATCH')
            self.usage_guards[role] = binding.usage_guard
        for role,binding in self.free_model_bindings.items():
            if type(binding) is not SiliconFlowFreeBinding or role not in ('embedding','rerank'):
                raise ValueError('SF_FREE_BINDING_INVALID')
            binding.permit.validate(registry.select(role),binding.authorized_transport)

    def build(self, role, key=None, *, xing_binding=None):
        spec = self.registry.select(role, key)
        kwargs = dict(enabled=role in self.enabled_roles, usage_guard=self.usage_guards.get(role))
        if role in self.api_keys:
            kwargs['api_key'] = self.api_keys[role]
        service_binding = self.service_bindings.get(role)
        if service_binding is not None:
            service_binding.validate(spec)
            if xing_binding is not None:
                raise ValueError('SERVICE_EVALUATION_POLICY_CONFLICT')
            kwargs.update(service_binding=service_binding, usage_guard=service_binding.usage_guard)
        free_binding = self.free_model_bindings.get(role)
        if free_binding is not None:
            kwargs.update(free_binding=free_binding,transport=free_binding.authorized_transport,
                          request_window=free_binding.permit)
        if xing_binding is not None:
            from backend.app.application.rule_router_v1 import V6XingGenerationBinding
            if type(xing_binding) is not V6XingGenerationBinding or role != 'chat_cheap':
                raise ValueError('XING_BINDING_INVALID')
            kwargs.update(xing_binding=xing_binding, transport=xing_binding.authorized_transport,
                xing_admission=XingChatAdmission(grant=xing_binding.grant,
                    counter=xing_binding.capacity_counter, transport=xing_binding.authorized_transport))
        if role == "embedding":
            admission = free_binding.admission if free_binding is not None else self.embedding_admission
            if free_binding is not None and self.embedding_admission is not None and self.embedding_admission is not admission:
                raise ValueError('SF_FREE_EMBEDDING_ADMISSION_MISMATCH')
            return SiliconFlowEmbedding(spec, chunking_index_identity=self.chunking_config.identity,
                                        admission=admission, **kwargs)
        adapter = DeepSeekVision if role == "vision" else SiliconFlowRerank if role == "rerank" else CloudChat
        return adapter(spec, **kwargs)

    def build_v6_gateway(self, *, activity_gate, gate_types, api_key, transport=None, send_claim_callback=None):
        """Explicit evaluation composition; never initializes a grant/ledger."""
        spec = self.registry.select('chat_expensive')
        if (spec.provider, spec.model_id) != ('deepseek', 'deepseek-flash'):
            raise ValueError('ROUTER_MODEL_IDENTITY_MISMATCH')
        return DeepSeekGateway(api_key=api_key, model=spec.model_id,
            cloud_enabled='chat_expensive' in self.enabled_roles,
            attempt_gate=activity_gate, gate_types=gate_types, transport=transport,
            send_claim_callback=send_claim_callback)

    def build_v6_token_usage_guard(self, gateway, binding):
        from backend.app.application.provider_usage import CanonicalTokenUsageGuard
        return CanonicalTokenUsageGuard(binding, gateway)
