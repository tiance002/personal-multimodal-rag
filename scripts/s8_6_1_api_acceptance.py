"""S8.6.1 test-only formal HTTP profile. Import and plan make zero sends.

Uses the normal RuntimeService configuration seam, Adapter, Registry, SendGate,
LocalExecution and HTTP routes. No production default or ledger is replaced.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.s8_6_4_language import expected_profile_language, message_headers as _language_headers, policy_manifest

PROMPT_B_SUFFIX = (
    'When the supplied evidence contains sufficient information, perform the necessary verifiable calculation or logical inference instead of refusing solely because the result is not stated verbatim.\n'
    'Distinguish derived conclusions from directly stated facts. Do not invent missing inputs, entities, dates, locations, or relationships. If the evidence does not support a required relation, explicitly state the uncertainty.'
)
PROFILE_ID = 's8.6.1-formal-api-prompt-b/v1'
BOUNDED_PROFILE_ID = 's8.6.2-r3-formal-api-prompt-b/v3'
BOUNDED_GOAL = 'S8.6.2-R3'
BOUNDED_BUDGET_SCHEMA = 's862r3-reservation-dryrun/v4'
PROFILE_QUERY_BOUND = {'version': 'effective-query/v1', 'unicode_characters': 256, 'utf8_bytes': 1024}
BASELINE = '0c496c230add039283c80635d4e7d4776a4e373b'
KB_IDS = ('96a9eebd-ffb2-4bc6-9bea-3ed015845479',)
DOCUMENT_IDS = (
    'a973df4a-0b4b-43f7-ad17-55265aac1556',
    '9d440f3c-021d-4c9b-adc5-6e7084fc8886',
    '4846280b-37b3-4fbf-97c9-e4d798b9009c',
    '3fdf4942-1265-4e9e-959d-27772f57e5dd',
)
QUESTIONS = {
    'compare': 'Compare Leyden jar and Franklin bells in terms of operating principles and uses.',
    'dependent': 'Where is the seminar Jeanne Marie Beaumont directed in 2007-2010 located?',
}
BOUNDED_QUESTIONS = {
    **QUESTIONS,
    'parallel': '分别介绍Leyden jar和Franklin bells的工作原理与用途',
}
QU_ONLY_QUESTIONS = {
    'two-object': QUESTIONS['compare'],
    'three-object': 'Compare PostgreSQL, Redis, and SQLite in terms of persistence and concurrency.',
    'parallel': BOUNDED_QUESTIONS['parallel'],
    'numeric-model': 'Compare Qwen3.5-4B and Qwen3-8B in terms of deployment requirements.',
    'relational-negative': '分别介绍A和B之间的通信方式',
    'dimension-negative': '比较PostgreSQL和Redis的区别',
}
MODES = ('off', 'query_structure_v1')



def completed_answer_contract(frames):
    """Fixed server replies have no provider finish; provider replies still must."""
    if not frames or frames[-1].get('response_type')!='complete':return False
    terminal=frames[-1].get('data',{})
    if terminal.get('status') in ('failed','error','cancelled') or any(f.get('response_type')=='error' for f in frames):return False
    if terminal.get('completion_source')=='server_fixed':
        return (terminal.get('provider_completion_required') is False
            and terminal.get('lifecycle_completed') is True
            and terminal.get('is_fallback') is True and terminal.get('attempts')==[]
            and bool(''.join(f.get('content','') for f in frames if f.get('response_type')=='answer').strip()))
    return terminal.get('finish_reason') in ('stop','end_turn','completed') and terminal.get('truncated') is False

def _canonical_default(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f'Unsupported canonical JSON value: {type(value).__name__}')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False, default=_canonical_default).encode('utf8')


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def require(value, code):
    if not value:
        raise ValueError(code)


def write_new(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write('\n')


def compose_configuration(configuration):
    """Append only Owner's B; keep normal resolved options and session mode."""
    from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
    options = dict(configuration.generation_options)
    prompt = options.get('system_prompt', DEFAULT_SYSTEM_PROMPT)
    require(isinstance(prompt, str), 'S861_RESOLVED_SYSTEM_PROMPT_REQUIRED')
    require(PROMPT_B_SUFFIX not in prompt, 'S861_PROFILE_ALREADY_APPLIED')
    options.update(citations_enabled=True, system_prompt=prompt + '\n\n' + PROMPT_B_SUFFIX)
    state = replace(configuration.pipeline_state, enable_expansion=False)
    return replace(configuration, pipeline_state=state, generation_options=options)


def _row(row):
    return asdict(row) if is_dataclass(row) else row


def profile_request_headers(q0, base=None, explicit_override=None):
    headers = _language_headers(base or {}, q0, explicit_override)
    expected, _ = expected_profile_language(q0, explicit_override)
    require(headers.get('Accept-Language') == expected, 'S864_REQUEST_LANGUAGE_HEADER_MISMATCH')
    return headers


def profile_language_guard(actual_language, q0, explicit_override=None):
    locale, expected_language = expected_profile_language(q0, explicit_override)
    require(actual_language == expected_language, 'S864_PROFILE_LANGUAGE_MISMATCH')
    return {'expected_locale': locale, 'expected_language': expected_language,
        'actual_language': actual_language, 'matched': True}


def _request_language_override(turn):
    request = turn.request
    return getattr(request, 'answer_language', None) or getattr(request, 'accept_language', None)


def _budget_state_guard(state, q0, explicit_override=None):
    from backend.weknora_port.retrieval.pipeline import PipelineState
    defaults = PipelineState()
    require(all(getattr(state, name) == getattr(defaults, name) for name in
        ('rewrite_system_prompt', 'rewrite_user_prompt', 'context_template'))
        and not any(getattr(state, name) for name in
            ('attachment_prompt', 'images', 'quoted_context', 'system_prompt_override',
             'intent_system_prompts', 'global_intent_system_prompts')),
        'S861_PREFLIGHT_PROMPT_CONFIGURATION_CHANGED')
    profile_language_guard(state.language, q0, explicit_override)
    require(not state.history and not state.memory_background, 'S861_PREFLIGHT_FRESH_CONTEXT_REQUIRED')


def query_input_bound(value):
    """This acceptance profile's input contract; never truncate production text."""
    try:
        size = len(value.encode('utf8')) if isinstance(value, str) else None
    except UnicodeError:
        size = None
    return {'unicode_characters': len(value) if isinstance(value, str) else None,
        'utf8_bytes': size, 'valid': isinstance(value, str) and bool(value)
            and size is not None and len(value) <= PROFILE_QUERY_BOUND['unicode_characters']
            and size <= PROFILE_QUERY_BOUND['utf8_bytes']}


def _require_query_bound(state):
    effective = state.rewrite_query or state.query
    require(query_input_bound(effective)['valid'], 'PROFILE_INPUT_BOUND_EXCEEDED')
    return effective


def _accepted_client_request_id(turn):
    # Storage binds request.request_id to the server run UUID. Only this
    # separately retained admission field identifies the original HTTP client.
    client_id = getattr(turn, 'client_request_id', None)
    require(isinstance(client_id, str) and bool(client_id), 'S861_APPROVED_TURN_REQUIRED')
    return client_id


class _ObservedQueryRegistry:
    """Delegate the real registry/model; retain raw QU return privately only."""
    def __init__(self, registry, observe, before=None):
        self.registry, self.observe, self.before = registry, observe, before

    def __getattr__(self, name):
        return getattr(self.registry, name)

    async def chat(self, *args, **kwargs):
        model = await self.registry.chat(*args, **kwargs)
        observe, before = self.observe, self.before
        class ObservedModel:
            def __getattr__(self, name):
                return getattr(model, name)
            async def chat(self, messages, options, context):
                if before is not None and context.request_id.endswith('/query_rewrite'):
                    before(model, messages, options, context)
                response = await model.chat(messages, options, context)
                # No synthetic response, retry or replacement of ProviderModel.
                if context.request_id.endswith('/query_rewrite'):
                    observe({'content': getattr(response, 'content', None),
                        'finish_reason': getattr(response, 'finish_reason', ''),
                        'usage': _row(getattr(response, 'usage', None)),
                        'request_id': context.request_id})
                return response
        return ObservedModel()


def state_snapshot(state):
    """Private, finite trace of the actual states, never a public SSE payload."""
    value = {name: getattr(state, name, None) for name in (
        'query', 'rewrite_query', 'query_type', 'intent', 'needs_retrieval',
        'retrieval_strategy_mode', 'source_evidence_mode', 'enable_expansion', 'rerank_top1_floorless',
        'query_trace', 'strategy_trace', 'strategy_coverage', 'bridge_trace',
        'evidence_trace', 'strategy_units_used')}
    value['plan'] = _row(getattr(state, 'strategy_plan', None))
    for name in ('search_results', 'rerank_results', 'merge_results',
                 'bridge_identity_evidence', 'rescue_evidence'):
        value[name] = [_row(row) for row in getattr(state, name, ())]
    value['rounds'] = [{
        'status': result.status, 'reason': result.reason,
        'query': result.state.query, 'rewrite_query': result.state.rewrite_query,
        'targets': [_row(t) for t in result.state.targets],
        'search': [_row(row) for row in result.state.search_results],
        'rerank_scores': dict(result.state.strategy_rerank_scores),
        'accepted_ids': list(result.state.strategy_rerank_accepted_ids),
        'originals': [_row(row) for row in result.state.merge_results],
    } for result in getattr(state, 'strategy_rounds', ())]
    assessment = getattr(state, '_strategy_bridge_assessment', None)
    if assessment is not None:
        value['bridge_assessment'] = asdict(assessment)
    return value


