"""One authorized S7 ordinary API request on an isolated loopback service.

Uses the standard API factory/assembly, an in-memory Cheap-role overlay and the
root-opened LocalExecution ledger. Never changes stored model roles or opens a
new grant. No Worker is started. Bearer credentials exist only in memory/env.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.s7_graph_generation import QWEN, QWEN_ID, read, require, sha, stage_module, validate_phase, write_new

CASES = {'franklin-mixed', 'franklin-maintenance-unanswerable'}


async def overlay_cheap(runtime):
    """Keep the normal snapshot path and all roles except isolated Cheap."""
    candidate = await runtime.models.resolve(QWEN_ID)
    require(candidate.role == 'cheap' and candidate.provider == 'siliconflow' and candidate.model == QWEN,
            'S7_ARCHIVED_CHEAP_IDENTITY_MISMATCH')
    original = runtime._snapshot

    async def snapshot(guard):
        before = await original(guard)
        require(any(role == 'cheap' for role, _ in before.model_ids), 'S7_EXISTING_CHEAP_ROLE_REQUIRED')
        return replace(before,
            model_ids=tuple((role, QWEN_ID if role == 'cheap' else model_id) for role, model_id in before.model_ids),
            configurations=tuple(candidate if role.role == 'cheap' else role for role in before.configurations),
            credential_errors=tuple(item for item in before.credential_errors if item[0] != 'cheap'))

    runtime._snapshot = snapshot
    return {'kind': 'SERVER_MEMORY_ONLY_ARCHIVED_CHEAP_BINDING', 'cheap_model_id': QWEN_ID,
            'provider': candidate.provider, 'model': candidate.model, 'database_role_write': False}


def question(case, kb):
    require(case['id'] in CASES and case['request']['knowledge_base_scope'] == [kb]
            and case['request']['document_scope'] == [], 'S7_API_CASE_SCOPE_INVALID')
    return {**case['request'], 'request_id': str(uuid.uuid4()), 'summary_model_id': QWEN_ID,
            'reasoning_effort': 'off', 'mode': 'quick'}


def available_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def classify_question(sse_text, history):
    """HTTP 200 / EOF is not proof that the ordinary assistant completed."""
    frames = []
    for block in sse_text.replace('\r\n', '\n').split('\n\n'):
        data = '\n'.join(line[5:].lstrip(' ') for line in block.splitlines() if line.startswith('data:'))
        if data:
            frames.append(json.loads(data))
    complete = [row for row in frames if row.get('response_type') == 'complete']
    errors = [row.get('data', {}).get('error_code', 'SSE_ERROR') for row in frames
              if row.get('response_type') == 'error']
    errors += [row.get('data', {}).get('error_code', 'SSE_FAILED') for row in complete
               if row.get('data', {}).get('status') in ('failed', 'error', 'cancelled')]
    assistants = [row for row in history if row.get('role') == 'assistant']
    assistant = assistants[-1] if assistants else {}
    if assistant.get('turn_error_code'):
        errors.append(assistant['turn_error_code'])
    failed = bool(errors) or assistant.get('turn_status') in ('error', 'cancelled')
    completed = bool(complete) and assistant.get('is_completed') is True
    nonempty = isinstance(assistant.get('content'), str) and bool(assistant['content'].strip())
    status = ('FAILED_ASSISTANT_TURN' if failed else 'INCOMPLETE_QUESTION' if not completed
              else 'EMPTY_ASSISTANT_ANSWER' if not nonempty else 'EXECUTED_REQUIRES_EVIDENCE_REVIEW')
    return {'status': status, 'sse_complete_present': bool(complete), 'assistant_completed': assistant.get('is_completed') is True,
            'assistant_nonempty': nonempty, 'error_codes': list(dict.fromkeys(errors)),
            'finish_reason': assistant.get('finish_reason')}


async def stop_owned_api(process, ledger_path, output):
    """Never interrupt a potentially sending child; only stop our own process."""
    if process is None or process.poll() is not None:
        return {'process_stopped': True}
    from backend.weknora_port.execution import LocalExecution
    if any(entry['status'] == 'INFLIGHT' for entry in read(ledger_path)['entries']):
        with LocalExecution(ledger_path)._ledger() as current:
            current['status'] = 'STOP'
        return {'process_kept_alive_due_inflight': process.pid}
    # Private file control, never a new public HTTP shutdown endpoint.
    write_new(output / 'stop.request', {'no_inflight_at_request': True})
    try:
        await asyncio.to_thread(process.wait, 20)
    except subprocess.TimeoutExpired:
        # Check again immediately before a hard stop, never kill an active send.
        if any(entry['status'] == 'INFLIGHT' for entry in read(ledger_path)['entries']):
            with LocalExecution(ledger_path)._ledger() as current:
                current['status'] = 'STOP'
            return {'process_kept_alive_due_inflight': process.pid}
        process.terminate()
        await asyncio.to_thread(process.wait, 20)
    return {'process_stopped': True}


async def server(args):
    import uvicorn
    from scripts.local_runtime import environment, load_config, verify_resources
    from scripts.s2_generation_quality import identity_for
    from scripts.m4_tenant332_api import BearerTokenGuard
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.entrypoint import create_app

    cfg = load_config(args.config)
    identity = await verify_resources(cfg)
    env, principal = identity_for(332, environment(cfg, identity['redis_run_id']), read(args.principal_state))
    env.pop('WEKNORA_PORT_EVIDENCE_DIR', None)
    env.update(WEKNORA_PORT_CLOUD_ALLOWED='true', WEKNORA_PORT_GRAPH_ENABLED='true',
               WEKNORA_PORT_EXECUTION_FILE=str(args.ledger), WEKNORA_PORT_REDIS_PREFIX=os.environ['S7_API_REDIS_PREFIX'],
               WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID=QWEN_ID)
    token = os.environ.get('S7_API_BEARER_TOKEN', '')
    require(len(token) >= 32, 'S7_EPHEMERAL_BEARER_MISSING')
    settings = DevelopmentSettings.from_environment(env)
    require(settings.router_mode == 'DYNAMIC', 'S7_ORIGINAL_DYNAMIC_REQUIRED')
    plan = read(args.plan)
    require(sha(principal.subject_id.encode()) == plan['subject_sha256'], 'S7_PRINCIPAL_MISMATCH')
    stage = stage_module(args.stage_helper)
    validate_phase(stage, args.ledger, principal, plan['knowledge_base_id'], 0)

    @asynccontextmanager
    async def factory(active):
        async with assemble(active, principal=principal) as runtime:
            require(runtime.principal == principal and runtime.execution.path.resolve() == args.ledger.resolve(),
                    'S7_RUNTIME_IDENTITY_MISMATCH')
            kb = await runtime._rows('SELECT id,tenant_id,cloud_allowed FROM knowledge_bases WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL',
                                     (plan['knowledge_base_id'], 332))
            require(len(kb) == 1 and kb[0]['cloud_allowed'] is True, 'S7_KB_EGRESS_NOT_AUTHORIZED')
            before = await runtime._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s',
                                         (332, principal.subject_id))
            overlay = await overlay_cheap(runtime)
            write_new(args.output / 'server-startup.json', {'status': 'READY', 'tenant_id': 332,
                'subject_sha256': sha(principal.subject_id.encode()), 'resource_identity': identity,
                'redis_prefix': settings.redis_prefix, 'storage_root': str(settings.storage),
                'graph_enabled': runtime.graph_enabled, 'router_mode': settings.router_mode,
                'generation_defaults': dict(runtime.conversation.summary), 'cheap_overlay': overlay,
                'query_understand_model_id': settings.query_understand_model_id,
                'conversation_settings': asdict(runtime.conversation),
                'role_configuration_before': before, 'workers_started': False})
            try:
                yield runtime
            finally:
                after = await runtime._rows('SELECT md5(roles::text) AS fingerprint FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s',
                                            (332, principal.subject_id))
                write_new(args.output / 'server-close.json', {'database_roles_unchanged': before == after,
                                                             'workers_started': False})

    app = create_app(settings=settings, runtime_factory=factory)
    guard = BearerTokenGuard(app, token, float(os.environ['S7_API_TOKEN_EXPIRES']))
    config = uvicorn.Config(guard, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)
    api_server = uvicorn.Server(config)

    async def stop_on_private_file():
        while not (args.output / 'stop.request').exists():
            await asyncio.sleep(.2)
        if not any(entry['status'] == 'INFLIGHT' for entry in read(args.ledger)['entries']):
            api_server.should_exit = True

    watcher = asyncio.create_task(stop_on_private_file())
    try:
        await api_server.serve()
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


async def run(args):
    import httpx
    from scripts.local_runtime import environment, load_config, verify_resources
    from scripts.s2_generation_quality import identity_for

    require(args.case in CASES, 'S7_UNAPPROVED_API_CASE')
    require(args.output.is_absolute() and args.output.drive.upper() in ('D:', 'E:')
            and not args.output.resolve().is_relative_to(ROOT), 'S7_EXTERNAL_OUTPUT_REQUIRED')
    plan = read(args.plan)
    case = next(case for case in plan['cases'] if case['id'] == args.case)
    cfg = load_config(args.config)
    identity = await verify_resources(cfg)
    env, principal = identity_for(332, environment(cfg, identity['redis_run_id']), read(args.principal_state))
    require(sha(principal.subject_id.encode()) == plan['subject_sha256'], 'S7_TRUSTED_SUBJECT_MISMATCH')
    stage = stage_module(args.stage_helper)
    validate_phase(stage, args.ledger, principal, plan['knowledge_base_id'], 0)
    args.output.mkdir(parents=True, exist_ok=True)
    write_new(args.output / 'operation.claim.json', {'case_id': args.case, 'once': True, 'ledger': str(args.ledger),
                                                  'plan_sha256': sha(args.plan.read_bytes()), 'retry': False})
    token, expiry = secrets.token_urlsafe(40), time.time() + 40 * 60
    port = args.port or available_port()
    env.update(S7_API_BEARER_TOKEN=token, S7_API_TOKEN_EXPIRES=str(expiry),
               S7_API_REDIS_PREFIX='weknora-python-s7-api-' + uuid.uuid4().hex)
    command = [sys.executable, str(Path(__file__).resolve()), '--serve', '--config', str(args.config),
               '--principal-state', str(args.principal_state), '--ledger', str(args.ledger), '--plan', str(args.plan),
               '--stage-helper', str(args.stage_helper), '--case', args.case, '--output', str(args.output), '--port', str(port)]
    process = None
    result = {'case_id': args.case, 'status': 'STARTED', 'provider_calls_before': len(read(args.ledger)['entries'])}
    try:
        with (args.output / 'server.stdout.log').open('xb') as stdout, (args.output / 'server.stderr.log').open('xb') as stderr:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
        write_new(args.output / 'process.json', {'pid': process.pid, 'port': port, 'host': '127.0.0.1',
                                                'token_persisted': False, 'worker_started': False})
        base = f'http://127.0.0.1:{port}'
        headers = {'Authorization': 'Bearer ' + token, 'Origin': f"http://127.0.0.1:{cfg['ui_port']}"}
        async with httpx.AsyncClient(base_url=base, headers=headers, timeout=httpx.Timeout(600, connect=5), trust_env=False) as client:
            for _ in range(60):
                require(process.poll() is None, 'S7_API_PROCESS_EXITED')
                try:
                    ready = await client.get('/api/v1/runtime')
                    if ready.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(.5)
            else:
                raise ValueError('S7_API_STARTUP_TIMEOUT')
            startup = read(args.output / 'server-startup.json')
            require(startup['tenant_id'] == 332 and startup['subject_sha256'] == plan['subject_sha256']
                    and startup['cheap_overlay']['cheap_model_id'] == QWEN_ID
                    and startup['graph_enabled'] is True, 'S7_API_STARTUP_IDENTITY_MISMATCH')
            session_response = await client.post('/api/v1/sessions', json={'title': 'S7 ' + args.case,
                'knowledge_base_scope': [plan['knowledge_base_id']], 'document_scope': []})
            write_new(args.output / 'session.response.json', {'status_code': session_response.status_code, 'body': session_response.text})
            session_response.raise_for_status()
            session_id = session_response.json()['data']['id']
            body = question(case, plan['knowledge_base_id'])
            write_new(args.output / 'question.claim.json', {'session_id': session_id, 'body': body})
            began = time.perf_counter()
            async with client.stream('POST', f'/api/v1/sessions/{session_id}/messages', json=body) as response:
                if response.status_code != 200:
                    raw = await response.aread()
                    write_new(args.output / 'question.http-error.json', {'status_code': response.status_code, 'body': raw.decode('utf-8', errors='replace')})
                    response.raise_for_status()
                with (args.output / 'question.sse').open('xb') as handle:
                    async for block in response.aiter_bytes():
                        handle.write(block)
                        handle.flush()
            validate_phase(stage, args.ledger, principal, plan['knowledge_base_id'], 0)
            calls_before_history = len(read(args.ledger)['entries'])
            histories = []
            for index in range(2):
                history = await client.get(f'/api/v1/sessions/{session_id}/messages')
                write_new(args.output / f'history-{index}.json', {'status_code': history.status_code, 'body': history.text})
                history.raise_for_status()
                histories.append(history.json()['data'])
            require(histories[0] == histories[1] and len(read(args.ledger)['entries']) == calls_before_history,
                    'S7_HISTORY_CHANGED_OR_RESENT')
            result.update(classify_question((args.output / 'question.sse').read_text(encoding='utf-8'), histories[0]))
            result.update(session_id=session_id, client_request_id=body['request_id'],
                          seconds=time.perf_counter() - began, history_readback_equal=True,
                          provider_calls_after=calls_before_history, history=histories[0])
    except BaseException as error:
        result.update(status='FAILED_RETAINED_NO_RETRY', exception_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, ValueError) else 'S7_API_OPERATION_FAILED_SEE_EVIDENCE')
        raise
    finally:
        result.update(await stop_owned_api(process, args.ledger, args.output))
        write_new(args.output / 'results.json', result)
    return result


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    for name in ('ledger', 'case', 'output', 'config', 'plan', 'principal-state', 'stage-helper'):
        parser.add_argument('--' + name, type=Path if name not in ('case',) else str, required=True)
    parser.add_argument('--port', type=int, default=0)
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            result = runner.run(server(args) if args.serve else run(args))
        return 0 if args.serve or result['status'] == 'EXECUTED_REQUIRES_EVIDENCE_REVIEW' else 2
    except Exception as error:
        print(json.dumps({'status': 'STOP', 'exception_type': type(error).__name__, 'error_code': getattr(error, 'code', None)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
