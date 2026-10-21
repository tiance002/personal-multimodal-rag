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
import time
from urllib.parse import urlsplit
import uuid

PROMPT_B_SUFFIX = (
    'When the supplied evidence contains sufficient information, perform the necessary verifiable calculation or logical inference instead of refusing solely because the result is not stated verbatim.\n'
    'Distinguish derived conclusions from directly stated facts. Do not invent missing inputs, entities, dates, locations, or relationships. If the evidence does not support a required relation, explicitly state the uncertainty.'
)
PROFILE_ID = 's8.6.1-formal-api-prompt-b/v1'
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
MODES = ('off', 'query_structure_v1')


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


def _budget_state_guard(state):
    from backend.weknora_port.retrieval.pipeline import PipelineState
    defaults = PipelineState()
    require(all(getattr(state, name) == getattr(defaults, name) for name in
        ('rewrite_system_prompt', 'rewrite_user_prompt', 'context_template'))
        and state.language == 'English'
        and not any(getattr(state, name) for name in
            ('attachment_prompt', 'images', 'quoted_context', 'system_prompt_override',
             'intent_system_prompts', 'global_intent_system_prompts')),
        'S861_PREFLIGHT_PROMPT_CONFIGURATION_CHANGED')
    require(not state.history and not state.memory_background, 'S861_PREFLIGHT_FRESH_CONTEXT_REQUIRED')


