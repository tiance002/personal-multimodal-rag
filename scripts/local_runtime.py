"""Own the explicit Windows API/worker/React development processes.

No dotenv, migrations, container recreation/removal, or model requests. Stop
leaves dependencies running and only stops matching recorded host processes.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    for name in ('python', 'node', 'state_dir'):
        require(Path(cfg[name]).is_absolute(), 'ABSOLUTE_' + name.upper() + '_REQUIRED')
    require(Path(cfg['state_dir']).drive.upper() in ('D:', 'E:'), 'STATE_MUST_USE_D_OR_E')
    for name in ('python', 'node'):
        require(Path(cfg[name]).is_file(), 'EXECUTABLE_MISSING_' + name.upper())
    require(type(cfg['api_port']) is int and type(cfg['ui_port']) is int and
            1024 <= cfg['api_port'] <= 65535 and 1024 <= cfg['ui_port'] <= 65535 and
            cfg['api_port'] != cfg['ui_port'], 'DISTINCT_LOCAL_PORTS_REQUIRED')
    env = cfg.get('environment', {})
    require(all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()), 'ENVIRONMENT_STRINGS_REQUIRED')
    require(env.get('WEKNORA_PORT_MINERU_ENABLED', '').lower() in ('1', 'true'), 'MINERU_REQUIRED')
    require(set(row['service'] for row in cfg['containers']) == {'postgres', 'redis'} and
            len(cfg['containers']) == 2, 'TWO_EXPLICIT_DEPENDENCIES_REQUIRED')
    for row in cfg['containers']:
        require(len(row['id']) == 64 and all(c in '0123456789abcdef' for c in row['id']), 'FULL_CONTAINER_ID_REQUIRED')
    return cfg


def inspect_container(row, cfg=None):
    result = subprocess.run(['docker', 'inspect', row['id']], capture_output=True, text=True, check=True)
    obj = json.loads(result.stdout)[0]
    labels = obj['Config'].get('Labels') or {}
    require(obj['Id'] == row['id'] and labels.get('com.docker.compose.project') == 'eviroute-rag' and
            labels.get('com.docker.compose.service') == row['service'], 'DEPENDENCY_CONTAINER_IDENTITY_MISMATCH')
    target = '/var/lib/postgresql/data' if row['service'] == 'postgres' else '/data'
    require(any(m.get('Name') == row['volume'] and m['Destination'] == target for m in obj['Mounts']),
            'DEPENDENCY_VOLUME_IDENTITY_MISMATCH')
    require(obj['Config']['Image'] == row['image'], 'DEPENDENCY_IMAGE_IDENTITY_MISMATCH')
    if cfg is not None:
        variable = 'DATABASE_URL' if row['service'] == 'postgres' else 'REDIS_URL'
        url = urlsplit(cfg['environment']['WEKNORA_PORT_' + variable])
        port = '5432/tcp' if row['service'] == 'postgres' else '6379/tcp'
        bindings = obj['HostConfig'].get('PortBindings', {}).get(port) or []
        require(url.hostname in ('127.0.0.1', 'localhost') and any(
            b.get('HostIp') == '127.0.0.1' and b.get('HostPort') == str(url.port) for b in bindings),
            'DEPENDENCY_URL_PORT_IDENTITY_MISMATCH')
    return obj


def environment(cfg, redis_run_id=None):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('WEKNORA_PORT_', 'VITE_')):
            env.pop(key)
    env.update(cfg['environment'])
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
               VITE_WEKNORA_API_BASE=f"http://127.0.0.1:{cfg['api_port']}",
               WEKNORA_PORT_ORIGINS=f"http://127.0.0.1:{cfg['ui_port']}")
    if redis_run_id:
        env['WEKNORA_PORT_REDIS_RUN_ID'] = redis_run_id
    return env


async def verify_resources(cfg):
    import psycopg
    from redis.asyncio import Redis
    from backend.weknora_port.development import DevelopmentSettings
    env = environment(cfg)
    redis = Redis.from_url(env['WEKNORA_PORT_REDIS_URL'], decode_responses=True)
    try:
        run_id = (await redis.info('server'))['run_id']
        settings = DevelopmentSettings.from_environment(environment(cfg, run_id))
        conn = await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True, connect_timeout=5)
        try:
            await conn.execute('BEGIN TRANSACTION READ ONLY')
            row = await (await conn.execute('SELECT system_identifier::text,current_database() FROM pg_control_system()')).fetchone()
            require(row == (settings.system_id, settings.database), 'POSTGRES_PHYSICAL_IDENTITY_MISMATCH')
            principal = await (await conn.execute("SELECT tenant_id,subject_id FROM port_local_principal WHERE slot='local'")).fetchone()
            require(principal is not None, 'EXISTING_LOCAL_PRINCIPAL_REQUIRED')
            await conn.execute('ROLLBACK')
        finally:
            await conn.close()
        require(len(settings.secret_key_file.read_bytes()) == 32, 'SECRET_KEY_FILE_LENGTH_INVALID')
        require(settings.mineru_settings.python.is_file() and settings.mineru_settings.model_root.is_dir(), 'MINERU_LOCAL_RUNTIME_MISSING')
        return {'postgres_system_id': row[0], 'database': row[1], 'redis_run_id': run_id,
                'redis_db': settings.redis_url.rsplit('/', 1)[-1], 'redis_prefix': settings.redis_prefix,
                'tenant_id': principal[0], 'cloud_allowed': settings.cloud_allowed, 'ocr': 'mineru-basic'}
    finally:
        await redis.aclose()


def process_identity(pid):
    command = (f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}';"
               "if($p){@{pid=$p.ProcessId;exe=$p.ExecutablePath;created=$p.CreationDate.ToUniversalTime().Ticks;"
               "command=$p.CommandLine}|ConvertTo-Json -Compress}")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, check=True)
    if not result.stdout.strip():
        return None
    obj = json.loads(result.stdout)
    return {'pid': obj['pid'], 'exe': obj['exe'], 'created': obj['created'],
            'command_sha256': hashlib.sha256((obj['command'] or '').encode()).hexdigest()}


def owns_process(saved, actual):
    return actual is not None and all(saved.get(k) == actual.get(k) for k in ('pid', 'exe', 'created', 'command_sha256'))


def api_identity(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/runtime', timeout=2) as response:
        body = json.load(response)
    require(body.get('data', {}).get('implementation') == 'weknora-python-port', 'API_IMPLEMENTATION_MISMATCH')
    return body


async def collect_usage(cfg):
    """Read the current local principal's provider-call ledger; never estimates absent usage."""
    import psycopg
    from psycopg.rows import dict_row

    env = environment(cfg)
    names = ('DATABASE_URL', 'DATABASE_SYSTEM_ID', 'DATABASE_NAME')
    expected = {name: env.get('WEKNORA_PORT_' + name, '') for name in names}
    require(all(expected.values()), 'EXPLICIT_POSTGRES_IDENTITY_REQUIRED')
    conn = await psycopg.AsyncConnection.connect(
        expected['DATABASE_URL'], autocommit=True, row_factory=dict_row, connect_timeout=5
    )
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        identity = await (await conn.execute(
            'SELECT system_identifier::text AS system_id,current_database() AS database '
            'FROM pg_control_system()'
        )).fetchone()
        require(identity['system_id'] == expected['DATABASE_SYSTEM_ID'] and
                identity['database'] == expected['DATABASE_NAME'], 'POSTGRES_PHYSICAL_IDENTITY_MISMATCH')
        principal = await (await conn.execute(
            "SELECT tenant_id,subject_id FROM port_local_principal WHERE slot='local'"
        )).fetchone()
        require(principal is not None, 'EXISTING_LOCAL_PRINCIPAL_REQUIRED')
        rows = await (await conn.execute(
            'SELECT provider,model,capability,outcome,usage FROM port_provider_calls '
            'WHERE tenant_id=%s AND subject_id=%s ORDER BY created_at,call_id',
            (principal['tenant_id'], principal['subject_id']),
        )).fetchall()
        models = await (await conn.execute(
            "SELECT id,role,name,source,status,parameters->>'provider' AS provider FROM models "
            "WHERE tenant_id=%s AND subject_id=%s AND status='active' AND deleted_at IS NULL "
            'ORDER BY role,id',
            (principal['tenant_id'], principal['subject_id']),
        )).fetchall()
        knowledge_bases = await (await conn.execute(
            'SELECT id,name,cloud_allowed FROM knowledge_bases '
            'WHERE tenant_id=%s AND deleted_at IS NULL ORDER BY created_at,id',
            (principal['tenant_id'],),
        )).fetchall()
        await conn.execute('ROLLBACK')
    finally:
        await conn.close()

    groups = {}
    for row in rows:
        key = (row['provider'], row['model'], row['capability'])
        group = groups.setdefault(key, {
            'provider': key[0], 'model': key[1], 'capability': key[2],
            'calls': 0, 'reported': 0, 'unreported': 0, 'unknown_outcome': 0,
            'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0,
            '_split_complete': True,
        })
        group['calls'] += 1
        if row['outcome'] == 'UNKNOWN':
            group['unknown_outcome'] += 1
        usage = row['usage']
        valid = (isinstance(usage, dict) and usage.get('status') == 'REPORTED' and
                 type(usage.get('total_tokens')) is int and usage['total_tokens'] >= 0)
        has_prompt = isinstance(usage, dict) and usage.get('prompt_tokens') is not None
        has_completion = isinstance(usage, dict) and usage.get('completion_tokens') is not None
        split_valid = (has_prompt and has_completion and
                       type(usage['prompt_tokens']) is int and usage['prompt_tokens'] >= 0 and
                       type(usage['completion_tokens']) is int and usage['completion_tokens'] >= 0 and
                       usage['prompt_tokens'] + usage['completion_tokens'] == usage.get('total_tokens'))
        split_absent = not has_prompt and not has_completion
        valid = valid and (split_valid or split_absent)
        if valid:
            group['reported'] += 1
            group['total_tokens'] += usage['total_tokens']
            if split_valid:
                group['prompt_tokens'] += usage['prompt_tokens']
                group['completion_tokens'] += usage['completion_tokens']
            else:
                group['_split_complete'] = False
        else:
            group['unreported'] += 1
    for group in groups.values():
        if group['reported'] == 0:
            group['prompt_tokens'] = group['completion_tokens'] = group['total_tokens'] = None
        elif not group['_split_complete']:
            group['prompt_tokens'] = group['completion_tokens'] = None
        del group['_split_complete']
    return {
        'postgres_system_id': identity['system_id'], 'database': identity['database'],
        'tenant_id': principal['tenant_id'], 'subject_id': principal['subject_id'],
        'calls': len(rows), 'reported': sum(g['reported'] for g in groups.values()),
        'unreported': sum(g['unreported'] for g in groups.values()),
        'unknown_outcome': sum(g['unknown_outcome'] for g in groups.values()),
        'groups': sorted(groups.values(), key=lambda g: (g['provider'], g['model'], g['capability'])),
        'models': models, 'knowledge_bases': knowledge_bases,
    }


