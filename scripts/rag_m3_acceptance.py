"""M3 isolated, single-send A/B controller and passive normal-chain observer.

No command calls a provider except explicit query-case/batch after prepare/start.
Gold and expected answers are excluded from frozen execution inputs. Existing
UNKNOWN calls and historical ledgers are never edited or retried.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
for _path in (str(REPO), 'E:/WK/deps/reader', str(REPO / 'var/weknora-port/runtime-deps')):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from scripts import p32_real_acceptance as base

OUT = Path('E:/WK/run/rag-m3-20261017')
MAXIMUM = {'end_to_end': 24, 'provider_calls': 120, 'reported_tokens': 450000}
BASELINE = '862b0642c3136dcbe12ab1e2914389b67fe8d2de'
UPSTREAM = '3e8b0bfc80b845b2d4b2ed683994748741450a97'
ID = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


def bind():
    base.OUT = OUT
    base.P31_RUNNER = Path(__file__).resolve()


def save(path, value):
    base.save(path, value)


def state():
    bind()
    return base.state()


def code_sha():
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()


def source_fingerprint():
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '--', 'backend/weknora_port',
        'scripts/p32_real_acceptance.py', 'scripts/weknora_port_p9_observed_api.py',
        'scripts/weknora_port_p17_observed_api.py', 'scripts/weknora_port_p8_observed_api.py'], cwd=REPO, text=True).splitlines()
    return base.sha(json.dumps({name: base.sha((REPO / name).read_bytes()) for name in names}, sort_keys=True).encode())


def freeze_plan(path):
    raw = base.read(path)
    if raw.get('schema') != 'rag-m3-plan/v1':
        raise RuntimeError('M3_PLAN_SCHEMA_INVALID')
    cases, seen = [], set()
    allowed = ('case_id', 'tenant_id', 'knowledge_base_id', 'document_scope', 'question',
               'title', 'source_dataset', 'document_id', 'session_group', 'parent_case_id')
    for item in raw.get('cases', []):
        cid = item.get('case_id', '')
        if not isinstance(cid, str) or not ID.fullmatch(cid) or cid in seen:
            raise RuntimeError('M3_CASE_ID_INVALID')
        seen.add(cid)
        if item.get('tenant_id', 332) != 332 or not item.get('knowledge_base_id') or not isinstance(item.get('question'), str) or not item['question'].strip():
            raise RuntimeError('M3_CASE_SCOPE_OR_QUESTION_INVALID')
        row = {k: item[k] for k in allowed if k in item}
        row.setdefault('tenant_id', 332)
        row.setdefault('document_scope', [])
        if not isinstance(row['document_scope'], list) or not all(isinstance(x, str) and x for x in row['document_scope']):
            raise RuntimeError('M3_DOCUMENT_SCOPE_INVALID')
        row.setdefault('session_group', cid)
        if not ID.fullmatch(row['session_group']):
            raise RuntimeError('M3_SESSION_GROUP_INVALID')
        cases.append(row)
    if not cases:
        raise RuntimeError('M3_EMPTY_PLAN')
    by_id = {x['case_id']: x for x in cases}
    schedule, scheduled = [], set()
    for entry in raw.get('schedule', []):
        cid, variant = entry.get('case_id'), entry.get('variant')
        if cid not in by_id or variant not in ('A', 'B') or (cid, variant) in scheduled:
            raise RuntimeError('M3_SCHEDULE_INVALID')
        case = by_id[cid]
        parent_id = case.get('parent_case_id')
        if parent_id:
            parent = by_id.get(parent_id)
            if not parent or (parent_id, variant) not in scheduled or any(case[k] != parent[k] for k in ('session_group', 'knowledge_base_id', 'document_scope')):
                raise RuntimeError('M3_PARENT_SCOPE_OR_ORDER_INVALID')
        elif any(by_id[old]['session_group'] == case['session_group'] and v == variant for old, v in scheduled):
            raise RuntimeError('M3_SHARED_SESSION_REQUIRES_PARENT')
        scheduled.add((cid, variant))
        schedule.append({'case_id': cid, 'variant': variant})
    if scheduled != {(cid, v) for cid in by_id for v in ('A', 'B')}:
        raise RuntimeError('M3_EXACTLY_ONE_A_B_REQUIRED')
    limits = {k: raw.get('limits', {}).get(k, value) for k, value in MAXIMUM.items()}
    if any(type(limits[k]) is not int or not 0 < limits[k] <= cap for k, cap in MAXIMUM.items()) or len(schedule) > limits['end_to_end']:
        raise RuntimeError('M3_LIMIT_INVALID')
    expected = raw.get('admission', {}).get('projected_tokens_per_case', raw.get('expected_tokens_per_case', 40000))
    if type(expected) is not int or expected <= 0 or expected > limits['reported_tokens']:
        raise RuntimeError('M3_EXPECTED_TOKENS_INVALID')
    return {'schema': raw['schema'], 'status': 'FROZEN', 'path': str(Path(path).resolve()),
            'sha256': base.sha(Path(path).read_bytes()), 'cases': cases, 'schedule': schedule,
            'limits': limits, 'expected_tokens_per_case': expected, 'tenants': [],
            'gold_loaded': False}


def validate(path):
    plan = freeze_plan(path)
    result = {'status': 'VALID_OFFLINE_NO_SERVICES', 'case_count': len(plan['cases']),
              'end_to_end_count': len(plan['schedule']), 'plan_sha256': plan['sha256'],
              'limits': plan['limits'], 'source_commit': code_sha(), 'provider_calls': 0}
    print(json.dumps(result))
    return result


async def role_identity(subject):
    conn = await base.pg()
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        role_row = await (await conn.execute('SELECT roles FROM port_model_configuration WHERE tenant_id=332 AND subject_id=%s', (subject,))).fetchone()
        configured = (role_row or {}).get('roles', {})
        rows = await (await conn.execute("SELECT id,role,name,type,source,parameters,identity_sha256,physical_sha256 FROM models WHERE tenant_id=332 AND subject_id=%s AND status='active' AND deleted_at IS NULL", (subject,))).fetchall()
        result = {}
        for role in ('cheap', 'expensive', 'embedding', 'rerank'):
            cfg = configured.get(role) or {}
            matches = [r for r in rows if r['role'] == role and r['name'] == cfg.get('model')
                       and (r['parameters'].get('provider') or r['source']) == cfg.get('provider')
                       and r['parameters'].get('base_url') == cfg.get('base_url')
                       and (not cfg.get('source') or r['source'] == cfg['source'])]
            if len(matches) != 1:
                raise RuntimeError('M3_EXISTING_ROLE_MODEL_NOT_UNIQUE_' + role)
            row = matches[0]
            result[role] = {k: row[k] for k in ('id', 'role', 'name', 'type', 'source', 'identity_sha256', 'physical_sha256')}
            result[role]['provider'] = cfg['provider']
        if (result['expensive']['provider'], result['expensive']['name'], result['expensive']['type']) != ('deepseek', 'deepseek-flash', 'KnowledgeQA'):
            raise RuntimeError('M3_DEEPSEEK_CANDIDATE_IDENTITY_MISMATCH')
        await conn.execute('ROLLBACK')
        return result
    finally:
        await conn.close()


async def resource_identity(plan, subject):
    """Read current owner/consent/source versions without uploading or indexing."""
    conn = await base.pg()
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        result = []
        for kb_id in sorted({c['knowledge_base_id'] for c in plan['cases']}):
            kb = await (await conn.execute('SELECT id,cloud_allowed FROM knowledge_bases WHERE id=%s AND tenant_id=332 AND deleted_at IS NULL', (kb_id,))).fetchone()
            if not kb or not kb['cloud_allowed']:
                raise RuntimeError('M3_PLAN_KB_MISSING_OR_CLOUD_DENIED')
            documents = await (await conn.execute('SELECT id,file_hash,metadata FROM knowledges WHERE tenant_id=332 AND knowledge_base_id=%s AND deleted_at IS NULL ORDER BY id', (kb_id,))).fetchall()
            available = {d['id'] for d in documents}
            for case in plan['cases']:
                if case['knowledge_base_id'] == kb_id and not set(case['document_scope']).issubset(available):
                    raise RuntimeError('M3_PLAN_DOCUMENT_SCOPE_DENIED')
            chunks = await (await conn.execute('SELECT id,knowledge_id,content,chunk_type,metadata FROM chunks WHERE tenant_id=332 AND knowledge_base_id=%s AND deleted_at IS NULL AND is_enabled=true ORDER BY id', (kb_id,))).fetchall()
            result.append({'knowledge_base_id': kb_id, 'cloud_allowed': True, 'document_count': len(documents),
                           'chunk_count': len(chunks), 'documents_sha256': base.rows_sha(documents),
                           'chunks_sha256': base.rows_sha(chunks)})
        await conn.execute('ROLLBACK')
        return result
    finally:
        await conn.close()


async def prepare(path):
    bind()
    if (OUT / 'state.json').exists():
        raise RuntimeError('M3_EXISTING_STATE_NO_OVERWRITE')
    plan = freeze_plan(path)
    if code_sha() != BASELINE:
        raise RuntimeError('M3_BASELINE_HEAD_MISMATCH')
    source, identity = await base.preflight(plan)
    roles = await role_identity(source['subject'])
    resources = await resource_identity(plan, source['subject'])
    value = {'schema': 'rag-m3-acceptance/v1', 'created_at': base.now(),
             'status': 'PREPARED_NO_LIVE_ACTIONS', 'source_commit': code_sha(),
             'upstream_commit': UPSTREAM, 'executor_sha256': base.sha(Path(__file__).read_bytes()),
             'source_fingerprint': source_fingerprint(),
             'plan': plan, 'identity': identity, 'roles': roles, 'resources': resources, 'origin': base.ORIGIN,
             'processes': {}, 'cases': {}, 'groups': {}, 'end_to_end_attempts': 0,
             'primary_tenant': {'tenant_id': 332, 'subject_id': source['subject'], 'port': 56425,
                                'storage_root': source['storage_root'],
                                'redis_prefix': 'weknora-python-m3-20261017:332:' + uuid.uuid4().hex,
                                'api_evidence': str(OUT / 'evidence-332')}}
    save(OUT / 'state.json', value)
    save(OUT / 'plan-frozen.json', plan)
    save(OUT / 'identity-before.json', identity)
    print('M3_PREPARED_NO_SERVICES_NO_PROVIDER_CALLS', OUT)


def check_frozen(value):
    if code_sha() != value['source_commit'] or base.sha(Path(__file__).read_bytes()) != value['executor_sha256'] or source_fingerprint() != value['source_fingerprint']:
        raise RuntimeError('M3_EXECUTED_SOURCE_CHANGED')
    plan_path = Path(value['plan']['path'])
    if not plan_path.exists() or base.sha(plan_path.read_bytes()) != value['plan']['sha256']:
        raise RuntimeError('M3_FROZEN_PLAN_CHANGED')


async def usage_snapshot(value=None):
    value = value or state()
    before = {(int(x['tenant_id']), x['subject_id'], x['call_id']) for x in value['identity']['provider_calls_before']}
    conn = await base.pg()
    try:
        rows = await (await conn.execute('SELECT tenant_id,subject_id,call_id,request_id,capability,session_id,provider,model,model_id,wire_sha256,outcome,usage,created_at,finished_at FROM port_provider_calls WHERE tenant_id=332 ORDER BY created_at,call_id')).fetchall()
    finally:
        await conn.close()
    calls = [r for r in rows if (int(r['tenant_id']), r['subject_id'], r['call_id']) not in before]
    tokens = sum((r.get('usage') or {}).get('total_tokens') or 0 for r in calls)
    return {'at': base.now(), 'provider_calls': len(calls), 'reported_tokens': tokens,
            'unknown_outcomes': sum(r['outcome'] != 'RESPONSE_RECEIVED' for r in calls),
            'missing_usage': sum((r.get('usage') or {}).get('status') != 'REPORTED' for r in calls),
            'calls': calls}


def budget_check(value, usage, *, new_case=False):
    if usage['unknown_outcomes'] or usage['missing_usage']:
        raise RuntimeError('M3_UNCERTAIN_PROVIDER_STATE_STOP')
    limits = value['plan']['limits']
    if usage['provider_calls'] >= limits['provider_calls'] or usage['reported_tokens'] >= limits['reported_tokens']:
        raise RuntimeError('M3_PROVIDER_OR_TOKEN_CAP_STOP')
    if new_case and (value['end_to_end_attempts'] >= limits['end_to_end'] or usage['reported_tokens'] + value['plan']['expected_tokens_per_case'] > limits['reported_tokens']):
        raise RuntimeError('M3_NEXT_CASE_WOULD_EXCEED_REMAINING_ALLOWANCE')


def start():
    import httpx
    bind()
    value = state()
    check_frozen(value)
    item = value['primary_tenant']
    if any(p.get('status') not in ('STOPPED', 'ALREADY_EXITED') for p in value['processes'].values()):
        raise RuntimeError('M3_SERVICE_ALREADY_RECORDED')
    process_key = 'api-332' if not value['processes'] else 'api-332-r' + str(len(value['processes']) + 1)
    base.launch(process_key, 332, item['subject_id'], item['port'], item['storage_root'], item['redis_prefix'], item['api_evidence'])
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            response = httpx.get('http://127.0.0.1:' + str(item['port']) + '/readyz', trust_env=False, timeout=2)
            if response.status_code == 200:
                save(OUT / 'ready.json', response.json())
                print('M3_OWNED_API_READY_NO_QUERY')
                return
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    raise RuntimeError('M3_API_NOT_READY_NO_QUERY_SENT')


async def fresh_configuration(session_id, variant, value):
    """Lock a fresh experiment session; only B's dedicated model field changes."""
    subject = value['primary_tenant']['subject_id']
    conn = await base.pg()
    try:
        async with conn.transaction():
            rows = await (await conn.execute('SELECT id,context_config,summary_model_id,summary_parameters,knowledge_base_scope,document_scope FROM sessions WHERE id=%s AND tenant_id=332 AND subject_id=%s AND deleted_at IS NULL FOR UPDATE', (session_id, subject))).fetchall()
            if len(rows) != 1 or await (await conn.execute('SELECT 1 FROM messages WHERE session_id=%s LIMIT 1', (session_id,))).fetchone():
                raise RuntimeError('M3_FRESH_OWNED_SESSION_GUARD')
            before = dict(rows[0])
            if variant == 'B':
                model_id = value['roles']['expensive']['id']
                models = await (await conn.execute("SELECT id FROM models WHERE id=%s AND tenant_id=332 AND subject_id=%s AND role='expensive' AND type='KnowledgeQA' AND status='active' AND deleted_at IS NULL FOR SHARE", (model_id, subject))).fetchall()
                if len(models) != 1:
                    raise RuntimeError('M3_CANDIDATE_MODEL_NO_LONGER_AVAILABLE')
                rows = await (await conn.execute("UPDATE sessions SET context_config=jsonb_set(COALESCE(context_config,'{}'::jsonb),'{conversation}',COALESCE(context_config->'conversation','{}'::jsonb)||jsonb_build_object('query_understand_model_id',%s::text),true) WHERE id=%s AND tenant_id=332 AND subject_id=%s AND NOT EXISTS(SELECT 1 FROM messages WHERE session_id=%s) RETURNING id,context_config,summary_model_id,summary_parameters,knowledge_base_scope,document_scope", (model_id, session_id, subject, session_id))).fetchall()
                if len(rows) != 1:
                    raise RuntimeError('M3_B_CONFIG_UPDATE_GUARD')
            return {'variant': variant, 'before': before, 'after': dict(rows[0]), 'changed_field': 'context_config.conversation.query_understand_model_id' if variant == 'B' else None}
    finally:
        await conn.close()


