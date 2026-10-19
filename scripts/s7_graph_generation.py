"""S7 fixed-evidence generation using existing rendering, Registry and Gate.

Root alone opens/closes the registered LocalExecution phase. This tool cannot
create a grant, renew a phase, change roles, retrieve sources or retry a call.
The only sendable material comes from each arm's finalized context/evidence.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
QWEN_ID = 'mdlf5350f8b80d8971c14ddaa8524f8a039d414e85c35a657e4271228473b25e'
QWEN = 'Qwen/Qwen3.5-4B'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=str)
        handle.write('\n')


def require(condition, code):
    if not condition:
        raise ValueError(code)


def prepare_case(case, arm):
    """Return immutable GenerationRequest and its formal PreparedGeneration.

    Deliberately does not read scoring_only, answers, qrels or expected chunks.
    """
    from backend.weknora_port.generation import GenerationRequest
    from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT, Evidence, History, prepare_generation
    from backend.weknora_port.models.types import ChatOptions
    from backend.weknora_port.retrieval.context import FinalizedContext
    from backend.weknora_port.retrieval.types import SearchResult

    selected = case['arms'][arm]
    raw_final = selected.get('finalized_context', selected.get('finalized'))
    require(isinstance(raw_final, dict), 'S7_FINALIZED_CONTEXT_REQUIRED')
    final = dict(raw_final)
    final['result_ids'] = tuple(final['result_ids'])
    final['images'] = tuple(final.get('images', ()))
    finalized = FinalizedContext(**final)
    if 'evidence' in selected:
        evidence = tuple(Evidence(**item) for item in selected['evidence'])
    else:
        rows = selected.get('search_results', selected.get('selected_results'))
        require(isinstance(rows, list), 'S7_RENDERED_SOURCE_RESULTS_REQUIRED')
        evidence = tuple(Evidence.from_result(SearchResult(**row)) for row in rows)
    timestamp = selected.get('rendered_at', case.get('rendered_at'))
    require(isinstance(timestamp, str) and bool(timestamp), 'S7_FROZEN_RENDER_TIME_REQUIRED')
    rendered_at = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    q0 = case.get('question', case.get('query'))
    require(isinstance(q0, str) and bool(q0.strip()), 'S7_ORIGINAL_QUESTION_REQUIRED')
    params = dict(case.get('generation_parameters', {}))
    allowed = {'temperature', 'max_completion_tokens', 'thinking'}
    require(not set(params) - allowed, 'S7_UNEXPECTED_GENERATION_PARAMETERS')
    require(params.get('thinking', False) is False and params.get('max_completion_tokens', 1024) == 1024,
            'S7_FROZEN_OUTPUT_PARAMETERS_CHANGED')
    require(params.get('temperature', .3) == .3, 'S7_FROZEN_TEMPERATURE_CHANGED')
    options = ChatOptions(temperature=params.get('temperature', .3), max_completion_tokens=1024, thinking=False)
    request = GenerationRequest(run_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()), message_id=str(uuid.uuid4()),
        q0=q0, finalized=finalized, evidence=evidence,
        history=tuple(History(**value) for value in selected.get('history', case.get('history', []))),
        memory_prompt=selected.get('memory_prompt', case.get('memory_prompt', '')),
        language=selected.get('language', case.get('language', 'Chinese (Simplified)')),
        system_prompt=selected.get('system_prompt', case.get('system_prompt', DEFAULT_SYSTEM_PROMPT)),
        is_fallback=selected.get('is_fallback', False), supports_vision=False, citations_enabled=False,
        faq_priority_enabled=selected.get('faq_priority_enabled', False), options=options, rendered_at=rendered_at)
    require(not finalized.images, 'S7_TEXT_ONLY_GENERATION_REQUIRED')
    return request, prepare_generation(request)


def selected_calls(pack, requested, arms):
    cases = pack.get('cases')
    require(isinstance(cases, list), 'S7_CONTRAST_CASES_REQUIRED')
    ids = [case['id'] for case in cases]
    require(len(ids) == len(set(ids)) and set(requested) <= set(ids), 'S7_CASE_IDENTITY_MISMATCH')
    require(all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in ids), 'S7_CASE_FILENAME_INVALID')
    require(set(arms) <= {'off', 'on'}, 'S7_UNKNOWN_ARM')
    require(all(set(arms) <= set(case['arms']) for case in cases if case['id'] in requested), 'S7_REQUESTED_ARM_MISSING')
    global_parameters = pack.get('generation_parameters', {})
    require(not set(global_parameters) - {'thinking', 'temperature', 'max_completion_tokens'}
            and global_parameters.get('thinking', False) is False
            and global_parameters.get('temperature', .3) == .3
            and global_parameters.get('max_completion_tokens', 1024) == 1024, 'S7_GLOBAL_GENERATION_PARAMETERS_CHANGED')
    calls = [(case, arm) for case in cases if case['id'] in requested for arm in arms if arm in case['arms']]
    require(0 < len(calls) <= 10 and len(calls) == len({(case['id'], arm) for case, arm in calls}), 'S7_FINITE_GENERATION_PLAN_REQUIRED')
    return calls


def stage_module(path):
    spec = importlib.util.spec_from_file_location('s7_owner_execution', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_phase(stage, ledger_path, principal, kb, planned):
    parent, ledger = read(stage.AUTH), read(ledger_path)
    require(parent.get('status') == 'OPEN' and parent.get('owner_approval', {}).get('answer') == '批准上述 S7 有限验证',
            'S7_OWNER_AUTHORIZATION_REQUIRED')
    registered = {Path(path).resolve() for path in parent['ledgers']}
    summary = stage.summary()
    require(Path(ledger_path).resolve() in registered and not summary['unknown']
            and {Path(path).resolve() for path in summary['active']} == {Path(ledger_path).resolve()}, 'S7_ROOT_OPEN_PHASE_REQUIRED')
    require(ledger['scope'] == 'S7_20261019' and ledger['status'] == 'OPEN'
            and ledger['tenant_id'] == 332 == principal.tenant_id
            and ledger['subject_id'] == principal.subject_id and ledger['allowed_kbs'] == [kb]
            and ledger.get('capture_evidence') is True, 'S7_EXECUTION_SCOPE_MISMATCH')
    require(all(entry['status'] == 'SETTLED' for entry in ledger['entries'])
            and len(ledger['entries']) + planned <= ledger['max_calls'] <= 12, 'S7_UNSETTLED_OR_CALL_LIMIT_STOP')
    expected = {'provider': 'siliconflow', 'model': QWEN, 'model_id': QWEN_ID, 'capability': 'chat'}
    require(expected in ledger['allowed_models'], 'S7_QWEN_CANDIDATE_NOT_AUTHORIZED')


async def run_selected(args):
    from scripts.local_runtime import environment, load_config, verify_resources
    from scripts.s2_generation_quality import identity_for
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from backend.weknora_port.models.output_contract import resolve_output_contract

    require(args.output.is_absolute() and args.output.drive.upper() in ('D:', 'E:')
            and not args.output.resolve().is_relative_to(ROOT), 'S7_EXTERNAL_OUTPUT_REQUIRED')
    pack = read(args.contrasts)
    calls = selected_calls(pack, args.cases.split(','), args.arms.split(','))
    prepared = [(case, arm, *prepare_case(case, arm)) for case, arm in calls]
    cfg = load_config(args.config)
    resources = await verify_resources(cfg)
    env = environment(cfg, resources['redis_run_id'])
    for path in reversed(env.get('PYTHONPATH', '').split(os.pathsep)):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    env, principal = identity_for(332, env, read(args.principal_state))
    stage = stage_module(args.stage_helper)
    plan = read(args.plan)
    kb = plan['knowledge_base_id']
    require(plan['tenant_id'] == 332 and sha(principal.subject_id.encode()) == plan['subject_sha256'], 'S7_VERIFIED_SUBJECT_MISMATCH')
    validate_phase(stage, args.ledger, principal, kb, len(prepared))
    for _, _, request, _ in prepared:
        require(all(e.knowledge_base_id == kb for e in request.evidence), 'S7_CONTEXT_KB_SCOPE_MISMATCH')
    env.pop('WEKNORA_PORT_EVIDENCE_DIR', None)
    env.update(WEKNORA_PORT_CLOUD_ALLOWED='true', WEKNORA_PORT_EXECUTION_FILE=str(args.ledger))
    args.output.mkdir(parents=True, exist_ok=True)
    write_new(args.output / 'batch.claim.json', {'once': True, 'ledger': str(args.ledger),
        'contrast_path': str(args.contrasts), 'contrast_sha256': sha(args.contrasts.read_bytes()),
        'call_ids': [(case['id'], arm) for case, arm, _, _ in prepared], 'retry': False})
    results = {'schema': 's7-fixed-generation/v1', 'scope': 'FIXED_CONTEXT_DIAGNOSTIC_NOT_FULL_RAG', 'calls': []}
    async with assemble(DevelopmentSettings.from_environment(env), principal=principal) as app:
        require(app.principal == principal and app.execution.path.resolve() == args.ledger.resolve(), 'S7_RUNTIME_FENCE_IDENTITY_MISMATCH')
        registry = app.registry_for_context()
        try:
            for index, (case, arm, request, rendering) in enumerate(prepared):
                validate_phase(stage, args.ledger, principal, kb, 1)
                context = CallContext(request.run_id, TrustedScope(principal.subject_id, (kb,), ()))
                model = await registry.chat(QWEN_ID, context)
                require(model.config.provider == 'siliconflow' and model.config.model == QWEN
                        and model.config.model_id == QWEN_ID, 'S7_ACTUAL_MODEL_MISMATCH')
                body = model.build_request_body(rendering.messages, request.options, stream=False)
                contract = resolve_output_contract(model.config, body, capability='chat')
                require(body.get('enable_thinking') is False and body.get('max_tokens') == 1024
                        and contract.output_upper == 1024, 'S7_OUTPUT_BOUND_OR_THINKING_MISMATCH')
                item = {'case_id': case['id'], 'arm': arm, 'request_id': request.run_id,
                        'provider': model.config.provider, 'model': model.config.model, 'model_id': QWEN_ID,
                        'prompt_sha256': rendering.prompt_sha256, 'context_sha256': rendering.context_sha256,
                        'source_ids': [e.id for e in request.evidence],
                        'evidence': [asdict(e) for e in request.evidence], 'messages': [asdict(m) for m in rendering.messages],
                        'body': body, 'output_contract': asdict(contract), 'status': 'PREPARED'}
                path = args.output / f'{index:02}-{case["id"]}-{arm}.json'
                write_new(path, item)
                began = time.perf_counter()
                try:
                    response = await model.chat(rendering.messages, request.options, context)
                    item.update(status='RESPONSE_RECEIVED', seconds=time.perf_counter() - began,
                                response=asdict(response))
                    rows = await app._rows('SELECT call_id,request_id,provider,model,model_id,outcome,usage,wire_sha256 FROM port_provider_calls '
                                           'WHERE tenant_id=%s AND subject_id=%s AND request_id=%s', (332, principal.subject_id, request.run_id))
                    item['durable_receipts'] = rows
                    require(len(rows) == 1 and rows[0]['outcome'] == 'RESPONSE_RECEIVED'
                            and (rows[0].get('usage') or {}).get('status') == 'REPORTED'
                            and rows[0]['provider'] == 'siliconflow' and rows[0]['model'] == QWEN
                            and rows[0]['model_id'] == QWEN_ID and rows[0]['wire_sha256'] == sha(body)
                            and response.model == QWEN, 'S7_UNKNOWN_SETTLEMENT_OR_MODEL_IDENTITY_STOP')
                    item['generation_quality_state'] = ('COMPLETE_REQUIRES_FACT_REVIEW' if response.finish_reason == 'stop' and response.content.strip()
                                                         else 'EMPTY_OR_NONSTOP_OUTPUT_RETAINED')
                    validate_phase(stage, args.ledger, principal, kb, 0)
                except BaseException as error:
                    item.update(status='FAILED_RETAINED_NO_RETRY', seconds=time.perf_counter() - began,
                                error_code=getattr(error, 'code', None), exception_type=type(error).__name__)
                    raise
                finally:
                    path.write_text(json.dumps(item, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
                    results['calls'].append(item)
                    (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
                print(json.dumps({'case': case['id'], 'arm': arm, 'finish_reason': response.finish_reason,
                                  'usage': asdict(response.usage), 'seconds': item['seconds']}, ensure_ascii=True), flush=True)
        finally:
            await registry.close()
    results['status'] = 'EXECUTED_REQUIRES_SOURCE_SUPPORT_REVIEW'
    (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    return results


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('contrasts', 'ledger', 'config', 'principal-state', 'output', 'plan', 'stage-helper'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--cases', required=True, help='Comma-separated frozen case IDs')
    parser.add_argument('--arms', default='off,on', help='Only existing off/on arms; no retries')
    return parser.parse_args(argv)


def main(argv=None):
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(run_selected(arguments(argv)))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'STOP', 'exception_type': type(error).__name__, 'error_code': getattr(error, 'code', None)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