def save(path, state):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def stop_owned(state):
    stopped = []
    for name in ('ui', 'worker', 'api'):
        saved = state.get('processes', {}).get(name)
        if not saved:
            continue
        actual = process_identity(saved['pid'])
        if actual is None:
            continue
        require(owns_process(saved, actual), 'PROCESS_IDENTITY_CHANGED_REFUSE_STOP_' + name.upper())
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                        f"Stop-Process -Id {int(saved['pid'])} -ErrorAction Stop"], check=True, capture_output=True)
        stopped.append(name)
    return stopped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'status', 'check', 'usage'))
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    for directory in cfg['environment'].get('PYTHONPATH', '').split(os.pathsep):
        if directory:
            sys.path.insert(0, directory)
    if args.action == 'usage':
        # psycopg async connections need Selector on Windows, as check/start do.
        loop = asyncio.SelectorEventLoop()
        try:
            print(json.dumps(loop.run_until_complete(collect_usage(cfg))))
        finally:
            loop.close()
        return
    state_dir = Path(cfg['state_dir']);state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / 'processes.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    require(not state or state.get('source_root') == str(ROOT), 'STATE_SOURCE_ROOT_MISMATCH')
    if args.action == 'stop':
        print(json.dumps({'stopped': stop_owned(state), 'dependencies': 'retained_running'}))
        save(state_path, {**state, 'stopped_at': time.time()})
        return
    containers = [(row, inspect_container(row, cfg)) for row in cfg['containers']]
    if args.action == 'status':
        active = {name: owns_process(saved, process_identity(saved['pid'])) for name, saved in state.get('processes', {}).items()}
        result = {'processes': active, 'containers': {r['service']: o['State']['Status'] for r, o in containers},
                  'source_root': str(ROOT)}
        if active.get('api'):
            api_identity(cfg['api_port']);result['api_identity'] = 'weknora-python-port'
        print(json.dumps(result));return
    if args.action == 'start':
        require(not any(process_identity(p['pid']) is not None for p in state.get('processes', {}).values()), 'RECORDED_PROCESSES_STILL_PRESENT')
        for port in (cfg['api_port'], cfg['ui_port']):
            with socket.socket() as probe:
                require(probe.connect_ex(('127.0.0.1', port)) != 0, 'PORT_OCCUPIED_REFUSE_REPLACE')
        for row, obj in containers:
            if not obj['State']['Running']:
                subprocess.run(['docker', 'start', row['id']], check=True, capture_output=True)
    loop = asyncio.SelectorEventLoop()
    try:
        identity = loop.run_until_complete(verify_resources(cfg))
    finally:
        loop.close()
    if args.action == 'check':
        print(json.dumps(identity));return
    env = environment(cfg, identity['redis_run_id'])
    for directory in ('TEMP', 'TMP', 'XDG_CACHE_HOME'):
        env[directory] = str(state_dir / 'tmp')
    (state_dir / 'tmp').mkdir(exist_ok=True)
    commands = {
        'api': [cfg['python'], '-B', '-m', 'backend.weknora_port.entrypoint', '--host', '127.0.0.1', '--port', str(cfg['api_port'])],
        'worker': [cfg['python'], '-B', '-m', 'backend.weknora_port.worker'],
        'ui': [cfg['node'], str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(cfg['ui_port']), '--strictPort'],
    }
    require(Path(commands['ui'][1]).is_file(), 'EXISTING_FRONTEND_DEPENDENCIES_REQUIRED')
    state = {'source_root': str(ROOT), 'started_at': time.time(), 'identity': identity, 'processes': {}}
    try:
        for name, command in commands.items():
            with (state_dir / (name + '.stdout.log')).open('ab') as out, (state_dir / (name + '.stderr.log')).open('ab') as err:
                process = subprocess.Popen(command, cwd=ROOT / 'frontend' if name == 'ui' else ROOT, env=env,
                                           stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
            actual = process_identity(process.pid)
            if actual is None:
                # This handle belongs to the process just created, not a PID lookup.
                process.terminate()
                raise RuntimeError('CHILD_START_FAILED_' + name.upper())
            state['processes'][name] = actual;save(state_path, state)
            if name == 'api':
                deadline = time.monotonic() + 30
                while True:
                    try:
                        api_identity(cfg['api_port']);break
                    except (OSError, ValueError):
                        require(process.poll() is None and time.monotonic() < deadline, 'API_NOT_READY_SEE_LOCAL_LOG')
                        time.sleep(.25)
        time.sleep(1)
        require(all(owns_process(p, process_identity(p['pid'])) for p in state['processes'].values()), 'CHILD_EXITED_SEE_LOCAL_LOG')
        print(json.dumps({'status': 'started', 'identity': identity, 'logs': str(state_dir), 'ui': f"http://127.0.0.1:{cfg['ui_port']}"}))
    except BaseException:
        stop_owned(state)
        raise


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Connection exceptions can contain credentials. Emit only stable error codes.
        message = str(exc)
        print(message if message.isupper() and ' ' not in message and '\n' not in message else type(exc).__name__, file=sys.stderr)
        sys.exit(1)
