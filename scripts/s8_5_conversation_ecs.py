"""One-use, seven-turn S8.5 ordinary-service diagnostic over public sources.

Import is inert. ``prepare`` is read-only and cloud-disabled; ``run`` requires a
fresh exact continuation grant, creates two isolated Sessions in existing PG
tables, and uses the existing Registry, durable gate, lifecycle and receipts.
No model output is repaired and no failed or completed operation is resent.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
import uuid

from scripts import s8_4_bridge_ecs as historical
from scripts import s8_5_bridge_ecs as bridge

require, canonical, sha = historical.require, historical.canonical, historical.sha
read, write_new, safe_write = historical.read, historical.write_new, historical.safe_write
FROZEN_SHA256 = bridge.FROZEN_SHA256
ROOT_PREFIX = '/opt/rag-s8/state/s8-5-conversation-'
EXECUTION_ROOT = historical.EXECUTION_ROOT
SESSION_ID, SESSION_ENVELOPE = bridge.SESSION_ID, deepcopy(bridge.SESSION_ENVELOPE)
KB_ID = '96a9eebd-ffb2-4bc6-9bea-3ed015845479'
DOCUMENT_IDS = (
    'a973df4a-0b4b-43f7-ad17-55265aac1556',  # Jeanne Marie Beaumont
    '9d440f3c-021d-4c9b-adc5-6e7084fc8886',  # Leyden jar
    '4846280b-37b3-4fbf-97c9-e4d798b9009c',  # Franklin bells
    '3fdf4942-1265-4e9e-959d-27772f57e5dd',  # The Frost Place
)
MODELS = {'cheap': 'Qwen/Qwen3.5-4B', 'embedding': 'BAAI/bge-m3',
          'rerank': 'BAAI/bge-reranker-v2-m3'}
TURNS = (
    {'case_id': 'independent', 'q0': 'Who is Jeanne Marie Beaumont?', 'needs_retrieval': True},
    {'case_id': 'pronoun-2', 'q0': 'What did she direct from 2007–2010?', 'needs_retrieval': True},
    {'case_id': 'pronoun-3', 'q0': 'Which poetry collections has she written?', 'needs_retrieval': True},
    {'case_id': 'topic-switch', 'q0': 'Compare a Leyden jar and Franklin bells.', 'needs_retrieval': True},
    {'case_id': 'ambiguous', 'q0': 'What is it used for?', 'needs_retrieval': True},
    {'case_id': 'format', 'q0': 'Format the previous answer as a table.', 'needs_retrieval': False},
    {'case_id': 'source-followup', 'q0': 'Which original document describes the purpose of a Leyden jar? Provide evidence.',
     'needs_retrieval': True},
)
TURN_CALLS, TURN_TOKENS, GENERATION_OUTPUT = 4, 20000, 1024
ENVELOPE = {'max_turns': 7, 'max_calls': 28, 'max_tokens': 140000,
    'per_turn_max_calls': TURN_CALLS, 'per_turn_max_tokens': TURN_TOKENS,
    'per_turn_capability_limits': {'chat': 2, 'embedding': 1, 'rerank': 1},
    'rewrite_max_output_tokens': 150, 'generation_max_output_tokens': GENERATION_OUTPUT,
    'router_mode': 'CHEAP_ONLY', 'memory_enabled': False, 'query_expansion': False,
    'bridge_rescue_mode': 'off', 'source_evidence_mode': 'bounded_source_v1', 'citations_enabled': True,
    'new_sessions': 2, 'retries': 0, 'expensive_calls': 0, 'provider': 'siliconflow',
    'models': MODELS, 'knowledge_base_ids': [KB_ID], 'document_ids': list(DOCUMENT_IDS),
    'turns': list(TURNS)}


def validate_inputs(raw):
    require(isinstance(raw, bytes) and sha(raw) == FROZEN_SHA256, 'S85C_FROZEN_INPUT_BYTES_CHANGED')
    payload = json.loads(raw.decode('utf-8-sig'))
    cases = historical.validate_payload(payload)
    sources, versions = {}, {}
    for case in cases:
        for row in case.get('source_rows', case['search_results'] + case['merge_results']):
            if row is None or row['chunk_type'] != 'text' or row['knowledge_id'] not in DOCUMENT_IDS:
                continue
            require(row['knowledge_base_id'] == KB_ID, 'S85C_FROZEN_SOURCE_SCOPE_CHANGED')
            version = (row.get('metadata') or {}).get('source_version')
            require(bool(version) and versions.setdefault(row['knowledge_id'], version) == version,
                    'S85C_FROZEN_SOURCE_VERSION_CONFLICT')
            old = sources.setdefault(row['id'], deepcopy(row))
            require(historical.source_identity(old) == historical.source_identity(row)
                    and historical.source_links_match(old, row), 'S85C_FROZEN_SOURCE_CONFLICT')
    require(set(versions) == set(DOCUMENT_IDS), 'S85C_FOUR_PUBLIC_DOCUMENTS_REQUIRED')
    return sources, versions


def validate_owner(owner, raw):
    validate_inputs(raw)
    require(isinstance(owner, dict) and owner.get('schema') == 's85-conversation-authorization/v1'
        and owner.get('approved') is True
        and isinstance(owner.get('approval_source'), str) and owner['approval_source'].strip()
        and isinstance(owner.get('owner_message'), str) and owner['owner_message'].strip(),
        'S85C_NEW_EXPLICIT_OWNER_APPROVAL_REQUIRED')
    stage = owner.get('stage_id')
    require(isinstance(stage, str) and re.fullmatch(r'[0-9]{8}-[0-9a-f]{32}', stage)
        and owner.get('grant_id') == 's85c-' + stage, 'S85C_FRESH_STAGE_AND_GRANT_REQUIRED')
    require(owner.get('session_id') == SESSION_ID and owner.get('session_envelope') == SESSION_ENVELOPE
        and owner.get('envelope') == ENVELOPE, 'S85C_EXACT_CONTINUATION_ENVELOPE_REQUIRED')
    require(owner.get('source_inputs_sha256') == FROZEN_SHA256, 'S85C_APPROVED_INPUT_CHANGED')
    return 's85c-' + stage


def validate_root(root, stage):
    value = str(root)
    path = PurePosixPath(value)
    require(value == ROOT_PREFIX + stage and path.parent == PurePosixPath('/opt/rag-s8/state')
            and '..' not in path.parts and '\\' not in value, 'S85C_PRIVATE_STAGE_ROOT_REQUIRED')


def validate_roles(roles):
    require(isinstance(roles, dict) and set(roles) == set(MODELS), 'S85C_THREE_FORMAL_ROLES_REQUIRED')
    for name, model in MODELS.items():
        row = roles[name]
        require(set(row) == {'provider', 'model', 'model_id', 'capability'}
            and row['provider'] == 'siliconflow' and row['model'] == model
            and isinstance(row['model_id'], str) and row['model_id']
            and row['capability'] == ('chat' if name == 'cheap' else name), 'S85C_PINNED_ROLE_REQUIRED')


def runtime_settings(stage):
    settings, principal = historical.runtime_settings(stage, cloud=False)
    return replace(settings, router_mode='CHEAP_ONLY', source_evidence_mode='bounded_source_v1',
                   redis_prefix='weknora-python-s8:s85c-' + stage), principal


def implementation_hashes():
    root = Path(__file__).resolve().parent.parent
    paths = ['scripts/s8_5_conversation_ecs.py', 'scripts/s8_5_bridge_ecs.py',
             'scripts/s8_4_bridge_ecs.py']
    paths.extend(str(path.relative_to(root)).replace('\\', '/')
                 for path in sorted((root / 'backend/weknora_port').rglob('*.py')))
    return {path: sha((root / path).read_bytes()) for path in paths}


def generation_contract(config):
    from backend.weknora_port.models.request import build_chat_body
    from backend.weknora_port.models.output_contract import resolve_output_contract
    from backend.weknora_port.models.types import Message, ChatOptions
    require(config.max_output_tokens == 0 or config.max_output_tokens >= GENERATION_OUTPUT,
            'S85C_GENERATION_MODEL_OUTPUT_LIMIT_STOP')
    body = build_chat_body(config, (Message('user', 'Output contract preflight; never sent.'),),
        ChatOptions(max_completion_tokens=GENERATION_OUTPUT, thinking=False), stream=True)
    contract = resolve_output_contract(config, body, capability='chat')
    require(contract.output_upper == GENERATION_OUTPUT and contract.thinking_mode == 'disabled',
            'S85C_GENERATION_OUTPUT_CONTRACT_STOP')
    return asdict(contract)


async def protected_snapshot(app, sessions=(), runs=()):
    """Fingerprint old rows, excluding only this run's genuinely new identities."""
    base = await historical.history_snapshot(app)
    provider = await app._rows(
        "SELECT count(*) AS calls,count(*) FILTER(WHERE outcome='UNKNOWN') AS unknown,"
        "count(*) FILTER(WHERE finished_at IS NULL AND outcome IS DISTINCT FROM 'UNKNOWN') AS unfinished,"
        "md5(string_agg(md5(row_to_json(p)::text),'' ORDER BY call_id)) AS fingerprints "
        "FROM port_provider_calls p WHERE NOT(split_part(request_id,'/',1)=ANY(%s))", (list(runs),))
    base['provider'] = provider[0]
    for table in ('sessions', 'messages', 'port_turns'):
        column = 'id' if table == 'sessions' else 'session_id'
        rows = await app._rows("SELECT count(*) AS count,md5(string_agg(md5(row_to_json(p)::text),'' "
            "ORDER BY row_to_json(p)::text)) AS fingerprints FROM " + table + " p WHERE NOT(" + column + "=ANY(%s))",
            (list(sessions),))
        base[table] = rows[0]
    for table in ('knowledges', 'chunks', 'embeddings'):
        # The diagnostic must never mutate either source rows or the existing index.
        rows = await app._rows("SELECT count(*) AS count,md5(string_agg(md5(row_to_json(p)::text),'' "
            "ORDER BY row_to_json(p)::text)) AS fingerprints FROM " + table + " p")
        base[table] = rows[0]
    return base