class SSEClock:
    """Incremental SSE decoder that retains byte boundaries only in saved SSE."""
    def __init__(self):
        self.pending = b''
        self.events = []
        self.malformed = 0

    def feed(self, raw):
        self.pending += raw
        emitted = []
        while True:
            match = re.search(b'\r?\n\r?\n', self.pending)
            if not match:
                break
            block, self.pending = self.pending[:match.start()], self.pending[match.end():]
            data = b'\n'.join(line[5:].lstrip() for line in block.splitlines() if line.startswith(b'data:'))
            if not data or data == b'[DONE]':
                continue
            try:
                event = json.loads(data)
                if not isinstance(event, dict):
                    raise ValueError()
                self.events.append(event)
                emitted.append(event)
            except (ValueError, UnicodeError):
                self.malformed += 1
        return emitted


async def stream_query(url, body, op, folder):
    import httpx
    bind()
    base.operation(op, 'POST', url, body)
    decoder, started = SSEClock(), time.perf_counter()
    timings = {'request_started_at': base.now(), 'first_progress_seconds': None,
               'first_answer_seconds': None, 'complete_seconds': None, 'events': []}
    folder.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(headers={'Origin': base.ORIGIN}, trust_env=False, timeout=600) as client:
            async with client.stream('POST', url, json=body) as response:
                timings.update(http_status=response.status_code, response_headers_seconds=time.perf_counter() - started)
                with (folder / 'answer.sse').open('xb') as stream:
                    async for raw in response.aiter_bytes():
                        stream.write(raw)
                        for event in decoder.feed(raw):
                            elapsed = time.perf_counter() - started
                            kind = event.get('response_type', event.get('type'))
                            timings['events'].append({'type': kind, 'seconds': elapsed, 'at': base.now(), 'data': event.get('data') if kind == 'progress' else None})
                            field = {'progress': 'first_progress_seconds', 'answer': 'first_answer_seconds', 'complete': 'complete_seconds'}.get(kind)
                            if field and timings[field] is None and (kind != 'answer' or event.get('content')):
                                timings[field] = elapsed
                raw = (folder / 'answer.sse').read_bytes()
        base.finish(op, 'RESPONSE_RECEIVED', raw, response.status_code)
    except BaseException:
        base.finish(op, 'UNKNOWN_NO_RETRY')
        timings['error_state'] = 'UNKNOWN_NO_RETRY'
        save(folder / 'client-timing.json', timings)
        raise
    timings.update(post_seconds=time.perf_counter() - started, completed_at=base.now(), malformed_events=decoder.malformed, trailing_bytes=len(decoder.pending))
    save(folder / 'client-timing.json', timings)
    if response.status_code != 200:
        raise RuntimeError('M3_QUERY_HTTP_' + str(response.status_code))
    complete = [e for e in decoder.events if e.get('response_type') == 'complete']
    errors = [e for e in decoder.events if e.get('response_type') == 'error']
    if len(complete) != 1 or errors or decoder.malformed:
        raise RuntimeError('M3_INCOMPLETE_OR_ERROR_SSE_NO_RETRY')
    if (complete[0].get('data') or {}).get('status') in ('failed', 'cancelled'):
        raise RuntimeError('M3_FAILED_OR_CANCELLED_SSE_NO_RETRY')
    return timings, decoder.events, ''.join(e.get('content', '') for e in decoder.events if e.get('response_type') == 'answer'), complete[0]


