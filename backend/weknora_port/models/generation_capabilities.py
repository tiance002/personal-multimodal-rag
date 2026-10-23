"""Fail closed when a requested answer mode cannot be expressed on the wire."""
from __future__ import annotations
import json

from .types import ProviderError


def _thinking_wire(config, body):
    if config.source == 'local':
        return ('think', body.get('think'))
    if config.api == 'openai-completions':
        kind = config.chat.thinking_format
        if kind == 'thinking-type':
            value = (body.get('thinking') or {}).get('type')
            return 'thinking', {'enabled': True, 'disabled': False}.get(value)
        if kind == 'enable-thinking':
            return 'enable_thinking', body.get('enable_thinking')
        if kind == 'chat-template-kwargs':
            return 'chat_template_kwargs', (body.get('chat_template_kwargs') or {}).get('enable_thinking')
        if kind == 'openrouter':
            value = body.get('reasoning') or {}
            return 'reasoning', value.get('enabled', bool(value.get('effort')) if value.get('effort') else None)
        if kind == 'openai':
            value = body.get(config.chat.reasoning_effort_field or 'reasoning_effort')
            return 'reasoning_effort', None if value is None else value not in ('none', 'off', 'disabled')
    if config.api == 'openai-responses':
        value = (body.get('reasoning') or {}).get('effort')
        return 'reasoning', None if value is None else value not in ('none', 'off', 'disabled')
    if config.api == 'anthropic-messages':
        value = (body.get('thinking') or {}).get('type')
        return 'thinking', {'enabled': True, 'adaptive': True, 'disabled': False}.get(value)
    if config.api == 'google-generative-ai':
        value = (body.get('generationConfig') or {}).get('thinkingConfig') or {}
        if 'thinkingBudget' in value:
            return 'generationConfig', value['thinkingBudget'] != 0
        if 'thinkingLevel' in value:
            return 'generationConfig', str(value['thinkingLevel']).lower() not in ('none', 'off', 'disabled')
    return '', None


def validate_generation_options(config, options, *, body=None, messages=(), stream=False):
    """Return safe provider mapping, without network access or option mutation.

    Legacy internal options with an empty product mode retain their existing
    adapter behavior. Explicit product modes require finite, declared capacity
    and a verifiable enable/disable mapping. No role/model fallback occurs here.
    """
    if options is None or not options.reasoning_mode:
        return {}
    mode = options.reasoning_mode
    if mode not in ('quick', 'smart'):
        raise ProviderError('GENERATION_REASONING_MODE_INVALID')
    maximum = options.completion_budget()
    if type(maximum) is not int or maximum <= 0:
        raise ProviderError('GENERATION_OUTPUT_LIMIT_INVALID')
    if config.max_output_tokens <= 0 or config.context_window <= 0:
        raise ProviderError('MODEL_GENERATION_CAPACITY_UNKNOWN')
    if maximum > config.max_output_tokens:
        raise ProviderError('MODEL_OUTPUT_LIMIT_EXCEEDED')
    if maximum >= config.context_window:
        raise ProviderError('MODEL_CONTEXT_LIMIT_EXCEEDED')
    # UTF-8 bytes are a conservative upper bound for supported byte/subword
    # text tokenizers. This is a safety bound, not provider-reported usage.
    input_bound = sum(len(m.content.encode('utf-8')) + 16 for m in messages)
    input_bound += sum(len(str(p.get('text', '')).encode('utf-8')) for m in messages for p in m.multi_content if p.get('type') == 'text')
    if input_bound + maximum > config.context_window:
        raise ProviderError('MODEL_CONTEXT_LIMIT_EXCEEDED')
    if body is None:
        if config.source == 'local':
            from .local_ollama import build_ollama_body
            body = build_ollama_body(config, messages, options, stream=stream)
        elif config.api == 'openai-completions':
            from .request import build_chat_body
            body = build_chat_body(config, messages, options, stream=stream)
        else:
            from .native_request import build_native_body
            body = build_native_body(config, messages, options, stream=stream)
    field, enabled = _thinking_wire(config, body)
    if mode == 'smart' and (not config.reasoning or enabled is not True):
        raise ProviderError('MODEL_SMART_MODE_UNSUPPORTED')
    if mode == 'quick' and enabled is not False:
        if config.reasoning or not config.cataloged:
            raise ProviderError('MODEL_QUICK_MODE_UNSUPPORTED')
    media = any(m.images or any(p.get('type') == 'image_url' or p.get('image_url') for p in m.multi_content) for m in messages)
    wire = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if media:
        from .input_bounds import deepseek_inline_image_context_input
        input_bound = deepseek_inline_image_context_input(config, body, wire, 'vision')
        input_recipe = 'deepseek-inline-image-context-1024/v1'
    else:
        input_bound = max(input_bound, len(wire))
        input_recipe = 'wire-bytes-upper-bound/v1'
    if input_bound + maximum > config.context_window:
        raise ProviderError('MODEL_CONTEXT_LIMIT_EXCEEDED')
    cap_fields = ('max_tokens', 'max_completion_tokens', 'max_output_tokens')
    mapping = {k: body[k] for k in cap_fields if k in body}
    if 'generationConfig' in body:
        mapping['generationConfig'] = {k: v for k, v in body['generationConfig'].items() if k in ('maxOutputTokens', 'thinkingConfig')}
    if config.source == 'local':
        mapping['options'] = {'num_predict': (body.get('options') or {}).get('num_predict')}
    wire_cap = body.get('max_tokens', body.get('max_completion_tokens', body.get('max_output_tokens',
        (body.get('generationConfig') or {}).get('maxOutputTokens', (body.get('options') or {}).get('num_predict')))))
    if wire_cap != maximum:
        raise ProviderError('MODEL_OUTPUT_LIMIT_MAPPING_UNSUPPORTED')
    if field and field in body:
        mapping[field] = body[field]
    return {'reasoning_mode': mode, 'max_output_tokens': maximum,
            'provider': config.provider, 'model': config.model, 'api': config.api,
            'parameters': mapping, 'input_token_upper_bound': input_bound, 'input_bound_recipe': input_recipe,
            'capacity_source': 'configured-model-capabilities'}
