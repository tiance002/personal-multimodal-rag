"""P17 bounded, passive predecoder evidence on the ordinary FastAPI chain."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from scripts.weknora_port_p9_observed_api import observed_factory as base_factory
from scripts.weknora_port_p8_1_observed_api import resolved

ATTEMPT = ContextVar('p17_attempt', default=None)
MAX_RAW_BYTES = 2 * 1024 * 1024


def journal(root, identity, record):
    folder = root / 'diagnostics'; folder.mkdir(exist_ok=True)
    path = folder / (hashlib.sha256(identity.encode()).hexdigest() + '.jsonl')
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')
        f.flush(); os.fsync(f.fileno())


class RawResponse:
    """Consume exactly once and yield identical bytes, with no prefetch."""
    def __init__(self, response, root, identity, capability):
        self.original = response
        self.path = root / 'raw' / (hashlib.sha256(identity.encode()).hexdigest() + '.body')
        self.path.parent.mkdir(exist_ok=True)
        self.file = self.path.open('xb')
        self.root, self.identity, self.capability = root, identity, capability
        self.size = 0; self.saved = 0; self.complete = False

    def __getattr__(self, name): return getattr(self.original, name)

    def append(self, raw):
        self.size += len(raw)
        remaining = MAX_RAW_BYTES - self.saved
        if remaining > 0:
            value = raw[:remaining]; self.file.write(value); self.saved += len(value)
            self.file.flush(); os.fsync(self.file.fileno())

    async def aiter_bytes(self):
        try:
            async for raw in self.original.aiter_bytes():
                self.append(raw)
                yield raw
            self.complete = True
        finally:
            self.finish()

    async def aread(self):
        try:
            raw = await self.original.aread(); self.append(raw); self.complete = True
            return raw
        finally:
            self.finish()

    def finish(self):
        if self.file.closed: return
        self.file.close()
        journal(self.root, self.identity, {'kind': 'raw_response', 'request_id': self.identity,
            'capability': self.capability, 'path': str(self.path), 'seen_bytes': self.size,
            'saved_bytes': self.saved, 'complete': self.complete and self.size == self.saved,
            'sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(), 'headers_saved': False})


def install_model(model, root, answer_claims):
    original_open = model.transport.open
    original_stream = model.chat_stream

    @asynccontextmanager
    async def observed_open(config, path, body, context, **kwargs):
        if '/answer/' in context.request_id:
            from backend.weknora_port.models.types import ProviderError
            if ((config.provider, config.model) != ('siliconflow', 'XingChenAGI/Xing4.0-29B')
                    or context.request_id in answer_claims or len(answer_claims) >= 2):
                raise ProviderError('P17_ANSWER_CAP_OR_IDENTITY_DENIED')
            answer_claims.add(context.request_id)
            journal(root, context.request_id, {'kind': 'answer_intent', 'request_id': context.request_id,
                'ordinal': len(answer_claims), 'maximum': 2})
        async with original_open(config, path, body, context, **kwargs) as handle:
            capture = '/answer/' in context.request_id or kwargs['capability'] == 'rerank'
            proxy = RawResponse(handle.response, root, context.request_id, kwargs['capability']) if capture else None
            if proxy: handle.response = proxy
            try: yield handle
            finally:
                if proxy: proxy.finish()

    async def observed_stream(messages, options, context):
        async for event in original_stream(messages, options, context):
            if '/answer/' in context.request_id:
                journal(root, context.request_id, {'kind': 'adapter_event', 'request_id': context.request_id,
                    'content': event.content, 'response_type': event.response_type,
                    'done': event.done, 'is_final': event.is_final, 'finish_reason': event.finish_reason})
            yield event

    model.transport.open = observed_open; model.chat_stream = observed_stream
    return model


@asynccontextmanager
async def decoder_observation(root):
    from backend.weknora_port.generation import coordinator as module
    original_decoder, original_attempt = module.CitationStreamDecoder, module.GenerationCoordinator._attempt

    class Decoder(original_decoder):
        def __init__(self, registry):
            super().__init__(registry)
            state = ATTEMPT.get(); self.trace = state
            self.channel = 'answer' if state and state['decoders'] == 0 else 'thinking'
            if state:
                state['decoders'] += 1
                journal(root, state['id'], {'kind': 'decoder_init', 'channel': self.channel,
                    'registry': asdict(registry.snapshot())})

        def feed(self, chunk):
            result = super().feed(chunk)
            if self.trace: journal(root, self.trace['id'], {'kind': 'decoder_feed', 'channel': self.channel,
                'input': chunk, 'output': result, 'pending': self.pending,
                'used_handles': sorted(self.registry.used_handles), 'invalid_count': self.registry.invalid_count})
            return result

        def flush(self):
            result = super().flush()
            if self.trace: journal(root, self.trace['id'], {'kind': 'decoder_flush', 'channel': self.channel,
                'output': result, 'used_handles': sorted(self.registry.used_handles),
                'invalid_count': self.registry.invalid_count})
            return result

    async def attempt(self, request, prepared, context, role, ordinal, reason):
        identity = f'{request.run_id}/answer/{ordinal}/{role}'
        token = ATTEMPT.set({'id': identity, 'decoders': 0})
        try:
            result = await original_attempt(self, request, prepared, context, role, ordinal, reason)
            record, output, error = result
            journal(root, identity, {'kind': 'attempt_result', 'request_id': identity,
                'finish_reason': record.finish_reason, 'status': record.status,
                'answer': output[0] if output else None, 'references': output[2] if output else None,
                'error_type': type(error).__name__ if error else None})
            return result
        finally: ATTEMPT.reset(token)

    module.CitationStreamDecoder = Decoder; module.GenerationCoordinator._attempt = attempt
    try: yield
    finally:
        module.CitationStreamDecoder = original_decoder; module.GenerationCoordinator._attempt = original_attempt


def observed_factory(authority_path, evidence_dir):
    root = Path(evidence_dir); factory = base_factory(authority_path, evidence_dir)
    answer_claims = {row['request_id'] for path in (root/'diagnostics').glob('*.jsonl')
                     for line in path.read_text(encoding='utf-8').splitlines()
                     if (row := json.loads(line)).get('kind') == 'answer_intent'}
    @asynccontextmanager
    async def assemble(settings):
        async with factory(settings) as runtime, decoder_observation(root):
            original = runtime.model_factory
            async def model_factory(config, key, gate):
                return install_model(await resolved(original(config, key, gate)), root, answer_claims)
            runtime.model_factory = model_factory
            yield runtime
    return assemble


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', type=int, required=True); p.add_argument('--authority-config', required=True)
    p.add_argument('--evidence-dir', required=True); a = p.parse_args()
    from backend.weknora_port.entrypoint import create_app
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(create_app(runtime_factory=observed_factory(a.authority_config, a.evidence_dir)), host='127.0.0.1', port=a.port))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(server.serve())


if __name__ == '__main__': main()