def install_profile(runtime, evidence_directory=None, *, approved_batch=None, expires_at=None,
                    bounded_query_profile=False, approved_scope=None, generation_contract=None):
    """Compose existing server dependencies; no class/Adapter monkeypatch."""
    require(generation_contract in (None, 's88-answer-settings/v1'), 'S88_GENERATION_CONTRACT_INVALID')
    require(not getattr(runtime, '_s861_profile_installed', False), 'S861_PROFILE_ALREADY_INSTALLED')
    ordinary = runtime.ordinary
    original_configuration = ordinary.configuration
    states, sequence, admitted_clients, qu_outputs = {}, [0], {}, {}
    profile_id = BOUNDED_PROFILE_ID if bounded_query_profile else PROFILE_ID
    directory = Path(evidence_directory) if evidence_directory is not None else None
    expected_kbs, expected_docs = KB_IDS, DOCUMENT_IDS
    if approved_scope is not None:
        require(isinstance(approved_scope, dict)
            and set(approved_scope) == {'knowledge_base_ids', 'document_ids'}
            and isinstance(approved_scope['knowledge_base_ids'], (list, tuple))
            and len(approved_scope['knowledge_base_ids']) == 1
            and isinstance(approved_scope['knowledge_base_ids'][0], str)
            and bool(approved_scope['knowledge_base_ids'][0])
            and approved_scope['document_ids'] in ([], ()), 'S861_CLOSED_CASE_SCOPE_REQUIRED')
        expected_kbs, expected_docs = tuple(approved_scope['knowledge_base_ids']), ()

    def save(run_id, stage, status, fields=None):
        if directory is None or run_id not in states:
            return
        sequence[0] += 1
        name = digest(run_id.encode()) + f'.{sequence[0]:05d}.profile.json'
        write_new(directory / name, {'profile': profile_id, 'run_id': run_id,
            'stage': stage, 'status': status, 'fields': fields or {},
            'state': state_snapshot(states[run_id]),
            'proof_boundary': 'actual server state; Provider send/completion proven separately by existing receipts'})

    async def configuration(turn):
        if approved_batch is not None:
            require(expires_at is not None and expires_at > datetime.now(timezone.utc), 'S861_AUTH_EXPIRED')
            client_id = _accepted_client_request_id(turn)
            allowed = [row for row in approved_batch['turns'] if row['request_id'] == client_id]
            require(len(allowed) == 1 and allowed[0]['question'] == turn.request.content,
                    'S861_APPROVED_TURN_REQUIRED')
            require(set(turn.context.scope.knowledge_base_ids) == set(expected_kbs)
                    and set(turn.context.scope.document_ids) == set(expected_docs), 'S861_APPROVED_SCOPE_REQUIRED')
            previous_run = admitted_clients.setdefault(client_id, turn.run_id)
            require(previous_run == turn.run_id, 'S861_CLIENT_REQUEST_ALREADY_BOUND')
            if bounded_query_profile:
                require(query_input_bound(turn.request.content)['valid'], 'PROFILE_INPUT_BOUND_EXCEEDED')
        base = await original_configuration(turn)
        if approved_batch is not None:
            require(base.pipeline_state.retrieval_strategy_mode == allowed[0]['mode'],
                    'S861_SAVED_MODE_MISMATCH')
            require(base.pipeline_state.rerank_threshold == .3
                and base.pipeline_state.rerank_top_k == 30 and base.pipeline_state.embedding_top_k == 30
                and not base.pipeline_state.rerank_context_enabled
                and base.pipeline_state.rerank_top1_floorless is True
                and len(base.pipeline_state.targets) == 1, 'S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED')
            require(base.pipeline_state.query_understand_model_id == approved_batch['qu_model_id'],
                    'S861_PREFLIGHT_QU_BINDING_CHANGED')
            chat = base.generation_options['options']
            if generation_contract == 's88-answer-settings/v1':
                from scripts.s8_8_acceptance_profile import validate_approved_generation
                validate_approved_generation(turn, base, allowed[0])
                require(chat.temperature == .7, 'S861_PREFLIGHT_GENERATION_OPTIONS_CHANGED')
            else:
                require(chat.max_completion_tokens == 2048 and chat.thinking is False
                        and chat.temperature == .7, 'S861_PREFLIGHT_GENERATION_OPTIONS_CHANGED')
            from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
            require(base.generation_options.get('system_prompt', DEFAULT_SYSTEM_PROMPT) == DEFAULT_SYSTEM_PROMPT,
                    'S861_PREFLIGHT_PROMPT_CONFIGURATION_CHANGED')
            if base.fallback is not None:
                from backend.weknora_port.conversation_configuration import source_prompt_templates
                expected_fallback = next(row['content'] for row in source_prompt_templates()['fallback']
                    if row['id'] == 'default_fallback_prompt')
                require(base.fallback.get('prompt') in ('', expected_fallback)
                    and base.fallback.get('options') == chat,
                    'S861_PREFLIGHT_FALLBACK_CONFIGURATION_CHANGED')
            _budget_state_guard(base.pipeline_state, turn.request.content,
                _request_language_override(turn))
            if bounded_query_profile:
                ids = approved_batch.get('model_ids', {})
                require(base.pipeline_state.bridge_cheap_model_id == ids.get('cheap')
                    and base.pipeline_state.rerank_model_id == ids.get('rerank'),
                    'S862R_PREFLIGHT_MODEL_BINDINGS_CHANGED')
        result = compose_configuration(base)
        states[turn.run_id] = result.pipeline_state
        options = result.generation_options
        save(turn.run_id, 'configuration', 'resolved', {
            'profile': profile_id,
            'system_prompt_sha256': digest(options['system_prompt'].encode()),
            'original_system_prompt_sha256': digest(base.generation_options.get('system_prompt', '').encode()),
            'prompt_b_suffix_sha256': digest(PROMPT_B_SUFFIX.encode()),
            'citations_enabled': options['citations_enabled'],
            'options': _row(options.get('options')),
            'policy': _row(options.get('policy')),
            'models': {name: getattr(result.pipeline_state, name) for name in
                ('chat_model_id', 'query_understand_model_id', 'rerank_model_id', 'bridge_cheap_model_id')},
            'fallback_prompt_B': 'normal fallback may replace system prompt; inspect actual wire',
        })
        return result

    def observe_progress(turn, progress):
        async def observed(stage, status, **fields):
            if approved_batch is not None and stage == 'understand' and status == 'started':
                _budget_state_guard(states[turn.run_id], turn.request.content,
                    _request_language_override(turn))
            save(turn.run_id, stage, status, fields)
            if progress is not None:
                output = progress(stage, status, **fields)
                if inspect.isawaitable(output):
                    await output
        return observed

    def enforce_completed_query(turn):
        state = states[turn.run_id]
        language = profile_language_guard(state.language, turn.request.content,
            _request_language_override(turn))
        save(turn.run_id, 'profile_language', 'validated', language)
        if not bounded_query_profile:
            return
        raw = qu_outputs.get(turn.run_id)
        if raw is None or raw.get('finish_reason') not in ('stop', 'end_turn', 'completed'):
            save(turn.run_id, 'profile_input', 'S862R_INCOMPLETE_QU_OUTPUT_STOP', {
                'raw_QU_response': raw, 'resent': False})
            raise ValueError('S862R_INCOMPLETE_QU_OUTPUT_STOP')
        bound = query_input_bound(state.rewrite_query or state.query)
        if not bound['valid']:
            save(turn.run_id, 'profile_input', 'PROFILE_INPUT_BOUND_EXCEEDED', {
                'input_contract': PROFILE_QUERY_BOUND, 'observed': bound,
                'raw_QU_response': qu_outputs.get(turn.run_id),
                'semantic_failure': False, 'truncated': False, 'resent': False})
            raise ValueError('PROFILE_INPUT_BOUND_EXCEEDED')
        save(turn.run_id, 'profile_input', 'validated', {
            'input_contract': PROFILE_QUERY_BOUND, 'observed': bound})

    ordinary.configuration = configuration
    for name in ('pipeline_factory', 'generation_factory'):
        original = getattr(ordinary, name, None)
        if not callable(original):
            continue
        def wrap(factory, factory_name):
            async def composed(turn, progress, **kwargs):
                if approved_batch is not None:
                    _budget_state_guard(states[turn.run_id], turn.request.content,
                    _request_language_override(turn))
                if factory_name == 'generation_factory':
                    enforce_completed_query(turn)
                result = factory(turn, observe_progress(turn, progress), **kwargs)
                result = await result if inspect.isawaitable(result) else result
                if bounded_query_profile and factory_name == 'pipeline_factory':
                    require(callable(getattr(result, '_progress', None)) and hasattr(result, 'registry'),
                            'S862R_PROFILE_PIPELINE_SEAM_REQUIRED')
                    def observe_qu(value):
                        qu_outputs[turn.run_id] = value
                        save(turn.run_id, 'query_understanding_raw', 'returned', value)
                    def check_qu_body(model, messages, options, context):
                        if approved_batch is None:
                            return
                        expected = approved_batch.get('qu_request_sha256s', {}).get(_accepted_client_request_id(turn))
                        if expected is None:
                            raise ValueError('S862R_EXACT_QU_PREFLIGHT_REQUIRED')
                        body = model.build_request_body(messages, options)
                        actual = digest(body)
                        save(turn.run_id, 'query_understanding_wire', 'checked', {
                            'body_sha256': actual, 'wire_bytes': len(canonical(body)),
                            'expected_body_sha256': expected})
                        require(actual == expected, 'S862R_ACTUAL_QU_REQUEST_DIFFERS_FROM_PREFLIGHT')
                    result.registry = _ObservedQueryRegistry(result.registry, observe_qu, check_qu_body)
                    ordinary_progress = result._progress
                    async def checked_progress(stage, status, **fields):
                        await ordinary_progress(stage, status, **fields)
                        # RetrievalPipeline intentionally suppresses ordinary
                        # observer exceptions. This instance-level check runs
                        # OUTSIDE that suppression and before planning/Search.
                        if stage == 'understand' and status == 'completed':
                            enforce_completed_query(turn)
                    result._progress = checked_progress
                return result
            return composed
        setattr(ordinary, name, wrap(original, name))
    runtime._s861_profile_installed = True
    runtime._s861_profile_states = states
    runtime._s862r_raw_qu_outputs = qu_outputs
    return runtime