async def current_sources(app, registry, sources, versions, context):
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    rows = await app._rows("SELECT id,metadata FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s "
        "AND id=ANY(%s) AND deleted_at IS NULL AND parse_status='completed' AND enable_status='enabled'",
        (app.principal.tenant_id, KB_ID, list(DOCUMENT_IDS)))
    actual = {row['id']: (json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']).get('source_version')
              for row in rows}
    require(actual == versions, 'S85C_CURRENT_DOCUMENT_VERSION_CHANGED')
    repo = LeasedKnowledgeRepository(app, registry)
    loaded = dict(await repo.get_current_chunks(tuple(sources), context))
    require(set(loaded) == set(sources), 'S85C_CURRENT_SOURCE_SCOPE_OR_VERSION_DENIED')
    return [historical.verify_source_pair(sources[key], loaded[key]) for key in sorted(sources)]


async def prepare(root, *, write=True):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.models.types import CallContext, TrustedScope
    raw = (root / 'payload/inputs.json').read_bytes()
    owner = read(root / 'payload/owner-authorization.json')
    stage_prefix = validate_owner(owner, raw)
    validate_root(root, owner['stage_id'])
    sources, versions = validate_inputs(raw)
    settings, principal = runtime_settings(owner['stage_id'])
    roles, configs, formal_ids = {}, {}, {}
    async with assemble(settings, principal=principal) as app:
        before = await protected_snapshot(app)
        require(before['provider']['unfinished'] == 0, 'S85C_UNFINISHED_PROVIDER_OPERATION_STOP')
        identity = await app.runtime_identity(principal)
        async with app.leases.lease() as guard:
            revision, configured = await PostgresModelRoleStore(
                guard, principal, app.catalogue, cipher=app.cipher).read()
        kb = await app._rows('SELECT id,embedding_model_id,cloud_allowed FROM knowledge_bases '
            'WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL', (principal.tenant_id, KB_ID))
        require(len(kb) == 1 and kb[0]['cloud_allowed'] is True, 'S85C_PUBLIC_KB_EGRESS_DENIED')
        for name, expected in MODELS.items():
            role = next((row for row in configured if row.role == name), None)
            require(role is not None and role.provider == 'siliconflow' and role.model == expected,
                    'S85C_FORMAL_ROLE_REQUIRED')
            formal_ids[name] = app.models._identity(role)[0]
            formal = await app.models.resolve(formal_ids[name])
            mid = kb[0]['embedding_model_id'] if name == 'embedding' else formal_ids[name]
            actual = await app.models.resolve(mid)
            require(actual.provider == formal.provider == role.provider and actual.model == formal.model == expected,
                    'S85C_KB_EMBEDDING_FORMAL_IDENTITY_CHANGED')
            config = app.catalogue.resolve(actual, mid)
            roles[name] = {'provider': actual.provider, 'model': actual.model, 'model_id': mid,
                           'capability': 'chat' if name == 'cheap' else name}
            configs[name] = config
        validate_roles(roles)
        output_contract = generation_contract(configs['cheap'])
        context = CallContext(stage_prefix + '/preflight',
            TrustedScope(principal.subject_id, (KB_ID,), tuple(sorted(DOCUMENT_IDS))))
        registry = app.registry_for_context()
        try:
            proofs = await current_sources(app, registry, sources, versions, context)
        finally:
            await registry.close()
        require(await protected_snapshot(app) == before, 'S85C_ZERO_SEND_MUTATION')
    result = {'status': 'S85C_READY_ZERO_SEND', 'stage_prefix': stage_prefix,
        'envelope': deepcopy(ENVELOPE), 'owner_authorization_sha256': sha(canonical(owner)),
        'source_inputs_sha256': sha(raw), 'document_versions': versions, 'current_sources': proofs,
        'registry_roles': roles, 'formal_role_ids': formal_ids, 'role_revision': revision,
        'model_config_sha256': {name: sha(canonical(asdict(config))) for name, config in configs.items()},
        'generation_output_contract': output_contract, 'identity': identity,
        'subject_sha256': sha(principal.subject_id), 'storage_marker': historical.storage_marker_identity(settings),
        'implementation_sha256': implementation_hashes(), 'before': before,
        'new_provider_calls': 0, 'actual_tokens_added': 0}
    if write:
        write_new(root / 'preflight.json', result)
    return result


def request_kind(request_id, run_id):
    require(isinstance(run_id, str) and re.fullmatch(r'[0-9a-f-]{36}', run_id), 'S85C_ACTUAL_RUN_UUID_REQUIRED')
    try:
        parsed = uuid.UUID(run_id)
    except ValueError:
        raise ValueError('S85C_ACTUAL_RUN_UUID_REQUIRED') from None
    require(str(parsed) == run_id and parsed.version == 4, 'S85C_ACTUAL_RUN_UUID_REQUIRED')
    if request_id == run_id + '/query_rewrite': return 'rewrite'
    if request_id == run_id + '/answer/1/cheap': return 'generation'
    if request_id == run_id + '/rerank': return 'rerank'
    if re.fullmatch(re.escape(run_id + '/query-embedding-' + KB_ID + '-') + r'[0-9a-f]{16}', request_id):
        return 'embedding'
    raise ValueError('S85C_EXACT_OPERATION_DENIED')


def ledger_spec(principal, run_id, roles):
    validate_roles(roles)
    request_kind(run_id + '/query_rewrite', run_id)
    return {'status': 'OPEN', 'scope': 'S8_5_CONVERSATION_NEW_OWNER',
        'tenant_id': principal.tenant_id, 'subject_id': principal.subject_id,
        'allowed_kbs': [KB_ID], 'allowed_models': list(deepcopy(roles).values()),
        'allowed_request_prefixes': [run_id + '/'], 'max_calls': TURN_CALLS,
        'max_tokens': TURN_TOKENS, 'capture_evidence': True, 'entries': []}


def check_aggregate(summaries, *, next_turn=False):
    require(len(summaries) + int(next_turn) <= 7, 'S85C_SEVEN_TURN_BOUND_EXCEEDED')
    require(all(row['possibly_sent_unknown_operations'] == 0 for row in summaries), 'S85C_UNKNOWN_STOP')
    calls = sum(row['attempted_operations'] for row in summaries)
    tokens = sum(row['held_tokens_total'] for row in summaries)
    require(calls + (TURN_CALLS if next_turn else 0) <= ENVELOPE['max_calls']
        and tokens + (TURN_TOKENS if next_turn else 0) <= ENVELOPE['max_tokens'], 'S85C_AGGREGATE_BOUND_EXCEEDED')
    return {'attempted_operations': calls, 'held_token_exposure': tokens}


class TurnFence:
    """Exact turn purpose and effective query; wraps the real transport/gate."""
    def __init__(self, run_id, index, roles, config_hashes, *, audit=None, held_tokens=lambda: 0):
        validate_roles(roles)
        require(type(index) is int and 1 <= index <= 7, 'S85C_FIXED_TURN_REQUIRED')
        request_kind(run_id + '/query_rewrite', run_id)
        self.run_id, self.index = run_id, index
        self.roles, self.config_hashes = deepcopy(roles), dict(config_hashes)
        self.audit, self.held_tokens = audit, held_tokens
        self.bound, self.generation = None, None
        self.seen, self.completed, self.wires, self.stopped = set(), set(), [], False

    def bind(self, context, state):
        require(context.request_id == self.run_id and state.query == TURNS[self.index-1]['q0']
            and context.scope.knowledge_base_ids == (KB_ID,)
            and set(context.scope.document_ids) == set(DOCUMENT_IDS), 'S85C_BOUND_TURN_REQUIRED')
        self.bound = context, state

    def validate(self, config, body, context, capability):
        kind = request_kind(context.request_id, self.run_id)
        require(not self.stopped and self.bound is not None and kind not in self.seen
                and len(self.seen) < TURN_CALLS, 'S85C_EXACT_OPERATION_DENIED')
        original, state = self.bound
        require(context.scope == original.scope and context.session_id == original.session_id
            and not context.required_origin_kbs, 'S85C_WIRE_SCOPE_CHANGED')
        require(capability == ('chat' if kind in ('rewrite', 'generation') else kind), 'S85C_CAPABILITY_CHANGED')
        name = 'cheap' if capability == 'chat' else capability
        role = self.roles[name]
        require(all(getattr(config, key) == role[key] for key in ('provider', 'model', 'model_id'))
                and sha(canonical(asdict(config))) == self.config_hashes[name], 'S85C_ROLE_OR_CONFIG_CHANGED')
        require(self.index != 6 or kind not in ('embedding', 'rerank'), 'S85C_FORMAT_RETRIEVAL_DENIED')
        if kind == 'embedding':
            from backend.weknora_port.models.request import build_embedding_body
            require(state.needs_retrieval and context.request_id == self.run_id + '/query-embedding-' + KB_ID + '-' + sha(state.rewrite_query)[:16]
                and body == build_embedding_body(config, [state.rewrite_query], 'query'), 'S85C_EFFECTIVE_EMBEDDING_QUERY_CHANGED')
        elif kind == 'rerank':
            require(state.needs_retrieval and body.get('query') == state.rewrite_query
                    and isinstance(body.get('documents'), list) and body['documents'], 'S85C_EFFECTIVE_RERANK_QUERY_CHANGED')
        elif kind == 'rewrite':
            require(body.get('stream') is not True and isinstance(body.get('messages'), list)
                and state.query in body['messages'][-1].get('content', '')
                and not body.get('tools'), 'S85C_CURRENT_REWRITE_INPUT_CHANGED')
        else:
            require(self.generation is not None, 'S85C_GENERATION_REQUEST_REQUIRED')
            from backend.weknora_port.models.request import build_chat_body
            from backend.weknora_port.generation.context import prepare_generation
            prepared = prepare_generation(self.generation)
            options = self.generation.options
            if context.session_id and not options.prompt_cache_key:
                options = replace(options, prompt_cache_key=context.session_id)
            require(body == build_chat_body(config, prepared.messages, options, stream=True),
                    'S85C_FINAL_GENERATION_WIRE_CHANGED')
        if capability == 'chat':
            from backend.weknora_port.models.output_contract import resolve_output_contract
            contract = resolve_output_contract(config, body, capability='chat')
            require(contract.output_upper == (150 if kind == 'rewrite' else GENERATION_OUTPUT)
                and contract.thinking_mode == 'disabled', 'S85C_OUTPUT_CONTRACT_STOP')
        require(bridge.reservation(config, body, capability) + self.held_tokens() <= TURN_TOKENS,
                'S85C_RESERVATION_BOUND_EXCEEDED')
        self.seen.add(kind)
        self.wires.append({'kind': kind, 'request_id': context.request_id, 'capability': capability,
            'wire_sha256': sha(canonical(body)), 'body': deepcopy(body)})

    def install(self, model):
        transport = model.transport
        require(not hasattr(transport, '_s85c_fence'), 'S85C_FENCE_REUSE_DENIED')
        original, fence = transport.open, self

        @asynccontextmanager
        async def opened(config, path, body, context, **kwargs):
            try:
                fence.validate(config, body, context, kwargs.get('capability'))
            except (ValueError, KeyError, TypeError) as exc:
                fence.stopped = True
                raise PermissionError(str(exc)) from None
            failure = None
            try:
                async with original(config, path, body, context, **kwargs) as handle:
                    yield handle
            except BaseException as exc:
                failure, fence.stopped = exc, True
                raise
            finally:
                try:
                    if fence.audit is not None:
                        await asyncio.shield(fence.audit())
                    if failure is None:
                        fence.completed.add(request_kind(context.request_id, fence.run_id))
                except BaseException:
                    fence.stopped = True
                    if failure is None:
                        raise
        transport.open, transport._s85c_fence = opened, self
        return model


def validate_settlements(rows, entries, run_id, roles):
    validate_roles(roles)
    indexed = []
    for label, values in (('PG', rows), ('EXECUTION', entries)):
        group = {}
        for row in values:
            key = row['request_id'], row['capability']
            require(key not in group, 'S85C_DUPLICATE_' + label + '_OPERATION')
            kind = request_kind(key[0], run_id)
            require(key[1] == ('chat' if kind in ('rewrite', 'generation') else kind), 'S85C_UNAPPROVED_CAPABILITY')
            group[key] = row
        indexed.append(group)
    pg, execution = indexed
    require(set(pg) == set(execution), 'S85C_DUAL_LEDGER_KEYS_MISMATCH')
    require(len(execution) <= TURN_CALLS, 'S85C_TURN_CALL_BOUND_EXCEEDED')
    totals = {cap: {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
              for cap in ('chat', 'embedding', 'rerank')}
    for key, entry in execution.items():
        row, cap = pg[key], key[1]
        role = roles['cheap' if cap == 'chat' else cap]
        require(entry.get('status') == 'SETTLED' and entry.get('outcome') == row.get('outcome') == 'RESPONSE_RECEIVED'
            and row.get('finished_at'), 'S85C_UNKNOWN_OR_UNFINISHED_STOP')
        require(all(entry.get(name) == row.get(name) == role[name] for name in ('provider', 'model', 'model_id'))
            and entry['wire_sha256'] == row['wire_sha256'], 'S85C_SETTLEMENT_IDENTITY_CHANGED')
        usage, other = row.get('usage'), entry.get('usage')
        require(isinstance(usage, dict) and isinstance(other, dict)
            and usage.get('status') == other.get('status') == 'REPORTED', 'S85C_USAGE_UNKNOWN_STOP')
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            require(type(usage.get(field)) is int and usage[field] >= 0 and usage[field] == other.get(field),
                    'S85C_USAGE_MISMATCH')
        require(usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens'] == entry['held_tokens'],
                'S85C_USAGE_PARTS_OR_HELD_MISMATCH')
        total = totals[cap]
        total['calls'] += 1
        total['input_tokens'] += usage['prompt_tokens']
        total['output_tokens'] += usage['completion_tokens']
        total['total_tokens'] += usage['total_tokens']
    require(totals['chat']['calls'] <= 2 and totals['embedding']['calls'] <= 1 and totals['rerank']['calls'] <= 1
        and sum(row['total_tokens'] for row in totals.values()) <= TURN_TOKENS, 'S85C_USAGE_BOUND_EXCEEDED')
    return totals


def trace_state(state):
    from backend.weknora_port.retrieval.types import context_results
    from backend.weknora_port.retrieval.query import _REFERENCE
    return {'q0': state.query, 'rewrite_query': state.rewrite_query,
        'rewrite_fallback_reason': state.rewrite_fallback_reason, 'query_trace': deepcopy(state.query_trace),
        'coreference_resolved': bool(_REFERENCE.search(state.query) and not state.rewrite_fallback_reason and state.rewrite_query
            and state.rewrite_query != state.query),
        'intent': state.intent, 'needs_retrieval': state.needs_retrieval,
        'history': [asdict(row) for row in state.history], 'user_content': state.user_content,
        'search_results': [asdict(row) for row in state.search_results if row is not None],
        'rerank_results': [asdict(row) for row in state.rerank_results if row is not None],
        'context_evidence': [asdict(row) for row in context_results(state) if row is not None],
        'evidence_trace': deepcopy(state.evidence_trace)}


async def source_readback(app, request, context, versions):
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    if not request.evidence:
        return []
    registry = app._runs[request.run_id]['registry']
    repo = LeasedKnowledgeRepository(app, registry)
    current = dict(await repo.get_current_chunks(tuple(row.id for row in request.evidence), context))
    require(set(current) == {row.id for row in request.evidence}, 'S85C_CONTEXT_SOURCE_READBACK_DENIED')
    proof = []
    for row in request.evidence:
        actual = current[row.id]
        require(actual.knowledge_base_id == row.knowledge_base_id == KB_ID
            and actual.knowledge_id == row.knowledge_id and actual.knowledge_id in versions
            and actual.metadata.get('source_version') == actual.chunk_metadata.get('source_version') == versions[actual.knowledge_id]
            and actual.content == row.source_content and actual.start_at == row.start_at
            and actual.end_at == row.end_at and actual.is_enabled and not actual.content_rewritten,
            'S85C_CONTEXT_NOT_CURRENT_ORIGINAL')
        proof.append({'chunk_id': actual.id, 'document_id': actual.knowledge_id,
            'source_version': versions[actual.knowledge_id], 'start_at': actual.start_at,
            'end_at': actual.end_at, 'content_sha256': sha(actual.content), 'exact_text': actual.content})
    return proof


async def isolation_checks(app, principal, main_session, empty_session, *, completed=None):
    from backend.weknora_port.conversation.types import MemoryContext
    before = await historical.history_snapshot(app)
    wrong = replace(principal, subject_id='s85c-isolation-' + uuid.uuid4().hex)
    denied = False
    try:
        await app.list_messages(wrong, main_session)
    except Exception as exc:
        # Accept only the concrete owner rejection, never DB/network errors.
        denied = getattr(exc, 'code', '') == 'TRUSTED_PRINCIPAL_REQUIRED'
        if not denied:
            raise
    require(denied, 'S85C_WRONG_SUBJECT_NOT_DENIED')
    memory = MemoryContext(principal.tenant_id, principal.subject_id, agent_memory_allowed=False)
    empty = await app.history.load(memory, empty_session, 5)
    require(not empty and not await app.list_messages(principal, empty_session), 'S85C_SESSION_HISTORY_LEAK')
    result = {'wrong_subject_denied': True, 'empty_session_history_rounds': len(empty), 'new_provider_calls': 0}
    if completed is not None:
        main = await app.history.load(memory, main_session, 5)
        require(main and any(row.request_id == completed.run_id for row in main), 'S85C_COMPLETED_HISTORY_NOT_READ')
        result['main_history_request_ids'] = [row.request_id for row in main]
    after = await historical.history_snapshot(app)
    require(before == after, 'S85C_ISOLATION_READ_MUTATED_OR_SENT')
    result['provider_before'], result['provider_after'] = before['provider'], after['provider']
    return result


def citation_readback(result, proofs):
    """Citable provenance only; this does not prove every prose claim is true."""
    by_id = {row['chunk_id']: row for row in proofs}
    checked = []
    for ref in result.references:
        source = by_id.get(ref.get('id'))
        require(source is not None and ref.get('knowledge_base_id') == KB_ID
            and ref.get('knowledge_id') == source['document_id']
            and ref.get('start_at') == source['start_at'] and ref.get('end_at') == source['end_at']
            and ref.get('content') == source['exact_text'], 'S85C_FINAL_CITATION_NOT_CURRENT_SOURCE')
        checked.append(ref['id'])
    return {'citations_present': bool(checked), 'cited_source_ids_read_back': checked,
            'prose_semantic_support': 'REQUIRES_REVIEW_OF_ANSWER_AND_ORIGINALS'}


async def run(root):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.execution import LocalExecution
    from backend.weknora_port.http_api import SessionCreate
    from backend.weknora_port.service import OrdinaryTurnRequest
    from backend.weknora_port.models.types import ChatOptions
    raw = (root / 'payload/inputs.json').read_bytes()
    owner = read(root / 'payload/owner-authorization.json')
    prefix = validate_owner(owner, raw)
    validate_root(root, owner['stage_id'])
    pre = read(root / 'preflight.json')
    require(pre == await prepare(root, write=False), 'S85C_PREFLIGHT_CHANGED_BEFORE_RUN')
    write_new(root / 'run.claim', {'stage_prefix': prefix, 'envelope': ENVELOPE, 'time': time.time()})
    execution_dir = Path(EXECUTION_ROOT) / prefix
    require(not execution_dir.exists(), 'S85C_EXISTING_EXECUTION_NO_RESET')
    execution_dir.mkdir(mode=0o700, parents=True)
    os.environ['WEKNORA_PORT_EXECUTION_ROOT'] = EXECUTION_ROOT
    settings, principal = runtime_settings(owner['stage_id'])
    sessions, run_ids, summaries, records = [], [], [], []
    final = {'status': 'S85C_STOP', 'stage_prefix': prefix, 'envelope': ENVELOPE,
        'preflight_sha256': sha(canonical(pre)), 'turns': records, 'new_sessions': sessions,
        'model_output_repaired': False, 'semantic_answer_support': 'REQUIRES_REVIEW_OF_RECORDED_ANSWER_AND_ORIGINALS'}
    current_ledger = None
    async with assemble(settings, principal=principal) as app:
        try:
            require(await protected_snapshot(app) == pre['before'], 'S85C_HISTORY_CHANGED_BEFORE_RUN')
            # Settings are process-only. Persist the ordinary Session's real scope/defaults.
            app.conversation = replace(app.conversation, enable_query_expansion=False, bridge_rescue_mode='off')
            for label in ('seven-turn', 'empty-isolation'):
                created = await app.create_session(principal, SessionCreate(title=prefix + ':' + label,
                    knowledge_base_scope=[KB_ID], document_scope=list(DOCUMENT_IDS), source_evidence_mode='bounded_source_v1'))
                sessions.append(created['id'])
                write_new(root / ('new-session-' + label + '.json'), created)
            require(await protected_snapshot(app, sessions) == pre['before'], 'S85C_OLD_HISTORY_OR_SOURCE_MUTATION')
            final['isolation_before'] = await isolation_checks(app, principal, *sessions)
            original_factory = app.model_factory
            original_config, original_pipeline, original_generation = (
                app.ordinary.configuration, app.ordinary.pipeline_factory, app.ordinary.generation_factory)
            for index, case in enumerate(TURNS, 1):
                check_aggregate(summaries, next_turn=True)
                app.cloud_allowed = False
                request = OrdinaryTurnRequest(sessions[0], case['q0'], str(uuid.uuid4()),
                    knowledge_base_scope=(KB_ID,), document_scope=tuple(DOCUMENT_IDS),
                    summary_model_id=pre['registry_roles']['cheap']['model_id'], reasoning_effort='off', accept_language='en')
                accepted = await app.turns.accept(request, principal)
                require(accepted.is_new and not accepted.replay_only, 'S85C_FRESH_ADMISSION_REQUIRED')
                accepted = replace(accepted, memory_context=replace(accepted.memory_context, agent_memory_allowed=False),
                    context=replace(accepted.context, deadline=asyncio.get_running_loop().time()+180, deadline_kind='run'))
                run_ids.append(accepted.run_id)
                turn_dir = execution_dir / ('turn-' + str(index).zfill(2))
                turn_dir.mkdir(mode=0o700)
                current_ledger = turn_dir / 'execution.json'
                write_new(current_ledger, ledger_spec(principal, accepted.run_id, pre['registry_roles']))
                app.execution = LocalExecution(current_ledger)
                record = {'index': index, **case, 'client_request_id': request.request_id,
                    'request_id': accepted.run_id, 'session_id': accepted.session_id,
                    'user_message_id': accepted.user_message_id, 'assistant_message_id': accepted.assistant_message_id,
                    'ledger': str(current_ledger), 'hybrid_search': [], 'source_readback': []}
                records.append(record)
                write_new(root / ('turn-' + str(index).zfill(2) + '-admission.json'), record)

                async def audit():
                    data = read(current_ledger)
                    rows = await historical.provider_rows(app, principal, accepted.run_id + '/')
                    validate_settlements(rows, data['entries'], accepted.run_id, pre['registry_roles'])
                    require(await protected_snapshot(app, sessions, run_ids) == pre['before'], 'S85C_OLD_HISTORY_OR_SOURCE_MUTATION')

                fence = TurnFence(accepted.run_id, index, pre['registry_roles'], pre['model_config_sha256'], audit=audit,
                    held_tokens=lambda: sum(row['held_tokens'] for row in read(current_ledger)['entries']))

                async def factory(config, key, gate):
                    model = original_factory(config, key, gate)
                    model = await model if inspect.isawaitable(model) else model
                    return fence.install(model)

                async def configuration(turn):
                    config = await original_config(turn)
                    state = config.pipeline_state
                    require(state.bridge_rescue_mode == 'off' and not state.enable_expansion
                        and state.source_evidence_mode == 'bounded_source_v1', 'S85C_CONFIGURATION_CHANGED')
                    config.generation_options['citations_enabled'] = True
                    options = config.generation_options.get('options', ChatOptions())
                    config.generation_options['options'] = replace(options, max_tokens=0,
                        max_completion_tokens=GENERATION_OUTPUT, thinking=False)
                    return config

                async def pipeline_factory(turn, progress):
                    pipeline = await original_pipeline(turn, progress)
                    run_pipeline, search = pipeline.run, pipeline.repository.hybrid_search

                    async def observed_search(targets, params, context, query_embedding=None):
                        record['hybrid_search'].append({'targets': [asdict(t) for t in targets],
                            'params': asdict(params), 'request_id': context.request_id})
                        require(len(record['hybrid_search']) == 1 and fence.bound is not None
                            and params.query_text == fence.bound[1].rewrite_query, 'S85C_EFFECTIVE_HYBRID_QUERY_CHANGED')
                        return await search(targets, params, context, query_embedding)

                    async def observed_run(state, context):
                        fence.bind(context, state)
                        try:
                            return await run_pipeline(state, context)
                        finally:
                            record['pipeline'] = trace_state(state)
                    pipeline.repository.hybrid_search, pipeline.run = observed_search, observed_run
                    return pipeline

                async def generation_factory(turn, progress):
                    coordinator = await original_generation(turn, progress)
                    generate = coordinator.run

                    async def observed_generation(generation_request, context):
                        require(generation_request.q0 == case['q0'] and generation_request.policy.mode == 'CHEAP_ONLY'
                            and generation_request.citations_enabled and not generation_request.memory_prompt,
                            'S85C_GENERATION_SCOPE_OR_POLICY_CHANGED')
                        require(fence.bound is not None and fence.bound[1].needs_retrieval == case['needs_retrieval'],
                                'S85C_INTENT_PATH_CONTRACT_STOP')
                        fence.generation = generation_request
                        record['source_readback'] = await source_readback(app, generation_request, context, pre['document_versions'])
                        record['generation_input'] = {'q0': generation_request.q0,
                            'is_fallback': generation_request.is_fallback,
                            'finalized': asdict(generation_request.finalized),
                            'history': [asdict(h) for h in generation_request.history],
                            'evidence': [asdict(e) for e in generation_request.evidence]}
                        return await generate(generation_request, context)
                    coordinator.run = observed_generation
                    return coordinator

                app.model_factory, app.ordinary.configuration = factory, configuration
                app.ordinary.pipeline_factory, app.ordinary.generation_factory = pipeline_factory, generation_factory
                app.cloud_allowed = True
                result = await app.ordinary.run_turn(accepted)
                app.cloud_allowed = False
                data = read(current_ledger)
                pg = await historical.provider_rows(app, principal, accepted.run_id + '/')
                record.update(wires=fence.wires, provider_rows=pg, execution=deepcopy(data),
                    result=asdict(result) if result is not None else None)
                record['execution_summary'] = bridge.execution_summary(data['entries'])
                record['usage_by_capability'] = validate_settlements(pg, data['entries'], accepted.run_id, pre['registry_roles'])
                state = await app.turns.status(accepted.session_id, accepted.assistant_message_id)
                record['turn_status'] = asdict(state)
                require(state.status == 'completed' and result is not None and not state.error_code,
                        'S85C_ORDINARY_TURN_FAILED')
                record['citation_readback'] = citation_readback(result, record['source_readback'])
                require(record['pipeline']['needs_retrieval'] == case['needs_retrieval'], 'S85C_INTENT_PATH_CONTRACT_STOP')
                if index == 6:
                    require(not record['hybrid_search'] and not record['source_readback']
                        and not any(row['capability'] != 'chat' for row in pg), 'S85C_FORMAT_RETRIEVAL_DENIED')
                if index == 5:
                    require(not record['pipeline']['coreference_resolved'], 'S85C_AMBIGUOUS_REFERENCE_SELECTED')
                # Re-read PG admission and actual SSE replay. Neither path may call a model.
                before_replay = deepcopy(pg), deepcopy(read(current_ledger))
                provider_before_replay = await historical.history_snapshot(app)
                replay = await app.turns.accept(request, principal)
                require(replay.replay_only and replay.run_id == accepted.run_id, 'S85C_REPLAY_IDENTITY_CHANGED')
                await app.ordinary.run_turn(replay)
                events = [asdict(event) async for event in app.ordinary.replay(replay, principal)]
                require((await historical.provider_rows(app, principal, accepted.run_id + '/'), read(current_ledger)) == before_replay,
                        'S85C_REPLAY_DUPLICATE_MODEL_CALL')
                provider_after_replay = await historical.history_snapshot(app)
                require(provider_before_replay == provider_after_replay, 'S85C_REPLAY_MUTATED_OR_SENT')
                record['replay'] = {'replay_only': True, 'events': events, 'new_provider_calls': 0,
                    'provider_before': provider_before_replay['provider'], 'provider_after': provider_after_replay['provider']}
                record['isolation'] = await isolation_checks(app, principal, *sessions, completed=accepted)
                historical.close_ledger(current_ledger, success=True)
                record['execution'] = read(current_ledger)
                summaries.append(record['execution_summary'])
                record['receipt_manifest'] = bridge.receipt_manifest(turn_dir)
                record['messages'] = await app.list_messages(principal, sessions[0])
                safe_write(root / ('turn-' + str(index).zfill(2) + '-result.json'), record)
                safe_write(root / 'progress.json', {'completed_turns': index, 'aggregate': check_aggregate(summaries)})
                current_ledger = None
            final['status'] = 'S85C_COMPLETED_DIAGNOSTIC'
        except BaseException as exc:
            final['error'] = historical.safe_error(exc)
            if current_ledger is not None:
                data = read(current_ledger)
                rows = await historical.provider_rows(app, principal, run_ids[-1] + '/')
                if records:
                    records[-1].update(provider_rows=rows, execution=deepcopy(data),
                        execution_summary=bridge.execution_summary(data['entries']))
                    if 'fence' in locals(): records[-1]['wires'] = fence.wires
                    records[-1]['receipt_manifest'] = bridge.receipt_manifest(current_ledger.parent)
                historical.close_ledger(current_ledger, success=False)
            if isinstance(exc, asyncio.CancelledError): final['cancelled'] = True
        finally:
            app.cloud_allowed = False
            final['after'] = await protected_snapshot(app, sessions, run_ids)
            final['old_history_and_sources_unchanged'] = final['after'] == pre['before']
            final['execution_summaries'] = [record['execution_summary'] for record in records if 'execution_summary' in record]
            actual = final['execution_summaries']
            final['actual_reported_tokens_subtotal'] = sum(row['actual_reported_tokens_subtotal'] for row in actual)
            final['possibly_sent_unknown_operations'] = sum(row['possibly_sent_unknown_operations'] for row in actual)
            final['attempted_operations'] = sum(row['attempted_operations'] for row in actual)
            final['all_actual_tokens'] = (final['actual_reported_tokens_subtotal']
                if not final['possibly_sent_unknown_operations'] else None)
            if not final['old_history_and_sources_unchanged']:
                final['status'], final['error'] = 'S85C_STOP', 'S85C_OLD_HISTORY_OR_SOURCE_MUTATION'
            safe_write(root / 'final.json', final)
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run'))
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(prepare(args.root) if args.action == 'prepare' else run(args.root))
    print(canonical({'status': result['status'], 'actual_reported_tokens_subtotal': result.get('actual_reported_tokens_subtotal'),
                     'error': result.get('error')}))
    if result['status'] == 'S85C_STOP': raise SystemExit(2)


if __name__ == '__main__':
    main()