def confirm_history(messages, run_id, answer):
    persisted = [m for m in messages.get('data', []) if m.get('role') == 'assistant' and m.get('request_id') == run_id]
    if len(persisted) != 1 or not persisted[0].get('is_completed') or persisted[0]['content'] != answer:
        raise RuntimeError('M3_SSE_HISTORY_MISMATCH')
    if persisted[0].get('turn_status') in ('error', 'cancelled'):
        raise RuntimeError('M3_FAILED_OR_CANCELLED_HISTORY_NO_RETRY')


async def query_case(case_id, variant):
    value = state()
    check_frozen(value)
    key = case_id + '-' + variant
    if key in value['cases'] or any(x.get('status') != 'COMPLETED' for x in value['cases'].values()):
        raise RuntimeError('M3_CASE_ALREADY_ATTEMPTED_OR_UNCERTAIN_NO_RETRY')
    index = len(value['cases'])
    if index >= len(value['plan']['schedule']) or value['plan']['schedule'][index] != {'case_id': case_id, 'variant': variant}:
        raise RuntimeError('M3_FROZEN_SCHEDULE_ORDER_REQUIRED')
    usage = await usage_snapshot(value)
    budget_check(value, usage, new_case=True)
    if await role_identity(value['primary_tenant']['subject_id']) != value['roles']:
        raise RuntimeError('M3_FROZEN_ROLE_IDENTITY_CHANGED')
    save(OUT / 'usage-before-next.json', usage)
    case = next(c for c in value['plan']['cases'] if c['case_id'] == case_id)
    attempt_number = value.get('attempt_numbers', {}).get(key, 1)
    operation_key = key if attempt_number == 1 else key + '-attempt' + str(attempt_number)
    folder = OUT / 'cases' / operation_key
    record = {'case_id': case_id, 'variant': variant, 'tenant_id': 332, 'status': 'STARTED_NO_RETRY',
              'started_at': base.now(), 'attempt_number': attempt_number, 'artifact_dir': str(folder)}
    value['cases'][key] = record
    save(OUT / 'state.json', value)
    endpoint = 'http://127.0.0.1:' + str(value['primary_tenant']['port'])
    group = case['session_group'] + '-' + variant
    if case.get('parent_case_id'):
        parent = value['cases'].get(case['parent_case_id'] + '-' + variant)
        if not parent or parent.get('status') != 'COMPLETED' or group not in value['groups']:
            raise RuntimeError('M3_REAL_PARENT_REQUIRED')
        session = value['groups'][group]
        configuration = {'variant': variant, 'parent_case_id': case['parent_case_id'], 'session_id': session, 'reused_real_history': True, 'settings_changed': False}
    else:
        session = (await base.http_request('POST', endpoint + '/api/v1/sessions', body={'title': 'M3 ' + operation_key, 'knowledge_base_scope': [case['knowledge_base_id']], 'document_scope': case['document_scope']}, op='session-' + operation_key)).json()['data']['id']
        configuration = await fresh_configuration(session, variant, value)
        value['groups'][group] = session
    record.update(session_id=session, request_id=str(uuid.uuid4()), knowledge_base_id=case['knowledge_base_id'], document_scope=case['document_scope'], question=case['question'])
    value['end_to_end_attempts'] += 1
    save(OUT / 'state.json', value)
    save(folder / 'configuration.json', configuration)
    body = {'content': case['question'], 'request_id': record['request_id'], 'knowledge_base_scope': [case['knowledge_base_id']], 'document_scope': case['document_scope'], 'reasoning_effort': 'off'}
    save(folder / 'request.json', body)
    timings, events, answer, completion = await stream_query(endpoint + '/api/v1/sessions/' + session + '/messages', body, 'query-' + operation_key, folder)
    messages = (await base.http_request('GET', endpoint + '/api/v1/sessions/' + session + '/messages', op='history-' + operation_key)).json()
    save(folder / 'messages.json', messages)
    run_id = completion['id']
    confirm_history(messages, run_id, answer)
    usage = await usage_snapshot(value)
    record.update(status='COMPLETED', run_id=run_id, answer=answer, complete=completion, client_timing=timings, sse_equals_history=True, completed_at=base.now())
    save(folder / 'result.json', record)
    save(folder / 'usage-after.json', usage)
    value = state()
    value['cases'][key] = record
    save(OUT / 'state.json', value)
    print(json.dumps({'case': key, 'seconds': timings['post_seconds'], 'run_id': run_id, 'provider_calls': usage['provider_calls'], 'reported_tokens': usage['reported_tokens'], 'unknown_outcomes': usage['unknown_outcomes']}))


