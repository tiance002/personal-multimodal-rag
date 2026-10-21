"""Fresh S8.5 Frost-A-only diagnostic. Importing this module never connects.

Prepare reads current authorized originals with cloud disabled. Run requires a
new exact owner grant, one-use stage and the ordinary runtime/LocalExecution
gates. This script is not deployment, answer generation or a retrieval retry.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path, PurePosixPath
import re
import time

from scripts import s8_4_bridge_ecs as historical

MAX_CALLS = 2
MAX_TOKENS = 10000
CHEAP_OUTPUT = 256
CONTRACT = 'sentence-id/v2'
ROOT_PREFIX = '/opt/rag-s8/state/s8-5-bridge-'
EXECUTION_ROOT = '/opt/rag-s8/state/executions'
FROZEN_SHA256 = 'e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135'
CANONICAL_SHA256 = 'c5d6a419e4735ddfce8df6e1f3006f33ea5e43f5a0de0f93cc0ba1390dfcea26'
MODELS = {'cheap': 'Qwen/Qwen3.5-4B', 'rerank': 'BAAI/bge-reranker-v2-m3'}
ENVELOPE = {'case_ids': ['frost-A'], 'max_calls': MAX_CALLS, 'max_tokens': MAX_TOKENS,
            'capability_limits': {'chat': 1, 'rerank': 1}, 'cheap_max_output_tokens': CHEAP_OUTPUT,
            'contract_version': CONTRACT, 'provider': 'siliconflow', 'models': MODELS,
            'embedding_calls': 0, 'generation_calls': 0, 'retries': 0}

require, canonical, sha = historical.require, historical.canonical, historical.sha
read, write_new, safe_write = historical.read, historical.write_new, historical.safe_write


def validate_inputs(raw):
    require(isinstance(raw, bytes) and sha(raw) == FROZEN_SHA256, 'S85_FROZEN_INPUT_BYTES_CHANGED')
    payload = json.loads(raw.decode('utf-8-sig'))
    require(sha(canonical(payload)) == CANONICAL_SHA256, 'S85_FROZEN_CANONICAL_INPUT_CHANGED')
    cases = historical.validate_payload(payload)
    selected = [case for case in cases if case['case_id'] == 'frost-A']
    require(len(selected) == 1 and selected[0].get('arm') == 'A', 'S85_EXACT_FROST_A_REQUIRED')
    return deepcopy(selected[0])


def validate_owner(owner, raw):
    validate_inputs(raw)
    require(isinstance(owner, dict) and owner.get('schema') == 's85-real-authorization/v1'
            and owner.get('approval_source') == 'Owner explicit current messages'
            and isinstance(owner.get('owner_message'), str) and owner['owner_message'].strip()
            and owner.get('approved') is True, 'S85_NEW_EXPLICIT_OWNER_APPROVAL_REQUIRED')
    require(owner.get('envelope') == ENVELOPE, 'S85_APPROVED_ENVELOPE_CHANGED')
    require(owner.get('source_inputs_sha256') == FROZEN_SHA256
            and owner.get('canonical_inputs_sha256') == CANONICAL_SHA256,
            'S85_APPROVED_INPUT_CHANGED')
    stage = owner.get('stage_id')
    require(isinstance(stage, str) and re.fullmatch(r'[0-9]{8}-[0-9a-f]{32}', stage),
            'S85_FRESH_STAGE_ID_REQUIRED')
    require(owner.get('grant_id') == 's85-' + stage, 'S85_FRESH_GRANT_REQUIRED')
    return 's85-' + stage + '/'


def validate_root(root, stage):
    value = str(root)
    candidate = PurePosixPath(value)
    require(value == ROOT_PREFIX + stage and candidate.parent == PurePosixPath('/opt/rag-s8/state')
            and '..' not in candidate.parts and '\\' not in value, 'S85_PRIVATE_STAGE_ROOT_REQUIRED')


def request_plan(prefix):
    require(isinstance(prefix, str) and re.fullmatch(r's85-[0-9]{8}-[0-9a-f]{32}/', prefix),
            'S85_EXACT_REQUEST_PREFIX_REQUIRED')
    return {prefix + 'frost-A/bridge-extract': 'chat', prefix + 'frost-A/bridge-rerank': 'rerank'}


def validate_roles(roles):
    require(isinstance(roles, dict) and set(roles) == set(MODELS), 'S85_EXACT_REGISTRY_ROLES_REQUIRED')
    for name, model in MODELS.items():
        role = roles[name]
        require(set(role) == {'provider', 'model', 'model_id', 'capability'}
                and role['provider'] == 'siliconflow' and role['model'] == model
                and isinstance(role['model_id'], str) and role['model_id']
                and role['capability'] == ('chat' if name == 'cheap' else 'rerank'),
                'S85_PINNED_REGISTRY_MODEL_REQUIRED')


def runtime_settings(stage, *, cloud=False, ledger=None):
    settings, principal = historical.runtime_settings(stage, cloud=cloud, ledger=ledger)
    return replace(settings, redis_prefix='weknora-python-s8:s85-' + stage), principal


def implementation_hashes():
    source = Path(__file__).resolve().parent.parent
    paths = ('scripts/s8_5_bridge_ecs.py', 'backend/weknora_port/retrieval/bridge_rescue.py',
             'backend/weknora_port/retrieval/bridge_identity.py', 'backend/weknora_port/retrieval/context.py',
             'backend/weknora_port/models/request.py', 'backend/weknora_port/execution.py')
    return {path: sha((source / path).read_bytes()) for path in paths}


async def current_case(app, registry, case, prefix, principal, roles):
    """Verify all saved originals and independently build the exact current input."""
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    from backend.weknora_port.retrieval.bridge_rescue import (
        RELATION, _read_originals, original_windows, extraction_payload,
    )
    repository = LeasedKnowledgeRepository(app, registry)
    context = historical.case_context(case, principal, prefix)
    frozen = {row['id']: row for row in case['source_rows'] if row['chunk_type'] == 'text'}
    loaded = dict(await repository.get_current_chunks(tuple(frozen), context))
    require(set(loaded) == set(frozen), 'S85_CURRENT_SOURCE_SCOPE_OR_VERSION_DENIED')
    for key in frozen:
        historical.verify_source_pair(frozen[key], loaded[key])
    state = historical.build_state(case, roles)
    final_ids = {value for row in state.merge_results for value in (row.id, *row.sub_chunk_ids)}
    pool = {row.id: row for row in state.search_results if row is not None}
    mains = [pool[row.id] for row in state.rerank_results if row is not None and row.id in pool
             and row.id in final_ids and pool[row.id].chunk_type == 'text'
             and RELATION.search(pool[row.id].content)][:2]
    require(bool(mains), 'S85_FIRST_HOP_SOURCE_MISSING')
    originals = await _read_originals(mains, state, context, repository)
    windows, sources, chains = await original_windows(originals, state, context, repository)
    for key, source in sources.items():
        require(key in frozen, 'S85_PREDECESSOR_NOT_IN_FROZEN_INPUT')
        historical.verify_source_pair(frozen[key], source)
    payload = extraction_payload(state.rewrite_query or state.query, windows, sources)
    require(payload['contract_version'] == CONTRACT, 'S85_CONTRACT_VERSION_CHANGED')
    return {'case_id': 'frost-A', 'payload': payload, 'payload_sha256': sha(canonical(payload)),
            'sources': {key: asdict(value) for key, value in sorted(loaded.items())},
            'windows': [asdict(window) for window in windows],
            'predecessor_chains': {key: [row.id for row in chain] for key, chain in chains.items()}}


def expected_chat_body(config, payload):
    from backend.weknora_port.models.request import build_chat_body
    from backend.weknora_port.models.types import ChatOptions, Message
    from backend.weknora_port.retrieval.bridge_rescue import SYSTEM
    return build_chat_body(config, (Message('system', SYSTEM), Message('user', json.dumps(payload, ensure_ascii=False))),
        ChatOptions(temperature=0, thinking=False, max_tokens=CHEAP_OUTPUT, format='json', send_temperature=True))


def reservation(config, body, capability):
    from backend.weknora_port.models.output_contract import resolve_output_contract
    upper = resolve_output_contract(config, body, capability=capability).output_upper
    return len(canonical(body).encode('utf-8')) + upper + 1024


async def prepare(root, *, write=True):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    raw = (root / 'payload/inputs.json').read_bytes()
    case = validate_inputs(raw)
    owner = read(root / 'payload/owner-authorization.json')
    prefix = validate_owner(owner, raw)
    validate_root(root, owner['stage_id'])
    plan = request_plan(prefix)
    settings, principal = runtime_settings(owner['stage_id'])
    roles_out, config_hashes = {}, {}
    async with assemble(settings, principal=principal) as app:
        identity = await app.runtime_identity(principal)
        before = await historical.history_snapshot(app)
        require(before['provider']['unfinished'] == 0, 'S85_UNFINISHED_PROVIDER_OPERATION_STOP')
        require(not await historical.provider_rows(app, principal, prefix), 'S85_REQUEST_PREFIX_ALREADY_USED')
        async with app.leases.lease() as guard:
            revision, configured = await PostgresModelRoleStore(
                guard, principal, app.catalogue, cipher=app.cipher).read()
        configs = {}
        for name in MODELS:
            role = next((row for row in configured if row.role == name), None)
            require(role is not None, 'S85_FORMAL_ROLE_REQUIRED')
            mid = app.models._identity(role)[0]
            loaded = await app.models.resolve(mid)
            require(loaded.model == role.model and loaded.provider == role.provider,
                    'S85_REGISTRY_ROLE_CHANGED')
            roles_out[name] = {'provider': role.provider, 'model': role.model, 'model_id': mid,
                               'capability': 'chat' if name == 'cheap' else 'rerank'}
            configs[name] = app.catalogue.resolve(loaded, mid)
            config_hashes[name] = sha(canonical(asdict(configs[name])))
        validate_roles(roles_out)
        registry = app.registry_for_context()
        try:
            current = await current_case(app, registry, case, prefix, principal, roles_out)
        finally:
            await registry.close()
        cheap_body = expected_chat_body(configs['cheap'], current['payload'])
        reserve = reservation(configs['cheap'], cheap_body, 'chat')
        require(reserve <= MAX_TOKENS, 'S85_PREPARE_RESERVATION_BOUND_EXCEEDED')
        require(await historical.history_snapshot(app) == before, 'S85_ZERO_SEND_MUTATION')
    result = {'status': 'S85_READY_ZERO_SEND', 'envelope': ENVELOPE,
              'source_inputs_sha256': sha(raw), 'canonical_inputs_sha256': CANONICAL_SHA256,
              'owner_authorization_sha256': sha(canonical(owner)), 'stage_prefix': prefix,
              'exact_request_plan': plan, 'registry_roles': roles_out, 'role_revision': revision,
              'model_config_sha256': config_hashes, 'implementation_sha256': implementation_hashes(),
              'identity': identity, 'subject_sha256': sha(principal.subject_id),
              'storage_marker': historical.storage_marker_identity(settings),
              'before': before, 'current_case': current, 'cheap_reservation_tokens': reserve,
              'new_provider_calls': 0, 'actual_tokens_added': 0}
    if write:
        write_new(root / 'preflight.json', result)
    return result


def validate_settlements(rows, entries, plan, roles):
    validate_roles(roles)
    require(len(rows) == len(entries) <= MAX_CALLS, 'S85_DUAL_LEDGER_COUNT_MISMATCH')
    indexed = []
    for label, values in (('PG', rows), ('EXECUTION', entries)):
        group = {}
        for value in values:
            key = value['request_id'], value['capability']
            require(key not in group, 'S85_DUPLICATE_' + label + '_OPERATION')
            require(plan.get(key[0]) == key[1], 'S85_UNAPPROVED_OPERATION')
            group[key] = value
        indexed.append(group)
    pg, local = indexed
    require(set(pg) == set(local), 'S85_DUAL_LEDGER_KEYS_MISMATCH')
    totals = {cap: {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
              for cap in ('chat', 'rerank')}
    for key, entry in local.items():
        row = pg[key]
        require(entry['status'] == 'SETTLED' and entry.get('outcome') == row['outcome'] == 'RESPONSE_RECEIVED'
                and row.get('finished_at'), 'S85_UNKNOWN_OR_UNFINISHED_STOP')
        role = roles['cheap' if key[1] == 'chat' else 'rerank']
        require(all(row[name] == entry[name] == role[name] for name in ('provider', 'model', 'model_id'))
                and row['wire_sha256'] == entry['wire_sha256'], 'S85_SETTLEMENT_IDENTITY_CHANGED')
        usage, other = row.get('usage'), entry.get('usage')
        require(isinstance(usage, dict) and isinstance(other, dict)
                and usage.get('status') == other.get('status') == 'REPORTED', 'S85_USAGE_UNKNOWN_STOP')
        for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            require(type(usage.get(name)) is int and usage[name] >= 0 and usage[name] == other.get(name),
                    'S85_USAGE_MISMATCH')
        require(usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens']
                and entry['held_tokens'] == usage['total_tokens'], 'S85_USAGE_PARTS_OR_HELD_MISMATCH')
        total = totals[key[1]]
        total['calls'] += 1
        total['input_tokens'] += usage['prompt_tokens']
        total['output_tokens'] += usage['completion_tokens']
        total['total_tokens'] += usage['total_tokens']
    require(all(row['calls'] <= 1 for row in totals.values())
            and sum(row['total_tokens'] for row in totals.values()) <= MAX_TOKENS, 'S85_USAGE_BOUND_EXCEEDED')
    return totals


class RequestFence:
    """Exact operation, scope, current sentence mapping and final wire fence."""
    def __init__(self, plan, roles, config_hashes, *, audit=None, held_tokens=lambda: 0):
        validate_roles(roles)
        prefixes = {rid.rsplit('frost-A/', 1)[0] for rid in plan}
        require(len(prefixes) == 1 and plan == request_plan(next(iter(prefixes))), 'S85_EXACT_PLAN_REQUIRED')
        self.plan, self.roles, self.config_hashes = dict(plan), deepcopy(roles), dict(config_hashes)
        self.audit, self.held_tokens = audit, held_tokens
        self.seen, self.completed, self.bound, self.stopped = set(), set(), None, False

    def bind(self, context, state, prepared):
        require(context.request_id + '/bridge-extract' in self.plan
                and prepared['case_id'] == 'frost-A', 'S85_BOUND_CASE_REQUIRED')
        self.bound = context.scope, state, deepcopy(prepared)

    def validate(self, config, body, context, capability):
        require(not self.stopped and self.plan.get(context.request_id) == capability
                and context.request_id not in self.seen and len(self.seen) < MAX_CALLS,
                'S85_EXACT_OPERATION_DENIED')
        require(self.bound is not None and context.scope == self.bound[0]
                and not context.required_origin_kbs, 'S85_WIRE_SCOPE_CHANGED')
        name = 'cheap' if capability == 'chat' else 'rerank'
        role = self.roles[name]
        require(all(getattr(config, key) == role[key] for key in ('provider', 'model', 'model_id'))
                and sha(canonical(asdict(config))) == self.config_hashes[name], 'S85_WIRE_ROLE_OR_CONFIG_CHANGED')
        _, state, prepared = self.bound
        from backend.weknora_port.retrieval.bridge_rescue import FactWindow, extraction_payload
        from backend.weknora_port.retrieval.types import SearchResult
        sources = {key: SearchResult(**value) for key, value in prepared['sources'].items()}
        if capability == 'chat':
            trace = next((item for item in reversed(state.bridge_trace)
                          if item.get('decision') == 'EXTRACTION_INPUT'), None)
            require(trace is not None, 'S85_PROGRAM_EXTRACTION_TRACE_REQUIRED')
            payload = extraction_payload(state.rewrite_query or state.query,
                [FactWindow(**item) for item in trace['windows']], sources)
            require(payload == prepared['payload'] and trace['windows'] == prepared['windows']
                    and trace['predecessor_chains'] == prepared['predecessor_chains'],
                    'S85_CURRENT_SENTENCE_INPUT_CHANGED')
            require(body == expected_chat_body(config, payload), 'S85_EXACT_EXTRACTION_WIRE_REQUIRED')
        else:
            from backend.weknora_port.models.request import build_rerank_body
            extract_id = context.request_id.rsplit('/', 1)[0] + '/bridge-extract'
            require(extract_id in self.completed, 'S85_EXTRACT_SETTLEMENT_REQUIRED')
            authorized = next((item for item in reversed(state.bridge_trace)
                               if item.get('decision') == 'SECOND_RERANK_INPUT'), None)
            first = next((item for item in reversed(state.bridge_trace)
                          if item.get('decision') == 'FIRST_HOP_VERIFIED'), None)
            require(first is not None and first['extraction']['relation_verified'] is True
                    and authorized is not None, 'S85_VERIFIED_BRIDGE_REQUIRED')
            ids = authorized['candidate_ids']
            require(isinstance(ids, list) and 1 <= len(ids) <= 8 and len(set(ids)) == len(ids)
                    and all(key in sources for key in ids), 'S85_CANDIDATE_SOURCE_REQUIRED')
            q1 = 'Where is ' + first['extraction']['object'] + ' located?'
            require(authorized['q1'] == q1 and 0 < len(q1) <= 192
                    and body == build_rerank_body(config, q1, [sources[key].content for key in ids]),
                    'S85_PROGRAM_DERIVED_RERANK_CHANGED')
        require(reservation(config, body, capability) + self.held_tokens() <= MAX_TOKENS,
                'S85_RESERVATION_BOUND_EXCEEDED')
        self.seen.add(context.request_id)

    def install(self, model):
        transport = model.transport
        existing = getattr(transport, '_s85_fence', None)
        require(existing in (None, self), 'S85_FENCE_IDENTITY_CHANGED')
        if existing is self:
            return model
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
                        fence.completed.add(context.request_id)
                except BaseException:
                    fence.stopped = True
                    from backend.weknora_port.models.types import CallCancelled, RunDeadlineExceeded
                    if not isinstance(failure, (CallCancelled, RunDeadlineExceeded, asyncio.CancelledError)):
                        raise
        transport.open, transport._s85_fence = opened, self
        return model


class FencedRegistry:
    def __init__(self, registry, fence):
        self.registry, self.fence = registry, fence

    async def chat(self, model_id, context):
        require(model_id == self.fence.roles['cheap']['model_id'], 'S85_CHEAP_ROLE_ID_REQUIRED')
        return self.fence.install(await self.registry.chat(model_id, context))

    async def rerank(self, model_id, context):
        require(model_id == self.fence.roles['rerank']['model_id'], 'S85_RERANK_ROLE_ID_REQUIRED')
        return self.fence.install(await self.registry.rerank(model_id, context))


def ledger_spec(principal, case, pre):
    return {'status': 'OPEN', 'scope': 'S8_5_FROST_A_NEW_OWNER', 'tenant_id': principal.tenant_id,
            'subject_id': principal.subject_id, 'allowed_kbs': case['knowledge_base_ids'],
            'allowed_models': list(pre['registry_roles'].values()),
            'allowed_request_prefixes': sorted(pre['exact_request_plan']),
            'max_calls': MAX_CALLS, 'max_tokens': MAX_TOKENS, 'capture_evidence': True, 'entries': []}


def execution_summary(entries):
    """Describe sends separately from attempts; never release a reservation.

    LocalExecution writes LOCAL_ADMISSION_FAILED_STOP before the wrapped
    authorize returns to HTTP transport. That status proves no HTTP send.
    Every other unsettled status may have sent and keeps actual totals unknown.
    """
    settled, unsent, uncertain = [], [], []
    for entry in entries:
        usage = entry.get('usage')
        reported = (isinstance(usage, dict) and usage.get('status') == 'REPORTED'
                    and all(type(usage.get(key)) is int and usage[key] >= 0
                            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                    and usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens'])
        if entry.get('status') == 'LOCAL_ADMISSION_FAILED_STOP':
            unsent.append(entry)
        elif (entry.get('status') == 'SETTLED' and entry.get('outcome') == 'RESPONSE_RECEIVED'
              and reported):
            settled.append(entry)
        else:
            uncertain.append(entry)
    subtotal = sum(entry['usage']['total_tokens'] for entry in settled)
    held_known = all(type(entry.get('held_tokens')) is int and entry['held_tokens'] >= 0 for entry in entries)
    reserved_known = all(type(entry.get('reserved_tokens')) is int and entry['reserved_tokens'] >= 0 for entry in entries)
    return {'attempted_operations': len(entries), 'proven_unsent_operations': len(unsent),
            'settled_response_count': len(settled), 'possibly_sent_unknown_operations': len(uncertain),
            'actual_calls': len(settled) if not uncertain else None,
            'new_unknown': len(uncertain), 'all_actual_tokens': subtotal if not uncertain else None,
            'actual_reported_tokens_subtotal': subtotal,
            'held_tokens_total': sum(entry['held_tokens'] for entry in entries) if held_known else None,
            'reserved_tokens_total': sum(entry['reserved_tokens'] for entry in entries) if reserved_known else None}


def receipt_manifest(execution_dir):
    files = []
    for path in sorted((execution_dir / 'evidence').glob('*')):
        require(path.is_file() and not path.is_symlink(), 'S85_RECEIPT_PATH_INVALID')
        if path.name.endswith('.request.json'):
            require(read(path).get('headers_saved') is False, 'S85_REQUEST_HEADERS_FORBIDDEN')
        files.append({'path': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path.read_bytes())})
    return files


async def run(root):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge
    from backend.weknora_port.retrieval.context import into_chat_message
    from backend.weknora_port.retrieval.types import context_results
    raw = (root / 'payload/inputs.json').read_bytes()
    case, owner = validate_inputs(raw), read(root / 'payload/owner-authorization.json')
    prefix = validate_owner(owner, raw)
    validate_root(root, owner['stage_id'])
    pre = read(root / 'preflight.json')
    require(pre == await prepare(root, write=False), 'S85_PREFLIGHT_CHANGED_BEFORE_RUN')
    write_new(root / 'run.claim', {'stage_prefix': prefix, 'envelope': ENVELOPE, 'time': time.time()})
    execution_dir = Path(EXECUTION_ROOT) / ('s85-' + owner['stage_id'])
    require(not execution_dir.exists(), 'S85_EXISTING_EXECUTION_NO_RESET')
    execution_dir.mkdir(parents=True, mode=0o700)
    ledger = execution_dir / 'execution.json'
    _, principal = runtime_settings(owner['stage_id'])
    write_new(ledger, ledger_spec(principal, case, pre))
    write_new(root / 'bounded-execution.json', {'path': str(ledger), 'envelope': ENVELOPE,
        'stage_prefix': prefix, 'exact_request_plan': pre['exact_request_plan']})
    settings, principal = runtime_settings(owner['stage_id'], cloud=True, ledger=ledger)
    success, error, result, totals = False, None, {'case_id': 'frost-A', 'status': 'NOT_STARTED'}, None
    after, current_after, rows = None, None, []
    try:
        async with assemble(settings, principal=principal) as app:
            registry = app.registry_for_context()
            async def audit():
                try:
                    require(await historical.history_snapshot(app, prefix) == pre['before'],
                            'S85_HISTORY_OR_ROLES_CHANGED_STOP')
                    current = await current_case(app, registry, case, prefix, principal, pre['registry_roles'])
                    require(current == pre['current_case'], 'S85_CURRENT_SOURCES_CHANGED_STOP')
                    return validate_settlements(await historical.provider_rows(app, principal, prefix),
                        read(ledger)['entries'], pre['exact_request_plan'], pre['registry_roles'])
                except (ValueError, KeyError, TypeError) as exc:
                    raise PermissionError(historical.safe_error(exc)) from None
            fence = RequestFence(pre['exact_request_plan'], pre['registry_roles'], pre['model_config_sha256'],
                audit=audit, held_tokens=lambda: sum(row['held_tokens'] for row in read(ledger)['entries']))
            context = historical.case_context(case, principal, prefix)
            state = historical.build_state(case, pre['registry_roles'])
            fence.bind(context, state, pre['current_case'])
            original = canonical({name: [asdict(row) if row is not None else None for row in getattr(state, name)]
                                  for name in ('search_results', 'rerank_results', 'merge_results')})
            started = time.monotonic()
            result['status'] = 'RUNNING'
            try:
                await rescue_bridge(state, context, LeasedKnowledgeRepository(app, registry), FencedRegistry(registry, fence))
                require(not any(row.get('reason') == 'VALIDATION_OR_OPTIONAL_MODEL_FAILURE' for row in state.bridge_trace),
                        'S85_EXTRACTION_OR_RESPONSE_CONTRACT_STOP')
                require(original == canonical({name: [asdict(row) if row is not None else None for row in getattr(state, name)]
                    for name in ('search_results', 'rerank_results', 'merge_results')}), 'S85_NORMAL_PIPELINE_MUTATED')
                finalized = await into_chat_message(state, context)
                require(tuple(row.id for row in context_results(state)) == finalized.result_ids,
                        'S85_CONTEXT_SOURCE_MAPPING_MISMATCH')
                result.update(status='DIAGNOSTIC_COMPLETE', rescue_ids=[row.id for row in state.rescue_evidence],
                              finalized_context=asdict(finalized), generation_called=False)
                totals = await audit()
                success = True
            except BaseException as exc:
                result.update(status='STOP', error=historical.safe_error(exc))
                raise
            finally:
                result.update(trace=state.bridge_trace, elapsed_seconds=time.monotonic()-started)
                write_new(root / 'cases/frost-A.json', result)
                await registry.close()
    except BaseException as exc:
        error = historical.safe_error(exc)
    finally:
        historical.close_ledger(ledger, success=success)
        zero, who = runtime_settings(owner['stage_id'])
        try:
            async with assemble(zero, principal=who) as app:
                after = await historical.history_snapshot(app, prefix)
                rows = await historical.provider_rows(app, who, prefix)
                registry = app.registry_for_context()
                try:
                    current_after = await current_case(app, registry, case, prefix, who, pre['registry_roles'])
                finally:
                    await registry.close()
            preserved = (after == pre['before'] and current_after == pre['current_case']
                         and historical.storage_marker_identity(zero) == pre['storage_marker'])
        except BaseException as exc:
            preserved = False
            error = error or historical.safe_error(exc)
        entries = read(ledger)['entries']
        summary = execution_summary(entries)
        final = {'status': 'S85_REAL_DIAGNOSTIC_COMPLETE' if success and preserved else 'S85_REAL_STOP',
                 'error': error, 'case': result, 'envelope': ENVELOPE, 'provider_rows': rows,
                 'execution_entries': entries, 'usage_by_capability': totals,
                 **summary, 'provider_receipt_count': len(rows),
                 'old_history_roles_models_source_counts_preserved': preserved,
                 'before': pre['before'], 'after_excluding_new': after, 'current_sources_after': current_after,
                 'ledger_status': read(ledger)['status'], 'receipt_files': receipt_manifest(execution_dir),
                 'final_answer_generation_calls': 0, 'embedding_calls': 0, 'frost_B_calls': 0,
                 'production_configuration_changed': False, 'deployment_performed': False,
                 'generated_answer_verified': False, 'headers_saved': False}
        write_new(root / 'final.json', final)
        write_new(root / 'authorization-closure.json', {'status': read(ledger)['status'],
            **summary, 'actual_reported_tokens': summary['all_actual_tokens'],
            'reported_subtotal': summary['actual_reported_tokens_subtotal'],
            'unused_call_allowance_revoked': MAX_CALLS-summary['actual_calls'] if summary['actual_calls'] is not None else None,
            'unused_operation_allowance_revoked': MAX_CALLS-summary['attempted_operations'],
            'unheld_token_allowance_revoked': MAX_TOKENS-summary['held_tokens_total'] if summary['held_tokens_total'] is not None else None,
            'held_reservations_released': False,
            'historical_authorizations_reused': False, 'frost_B_authorized': False})
        print(canonical({key: final[key] for key in ('status', 'all_actual_tokens', 'new_unknown', 'ledger_status')}), flush=True)
    return final['status'] == 'S85_REAL_DIAGNOSTIC_COMPLETE'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('prepare', 'run'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == 'prepare':
        result = asyncio.run(prepare(root))
        print(canonical({'status': result['status'], 'envelope': result['envelope'],
                         'new_provider_calls': 0, 'actual_tokens_added': 0}), flush=True)
    else:
        raise SystemExit(0 if asyncio.run(run(root)) else 2)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(canonical({'status': 'S85_STOP', 'error': historical.safe_error(exc)}), flush=True)
        raise SystemExit(2)
