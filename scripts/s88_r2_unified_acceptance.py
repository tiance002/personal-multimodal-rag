"""Explicit R2 composition and one-case HTTP evidence collection; import sends nothing.

Authorization, image/source verification, model binding resolution, server startup,
and any narrowly approved operational allowance belong to the caller. This module
never creates a grant, starts a service, changes a model role, or retries a POST.
All content-bearing inputs and output artifacts are supplied as private files.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import s8_6_1_api_acceptance as prior
from scripts import s8_8_acceptance_profile as profile
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT
from backend.weknora_port.generation.prompt_candidates import apply_candidate

SCHEMA = 's88-r2-unified-plan/v1'
PROMPT_VERSION = 's88-source-precision/v3'
INFERENCE_FIELDS = {'schema', 'input_contract_schema', 'input_contract_sha256_canonical',
                    'gold_included', 'historical_labels_included', 'count', 'cases'}
CASE_FIELDS = {'ordinal', 'case_id', 'dataset', 'qid', 'question', 'q0_sha256',
               'owner_id', 'knowledge_base_ids', 'document_ids'}
PLAN_FIELDS = {'schema', 'inference_inputs', 'model_bindings', 'request_ids',
               'qu_request_sha256s', 'expires_at', 'deployment', 'execution_file',
               'rerank_context_enabled', 'prompt_version', 'plan_sha256'}
require = prior.require
digest = prior.digest
write_new = prior.write_new


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _strings(value):
    return isinstance(value, list) and all(isinstance(x, str) and x for x in value) and len(value) == len(set(value))


def validate_inference(value):
    """Only independent task inputs can cross this executable boundary."""
    require(isinstance(value, dict) and set(value) == INFERENCE_FIELDS, 'R2_INFERENCE_WHITELIST_REQUIRED')
    require(value['schema'] == 'ECS55_R2_INFERENCE_INPUTS_V1'
            and value['input_contract_schema'] == 'ECS55_INPUT_CONTRACT_V2'
            and _sha(value['input_contract_sha256_canonical'])
            and value['gold_included'] is False and value['historical_labels_included'] is False
            and type(value['count']) is int and value['count'] == 55, 'R2_FROZEN_COHORT_REQUIRED')
    cases = value['cases']
    require(isinstance(cases, list) and len(cases) == 55, 'R2_55_CASES_REQUIRED')
    require(all(isinstance(row, dict) and set(row) == CASE_FIELDS for row in cases), 'R2_CASE_WHITELIST_REQUIRED')
    require(all(type(row['ordinal']) is int for row in cases)
            and sorted(row['ordinal'] for row in cases) == list(range(55))
            and len({row['case_id'] for row in cases}) == 55
            and len({(row['dataset'], row['qid']) for row in cases}) == 55, 'R2_UNIQUE_ORDINAL_IDENTITY_REQUIRED')
    for row in cases:
        require(all(isinstance(row[key], str) and row[key] for key in ('case_id', 'qid', 'question', 'owner_id')),
                'R2_CASE_IDENTITY_REQUIRED')
        require(digest(row['question'].encode('utf8')) == row['q0_sha256'], 'R2_Q0_SHA256_MISMATCH')
        require(prior.query_input_bound(row['question'])['valid'], 'R2_QUERY_BOUND_EXCEEDED')
        require(_strings(row['knowledge_base_ids']) and len(row['knowledge_base_ids']) == 1
                and _strings(row['document_ids']), 'R2_SCOPE_REQUIRED')
        require(row['dataset'] in ('longbench-zh', 'mmlongbench-doc', 'hotpotqa'), 'R2_DATASET_REQUIRED')
        require(len(row['document_ids']) == (0 if row['dataset'] == 'hotpotqa' else 1), 'R2_TASK_SCOPE_MISMATCH')
    return deepcopy(value)


def validate_frozen_cohort(manifest, inference_inputs):
    """Audit manifest may contain Gold; only the existing whitelist is projected.

    The manifest is never inserted into the executable plan or HTTP requests.
    Its complete canonical hash still binds the independent input freeze.
    """
    from scripts.s88_r2_input_contract import inference_inputs as project
    require(isinstance(manifest, dict) and manifest.get('schema') == 'ECS55_INPUT_CONTRACT_V2'
        and manifest.get('status') == 'FROZEN_OFFLINE', 'R2_FROZEN_MANIFEST_REQUIRED')
    value = validate_inference(inference_inputs)
    require(project(manifest) == value, 'R2_MANIFEST_INFERENCE_MISMATCH')
    require(all(row.get('status') == 'FROZEN' and row.get('question_changed') is False
        and row.get('old_question') == row.get('new_question') for row in manifest['cases']),
        'R2_FROZEN_MANIFEST_REQUIRED')
    return value


def build_plan(inference_inputs, *, model_bindings, request_ids, qu_request_sha256s,
               expires_at, deployment, execution_file, rerank_context_enabled=True, frozen_manifest=None):
    """Pure offline assembly. IDs/hashes/bindings must already be approved."""
    require(frozen_manifest is not None, 'R2_FROZEN_MANIFEST_REQUIRED')
    plan = {'schema': SCHEMA, 'inference_inputs': validate_frozen_cohort(frozen_manifest, inference_inputs),
            'model_bindings': deepcopy(model_bindings),
            'request_ids': {str(k): v for k, v in request_ids.items()},
            'qu_request_sha256s': deepcopy(qu_request_sha256s), 'expires_at': expires_at,
            'deployment': deepcopy(deployment), 'execution_file': str(execution_file),
            'rerank_context_enabled': rerank_context_enabled, 'prompt_version': PROMPT_VERSION}
    plan['plan_sha256'] = digest(plan)
    return validate_plan(plan)


def validate_plan(plan, *, check_expiry=True):
    require(isinstance(plan, dict) and set(plan) == PLAN_FIELDS and plan['schema'] == SCHEMA,
            'R2_PLAN_WHITELIST_REQUIRED')
    require(plan['plan_sha256'] == digest({k: v for k, v in plan.items() if k != 'plan_sha256'}), 'R2_PLAN_HASH_MISMATCH')
    validate_inference(plan['inference_inputs'])
    require(plan['prompt_version'] == PROMPT_VERSION and type(plan['rerank_context_enabled']) is bool,
            'R2_UNIFORM_PROFILE_REQUIRED')
    binding = plan['deployment']
    require(isinstance(binding, dict) and set(binding) == {'image_id', 'source_commit'}
            and isinstance(binding['image_id'], str) and binding['image_id'].startswith('sha256:')
            and _sha(binding['image_id'][7:]) and isinstance(binding['source_commit'], str)
            and re.fullmatch(r'[0-9a-f]{40}', binding['source_commit']) is not None, 'R2_DEPLOYMENT_BINDING_REQUIRED')
    require(isinstance(plan['execution_file'], str) and bool(plan['execution_file']), 'R2_NATIVE_LEDGER_REQUIRED')
    ids = plan['request_ids']
    require(isinstance(ids, dict) and set(ids) == {str(i) for i in range(55)}
            and all(isinstance(v, str) and v.startswith('s88-r2-') and len(v) > 12 for v in ids.values())
            and len(set(ids.values())) == 55, 'R2_FRESH_TRUSTED_IDS_REQUIRED')
    hashes = plan['qu_request_sha256s']
    require(isinstance(hashes, dict) and set(hashes) == set(ids.values())
            and all(_sha(v) for v in hashes.values()), 'R2_EXACT_QU_HASHES_REQUIRED')
    models = plan['model_bindings']
    require(isinstance(models, dict) and {'qu', 'cheap', 'expensive', 'embedding', 'rerank'} <= set(models)
            and set(models) <= {'qu', 'cheap', 'expensive', 'embedding', 'rerank', 'vlm'}, 'R2_RESOLVED_MODELS_REQUIRED')
    for role, model in models.items():
        require(isinstance(model, dict) and set(model) == {'model_id', 'provider', 'model', 'capability'}
                and all(isinstance(v, str) and v for v in model.values())
                and model['capability'] == {'embedding': 'embedding', 'rerank': 'rerank', 'vlm': 'vision'}.get(role, 'chat'),
                'R2_RESOLVED_MODELS_REQUIRED')
    try:
        expiry = datetime.fromisoformat(plan['expires_at'].replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('R2_EXPIRY_REQUIRED') from None
    require(expiry.tzinfo is not None, 'R2_EXPIRY_REQUIRED')
    if check_expiry:
        require(expiry > datetime.now(timezone.utc), 'R2_PLAN_EXPIRED')
    return deepcopy(plan)


def approved_batch(plan):
    plan = validate_plan(plan)
    models = plan['model_bindings']
    turns, scopes = [], {}
    for case in sorted(plan['inference_inputs']['cases'], key=lambda c: c['ordinal']):
        client_id = plan['request_ids'][str(case['ordinal'])]
        turns.append({'request_id': client_id, 'question': case['question'],
            'mode': 'query_structure_v1', 'reasoning_mode': 'quick', 'max_output_tokens': 2048,
            'rerank_context_enabled': plan['rerank_context_enabled']})
        scopes[client_id] = {key: case[key] for key in ('knowledge_base_ids', 'document_ids')}
    return {'turns': turns, 'qu_model_id': models['qu']['model_id'],
        'model_ids': {role: model['model_id'] for role, model in models.items()},
        'qu_request_sha256s': plan['qu_request_sha256s']}, scopes


def install(runtime, plan, evidence_directory=None):
    """Existing admission/HTTP/Registry/SendGate paths remain the execution path."""
    frozen = validate_plan(plan)
    batch, scopes = approved_batch(frozen)
    profile.install_profile(runtime, evidence_directory, approved_batch=batch, approved_scopes=scopes,
        expires_at=datetime.fromisoformat(frozen['expires_at'].replace('Z', '+00:00')))
    original = runtime.ordinary.configuration
    expected = apply_candidate(DEFAULT_SYSTEM_PROMPT + '\n\n' + prior.PROMPT_B_SUFFIX, version=PROMPT_VERSION)
    async def configuration(turn):
        client_id = prior._accepted_client_request_id(turn)
        allowed = next((c for c in frozen['inference_inputs']['cases']
            if frozen['request_ids'][str(c['ordinal'])] == client_id), None)
        require(allowed is not None and turn.context.scope.owner_id == allowed['owner_id'], 'R2_OWNER_SCOPE_MISMATCH')
        result = await original(turn)
        options = dict(result.generation_options)
        require(options['system_prompt'] == DEFAULT_SYSTEM_PROMPT + '\n\n' + prior.PROMPT_B_SUFFIX,
                'R2_DEFAULT_PROMPT_B_REQUIRED')
        options['system_prompt'] = apply_candidate(options['system_prompt'], version=PROMPT_VERSION)
        require(options['system_prompt'] == expected, 'R2_CATALOG_PROMPT_REQUIRED')
        if evidence_directory is not None:
            write_new(Path(evidence_directory) / (digest(turn.run_id.encode()) + '.r2-prompt.json'), {
                'run_id': turn.run_id, 'plan_sha256': frozen['plan_sha256'], 'prompt_version': PROMPT_VERSION,
                'system_prompt_sha256': digest(expected.encode()), 'prompt_source': 'DEFAULT_PLUS_B_PLUS_EXISTING_CATALOG_V3',
                'proof_boundary': 'resolved configuration; actual wire requires matching provider receipt'})
        return replace(result, generation_options=options)
    runtime.ordinary.configuration = configuration
    return runtime


def ledger_delta(before, after):
    """Compare raw native entries by their immutable request/capability identity."""
    def indexed(snapshot):
        rows = snapshot.get('entries', [])
        require(all(isinstance(r, dict) and isinstance(r.get('request_id'), str)
                    and r.get('request_id') and isinstance(r.get('capability'), str)
                    and r.get('capability') for r in rows), 'R2_LEDGER_ENTRY_ID_REQUIRED')
        result = {(r['request_id'], r['capability']): r for r in rows}
        require(len(result) == len(rows), 'R2_LEDGER_DUPLICATE_ID')
        return result
    old, new = indexed(before), indexed(after)
    added = [new[k] for k in new.keys() - old.keys()]
    return {'added': added,
            'added_identities': [{'request_id': r['request_id'], 'capability': r['capability']} for r in added],
            'changed': [{'before': old[k], 'after': new[k]} for k in old.keys() & new.keys() if old[k] != new[k]],
            'removed_ids': [{'request_id': request_id, 'capability': capability}
                for request_id, capability in sorted(old.keys() - new.keys())],
            'before_sha256': digest(before), 'after_sha256': digest(after)}


def native_ledger_snapshot(path):
    """Read the native ledger verbatim; summary status is a separate derived view."""
    value = json.loads(Path(path).read_text(encoding='utf8'))
    require(isinstance(value, dict) and isinstance(value.get('entries'), list), 'R2_NATIVE_LEDGER_REQUIRED')
    return deepcopy(value)


def _response(response):
    try:
        body = response.json()
    except ValueError:
        body = {'unparsed_text': response.text}
    return {'status': response.status_code, 'body': body}


def _answer(frames):
    return ''.join(f.get('content', '') for f in frames if f.get('response_type') == 'answer')


async def collect_one(client, plan, ordinal, output, *, binding):
    """One explicitly selected frozen case, one message POST, no retries.

    Caller supplies an authenticated httpx client and externally verified image/
    source binding. Native ledger snapshots are private read-only observations.
    Return failures after writing all obtainable terminal/history/replay artifacts.
    """
    frozen = validate_plan(plan)
    require(binding == frozen['deployment'], 'R2_EXECUTION_BINDING_MISMATCH')
    require(type(ordinal) is int and ordinal in range(55), 'R2_ORDINAL_REQUIRED')
    case = next(c for c in frozen['inference_inputs']['cases'] if c['ordinal'] == ordinal)
    target = Path(output)
    target.mkdir(parents=True, exist_ok=False)
    write_new(target / 'input.json', {'case': case, 'request_id': frozen['request_ids'][str(ordinal)],
        'plan_sha256': frozen['plan_sha256'], 'deployment': binding, 'prompt_version': PROMPT_VERSION,
        'authorization': 'external; this collector neither creates nor expands authority'})
    sid = None
    frames, sources, history, replay_frames = [], [], None, []
    result = {'ordinal': ordinal, 'case_id': case['case_id'], 'status': 'FAILED', 'message_posts': 0,
        'final_answer_correct': 'NOT_REVIEWED', 'evidence_support': 'NOT_REVIEWED',
        'clarification': 'NOT_REVIEWED', 'citation_claim_support': 'NOT_ASSESSED'}
    began = time.monotonic()
    before = None
    async def request(method, path, name, **kwargs):
        response = await client.request(method, path, **kwargs)
        value = _response(response)
        write_new(target / name, value)
        require(response.status_code == 200, 'R2_HTTP_' + name.upper().replace('.', '_'))
        return value['body']['data']
    try:
        runtime = await request('GET', '/api/v1/runtime', 'runtime.json')
        require(runtime.get('implementation') == 'weknora-python-port', 'R2_RUNTIME_IDENTITY_MISMATCH')
        before_native = native_ledger_snapshot(frozen['execution_file'])
        before = prior.ledger_status(frozen['execution_file'])
        write_new(target / 'ledger-before.json', before_native)
        write_new(target / 'ledger-before-summary.json', before)
        require(before['unsettled'] == 0 and before['unknown'] == 0, 'R2_UNSETTLED_BEFORE_CASE')
        generation = {'reasoning_mode': 'quick', 'max_output_tokens': 2048}
        scope = {'knowledge_base_scope': case['knowledge_base_ids'], 'document_scope': case['document_ids']}
        session = await request('POST', '/api/v1/sessions', 'session.json', json={
            'title': 'S8.8-R2 ' + case['case_id'], **scope, **generation,
            'source_evidence_mode': 'bounded_source_v1', 'retrieval_strategy_mode': 'query_structure_v1'})
        sid = session['id']
        require(session.get('knowledge_base_scope') == case['knowledge_base_ids']
                and session.get('document_scope') == case['document_ids'], 'R2_SESSION_SCOPE_MISMATCH')
        await request('PUT', f'/api/v1/sessions/{sid}/generation-settings', 'settings-save.json', json=generation)
        settings = await request('GET', f'/api/v1/sessions/{sid}/generation-settings', 'settings-read.json')
        require(settings.get('effective') == generation
                and all(settings.get('saved', {}).get(k) == v for k, v in generation.items()), 'R2_SETTINGS_MISMATCH')
        body = {'content': case['question'], 'request_id': frozen['request_ids'][str(ordinal)],
            'mode': 'quick', **scope, **generation, 'reasoning_effort': 'off',
            'summary_model_id': frozen['model_bindings']['cheap']['model_id']}
        write_new(target / 'submission-claim.json', {'request_id': body['request_id'], 'body_sha256': digest(body),
            'plan_sha256': frozen['plan_sha256'], 'retry': False})
        result['message_posts'] = 1
        with (target / 'response.sse').open('xb') as stream:
            async with client.stream('POST', f'/api/v1/sessions/{sid}/messages', json=body,
                    headers=prior.profile_request_headers(case['question'])) as response:
                result['message_status'] = response.status_code
                async for chunk in response.aiter_bytes():
                    stream.write(chunk)
                    stream.flush()
        require(result['message_status'] == 200, 'R2_MESSAGE_POST_FAILED')
        frames = prior.parse_sse((target / 'response.sse').read_text(encoding='utf8'))
        write_new(target / 'frames.json', frames)
        require(prior.completed_answer_contract(frames), 'R2_INCOMPLETE_OUTPUT')
        result['completion_source'] = frames[-1].get('data', {}).get('completion_source', 'provider')
        result['status'] = 'HTTP_COMPLETED_REVIEW_REQUIRED'
    except Exception as error:
        result['error'] = {'type': type(error).__name__, 'message': str(error)}
    finally:
        # Every failed stream keeps partial bytes; parse what is complete without
        # fabricating a complete terminal event or another message POST.
        if not (target / 'frames.json').exists():
            try:
                raw = (target / 'response.sse').read_text(encoding='utf8') if (target / 'response.sse').exists() else ''
                complete = raw.rsplit('\n\n', 1)[0] + '\n\n' if '\n\n' in raw else ''
                frames = prior.parse_sse(complete) if complete else []
                write_new(target / 'frames.json', frames)
            except Exception as error:
                write_new(target / 'frames-error.json', {'type': type(error).__name__, 'message': str(error)})
        after = None
        try:
            after_native = native_ledger_snapshot(frozen['execution_file'])
            after = prior.ledger_status(frozen['execution_file'])
            write_new(target / 'ledger-after.json', after_native)
            write_new(target / 'ledger-after-summary.json', after)
            if before is not None:
                delta = ledger_delta(before_native, after_native)
                write_new(target / 'ledger-delta.json', delta)
                require(not delta['removed_ids'] and not delta['changed'], 'R2_EXISTING_LEDGER_ENTRY_CHANGED')
                require(after['unsettled'] == 0 and after['unknown'] == 0, 'R2_UNSETTLED_AFTER_CASE')
        except Exception as error:
            result['status'] = 'FAILED'
            result['ledger_error'] = {'type': type(error).__name__, 'message': str(error)}
        try:
            if sid is not None:
                history = await request('GET', f'/api/v1/sessions/{sid}/messages', 'history.json')
                assistants = [m for m in history if m.get('role') == 'assistant']
                users = [m for m in history if m.get('role') == 'user']
                require(len(users) == 1 and users[0].get('content') == case['question'], 'R2_HISTORY_Q0_MISMATCH')
                require(len(assistants) == 1, 'R2_HISTORY_ASSISTANT_REQUIRED')
                assistant = assistants[0]
                require(assistant.get('is_completed') is True and assistant.get('turn_status') == 'completed',
                    'R2_HISTORY_INCOMPLETE')
                result['assistant_message_id'] = assistant['id']
                result['server_run_id'] = assistant.get('request_id')
                replay = await client.get(f'/api/v1/sessions/continue-stream/{sid}', params={'message_id': assistant['id']})
                replay_frames = prior.parse_sse(replay.text) if replay.status_code == 200 else []
                write_new(target / 'replay.json', {'status': replay.status_code, 'frames': replay_frames, 'raw': replay.text})
                after_replay_native = native_ledger_snapshot(frozen['execution_file'])
                after_replay = prior.ledger_status(frozen['execution_file'])
                write_new(target / 'ledger-after-replay.json', after_replay_native)
                write_new(target / 'ledger-after-replay-summary.json', after_replay)
                require(after is not None and after_replay_native == after_native
                    and after_replay == after, 'R2_REPLAY_NEW_SEND_OR_STATE_CHANGE')
                require(replay.status_code == 200 and replay_frames, 'R2_REPLAY_REQUIRED')
                require(_answer(replay_frames) == _answer(frames) == assistant.get('content'), 'R2_REPLAY_HISTORY_ANSWER_MISMATCH')
                require(replay_frames[-1] == frames[-1], 'R2_REPLAY_TERMINAL_MISMATCH')
                require([f for f in replay_frames if f.get('response_type') == 'references']
                    == [f for f in frames if f.get('response_type') == 'references'], 'R2_REPLAY_REFERENCE_MISMATCH')
                queries = [f.get('data', {}) for f in frames if f.get('response_type') == 'agent_query']
                require(len(queries) == 1 and queries[0].get('query') == case['question']
                    and queries[0].get('session_id') == sid and queries[0].get('assistant_message_id') == assistant['id']
                    and queries[0].get('request_id') == assistant.get('request_id'), 'R2_RUN_BINDING_MISMATCH')
                require(all(f.get('id') == assistant['request_id'] for f in [*frames, *replay_frames]), 'R2_SSE_RUN_ID_MISMATCH')
                refs = assistant.get('knowledge_references') or []
                reference_events = [f.get('knowledge_references') or f.get('data', {}).get('references') or []
                    for f in frames if f.get('response_type') == 'references']
                require((reference_events[-1] if reference_events else []) == refs, 'R2_HISTORY_REFERENCES_MISMATCH')
                for reference in refs:
                    identifier = reference.get('chunk_id') or reference.get('id')
                    require(isinstance(identifier, str) and bool(identifier), 'R2_CITATION_ID_REQUIRED')
                    response = await client.get('/api/v1/chunks/' + identifier)
                    source = _response(response)
                    sources.append({'chunk_id': identifier, **source})
                    write_new(target / (digest(identifier.encode()) + '.source.json'), sources[-1])
                    require(response.status_code == 200, 'R2_CITATION_READBACK_FAILED')
                    row = source['body']['data']
                    require(row.get('chunk_id') == identifier and row.get('knowledge_base_id') in case['knowledge_base_ids']
                        and (not case['document_ids'] or row.get('document_id') in case['document_ids']), 'R2_CITATION_SCOPE_MISMATCH')
                    expected_version = (reference.get('metadata') or {}).get('source_version')
                    require(not expected_version or row.get('source_version') == expected_version, 'R2_CITATION_VERSION_MISMATCH')
                result['citation_ownership'] = 'READBACK_ONLY' if sources else 'NO_CITATIONS'
                if frames and frames[-1].get('data', {}).get('completion_source') == 'server_fixed':
                    result['interaction_kind'] = 'SERVER_FIXED_REPLY_REVIEW_REQUIRED'
                    result['clarification'] = 'REASONABLENESS_NOT_REVIEWED'
        except Exception as error:
            result['status'] = 'FAILED'
            result['verification_error'] = {'type': type(error).__name__, 'message': str(error)}
        finally:
            write_new(target / 'sources.json', sources)
            result['session_id'] = sid
            result['whole_seconds'] = time.monotonic() - began
            write_new(target / 'result.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['validate', 'collect'])
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--ordinal', type=int)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--base-url')
    parser.add_argument('--image-id')
    parser.add_argument('--source-commit')
    parser.add_argument('--token-env', default='S88_R2_API_TOKEN')
    args = parser.parse_args(argv)
    plan = validate_plan(json.loads(args.plan.read_text(encoding='utf-8-sig')))
    validate_frozen_cohort(json.loads(args.manifest.read_text(encoding='utf-8-sig')), plan['inference_inputs'])
    if args.command == 'validate':
        print(json.dumps({'status': 'OFFLINE_VALIDATED', 'plan_sha256': plan['plan_sha256'], 'provider_calls': 0}))
        return 0
    from urllib.parse import urlsplit
    import asyncio
    import httpx
    require(args.ordinal is not None and args.output is not None and args.base_url
        and args.image_id and args.source_commit, 'R2_EXPLICIT_COLLECT_ARGUMENTS_REQUIRED')
    parsed = urlsplit(args.base_url)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.path in ('', '/')
        and not parsed.query and not parsed.fragment and not parsed.username and not parsed.password,
        'R2_LOOPBACK_API_REQUIRED')
    token = os.environ.get(args.token_env)
    require(isinstance(token, str) and bool(token), 'R2_EXTERNAL_AUTHENTICATION_REQUIRED')
    async def run():
        async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'),
            headers={'Authorization': 'Bearer ' + token, 'Origin': 'http://127.0.0.1:5173'},
            timeout=httpx.Timeout(900.0, connect=10.0)) as client:
            return await collect_one(client, plan, args.ordinal, args.output,
                binding={'image_id': args.image_id, 'source_commit': args.source_commit})
    result = asyncio.run(run())
    print(json.dumps({k: result[k] for k in ('ordinal', 'status', 'message_posts')}))
    return 0 if result['status'] == 'HTTP_COMPLETED_REVIEW_REQUIRED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