async def batch(count=4):
    if type(count) is not int or not 1 <= count <= 6:
        raise RuntimeError('M3_BATCH_COUNT_MUST_BE_1_TO_6')
    value = state()
    remaining = value['plan']['schedule'][len(value['cases']):]
    for row in remaining[:count]:
        await query_case(row['case_id'], row['variant'])


async def resume_observer_fix():
    """One approved independent attempt after the proven zero-send gate bug.

    This never replays HTTP, clears usage, rewrites old evidence, or changes the
    plan. It preserves the failed attempt and consumes its existing E2E slot.
    """
    value = state()
    if value.get('observer_fix_recovery'):
        raise RuntimeError('M3_RECOVERY_ALREADY_RECORDED')
    if code_sha() != value['source_commit'] or source_fingerprint() != value['source_fingerprint']:
        raise RuntimeError('M3_RECOVERY_PRODUCTION_SOURCE_CHANGED')
    if base.sha(Path(value['plan']['path']).read_bytes()) != value['plan']['sha256']:
        raise RuntimeError('M3_RECOVERY_PLAN_CHANGED')
    if any(p.get('status') not in ('STOPPED', 'ALREADY_EXITED') for p in value['processes'].values()):
        raise RuntimeError('M3_STOP_OLD_OBSERVER_BEFORE_RECOVERY')
    key = 'scifact-339-A'
    if set(value['cases']) != {key} or value['end_to_end_attempts'] != 1:
        raise RuntimeError('M3_RECOVERY_REQUIRES_EXACT_FIRST_ATTEMPT')
    usage = await usage_snapshot(value)
    if usage['provider_calls'] != 0 or usage['reported_tokens'] != 0 or usage['unknown_outcomes'] != 0:
        raise RuntimeError('M3_RECOVERY_REQUIRES_PROVEN_ZERO_PROVIDER_SENDS')
    folder = OUT / 'cases' / key
    decoder = SSEClock()
    decoder.feed((folder / 'answer.sse').read_bytes())
    errors = [e for e in decoder.events if e.get('response_type') == 'error']
    complete = [e for e in decoder.events if e.get('response_type') == 'complete']
    if decoder.malformed or len(errors) != 1 or errors[0].get('data', {}).get('error_code') != 'RUNTIME_DURABLE_GATE_REQUIRED' or len(complete) != 1 or complete[0].get('data', {}).get('status') != 'failed':
        raise RuntimeError('M3_RECOVERY_REQUIRES_EXACT_RECORDED_GATE_FAILURE')
    operation = base.read(OUT / 'operations.json').get('query-' + key, {})
    if operation.get('status') != 'RESPONSE_RECEIVED' or operation.get('response_sha256') != base.sha((folder / 'answer.sse').read_bytes()):
        raise RuntimeError('M3_RECOVERY_HTTP_RESULT_NOT_CONFIRMED')
    old_script = OUT / 'executor-revisions' / (value['executor_sha256'] + '.py')
    if not old_script.is_file() or base.sha(old_script.read_bytes()) != value['executor_sha256']:
        raise RuntimeError('M3_RECOVERY_ORIGINAL_EXECUTOR_ARCHIVE_REQUIRED')
    conn = await base.pg()
    try:
        row = await (await conn.execute('SELECT status,error_code FROM port_turns WHERE run_id=%s AND session_id=%s AND tenant_id=332 AND subject_id=%s', (complete[0]['id'], value['cases'][key]['session_id'], value['primary_tenant']['subject_id']))).fetchone()
        if not row or (row['status'], row['error_code']) != ('error', 'RUNTIME_DURABLE_GATE_REQUIRED'):
            raise RuntimeError('M3_RECOVERY_DURABLE_FAILURE_MISMATCH')
    finally:
        await conn.close()
    archive = OUT / 'recovery' / 'first-failure-state.json'
    if archive.exists():
        raise RuntimeError('M3_RECOVERY_ARCHIVE_EXISTS_NO_OVERWRITE')
    save(archive, value)
    failed = dict(value['cases'][key])
    failed.update(status='FAILED_OBSERVER_BEFORE_PROVIDER_SEND', error_code='RUNTIME_DURABLE_GATE_REQUIRED',
                  run_id=complete[0]['id'], artifact_dir=str(folder), attempt_number=1, provider_calls=0)
    value['failed_attempts'] = [failed]
    value['cases'], value['groups'] = {}, {}
    value['attempt_numbers'] = {key: 2}
    value['observer_fix_recovery'] = {'at': base.now(), 'old_executor_sha256': value['executor_sha256'],
                                    'new_executor_sha256': base.sha(Path(__file__).read_bytes()),
                                    'zero_provider_usage': usage, 'old_failure_counted_in_end_to_end': True,
                                    'independent_attempt_authorized_by': 'PARENT_COORDINATION_AFTER_ZERO_SEND_PROOF'}
    value['executor_sha256'] = base.sha(Path(__file__).read_bytes())
    save(OUT / 'state.json', value)
    print('M3_ZERO_SEND_FAILURE_PRESERVED_NEW_INDEPENDENT_ATTEMPT_READY_NO_HTTP_SENT')


