"""Explicit, temporary owner execution fence; no account or monthly budget."""
from __future__ import annotations
from contextlib import contextmanager, asynccontextmanager
from dataclasses import asdict
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import threading
import time
import uuid

from .models.types import ProviderError

_LOCK = threading.RLock()
_OUTPUT_BOUND = ContextVar('local_execution_output_bound', default=None)
_LINUX_EXECUTION_ROOT = '/opt/rag-s8/state/executions'


def _linux_execution_location(path, approved_root):
    """Server-only opt-in to one fixed private Linux root, never a client field."""
    raw = os.fspath(path)
    if (approved_root != _LINUX_EXECUTION_ROOT or not isinstance(raw, str) or not raw
            or not raw.startswith('/') or raw.startswith('//') or '\\' in raw or ':' in raw
            or '\x00' in raw or any(part in ('.', '..') for part in raw.split('/'))):
        raise ValueError('EXECUTION_APPROVED_LINUX_ROOT_REQUIRED')
    candidate, root = PurePosixPath(raw), PurePosixPath(_LINUX_EXECUTION_ROOT)
    if root not in candidate.parents:
        raise ValueError('EXECUTION_APPROVED_LINUX_ROOT_REQUIRED')
    return candidate


def _resolve_linux_execution_path(path, approved_root):
    candidate = _linux_execution_location(path, approved_root)
    try:
        root = Path(_LINUX_EXECUTION_ROOT)
        # Even an approved root/ancestor must not redirect into another tree.
        if root.resolve(strict=True) != root:
            raise ValueError('EXECUTION_LINUX_PATH_INVALID')
        resolved = Path(str(candidate)).resolve(strict=True)
        _linux_execution_location(str(resolved), approved_root)
        if not resolved.is_file():
            raise ValueError('EXECUTION_LINUX_PATH_INVALID')
        lock = resolved.with_suffix(resolved.suffix + '.lock')
        if lock.is_symlink():
            raise ValueError('EXECUTION_LINUX_PATH_INVALID')
        _linux_execution_location(str((resolved.parent / 'evidence').resolve()), approved_root)
        return resolved
    except (OSError, ValueError):
        raise ValueError('EXECUTION_LINUX_PATH_INVALID') from None


