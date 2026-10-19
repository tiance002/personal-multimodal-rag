"""Opt-in local passive evidence. This supplies no budget or call limit protection.

The existing transport and its real Gate retain all admission/settlement duties.
Private request/image data and raw responses are saved only to the explicit directory.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time
import uuid


def validate_evidence_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.drive.upper() not in ('D:', 'E:'):
        raise ValueError('EVIDENCE_ABSOLUTE_D_E_PATH_REQUIRED')
    resolved = path.resolve()
    if resolved.drive.upper() not in ('D:', 'E:'):
        raise ValueError('EVIDENCE_ABSOLUTE_D_E_PATH_REQUIRED')
    if resolved.exists() and not resolved.is_dir():
        raise ValueError('EVIDENCE_DIRECTORY_REQUIRED')
    return resolved


class PassiveEvidenceCapture:
    """Observe normal calls without installing execution objects or changing Gate."""
    capture_evidence = True

    def __init__(self, directory):
        self.evidence = validate_evidence_directory(directory)

    def observe_model(self, model):
        from .execution import _RawResponse, _safe_body
        transport = model.transport
        if getattr(transport, '_passive_evidence_observed', False): return model
        original = transport.open
        @asynccontextmanager
        async def observed(config, path, body, context, **kwargs):
            self.evidence.mkdir(parents=True, exist_ok=True)
            prefix = self.evidence / uuid.uuid4().hex
            # Same canonical serialization as AsyncHTTPTransport, for wire/image verification.
            wire = json.dumps(body, ensure_ascii=False, separators=(",", ":"),
                              sort_keys=True, allow_nan=False).encode('utf-8')
            record = {'mode': 'passive_evidence_only', 'budget_protection': False,
                'request_capture_point': 'transport input before normal gate admission; not proof of send',
                'provider': config.provider, 'model': config.model, 'model_id': config.model_id,
                'capability': kwargs['capability'], 'request_id': context.request_id,
                'scope': asdict(context.scope), 'required_origin_kbs': context.required_origin_kbs,
                'body': _safe_body(body), 'headers_saved': False,
                'wire_sha256': hashlib.sha256(wire).hexdigest(), 'wire_bytes': len(wire)}
            prefix.with_suffix('.request.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            started = time.monotonic()
            proxy = None
            try:
                async with original(config, path, body, context, **kwargs) as handle:
                    proxy = _RawResponse(handle.response, prefix, transport_started=started)
                    handle.response = proxy
                    try:
                        yield handle
                    except BaseException:
                        # Capture failures must not replace the original provider/cancellation error.
                        try: proxy.finish()
                        except OSError: pass
                        raise
                    else:
                        proxy.finish()
            except BaseException as error:
                try:
                    prefix.with_suffix('.error.json').write_text(json.dumps({
                        'error_type': type(error).__name__, 'headers_saved': False}), encoding='utf-8')
                except OSError: pass
                raise
        transport.open, transport._passive_evidence_observed = observed, True
        return model

    def observe_pipeline(self, pipeline, turn):
        return observe_pipeline_capture(self, pipeline, turn)


def observe_pipeline_capture(self, pipeline, turn):
    """Passive snapshots of the same normal pipeline, without replacing evidence."""
    if not self.capture_evidence: return pipeline
    original_run, original_progress = pipeline.run, pipeline.on_progress
    target = self.evidence / (hashlib.sha256(turn.run_id.encode()).hexdigest() + '.stages.json')
    record = {'run_id': turn.run_id, 'session_id': turn.session_id, 'stages': []}
    active_state = None
    def save():
        self.evidence.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding='utf-8')
    async def progress(stage, status, **fields):
        from copy import deepcopy
        entry = {'stage': stage, 'status': status, 'time': time.time(), 'fields': fields}
        if status == 'completed':
            entry['state'] = {name: [asdict(row) if row is not None else None for row in getattr(active_state, name)]
                for name in ('search_results', 'rerank_results', 'merge_results')}
            entry['state'].update({name: deepcopy(getattr(active_state, name, None))
                for name in ('query', 'rewrite_query', 'intent', 'needs_retrieval', 'user_content',
                             'source_evidence_mode', 'evidence_trace')})
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
            record['error_type'] = type(error).__name__
            try: save()
            except OSError: pass
            raise
    pipeline.on_progress, pipeline.run = progress, run
    return pipeline
