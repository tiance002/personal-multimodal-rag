"""Fresh bounded S8.5 diagnostic. Importing this module never connects.

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
SESSION_ID = 's85-continuation-20261021'
SESSION_ENVELOPE = {'max_calls': 40, 'max_tokens': 200000,
                    'max_bridge_attempts': 6, 'max_conversation_turns': 7}
BRIDGE_SESSION_CALLS = 12
BRIDGE_SESSION_TOKENS = 60000

require, canonical, sha = historical.require, historical.canonical, historical.sha
read, write_new, safe_write = historical.read, historical.write_new, historical.safe_write


def validate_inputs(raw, case_id='frost-A'):
    require(isinstance(raw, bytes) and sha(raw) == FROZEN_SHA256, 'S85_FROZEN_INPUT_BYTES_CHANGED')
    payload = json.loads(raw.decode('utf-8-sig'))
    require(sha(canonical(payload)) == CANONICAL_SHA256, 'S85_FROZEN_CANONICAL_INPUT_CHANGED')
    cases = historical.validate_payload(payload)
    require(case_id in ('frost-A', 'frost-B'), 'S85_FROST_CASE_REQUIRED')
    selected = [case for case in cases if case['case_id'] == case_id]
    require(len(selected) == 1 and selected[0].get('arm') == case_id[-1], 'S85_EXACT_FROST_CASE_REQUIRED')
    return deepcopy(selected[0])


def attempt_case(owner):
    return owner.get('case_id') if owner.get('schema') == 's85-real-authorization/v2' else 'frost-A'


def attempt_envelope(owner):
    envelope = deepcopy(ENVELOPE)
    envelope['case_ids'] = [attempt_case(owner)]
    return envelope


def validate_owner(owner, raw):
    require(isinstance(owner, dict), 'S85_NEW_EXPLICIT_OWNER_APPROVAL_REQUIRED')
    schema = owner.get('schema')
    validate_inputs(raw, attempt_case(owner))
    require(schema in ('s85-real-authorization/v1', 's85-real-authorization/v2')
            and isinstance(owner.get('approval_source'), str) and owner['approval_source'].strip()
            and isinstance(owner.get('owner_message'), str) and owner['owner_message'].strip()
            and owner.get('approved') is True, 'S85_NEW_EXPLICIT_OWNER_APPROVAL_REQUIRED')
    if schema == 's85-real-authorization/v1':
        require(owner['approval_source'] == 'Owner explicit current messages', 'S85_NEW_EXPLICIT_OWNER_APPROVAL_REQUIRED')
    else:
        require(owner.get('authorization_basis') == 'current-owner-continuation'
                and owner.get('session_id') == SESSION_ID and owner.get('session_envelope') == SESSION_ENVELOPE,
                'S85_CONTINUATION_SESSION_REQUIRED')
        index = owner.get('attempt_index')
        require(type(index) is int and 1 <= index <= SESSION_ENVELOPE['max_bridge_attempts']
                and isinstance(owner.get('prior_receipts'), list)
                and len(owner['prior_receipts']) == index-1, 'S85_CONTINUATION_INDEX_REQUIRED')
        require(isinstance(owner.get('continuation_reason'), str) and owner['continuation_reason'].strip(),
                'S85_CONTINUATION_REASON_REQUIRED')
    require(owner.get('envelope') == attempt_envelope(owner), 'S85_APPROVED_ENVELOPE_CHANGED')
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


def request_plan(prefix, case_id='frost-A'):
    require(isinstance(prefix, str) and re.fullmatch(r's85-[0-9]{8}-[0-9a-f]{32}/', prefix),
            'S85_EXACT_REQUEST_PREFIX_REQUIRED')
    require(case_id in ('frost-A', 'frost-B'), 'S85_FROST_CASE_REQUIRED')
    return {prefix + case_id + '/bridge-extract': 'chat', prefix + case_id + '/bridge-rerank': 'rerank'}


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
    return {'case_id': case['case_id'], 'payload': payload, 'payload_sha256': sha(canonical(payload)),
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


def load_prior_receipts(owner):
    """Read immutable receipts at fixed prior stage paths; never open ledgers."""
    records = []
    files = {'final': 'final.json', 'closure': 'authorization-closure.json',
             'preflight': 'preflight.json', 'authorization': 'payload/owner-authorization.json'}
    for reference in owner.get('prior_receipts', []):
        require(isinstance(reference, dict) and set(reference) ==
                {'stage_id', *(name + '_sha256' for name in files)}, 'S85_PRIOR_RECEIPT_REFERENCE_INVALID')
        stage = reference['stage_id']
        require(isinstance(stage, str) and re.fullmatch(r'[0-9]{8}-[0-9a-f]{32}', stage),
                'S85_PRIOR_STAGE_REQUIRED')
        prior_root = Path(ROOT_PREFIX + stage)
        validate_root(prior_root, stage)
        require(prior_root.resolve() == prior_root, 'S85_PRIOR_STAGE_REDIRECT_DENIED')
        record = {'stage_id': stage}
        for name, relative in files.items():
            path = prior_root / relative
            require(path.is_file() and not path.is_symlink() and prior_root in path.resolve().parents,
                    'S85_PRIOR_RECEIPT_MISSING')
            raw = path.read_bytes()
            require(sha(raw) == reference[name + '_sha256'], 'S85_PRIOR_RECEIPT_HASH_CHANGED')
            record[name] = json.loads(raw.decode('utf-8-sig'))
        records.append(record)
    return records


def _verified_a(record):
    final = record['final']
    summary = execution_summary(final['execution_entries'])
    trace = final.get('case', {}).get('trace', [])
    decisions = {item.get('decision') for item in trace}
    return bool(final.get('status') == 'S85_REAL_DIAGNOSTIC_COMPLETE'
                and final.get('ledger_status') == 'CLOSED'
                and final.get('case', {}).get('case_id') == 'frost-A'
                and summary['new_unknown'] == 0 and summary['settled_response_count'] == 2
                and {entry['capability'] for entry in final['execution_entries']} == {'chat', 'rerank'}
                and {'FIRST_HOP_VERIFIED', 'BRIDGE_QUERY_VERIFIED', 'SECOND_HOP_LOCATION_FOUND',
                     'CONTEXT_ADMITTED'} <= decisions
                and any(item.get('decision') == 'FIRST_HOP_VERIFIED'
                        and item.get('extraction', {}).get('relation_verified') is True
                        and item.get('identity_proof', {}).get('verified') is True for item in trace)
                and record['preflight'].get('current_case', {}).get('payload', {}).get('contract_version') == CONTRACT)


def validate_continuation(owner, records, current_implementation, raw):
    """Account only fresh session receipts; unknown exposure remains held."""
    if owner.get('schema') != 's85-real-authorization/v2':
        return None
    validate_owner(owner, raw)
    require(len(records) == owner['attempt_index'] - 1, 'S85_PRIOR_RECEIPT_COUNT_CHANGED')
    stages, exposure, operations, unknown, permission_stops = set(), 0, 0, 0, []
    for index, record in enumerate(records, 1):
        reference = owner['prior_receipts'][index-1]
        previous = record['authorization']
        prefix = validate_owner(previous, raw)
        require(record['stage_id'] == reference['stage_id'] == previous['stage_id']
                and record['stage_id'] != owner['stage_id'] and record['stage_id'] not in stages,
                'S85_PRIOR_STAGE_REUSE_DENIED')
        stages.add(record['stage_id'])
        require(previous.get('session_id') == SESSION_ID and previous.get('session_envelope') == SESSION_ENVELOPE
                and previous.get('attempt_index') == index
                and previous.get('prior_receipts') == owner['prior_receipts'][:index-1],
                'S85_PRIOR_SESSION_CHAIN_CHANGED')
        final, closure, preflight = record['final'], record['closure'], record['preflight']
        require(final.get('status') in ('S85_REAL_DIAGNOSTIC_COMPLETE', 'S85_REAL_STOP')
                and final.get('ledger_status') in ('CLOSED', 'STOP')
                and final.get('final_answer_generation_calls') == 0 and final.get('embedding_calls') == 0
                and closure.get('status') == final['ledger_status']
                and closure.get('historical_authorizations_reused') is False
                and preflight.get('owner_authorization_sha256') == sha(canonical(previous))
                and preflight.get('source_inputs_sha256') == FROZEN_SHA256
                and final.get('envelope') == attempt_envelope(previous), 'S85_PRIOR_RECEIPT_CONTRACT_CHANGED')
        entries = final.get('execution_entries')
        require(isinstance(entries, list) and len(entries) <= MAX_CALLS, 'S85_PRIOR_ENTRIES_INVALID')
        plan = request_plan(prefix, attempt_case(previous))
        keys = [(entry['request_id'], entry['capability']) for entry in entries]
        require(len(keys) == len(set(keys)) and all(plan.get(rid) == capability for rid, capability in keys),
                'S85_PRIOR_OPERATION_ID_CHANGED')
        summary = execution_summary(entries)
        require(all(final.get(key) == value and closure.get(key) == value for key, value in summary.items())
                and summary['held_tokens_total'] is not None
                and summary['held_tokens_total'] <= MAX_TOKENS, 'S85_PRIOR_ACCOUNTING_CHANGED')
        operations += summary['attempted_operations']
        exposure += summary['held_tokens_total']
        unknown += summary['possibly_sent_unknown_operations']
        code = final.get('error') or ''
        if code in {'MODEL_ORIGIN_KB_ACCESS_DENIED', 'MODEL_ORIGIN_KB_EGRESS_DENIED', 'MODEL_CLOUD_DISABLED',
                    'MODEL_DOCUMENT_ACCESS_DENIED', 'MODEL_OWNER_MISMATCH', 'LOCAL_EXECUTION_SCOPE_DENIED'}:
            permission_stops.append({'stage_id': record['stage_id'], 'error': code})
    require(operations + MAX_CALLS <= BRIDGE_SESSION_CALLS
            and exposure + MAX_TOKENS <= BRIDGE_SESSION_TOKENS, 'S85_BRIDGE_SESSION_BOUND_EXCEEDED')
    if unknown or permission_stops:
        require(isinstance(owner.get('prior_stop_diagnosis'), str) and owner['prior_stop_diagnosis'].strip(),
                'S85_PRIOR_STOP_DIAGNOSIS_REQUIRED')
    previous_same_case = [record for record in records if attempt_case(record['authorization']) == attempt_case(owner)]
    fresh_transport_diagnostic = False
    if previous_same_case:
        latest = previous_same_case[-1]
        fresh_transport_diagnostic = bool(execution_summary(latest['final']['execution_entries'])[
            'possibly_sent_unknown_operations'] and isinstance(owner.get('prior_stop_diagnosis'), str)
            and owner['prior_stop_diagnosis'].strip())
        old = latest['preflight'].get('implementation_sha256', {})
        require(fresh_transport_diagnostic or any(
                    path != 'scripts/s8_5_bridge_ecs.py' and path in old and old[path] != value
                    for path, value in current_implementation.items()), 'S85_REPAIR_IMPLEMENTATION_CHANGE_REQUIRED')
    if attempt_case(owner) == 'frost-B':
        require(any(_verified_a(record) for record in records), 'S85_CONDITIONAL_B_REQUIRES_VERIFIED_A')
    return {'session_id': SESSION_ID, 'session_envelope': SESSION_ENVELOPE,
            'bridge_subbudget': {'max_calls': BRIDGE_SESSION_CALLS, 'max_tokens': BRIDGE_SESSION_TOKENS,
                                 'max_attempts': SESSION_ENVELOPE['max_bridge_attempts']},
            'prior_attempts': len(records), 'prior_attempted_operations': operations,
            'prior_held_token_exposure': exposure, 'prior_possibly_sent_unknown': unknown,
            'prior_permission_stops': permission_stops, 'prior_ledgers_modified': False,
            'continuation_mode': 'FRESH_TRANSPORT_DIAGNOSTIC' if fresh_transport_diagnostic else 'NEW_CASE_OR_IMPLEMENTATION_REPAIR',
            'next_case_id': attempt_case(owner), 'next_attempt_index': owner['attempt_index']}


async def prepare(root, *, write=True):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    raw = (root / 'payload/inputs.json').read_bytes()
    owner = read(root / 'payload/owner-authorization.json')
    prefix = validate_owner(owner, raw)
    case = validate_inputs(raw, attempt_case(owner))
    validate_root(root, owner['stage_id'])
    plan = request_plan(prefix, case['case_id'])
    implementation = implementation_hashes()
    continuation = validate_continuation(owner, load_prior_receipts(owner), implementation, raw)
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
    result = {'status': 'S85_READY_ZERO_SEND', 'envelope': attempt_envelope(owner),
              'source_inputs_sha256': sha(raw), 'canonical_inputs_sha256': CANONICAL_SHA256,
              'owner_authorization_sha256': sha(canonical(owner)), 'stage_prefix': prefix,
              'exact_request_plan': plan, 'registry_roles': roles_out, 'role_revision': revision,
              'model_config_sha256': config_hashes, 'implementation_sha256': implementation,
              'identity': identity, 'subject_sha256': sha(principal.subject_id),
              'storage_marker': historical.storage_marker_identity(settings),
              'before': before, 'current_case': current, 'cheap_reservation_tokens': reserve,
              'new_provider_calls': 0, 'actual_tokens_added': 0}
    if continuation is not None:
        result['continuation'] = continuation
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


def safe_failure(error):
    """Capture typed metadata only; sent is reported, never proof of no send."""
    name = type(error).__name__
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', name):
        name = 'Exception'
    code = getattr(error, 'code', None)
    if code is None and isinstance(error, (PermissionError, ValueError)) and len(error.args) == 1:
        code = error.args[0]
    if not isinstance(code, str) or not re.fullmatch(
            r'(?:S85_|LOCAL_EXECUTION_|MODEL_|PROVIDER_|RUN_DEADLINE_)[A-Z0-9_]{1,100}', code):
        code = None
    sent, status = getattr(error, 'sent', None), getattr(error, 'status_code', None)
    return {'type': name, 'code': code, 'sent': sent if type(sent) is bool else None,
            'http_status': status if type(status) is int and 100 <= status <= 599 else None}


class RequestFence:
    """Exact operation, scope, current sentence mapping and final wire fence."""
    def __init__(self, plan, roles, config_hashes, *, audit=None, held_tokens=lambda: 0):
        validate_roles(roles)
        identities = [re.fullmatch(r'(s85-[0-9]{8}-[0-9a-f]{32}/)(frost-[AB])/(bridge-extract|bridge-rerank)', rid)
                      for rid in plan]
        require(identities and all(identities), 'S85_EXACT_PLAN_REQUIRED')
        cases = {(match[1], match[2]) for match in identities}
        require(len(cases) == 1, 'S85_EXACT_PLAN_REQUIRED')
        prefix, self.case_id = next(iter(cases))
        require(plan == request_plan(prefix, self.case_id), 'S85_EXACT_PLAN_REQUIRED')
        self.plan, self.roles, self.config_hashes = dict(plan), deepcopy(roles), dict(config_hashes)
        self.audit, self.held_tokens = audit, held_tokens
        self.seen, self.completed, self.bound, self.stopped = set(), set(), None, False
        self.last_failure, self.last_audit_failure = None, None

    def bind(self, context, state, prepared):
        require(context.request_id + '/bridge-extract' in self.plan
                and prepared['case_id'] == self.case_id, 'S85_BOUND_CASE_REQUIRED')
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
                fence.last_failure = {'request_id': context.request_id,
                                      'capability': kwargs.get('capability'), **safe_failure(exc)}
                raise
            finally:
                try:
                    if fence.audit is not None:
                        await asyncio.shield(fence.audit())
                    if failure is None:
                        fence.completed.add(context.request_id)
                except BaseException as exc:
                    fence.stopped = True
                    fence.last_audit_failure = safe_failure(exc)
                    # Keep the original transport/deadline failure authoritative.
                    # Audit uncertainty is separately recorded and never permits
                    # a retry, reservation release or an unsent inference.
                    if failure is None:
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
    return {'status': 'OPEN', 'scope': 'S8_5_' + case['case_id'].upper().replace('-', '_') + '_NEW_OWNER', 'tenant_id': principal.tenant_id,
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
    owner = read(root / 'payload/owner-authorization.json')
    prefix = validate_owner(owner, raw)
    case = validate_inputs(raw, attempt_case(owner))
    envelope = attempt_envelope(owner)
    validate_root(root, owner['stage_id'])
    pre = read(root / 'preflight.json')
    require(pre == await prepare(root, write=False), 'S85_PREFLIGHT_CHANGED_BEFORE_RUN')
    write_new(root / 'run.claim', {'stage_prefix': prefix, 'envelope': envelope, 'time': time.time()})
    execution_dir = Path(EXECUTION_ROOT) / ('s85-' + owner['stage_id'])
    require(not execution_dir.exists(), 'S85_EXISTING_EXECUTION_NO_RESET')
    execution_dir.mkdir(parents=True, mode=0o700)
    ledger = execution_dir / 'execution.json'
    _, principal = runtime_settings(owner['stage_id'])
    write_new(ledger, ledger_spec(principal, case, pre))
    write_new(root / 'bounded-execution.json', {'path': str(ledger), 'envelope': envelope,
        'stage_prefix': prefix, 'exact_request_plan': pre['exact_request_plan']})
    settings, principal = runtime_settings(owner['stage_id'], cloud=True, ledger=ledger)
    success, error, result, totals = False, None, {'case_id': case['case_id'], 'status': 'NOT_STARTED'}, None
    after, current_after, rows, fence = None, None, [], None
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
                              finalized_context=asdict(finalized), generation_called=False,
                              first_hop_verified=any(item.get('decision') == 'FIRST_HOP_VERIFIED' for item in state.bridge_trace),
                              entity_bridge_verified=any(row.metadata['bridge_rescue'].get('entity_bridge_verified') is True
                                                         for row in state.rescue_evidence),
                              final_answer_supported=any(row.metadata['bridge_rescue'].get('final_answer_supported') is True
                                                         for row in state.rescue_evidence),
                              generated_answer_verified=False)
                totals = await audit()
                success = True
            except BaseException as exc:
                result.update(status='STOP', error=historical.safe_error(exc))
                raise
            finally:
                result.update(trace=state.bridge_trace, elapsed_seconds=time.monotonic()-started,
                              transport_failure=fence.last_failure, post_transport_audit_failure=fence.last_audit_failure)
                write_new(root / 'cases' / (case['case_id'] + '.json'), result)
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
                 'error': error, 'case': result, 'envelope': envelope, 'provider_rows': rows,
                 'transport_failure': fence.last_failure if fence is not None else None,
                 'post_transport_audit_failure': fence.last_audit_failure if fence is not None else None,
                 'execution_entries': entries, 'usage_by_capability': totals,
                 **summary, 'provider_receipt_count': len(rows),
                 'old_history_roles_models_source_counts_preserved': preserved,
                 'before': pre['before'], 'after_excluding_new': after, 'current_sources_after': current_after,
                 'ledger_status': read(ledger)['status'], 'receipt_files': receipt_manifest(execution_dir),
                 'final_answer_generation_calls': 0, 'embedding_calls': 0,
                 'frost_B_calls': summary['actual_calls'] if case['case_id'] == 'frost-B' else 0,
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
            'historical_authorizations_reused': False, 'frost_B_authorized': case['case_id'] == 'frost-B'})
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
