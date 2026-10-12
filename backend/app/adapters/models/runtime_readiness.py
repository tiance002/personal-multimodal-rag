"""Local configuration evidence for the selected adapters; never a model probe.

Model choice remains in the registry/factory. Capacity is supplied by a verified
adapter capability, not by accepting an arbitrary user-entered token number.
"""
import os

from backend.app.adapters.models.cloud import EmbeddingAdmission
from backend.app.ports.context_budget import ContextDenied


def configured_embedding_admission(spec, path):
    if path is None:
        return None, "EMBEDDING_CAPACITY_GUARANTEE_UNKNOWN"
    # This is the currently verified capability adapter, not a default model
    # override. Other registered models must supply their own verified loader.
    if (spec.provider, spec.model_id) != ("siliconflow", "BAAI/bge-m3"):
        return None, "EMBEDDING_TOKENIZER_MODEL_UNSUPPORTED"
    try:
        return EmbeddingAdmission.from_bge_m3_file(path), None
    except FileNotFoundError:
        return None, "EMBEDDING_TOKENIZER_MISSING"
    except Exception:
        # No filesystem paths, file contents or third-party errors are public.
        return None, "EMBEDDING_TOKENIZER_UNVERIFIED"


def configuration_readiness(container):
    """Read actual assembly without querying DB, storage, network or credentials.

Credential presence is a boolean only. READY_CONFIG does not establish runtime
provider availability, account balance, KB consent or index compatibility.
"""
    settings = container.settings
    embedding = container.store.embedding_provider
    spec = getattr(embedding, "spec", None)
    reasons = []
    if embedding is None:
        reasons.append("EMBEDDING_PROVIDER_MISSING")
    elif getattr(embedding, "provider_kind", None) == "cloud":
        if not getattr(embedding, "enabled", False):
            reasons.append("MODEL_ROLE_EGRESS_DENIED")
        if getattr(embedding, "usage_guard", None) is None:
            reasons.append("MODEL_USAGE_GUARD_REQUIRED")
        if spec is None or not embedding.credential_present():
            reasons.append("MODEL_CREDENTIAL_MISSING")
        admission = getattr(embedding, "admission", None)
        try:
            if admission is None:
                raise ValueError()
            admission.count(["configuration probe"])
        except Exception:
            reasons.append(getattr(container, "embedding_admission_error", None)
                           or "EMBEDDING_CAPACITY_GUARANTEE_UNKNOWN")
    else:
        # A process responding is not evidence of a local model's input limits.
        reasons.append("MODEL_INPUT_CAPACITY_UNVERIFIED")
    roles = {"embedding": {
        "provider": getattr(embedding, "provider_name", "ollama" if embedding is not None else "UNKNOWN"),
        "model": getattr(spec, "model_id", getattr(embedding, "embedding_model", "UNKNOWN")),
        "status": "NOT_READY" if reasons else "READY_CONFIG",
        "reasons": reasons,
    }}
    windows = getattr(getattr(container.quick_chain, "context_manager", None), "windows", {})
    selected_role = "cloud_chat" if settings.prefer_cloud else "local_chat"
    window = windows.get(selected_role)
    chat_reasons = []
    gateway = (getattr(container.quick_chain, "cloud_answer_gateway", None) if settings.prefer_cloud
               else getattr(container.quick_chain, "answer_gateway", None))
    if gateway is None:
        chat_reasons.append("GENERATION_PROVIDER_MISSING")
    try:
        if window is None:
            raise ContextDenied("MODEL_CONTEXT_CAPACITY_UNKNOWN")
        window.count([{'role': 'user', 'content': 'configuration probe'}])
    except Exception:
        chat_reasons.append("MODEL_CONTEXT_CAPACITY_UNKNOWN")
    roles["chat"] = {
        "provider": window.provider if window else "UNKNOWN",
        "model": window.model if window else "UNKNOWN",
        "status": "NOT_READY" if chat_reasons else "READY_CONFIG",
        "reasons": chat_reasons,
    }
    factory = getattr(container, 'provider_factory', None)
    if settings.service_model_policy_json is not None and factory is not None:
        errors = container.service_capacity_errors or {}
        if 'embedding' not in factory.service_bindings:
            roles['embedding']['reasons'].append(errors.get('embedding', 'SERVICE_POLICY_MISSING'))
            roles['embedding']['status'] = 'NOT_READY'
        for role in ('chat_cheap', 'chat_expensive', 'vision', 'rerank'):
            spec = factory.registry.select(role)
            binding = factory.service_bindings.get(role)
            reasons = []
            if role not in factory.enabled_roles:
                reasons.append('MODEL_ROLE_EGRESS_DENIED')
            if binding is None:
                reasons.append(errors.get(role, 'SERVICE_POLICY_MISSING'))
            key = settings.model_api_keys.get(role, os.environ.get(spec.api_key_env, ''))
            if not key.strip():
                reasons.append('MODEL_CREDENTIAL_MISSING')
            if role in ('chat_cheap', 'chat_expensive') and not settings.redis_url:
                reasons.append('SERVICE_RUN_LIFECYCLE_REQUIRED')
            if role in ('chat_cheap', 'chat_expensive') and not settings.rule_router_enabled:
                reasons.append('SERVICE_ROUTER_REQUIRED')
            output_readiness = None
            if binding is not None and role in ('chat_cheap', 'chat_expensive'):
                capacity = getattr(binding, 'capacity', None)
                if capacity is not None and hasattr(capacity, 'output_readiness'):
                    output_readiness = capacity.output_readiness
                    if output_readiness['endpoint_output_evidence'] == 'UNKNOWN':
                        reasons.append('OUTPUT_CAPACITY_UNVERIFIED')
            roles[role] = dict(provider=spec.provider, model=spec.model_id,
                status='NOT_READY' if reasons else 'READY_CONFIG', reasons=reasons,
                capacity_status='LOCAL_BOUND_VERIFIED' if binding else 'UNKNOWN', hosted_equivalence='UNKNOWN')
            if output_readiness is not None:
                roles[role]['ordinary_output'] = output_readiness
        if settings.rule_router_enabled:
            roles['chat'] = dict(roles['chat_cheap'])
            roles['chat']['reasons'] = sorted(set(roles['chat_cheap']['reasons'] + roles['chat_expensive']['reasons']))
            roles['chat']['status'] = 'NOT_READY' if roles['chat']['reasons'] else 'READY_CONFIG'
        else:
            roles['chat']['reasons'].append('SERVICE_ROUTER_REQUIRED')
            roles['chat']['status'] = 'NOT_READY'
    required = ('embedding', 'chat')
    if settings.vision_egress_enabled:
        required += ('vision',)
    return {"schema_version": "model-readiness-v1", "check_kind": "CONFIGURATION_ONLY",
            "status": "NOT_READY" if any(roles[row]["reasons"] for row in required if row in roles) else "READY_CONFIG",
            "runtime_verified": False, "roles": roles,
            "configuration_revision": settings.model_configuration_revision}
