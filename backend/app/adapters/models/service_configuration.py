"""Parse ordinary-service policy and load existing pinned capabilities locally.

No provider calls, invoice claims, grant edits or database initialization here.
"""
import json

from backend.app.ports.service_policy import ServiceModelPolicy
from backend.app.adapters.models.service_capacity import ServiceCapacity


def configured_service_policies(settings, registry):
    if settings.service_model_policy_json is None:
        return {}
    try:
        raw = json.loads(settings.service_model_policy_json)
        if (type(raw) is not dict or set(raw) != {'schema_version', 'policies'}
                or type(raw['schema_version']) is not int or raw['schema_version'] != 1
                or type(raw['policies']) is not list):
            raise ValueError()
        selected, identities = {}, set()
        if not raw['policies'] and not any(spec.user_configured for spec in registry.models.values()):
            raise ValueError()
        for value in raw['policies']:
            if type(value) is not dict or type(value.get('roles')) is not list:
                raise ValueError()
            policy = ServiceModelPolicy(**{**value, 'roles': tuple(value['roles'])})
            if policy.policy_id in identities:
                raise ValueError()
            identities.add(policy.policy_id)
            for role in policy.roles:
                policy.check_model(registry.select(role))
                if role in selected:
                    raise ValueError()
                selected[role] = policy
        return selected
    except (ValueError, TypeError, KeyError):
        raise ValueError('SERVICE_MODEL_POLICY_CONFIG_INVALID') from None


def configured_service_capacity(spec, settings, embedding_admission):
    try:
        if spec.role == 'embedding':
            capacity = ServiceCapacity(spec.provider, spec.model_id, embedding_admission, kind='embedding')
            capacity.counter.count(['configuration probe'])
        elif (spec.provider, spec.model_id) == ('siliconflow', 'XingChenAGI/Xing4.0-29B'):
            if settings.xing_capacity_resources is None:
                return None, 'XING_CAPACITY_RESOURCES_MISSING'
            from backend.app.adapters.models.siliconflow_xing_capacity import OrdinarySiliconFlowXingCapacityCounter
            capacity = ServiceCapacity(spec.provider, spec.model_id,
                OrdinarySiliconFlowXingCapacityCounter(settings.xing_capacity_resources), kind='xing_text')
        elif spec.role == 'vision':
            if settings.deepseek_tokenizer_path is None:
                return None, 'DEEPSEEK_TOKENIZER_MISSING'
            from backend.app.adapters.models.service_deepseek_capacity import DeepseekV41VisionCapacityCounter
            capacity = ServiceCapacity(spec.provider, spec.model_id,
                DeepseekV41VisionCapacityCounter(settings.deepseek_tokenizer_path), kind='deepseek_vision')
        elif (spec.provider, spec.model_id) == ('deepseek', 'deepseek-flash'):
            if settings.deepseek_tokenizer_path is None:
                return None, 'DEEPSEEK_TOKENIZER_MISSING'
            from backend.app.adapters.models.v6_text_capacity import DeepseekV41RecipeRenderer, OrdinaryDeepseekV41TextCapacityCounter
            from backend.app.adapters.models.service_deepseek_capacity import verified_recipe
            verified_recipe(settings.deepseek_tokenizer_path)
            counter = OrdinaryDeepseekV41TextCapacityCounter(DeepseekV41RecipeRenderer(settings.deepseek_tokenizer_path))
            capacity = ServiceCapacity(spec.provider, spec.model_id, counter, kind='deepseek_text')
        else:
            return None, 'SERVICE_CAPACITY_PROTOCOL_UNSUPPORTED'
        if spec.role not in {'embedding', 'vision'}:
            capacity.count_window({'model': spec.model_id, 'messages': [{'role': 'user', 'content': 'configuration probe'}], 'tools': []})
            proof_path = settings.output_capability_file
            proof_hash = settings.output_capability_sha256
            if proof_path is not None or proof_hash is not None:
                from backend.app.ports.output_capability import load_output_capabilities
                for evidence in load_output_capabilities(proof_path, proof_hash):
                    if (evidence.provider, evidence.model) == (spec.provider, spec.model_id):
                        if evidence.endpoint != spec.base_url.rstrip('/') + '/chat/completions':
                            raise ValueError('OUTPUT_CAPABILITY_ENDPOINT_MISMATCH')
                        capacity.bind_output_evidence(evidence)
        return capacity, None
    except Exception:
        return None, 'SERVICE_CAPACITY_RESOURCES_UNVERIFIED'