def _process_token(pid):
    """OS creation identity; failure/permission denial is deliberately unknown."""
    try:
        if type(pid) is not int or pid <= 0: return None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle: return None
            try:
                stamps = [wintypes.FILETIME() for _ in range(4)]
                if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in stamps)): return None
                return str((stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime)
            finally: kernel.CloseHandle(handle)
        raw = Path(f'/proc/{pid}/stat').read_text()
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + raw.rsplit(')', 1)[1].split()[19]
    except (OSError, ValueError, IndexError): return None


def _usage_valid(usage, reserve):
    if not isinstance(usage, dict) or usage.get('status') != 'REPORTED': return False
    total = usage.get('total_tokens')
    parts = [usage.get(k) for k in ('prompt_tokens', 'completion_tokens')]
    return (type(total) is int and 0 <= total <= reserve
        and all(v is None or type(v) is int and 0 <= v <= total for v in parts)
        and (None in parts or sum(parts) == total)
        and all(type(usage.get(k, 0)) is int and 0 <= usage.get(k, 0) <= total
            for k in ('cached_tokens', 'cache_read_tokens', 'cache_write_tokens', 'cache_miss_tokens')))


def _require(condition):
    if not condition: raise ValueError('LOCAL_EXECUTION_CHECK_FAILED')


def _validate_ledger(data):
    try:
        _require(isinstance(data, dict) and data['status'] in ('OPEN', 'STOP', 'CLOSED'))
        _require(type(data['max_calls']) is int and 0 < data['max_calls'] <= 12)
        _require(type(data['max_tokens']) is int and 0 < data['max_tokens'] <= 100000)
        _require(str(data['tenant_id']) and isinstance(data['subject_id'], str) and data['subject_id'])
        for field in ('allowed_kbs', 'allowed_request_prefixes'):
            values = data[field]
            _require(isinstance(values, list) and values and all(isinstance(v, str) and v for v in values))
            _require(len(values) == len(set(values)))
        models = data['allowed_models']
        _require(isinstance(models, list) and models)
        model_fields = {'provider', 'model', 'model_id', 'capability'}
        permitted_fields = (model_fields,) if data['status'] == 'OPEN' else (model_fields, model_fields - {'model_id'})
        _require(all(isinstance(m, dict) and set(m) in permitted_fields
            and all(isinstance(v, str) and v for v in m.values()) for m in models))
        _require(len({json.dumps(m, sort_keys=True) for m in models}) == len(models))
        _require(type(data.get('capture_evidence', False)) is bool)
        entries = data['entries']
        _require(isinstance(entries, list) and len(entries) <= data['max_calls'])
        seen = set()
        for row in entries:
            _require(isinstance(row, dict))
            identity = hashlib.sha256(json.dumps([row['request_id'], row['capability']]).encode()).hexdigest()
            _require(row['id'] == identity and identity not in seen)
            seen.add(identity)
            _require(isinstance(row['request_id'], str) and bool(row['request_id'])
                and any(p == '*' or row['request_id'].startswith(p) for p in data['allowed_request_prefixes']))
            row_model = {k: row[k] for k in ('provider', 'model', 'capability')}
            if 'model_id' in row: row_model['model_id'] = row['model_id']
            _require(row_model in models)
            _require(isinstance(row['wire_sha256'], str) and len(row['wire_sha256']) == 64)
            int(row['wire_sha256'], 16)
            reserved, held = row['reserved_tokens'], row['held_tokens']
            _require(type(reserved) is int and reserved >= 1024 and type(held) is int and 0 <= held <= reserved)
            _require(row['status'] in ('INFLIGHT', 'SETTLED', 'UNKNOWN', 'LOCAL_ADMISSION_FAILED_STOP',
                'LOCAL_SETTLEMENT_FAILED_STOP', 'LOCAL_SETTLEMENT_UNCONFIRMED_STOP'))
            if row.get('usage') is not None: _require(_usage_valid(row['usage'], reserved))
            if row['status'] == 'SETTLED':
                _require(row.get('outcome') == 'RESPONSE_RECEIVED' and _usage_valid(row.get('usage'), reserved))
                _require(held == row['usage']['total_tokens'])
            else:
                _require(held == reserved)
                if row['status'] != 'INFLIGHT': _require(data['status'] != 'OPEN')
        _require(sum(row['held_tokens'] for row in entries) <= data['max_tokens'])
    except (KeyError, TypeError, ValueError):
        raise ProviderError('LOCAL_EXECUTION_INVALID') from None


def _stop_orphans(data):
    for row in data['entries']:
        if row['status'] == 'INFLIGHT':
            owner = row.get('process_identity', {})
            token = _process_token(owner.get('pid')) if isinstance(owner, dict) else None
            if token is None or token != owner.get('creation_token'):
                data['status'] = 'STOP'



def _safe_body(value):
    if isinstance(value, dict):
        secret = {'key', 'api_key', 'api-key', 'authorization', 'access_token', 'headers', 'custom_headers', 'app_secret', 'secret_key'}
        return {k: _safe_body(v) for k, v in value.items() if k.lower() not in secret}
    if isinstance(value, (list, tuple)): return [_safe_body(v) for v in value]
    return value


class LocalExecution:
    def __init__(self, path):
        if os.name == 'nt':
            self.path = Path(path)
            if not self.path.is_absolute() or self.path.drive.upper() not in ('D:', 'E:'):
                raise ValueError('EXECUTION_ABSOLUTE_D_E_PATH_REQUIRED')
        else:
            self.path = _resolve_linux_execution_path(path, os.environ.get('WEKNORA_PORT_EXECUTION_ROOT'))
        self.evidence = self.path.parent / 'evidence'
        with self._ledger() as data:
            if Path(data.get('evidence_dir', self.evidence)).resolve() != self.evidence.resolve():
                raise ValueError('EXECUTION_EVIDENCE_PATH_INVALID')
            self.capture_evidence = data.get('capture_evidence', False)
            _stop_orphans(data)

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
                try: data = json.loads(self.path.read_text(encoding='utf-8'))
                except (OSError, ValueError): raise ProviderError('LOCAL_EXECUTION_INVALID') from None
                _validate_ledger(data)
                yield data
                _validate_ledger(data)
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
            _stop_orphans(data)
        with self._ledger() as data:
            if data['status'] == 'STOP': raise ProviderError('LOCAL_EXECUTION_UNKNOWN_STOP')
            if data['status'] == 'CLOSED': raise ProviderError('LOCAL_EXECUTION_CLOSED')
            principal, context = gate.application.principal, call.context
            kbs = set(context.scope.knowledge_base_ids) | set(context.required_origin_kbs)
            allowed = {'provider': call.provider, 'model': call.model, 'model_id': call.model_id, 'capability': call.capability}
            valid = (str(data['tenant_id']) == str(principal.tenant_id)
                and str(data['subject_id']) == str(principal.subject_id) == str(context.scope.owner_id)
                and bool(kbs) and kbs <= set(data['allowed_kbs'])
                and allowed in data['allowed_models']
                and isinstance(context.request_id, str) and bool(context.request_id)
                and any(p == '*' or context.request_id.startswith(p) for p in data['allowed_request_prefixes']))
            if not valid: raise ProviderError('LOCAL_EXECUTION_SCOPE_DENIED')
            if (type(call.wire_bytes) is not int or call.wire_bytes <= 0 or
                    (call.output_budget is not None and (type(call.output_budget) is not int or call.output_budget < 0))):
                raise ProviderError('LOCAL_EXECUTION_BOUND_INVALID')
            bound = _OUTPUT_BOUND.get()
            output_upper = bound if bound is not None else (call.output_budget or 0)
            reserve = call.wire_bytes + output_upper + 1024
            entries = data['entries']
            if any(e['id'] == self._key(call) for e in entries):
                raise ProviderError('LOCAL_EXECUTION_REUSE_DENIED')
            if (type(data['max_calls']) is not int or not 0 < data['max_calls'] <= 12 or
                    type(data['max_tokens']) is not int or not 0 < data['max_tokens'] <= 100000 or
                    len(entries) >= data['max_calls'] or sum(e['held_tokens'] for e in entries) + reserve > data['max_tokens']):
                raise ProviderError('LOCAL_EXECUTION_LIMIT_DENIED')
            entries.append({'id': self._key(call), 'request_id': context.request_id,
                **allowed, 'wire_sha256': call.wire_sha256, 'reserved_tokens': reserve,
                'held_tokens': reserve, 'status': 'INFLIGHT',
                'process_identity': {'pid': os.getpid(), 'creation_token': _process_token(os.getpid())}})

    def _finish(self, call, usage=None, failure='', outcome='UNKNOWN'):
        with self._ledger() as data:
            rows = [e for e in data['entries'] if e['id'] == self._key(call)]
            if len(rows) != 1 or rows[0]['status'] != 'INFLIGHT':
                data['status'] = 'STOP'
                return
            row = rows[0]
            row['outcome'] = outcome
            row['usage'] = asdict(usage) if usage is not None else None
            reported_valid = _usage_valid(row['usage'], row['reserved_tokens'])
            if row['usage'] is not None and not reported_valid:
                row['usage'], row['usage_invalid'] = None, True
            valid = not failure and outcome == 'RESPONSE_RECEIVED' and reported_valid
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
            try: accepted = await original_finish(call, outcome=outcome, usage=usage)
            except BaseException as error:
                self._finish(call, failure='LOCAL_SETTLEMENT_FAILED_STOP')
                if isinstance(error, Exception): raise ProviderError('LOCAL_EXECUTION_SETTLEMENT_FAILED') from None
                raise
            if accepted is not True:
                self._finish(call, usage, failure='LOCAL_SETTLEMENT_UNCONFIRMED_STOP', outcome=outcome)
                raise ProviderError('LOCAL_EXECUTION_SETTLEMENT_FAILED')
            self._finish(call, usage, outcome=outcome)
            return True
        gate.authorize, gate.finish, gate._local_execution = authorize, finish, self
        return gate

    def observe_model(self, model):
        transport = model.transport
        if getattr(transport, '_local_execution_observed', False): return model
        original = transport.open
        @asynccontextmanager
        async def observed(config, path, body, context, **kwargs):
            from .models.output_contract import resolve_output_contract
            try:
                capability = kwargs['capability']
                _require(config.provider in ('siliconflow', 'deepseek') and isinstance(body, dict))
                if capability == 'chat':
                    _require(config.api == 'openai-completions')
                    _require(isinstance(body.get('messages'), list) and body['messages'])
                    _require(all(isinstance(m, dict) and isinstance(m.get('content'), str)
                        and not any(k in m for k in ('audio', 'images', 'input_audio')) for m in body['messages']))
                    _require(not any(k in body for k in ('audio', 'modalities', 'images', 'input_audio')))
                elif capability == 'embedding':
                    _require(config.provider == 'siliconflow' and config.embedding_api == 'openai-embeddings')
                    value = body.get('input')
                    _require(isinstance(value, str) or isinstance(value, list) and value and all(isinstance(v, str) for v in value))
                elif capability == 'rerank':
                    _require(config.provider == 'siliconflow' and config.rerank_api == 'cohere-rerank')
                    _require(isinstance(body.get('query'), str) and isinstance(body.get('documents'), list))
                    _require(body['documents'] and all(isinstance(v, str) for v in body['documents']))
                else: raise ValueError('UNSUPPORTED_CAPABILITY')
                contract = resolve_output_contract(config, body, capability=capability)
            except (ValueError, TypeError, AttributeError, KeyError, ProviderError):
                raise ProviderError('LOCAL_EXECUTION_BOUND_UNVERIFIABLE') from None
            token = _OUTPUT_BOUND.set(contract.output_upper)
            try:
                prefix = None
                if self.capture_evidence:
                    self.evidence.mkdir(exist_ok=True)
                    prefix = self.evidence / uuid.uuid4().hex
                    record = {'provider': config.provider, 'model': config.model, 'model_id': config.model_id,
                        'capability': capability, 'request_id': context.request_id,
                        'scope': asdict(context.scope), 'required_origin_kbs': context.required_origin_kbs,
                        'body': _safe_body(body), 'headers_saved': False}
                    prefix.with_suffix('.request.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
                transport_started = time.monotonic()
                async with original(config, path, body, context, **kwargs) as handle:
                    if prefix is None:
                        yield handle
                    else:
                        proxy = _RawResponse(handle.response, prefix, transport_started=transport_started)
                        handle.response = proxy
                        try: yield handle
                        finally: proxy.finish()
            finally:
                try: _OUTPUT_BOUND.reset(token)
                except ValueError:
                    # Stream cancellation may close the generator in a copied
                    # cleanup context. Never mask its durable settlement.
                    pass
        transport.open, transport._local_execution_observed = observed, True
        return model

    def observe_pipeline(self, pipeline, turn):
        from .evidence_capture import observe_pipeline_capture
        return observe_pipeline_capture(self, pipeline, turn)


class _RawResponse:
    def __init__(self, original, prefix, *, transport_started=None):
        self.original, self.prefix = original, prefix
        self.file = prefix.with_suffix('.response.body').open('xb')
        self.complete = False
        self.timing = {'transport_open_started_monotonic': transport_started,
                       'response_headers_observed_monotonic': time.monotonic(),
                       'first_raw_byte_monotonic': None,
                       'meaning': 'transport open includes normal gate admission; no separate DNS/TCP/TLS/queue timing'}
    def __getattr__(self, name): return getattr(self.original, name)
    def append(self, raw):
        if raw and self.timing['first_raw_byte_monotonic'] is None:
            self.timing['first_raw_byte_monotonic'] = time.monotonic()
        # Passive evidence is buffered. Durable authorization/settlement writes
        # retain their own locking/fsync; no per-delta fsync delays generation.
        self.file.write(raw)
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
        self.timing['read_finished_monotonic'] = time.monotonic()
        self.prefix.with_suffix('.timing.json').write_text(json.dumps(self.timing), encoding='utf-8')
        self.prefix.with_suffix('.response.json').write_text(json.dumps({'complete': self.complete,
            'headers_saved': False, 'status_code': self.original.status_code}), encoding='utf-8')