def profile_manifest(*, bounded_query_profile=False):
    from backend.weknora_port.runtime import ConversationSettings
    from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
    from backend.weknora_port.generation import RouterPolicy
    from backend.weknora_port.retrieval.pipeline import PipelineState
    defaults = PipelineState()
    original = DEFAULT_SYSTEM_PROMPT
    manifest = {'profile': PROFILE_ID, 'baseline': BASELINE, 'test_only': True,
        'question_inputs': QUESTIONS, 'modes': list(MODES),
        'session_fields': {'source_evidence_mode': 'bounded_source_v1'},
        'knowledge_base_ids': list(KB_IDS), 'document_ids': list(DOCUMENT_IDS),
        'answer_language_policy': policy_manifest(),
        'accept_language_by_case': {case: expected_profile_language(q)[0]
            for case, q in ({'compare': BOUNDED_QUESTIONS['compare']}
                if bounded_query_profile else QUESTIONS).items()},
        'reasoning_effort': 'off',
        'prompt_b_suffix': PROMPT_B_SUFFIX,
        'prompt_b_suffix_sha256': digest(PROMPT_B_SUFFIX.encode()),
        'default_original_system_sha256': digest(original.encode()),
        'default_profile_system_sha256': digest((original + '\n\n' + PROMPT_B_SUFFIX).encode()),
        'approved_budget_template_sha256': {name: digest(getattr(defaults, name).encode())
            for name in ('rewrite_system_prompt', 'rewrite_user_prompt', 'context_template')},
        'approved_fresh_context_required': True,
        'resolved_custom_system_prompt': 'preserved before same suffix; per-run actual wire hash authoritative',
        'citations_enabled': True, 'enable_expansion_both_arms': False,
        'rerank_top1_floorless': True,
        'router_policy_unchanged': asdict(RouterPolicy(mode='DYNAMIC')),
        'production_defaults_unchanged': {'strategy': ConversationSettings().retrieval_strategy_mode,
            'bridge': ConversationSettings().bridge_rescue_mode,
            'expansion': ConversationSettings().enable_query_expansion},
        'evaluations': {'primary': 'final_answer_correct',
            'separate': ['evidence_support', 'presentation_consistency', 'citation_ownership', 'new_recall_gain'],
            'no_automatic_gold_substring_score': True,
            'internal_final_answer_supported_is_not_answer_correctness': True},
        'new_actual_provider_calls': 0, 'authorization': 'NONE'}
    if bounded_query_profile:
        from backend.weknora_port.retrieval.rule_registry import RULES, render_classification_guidance
        from backend.weknora_port.retrieval import query as query_module
        manifest.update(profile=BOUNDED_PROFILE_ID, goal=BOUNDED_GOAL,
            question_inputs={'compare': BOUNDED_QUESTIONS['compare']}, modes=['query_structure_v1'],
            candidate_inputs_not_authorized={case: question for case, question in BOUNDED_QUESTIONS.items()
                if case != 'compare'}, qu_only_inputs={},
            effective_query_input_contract=dict(PROFILE_QUERY_BOUND),
            query_implementation_sha256_LF=digest(Path(query_module.__file__).read_bytes().replace(b'\r\n', b'\n')),
            qu_prompt_sha256s={name: digest(getattr(query_module, name).encode('utf8'))
                for name in ('_QU_V2_SYSTEM', '_QU_V2_USER', '_REWRITE_GUARD_V2')},
            execution_scope='one initial compare HTTP submission; other cases require a later explicit authorization',
            raw_qu_response_capture='private normal Registry delegate; no response replacement',
            input_guard_stage='understand/completed; before planning, Embedding, Search and Rerank',
            input_bound_failure='PROFILE_INPUT_BOUND_EXCEEDED',
            enabled_rules=[{'rule_id': rule.rule_id, 'rule_version': rule.rule_version,
                            'query_type': rule.query_type} for rule in RULES],
            classification_guidance_sha256=digest(render_classification_guidance().encode('utf8')))
    return manifest


