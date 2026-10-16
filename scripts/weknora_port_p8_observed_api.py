"""Evaluation-only passive capture around the ordinary, real HTTP application.

The observer records the existing preparation result; it never supplies results,
changes requests, reruns retrieval, or reads evaluation Gold.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path


def capture(prepared):
    from backend.weknora_port.generation.context import prepare_generation
    request = prepared.request
    generation = prepare_generation(request)
    return {
        'schema': 'p8-passive-ordinary-capture/v1',
        'run_id': request.run_id,
        'session_id': request.session_id,
        'message_id': request.message_id,
        'question': request.q0,
        'rankings': dict(prepared.rankings),
        'evidence': [asdict(row) for row in request.evidence],
        'context_sha256': generation.context_sha256,
        'prompt_sha256': generation.prompt_sha256,
        'messages': [asdict(row) for row in generation.messages],
        'context': asdict(request.finalized),
        'policy': asdict(request.policy),
        'fixed_fallback': request.fixed_fallback,
        'observer': 'READ_ONLY_AFTER_NORMAL_PREPARATION',
        'gold_loaded': False,
    }


def observed_factory(authority_path, evidence_dir):
    from backend.weknora_port.authority import runtime_factory
    original_factory = runtime_factory(authority_path)
    output = Path(evidence_dir) / 'captures'
    output.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def assemble(settings):
        async with original_factory(settings) as runtime:
            original = runtime.ordinary.prepare_turn

            async def observe(turn, progress=None):
                prepared = await original(turn, progress)
                row = capture(prepared)
                # A hashed server identity cannot become a filesystem path.
                name = hashlib.sha256(turn.run_id.encode()).hexdigest() + '.json'
                with (output / name).open('x', encoding='utf-8') as stream:
                    json.dump(row, stream, ensure_ascii=False, indent=2, default=str)
                return prepared

            runtime.ordinary.prepare_turn = observe
            yield runtime

    return assemble


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=56427)
    parser.add_argument('--authority-config', required=True)
    parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    from backend.weknora_port.entrypoint import create_app
    import uvicorn
    app = create_app(runtime_factory=observed_factory(args.authority_config, args.evidence_dir))
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(server.serve())


if __name__ == '__main__':
    main()