def serial(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


async def resolved(value):
    return await value if inspect.isawaitable(value) else value


class LimitedGate:
    def __init__(self, original, lock):
        self.original, self.lock = original, lock
        self.original_authorize = original.authorize

    async def authorize(self, call):
        from backend.weknora_port.models.types import ProviderError
        async with self.lock:
            value = state()
            if call.context.session_id not in {c.get('session_id') for c in value['cases'].values() if c.get('status') == 'STARTED_NO_RETRY'}:
                raise ProviderError('M3_UNREGISTERED_SESSION_SEND_DENIED')
            usage = await usage_snapshot(value)
            # Earlier independent calls may still be in flight within one RAG
            # operation. UNKNOWN outside the current session is always terminal.
            other_unknown = [r for r in usage['calls'] if r['outcome'] != 'RESPONSE_RECEIVED' and (r['session_id'] != call.context.session_id or r.get('finished_at') is not None)]
            missing_settled_usage = any(r['outcome'] == 'RESPONSE_RECEIVED' and (r.get('usage') or {}).get('status') != 'REPORTED' for r in usage['calls'])
            if other_unknown or missing_settled_usage or usage['provider_calls'] >= value['plan']['limits']['provider_calls'] or usage['reported_tokens'] >= value['plan']['limits']['reported_tokens']:
                raise ProviderError('M3_EXPERIMENT_LIMIT_OR_UNKNOWN_STOP')
            return await self.original_authorize(call)

    async def finish(self, call, **kwargs):
        return await self.original.finish(call, **kwargs)


def install_gate_limit(gate, lock):
    # ApplicationRuntime requires model.transport.gate to remain the exact
    # durable gate instance. Preserve it and wrap only its bound authorize.
    if not getattr(gate, '_m3_limit_installed', False):
        limiter = LimitedGate(gate, lock)
        gate.authorize = limiter.authorize
        gate._m3_limit_installed = True
    return gate


def install_timing(model, output):
    from scripts.weknora_port_p9_observed_api import install_wire_observer
    from scripts.weknora_port_p17_observed_api import RawResponse
    model = install_wire_observer(model, output)
    original_open = model.transport.open
    active = ContextVar('m3_active_provider_timing', default=None)
    async def request_hook(request):
        current = active.get()
        if current:
            record, started = current
            record.update(http_send_seconds=time.perf_counter() - started, http_send_at=base.now())
    async def response_hook(response):
        current = active.get()
        if current:
            record, started = current
            record.update(http_headers_seconds=time.perf_counter() - started, http_status=response.status_code)
    model.transport.client.event_hooks.setdefault('request', []).append(request_hook)
    model.transport.client.event_hooks.setdefault('response', []).append(response_hook)

    @asynccontextmanager
    async def opened(config, path, body, context, **kwargs):
        started = time.perf_counter()
        record = {'request_id': context.request_id, 'session_id': context.session_id,
                  'model_id': config.model_id, 'provider': config.provider, 'model': config.model,
                  'capability': kwargs['capability'], 'started_at': base.now(),
                  'stream': bool(body.get('stream')), 'first_body_seconds': None,
                  'first_valid_output_seconds': None, 'first_answer_seconds': None,
                  'effective_output_basis': 'PROVIDER_SSE_DELTA' if body.get('stream') else 'NOT_AVAILABLE_NON_STREAM'}
        target = output / 'provider-timing' / (base.sha(context.request_id.encode()) + '.json')
        save(target, record)
        token = active.set((record, started))
        try:
            async with original_open(config, path, body, context, **kwargs) as handle:
                record['headers_seconds'] = time.perf_counter() - started
                decoder = SSEClock()
                class TimedRaw(RawResponse):
                    def append(self, raw):
                        elapsed = time.perf_counter() - started
                        record['last_body_seconds'] = elapsed
                        if raw and record['first_body_seconds'] is None:
                            record['first_body_seconds'] = elapsed
                        if body.get('stream'):
                            for event in decoder.feed(raw):
                                for choice in event.get('choices', []):
                                    delta = choice.get('delta') or {}
                                    if any(delta.get(k) for k in ('content', 'reasoning_content', 'reasoning')) and record['first_valid_output_seconds'] is None:
                                        record['first_valid_output_seconds'] = elapsed
                                    if delta.get('content') and record['first_answer_seconds'] is None:
                                        record['first_answer_seconds'] = elapsed
                        super().append(raw)
                proxy = TimedRaw(handle.response, output, context.request_id, kwargs['capability'])
                handle.response = proxy
                try:
                    yield handle
                finally:
                    proxy.finish()
                record['outcome'] = 'NORMAL_CONTEXT_COMPLETED'
        except BaseException as error:
            record.update(outcome='NORMAL_CONTEXT_ERROR', error_type=type(error).__name__)
            raise
        finally:
            active.reset(token)
            record.update(transport_context_seconds=time.perf_counter() - started, finished_at=base.now())
            record['http_send_to_last_body_seconds'] = record['last_body_seconds'] - record['http_send_seconds'] if 'last_body_seconds' in record and 'http_send_seconds' in record else None
            record['http_send_to_headers_seconds'] = record['http_headers_seconds'] - record['http_send_seconds'] if 'http_headers_seconds' in record and 'http_send_seconds' in record else None
            save(target, record)
    model.transport.open = opened
    return model


def service():
    from backend.weknora_port.development import assemble
    from backend.weknora_port.entrypoint import create_app
    from backend.weknora_port.storage.history import LocalPrincipal
    from scripts.weknora_port_p8_observed_api import capture
    import uvicorn
    value = state()
    check_frozen(value)
    principal = LocalPrincipal(332, value['primary_tenant']['subject_id'])
    output = Path(value['primary_tenant']['api_evidence'])
    output.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def factory(settings):
        async with assemble(settings, principal=principal) as runtime:
            original_factory, lock = runtime.model_factory, asyncio.Lock()
            async def model_factory(config, key, gate):
                install_gate_limit(gate, lock)
                return install_timing(await resolved(original_factory(config, key, gate)), output)
            runtime.model_factory = model_factory
            ordinary = runtime.ordinary
            original_configuration, original_pipeline, original_prepare = ordinary.configuration, ordinary.pipeline_factory, ordinary.prepare_turn
            states, timelines = {}, {}
            async def configuration(turn):
                started = time.perf_counter()
                result = await resolved(original_configuration(turn))
                states[turn.run_id] = result.pipeline_state
                st = result.pipeline_state
                save(output / 'configuration' / (base.sha(turn.run_id.encode()) + '.json'),
                     {'run_id': turn.run_id, 'session_id': turn.session_id, 'configuration_seconds': time.perf_counter() - started,
                      'pipeline_state': {k: serial(getattr(st, k)) for k in ('query_understand_model_id', 'chat_model_id', 'vlm_model_id', 'rerank_model_id', 'enable_rewrite', 'enable_expansion', 'max_rounds', 'targets', 'rewrite_system_prompt', 'rewrite_user_prompt', 'context_template')},
                      'generation_options': serial(result.generation_options.get('options'))})
                return result
            async def pipeline(turn, progress):
                obj = await resolved(original_pipeline(turn, progress))
                old = obj.on_progress
                async def observe(stage, status, **fields):
                    st = states[turn.run_id]
                    record = timelines.setdefault(turn.run_id, {'run_id': turn.run_id, 'session_id': turn.session_id, 'question': turn.request.content, 'gold_loaded': False, 'stages': []})
                    entry = {'stage': stage, 'status': status, 'at': base.now(), 'perf_counter': time.perf_counter(), 'fields': serial(fields)}
                    if status == 'completed':
                        start = next((e for e in reversed(record['stages']) if e['stage'] == stage and e['status'] == 'started'), None)
                        entry['stage_seconds'] = entry['perf_counter'] - start['perf_counter'] if start else None
                    if status == 'completed':
                        entry.update({k: serial(getattr(st, k, [])) for k in ('search_results', 'rerank_results', 'merge_results')})
                        entry.update(rewrite_query=st.rewrite_query, intent=st.intent, needs_retrieval=st.needs_retrieval, history=serial(st.history))
                    record['stages'].append(entry)
                    save(output / 'stages' / (base.sha(turn.run_id.encode()) + '.json'), record)
                    if old:
                        return await resolved(old(stage, status, **fields))
                obj.on_progress = observe
                return obj
            async def prepare_turn(turn, progress=None):
                try:
                    result = await resolved(original_prepare(turn, progress))
                    save(output / 'captures' / (base.sha(turn.run_id.encode()) + '.json'), capture(result))
                    return result
                finally:
                    states.pop(turn.run_id, None)
                    timelines.pop(turn.run_id, None)
            ordinary.configuration, ordinary.pipeline_factory, ordinary.prepare_turn = configuration, pipeline, prepare_turn
            yield runtime

    original = create_app(runtime_factory=factory)
    async def timed_app(scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or not scope.get('path', '').endswith('/messages'):
            return await original(scope, receive, send)
        started, chunks = time.perf_counter(), []
        decoder = SSEClock()
        record = {'path': scope['path'], 'api_received_at': base.now(), 'response_started_seconds': None, 'response_complete_seconds': None, 'events': []}
        async def observed_receive():
            message = await receive()
            if message['type'] == 'http.request':
                chunks.append(message.get('body', b''))
                if not message.get('more_body'):
                    try:
                        record['request_id'] = json.loads(b''.join(chunks)).get('request_id')
                    except ValueError:
                        pass
            return message
        async def observed_send(message):
            if message['type'] == 'http.response.start':
                record.update(http_status=message['status'], response_started_seconds=time.perf_counter() - started)
            elif message['type'] == 'http.response.body':
                for event in decoder.feed(message.get('body', b'')):
                    record['events'].append({'type': event.get('response_type'), 'seconds': time.perf_counter() - started, 'at': base.now(), 'data': event.get('data') if event.get('response_type') == 'progress' else None})
                if not message.get('more_body'):
                    record['response_complete_seconds'] = time.perf_counter() - started
            await send(message)
        try:
            await original(scope, observed_receive, observed_send)
        finally:
            record.update(total_seconds=time.perf_counter() - started, finished_at=base.now())
            save(output / 'api-timing' / (base.sha(str(record.get('request_id', uuid.uuid4())).encode()) + '.json'), record)
    base.run(uvicorn.Server(uvicorn.Config(timed_app, host='127.0.0.1', port=value['primary_tenant']['port'], log_level='info')).serve())


async def snapshot():
    bind()
    await base.snapshot()
    usage = await usage_snapshot()
    save(OUT / 'usage.json', usage)
    print(json.dumps({k: v for k, v in usage.items() if k != 'calls'}, default=str))


async def summarize():
    await snapshot()
    value, snap = state(), base.read(OUT / 'snapshot.json')
    result = {'source_commit': value['source_commit'], 'upstream_commit': UPSTREAM,
              'plan_sha256': value['plan']['sha256'], 'cases': value['cases'],
              'usage': base.read(OUT / 'usage.json'), 'roles': value['roles'],
              'legacy_accounting_unchanged': snap['legacy_before'] == snap['legacy_after'],
              'evidence_root': str(OUT / 'evidence-332'), 'scoring_performed': False}
    result['failed_attempts'] = value.get('failed_attempts', [])
    result['end_to_end_attempts'] = value['end_to_end_attempts']
    result['resources_after'] = await resource_identity(value['plan'], value['primary_tenant']['subject_id'])
    result['source_resources_unchanged'] = result['resources_after'] == value['resources']
    save(OUT / 'summary.json', result)
    print('M3_SUMMARY_SAVED', OUT / 'summary.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('validate', 'prepare'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--plan', required=True)
    cmd = commands.add_parser('query-case')
    cmd.add_argument('case_id')
    cmd.add_argument('variant', choices=('A', 'B'))
    cmd = commands.add_parser('batch')
    cmd.add_argument('--count', type=int, default=4)
    for name in ('start', 'snapshot', 'summarize', 'stop', 'service', 'resume-observer-fix'):
        commands.add_parser(name)
    args = parser.parse_args()
    bind()
    if args.command == 'validate':
        validate(args.plan)
    elif args.command == 'prepare':
        base.run(prepare(args.plan))
    elif args.command == 'query-case':
        base.run(query_case(args.case_id, args.variant))
    elif args.command == 'batch':
        base.run(batch(args.count))
    elif args.command == 'resume-observer-fix':
        base.run(resume_observer_fix())
    elif args.command in ('snapshot', 'summarize'):
        base.run(globals()[args.command]())
    else:
        globals()[args.command]() if args.command != 'stop' else base.stop()


if __name__ == '__main__':
    main()
