"""P8.1 passive stage and raw rerank capture around the unchanged ordinary API.

No Gold, injected candidates, model sends, threshold changes or extra retrieval.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
import hashlib
import inspect
import json
from pathlib import Path
from scripts.weknora_port_p8_observed_api import capture


async def resolved(value):
    return await value if inspect.isawaitable(value) else value


def write(output, folder, run_id, value):
    target = output / folder / (hashlib.sha256(run_id.encode()).hexdigest() + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(target)


def rows(values):
    return [asdict(row) if is_dataclass(row) else dict(row) for row in values if row is not None]


class RerankRegistryObserver:
    def __init__(self, registry, output, run_id):
        self.registry, self.output, self.run_id = registry, output, run_id
        self.calls = []

    def __getattr__(self, name): return getattr(self.registry, name)

    async def rerank(self, model_id, context):
        model = await self.registry.rerank(model_id, context)
        observer = self
        class ModelObserver:
            def __getattr__(self, name): return getattr(model, name)
            async def rerank(self, query, documents, context):
                record = {'model_id': model_id, 'request_id': context.request_id, 'query': query,
                          'documents': list(documents), 'status': 'STARTED_NORMAL_INVOCATION'}
                observer.calls.append(record)
                write(observer.output, 'rerank', observer.run_id, {'run_id': observer.run_id, 'calls': observer.calls})
                try:
                    response = await model.rerank(query, documents, context)
                except BaseException as error:
                    record.update(status='NORMAL_INVOCATION_ERROR', error_type=type(error).__name__)
                    write(observer.output, 'rerank', observer.run_id, {'run_id': observer.run_id, 'calls': observer.calls})
                    raise
                values = response.results if hasattr(response, 'results') else response
                record.update(status='NORMAL_RESPONSE_RECEIVED', results=rows(values))
                write(observer.output, 'rerank', observer.run_id, {'run_id': observer.run_id, 'calls': observer.calls})
                return response
        return ModelObserver()


def observed_factory(authority_path, evidence_dir):
    from backend.weknora_port.authority import runtime_factory
    original_factory = runtime_factory(authority_path)
    output = Path(evidence_dir)

    @asynccontextmanager
    async def assemble(settings):
        async with original_factory(settings) as runtime:
            ordinary = runtime.ordinary
            original_configuration, original_pipeline = ordinary.configuration, ordinary.pipeline_factory
            original_prepare = ordinary.prepare_turn
            states = {}
            stage_results = {}

            async def configure(turn):
                config = await resolved(original_configuration(turn))
                states[turn.run_id] = config.pipeline_state
                return config

            async def pipeline_factory(turn, progress):
                pipeline = await resolved(original_pipeline(turn, progress))
                original_progress = pipeline.on_progress
                pipeline.registry = RerankRegistryObserver(pipeline.registry, output, turn.run_id)

                async def observe_progress(stage, status, **fields):
                    if status == 'completed':
                        state = states[turn.run_id]
                        result = stage_results.setdefault(turn.run_id, {'run_id': turn.run_id, 'session_id': turn.session_id,
                            'question': turn.request.content, 'observer': 'PASSIVE_NORMAL_STAGE_COMPLETION', 'gold_loaded': False, 'stages': []})
                        result['stages'].append({'stage': stage, 'status': status, 'fields': fields,
                            'search_results': rows(state.search_results), 'rerank_results': rows(state.rerank_results),
                            'merge_results': rows(state.merge_results)})
                        write(output, 'stages', turn.run_id, result)
                    if original_progress is not None: return await resolved(original_progress(stage, status, **fields))
                pipeline.on_progress = observe_progress
                return pipeline

            async def observe(turn, progress=None):
                try:
                    prepared = await original_prepare(turn, progress)
                    write(output, 'captures', turn.run_id, capture(prepared))
                    return prepared
                finally:
                    states.pop(turn.run_id, None)
                    stage_results.pop(turn.run_id, None)

            ordinary.configuration, ordinary.pipeline_factory, ordinary.prepare_turn = configure, pipeline_factory, observe
            yield runtime

    return assemble


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1'); parser.add_argument('--port', type=int, default=56429)
    parser.add_argument('--authority-config', required=True); parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    from backend.weknora_port.entrypoint import create_app
    import uvicorn
    app = create_app(runtime_factory=observed_factory(args.authority_config, args.evidence_dir))
    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(server.serve())


if __name__ == '__main__': main()
