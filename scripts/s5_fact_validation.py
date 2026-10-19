"""Authorized S5 fixed-context generation. No retrieval, upload or role writes.

Requires a separately recorded current Owner grant. Each run has a durable
exclusive claim; no retry/resume of a sent or uncertain batch is implemented.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.local_runtime import load_config, environment, verify_resources
from scripts.s5_fact_reliability import sha, body_sha

KB = 'd683c1b1-7268-45c2-8a05-23c94f4d5db0'
QWEN = 'Qwen/Qwen3.5-4B'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def planned_calls(pack):
    return [(c['id'], arm, c[arm]) for c in pack['cases'] for arm in ('baseline', 'candidate')
            if not (arm == 'baseline' and isinstance(c[arm]['response'], dict))]


async def run(args):
    grant = read(args.authorization)
    if not (grant.get('owner_approved') is True and grant.get('scope') == 'S5_20261019'
            and grant.get('status') == 'OPEN' and grant.get('max_calls') == 12
            and grant.get('max_tokens') == 65000 and grant.get('manifest_sha256') == sha(args.manifest.read_bytes())):
        raise ValueError('S5_NEW_OWNER_GRANT_REQUIRED')
    pack = read(args.manifest)
    calls = planned_calls(pack)
    if len(calls) != 12:
        raise ValueError('FROZEN_TWELVE_CALLS_REQUIRED')
    cfg = load_config(args.config)
    identity = await verify_resources(cfg)
    env = environment(cfg, identity['redis_run_id'])
    for path in reversed(env.get('PYTHONPATH', '').split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    env.pop('WEKNORA_PORT_EVIDENCE_DIR', None)
    env.pop('WEKNORA_PORT_EXECUTION_FILE', None)
    env['WEKNORA_PORT_CLOUD_ALLOWED'] = 'false'
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.models.types import CallContext, TrustedScope, Message, ChatOptions
    options = ChatOptions(temperature=.3, max_completion_tokens=1024, thinking=False)
    async with assemble(DevelopmentSettings.from_environment(env)) as app:
        principal = app.principal
        if principal.tenant_id != 1 or sha(principal.subject_id) != next(iter(pack['historical_inputs'].values()))['owner_sha256']:
            raise ValueError('DAILY_PRINCIPAL_MISMATCH')
        documents = await app._rows("SELECT id,tenant_id,metadata->>'source_version' AS source_version FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL ORDER BY id", (1, KB))
        expected = {'fce4db0b-c790-49ad-b2a4-441769da3b94': '9df714d2-5fa4-47ff-a06b-1405059acbec',
                    '3b2870ce-81d3-4cbf-b14f-24cc1088f96a': '18ff1d62-d644-4376-b82c-76743fb1d0e7'}
        if not all(any(str(d['id']) == key and d['source_version'] == value for d in documents) for key, value in expected.items()):
            raise ValueError('SOURCE_VERSION_MISMATCH')
        async with app.leases.lease() as guard:
            _, roles, _ = await PostgresModelRoleStore(guard, principal, app.catalogue, cipher=app.cipher).read_available()
        role = next(r for r in roles if r.role == 'cheap')
        if role.provider != 'siliconflow' or role.model != QWEN:
            raise ValueError('DAILY_CHEAP_MODEL_CHANGED')
        mid = app.models._identity(role)[0]
        registry = app.registry_for_context()
        try:
            model = await registry.chat(mid, CallContext('s5-zero-send-preflight', TrustedScope(principal.subject_id, (KB,), ())))
            reservations = []
            for identifier, arm, item in calls:
                body = item['body']
                built = model.build_request_body(tuple(Message(**m) for m in body['messages']), options, stream=body.get('stream', False))
                if built != body or body_sha(body) != item['sha256'] or body.get('enable_thinking') is not False:
                    raise ValueError('FROZEN_WIRE_CHANGED')
                wire = json.dumps(built, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False).encode()
                reservations.append(len(wire) + 1024 + 1024)
        finally:
            await registry.close()
    if sum(reservations) > grant['max_tokens']:
        raise ValueError('PRE_SEND_ENVELOPE_TOO_SMALL')
    args.output.mkdir(parents=True, exist_ok=True)
    prepared = {'status': 'ZERO_SEND_PREPARED', 'model_id': mid, 'provider': 'siliconflow', 'model': QWEN,
                'tenant_id': 1, 'subject_sha256': sha(principal.subject_id), 'knowledge_base_id': KB,
                'documents': documents, 'calls': len(calls), 'reservation_upper_sum': sum(reservations),
                'resource_identity': identity,
                'manifest_sha256': grant['manifest_sha256']}
    write(args.output / 'preflight.json', prepared)
    if args.command == 'prepare':
        print(json.dumps({k: prepared[k] for k in ('status', 'calls', 'reservation_upper_sum')}))
        return
    with (args.output / 'batch.claim').open('x', encoding='utf-8') as f:
        f.write('S5 authorized once; never resume/resend this batch')
    prefix = 's5-facts-' + uuid.uuid4().hex
    ledger_path = args.output / 'execution.json'
    if ledger_path.exists():
        raise ValueError('EXISTING_EXECUTION_NO_RESET')
    write(ledger_path, {'status': 'OPEN', 'scope': 'S5_20261019', 'tenant_id': 1,
        'subject_id': principal.subject_id, 'allowed_kbs': [KB],
        'allowed_models': [{'provider': 'siliconflow', 'model': QWEN, 'model_id': mid, 'capability': 'chat'}],
        'allowed_request_prefixes': [prefix], 'max_calls': 12, 'max_tokens': 65000,
        'capture_evidence': True, 'entries': []})
    env.update(WEKNORA_PORT_CLOUD_ALLOWED='true', WEKNORA_PORT_EXECUTION_FILE=str(ledger_path))
    results = {'status': 'RUNNING', 'scope': 'FIXED_CONTEXT_NOT_RAG_ACCEPTANCE', 'calls': []}
    try:
        async with assemble(DevelopmentSettings.from_environment(env)) as app:
            if app.principal != principal:
                raise ValueError('PRINCIPAL_CHANGED_BEFORE_SEND')
            old_calls = await app._rows('SELECT call_id,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p ORDER BY call_id', ())
            old_config = await app._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s', (1, principal.subject_id))
            write(args.output / 'history-before.json', {'calls': old_calls, 'configuration': old_config})
            registry = app.registry_for_context()
            try:
                for identifier, arm, item in calls:
                    if read(ledger_path)['status'] != 'OPEN':
                        raise ValueError('EXECUTION_STOP')
                    request_id = prefix + '/' + identifier + '/' + arm
                    context = CallContext(request_id, TrustedScope(principal.subject_id, (KB,), ()))
                    model = await registry.chat(mid, context)
                    body = item['body']
                    messages = tuple(Message(**m) for m in body['messages'])
                    if model.build_request_body(messages, options, stream=body.get('stream', False)) != body:
                        raise ValueError('PRE_SEND_WIRE_MISMATCH')
                    started, events = time.monotonic(), []
                    if body.get('stream'):
                        async for event in model.chat_stream(messages, options, context):
                            events.append(asdict(event))
                            write(args.output / (identifier + '-' + arm + '.events.json'), events)
                        answer = ''.join(e['content'] for e in events if e.get('response_type') == 'answer')
                        finish = [e['finish_reason'] for e in events if e.get('finish_reason')]
                    else:
                        response = await model.chat(messages, options, context)
                        write(args.output / (identifier + '-' + arm + '.response.json'), asdict(response))
                        answer, finish = response.content, [response.finish_reason]
                    rows = await app._rows('SELECT call_id,request_id,provider,model,model_id,outcome,usage,wire_sha256 FROM port_provider_calls WHERE tenant_id=%s AND subject_id=%s AND request_id=%s', (1, principal.subject_id, request_id))
                    record = {'id': identifier, 'arm': arm, 'request_id': request_id, 'answer': answer,
                              'finish_reason': finish, 'seconds': time.monotonic() - started, 'receipt': rows}
                    results['calls'].append(record)
                    write(args.output / 'results.json', results)
                    if (len(rows) != 1 or rows[0]['outcome'] != 'RESPONSE_RECEIVED'
                            or rows[0]['usage'].get('status') != 'REPORTED'
                            or body_sha(body) != rows[0]['wire_sha256']
                            or read(ledger_path)['entries'][-1]['status'] != 'SETTLED'):
                        raise ValueError('UNKNOWN_IDENTITY_OR_SETTLEMENT_STOP')
                    print(json.dumps({'id': identifier, 'arm': arm, 'seconds': record['seconds'], 'usage': rows[0]['usage']}, ensure_ascii=False), flush=True)
            finally:
                await registry.close()
            after = await app._rows('SELECT call_id,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p WHERE request_id NOT LIKE %s ORDER BY call_id', (prefix + '/%',))
            config_after = await app._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s', (1, principal.subject_id))
            results['historical_calls_unchanged'] = after == old_calls
            results['default_configuration_unchanged'] = config_after == old_config
            if after != old_calls or config_after != old_config:
                raise ValueError('HISTORY_OR_DEFAULT_CONFIGURATION_CHANGED')
        results['status'] = 'COMPLETE'
    finally:
        ledger = read(ledger_path)
        settled = (len(ledger['entries']) == 12 and len(results['calls']) == 12
                   and results.get('historical_calls_unchanged') is True
                   and results.get('default_configuration_unchanged') is True
                   and all(e['status'] == 'SETTLED' for e in ledger['entries']))
        ledger['status'] = 'CLOSED' if settled else 'STOP'
        write(ledger_path, ledger)
        grant.update(status=ledger['status'], actual_calls=len(ledger['entries']),
                     actual_reported_tokens=sum((e.get('usage') or {}).get('total_tokens', 0) for e in ledger['entries']),
                     unreported_entries=sum(e['status'] != 'SETTLED' for e in ledger['entries']))
        write(args.authorization, grant)
        results['status'] = 'COMPLETE' if settled else 'INCOMPLETE_STOP'
        write(args.output / 'results.json', results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    for name in ('config', 'authorization', 'manifest', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    asyncio.run(run(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop)
