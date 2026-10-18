"""One bounded S1 input A/B batch through the existing registry and SendGate.

Prepare mode is zero send. Send mode requires an existing Owner-authorized
parent envelope; every case/arm is permanently claimed before the call.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict, fields
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.local_runtime import environment, load_config, verify_resources


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    temporary.replace(path)


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def run(args):
    cfg = load_config(args.config)
    identity = await verify_resources(cfg)
    env = environment(cfg, identity['redis_run_id'])
    # Match the normal wk dependency environment before importing cryptography.
    for path in reversed(env.get('PYTHONPATH', '').split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.storage.history import LocalPrincipal
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from backend.weknora_port.models.request import build_rerank_body, split_batches
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    from backend.weknora_port.retrieval.rerank import enriched_passage, model_passage
    from backend.weknora_port.retrieval.types import SearchResult
    state = read(args.principal_state)
    plan = read(args.plan)
    case = next(c for c in plan['cases'] if c['case_id'] == args.case)
    owner = state['subject']
    output = args.output.resolve()
    settings_env = dict(env, WEKNORA_PORT_STORAGE_ROOT=state['storage_root'],
        WEKNORA_PORT_SECRET_KEY_FILE=state['secret_key_file'],
        WEKNORA_PORT_REDIS_PREFIX='weknora-python-s1-rerank-input-' + uuid.uuid4().hex,
        WEKNORA_PORT_CLOUD_ALLOWED='true' if args.send else 'false')
    parent = None
    grant_path = output / 'execution.json'
    if args.send:
        parent = read(args.authorization)
        assert parent['status'] == 'OPEN' and parent['owner_approved'] is True
        assert parent['max_calls'] == 20 and parent['max_tokens'] == 300000
        assert all(b['status'] == 'CLOSED' for b in parent['batches']), 'UNFINISHED_PREVIOUS_BATCH'
        assert not any(b['case_id'] == args.case for b in parent['batches']), 'NO_RESEND_OF_CLAIMED_CASE'
        spent_calls = sum(b['calls'] for b in parent['batches'])
        spent_tokens = sum(b['tokens'] for b in parent['batches'])
        cap_calls, cap_tokens = 2, min(100000, parent['max_tokens'] - spent_tokens)
        assert spent_calls + cap_calls <= parent['max_calls'] and cap_tokens > 5000
        output.mkdir(parents=True, exist_ok=False)
        batch = {'case_id': args.case, 'status': 'DELEGATED', 'calls': 0, 'tokens': 0,
                 'max_calls': cap_calls, 'max_tokens': cap_tokens, 'path': str(output)}
        parent['batches'].append(batch)
        write(args.authorization, parent)
        write(grant_path, {'status': 'OPEN', 'scope': 'S1_RERANK_INPUT_20261019',
            'tenant_id': 332, 'subject_id': owner, 'allowed_kbs': [case['kb_id']],
            'allowed_models': [{'provider': 'siliconflow', 'model': 'BAAI/bge-reranker-v2-m3',
                'model_id': plan['rerank_model_id'], 'capability': 'rerank'}],
            'allowed_request_prefixes': ['s1ri-'], 'max_calls': cap_calls, 'max_tokens': cap_tokens,
            'capture_evidence': True, 'entries': []})
        settings_env['WEKNORA_PORT_EXECUTION_FILE'] = str(grant_path)
    else:
        output.mkdir(parents=True, exist_ok=True)
        settings_env.pop('WEKNORA_PORT_EXECUTION_FILE', None)
    settings = DevelopmentSettings.from_environment(settings_env)
    summary = {'case_id': args.case, 'execution': 'SEND' if args.send else 'PREFLIGHT_ONLY',
               'provider_calls': 0, 'identity': identity, 'arms': {}}
    try:
        async with assemble(settings, principal=LocalPrincipal(332, owner)) as app:
            protected = await app._rows("SELECT call_id,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p WHERE outcome='UNKNOWN' ORDER BY call_id")
            write(output / 'unknown-before.json', protected)
            context = CallContext('s1ri-' + uuid.uuid4().hex,
                TrustedScope(owner, (case['kb_id'],), ()))
            registry = app.registry_for_context()
            try:
                repository = LeasedKnowledgeRepository(app, registry)
                sources = await repository.get_chunks(tuple(r['id'] for r in case['candidates']), context)
                names = {f.name for f in fields(SearchResult)}
                candidates = []
                for frozen in case['candidates']:
                    source = sources[frozen['id']]
                    assert source.knowledge_base_id == case['kb_id'] and source.knowledge_id == frozen['knowledge_id']
                    assert source.content == frozen['content'], 'SOURCE_BODY_CHANGED'
                    assert source.metadata.get('source_version') == frozen['metadata'].get('source_version'), 'SOURCE_VERSION_CHANGED'
                    candidate = SearchResult(**{k: v for k, v in frozen.items() if k in names})
                    candidate.knowledge_title = source.knowledge_title
                    candidate.context_header = source.context_header
                    candidates.append(candidate)
                a = [enriched_passage(r) for r in candidates]
                b = [model_passage(r) for r in candidates]
                assert a == case['legacy_documents'], 'FROZEN_LEGACY_INPUT_MISMATCH'
                assert all(v.strip() for v in a) and len(a) == len(b)
                model = await registry.rerank(plan['rerank_model_id'], context)
                assert model.config.provider == 'siliconflow' and model.config.model == 'BAAI/bge-reranker-v2-m3'
                inputs = {'case_id': args.case, 'query': case['query'], 'candidates': [asdict(r) for r in candidates],
                          'A': a, 'B': b, 'construction': case.get('construction'),
                          'source_input_identity': case.get('source_input_identity')}
                write(output / 'inputs.json', inputs)
                summary.update(input_sha256=sha(inputs), candidate_count=len(a),
                    persisted_nonempty_headers=sum(bool(r.context_header) for r in candidates),
                    input_chars={'A': sum(map(len, a)), 'B': sum(map(len, b))})
                for label, docs in [('A', a), ('B', b)]:
                    batches = split_batches(tuple(docs), max_items=model.config.reranking.max_documents,
                        max_item_chars=model.config.reranking.max_document_chars,
                        max_total_chars=model.config.reranking.max_request_chars, fixed_chars=len(case['query']))
                    assert len(batches) == 1, 'EXPERIMENT_ONE_PROVIDER_CALL_PER_ARM_REQUIRED'
                    body = build_rerank_body(model.config, case['query'], docs)
                    wire_bytes = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode())
                    summary['arms'][label] = {'request_body_sha256': sha(body), 'wire_bytes': wire_bytes,
                                              'reserve_upper_bound': wire_bytes + 1024}
                if args.send:
                    for label, docs in [('A', a), ('B', b)]:
                        execution = read(grant_path)
                        assert execution['status'] == 'OPEN'
                        assert sum(r['held_tokens'] for r in execution['entries']) + summary['arms'][label]['reserve_upper_bound'] <= execution['max_tokens'], 'PRE_SEND_BATCH_LIMIT'
                        request_id = context.request_id + '/' + args.case + '/' + label
                        with (output / (label + '.claimed')).open('x', encoding='utf-8') as claim:
                            claim.write(request_id)
                        started = time.monotonic()
                        response = await model.rerank(case['query'], docs, context.child(args.case + '/' + label))
                        summary['provider_calls'] += 1
                        summary['arms'][label].update(seconds=time.monotonic() - started,
                            request_id=request_id, response=asdict(response))
                        write(output / (label + '.response.json'), asdict(response))
                        write(output / 'result.json', summary)
                        assert response.usage.status == 'REPORTED', 'UNREPORTED_USAGE_STOP'
                        assert read(grant_path)['status'] == 'OPEN', 'EXECUTION_STOP'
                after = await app._rows("SELECT call_id,md5(row_to_json(p)::text) AS fingerprint FROM port_provider_calls p WHERE outcome='UNKNOWN' ORDER BY call_id")
                write(output / 'unknown-after.json', after)
                assert protected == after, 'UNKNOWN_LEDGER_CHANGED'
                summary['historical_unknown_unchanged'] = True
            finally:
                await registry.close()
        summary['status'] = 'COMPLETE' if args.send else 'PREFLIGHT_PASS_ZERO_SEND'
    except BaseException as exc:
        summary.update(status='STOP', error=type(exc).__name__ + ':' + str(exc))
        raise
    finally:
        if args.send:
            ledger = read(grant_path)
            good = all(r['status'] == 'SETTLED' for r in ledger['entries']) and summary.get('status') == 'COMPLETE'
            ledger['status'] = 'CLOSED' if good else 'STOP'
            batch.update(status=ledger['status'], calls=len(ledger['entries']),
                tokens=sum(r['held_tokens'] for r in ledger['entries']))
            if good:
                ledger.update(unused_calls_revoked=ledger['max_calls'] - batch['calls'],
                              unused_tokens_revoked=ledger['max_tokens'] - batch['tokens'])
            else:
                parent['status'] = 'STOP'
            write(grant_path, ledger)
            write(args.authorization, parent)
            summary['accounting'] = batch
        write(output / 'result.json', summary)
    print(json.dumps({k: summary.get(k) for k in ('case_id', 'status', 'provider_calls', 'input_chars', 'accounting')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('config', 'principal-state', 'plan', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--authorization', type=Path)
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    if args.send and args.authorization is None:
        parser.error('--send requires --authorization')
    asyncio.run(run(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None)
