"""Explicit local graph migration/indexing; no startup hook or permission grant."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path


def arguments(argv=None):
    parser = argparse.ArgumentParser(description='Opt-in source-bound graph commands; no automatic cloud authorization')
    parser.add_argument('--config', required=True, help='Existing approved wk local JSON configuration')
    parser.add_argument('--output', required=True, help='New absolute evidence JSON path on D: or E:')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate', help='Explicit graph-only schema installation after resource identity verification')
    index = commands.add_parser('index', help='Index current original chunks of one existing document')
    index.add_argument('--kb', required=True)
    index.add_argument('--document', required=True)
    index.add_argument('--model-id', required=True)
    index.add_argument('--request-id', required=True, help='New explicit owner-authorized operation identity; never generated')
    return parser.parse_args(argv)


async def execute(args):
    from scripts.local_runtime import load_config, environment, verify_resources
    from .development import DevelopmentSettings, assemble
    from .models.types import CallContext, TrustedScope
    from .storage.graph_migrate import apply_graph_schema
    from .graph.service import GraphIndexer

    config = load_config(args.config)
    identity = await verify_resources(config)
    settings = DevelopmentSettings.from_environment(environment(config, identity['redis_run_id']))
    async with assemble(settings) as app:
        if app.principal.tenant_id != identity['tenant_id']:
            raise PermissionError('GRAPH_CLI_PRINCIPAL_IDENTITY_MISMATCH')
        if args.command == 'migrate':
            async with app.leases.lease() as guard:
                digest = await apply_graph_schema(guard)
            return {'schema': 's6-explicit-graph-migration/v1', 'status': 'APPLIED',
                    'migration': '016_graph', 'sha256': digest, 'provider_calls': 0,
                    'postgres_system_id': identity['postgres_system_id'], 'database': identity['database']}
        context = CallContext(args.request_id, TrustedScope(app.principal.subject_id, (args.kb,), (args.document,)))
        return await GraphIndexer(app).index_document(args.kb, args.document, args.model_id, context)


def main(argv=None):
    args = arguments(argv)
    output = Path(args.output)
    if not output.is_absolute() or output.drive.upper() not in ('D:', 'E:'):
        raise SystemExit('GRAPH_OUTPUT_MUST_BE_ABSOLUTE_D_OR_E')
    if output.exists():
        raise SystemExit('GRAPH_OUTPUT_ALREADY_EXISTS')
    output.parent.mkdir(parents=True, exist_ok=True)
    # The exclusive claim is made before any service/Provider operation. An
    # interrupted attempt cannot be silently replaced by a second invocation.
    with output.open('x', encoding='utf-8') as handle:
        json.dump({'status': 'STARTED', 'command': args.command}, handle)
    result = None
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            result = runner.run(execute(args))
        exit_code = 0
    except Exception as error:
        from .graph.service import GraphIndexError
        result = error.result if isinstance(error, GraphIndexError) else {
            'status': 'BLOCKED', 'error_code': getattr(error, 'code', 'GRAPH_COMMAND_FAILED'),
            'exception_type': type(error).__name__, 'provider_calls': None}
        exit_code = 2
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'output': str(output),
                      'provider_calls': result.get('provider_calls'), 'reported_tokens': result.get('reported_tokens')},
                     ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
