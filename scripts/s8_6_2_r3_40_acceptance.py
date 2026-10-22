"""Closed ECS40 HTTP acceptance adapter; no grant creation or automatic retries.

This test-only composition reuses the formal Linux runtime, the R3 profile,
Provider Adapter and LocalExecution. Gold never enters this input manifest.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import s8_6_1_api_acceptance as prior
from scripts.s8_6_4_language import expected_profile_language, message_headers as _language_headers, policy_manifest
from backend.weknora_port import execution as execution_module
from backend.weknora_port.models.types import ProviderError

GOAL = 'S8.6.2-R3-ECS40'
PROFILE_ID = 's8.6.2-r3-ecs40-prompt-b/v2'
INPUT_SCHEMA = 's862r3-ecs40-input/v1'
GRANT_SCHEMA = 's862r3-ecs40-owner-grant/v2'
OWNER_TOKEN_MODE = 'OWNER_TOKEN_CAP_V1'
OWNER_TOKEN_CAP = 500000
ORIGINAL_INPUT_IDENTITY_SHA256 = '18ecc8ba164dcfad8fe69136c9300eb5f1f9ebdce963efce20c7f2dd592770ef'
ORIGINAL_COHORT_SHA256 = '0e2f124015d9589dbe41497d7378362bdc0d009a295905a62809180fe4f4e746'
PROTECTED = frozenset(('scifact-339', 'hotpotqa-5ae3992d5542994393b9e722'))
DATASETS = ('scifact', 'miracl-zh', 'longbench-zh', 'hotpotqa', 'mmlongbench-doc')
IDENTITY_FIELDS = ('case_id', 'dataset', 'qid', 'question', 'knowledge_base_id', 'document_scope')
require, digest, write_new = prior.require, prior.digest, prior.write_new


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def case_scope(case):
    return {'knowledge_base_ids': [case['knowledge_base_id']], 'document_ids': []}


def profile_request_headers(q0, base=None, explicit_override=None):
    headers = _language_headers(base or {}, q0, explicit_override)
    expected, _ = expected_profile_language(q0, explicit_override)
    require(headers.get('Accept-Language') == expected, 'S864_REQUEST_LANGUAGE_HEADER_MISMATCH')
    return headers


def profile_manifest():
    return {'schema': 's862r3-ecs40-profile/v1', 'profile': PROFILE_ID, 'goal': GOAL,
        'composition_profile': prior.BOUNDED_PROFILE_ID,
        'composition_profile_sha256': digest(prior.profile_manifest(bounded_query_profile=True)),
        'composition_implementation_sha256_LF': digest(Path(prior.__file__).read_bytes().replace(b'\r\n', b'\n')),
        'adapter_implementation_sha256_LF': digest(Path(__file__).read_bytes().replace(b'\r\n', b'\n')),
        'input_identity_sha256': ORIGINAL_INPUT_IDENTITY_SHA256,
        'original_cohort_sha256': ORIGINAL_COHORT_SHA256,
        'protected_case_ids': sorted(PROTECTED), 'denominator': 40, 'eligible_submissions': 38,
        'answer_language_policy': policy_manifest(),
        'prompt_b_suffix_sha256': digest(prior.PROMPT_B_SUFFIX.encode()),
        'query_input_bound': dict(prior.PROFILE_QUERY_BOUND), 'generation': 'existing P5 DYNAMIC',
        'scope': 'one frozen whole KB per case; no document Gold filter',
        'source_evidence_mode': 'bounded_source_v1', 'retrieval_strategy_mode': 'query_structure_v1',
        'query_expansion': False, 'citations_enabled': True, 'graph': False, 'rerank_top1_floorless': True,
        'authority_mode': OWNER_TOKEN_MODE, 'owner_max_total_tokens': OWNER_TOKEN_CAP,
        'unlimited_provider_calls': True, 'operational_child_max_calls': 12,
        'operational_child_max_tokens': 100000,
        'shared_owner_cap_over_child_ledgers': True, 'numerical_budget_preflight': 'NOT_USED_OWNER_WAIVER',
        'retry': False, 'new_session_per_case': True, 'worker_started': False,
        'actual_provider_calls': 0}


def validate_inputs(value):
    require(value.get('schema') == INPUT_SCHEMA and isinstance(value.get('cases'), list),
            'S840_FROZEN_INPUT_REQUIRED')
    rows = value['cases']
    require(len(rows) == 40 and len({r.get('case_id') for r in rows}) == 40,
            'S840_ORIGINAL_FORTY_REQUIRED')
    require(Counter(r.get('dataset') for r in rows) == Counter({name: 8 for name in DATASETS}),
            'S840_ORIGINAL_DATASET_COUNTS_REQUIRED')
    allowed = set(IDENTITY_FIELDS) | {'status', 'dataset_version', 'question_sha256'}
    for row in rows:
        require(set(row) == allowed and all(isinstance(row.get(k), str) and row[k]
            for k in IDENTITY_FIELDS if k != 'document_scope') and row['document_scope'] == [],
            'S840_CLOSED_NON_GOLD_INPUT_REQUIRED')
        require(isinstance(row['dataset_version'], str) and row['dataset_version']
            and row['question_sha256'] == digest(row['question'].encode()), 'S840_QUESTION_HASH_MISMATCH')
        expected = 'NOT_RUN_LEGACY_UNKNOWN_PROTECTED' if row['case_id'] in PROTECTED else 'ELIGIBLE'
        require(row['status'] == expected, 'S840_PROTECTED_CASE_STATUS_MISMATCH')
        require(prior.query_input_bound(row['question'])['valid'], 'PROFILE_INPUT_BOUND_EXCEEDED')
    identities = sorted(({k: r[k] for k in IDENTITY_FIELDS} for r in rows), key=lambda r: r['case_id'])
    require(digest(identities) == ORIGINAL_INPUT_IDENTITY_SHA256, 'S840_ORIGINAL_INPUT_IDENTITY_CHANGED')
    return value


def _hex(value, length=64):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def approved(environment):
    """Validate the new Owner token cap and closed fixture; no budget preflight."""
    grant_path = environment.get('S840_AUTHORIZATION_FILE', '')
    require(bool(grant_path), 'S840_NEW_OWNER_AUTHORIZATION_REQUIRED')
    grant = read(grant_path)
    require(grant.get('schema') == GRANT_SCHEMA and grant.get('goal') == GOAL
        and grant.get('owner_approved') is True and _hex(grant.get('owner_approval_evidence_sha256'))
        and grant.get('authority_mode') == OWNER_TOKEN_MODE
        and grant.get('unlimited_provider_calls') is True and grant.get('max_total_tokens') == OWNER_TOKEN_CAP
        and grant.get('profile_sha256') == digest(profile_manifest()), 'S840_NEW_OWNER_AUTHORIZATION_REQUIRED')
    revision, image = environment.get('S840_SOURCE_REVISION'), environment.get('S840_IMAGE_ID')
    require(_hex(revision, 40) and grant.get('source_revision') == revision,
            'S840_SOURCE_REVISION_MISMATCH')
    require(isinstance(image, str) and image.startswith('sha256:') and _hex(image[7:])
        and grant.get('image_id') == image, 'S840_IMAGE_ID_MISMATCH')
    expiry = datetime.fromisoformat(grant.get('expires_at', '').replace('Z', '+00:00'))
    require(expiry.tzinfo is not None and expiry > datetime.now(timezone.utc), 'S840_AUTH_EXPIRED')
    inputs_path = environment.get('S840_FROZEN_INPUT_FILE', '')
    inputs = validate_inputs(read(inputs_path))
    require(grant.get('frozen_inputs_sha256') == digest(Path(inputs_path).read_bytes()),
            'S840_FROZEN_INPUT_FILE_MISMATCH')
    inventory_path = environment.get('S840_CURRENT_INVENTORY_FILE', '')
    require(inventory_path and _hex(grant.get('inventory_sha256'))
        and digest(Path(inventory_path).read_bytes()) == grant['inventory_sha256'], 'S840_CURRENT_INVENTORY_MISMATCH')
    models = grant.get('model_ids', {})
    require(set(models) == {'cheap', 'expensive', 'embedding', 'rerank'}
        and all(isinstance(v, str) and v for v in models.values()), 'S840_MODEL_BINDINGS_REQUIRED')
    eligible = {r['case_id']: r for r in inputs['cases'] if r['status'] == 'ELIGIBLE'}
    batches = grant.get('batches', {})
    require(set(batches) == set(eligible) and len(batches) == 38, 'S840_EXACT_ELIGIBLE_SCOPE_REQUIRED')
    owner_lock = Path(grant.get('owner_lock_file', ''))
    require(owner_lock.is_absolute() and owner_lock.name == 'owner-token.lock', 'S840_OWNER_LOCK_PATH_REQUIRED')
    require(not owner_lock.with_name('owner-stop.json').exists(), 'S840_OWNER_SCOPE_STOPPED')
    request_ids, paths = set(), set()
    selected = environment.get('S840_BATCH_ID')
    require(selected in eligible, 'S840_PROTECTED_OR_UNKNOWN_CASE_DENIED')
    for identifier, batch in batches.items():
        case = eligible[identifier]
        scope = case_scope(case)
        require(batch.get('case_id') == identifier and batch.get('scope') == scope
            and batch.get('scope_sha256') == digest(scope),
            'S840_CASE_SCOPE_MISMATCH')
        require(type(batch.get('max_calls')) is int and 0 < batch['max_calls'] <= 12
            and type(batch.get('max_tokens')) is int and 0 < batch['max_tokens'] <= 100000,
            'S840_EXISTING_CHILD_LIMIT_REQUIRED')
        require(batch.get('model_ids') == models and batch.get('qu_model_id') == models['cheap']
            and batch.get('question_sha256') == digest(case['question'].encode()), 'S840_CASE_MODEL_OR_Q0_MISMATCH')
        request_id = batch.get('request_id', '')
        require(isinstance(request_id, str) and re.fullmatch(r's840-[0-9a-f]{24}', request_id)
            and request_id not in request_ids, 'S840_FRESH_CLIENT_ID_REQUIRED')
        request_ids.add(request_id)
        require(isinstance(batch.get('qu_request_sha256s'), dict)
            and set(batch['qu_request_sha256s']) == {request_id}
            and _hex(batch['qu_request_sha256s'][request_id]), 'S840_EXACT_QU_BODY_IDENTITY_REQUIRED')
        file = Path(batch.get('execution_file', ''))
        require(file.is_absolute() and str(file) not in paths and file.name == 'execution.json',
                'S840_UNIQUE_LEDGER_REQUIRED')
        paths.add(str(file))
        ledger = read(file)
        require(ledger.get('status') in ('OPEN', 'CLOSED')
            and all(e.get('status') == 'SETTLED' for e in ledger.get('entries', [])),
            'S840_UNKNOWN_OR_UNSETTLED_STOP')
        if identifier != selected:
            from backend.weknora_port.execution import _validate_ledger
            _validate_ledger(ledger)
            continue
        require(str(file) == environment.get('WEKNORA_PORT_EXECUTION_FILE')
            and ledger.get('status') == 'OPEN' and ledger.get('entries') == []
            and ledger.get('max_calls') == batch['max_calls'] and ledger.get('max_tokens') == batch['max_tokens']
            and ledger.get('tenant_id') == 332 and ledger.get('capture_evidence') is True
            and ledger.get('allowed_kbs') == scope['knowledge_base_ids']
            and ledger.get('allowed_models') == batch.get('allowed_models')
            and ledger.get('allowed_request_prefixes') == ['*'], 'S840_SELECTED_LEDGER_MISMATCH')
        allowed_models = batch.get('allowed_models', [])
        require(len(allowed_models) == 4 and {r.get('model_id') for r in allowed_models} == set(models.values())
            and all(r.get('provider') and r.get('model') and r.get('capability') in ('chat', 'embedding', 'rerank')
                    for r in allowed_models), 'S840_MODEL_ALLOWLIST_REQUIRED')
        from backend.weknora_port.execution import _validate_ledger
        _validate_ledger(ledger)
    require(sum(e['held_tokens'] for b in batches.values() for e in read(b['execution_file'])['entries'])
        <= OWNER_TOKEN_CAP, 'S840_OWNER_TOKEN_CAP_EXCEEDED')
    return grant, eligible[selected], None


def install_case_profile(runtime, case, batch, expires_at, evidence_directory=None):
    """Use the R3 guards with only a closed per-case scope projection."""
    approved_batch = {**batch, 'turns': [{'case': case['case_id'], 'request_id': batch['request_id'],
        'question': case['question'], 'mode': 'query_structure_v1'}]}
    prior.install_profile(runtime, evidence_directory, approved_batch=approved_batch,
        expires_at=expires_at, bounded_query_profile=True, approved_scope=case_scope(case))
    composed = runtime.ordinary.configuration
    async def configuration(turn):
        result = await composed(turn)
        state = result.pipeline_state
        require(state.source_evidence_mode == 'bounded_source_v1' and not state.enable_expansion
            and not state.graph_enabled and state.rerank_top1_floorless is True and state.bridge_rescue_mode == 'off',
            'S840_SAVED_PRODUCTION_BOUNDARIES_CHANGED')
        require([t.knowledge_base_id for t in state.targets] == [case['knowledge_base_id']]
            and all(t.target_type == 'knowledge_base' and not t.knowledge_ids
                and not t.tag_ids and not t.scope_tag_ids for t in state.targets), 'S840_WHOLE_KB_TARGET_REQUIRED')
        return result
    runtime.ordinary.configuration = configuration


@contextmanager
def owner_process_lock(path):
    """One candidate API process for this complete Owner scope; fail on overlap."""
    file = Path(path)
    require(file.is_absolute(), 'S840_OWNER_LOCK_PATH_REQUIRED')
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell(): lock.write(b'0'); lock.flush()
        lock.seek(0)
        acquired = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        except OSError as exc:
            if not acquired: raise ValueError('S840_PARALLEL_API_PROCESS_DENIED') from None
            raise
        finally:
            if acquired:
                lock.seek(0)
                if os.name == 'nt': msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def owner_occupancy(grant, selected_execution):
    """Actual settled totals plus retained occupancy; never resets a ledger."""
    mode = grant.get('accounting_mode', execution_module.RESERVATION_MODE)
    require(mode in (execution_module.RESERVATION_MODE, execution_module.REPORTED_USAGE_ONLY),
            'S864_ACCOUNTING_MODE_MISMATCH')
    reported_only = mode == execution_module.REPORTED_USAGE_ONLY
    if reported_only:
        require(grant.get('owner_max_total_attempts') == 40, 'S864_OWNER_ATTEMPT_CAP_REQUIRED')
    total = 0
    for batch in grant['batches'].values():
        path = Path(batch['execution_file'])
        data = read(path)
        require(data.get('accounting_mode', execution_module.RESERVATION_MODE) == mode,
                'S864_ACCOUNTING_MODE_MISMATCH')
        execution_module._validate_ledger(data)
        require(data['status'] != 'STOP', 'S840_UNKNOWN_OR_UNSETTLED_STOP')
        for row in data['entries']:
            if reported_only:
                require(row['status'] == 'SETTLED', 'S840_UNKNOWN_OR_UNSETTLED_STOP')
                require(execution_module._reported_usage_valid(row.get('usage')),
                        'S864_REPORTED_USAGE_REQUIRED')
                total += row['usage']['total_tokens']
                continue
            require(row['status'] in ('SETTLED', 'INFLIGHT'), 'S840_UNKNOWN_OR_UNSETTLED_STOP')
            require(row['status'] == 'SETTLED' or path.resolve() == Path(selected_execution).resolve(),
                'S840_OTHER_CASE_INFLIGHT_STOP')
            total += row['held_tokens']
    return total


def install_owner_cap(runtime, grant, execution_file):
    """Wrap admission after normal LocalExecution installation; no Adapter bypass."""
    import asyncio
    import inspect
    require(not getattr(runtime, '_s840_owner_cap_installed', False), 'S840_OWNER_CAP_ALREADY_INSTALLED')
    mode = grant.get('accounting_mode', execution_module.RESERVATION_MODE)
    require(mode in (execution_module.RESERVATION_MODE, execution_module.REPORTED_USAGE_ONLY),
            'S864_ACCOUNTING_MODE_MISMATCH')
    original = runtime._make_model
    admission_lock = asyncio.Lock()
    async def make_model(config, key, gate):
        result = original(config, key, gate)
        model = await result if inspect.isawaitable(result) else result
        require(getattr(gate, '_local_execution', None) is runtime.execution
            and runtime.execution.path.resolve() == Path(execution_file).resolve(), 'S840_NORMAL_CHILD_LEDGER_REQUIRED')
        if not getattr(gate, '_s840_owner_cap_installed', False):
            authorize = gate.authorize
            async def bounded_authorize(call):
                async with admission_lock:
                    stop_file = Path(grant['owner_lock_file']).with_name('owner-stop.json')
                    if stop_file.exists(): raise ProviderError('MODEL_SEND_AUTHORIZATION_FAILED')
                    held = owner_occupancy(grant, execution_file)
                    if mode == execution_module.REPORTED_USAGE_ONLY:
                        # New Owner instruction: actual usage only. No request
                        # bytes, output estimate or token reservation is used.
                        attempts = sum(len(read(batch['execution_file'])['entries'])
                            for batch in grant['batches'].values())
                        if held >= OWNER_TOKEN_CAP or attempts >= grant['owner_max_total_attempts']:
                            write_new(stop_file, {'code': 'S840_OWNER_TOKEN_CAP_EXCEEDED'
                                    if held >= OWNER_TOKEN_CAP else 'S864_OWNER_ATTEMPT_CAP_EXCEEDED',
                                'accounting_mode': mode, 'known_reported_tokens': held,
                                'attempts': attempts, 'owner_max_total_attempts': 40,
                                'owner_cap': OWNER_TOKEN_CAP, 'request_id': call.context.request_id,
                                'provider_sent': False, 'automatic_retry': False})
                            raise ProviderError('MODEL_SEND_AUTHORIZATION_FAILED')
                        await authorize(call)
                        return
                    output = execution_module._OUTPUT_BOUND.get()
                    require(type(call.wire_bytes) is int and call.wire_bytes > 0
                        and type(output) is int and output >= 0, 'S840_ACTUAL_REQUEST_BOUND_REQUIRED')
                    reserve = call.wire_bytes + output + 1024
                    if held + reserve > OWNER_TOKEN_CAP:
                        write_new(stop_file, {'code': 'S840_OWNER_TOKEN_CAP_EXCEEDED',
                            'settled_actual_plus_held': held, 'next_wire_reservation': reserve,
                            'owner_cap': OWNER_TOKEN_CAP, 'request_id': call.context.request_id,
                            'provider_sent': False, 'automatic_retry': False})
                        # Existing known denial survives every optional QU or
                        # bridge fallback. The private record retains the exact cause.
                        raise ProviderError('MODEL_SEND_AUTHORIZATION_FAILED')
                    # No other candidate API holds the process lock. This
                    # asyncio lock includes the unchanged child reservation.
                    await authorize(call)
            gate.authorize = bounded_authorize
            gate._s840_owner_cap_installed = True
        return model
    runtime._make_model = make_model
    runtime._s840_owner_cap_installed = True


def create_application(environment=None, *, assemble_factory=None):
    from backend.weknora_port.development import assemble
    from scripts.s8_ecs_runtime import create_application as formal_application
    environment = dict(os.environ if environment is None else environment)
    require(environment.get('WEKNORA_PORT_REDIS_PREFIX', '').startswith('weknora-python-s8:s840:'),
            'S840_ISOLATED_REDIS_PREFIX_REQUIRED')
    require(environment.get('S8_COMPONENT', 'api') == 'api'
        and environment.get('WEKNORA_PORT_CLOUD_ALLOWED', '').lower() == 'true'
        and environment.get('WEKNORA_PORT_GRAPH_ENABLED', 'false').lower() == 'false'
        and environment.get('WEKNORA_PORT_ROUTER_MODE', 'DYNAMIC') == 'DYNAMIC', 'S840_FORMAL_API_ONLY_REQUIRED')
    grant, case, _ = approved(environment)
    batch = grant['batches'][case['case_id']]
    audit = Path(environment.get('S840_AUDIT_DIR', ''))
    require(audit.is_absolute() and audit.parent.resolve() == Path(batch['execution_file']).parent.resolve()
        and audit.name == 'strategy-audit' and not audit.exists(), 'S840_FRESH_AUDIT_DIRECTORY_REQUIRED')
    prior.claim_batch(batch['execution_file'], 'api', digest(profile_manifest()))
    expiry = datetime.fromisoformat(grant['expires_at'].replace('Z', '+00:00'))
    @asynccontextmanager
    async def factory(settings, **kwargs):
        with owner_process_lock(grant['owner_lock_file']):
            async with (assemble_factory or assemble)(settings, **kwargs) as runtime:
                require(not runtime.graph_enabled and runtime.conversation.bridge_rescue_mode == 'off'
                    and runtime.cloud_allowed, 'S840_RUNTIME_DEFAULTS_CHANGED')
                install_owner_cap(runtime, grant, batch['execution_file'])
                install_case_profile(runtime, case, batch, expiry, audit)
                write_new(audit / 'ecs40-profile.json', {'profile': profile_manifest(), 'case_id': case['case_id'],
                    'scope': case_scope(case), 'source_revision': grant['source_revision'], 'image_id': grant['image_id'],
                    'composition_trace_profile': prior.BOUNDED_PROFILE_ID,
                    'answer_language': {'q0': case['question'],
                        'expected': expected_profile_language(case['question'])[0],
                        'source': 'q0_explicit_directive_or_script_default'}})
                yield runtime
    return formal_application(environment, assemble_factory=factory)


async def collect_http(base_url, output, environment=None, *, transport=None):
    """One original frozen question through HTTP. Every failure is terminal."""
    import httpx
    from scripts.s8_ecs_runtime import api_token
    environment = dict(os.environ if environment is None else environment)
    grant, case, _ = approved(environment)
    batch = grant['batches'][case['case_id']]
    prior.claim_batch(batch['execution_file'], 'collector', digest(profile_manifest()))
    parsed = urlsplit(base_url)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.path in ('', '/')
        and not parsed.query and not parsed.fragment and not parsed.username and not parsed.password,
        'S840_LOOPBACK_HTTP_REQUIRED')
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    write_new(target / 'input.json', {'case': case, 'request_id': batch['request_id'],
        'profile_sha256': grant['profile_sha256'], 'source_revision': grant['source_revision'],
        'image_id': grant['image_id'], 'authorization_file_sha256': digest(Path(environment['S840_AUTHORIZATION_FILE']).read_bytes()),
        'query_type_injected': False, 'gold_sent': False})
    headers = {'Authorization': 'Bearer ' + api_token(environment),
        'Origin': 'http://127.0.0.1:5173'}
    result = None
    try:
        async with httpx.AsyncClient(base_url=base_url.rstrip('/'), headers=headers,
                timeout=httpx.Timeout(300, connect=10), transport=transport) as client:
            require(prior.ledger_status(batch['execution_file'])['unsettled'] == 0, 'S840_UNSETTLED_BEFORE_POST')
            session_body = {'title': 'ECS40 ' + case['case_id'][:100],
                'knowledge_base_scope': [case['knowledge_base_id']], 'document_scope': [],
                'source_evidence_mode': 'bounded_source_v1', 'retrieval_strategy_mode': 'query_structure_v1'}
            session = await client.post('/api/v1/sessions', json=session_body)
            write_new(target / 'session.json', {'status': session.status_code, 'body': session.json()})
            require(session.status_code == 200, 'S840_SESSION_CREATE_FAILED')
            sid = session.json()['data']['id']
            body = {'content': case['question'], 'request_id': batch['request_id'], 'mode': 'quick',
                'knowledge_base_scope': [case['knowledge_base_id']], 'document_scope': [], 'reasoning_effort': 'off'}
            write_new(target / 'submit-claim.json', {'status': 'SUBMITTING_NO_RETRY', 'session_id': sid,
                'request_id': batch['request_id'], 'body': body})
            began, first_answer = time.monotonic(), None
            blocks = []
            with (target / 'response.sse').open('xb') as raw:
                request_headers = profile_request_headers(case['question'], headers,
                    case.get('answer_language'))
                async with client.stream('POST', f'/api/v1/sessions/{sid}/messages', json=body,
                                         headers=request_headers) as response:
                    status_code = response.status_code
                    async for chunk in response.aiter_bytes():
                        raw.write(chunk); raw.flush(); blocks.append(chunk)
                        if first_answer is None:
                            text = b''.join(blocks).decode('utf8', errors='replace')
                            if '\n\n' in text:
                                complete = text.rsplit('\n\n', 1)[0] + '\n\n'
                                if any(f.get('response_type') == 'answer' and f.get('content')
                                        for f in prior.parse_sse(complete)):
                                    first_answer = time.monotonic() - began
            frames = prior.parse_sse(b''.join(blocks).decode('utf8'))
            ledger = prior.ledger_status(batch['execution_file'])
            write_new(target / 'frames.json', frames)
            write_new(target / 'ledger.json', ledger)
            require(status_code == 200 and frames and frames[-1].get('response_type') == 'complete',
                    'S840_MISSING_SSE_COMPLETION')
            terminal = frames[-1].get('data', {})
            require(not any(f.get('response_type') == 'error' for f in frames)
                and terminal.get('status') not in ('failed', 'error', 'cancelled'), 'S840_FAILED_TURN_STOP')
            require(terminal.get('finish_reason') in ('stop', 'end_turn', 'completed')
                and terminal.get('truncated') is False, 'S840_INCOMPLETE_OUTPUT_STOP')
            require(ledger['status'] == 'OPEN' and ledger['unsettled'] == 0, 'S840_UNKNOWN_OR_LEDGER_STOP')
            history = await client.get(f'/api/v1/sessions/{sid}/messages')
            write_new(target / 'history.json', {'status': history.status_code, 'body': history.json()})
            require(history.status_code == 200, 'S840_HISTORY_READ_FAILED')
            assistants = [r for r in history.json()['data'] if r.get('role') == 'assistant']
            users = [r for r in history.json()['data'] if r.get('role') == 'user']
            require(len(assistants) == 1 and assistants[0].get('is_completed') is True
                and assistants[0].get('turn_status') == 'completed', 'S840_HISTORY_STATUS_MISMATCH')
            assistant = assistants[0]
            run_id = frames[0].get('id')
            require(run_id and run_id != batch['request_id'] and all(f.get('id') == run_id for f in frames),
                    'S840_CLIENT_SERVER_IDENTITY_MISMATCH')
            queries = [f.get('data', {}) for f in frames if f.get('response_type') == 'agent_query']
            require(len(queries) == 1 and queries[0].get('request_id') == run_id
                and queries[0].get('query') == case['question'] and queries[0].get('session_id') == sid
                and queries[0].get('assistant_message_id') == assistant['id']
                and assistant.get('request_id') == run_id, 'S840_HISTORY_RUN_BINDING_MISMATCH')
            require(len(users) == 1 and users[0].get('content') == case['question'], 'S840_HISTORY_Q0_MISMATCH')
            replay = await client.get(f'/api/v1/sessions/continue-stream/{sid}', params={'message_id': assistant['id']})
            replay_frames = prior.parse_sse(replay.text) if replay.status_code == 200 else []
            write_new(target / 'replay.json', {'status': replay.status_code, 'frames': replay_frames})
            require(replay.status_code == 200 and replay_frames and prior.ledger_status(batch['execution_file']) == ledger
                and all(f.get('id') == run_id for f in replay_frames)
                and replay_frames[-1].get('response_type') == 'complete'
                and replay_frames[-1].get('data') == terminal, 'S840_REPLAY_NEW_SEND_OR_STATE_CHANGE')
            require([f.get('data') for f in replay_frames if f.get('response_type') == 'agent_query'] == queries,
                    'S840_REPLAY_RUN_BINDING_MISMATCH')
            answer = lambda values: ''.join(f.get('content', '') for f in values if f.get('response_type') == 'answer')
            require(answer(frames) == answer(replay_frames) == assistant.get('content') and bool(answer(frames).strip()),
                    'S840_ANSWER_HISTORY_MISMATCH')
            references = [f.get('knowledge_references') for f in frames if f.get('response_type') == 'references']
            require(references == [f.get('knowledge_references') for f in replay_frames if f.get('response_type') == 'references']
                and ((references[-1] or []) == (assistant.get('knowledge_references') or []) if references
                     else not assistant.get('knowledge_references')), 'S840_REFERENCE_PERSISTENCE_MISMATCH')
            sources = []
            for reference in assistant.get('knowledge_references') or []:
                chunk_id = reference.get('chunk_id') or reference.get('id')
                require(isinstance(chunk_id, str) and chunk_id, 'S840_CITATION_ID_MISSING')
                source = await client.get('/api/v1/chunks/' + chunk_id)
                sources.append({'chunk_id': chunk_id, 'status': source.status_code, 'body': source.json()})
                require(source.status_code == 200, 'S840_CITATION_READBACK_FAILED')
                payload = source.json().get('data', {})
                require(payload.get('knowledge_base_id') == case['knowledge_base_id'], 'S840_CITATION_SCOPE_MISMATCH')
            write_new(target / 'sources.json', sources)
            result = {'status': 'HTTP_COMPLETED_REVIEW_REQUIRED', 'case_id': case['case_id'],
                'session_id': sid, 'client_request_id': batch['request_id'], 'server_run_id': run_id,
                'assistant_message_id': assistant['id'], 'first_answer_seconds': first_answer,
                'whole_seconds': time.monotonic() - began, 'ledger': ledger,
                'final_answer_correct': 'NOT_REVIEWED', 'evidence_support': 'NOT_REVIEWED',
                'actual_rule_activation': 'REQUIRES_STRATEGY_AUDIT_REVIEW',
                'original_evidence_delivery': 'REQUIRES_ACTUAL_PROVIDER_WIRE_REVIEW',
                'PG_reconciliation': 'REQUIRES_READ_ONLY_PG_REVIEW',
                'citation_ownership': 'SAME_KB_READBACK; current version and original asset proof reviewed separately'}
    except BaseException as exc:
        write_new(target / 'results.json', {'status': 'STOP_NO_RETRY', 'case_id': case['case_id'],
            'error_type': type(exc).__name__, 'error': str(exc), 'send_state': 'READ_RAW_RECEIPTS_NO_RESEND',
            'ledger': prior.ledger_status(batch['execution_file'])})
        raise
    write_new(target / 'results.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    profile = sub.add_parser('profile'); profile.add_argument('--output', required=True)
    api = sub.add_parser('api'); api.add_argument('--port', type=int, default=8000)
    collect = sub.add_parser('collect'); collect.add_argument('--base-url', required=True); collect.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    if args.command == 'profile':
        value = profile_manifest(); write_new(args.output, value)
        print(json.dumps({'profile': PROFILE_ID, 'sha256': digest(value), 'actual_provider_calls': 0})); return 0
    if args.command == 'collect':
        import asyncio
        asyncio.run(collect_http(args.base_url, args.output)); return 0
    import uvicorn
    uvicorn.run(create_application(), host='0.0.0.0', port=args.port, workers=1, log_level='warning', access_log=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
