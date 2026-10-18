"""Explicit, temporary owner execution fence; no account or monthly budget."""
from __future__ import annotations
from contextlib import contextmanager, asynccontextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from .models.types import ProviderError

_LOCK = threading.RLock()


def _safe_body(value):
    if isinstance(value, dict):
        secret = {'key', 'api_key', 'api-key', 'authorization', 'access_token', 'headers', 'custom_headers', 'app_secret', 'secret_key'}
        return {k: _safe_body(v) for k, v in value.items() if k.lower() not in secret}
    if isinstance(value, (list, tuple)): return [_safe_body(v) for v in value]
    return value


class LocalExecution:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.drive.upper() not in ('D:', 'E:'):
            raise ValueError('EXECUTION_ABSOLUTE_D_E_PATH_REQUIRED')
        self.evidence = self.path.parent / 'evidence'
        with self._ledger() as data:
            if Path(data.get('evidence_dir', self.evidence)).resolve() != self.evidence.resolve():
                raise ValueError('EXECUTION_EVIDENCE_PATH_INVALID')
            if any(row['status'] == 'INFLIGHT' for row in data['entries']):
                data['status'] = 'STOP'

    @contextmanager
    def _ledger(self):
        # The sidecar is never replaced; every process locks the same byte.
        with _LOCK, self.path.with_suffix(self.path.suffix + '.lock').open('a+b') as lock:
            lock.seek(0, 2)
            if not lock.tell(): lock.write(b'0'); lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                yield data
                tmp = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    with tmp.open('x', encoding='utf-8') as output:
                        json.dump(data, output, ensure_ascii=False, indent=2)
                        output.flush(); os.fsync(output.fileno())
                    os.replace(tmp, self.path)
                finally:
                    tmp.unlink(missing_ok=True)
            finally:
                lock.seek(0)
                if os.name == 'nt': msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _key(call):
        return hashlib.sha256(json.dumps([call.context.request_id, call.capability]).encode()).hexdigest()

    def _reserve(self, gate, call):
        with self._ledger() as data:
            principal, context = gate.application.principal, call.context
            kbs = set(context.scope.knowledge_base_ids) | set(context.required_origin_kbs)
            allowed = {'provider': call.provider, 'model': call.model, 'capability': call.capability}
            valid = (data['status'] == 'OPEN' and str(data['tenant_id']) == str(principal.tenant_id)
                and str(data['subject_id']) == str(principal.subject_id) == str(context.scope.owner_id)
                and bool(kbs) and kbs <= set(data['allowed_kbs'])
                and allowed in data['allowed_models']
                and any(context.request_id.startswith(p) for p in data['allowed_request_prefixes'] if p))
            if not valid: raise ProviderError('LOCAL_EXECUTION_SCOPE_DENIED')
            if (type(call.wire_bytes) is not int or call.wire_bytes <= 0 or
                    (call.output_budget is not None and (type(call.output_budget) is not int or call.output_budget < 0))):
                raise ProviderError('LOCAL_EXECUTION_BOUND_INVALID')
            reserve = call.wire_bytes + (call.output_budget or 0) + 1024
            entries = data['entries']
            if any(e['id'] == self._key(call) for e in entries):
                raise ProviderError('LOCAL_EXECUTION_REUSE_DENIED')
            if (type(data['max_calls']) is not int or not 0 < data['max_calls'] <= 12 or
                    type(data['max_tokens']) is not int or not 0 < data['max_tokens'] <= 100000 or
                    len(entries) >= data['max_calls'] or sum(e['held_tokens'] for e in entries) + reserve > data['max_tokens']):
                raise ProviderError('LOCAL_EXECUTION_LIMIT_DENIED')
            entries.append({'id': self._key(call), 'request_id': context.request_id,
                **allowed, 'wire_sha256': call.wire_sha256, 'reserved_tokens': reserve,
                'held_tokens': reserve, 'status': 'INFLIGHT'})

    def _finish(self, call, usage=None, failure='', outcome='UNKNOWN'):
        with self._ledger() as data:
            rows = [e for e in data['entries'] if e['id'] == self._key(call)]
            if len(rows) != 1 or rows[0]['status'] != 'INFLIGHT':
                data['status'] = 'STOP'
                return
            row = rows[0]
            row['outcome'] = outcome
            row['usage'] = asdict(usage) if usage is not None else None
            valid = (not failure and outcome == 'RESPONSE_RECEIVED' and usage.status == 'REPORTED' and
                type(usage.total_tokens) is int and 0 <= usage.total_tokens <= row['reserved_tokens'] and
                all(v is None or (type(v) is int and v >= 0) for v in (usage.prompt_tokens, usage.completion_tokens)))
            row['status'] = 'SETTLED' if valid else failure or 'UNKNOWN'
            if valid: row['held_tokens'] = usage.total_tokens
            else: data['status'] = 'STOP'

    def install_gate(self, gate):
        installed = getattr(gate, '_local_execution', None)
        if installed is not None:
            if installed.path.resolve() != self.path.resolve(): raise ValueError('EXECUTION_GATE_ALREADY_BOUND')
            return gate
        original_authorize, original_finish = gate.authorize, gate.finish
        async def authorize(call):
            self._reserve(gate, call)
            try: await original_authorize(call)
            except BaseException:
                self._finish(call, failure='LOCAL_ADMISSION_FAILED_STOP')
                raise
        async def finish(call, *, outcome, usage):
            try: await original_finish(call, outcome=outcome, usage=usage)
            except BaseException:
                self._finish(call, failure='LOCAL_SETTLEMENT_FAILED_STOP')
                raise
            self._finish(call, usage, outcome=outcome)
        gate.authorize, gate.finish, gate._local_execution = authorize, finish, self
        return gate

    def observe_model(self, model):
        transport = model.transport
        if getattr(transport, '_local_execution_observed', False): return model
        original = transport.open
        @asynccontextmanager
        async def observed(config, path, body, context, **kwargs):
            self.evidence.mkdir(exist_ok=True)
            prefix = self.evidence / uuid.uuid4().hex
            record = {'provider': config.provider, 'model': config.model, 'model_id': config.model_id,
                'capability': kwargs['capability'], 'request_id': context.request_id,
                'scope': asdict(context.scope), 'required_origin_kbs': context.required_origin_kbs,
                'body': _safe_body(body), 'headers_saved': False}
            prefix.with_suffix('.request.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            async with original(config, path, body, context, **kwargs) as handle:
                proxy = _RawResponse(handle.response, prefix)
                handle.response = proxy
                try: yield handle
                finally: proxy.finish()
        transport.open, transport._local_execution_observed = observed, True
        return model

    def observe_pipeline(self, pipeline, turn):
        """Passive snapshots of the same normal pipeline, without replacing evidence."""
        original_run, original_progress = pipeline.run, pipeline.on_progress
        target = self.evidence / (hashlib.sha256(turn.run_id.encode()).hexdigest() + '.stages.json')
        record = {'run_id': turn.run_id, 'session_id': turn.session_id, 'stages': []}
        active_state = None
        def save():
            self.evidence.mkdir(exist_ok=True)
            target.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding='utf-8')
        async def progress(stage, status, **fields):
            entry = {'stage': stage, 'status': status, 'time': time.time(), 'fields': fields}
            if status == 'completed':
                entry['state'] = {name: [asdict(row) if row is not None else None for row in getattr(active_state, name)]
                    for name in ('search_results', 'rerank_results', 'merge_results')}
                entry['state'].update({name: getattr(active_state, name, None)
                    for name in ('query', 'rewrite_query', 'intent', 'needs_retrieval', 'user_content')})
            record['stages'].append(entry); save()
            if original_progress is not None:
                result = original_progress(stage, status, **fields)
                if hasattr(result, '__await__'): await result
        async def run(state, context):
            nonlocal active_state
            active_state = state
            try:
                result = await original_run(state, context)
                record['finalized_context'] = asdict(result); save()
                return result
            except BaseException as error:
                record['error_type'] = type(error).__name__; save()
                raise
        pipeline.on_progress, pipeline.run = progress, run
        return pipeline


class _RawResponse:
    def __init__(self, original, prefix):
        self.original, self.prefix = original, prefix
        self.file = prefix.with_suffix('.response.body').open('xb')
        self.complete = False
    def __getattr__(self, name): return getattr(self.original, name)
    def append(self, raw):
        self.file.write(raw); self.file.flush(); os.fsync(self.file.fileno())
    async def aread(self):
        try:
            raw = await self.original.aread(); self.append(raw); self.complete = True
            return raw
        finally: self.finish()
    async def aiter_bytes(self):
        try:
            async for raw in self.original.aiter_bytes():
                self.append(raw)
                yield raw
            self.complete = True
        finally: self.finish()
    def finish(self):
        if self.file.closed: return
        self.file.close()
        self.prefix.with_suffix('.response.json').write_text(json.dumps({'complete': self.complete,
            'headers_saved': False, 'status_code': self.original.status_code}), encoding='utf-8')