def _approved(environment):
    """A profile manifest is never an Owner grant; use existing ledger bounds."""
    path = environment.get('S861_AUTHORIZATION_FILE', '')
    require(path, 'S861_FRESH_OWNER_AUTHORIZATION_REQUIRED')
    value = json.loads(Path(path).read_text(encoding='utf8'))
    bounded = environment.get('S861_BOUNDED_QUERY_PROFILE', 'false').lower() == 'true'
    require(value.get('goal') == (BOUNDED_GOAL if bounded else 'S8.6.1') and value.get('owner_approved') is True,
            'S861_FRESH_OWNER_AUTHORIZATION_REQUIRED')
    require(value.get('profile_sha256') == digest(profile_manifest(bounded_query_profile=bounded)),
            'S861_AUTH_PROFILE_MISMATCH')
    require(value.get('source_revision') == environment.get('S861_SOURCE_REVISION')
            and len(value.get('source_revision', '')) == 40, 'S861_AUTH_SOURCE_MISMATCH')
    require(value.get('image_id') == environment.get('S861_IMAGE_ID')
            and str(value.get('image_id', '')).startswith('sha256:'), 'S861_AUTH_IMAGE_MISMATCH')
    expiry = datetime.fromisoformat(value.get('expires_at', '').replace('Z', '+00:00'))
    require(expiry.tzinfo is not None and expiry > datetime.now(timezone.utc), 'S861_AUTH_EXPIRED')
    batch = environment.get('S861_BATCH_ID')
    scope = value.get('batches', {}).get(batch, {})
    if bounded:
        require(scope.get('kind') == 'http' and len(scope.get('turns', [])) == 1
            and scope['turns'][0].get('case') == 'compare', 'S862R3_INITIAL_COMPARE_ONLY')
        require(type(value.get('max_total_tokens')) is int and 0 < value['max_total_tokens'] <= 500000
            and sum(row.get('max_tokens', 500001) for row in value['batches'].values()) <= value['max_total_tokens'],
            'S862R_AGGREGATE_RESERVATION_LIMIT')
    require(type(scope.get('max_calls')) is int and 0 < scope['max_calls'] <= 12,
            'S861_AUTH_BATCH_CALL_LIMIT')
    require(type(scope.get('max_tokens')) is int and 0 < scope['max_tokens'] <= 100000,
            'S861_AUTH_BATCH_TOKEN_LIMIT')
    require(scope.get('execution_file') == environment.get('WEKNORA_PORT_EXECUTION_FILE'),
            'S861_AUTH_EXECUTION_MISMATCH')
    ledger = json.loads(Path(scope['execution_file']).read_text(encoding='utf8'))
    require(ledger.get('status') == 'OPEN' and ledger.get('entries') == [], 'S861_FRESH_EMPTY_LEDGER_REQUIRED')
    require(ledger.get('max_calls') == scope['max_calls'] and ledger.get('max_tokens') == scope['max_tokens'],
            'S861_AUTH_LEDGER_LIMIT_MISMATCH')
    require(ledger.get('capture_evidence') is True, 'S861_EXECUTION_EVIDENCE_REQUIRED')
    require(ledger.get('allowed_kbs') == list(KB_IDS), 'S861_AUTH_KB_SCOPE_MISMATCH')
    require(ledger.get('allowed_models') == scope.get('allowed_models')
            and bool(scope.get('allowed_models')), 'S861_AUTH_MODEL_SCOPE_MISMATCH')
    request_ids = [turn.get('request_id', '') for turn in scope.get('turns', [])]
    turn_limit = 6 if bounded and scope.get('kind') == 'qu_only' else 1 if bounded else 2
    require(0 < len(request_ids) <= turn_limit and len(set(request_ids)) == len(request_ids)
            and all(isinstance(v, str) and v.startswith('s861-') and len(v) <= 36 for v in request_ids)
            and ledger.get('allowed_request_prefixes') == ['*'],
            'S861_AUTH_REQUEST_SCOPE_MISMATCH')
    require(str(scope['execution_file']).startswith('/opt/rag-s8/state/executions/s861-'),
            'S861_NEW_EXECUTION_NAMESPACE_REQUIRED')
    budget_path = value.get('budget_preflight_file')
    require(isinstance(budget_path, str) and bool(budget_path), 'S861_FRESH_BUDGET_PREFLIGHT_REQUIRED')
    budget_bytes = Path(budget_path).read_bytes()
    require(digest(budget_bytes) == value.get('budget_preflight_sha256'), 'S861_BUDGET_PREFLIGHT_HASH_MISMATCH')
    budget = json.loads(budget_bytes)
    require(budget.get('schema') == (BOUNDED_BUDGET_SCHEMA if bounded else 's861-reservation-dryrun/v2')
        and budget.get('profile_sha256') == digest(profile_manifest(bounded_query_profile=bounded))
        and budget.get('implementation_sha256_LF') == digest(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
        'S861_BUDGET_PREFLIGHT_IMPLEMENTATION_MISMATCH')
    if bounded:
        require(scope.get('model_ids') == budget.get('model_ids'), 'S862R_BUDGET_MODEL_BINDINGS_MISMATCH')
        inputs = QU_ONLY_QUESTIONS if scope['kind'] == 'qu_only' else BOUNDED_QUESTIONS
        require(all(turn.get('question') == inputs.get(turn.get('case'))
                    and turn.get('mode') == 'query_structure_v1' for turn in scope.get('turns', [])),
                'S861_FROZEN_INPUT_MISMATCH')
        if scope['kind'] == 'qu_only':
            require([turn['case'] for turn in scope['turns']] == list(QU_ONLY_QUESTIONS),
                    'S862R_QU_FIXED_ORDER_REQUIRED')
        expected_requests = {turn['request_id']: budget['records'][
            ('qu-only:' if scope['kind'] == 'qu_only' else '') + turn['case'] + ':query_structure_v1:QU']['body_sha256']
            for turn in scope['turns']}
        require(scope.get('qu_request_sha256s') == expected_requests, 'S862R_EXACT_QU_PREFLIGHT_REQUIRED')
    row_key = lambda turn: ('qu-only:' + turn.get('case', '') if bounded and scope.get('kind') == 'qu_only'
        else turn.get('case', '') + ':' + turn.get('mode', ''))
    rows = [budget.get('submission_bounds', {}).get(row_key(turn), {})
            for turn in scope['turns']]
    require(all(row.get('max_calls', 0) > 0 and row.get('reservation_tokens_upper', 0) > 0 for row in rows)
        and sum(row['max_calls'] for row in rows) <= scope['max_calls']
        and sum(row['reservation_tokens_upper'] for row in rows) <= scope['max_tokens'],
        'S861_PREFLIGHT_BUDGET_EXCEEDED')
    return value


def claim_batch(execution_file, component, profile_sha256):
    # Persistent one-use marker in this NEW batch directory, never an old ledger.
    # UUID Provider run IDs remain allocated by normal HTTP admission. The
    # approved '*' ledger is narrowed by the server's exact turn/scope binding.
    require(component in ('api', 'collector', 'qu-only'), 'S861_CLAIM_COMPONENT_INVALID')
    path = Path(execution_file).parent / ('.s861-' + component + '-claim.json')
    write_new(path, {'component': component, 'profile_sha256': profile_sha256,
        'pid': os.getpid(), 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'retry': 'requires a new independent batch authorization; never delete this marker'})


def create_application(environment=None, *, assemble_factory=None):
    """Future isolated API startup through the existing private formal launcher."""
    from backend.weknora_port.development import assemble
    from scripts.s8_ecs_runtime import create_application as formal_application
    environment = dict(os.environ if environment is None else environment)
    require(environment.get('WEKNORA_PORT_REDIS_PREFIX', '').startswith('weknora-python-s8:s861:'),
            'S861_ISOLATED_REDIS_PREFIX_REQUIRED')
    require(environment.get('S8_COMPONENT', 'api') == 'api', 'S861_API_ONLY_NO_WORKER')
    require(environment.get('WEKNORA_PORT_GRAPH_ENABLED', 'false').lower() == 'false', 'S861_GRAPH_DISABLED_REQUIRED')
    require(environment.get('WEKNORA_PORT_ROUTER_MODE', 'DYNAMIC') == 'DYNAMIC', 'S861_ORIGINAL_P5_REQUIRED')
    approval = None
    bounded = environment.get('S861_BOUNDED_QUERY_PROFILE', 'false').lower() == 'true'
    if environment.get('WEKNORA_PORT_CLOUD_ALLOWED', 'false').lower() == 'true':
        approval = _approved(environment)
        batch = approval['batches'][environment['S861_BATCH_ID']]
        require(batch.get('kind', 'http') == 'http', 'S862R_HTTP_BATCH_REQUIRED')
        audit = Path(environment.get('S861_AUDIT_DIR', ''))
        require(audit.is_absolute() and audit.parent.resolve() == Path(batch['execution_file']).parent.resolve()
                and audit.name == 'strategy-audit' and not audit.exists(), 'S861_FRESH_DURABLE_AUDIT_DIR_REQUIRED')
        claim_batch(batch['execution_file'], 'api', digest(profile_manifest(bounded_query_profile=bounded)))

    @asynccontextmanager
    async def factory(settings, **kwargs):
        async with (assemble_factory or assemble)(settings, **kwargs) as runtime:
            require(not runtime.graph_enabled and runtime.conversation.bridge_rescue_mode == 'off',
                    'S861_ORIGINAL_EXPERIMENT_DEFAULTS_REQUIRED')
            install_profile(runtime, environment.get('S861_AUDIT_DIR'),
                approved_batch=approval['batches'][environment['S861_BATCH_ID']] if approval else None,
                expires_at=datetime.fromisoformat(approval['expires_at'].replace('Z', '+00:00')) if approval else None,
                bounded_query_profile=bounded)
            yield runtime

    return formal_application(environment, assemble_factory=factory)


def parse_sse(text):
    frames = []
    for block in text.replace('\r\n', '\n').split('\n\n'):
        if not block.strip():
            continue
        lines = block.splitlines()
        require('event: message' in lines, 'S861_SSE_EVENT_CONTRACT')
        payload = '\n'.join(line[6:] for line in lines if line.startswith('data: '))
        frames.append(json.loads(payload))
    return frames


def ledger_status(path):
    value = json.loads(Path(path).read_text(encoding='utf8'))
    entries = value['entries']
    from backend.weknora_port import execution
    mode = value.get('accounting_mode', execution.RESERVATION_MODE)
    require(mode in (execution.RESERVATION_MODE, execution.REPORTED_USAGE_ONLY),
            'S864_ACCOUNTING_MODE_MISMATCH')
    if mode == execution.REPORTED_USAGE_ONLY:
        settled = [row for row in entries if row['status'] == 'SETTLED'
            and execution._reported_usage_valid(row.get('usage'))]
        known = sum(row['usage']['total_tokens'] for row in entries
            if execution._reported_usage_valid(row.get('usage')))
        unsettled = len(entries) - len(settled)
        return {'status': value['status'], 'accounting_mode': mode, 'attempts': len(entries),
            'settled': len(settled), 'unsettled': unsettled,
            'unknown': sum(row['status'] == 'UNKNOWN' for row in entries),
            'invalid_settled_usage': sum(row['status'] == 'SETTLED'
                and not execution._reported_usage_valid(row.get('usage')) for row in entries),
            'known_reported_tokens': known, 'reported_tokens': known,
            'settled_reported_tokens': sum(row['usage']['total_tokens'] for row in settled),
            'actual_usage_complete': unsettled == 0, 'held_tokens': None,
            'entries': entries, 'max_calls': value['max_calls'], 'max_tokens': value['max_tokens']}
    return {'status': value['status'], 'attempts': len(entries),
        'settled': sum(row['status'] == 'SETTLED' for row in entries),
        'unsettled': sum(row['status'] != 'SETTLED' for row in entries),
        'held_tokens': sum(row['held_tokens'] for row in entries),
        'reported_tokens': sum((row.get('usage') or {}).get('total_tokens', 0)
            for row in entries if row['status'] == 'SETTLED'),
        'entries': entries, 'max_calls': value['max_calls'], 'max_tokens': value['max_tokens']}


async def dry_run_inventory(inventory_path, source_path, *, bounded_query_profile=False):
    """Latest body builders + current bindings and hash-matched originals.

    Reservation bounds are not actual model usage or expected billed Tokens.
    Envelope control characters exist only in unsent length-bound bodies.
    """
    from copy import deepcopy
    from types import SimpleNamespace
    from backend.weknora_port.model_registry import SourceCatalogue
    from backend.weknora_port.storage.configuration import ConfiguredRole
    from backend.weknora_port.models.provider import ProviderModel
    from backend.weknora_port.models.request import build_embedding_body, build_rerank_body, split_batches
    from backend.weknora_port.models.output_contract import resolve_output_contract
    from backend.weknora_port.models.types import Message, ChatOptions, CallContext, TrustedScope
    from backend.weknora_port.runtime import ConversationSettings
    from backend.weknora_port.conversation_configuration import resolved_chat_options
    from backend.weknora_port.retrieval.pipeline import PipelineState
    from backend.weknora_port.retrieval.query import understand_query
    from backend.weknora_port.retrieval.types import SearchResult, bridge_context_attributes
    from backend.weknora_port.retrieval.rerank import enriched_passage
    from backend.weknora_port.retrieval.context import into_chat_message, FinalizedContext, render_placeholders
    from backend.weknora_port.conversation_configuration import source_prompt_templates
    from backend.weknora_port.retrieval.bridge_rescue import (SYSTEM, CONTRACT_VERSION, sentence_spans,
        INPUT_CHARS, FactWindow, extraction_payload)
    from backend.weknora_port.generation import GenerationRequest, Evidence, prepare_generation
    questions = BOUNDED_QUESTIONS if bounded_query_profile else QUESTIONS
    modes = ('query_structure_v1',) if bounded_query_profile else MODES
    query_chars = PROFILE_QUERY_BOUND['unicode_characters'] if bounded_query_profile else 2048
    child_chars = 2080
    child_literal_bytes = 32
    if bounded_query_profile:
        from backend.weknora_port.retrieval.rule_registry import RULES
        literals = [template.format(object='', dimension='')
                    for rule in RULES for _, template in rule.templates]
        child_chars = query_chars + max(map(len, literals))
        child_literal_bytes = max(len(text.encode('utf8')) for text in literals)
        require(all(query_input_bound(q)['valid'] for q in (*questions.values(), *QU_ONLY_QUESTIONS.values())),
                'S862R_FROZEN_Q0_INPUT_BOUND_EXCEEDED')
    inventory = json.loads(Path(inventory_path).read_text(encoding='utf8'))
    require(inventory['status'] == 'READ_ONLY_INVENTORY' and inventory['transaction_read_only'],
            'S861_CURRENT_READONLY_INVENTORY_REQUIRED')
    require(inventory['scope'] == {'knowledge_base_ids': list(KB_IDS), 'document_ids': list(DOCUMENT_IDS)},
            'S861_INVENTORY_SCOPE_MISMATCH')
    configs = {}
    for role, row in inventory['models'].items():
        require(not row['unreviewed_parameter_keys'], 'S861_UNREVIEWED_MODEL_PARAMETERS')
        reconstruction = row['reconstruction']
        require(set(reconstruction['blocked_fields']) <= {'protocol_settings'}, 'S861_UNSAFE_MODEL_CONFIG')
        current = reconstruction['model_config']
        configs[role] = SourceCatalogue().resolve(ConfiguredRole(role, row['provider'], row['model'],
            current['base_url'], '', {}, row['source']), row['model_id'])
        expected = asdict(configs[role])
        # The read-only inventory redacts only built-in protocol metadata.
        # All other actual configuration fields must match the latest normal
        # catalogue; an old cap/encoding override invalidates this budget.
        require(set(current) == set(expected) - {'protocol_settings'}
            and all(canonical(value) == canonical(expected[key]) for key, value in current.items()),
            'S861_MODEL_CONFIGURATION_DRIFT')
    normal = ConversationSettings()
    require(normal.rerank_threshold == .3 and not normal.rerank_context_enabled, 'S861_RECOMPUTE_RERANK_BUDGET')
    require(inventory['safe_environment']['WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID'] == configs['cheap'].model_id,
            'S861_CHEAP_QU_BINDING_REQUIRED')
    require({r['embedding_physical_key'] for r in inventory['knowledge_base_bindings']} ==
            {inventory['models']['embedding']['physical_key']}, 'S861_SINGLE_PHYSICAL_EMBEDDING_REQUIRED')
    require(not set(inventory['memory']['origin_knowledge_base_ids']) <= set(KB_IDS),
            'S861_RECOMPUTE_MEMORY_BUDGET')
    raw = json.loads(Path(source_path).read_text(encoding='utf-8-sig'))
    candidates = {}
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('content'), str) and value.get('id'):
                candidates.setdefault(value['id'], []).append(value)
            for item in value.values(): walk(item)
        elif isinstance(value, list):
            for item in value: walk(item)
    walk(raw)
    documents = {row['id']: row for row in inventory['documents']}
    rows = []
    for current in inventory['current_searchable_chunk_stats']:
        matching = [value for value in candidates.get(current['id'], [])
            if digest(value['content'].encode()) == current['content_sha256']
            and value.get('knowledge_id') == current['document_id']
            and value.get('knowledge_base_id') in KB_IDS
            and (value.get('metadata') or {}).get('source_version') == current['source_version']]
        require(bool(matching), 'S861_CURRENT_ORIGINAL_READBACK_HASH_MISMATCH')
        value = deepcopy(matching[0]); doc = documents[current['document_id']]
        require(doc['source_version'] == current['source_version'], 'S861_CURRENT_DOCUMENT_VERSION_MISMATCH')
        value.update(chunk_type=current['chunk_type'], knowledge_title=doc['title'], knowledge_filename=doc['file_name'])
        row = SearchResult(**{k: v for k, v in value.items() if k in SearchResult.__dataclass_fields__})
        require(len(canonical(enriched_passage(row))) == current['enriched_passage_json_bytes'],
                'S861_CURRENT_PASSAGE_SHAPE_MISMATCH')
        rows.append(row)
    originals = [row for row in rows if row.chunk_type == 'text']
    passages = [enriched_passage(row) for row in rows]
    rerank = configs['rerank'].reranking
    require(len(split_batches(passages, max_items=rerank.max_documents,
        max_item_chars=rerank.max_document_chars, max_total_chars=rerank.max_request_chars,
        fixed_chars=child_chars)) == 1, 'S861_RECOMPUTE_RERANK_BATCHES')
    final_options = resolved_chat_options(dict(normal.summary), 'off')
    ctx = CallContext('s861-dry-run', TrustedScope('dry-run-scope-only', KB_IDS, DOCUMENT_IDS))
    records = {}
    def record(name, role, body, capability='chat', extra_bytes=0, meaning='EXACT_BODY'):
        contract = resolve_output_contract(configs[role], body, capability=capability)
        wire = len(canonical(body)) + extra_bytes
        records[name] = {'role': role, 'provider': configs[role].provider, 'model': configs[role].model,
            'model_id': configs[role].model_id, 'capability': capability,
            'wire_bytes_upper': wire, 'output_upper': contract.output_upper,
            'reserved_tokens_upper': wire + contract.output_upper + 1024,
            'body_sha256': digest(body), 'extra_wire_bytes_upper': extra_bytes,
            'meaning': meaning, 'contract': asdict(contract), 'sent': False}
        if bounded_query_profile:
            records[name]['request_body'] = body
            records[name]['wire_sha256'] = digest(canonical(body))
            records[name]['wire_bytes'] = len(canonical(body))
        return records[name]['reserved_tokens_upper']
    class QueryCapture:
        def __init__(self, case, mode, q0=None):
            self.case, self.mode, self.q0 = case, mode, q0 or questions[case]
        async def chat(self, messages, options, context):
            body = ProviderModel(configs['cheap'], None).build_request_body(messages, options)
            record(self.case + ':' + self.mode + ':QU', 'cheap', body)
            return SimpleNamespace(content=json.dumps({'rewrite_query': self.q0,
                'intent': 'kb_search', 'image_description': '', 'query_type': 'UNKNOWN'}), finish_reason='stop')
    for case, q0 in questions.items():
        for mode in modes:
            capture = QueryCapture(case, mode)
            class Registry:
                async def chat(self, *args): return capture
            state = PipelineState(query=q0, rewrite_query=q0,
                language=expected_profile_language(q0)[1],
                chat_model_id=configs['cheap'].model_id, query_understand_model_id=configs['cheap'].model_id,
                retrieval_strategy_mode=mode, source_evidence_mode='bounded_source_v1')
            await understand_query(state, ctx, Registry())
    if bounded_query_profile:
        for case, q0 in QU_ONLY_QUESTIONS.items():
            capture = QueryCapture('qu-only:' + case, 'query_structure_v1', q0)
            state = PipelineState(query=q0, rewrite_query=q0,
                language=expected_profile_language(q0)[1],
                chat_model_id=configs['cheap'].model_id, query_understand_model_id=configs['cheap'].model_id,
                retrieval_strategy_mode='query_structure_v1', source_evidence_mode='bounded_source_v1')
            await understand_query(state, ctx, Registry())
    # validate_rewrite allows <=2048 characters; a child template adds <=32.
    # JSON string encoding is at most6 bytes/Unicode character. These unsent
    # envelopes are never registered as chunks, evidence, or actual query input.
    for label, chars in (('initial', query_chars), ('compare-child', child_chars), ('Q1', 192)):
        envelope = '\x1c' * chars
        record('embedding:' + label, 'embedding', build_embedding_body(configs['embedding'], (envelope,), 'query'),
               'embedding', meaning='JSON_LENGTH_ENVELOPE_NOT_ACTUAL_QUERY')
        record('rerank:' + label, 'rerank', build_rerank_body(configs['rerank'], envelope, passages),
               'rerank', meaning='ALL_CURRENT_PASSAGES_AND_QUERY_LENGTH_ENVELOPE')
    pool = sorted((row.content for row in originals), key=lambda value: len(canonical(value)), reverse=True)[:8]
    record('old-pool', 'rerank', build_rerank_body(configs['rerank'], '\x1c' * 192, pool),
           'rerank', meaning='ALL_POSSIBLE_ORIGINAL_POOL_STRINGS_AND_Q1_ENVELOPE')
    sentence_payload = {'contract_version': CONTRACT_VERSION, 'question': '\x1c' * query_chars,
        'sentences': [{'sentence_id': 's-' + '0' * 24, 'text': row.content[a:b], 'role': 'preceding'}
            for row in originals for a, b in sentence_spans(row.content)]}
    if bounded_query_profile:
        # Obtain IDs/text through the production renderer, one original
        # sentence at a time. The union is an UNSENT length superset, not an
        # admissible2400-character extraction window or new source evidence.
        sentences = {}
        for row in originals:
            for start, end in sentence_spans(row.content):
                if end - start > INPUT_CHARS:
                    continue  # such a sentence cannot enter a real payload
                window = FactWindow(row.id, start, end, row.content[start:end], 'preceding')
                payload = extraction_payload('\x1c' * query_chars, (window,), {row.id: row})
                for item in payload['sentences']:
                    sentences[item['sentence_id']] = item
        sentence_payload['sentences'] = list(sentences.values())
    extract_body = ProviderModel(configs['cheap'], None).build_request_body(
        (Message('system', SYSTEM), Message('user', json.dumps(sentence_payload, ensure_ascii=False))),
        ChatOptions(temperature=0, thinking=False, max_tokens=256, format='json', send_temperature=True))
    record('extract', 'cheap', extract_body, meaning='ALL_ORIGINAL_SENTENCES_SUPERSET_NOT_A_SENT_PAYLOAD')
    # Every possible admitted original is included, regardless of Rerank/TopK.
    # Add a per-row upper for every constant bridge qualifier plus its JSON escapes.
    qualifier_lengths = []
    for exact in (True, False):
        for supported in (True, False):
            for attribute in ('location', 'event_venue'):
                copy = deepcopy(originals[0])
                copy.metadata['bridge_rescue'] = {'entity_bridge_verified': exact,
                    'final_answer_supported': supported, 'target_attribute': attribute}
                qualifier_lengths.append(len(canonical(bridge_context_attributes(copy))) - 2)
    qualifier_upper = len(originals) * max(qualifier_lengths)
    for case, q0 in questions.items():
        state = PipelineState(query=q0, rewrite_query='\x1c' * query_chars, merge_results=deepcopy(originals), language='English',
            source_evidence_mode='bounded_source_v1')
        finalized = await into_chat_message(state, ctx, record_bridge=False)
        from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
        prepared = prepare_generation(GenerationRequest('dry-run', 'dry-run', 'dry-run', q0=q0,
            finalized=finalized, evidence=tuple(Evidence.from_result(row) for row in originals),
            options=final_options, language='English', citations_enabled=True,
            system_prompt=DEFAULT_SYSTEM_PROMPT + '\n\n' + PROMPT_B_SUFFIX))
        for role in ('cheap', 'expensive'):
            body = ProviderModel(configs[role], None).build_request_body(prepared.messages, final_options, stream=True)
            record(case + ':final:' + role, role, body, extra_bytes=qualifier_upper,
                   meaning='ALL_ORIGINALS_PLUS_QUERY_ENVELOPE_AND_MAX_CONSTANT_BRIDGE_QUALIFIER_UPPER')
    fallback_template = next(row['content'] for row in source_prompt_templates()['fallback']
        if row['id'] == 'default_fallback_prompt')
    listing = 'Document catalog metadata only (titles and file types; contents not read):\n' + ''.join(
        '- ' + json.dumps({'title': row['title'] or row['file_name'], 'file_type': row['file_type'] or ''},
                         ensure_ascii=False) + '\n' for row in inventory['documents'])
    fallback_query = '\x1c' * query_chars
    fallback_prompt = render_placeholders(fallback_template,
        {'query': fallback_query, 'language': 'English', 'kb_documents': listing})
    for case, q0 in questions.items():
        fallback = prepare_generation(GenerationRequest('dry-run', 'dry-run', 'dry-run', q0=q0,
            finalized=FinalizedContext(fallback_query, '', (), fallback_prompt), evidence=(),
            options=final_options, language='English', citations_enabled=True, is_fallback=True,
            system_prompt=fallback_prompt))
        for role in ('cheap', 'expensive'):
            body = ProviderModel(configs[role], None).build_request_body(fallback.messages, final_options, stream=True)
            record(case + ':fallback:' + role, role, body,
                meaning='NORMAL_NO_SOURCE_FALLBACK_WITH_CURRENT_CATALOG_AND_QUERY_ENVELOPE')
    costs = lambda names: sum(records[name]['reserved_tokens_upper'] for name in names)
    bounds = {}
    for case in questions:
        for mode in modes:
            names = [case + ':' + mode + ':QU']
            if case == 'compare' and mode != 'off':
                names += ['embedding:compare-child', 'rerank:compare-child'] * 2
            else:
                names += ['embedding:initial', 'rerank:initial']
            if case == 'dependent' and mode != 'off':
                names += ['extract', 'old-pool', 'embedding:Q1', 'rerank:Q1']
            # All retrieval work can be followed by the normal no-source fallback.
            # It replaces B; budget it without changing that production behavior.
            names += [max((case + ':' + kind + ':' + role for kind in ('final', 'fallback')),
                          key=lambda name: records[name]['reserved_tokens_upper'])
                      for role in ('cheap', 'expensive')]
            bounds[case + ':' + mode] = {'max_calls': len(names), 'reservation_tokens_upper': costs(names),
                'physical_call_records': names, 'actual_usage': 'NOT_RUN', 'final_generation_reserve_included': True}
    batches = {}
    for case in questions:
        children = [bounds[case + ':' + mode] for mode in modes]
        count = sum(row['max_calls'] for row in children)
        tokens = sum(row['reservation_tokens_upper'] for row in children)
        batches[case] = {'submissions': [case + ':' + mode for mode in MODES], 'max_calls': count,
            'reservation_tokens_upper': tokens, 'fits_existing_ledger': count <= 12 and tokens <= 100000}
    candidates = {case: {'submissions': [case + ':query_structure_v1'], **bounds[case + ':query_structure_v1']}
        for case in questions}
    reduced = {}
    # Retain an off/on pair only when its complete worst path fits this ledger.
    for case in questions:
        candidates[case]['fits_existing_ledger'] = (candidates[case]['max_calls'] <= 12
            and candidates[case]['reservation_tokens_upper'] <= 100000)
        if batches[case]['fits_existing_ledger']:
            reduced[case] = batches[case]
        elif candidates[case]['fits_existing_ledger']:
            reduced[case] = candidates[case]
    report = {'schema': 's861-reservation-dryrun/v2',
        'status': 'DRY_RUN_COMPLETE_SCOPE_REDUCED' if reduced else 'BLOCKED_RESERVATION_BUDGET',
        'profile_sha256': digest(profile_manifest(bounded_query_profile=bounded_query_profile)),
        'implementation_sha256_LF': digest(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
        'inventory_sha256': digest(Path(inventory_path).read_bytes()),
        'frozen_source_sha256': digest(Path(source_path).read_bytes()),
        'originals_hash_matched_to_current_inventory': len(originals), 'all_searchable_rows_matched': len(rows),
        'physical_embedding_groups': 1, 'memory_calls': 0, 'memory_reason': 'existing origin-KB guard excludes scoped recall',
        'rerank_batches_per_nonempty_pool': 1, 'rerank_threshold': .3, 'automatic_threshold_retry_calls': 0,
        'query_expansion': False, 'records': records, 'submission_bounds': bounds, 'requested_batches': batches,
        'candidate_scopes_not_authorized': candidates,
        'fallback_included': True,
        'budget_block_reason': '' if reduced else 'Even one candidate submission exceeds the existing100000 reservation ledger cap; no Provider batch recommended',
        'requested_full_calls': sum(row['max_calls'] for row in batches.values()),
        'recommended_batches_not_authorized': reduced,
        'recommended_max_calls': sum(row['max_calls'] for row in reduced.values()),
        'recommended_reservation_upper': sum(row['reservation_tokens_upper'] for row in reduced.values()),
        'output_caps': {'QU_off': 150, 'QU_on': 256, 'extract': 256, 'final_each_role': 2048},
        'metric': 'wire_bytes + verified_output_upper +1024 per physical call; conservative reservation, never actual Token usage',
        'classification_quality': 'NOT_RUN; synthetic UNKNOWN used only to capture exact QU request',
        'new_actual_provider_calls': 0, 'actual_tokens': 0, 'authorization': 'NONE'}
    if bounded_query_profile:
        return _bounded_dryrun_report(report, configs, query_chars, child_chars, child_literal_bytes)
    return report


def _bounded_dryrun_report(report, configs, query_chars, child_chars, child_literal_bytes):
    """Small candidate tree over the existing renderer and ledger formula."""
    from backend.weknora_port.retrieval.rule_registry import RULES, render_classification_guidance
    records, bounds, batches = report['records'], {}, {}
    costs = lambda names: sum(records[name]['reserved_tokens_upper'] for name in names)
    for case in BOUNDED_QUESTIONS:
        qu = [case + ':query_structure_v1:QU']
        final = [max((case + ':' + kind + ':' + role for kind in ('final', 'fallback')),
                     key=lambda name: records[name]['reserved_tokens_upper'])
                 for role in ('cheap', 'expensive')]
        paths = {
            'plain': qu + ['embedding:initial', 'rerank:initial'] + final,
            'simple-family-max3': qu + ['embedding:compare-child', 'rerank:compare-child'] * 3 + final,
            'dependent-max1-new-search': qu + ['embedding:initial', 'rerank:initial',
                'extract', 'old-pool', 'embedding:Q1', 'rerank:Q1'] + final,
        }
        largest = max(paths, key=lambda name: costs(paths[name]))
        upper = costs(paths[largest]); count = max(map(len, paths.values()))
        bounds[case + ':query_structure_v1'] = {
            'max_calls': count, 'reservation_tokens_upper': upper,
            'physical_call_records': paths[largest], 'maximum_reservation_path': largest,
            'request_paths': {name: {'max_calls': len(names), 'reservation_tokens_upper': costs(names),
                'physical_call_records': names} for name, names in paths.items()},
            'actual_usage': 'NOT_RUN', 'final_generation_reserve_included': True,
            'query_type_is_not_injected': True,
        }
        batches[case] = {'kind': 'http', 'submissions': [case + ':query_structure_v1'],
            'max_calls': count, 'reservation_tokens_upper': upper,
            'fits_existing_ledger': count <= 12 and upper <= 100000}
    qu_names = ['qu-only:' + case + ':query_structure_v1:QU' for case in QU_ONLY_QUESTIONS]
    for case, name in zip(QU_ONLY_QUESTIONS, qu_names):
        bounds['qu-only:' + case] = {'max_calls': 1,
            'reservation_tokens_upper': costs([name]), 'physical_call_records': [name],
            'actual_usage': 'NOT_RUN', 'final_generation_reserve_included': False}
    batches['qu-only'] = {'kind': 'qu_only', 'submissions': ['qu-only:' + case for case in QU_ONLY_QUESTIONS],
        'max_calls': len(qu_names), 'reservation_tokens_upper': costs(qu_names),
        'fits_existing_ledger': len(qu_names) <= 12 and costs(qu_names) <= 100000}
    recommended = {case: row for case, row in batches.items() if row['fits_existing_ledger']}
    complete = len(recommended) == len(batches)
    report.update(schema=BOUNDED_BUDGET_SCHEMA,
        status='DRY_RUN_COMPLETE' if complete else 'BLOCKED_RESERVATION_BUDGET',
        profile=BOUNDED_PROFILE_ID, submission_bounds=bounds, requested_batches=batches,
        recommended_batches_not_authorized=recommended,
        candidate_scopes_not_authorized=batches,
        requested_full_calls=sum(row['max_calls'] for row in batches.values()),
        recommended_max_calls=sum(row['max_calls'] for row in recommended.values()),
        recommended_reservation_upper=sum(row['reservation_tokens_upper'] for row in recommended.values()),
        budget_block_reason='' if complete else 'At least one proposed isolated batch exceeds12calls or100000 reservedTokens; do not send it.',
        effective_query_input_contract=dict(PROFILE_QUERY_BOUND),
        derived_query_bounds={'initial_characters': query_chars,
            'initial_utf8_bytes': PROFILE_QUERY_BOUND['utf8_bytes'],
            'child_characters': child_chars,
            'child_utf8_bytes': PROFILE_QUERY_BOUND['utf8_bytes'] + child_literal_bytes,
            'child_derivation': 'disjoint exact object/dimension spans plus longest enabled Registry template literal',
            'Q1_characters': 192, 'Q1_utf8_bytes_upper': 192 * 4,
            'json_string_bytes_per_character_upper': 6,
            'nested_json': 'actual extraction_payload then json.dumps then ProviderModel request renderer'},
        registry_templates=[{'rule_id': rule.rule_id, 'rule_version': rule.rule_version,
            'templates': list(rule.templates)} for rule in RULES],
        classification_guidance_sha256=digest(render_classification_guidance().encode('utf8')),
        source_runtime_validation='frozen read-only inventory; revalidate same hashes,bindings and scope at current isolated startup',
        image_identity='NOT_BUILT_FOR_CURRENT_SOURCE',
        no_scope_reduction_assumes_two_objects=True,
        classification_quality='NOT_RUN; synthetic UNKNOWN captures exact normal understand_query request only')
    # R3 starts with one HTTP comparison. Other complete request-tree previews
    # remain inspectable candidates, never an implicit send authorization.
    initial = {'compare': batches['compare']}
    report.update(status='DRY_RUN_COMPLETE' if initial['compare']['fits_existing_ledger'] else 'BLOCKED_RESERVATION_BUDGET',
        budget_block_reason='' if initial['compare']['fits_existing_ledger'] else 'Initial comparison exceeds the existing12-call/100000 reservation fence.',
        requested_batches=initial,
        requested_full_calls=initial['compare']['max_calls'],
        recommended_batches_not_authorized=initial if initial['compare']['fits_existing_ledger'] else {},
        recommended_max_calls=initial['compare']['max_calls'] if initial['compare']['fits_existing_ledger'] else 0,
        recommended_reservation_upper=initial['compare']['reservation_tokens_upper']
            if initial['compare']['fits_existing_ledger'] else 0,
        candidate_scopes_not_authorized={case: row for case, row in batches.items() if case != 'compare'},
        qu_only_phase='NOT_REQUESTED_IN_R3',
        subsequent_http_cases='NOT_AUTHORIZED; require a separate explicit Owner decision')
    report['output_caps'].update(QU_on=256, extract=256, final_each_role=2048)
    report['model_ids'] = {role: config.model_id for role, config in configs.items()}
    return report


async def collect_qu_only(output, environment=None, *, settings_factory=None):
    """Six normal understand_query calls, no retrieval or final generation.

    Runtime construction, Registry, ProviderModel, SendGate and LocalExecution
    are the existing formal runtime. This is a QU diagnostic, not HTTP QA.
    """
    from scripts.s8_ecs_runtime import settings_and_factory
    from backend.weknora_port.http_api import SessionCreate
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from backend.weknora_port.retrieval.pipeline import PipelineState
    from backend.weknora_port.retrieval.query import understand_query
    from backend.weknora_port.retrieval.strategy import plan_query, plan_rule_trace
    environment = dict(os.environ if environment is None else environment)
    require(environment.get('S861_BOUNDED_QUERY_PROFILE', '').lower() == 'true'
        and environment.get('WEKNORA_PORT_CLOUD_ALLOWED', '').lower() == 'true',
        'S862R_EXPLICIT_QU_EXECUTION_REQUIRED')
    approval = _approved(environment)
    batch = approval['batches'][environment['S861_BATCH_ID']]
    require(batch.get('kind') == 'qu_only', 'S862R_QU_ONLY_BATCH_REQUIRED')
    claim_batch(batch['execution_file'], 'qu-only', digest(profile_manifest(bounded_query_profile=True)))
    target = Path(output); target.mkdir(parents=True, exist_ok=False)
    write_new(target / 'input.json', {'profile_sha256': approval['profile_sha256'],
        'source_revision': approval['source_revision'], 'image_id': approval['image_id'],
        'authorization_file_sha256': digest(Path(environment['S861_AUTHORIZATION_FILE']).read_bytes()),
        'turns': batch['turns'], 'query_type_injected': False, 'retrieval_calls': 0,
        'final_generation_calls': 0})
    settings, factory, _ = (settings_factory or settings_and_factory)(environment)
    results = []
    try:
        async with factory(settings) as runtime:
            require(runtime.cloud_allowed and not runtime.graph_enabled,
                    'S862R_FORMAL_QU_RUNTIME_REQUIRED')
            for number, spec in enumerate(batch['turns'], 1):
                require(datetime.fromisoformat(approval['expires_at'].replace('Z', '+00:00'))
                        > datetime.now(timezone.utc), 'S861_AUTH_EXPIRED')
                before = ledger_status(batch['execution_file'])
                require(before['unsettled'] == 0 and before['status'] == 'OPEN', 'S861_UNKNOWN_OR_LEDGER_STOP')
                session = await runtime.create_session(runtime.principal, SessionCreate(
                    title='S8.6.2-R QU-only ' + spec['case'], knowledge_base_scope=list(KB_IDS),
                    document_scope=list(DOCUMENT_IDS), source_evidence_mode='bounded_source_v1',
                    retrieval_strategy_mode='query_structure_v1'))
                context = CallContext(spec['request_id'], TrustedScope(runtime.principal.subject_id,
                    KB_IDS, DOCUMENT_IDS), session_id=session['id'])
                state = PipelineState(query=spec['question'], rewrite_query=spec['question'],
                    language=expected_profile_language(spec['question'],
                        spec.get('answer_language'))[1], chat_model_id=batch['model_ids']['cheap'],
                    query_understand_model_id=batch['model_ids']['cheap'],
                    retrieval_strategy_mode='query_structure_v1', source_evidence_mode='bounded_source_v1',
                    enable_expansion=False)
                _budget_state_guard(state, spec['question'], spec.get('answer_language'))
                require(query_input_bound(state.query)['valid'], 'PROFILE_INPUT_BOUND_EXCEEDED')
                raw = []
                prefix = f'{number:02d}-{spec["case"]}'
                def observe(value):
                    raw.append(value)
                    write_new(target / (prefix + '.raw-qu.json'), value)
                def before_send(model, messages, options, active_context):
                    body = model.build_request_body(messages, options)
                    actual = digest(body)
                    write_new(target / (prefix + '.actual-request.json'), {'request_id': active_context.request_id,
                        'body': body, 'body_sha256': actual, 'wire_bytes': len(canonical(body))})
                    require(actual == batch['qu_request_sha256s'][spec['request_id']],
                            'S862R_ACTUAL_QU_REQUEST_DIFFERS_FROM_PREFLIGHT')
                registry = runtime.registry_for_context()
                try:
                    await understand_query(state, context, _ObservedQueryRegistry(registry, observe, before_send))
                finally:
                    await registry.close()
                after = ledger_status(batch['execution_file'])
                write_new(target / (prefix + '.state-ledger.json'), {'state': state_snapshot(state), 'ledger': after})
                require(len(raw) == 1 and raw[0]['finish_reason'] in ('stop', 'end_turn', 'completed'),
                        'S862R_INCOMPLETE_QU_OUTPUT_STOP')
                if not query_input_bound(state.rewrite_query or state.query)['valid']:
                    write_new(target / (prefix + '.bound-failure.json'), {
                        'status': 'PROFILE_INPUT_BOUND_EXCEEDED', 'state': state_snapshot(state),
                        'raw_QU_response': raw[0], 'truncated': False, 'resent': False})
                    raise ValueError('PROFILE_INPUT_BOUND_EXCEEDED')
                require(after['attempts'] == before['attempts'] + 1 and after['unsettled'] == 0
                        and after['status'] == 'OPEN', 'S862R_QU_ATTEMPT_OR_SETTLEMENT_MISMATCH')
                plan = plan_query(state.query, state.rewrite_query or state.query, state.query_type)
                item = {'case': spec['case'], 'q0': state.query, 'effective_query': state.rewrite_query,
                    'actual_query_type': state.query_type, 'session_id': session['id'],
                    'request_id': context.request_id, 'plan': asdict(plan), 'rule_trace': plan_rule_trace(plan),
                    'query_trace': state.query_trace, 'ledger': after, 'raw_QU_response': raw[0],
                    'retrieval_calls': 0, 'final_generation_calls': 0,
                    'classification_correct': 'NOT_REVIEWED', 'semantic_failure_inferred': False}
                results.append(item); write_new(target / (prefix + '.result.json'), item)
    except BaseException as error:
        write_new(target / 'failure.json', {'status': 'STOPPED', 'error_type': type(error).__name__,
            'error_code': str(error) if isinstance(error, ValueError) else 'S862R_QU_EXECUTION_STOP',
            'completed_cases': len(results), 'ledger': ledger_status(batch['execution_file']), 'retry': False})
        raise
    write_new(target / 'results.json', {'status': 'QU_COMPLETED_REVIEW_REQUIRED', 'results': results,
        'retrieval_calls': 0, 'final_generation_calls': 0, 'classification_quality': 'REQUIRES_REVIEW'})
    return results


async def collect_http(base_url, batch_id, output, environment=None):
    """Run only frozen approved turns via HTTP; never resubmit on failure."""
    import httpx
    from scripts.s8_ecs_runtime import api_token
    environment = dict(os.environ if environment is None else environment)
    approval = _approved(environment)
    bounded = environment.get('S861_BOUNDED_QUERY_PROFILE', 'false').lower() == 'true'
    require(batch_id == environment.get('S861_BATCH_ID'), 'S861_BATCH_MISMATCH')
    batch = approval['batches'][batch_id]
    require(batch.get('kind', 'http') == 'http', 'S862R_HTTP_BATCH_REQUIRED')
    turns = batch.get('turns', [])
    require(0 < len(turns) <= 2, 'S861_FROZEN_BATCH_REQUIRED')
    for turn in turns:
        inputs = BOUNDED_QUESTIONS if bounded else QUESTIONS
        require(turn.get('question') == inputs.get(turn.get('case'))
                and turn.get('mode') in (('query_structure_v1',) if bounded else MODES), 'S861_FROZEN_INPUT_MISMATCH')
    claim_batch(batch['execution_file'], 'collector', digest(profile_manifest(bounded_query_profile=bounded)))
    parsed = urlsplit(base_url)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
        and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
        and not parsed.username and not parsed.password, 'S861_LOOPBACK_API_REQUIRED')
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    write_new(target / 'input.json', {'batch': batch_id, 'turns': turns,
        'profile_sha256': digest(profile_manifest(bounded_query_profile=bounded)), 'authorization_file_sha256':
        digest(Path(environment['S861_AUTHORIZATION_FILE']).read_bytes())})
    headers = {'Authorization': 'Bearer ' + api_token(environment),
        'Origin': 'http://127.0.0.1:5173'}
    results = []
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), headers=headers,
                                 timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        for number, spec in enumerate(turns, 1):
            prefix = f'{number:02d}-{spec["case"]}-{spec["mode"]}'
            require(ledger_status(batch['execution_file'])['unsettled'] == 0,
                    'S861_UNSETTLED_BEFORE_NEXT_TURN')
            session_body = {'title': 'S8.6.1 ' + prefix, 'knowledge_base_scope': list(KB_IDS),
                'document_scope': list(DOCUMENT_IDS), 'source_evidence_mode': 'bounded_source_v1',
                'retrieval_strategy_mode': spec['mode']}
            response = await client.post('/api/v1/sessions', json=session_body)
            write_new(target / (prefix + '.session.json'), {'status': response.status_code, 'body': response.json()})
            require(response.status_code == 200, 'S861_SESSION_CREATE_FAILED')
            sid = response.json()['data']['id']
            request_id = spec.get('request_id', '')
            require(isinstance(request_id, str) and request_id.startswith('s861-'), 'S861_FRESH_REQUEST_REQUIRED')
            body = {'content': spec['question'], 'request_id': request_id, 'mode': 'quick',
                'knowledge_base_scope': list(KB_IDS), 'document_scope': list(DOCUMENT_IDS),
                'reasoning_effort': 'off'}
            began, first_answer = time.monotonic(), None
            blocks = []
            # Preserve response bytes incrementally even on timeout/disconnect.
            with (target / (prefix + '.sse')).open('xb') as raw:
                request_headers = profile_request_headers(spec['question'], headers,
                    spec.get('answer_language'))
                async with client.stream('POST', f'/api/v1/sessions/{sid}/messages', json=body,
                                         headers=request_headers) as response:
                    status_code = response.status_code
                    async for chunk in response.aiter_bytes():
                        raw.write(chunk); raw.flush(); blocks.append(chunk)
                        if first_answer is None:
                            text = b''.join(blocks).decode('utf8', errors='replace')
                            if '\n\n' in text:
                                complete_blocks = text.rsplit('\n\n', 1)[0] + '\n\n'
                                if any(item.get('response_type') == 'answer' and item.get('content')
                                       for item in parse_sse(complete_blocks)):
                                    first_answer = time.monotonic() - began
            stream_ended = time.monotonic()
            frames = parse_sse(b''.join(blocks).decode('utf8'))
            ledger = ledger_status(batch['execution_file'])
            write_new(target / (prefix + '.frames.json'), frames)
            write_new(target / (prefix + '.ledger.json'), ledger)
            require(status_code == 200 and frames and frames[-1].get('response_type') == 'complete',
                    'S861_MISSING_HTTP_SSE_COMPLETION')
            terminal = frames[-1].get('data', {})
            require(not any(f.get('response_type') == 'error' for f in frames)
                and terminal.get('status') not in ('failed', 'cancelled'), 'S861_FAILED_TURN_STOP')
            require(completed_answer_contract(frames), 'S861_INCOMPLETE_OUTPUT_STOP')
            require(ledger['status'] == 'OPEN' and ledger['unsettled'] == 0,
                    'S861_UNKNOWN_OR_LEDGER_STOP')
            history = await client.get(f'/api/v1/sessions/{sid}/messages')
            write_new(target / (prefix + '.history.json'), {'status': history.status_code, 'body': history.json()})
            require(history.status_code == 200, 'S861_HISTORY_READ_FAILED')
            assistants = [row for row in history.json()['data'] if row['role'] == 'assistant']
            require(len(assistants) == 1 and assistants[0].get('is_completed') is True
                and assistants[0].get('turn_status') == 'completed', 'S861_HISTORY_STATUS_MISMATCH')
            message_id = assistants[0]['id']
            queries = [frame for frame in frames if frame.get('response_type') == 'agent_query']
            require(len(queries) == 1, 'S861_RUN_IDENTITY_EVENT_MISSING')
            run_id = frames[0]['id']
            require(all(frame['id'] == run_id for frame in frames), 'S861_SSE_RUN_IDENTITY_MISMATCH')
            binding = queries[0].get('data', {})
            require(binding.get('request_id') == run_id and binding.get('query') == spec['question']
                and binding.get('session_id') == sid and binding.get('assistant_message_id') == message_id
                and assistants[0].get('request_id') == run_id, 'S861_HISTORY_RUN_BINDING_MISMATCH')
            users = [row for row in history.json()['data'] if row['role'] == 'user']
            require(len(users) == 1 and users[0].get('content') == spec['question'], 'S861_HISTORY_Q0_MISMATCH')
            replay = await client.get(f'/api/v1/sessions/continue-stream/{sid}', params={'message_id': message_id})
            replay_frames = parse_sse(replay.text) if replay.status_code == 200 else []
            write_new(target / (prefix + '.replay.json'), {'status': replay.status_code,
                'frames': replay_frames})
            after_replay = ledger_status(batch['execution_file'])
            require(replay.status_code == 200 and replay_frames and after_replay == ledger
                and all(frame['id'] == run_id for frame in replay_frames)
                and replay_frames[-1].get('response_type') == 'complete'
                and replay_frames[-1].get('data') == frames[-1].get('data'), 'S861_REPLAY_NEW_SEND_OR_STATE_CHANGE')
            require([f.get('data') for f in replay_frames if f.get('response_type') == 'agent_query']
                    == [binding], 'S861_REPLAY_RUN_BINDING_MISMATCH')
            def answer_text(items):
                return ''.join(item.get('content', '') for item in items if item.get('response_type') == 'answer')
            require(answer_text(replay_frames) == answer_text(frames) == assistants[0].get('content'),
                    'S861_REPLAY_ANSWER_HISTORY_MISMATCH')
            require([f.get('knowledge_references') for f in replay_frames if f.get('response_type') == 'references']
                == [f.get('knowledge_references') for f in frames if f.get('response_type') == 'references'],
                    'S861_REPLAY_REFERENCE_MISMATCH')
            reference_events = [f.get('knowledge_references') for f in frames if f.get('response_type') == 'references']
            persisted_references = assistants[0].get('knowledge_references') or []
            require((reference_events[-1] or []) == persisted_references if reference_events
                    else not persisted_references,
                    'S861_PERSISTED_REFERENCES_MISMATCH')
            sources = []
            for reference in assistants[0].get('knowledge_references') or []:
                identifier = reference.get('chunk_id') or reference.get('id')
                require(isinstance(identifier, str) and bool(identifier), 'S861_CITATION_ID_MISSING')
                source = await client.get('/api/v1/chunks/' + identifier)
                sources.append({'chunk_id': identifier, 'status': source.status_code, 'body': source.json()})
                require(source.status_code == 200, 'S861_CITATION_READBACK_FAILED')
            write_new(target / (prefix + '.sources.json'), sources)
            results.append({'case': spec['case'], 'mode': spec['mode'], 'request_id': request_id,
                'session_id': sid, 'assistant_message_id': message_id,
                'first_answer_seconds': first_answer, 'whole_answer_seconds': stream_ended - began,
                'verification_seconds': time.monotonic() - stream_ended, 'server_run_id': run_id,
                'final_answer_correct': 'NOT_REVIEWED', 'evidence_support': 'NOT_REVIEWED',
                'presentation_consistency': 'NOT_REVIEWED',
                'citation_ownership': 'READBACK_ONLY' if sources else 'NO_CITATIONS',
                'ledger': ledger, 'profile_actual_wire_B': 'REQUIRES_RECEIPT_REVIEW',
                'PG_provider_receipt_reconciliation': 'REQUIRES_READ_ONLY_PG_REVIEW'})
    write_new(target / 'results.json', {'status': 'HTTP_COMPLETED_REVIEW_REQUIRED', 'results': results,
        'second_authenticated_principal': 'BLOCKED_UNAVAILABLE', 'performance_inference': 'NONE'})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('profile')
    plan.add_argument('--output')
    plan.add_argument('--bounded-query-profile', action='store_true')
    api = commands.add_parser('api')
    api.add_argument('--port', type=int, default=8000)
    collect = commands.add_parser('collect')
    collect.add_argument('--base-url', required=True)
    collect.add_argument('--batch-id', required=True)
    collect.add_argument('--output', required=True)
    dry = commands.add_parser('dry-run')
    dry.add_argument('--inventory', required=True)
    dry.add_argument('--sources', required=True)
    dry.add_argument('--output', required=True)
    dry.add_argument('--bounded-query-profile', action='store_true')
    qu = commands.add_parser('qu-only')
    qu.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.command == 'profile':
        value = profile_manifest(bounded_query_profile=args.bounded_query_profile)
        if args.output:
            write_new(args.output, value)
        print(json.dumps({'profile': value['profile'], 'sha256': digest(value),
            'prompt_b_suffix_sha256': value['prompt_b_suffix_sha256'], 'new_actual_provider_calls': 0}))
        return 0
    if args.command == 'collect':
        import asyncio
        asyncio.run(collect_http(args.base_url, args.batch_id, args.output))
        return 0
    if args.command == 'dry-run':
        import asyncio
        value = asyncio.run(dry_run_inventory(args.inventory, args.sources,
            bounded_query_profile=args.bounded_query_profile))
        write_new(args.output, value)
        print(json.dumps({key: value[key] for key in ('status', 'requested_full_calls',
            'recommended_max_calls', 'recommended_reservation_upper', 'new_actual_provider_calls')}))
        return 0
    if args.command == 'qu-only':
        import asyncio
        asyncio.run(collect_qu_only(args.output))
        return 0
    import uvicorn
    uvicorn.run(create_application(), host='0.0.0.0', port=args.port,
                workers=1, log_level='warning', access_log=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