def state_snapshot(state):
    """Private, finite trace of the actual states, never a public SSE payload."""
    value = {name: getattr(state, name, None) for name in (
        'query', 'rewrite_query', 'query_type', 'intent', 'needs_retrieval',
        'retrieval_strategy_mode', 'source_evidence_mode', 'enable_expansion',
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


def install_profile(runtime, evidence_directory=None, *, approved_batch=None, expires_at=None):
    """Compose existing server dependencies; no class/Adapter monkeypatch."""
    require(not getattr(runtime, '_s861_profile_installed', False), 'S861_PROFILE_ALREADY_INSTALLED')
    ordinary = runtime.ordinary
    original_configuration = ordinary.configuration
    states, sequence, admitted_clients = {}, [0], {}
    directory = Path(evidence_directory) if evidence_directory is not None else None

    def save(run_id, stage, status, fields=None):
        if directory is None or run_id not in states:
            return
        sequence[0] += 1
        name = digest(run_id.encode()) + f'.{sequence[0]:05d}.profile.json'
        write_new(directory / name, {'profile': PROFILE_ID, 'run_id': run_id,
            'stage': stage, 'status': status, 'fields': fields or {},
            'state': state_snapshot(states[run_id]),
            'proof_boundary': 'actual server state; Provider send/completion proven separately by existing receipts'})

    async def configuration(turn):
        if approved_batch is not None:
            require(expires_at is not None and expires_at > datetime.now(timezone.utc), 'S861_AUTH_EXPIRED')
            client_id = turn.request.request_id
            allowed = [row for row in approved_batch['turns'] if row['request_id'] == client_id]
            require(len(allowed) == 1 and allowed[0]['question'] == turn.request.content,
                    'S861_APPROVED_TURN_REQUIRED')
            require(set(turn.context.scope.knowledge_base_ids) == set(KB_IDS)
                    and set(turn.context.scope.document_ids) == set(DOCUMENT_IDS), 'S861_APPROVED_SCOPE_REQUIRED')
            previous_run = admitted_clients.setdefault(client_id, turn.run_id)
            require(previous_run == turn.run_id, 'S861_CLIENT_REQUEST_ALREADY_BOUND')
        base = await original_configuration(turn)
        if approved_batch is not None:
            require(base.pipeline_state.retrieval_strategy_mode == allowed[0]['mode'],
                    'S861_SAVED_MODE_MISMATCH')
            require(base.pipeline_state.rerank_threshold == .3
                and base.pipeline_state.rerank_top_k == 30 and base.pipeline_state.embedding_top_k == 30
                and not base.pipeline_state.rerank_context_enabled
                and len(base.pipeline_state.targets) == 1, 'S861_PREFLIGHT_RETRIEVAL_CONFIGURATION_CHANGED')
            require(base.pipeline_state.query_understand_model_id == approved_batch['qu_model_id'],
                    'S861_PREFLIGHT_QU_BINDING_CHANGED')
            chat = base.generation_options['options']
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
            _budget_state_guard(base.pipeline_state)
        result = compose_configuration(base)
        states[turn.run_id] = result.pipeline_state
        options = result.generation_options
        save(turn.run_id, 'configuration', 'resolved', {
            'profile': PROFILE_ID,
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
                _budget_state_guard(states[turn.run_id])
            save(turn.run_id, stage, status, fields)
            if progress is not None:
                output = progress(stage, status, **fields)
                if inspect.isawaitable(output):
                    await output
        return observed

    ordinary.configuration = configuration
    for name in ('pipeline_factory', 'generation_factory'):
        original = getattr(ordinary, name, None)
        if not callable(original):
            continue
        def wrap(factory):
            async def composed(turn, progress, **kwargs):
                if approved_batch is not None:
                    _budget_state_guard(states[turn.run_id])
                result = factory(turn, observe_progress(turn, progress), **kwargs)
                return await result if inspect.isawaitable(result) else result
            return composed
        setattr(ordinary, name, wrap(original))
    runtime._s861_profile_installed = True
    runtime._s861_profile_states = states
    return runtime


def profile_manifest():
    from backend.weknora_port.runtime import ConversationSettings
    from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
    from backend.weknora_port.generation import RouterPolicy
    from backend.weknora_port.retrieval.pipeline import PipelineState
    defaults = PipelineState()
    original = DEFAULT_SYSTEM_PROMPT
    return {'profile': PROFILE_ID, 'baseline': BASELINE, 'test_only': True,
        'question_inputs': QUESTIONS, 'modes': list(MODES),
        'session_fields': {'source_evidence_mode': 'bounded_source_v1'},
        'knowledge_base_ids': list(KB_IDS), 'document_ids': list(DOCUMENT_IDS),
        'accept_language': 'en-US', 'reasoning_effort': 'off',
        'prompt_b_suffix': PROMPT_B_SUFFIX,
        'prompt_b_suffix_sha256': digest(PROMPT_B_SUFFIX.encode()),
        'default_original_system_sha256': digest(original.encode()),
        'default_profile_system_sha256': digest((original + '\n\n' + PROMPT_B_SUFFIX).encode()),
        'approved_budget_template_sha256': {name: digest(getattr(defaults, name).encode())
            for name in ('rewrite_system_prompt', 'rewrite_user_prompt', 'context_template')},
        'approved_fresh_context_required': True,
        'resolved_custom_system_prompt': 'preserved before same suffix; per-run actual wire hash authoritative',
        'citations_enabled': True, 'enable_expansion_both_arms': False,
        'router_policy_unchanged': asdict(RouterPolicy(mode='DYNAMIC')),
        'production_defaults_unchanged': {'strategy': ConversationSettings().retrieval_strategy_mode,
            'bridge': ConversationSettings().bridge_rescue_mode,
            'expansion': ConversationSettings().enable_query_expansion},
        'evaluations': {'primary': 'final_answer_correct',
            'separate': ['evidence_support', 'presentation_consistency', 'citation_ownership', 'new_recall_gain'],
            'no_automatic_gold_substring_score': True,
            'internal_final_answer_supported_is_not_answer_correctness': True},
        'new_actual_provider_calls': 0, 'authorization': 'NONE'}


def _approved(environment):
    """A profile manifest is never an Owner grant; use existing ledger bounds."""
    path = environment.get('S861_AUTHORIZATION_FILE', '')
    require(path, 'S861_FRESH_OWNER_AUTHORIZATION_REQUIRED')
    value = json.loads(Path(path).read_text(encoding='utf8'))
    require(value.get('goal') == 'S8.6.1' and value.get('owner_approved') is True,
            'S861_FRESH_OWNER_AUTHORIZATION_REQUIRED')
    require(value.get('profile_sha256') == digest(profile_manifest()), 'S861_AUTH_PROFILE_MISMATCH')
    require(value.get('source_revision') == environment.get('S861_SOURCE_REVISION')
            and len(value.get('source_revision', '')) == 40, 'S861_AUTH_SOURCE_MISMATCH')
    require(value.get('image_id') == environment.get('S861_IMAGE_ID')
            and str(value.get('image_id', '')).startswith('sha256:'), 'S861_AUTH_IMAGE_MISMATCH')
    expiry = datetime.fromisoformat(value.get('expires_at', '').replace('Z', '+00:00'))
    require(expiry.tzinfo is not None and expiry > datetime.now(timezone.utc), 'S861_AUTH_EXPIRED')
    batch = environment.get('S861_BATCH_ID')
    scope = value.get('batches', {}).get(batch, {})
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
    require(0 < len(request_ids) <= 2 and len(set(request_ids)) == len(request_ids)
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
    require(budget.get('schema') == 's861-reservation-dryrun/v2'
        and budget.get('profile_sha256') == digest(profile_manifest())
        and budget.get('implementation_sha256_LF') == digest(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
        'S861_BUDGET_PREFLIGHT_IMPLEMENTATION_MISMATCH')
    rows = [budget.get('submission_bounds', {}).get(turn.get('case', '') + ':' + turn.get('mode', ''), {})
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
    require(component in ('api', 'collector'), 'S861_CLAIM_COMPONENT_INVALID')
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
    if environment.get('WEKNORA_PORT_CLOUD_ALLOWED', 'false').lower() == 'true':
        approval = _approved(environment)
        batch = approval['batches'][environment['S861_BATCH_ID']]
        audit = Path(environment.get('S861_AUDIT_DIR', ''))
        require(audit.is_absolute() and audit.parent.resolve() == Path(batch['execution_file']).parent.resolve()
                and audit.name == 'strategy-audit' and not audit.exists(), 'S861_FRESH_DURABLE_AUDIT_DIR_REQUIRED')
        claim_batch(batch['execution_file'], 'api', digest(profile_manifest()))

    @asynccontextmanager
    async def factory(settings, **kwargs):
        async with (assemble_factory or assemble)(settings, **kwargs) as runtime:
            require(not runtime.graph_enabled and runtime.conversation.bridge_rescue_mode == 'off',
                    'S861_ORIGINAL_EXPERIMENT_DEFAULTS_REQUIRED')
            install_profile(runtime, environment.get('S861_AUDIT_DIR'),
                approved_batch=approval['batches'][environment['S861_BATCH_ID']] if approval else None,
                expires_at=datetime.fromisoformat(approval['expires_at'].replace('Z', '+00:00')) if approval else None)
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
    return {'status': value['status'], 'attempts': len(entries),
        'settled': sum(row['status'] == 'SETTLED' for row in entries),
        'unsettled': sum(row['status'] != 'SETTLED' for row in entries),
        'held_tokens': sum(row['held_tokens'] for row in entries),
        'reported_tokens': sum((row.get('usage') or {}).get('total_tokens', 0)
            for row in entries if row['status'] == 'SETTLED'),
        'entries': entries, 'max_calls': value['max_calls'], 'max_tokens': value['max_tokens']}


async def dry_run_inventory(inventory_path, source_path):
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
    from backend.weknora_port.retrieval.bridge_rescue import SYSTEM, CONTRACT_VERSION, sentence_spans
    from backend.weknora_port.generation import GenerationRequest, Evidence, prepare_generation
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
        fixed_chars=2080)) == 1, 'S861_RECOMPUTE_RERANK_BATCHES')
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
        return records[name]['reserved_tokens_upper']
    class QueryCapture:
        def __init__(self, case, mode): self.case, self.mode = case, mode
        async def chat(self, messages, options, context):
            body = ProviderModel(configs['cheap'], None).build_request_body(messages, options)
            record(self.case + ':' + self.mode + ':QU', 'cheap', body)
            return SimpleNamespace(content=json.dumps({'rewrite_query': QUESTIONS[self.case],
                'intent': 'kb_search', 'image_description': '', 'query_type': 'UNKNOWN'}), finish_reason='stop')
    for case, q0 in QUESTIONS.items():
        for mode in MODES:
            capture = QueryCapture(case, mode)
            class Registry:
                async def chat(self, *args): return capture
            state = PipelineState(query=q0, rewrite_query=q0, language='English',
                chat_model_id=configs['cheap'].model_id, query_understand_model_id=configs['cheap'].model_id,
                retrieval_strategy_mode=mode, source_evidence_mode='bounded_source_v1')
            await understand_query(state, ctx, Registry())
    # validate_rewrite allows <=2048 characters; a child template adds <=32.
    # JSON string encoding is at most6 bytes/Unicode character. These unsent
    # envelopes are never registered as chunks, evidence, or actual query input.
    for label, chars in (('initial', 2048), ('compare-child', 2080), ('Q1', 192)):
        envelope = '\x1c' * chars
        record('embedding:' + label, 'embedding', build_embedding_body(configs['embedding'], (envelope,), 'query'),
               'embedding', meaning='JSON_LENGTH_ENVELOPE_NOT_ACTUAL_QUERY')
        record('rerank:' + label, 'rerank', build_rerank_body(configs['rerank'], envelope, passages),
               'rerank', meaning='ALL_CURRENT_PASSAGES_AND_QUERY_LENGTH_ENVELOPE')
    pool = sorted((row.content for row in originals), key=lambda value: len(canonical(value)), reverse=True)[:8]
    record('old-pool', 'rerank', build_rerank_body(configs['rerank'], '\x1c' * 192, pool),
           'rerank', meaning='ALL_POSSIBLE_ORIGINAL_POOL_STRINGS_AND_Q1_ENVELOPE')
    sentence_payload = {'contract_version': CONTRACT_VERSION, 'question': '\x1c' * 2048,
        'sentences': [{'sentence_id': 's-' + '0' * 24, 'text': row.content[a:b], 'role': 'preceding'}
            for row in originals for a, b in sentence_spans(row.content)]}
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
    for case, q0 in QUESTIONS.items():
        state = PipelineState(query=q0, rewrite_query='\x1c' * 2048, merge_results=deepcopy(originals), language='English',
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
    fallback_query = '\x1c' * 2048
    fallback_prompt = render_placeholders(fallback_template,
        {'query': fallback_query, 'language': 'English', 'kb_documents': listing})
    for case, q0 in QUESTIONS.items():
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
    for case in QUESTIONS:
        for mode in MODES:
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
    for case in QUESTIONS:
        children = [bounds[case + ':' + mode] for mode in MODES]
        count = sum(row['max_calls'] for row in children)
        tokens = sum(row['reservation_tokens_upper'] for row in children)
        batches[case] = {'submissions': [case + ':' + mode for mode in MODES], 'max_calls': count,
            'reservation_tokens_upper': tokens, 'fits_existing_ledger': count <= 12 and tokens <= 100000}
    candidates = {case: {'submissions': [case + ':' + MODES[1]], **bounds[case + ':' + MODES[1]]}
        for case in QUESTIONS}
    reduced = {}
    # Retain an off/on pair only when its complete worst path fits this ledger.
    for case in QUESTIONS:
        candidates[case]['fits_existing_ledger'] = (candidates[case]['max_calls'] <= 12
            and candidates[case]['reservation_tokens_upper'] <= 100000)
        if batches[case]['fits_existing_ledger']:
            reduced[case] = batches[case]
        elif candidates[case]['fits_existing_ledger']:
            reduced[case] = candidates[case]
    return {'schema': 's861-reservation-dryrun/v2',
        'status': 'DRY_RUN_COMPLETE_SCOPE_REDUCED' if reduced else 'BLOCKED_RESERVATION_BUDGET',
        'profile_sha256': digest(profile_manifest()),
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


async def collect_http(base_url, batch_id, output, environment=None):
    """Run only frozen approved turns via HTTP; never resubmit on failure."""
    import httpx
    from scripts.s8_ecs_runtime import api_token
    environment = dict(os.environ if environment is None else environment)
    approval = _approved(environment)
    require(batch_id == environment.get('S861_BATCH_ID'), 'S861_BATCH_MISMATCH')
    batch = approval['batches'][batch_id]
    turns = batch.get('turns', [])
    require(0 < len(turns) <= 2, 'S861_FROZEN_BATCH_REQUIRED')
    for turn in turns:
        require(turn.get('question') == QUESTIONS.get(turn.get('case'))
                and turn.get('mode') in MODES, 'S861_FROZEN_INPUT_MISMATCH')
    claim_batch(batch['execution_file'], 'collector', digest(profile_manifest()))
    parsed = urlsplit(base_url)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
        and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
        and not parsed.username and not parsed.password, 'S861_LOOPBACK_API_REQUIRED')
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    write_new(target / 'input.json', {'batch': batch_id, 'turns': turns,
        'profile_sha256': digest(profile_manifest()), 'authorization_file_sha256':
        digest(Path(environment['S861_AUTHORIZATION_FILE']).read_bytes())})
    headers = {'Authorization': 'Bearer ' + api_token(environment),
        'Origin': 'http://127.0.0.1:5173', 'Accept-Language': 'en-US'}
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
                async with client.stream('POST', f'/api/v1/sessions/{sid}/messages', json=body) as response:
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
            require(terminal.get('finish_reason') in ('stop', 'end_turn', 'completed')
                and terminal.get('truncated') is False, 'S861_INCOMPLETE_OUTPUT_STOP')
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
    args = parser.parse_args(argv)
    if args.command == 'profile':
        value = profile_manifest()
        if args.output:
            write_new(args.output, value)
        print(json.dumps({'profile': PROFILE_ID, 'sha256': digest(value),
            'prompt_b_suffix_sha256': value['prompt_b_suffix_sha256'], 'new_actual_provider_calls': 0}))
        return 0
    if args.command == 'collect':
        import asyncio
        asyncio.run(collect_http(args.base_url, args.batch_id, args.output))
        return 0
    if args.command == 'dry-run':
        import asyncio
        value = asyncio.run(dry_run_inventory(args.inventory, args.sources))
        write_new(args.output, value)
        print(json.dumps({key: value[key] for key in ('status', 'requested_full_calls',
            'recommended_max_calls', 'recommended_reservation_upper', 'new_actual_provider_calls')}))
        return 0
    import uvicorn
    uvicorn.run(create_application(), host='0.0.0.0', port=args.port,
                workers=1, log_level='warning', access_log=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
