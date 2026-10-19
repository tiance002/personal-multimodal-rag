"""Owner-authorized S2 fixed-context pairs through the existing registry/Gate.

No retrieval, new evidence, automatic retry, model-role update or Gold injection.
Prepare verifies wires without sending; each pair has a permanent claim.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.local_runtime import environment, load_config, verify_resources
from scripts.s1_rerank_input_ab import read, write

QWEN = 'Qwen/Qwen3.5-4B'
# Rejected experimental instruction; not part of the production prompt.
ANSWER_COMPLETENESS_GUIDANCE = "Answer completeness:\nCover each part of the user's request and every directly supported answer item within that scope, including relevant alternatives. Do not replace a requested list or comparison with a single example. If a requested part lacks support, identify that gap explicitly. Keep the answer focused and concise; omit background and extra factual claims that are unnecessary to resolve the request."


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paired_messages(raw_messages, guide):
    """Only the system instruction differs; source/user/history bytes stay fixed."""
    baseline = [dict(m) for m in raw_messages]
    if not baseline or baseline[0].get('role') != 'system':
        raise ValueError('SAVED_SYSTEM_MESSAGE_REQUIRED')
    if any(not isinstance(m.get('content'), str) or set(m) - {'role', 'content'}
           for m in baseline):
        raise ValueError('TEXT_ONLY_FROZEN_MESSAGES_REQUIRED')
    if guide in baseline[0]['content']:
        raise ValueError('BASELINE_ALREADY_HAS_CANDIDATE_GUIDANCE')
    changed = [dict(m) for m in baseline]
    marker = '\n\n## Source handling protocol'
    system = baseline[0]['content']
    changed[0]['content'] = (system.replace(marker, '\n\n' + guide + marker, 1)
                              if marker in system else system + '\n\n' + guide)
    return baseline, changed


def pair_is_complete(entries, arms, prefix):
    expected = {prefix + '/A', prefix + '/B'}
    return (set(arms) == {'A', 'B'} and len(entries) == 2
            and {e['request_id'] for e in entries} == expected
            and all(e['status'] == 'SETTLED' for e in entries))


def authorization(path):
    data = read(path)
    if data.get('owner_approved') is not True or data.get('status') != 'OPEN' or data.get('scope') != 'S2_20261019':
        raise ValueError('S2_OWNER_AUTHORIZATION_REQUIRED')
    if any(b['status'] != 'CLOSED' for b in data['batches']):
        raise ValueError('S2_UNSETTLED_PREVIOUS_BATCH')
    if sum(b['calls'] for b in data['batches']) >= data['max_calls']:
        raise ValueError('S2_AUTHORIZED_CALL_COUNT_EXHAUSTED')
    return data


async def base_environment(args):
    cfg = load_config(args.config)
    identity = await verify_resources(cfg)
    env = environment(cfg, identity['redis_run_id'])
    for p in reversed(env.get('PYTHONPATH', '').split(os.pathsep)):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    return env, identity


def identity_for(tenant, env, state):
    from backend.weknora_port.storage.history import LocalPrincipal
    if tenant == 332:
        if state['tenant'] != 332 or not state['subject'].startswith('p8-real-'):
            raise ValueError('TRUSTED_332_PRINCIPAL_REQUIRED')
        env = dict(env, WEKNORA_PORT_STORAGE_ROOT=state['storage_root'],
                   WEKNORA_PORT_SECRET_KEY_FILE=state['secret_key_file'])
        return env, LocalPrincipal(332, state['subject'])
    if tenant != 1:
        raise ValueError('UNAUTHORIZED_TENANT')
    return env, None  # normal assemble resolves the persisted daily principal


async def prepare(args):
    parent = authorization(args.authorization)
    if not parent.get('allow_tenant332_candidate_registration'):
        raise ValueError('CANDIDATE_REGISTRATION_NOT_AUTHORIZED')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'prepared.json').exists():
        raise ValueError('PREPARATION_ALREADY_FROZEN')
    env, identity = await base_environment(args)
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.models.types import CallContext, TrustedScope, Message, ChatOptions
    manifest = read(args.manifest)
    state = read(args.principal_state)
    models, inputs = {}, []
    for tenant in (332, 1):
        active_env, principal = identity_for(tenant, env, state)
        active_env = dict(active_env, WEKNORA_PORT_CLOUD_ALLOWED='false')
        active_env.pop('WEKNORA_PORT_EXECUTION_FILE', None)
        async with assemble(DevelopmentSettings.from_environment(active_env), principal=principal) as app:
            before = await app._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s', (tenant, app.principal.subject_id))
            async with app.leases.lease() as guard:
                store = PostgresModelRoleStore(guard, app.principal, app.catalogue, cipher=app.cipher)
                _, roles, errors = await store.read_available()
            role = next(r for r in roles if r.role == 'cheap')
            if tenant == 332:
                if role.provider != 'siliconflow':
                    raise ValueError('EXISTING_SILICONFLOW_CREDENTIAL_REQUIRED')
                role = replace(role, model=QWEN, source=role.source or 'remote')
                model_id = await app.models.persist(role)
            else:
                if role.provider != 'siliconflow' or role.model != QWEN:
                    raise ValueError('DAILY_CHEAP_MODEL_IDENTITY_CHANGED')
                model_id = app.models._identity(role)[0]
            after = await app._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s', (tenant, app.principal.subject_id))
            if before != after:
                raise ValueError('DEFAULT_ROLES_CHANGED')
            models[str(tenant)] = {'model_id': model_id, 'provider': role.provider, 'model': role.model,
                                   'default_roles_unchanged': True}
            registry = app.registry_for_context()
            try:
                for case in manifest['cases']:
                    if case['tenant_id'] != tenant:
                        continue
                    request_path = Path(case['request_path'])
                    if sha(request_path) != case['request_sha256']:
                        raise ValueError('FROZEN_REQUEST_HASH_MISMATCH')
                    raw = read(request_path)
                    arms = paired_messages(raw['body']['messages'], ANSWER_COMPLETENESS_GUIDANCE)
                    scope = TrustedScope(app.principal.subject_id, tuple(case['kb_scope']), tuple(case.get('document_scope', [])))
                    ctx = CallContext('s2-preflight-' + case['case_id'], scope)
                    model = await registry.chat(model_id, ctx)
                    options = ChatOptions(temperature=0.3, max_completion_tokens=1024, thinking=False)
                    bodies = [model.build_request_body(tuple(Message(**m) for m in arm), options, stream=True) for arm in arms]
                    for body in bodies:
                        if body.get('model') != QWEN or body.get('enable_thinking') is not False or body.get('max_tokens') != 1024:
                            raise ValueError('QWEN_WIRE_CONTRACT_MISMATCH')
                    if bodies[0]['messages'][1:] != bodies[1]['messages'][1:]:
                        raise ValueError('FIXED_EVIDENCE_CHANGED')
                    inputs.append({'case_id': case['case_id'], 'tenant_id': tenant,
                                   'kb_scope': case['kb_scope'], 'document_scope': case.get('document_scope', []),
                                   'request_path': str(request_path), 'request_sha256': sha(request_path),
                                   'arms': {'A': bodies[0], 'B': bodies[1]}})
            finally:
                await registry.close()
    if len(inputs) != 6 or {x['case_id'] for x in inputs} != {c['case_id'] for c in manifest['cases']}:
        raise ValueError('SIX_FROZEN_CASES_REQUIRED')
    write(output / 'prepared.json', {'status': 'ZERO_SEND_PREPARED', 'identity': identity,
        'models': models, 'manifest_sha256': sha(args.manifest), 'inputs': inputs,
        'provider_sends': 0, 'guidance': ANSWER_COMPLETENESS_GUIDANCE})
    parent['prepared_sha256'] = sha(output / 'prepared.json')
    write(args.authorization, parent)
    print(json.dumps({'status': 'ZERO_SEND_PREPARED', 'cases': len(inputs), 'models': models}))


async def run_pair(args):
    parent = authorization(args.authorization)
    if parent.get('prepared_sha256') != sha(args.output / 'prepared.json'):
        raise ValueError('PREPARED_INPUT_CHANGED')
    plan = read(args.output / 'prepared.json')
    if plan['manifest_sha256'] != sha(args.manifest):
        raise ValueError('FROZEN_MANIFEST_CHANGED')
    case = next(x for x in plan['inputs'] if x['case_id'] == args.case)
    frozen = next(x for x in read(args.manifest)['cases'] if x['case_id'] == args.case)
    for key in ('tenant_id', 'kb_scope', 'document_scope', 'request_path', 'request_sha256'):
        if case[key] != frozen[key]:
            raise ValueError('FROZEN_CASE_BINDING_CHANGED')
    if any(b['case_id'] == args.case for b in parent['batches']):
        raise ValueError('PAIR_ALREADY_CLAIMED_NO_RESEND')
    if sum(b['calls'] for b in parent['batches']) + 2 > parent['max_calls']:
        raise ValueError('PAIR_EXCEEDS_AUTHORIZED_CALLS')
    if sha(case['request_path']) != case['request_sha256']:
        raise ValueError('FROZEN_REQUEST_CHANGED')
    expected = paired_messages(read(case['request_path'])['body']['messages'], ANSWER_COMPLETENESS_GUIDANCE)
    if any(case['arms'][arm]['messages'] != expected[i] for i, arm in enumerate(('A', 'B'))):
        raise ValueError('FROZEN_PASSAGES_OR_GUIDANCE_CHANGED')
    output = args.output / args.case
    output.mkdir(parents=True, exist_ok=False)
    env, _ = await base_environment(args)
    active_env, principal = identity_for(case['tenant_id'], env, read(args.principal_state))
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.models.types import CallContext, TrustedScope, Message, ChatOptions
    # Resolve daily principal while globally closed; no requests or role changes.
    if principal is None:
        async with assemble(DevelopmentSettings.from_environment(dict(active_env, WEKNORA_PORT_CLOUD_ALLOWED='false'))) as app:
            principal = app.principal
    prefix = 's2-' + args.case + '-' + uuid.uuid4().hex
    mid = plan['models'][str(case['tenant_id'])]['model_id']
    batch = {'case_id': args.case, 'status': 'DELEGATED', 'calls': 0, 'tokens': 0, 'path': str(output)}
    parent['batches'].append(batch)
    write(args.authorization, parent)
    grant = {'status': 'OPEN', 'scope': parent['scope'], 'tenant_id': principal.tenant_id,
        'subject_id': principal.subject_id, 'allowed_kbs': case['kb_scope'],
        'allowed_models': [{'provider': 'siliconflow', 'model': QWEN, 'model_id': mid, 'capability': 'chat'}],
        'allowed_request_prefixes': [prefix], 'max_calls': 2, 'max_tokens': 100000,
        'capture_evidence': True, 'entries': []}
    write(output / 'execution.json', grant)
    active_env.update(WEKNORA_PORT_CLOUD_ALLOWED='true', WEKNORA_PORT_EXECUTION_FILE=str(output / 'execution.json'))
    result = {'case_id': args.case, 'type': 'FIXED_CONTEXT_GENERATION_NOT_RETRIEVAL_ACCEPTANCE', 'arms': {}}
    try:
        async with assemble(DevelopmentSettings.from_environment(active_env), principal=principal) as app:
            registry = app.registry_for_context()
            try:
                for arm in ('A', 'B'):
                    request_id = prefix + '/' + arm
                    (output / (arm + '.claimed')).write_text(request_id, encoding='utf-8')
                    body = case['arms'][arm]
                    messages = tuple(Message(**m) for m in body['messages'])
                    ctx = CallContext(request_id, TrustedScope(principal.subject_id, tuple(case['kb_scope']), tuple(case['document_scope'])))
                    model = await registry.chat(mid, ctx)
                    options = ChatOptions(temperature=0.3, max_completion_tokens=1024, thinking=False)
                    if model.build_request_body(messages, options, stream=True) != body:
                        raise ValueError('PREPARED_WIRE_CHANGED')
                    events, started = [], time.monotonic()
                    first = None
                    async for event in model.chat_stream(messages, options, ctx):
                        if first is None and event.content:
                            first = time.monotonic() - started
                        events.append(asdict(event))
                        write(output / (arm + '.events.json'), events)
                    rows = await app._rows('SELECT provider,model,model_id,outcome,usage,wire_sha256 FROM port_provider_calls WHERE tenant_id=%s AND subject_id=%s AND request_id=%s', (principal.tenant_id, principal.subject_id, request_id))
                    write(output / (arm + '.receipt.json'), rows)
                    answer = ''.join(e['content'] for e in events if e.get('response_type') == 'answer')
                    result['arms'][arm] = {'request_id': request_id, 'answer': answer,
                        'finish_reasons': [e['finish_reason'] for e in events if e.get('finish_reason')],
                        'seconds': time.monotonic()-started, 'first_content_seconds': first, 'receipt': rows}
                    write(output / 'result.json', result)
                    if len(rows) != 1 or rows[0]['outcome'] != 'RESPONSE_RECEIVED' or rows[0]['usage'].get('status') != 'REPORTED':
                        raise RuntimeError('S2_UNKNOWN_OR_USAGE_ANOMALY_STOP')
            finally:
                await registry.close()
    finally:
        ledger = read(output / 'execution.json')
        settled = pair_is_complete(ledger['entries'], result['arms'], prefix)
        ledger['status'] = 'CLOSED' if settled else 'STOP'
        result['status'] = 'COMPLETE' if settled else 'INCOMPLETE_STOP'
        batch.update(status=ledger['status'], calls=len(ledger['entries']), tokens=sum(e['held_tokens'] for e in ledger['entries']))
        write(output / 'execution.json', ledger)
        parent['status'] = 'OPEN' if settled else 'STOP'
        write(args.authorization, parent)
        write(output / 'result.json', result)
    print(json.dumps({'case': args.case, 'calls': batch['calls'], 'tokens': batch['tokens'], 'status': batch['status']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    for field in ('config', 'principal-state', 'manifest', 'authorization', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--case')
    args = parser.parse_args()
    if args.command == 'run' and not args.case:
        parser.error('run requires --case')
    asyncio.run(prepare(args) if args.command == 'prepare' else run_pair(args), loop_factory=asyncio.SelectorEventLoop)
