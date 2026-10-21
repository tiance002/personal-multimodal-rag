"""Passive P9 provider wire evidence, layered over P8.1 stage observation.

The actual httpx request hook runs after the unchanged durable send gate. It
records only the body and safe identities, never credentials or headers.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
from scripts.weknora_port_p8_1_observed_api import observed_factory as stage_factory, resolved


def install_wire_observer(model, output):
    transport = model.transport
    current = ContextVar('p9_observed_call', default=None)
    original_open = transport.open
    original_chat, original_stream = model.chat, model.chat_stream

    def store(record):
        folder = output / 'provider-requests'; folder.mkdir(parents=True, exist_ok=True)
        filename = hashlib.sha256(record['request_id'].encode()).hexdigest() + '.json'
        target = folder / filename
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        temporary.replace(target)

    records = {}

    async def request_hook(request):
        info = current.get()
        if not info: return
        raw = request.content
        request_id, capability = info
        record = {'schema': 'p9-actual-provider-wire/v1', 'request_id': request_id,
            'run_id': request_id.split('/answer/', 1)[0], 'capability': capability,
            'stage': 'answer' if '/answer/' in request_id else 'auxiliary',
            'provider': model.config.provider, 'model': model.config.model,
            'boundary': 'HTTPX_REQUEST_HOOK_AFTER_DURABLE_GATE', 'wire_sha256': hashlib.sha256(raw).hexdigest(),
            'wire_bytes': len(raw), 'body': json.loads(raw), 'finish_reason': None,
            'state': 'ACTUAL_HTTP_SEND_HOOK_OBSERVED', 'headers_saved': False}
        records[request_id] = record
        store(record)

    async def response_hook(response):
        info = current.get()
        if info and info[0] in records:
            record = records[info[0]]
            record.update(status_code=response.status_code, state='ACTUAL_HTTP_RESPONSE_HEADERS_RECEIVED')
            store(record)

    @asynccontextmanager
    async def observed_open(config, path, body, context, **kwargs):
        token = current.set((context.request_id, kwargs['capability']))
        try:
            async with original_open(config, path, body, context, **kwargs) as handle:
                yield handle
        finally:
            current.reset(token)

    def terminal(context, response):
        if not (getattr(response, 'finish_reason', '') or getattr(response, 'usage', None) is not None
                or getattr(response, 'is_final', False)):
            return
        for request_id, record in records.items():
            if request_id == context.request_id or request_id.startswith(context.request_id + '/'):
                reason = getattr(response, 'finish_reason', '')
                if reason: record['finish_reason'] = reason
                usage = getattr(response, 'usage', None)
                if usage is not None: record['parsed_usage'] = asdict(usage)
                if getattr(response, 'is_final', False): record['parsed_final_observed'] = True
                store(record)

    async def chat(messages, options, context, **kwargs):
        response = await original_chat(messages, options, context, **kwargs)
        terminal(context, response)
        return response

    async def chat_stream(messages, options, context):
        async for event in original_stream(messages, options, context):
            terminal(context, event)
            yield event

    transport.client.event_hooks.setdefault('request', []).append(request_hook)
    transport.client.event_hooks.setdefault('response', []).append(response_hook)
    transport.open, model.chat, model.chat_stream = observed_open, chat, chat_stream
    return model


def observed_factory(authority_path, evidence_dir):
    factory = stage_factory(authority_path, evidence_dir)
    @asynccontextmanager
    async def assemble(settings):
        async with factory(settings) as runtime:
            original_factory = runtime.model_factory
            async def model_factory(config, key, gate):
                model = await resolved(original_factory(config, key, gate))
                return install_wire_observer(model, Path(evidence_dir))
            runtime.model_factory = model_factory
            yield runtime
    return assemble


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1'); parser.add_argument('--port', type=int, default=56429)
    parser.add_argument('--authority-config', required=True); parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    from backend.weknora_port.entrypoint import create_app
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(create_app(runtime_factory=observed_factory(args.authority_config, args.evidence_dir)),
                                         host=args.host, port=args.port))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(server.serve())


if __name__ == '__main__': main()
