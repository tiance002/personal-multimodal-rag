"""Prepare/dry-run ten existing actual Contexts; bounded normal-Registry ABC run.

No retrieval, Gold loading at execution, default role identities, or retries.
Preparation is local file work. Run requires a fresh Owner grant, an independently
created empty reported-usage LocalExecution ledger, and normal app composition.
The historical package is private and is not shipped with the public source.
Every CLI command requires an explicit --package; preparation inputs are also
caller-owned paths. Public unit tests use clearly marked synthetic fixtures.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.s8_6_3_fixed_evidence import canonical, digest, load_inputs
from backend.weknora_port.generation.prompt_candidate import candidate_profile, comparison_messages
from backend.weknora_port.models.types import CallContext, Message, TrustedScope

ACCOUNTING = 'reported_usage_only_v1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    temporary.replace(path)


def fingerprint(path):
    raw = Path(path).read_bytes()
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def checked_settlement(ledger, request):
    matches = [row for row in ledger['entries'] if row['request_id'] == request['request_id']]
    if (len(matches) != 1 or matches[0].get('status') != 'SETTLED'
            or matches[0].get('outcome') != 'RESPONSE_RECEIVED'
            or matches[0].get('wire_sha256') != digest(request['body'])):
        raise ValueError('S865_HISTORICAL_SETTLED_REQUEST_REQUIRED')
    return {key: matches[0].get(key) for key in
            ('request_id', 'status', 'outcome', 'wire_sha256', 'usage', 'provider', 'model', 'model_id')}


def prepare(package, *, export=None, snapshot=None, inventory=None, safe_config=None):
    """Copy only hash-verified actual inputs; scoring is written separately."""
    package = Path(package)
    if package.exists():
        raise ValueError('S865_PACKAGE_ALREADY_EXISTS')
    if export is None or snapshot is None:
        raise ValueError('S865_PRIVATE_PREPARATION_INPUTS_REQUIRED')
    old = read(snapshot)
    old_by_id = {row['case_id']: row for row in old['cases']}
    review_path = ROOT / 'docs/evaluation/evidence/s8_6_4/real_acceptance/http-quality-review.json'
    latest = read(review_path)['cases']
    manifest = {'schema': 's8.6.5-fixed-generation/v1', 'status': 'OFFLINE_PREPARED_NEW_OWNER_GRANT_REQUIRED',
        'new_provider_calls': 0, 'new_database_calls': 0, 'maximum_generation_calls': 30,
        'unique_questions': 10, 'accounting_mode': ACCOUNTING, 'cases': [],
        'candidate_profile': {k: v for k, v in candidate_profile().items() if k not in ('suffix', 'baseline_prompt_b_suffix')},
        'historical_sources': [fingerprint(snapshot), fingerprint(review_path)],
        'current_version_proof': 'HISTORICAL_ONLY; no current ECS/DB read, source membership/version/egress and actual role configuration must be rechecked before new execution.',
        'execution': 'A=frozen Prompt B Cheap; B=candidate Cheap; C=same candidate Expensive. Ten unique actual contexts, one generation per arm, no P5/retrieval changes.'}
    scoring = {'schema': 's8.6.5-scoring-only/v1', 'warning': 'Never loaded by load_case, dry_run or run_case; historical reviews are not new quality scores.', 'cases': []}

    def add(case_id, question, request_path, ledger, cohort, role, tags, historical_review, native=(), expected_body=None):
        raw = read(request_path)
        body = raw['body']
        if expected_body is not None and body != expected_body:
            raise ValueError('S865_ACTUAL_SOURCE_BODY_MISMATCH')
        if question not in body['messages'][-1]['content']:
            raise ValueError('S865_Q0_NOT_IN_ACTUAL_MESSAGES')
        comparison_messages(body['messages'])  # Require actual frozen Prompt B; never add it invisibly.
        settled = checked_settlement(ledger, raw)
        number = len(manifest['cases']) + 1
        relative = f'requests/{number:02}.json'
        write(package / relative, body)
        scope = raw['scope']
        contents = re.findall(r'<content>(.*?)</content>', body['messages'][-1]['content'], re.S)
        row = dict(case_id=case_id, question=question, request_file=relative, body_sha256=digest(body),
            messages_sha256=digest(body['messages']), source_messages_sha256=digest(body['messages'][1:]),
            q0_sha256=hashlib.sha256(question.encode('utf8')).hexdigest(),
            source_content_order_sha256=[hashlib.sha256(value.encode('utf8')).hexdigest() for value in contents],
            source_cohort=cohort, selection_role=role, coverage_tags=tags,
            kb_scope=list(scope['knowledge_base_ids']), document_scope=list(scope.get('document_ids', [])),
            original_scope_owner_id=scope['owner_id'], historical_request=fingerprint(request_path),
            historical_settlement=settled, historical_ids='PROVENANCE_ONLY_NEVER_REUSE',
            current_version_proof='NOT_RECHECKED_CURRENT_SOURCE; frozen original provider input, not fresh retrieval/readback acceptance')
        manifest['cases'].append(row)
        scoring['cases'].append({'case_id': case_id, 'question': question, 'expected_chunk_ids': [],
            'answer_points': list(native), 'kb_scope': list(scope['knowledge_base_ids']),
            'dataset_version': 's8.6.5-fixed-generation/v1', 'historical_review': historical_review,
            'expected_chunk_ids_status': 'NOT_DECLARED; compare cited claims against frozen actual sources and authorized readback',
            'answer_points_status': 'HISTORICAL_NATIVE_REFERENCES' if native else 'NO_NATIVE_STRING_HERE; use source-based historical review, no official accuracy claim'})

    # Three actual S8.6.4 completed HTTP turns, including extraction and multihop controls.
    for index, row in enumerate(latest, 1):
        request_path = Path(export) / f'execution-{index}' / 'evidence' / Path(row['final_model_input']['path']).name
        ledger = read(Path(export) / f'execution-{index}' / 'execution.json')
        if fingerprint(request_path)['sha256'] != row['final_model_input']['sha256']:
            raise ValueError('S865_LATEST_REQUEST_HASH_MISMATCH')
        add(row['case_id'], row['q0'], request_path, ledger, 'S8.6.4_ACTUAL_HTTP',
            'FAILURE' if index == 2 else 'CORRECT_CONTROL',
            ['conditions', 'step_order', 'unjustified_generalization', 'citation'] if index == 2 else
            ['extraction', 'control'] if index == 1 else ['multihop', 'citation', 'control'], row)

    diagnostics = ROOT / 'docs/evaluation/evidence/s8_6_4/prompt_diagnostics'
    for row in read(diagnostics / 'manifest.json')['generation']:
        old_case = old_by_id[row['case_id']]
        if old_case['execution_status'] != 'COMPLETED' or old_case['unknown_entries'] or old_case['unsettled_entries']:
            raise ValueError('S865_HISTORICAL_CASE_NOT_SETTLED')
        add(row['case_id'], old_case['question'], Path(row['historical_request_artifact']['path']),
            old_case['ledger'], 'S8.6.4_FROZEN_DIAGNOSTICS', 'FAILURE_OR_PARTIAL',
            ['units', 'numbers'] if 'multifieldqa' in row['case_id'] else ['entities', 'source_fidelity'],
            old_case['review'], old_case.get('native_references', []), expected_body=read(diagnostics / row['old']['file']))
        manifest['cases'][-1]['historical_input_sufficient'] = row['final_input_sufficient']
        manifest['cases'][-1]['historical_failure_layer'] = row['layer']

    controls = ROOT / 'docs/evaluation/evidence/s8_6_3/fixed_evidence'
    for row in read(controls / 'manifest.json')['cases']:
        if row['role'] != 'CORRECT_CONTROL':
            continue
        old_case = old_by_id[row['case_id']]
        if old_case['execution_status'] != 'COMPLETED' or old_case['unknown_entries'] or old_case['unsettled_entries']:
            raise ValueError('S865_HISTORICAL_CASE_NOT_SETTLED')
        add(row['case_id'], old_case['question'], Path(row['historical_request_artifact']['path']), old_case['ledger'],
            'ECS40_ACTUAL_CONTROL', 'CORRECT_CONTROL', ['extraction', 'numeric_comparison', 'control'],
            old_case['review'], old_case.get('native_references', []), expected_body=read(controls / row['request_file']))
        manifest['cases'][-1]['historical_citation_identity_limit'] = row['citation_identity_limit']

    if inventory is not None and Path(inventory).is_file():
        (package / 'current_model_inventory.json').write_bytes(Path(inventory).read_bytes())
        info = fingerprint(inventory)
        manifest['current_model_inventory'] = {'file': 'current_model_inventory.json',
            'sha256': info['sha256'], 'bytes': info['bytes'], 'provenance_path': info['path']}
    if safe_config is not None and Path(safe_config).is_file():
        (package / 'current_model_configuration_safe.json').write_bytes(Path(safe_config).read_bytes())
        info = fingerprint(safe_config)
        manifest['current_model_configuration'] = {'file': 'current_model_configuration_safe.json',
            'sha256': info['sha256'], 'bytes': info['bytes'], 'provenance_path': info['path']}
    write(package / 'manifest.json', manifest)
    read_manifest(package)
    write(package / 'scoring_only.json', scoring)
    if manifest.get('current_model_configuration'):
        freeze_wires(package)
    report = dry_run(package)
    write(package / 'preparation.json', report)
    return report


def read_manifest(package):
    manifest = read(Path(package) / 'manifest.json')
    cases = manifest['cases']
    if (len(cases) != 10 or len({c['case_id'] for c in cases}) != 10
            or len({c['question'] for c in cases}) != 10):
        raise ValueError('S865_TEN_UNIQUE_QUESTIONS_REQUIRED')
    if manifest.get('maximum_generation_calls') != 30 or manifest.get('accounting_mode') != ACCOUNTING:
        raise ValueError('S865_FIXED_PLAN_CHANGED')
    if manifest['candidate_profile']['suffix_sha256'] != candidate_profile()['suffix_sha256']:
        raise ValueError('S865_FROZEN_CANDIDATE_CHANGED')
    inventory = manifest.get('current_model_inventory')
    if inventory and fingerprint(Path(package) / inventory['file'])['sha256'] != inventory['sha256']:
        raise ValueError('S865_MODEL_INVENTORY_CHANGED')
    safe_config = manifest.get('current_model_configuration')
    if safe_config and fingerprint(Path(package) / safe_config['file'])['sha256'] != safe_config['sha256']:
        raise ValueError('S865_MODEL_CONFIGURATION_CHANGED')
    return manifest


def load_case(package, case_id):
    read_manifest(package)
    messages, options, row = load_inputs(package, case_id)  # Existing checked loader never reads Gold.
    raw_messages = [{'role': m.role, 'content': m.content} for m in messages]
    if (digest(raw_messages[1:]) != row['source_messages_sha256'] or row['question'] not in raw_messages[-1]['content']
            or hashlib.sha256(row['question'].encode('utf8')).hexdigest() != row['q0_sha256']):
        raise ValueError('S865_SOURCE_MESSAGES_CHANGED')
    return {arm: (options, tuple(Message(**m) for m in group))
            for arm, group in comparison_messages(raw_messages).items()}


def models_from_safe_snapshot(path):
    """Use the same SourceCatalogue resolver as Registry, without a key or send."""
    from backend.weknora_port.model_registry import SourceCatalogue
    from backend.weknora_port.storage.configuration import ConfiguredRole
    from backend.weknora_port.models.provider import ProviderModel
    source = read(path)
    models = {}
    for row in source['selected_roles']:
        if (row['selection_status'] != 'MATCHED' or row['base_url_redacted']
                or row['removed_custom_headers_or_sensitive_parameters']):
            raise ValueError('S865_EXACT_SAFE_ROLE_SNAPSHOT_REQUIRED')
        role = ConfiguredRole(row['role'], row['provider'], row['model'], row['base_url'],
                              parameters=row['parameters'], source=row['source'])
        models[row['role']] = ProviderModel(SourceCatalogue().resolve(role, row['model_id']), None)
    if set(models) != {'cheap', 'expensive'}:
        raise ValueError('S865_EXACT_SAFE_ROLE_SNAPSHOT_REQUIRED')
    return models


def freeze_wires(package):
    """Save exact pure wire renderings; no transport or model call is created."""
    package = Path(package)
    manifest = read_manifest(package)
    models = models_from_safe_snapshot(package / manifest['current_model_configuration']['file'])
    records = []
    for index, row in enumerate(manifest['cases'], 1):
        for arm, (options, messages) in load_case(package, row['case_id']).items():
            body = models['expensive' if arm == 'C' else 'cheap'].build_request_body(messages, options, stream=True)
            relative = f'wires/{index:02}-{arm}.json'
            path = package / relative
            if path.exists() and read(path) != body:
                raise ValueError('S865_EXISTING_FROZEN_WIRE_DIFFERS')
            if not path.exists():
                write(path, body)
            info = fingerprint(path)
            records.append({'case_id': row['case_id'], 'arm': arm, 'file': relative,
                            'file_sha256': info['sha256'], 'body_sha256': digest(body)})
    value = {'schema': 's8.6.5-fixed-wires/v1', 'status': 'OFFLINE_RENDERED_NO_PROVIDER',
             'dataset_manifest_sha256': fingerprint(package / 'manifest.json')['sha256'], 'wires': records}
    write(package / 'wire_manifest.json', value)
    return value


def dry_run(package, *, models=None):
    """Plan conservative byte bounds; current Registry configs are caller-owned."""
    manifest = read_manifest(package)
    if models is None and manifest.get('current_model_configuration'):
        models = models_from_safe_snapshot(Path(package) / manifest['current_model_configuration']['file'])
    reports = []
    for row in manifest['cases']:
        arms = {}
        for arm, (options, messages) in load_case(package, row['case_id']).items():
            # Same conservative text recipe used by LocalExecution, with 4096
            # extra bytes for unresolved wire/model framing. Planning only.
            size = len(canonical([{'role': m.role, 'content': m.content} for m in messages]))
            bound = size + 4096 + options.max_completion_tokens + 1024
            item = {'messages_sha256': digest([{'role': m.role, 'content': m.content} for m in messages]),
                    'output_token_limit': options.max_completion_tokens, 'planning_tokens_upper_bound': bound}
            if models is not None:
                model = models['expensive' if arm == 'C' else 'cheap']
                body = model.build_request_body(messages, options, stream=True)
                if len(canonical(body)) + options.max_completion_tokens + 1024 > bound:
                    raise ValueError('S865_RESOLVED_WIRE_EXCEEDS_PLANNING_BOUND')
                item.update(body_sha256=digest(body), model_id=model.config.model_id,
                            provider=model.config.provider, model=model.config.model)
            arms[arm] = item
        reports.append({'case_id': row['case_id'], 'arms': arms})
    return {'schema': 's8.6.5-generation-dry-run/v1', 'status': 'OFFLINE_PREPARED_NEW_GRANT_REQUIRED',
        'cases': 10, 'arms': 30, 'provider_calls': 0, 'database_calls': 0, 'accounting_mode': ACCOUNTING,
        'planning_tokens_upper_bound': sum(a['planning_tokens_upper_bound'] for c in reports for a in c['arms'].values()),
        'bound_recipe': 'UTF8 canonical message bytes +4096 wire/framing allowance +configured 2048 output +1024 safety; conservative planning envelope, not tokenizer measurement/reservation/actual usage.',
        'actual_current_role_snapshot': ('SOURCE_READ_CURRENT_SNAPSHOT_EXECUTION_RECHECK_REQUIRED'
            if manifest.get('current_model_inventory') else 'NOT_VERIFIED_CURRENT_ECS_READ_REQUIRED'),
        'current_source_preflight': 'BLOCKED_CURRENT_SOURCE_PREFLIGHT',
        'real_provider_execution': 'NOT_RUN',
        'render_status': 'EXPLICIT_CALLER_CONFIGS_RENDERED' if models else 'MESSAGES_VERIFIED; WIRE_RENDER_REQUIRES_CURRENT_REGISTRY_CONFIGS',
        'manifest_sha256': fingerprint(Path(package) / 'manifest.json')['sha256'], 'rows': reports}


def validate_owner_grant(grant, package, case_id):
    now = time.time()
    if (grant.get('owner_approved') is not True or grant.get('status') != 'OPEN'
            or grant.get('scope') != 'S8_6_5_FIXED_GENERATION'
            or grant.get('dataset_manifest_sha256') != fingerprint(Path(package) / 'manifest.json')['sha256']
            or case_id not in grant.get('allowed_case_ids', [])
            or grant.get('accounting_mode') != ACCOUNTING
            or type(grant.get('max_calls')) is not int or not 1 <= grant['max_calls'] <= 30
            or type(grant.get('max_tokens')) is not int or grant['max_tokens'] <= 0
            or grant.get('reported_total_tokens', 0) >= grant['max_tokens']
            or grant.get('script_sha256') != fingerprint(Path(__file__))['sha256']
            or grant.get('candidate_suffix_sha256') != candidate_profile()['suffix_sha256']
            or grant.get('rendered_wire_manifest_sha256') != fingerprint(Path(package) / 'wire_manifest.json')['sha256']
            or not all(type(grant.get(name)) in (int, float) for name in
                       ('expires_at_unix', 'batch_started_at_unix', 'batch_deadline_unix'))
            or not grant['batch_started_at_unix'] <= now < grant['batch_deadline_unix'] <= grant['expires_at_unix']
            or grant['batch_deadline_unix'] - grant['batch_started_at_unix'] > 2400
            or grant.get('allow_current_frozen_source_egress') is not True):
        raise ValueError('S865_FRESH_OWNER_GRANT_REQUIRED')
    validate_source_proof(grant, package, case_id, now=now)


def validate_source_proof(grant, package, case_id, *, now=None):
    """Require Owner-bound current readback proof; this runner performs no retrieval."""
    now = time.time() if now is None else now
    try:
        path = Path(grant['source_proof_manifest_path'])
        proof = read(path)
        identity = proof['source_identity']
        if (digest(identity) != grant['source_identity_sha256']
                or identity['dataset_manifest_sha256'] != fingerprint(Path(package) / 'manifest.json')['sha256']):
            raise ValueError()
        row = next(c for c in read_manifest(package)['cases'] if c['case_id'] == case_id)
        current = next(c for c in proof['cases'] if c['case_id'] == case_id)
        source = next(c for c in identity['cases'] if c['case_id'] == case_id)
        stable = {key: current[key] for key in ('case_id', 'q0_sha256', 'source_messages_sha256',
                  'knowledge_base_ids', 'document_ids', 'chunk_version_readbacks')}
        if (proof['status'] != 'PASS_CURRENT_AUTHORIZED_READBACK'
                or proof['dataset_manifest_sha256'] != fingerprint(Path(package) / 'manifest.json')['sha256']
                or not grant['batch_started_at_unix'] <= proof['checked_at_unix'] <= now
                or stable != source
                or current['q0_sha256'] != row['q0_sha256']
                or current['source_messages_sha256'] != row['source_messages_sha256']
                or current['knowledge_base_ids'] != row['kb_scope']
                or current['document_ids'] != row['document_scope']
                or any(current.get(key) != 'PASS' for key in ('source_version_membership', 'permissions',
                          'cloud_consent', 'plaintext_matches_frozen'))
                or not current.get('chunk_version_readbacks')):
            raise ValueError()
        return {'source_identity_sha256': digest(identity), 'proof': fingerprint(path),
                'checked_at_unix': proof['checked_at_unix']}
    except (OSError, KeyError, TypeError, ValueError, StopIteration):
        raise ValueError('S865_BLOCKED_CURRENT_SOURCE_PREFLIGHT') from None


def claim_case(owner_grant, package, case_id, output):
    """Single atomic parent claim; a claimed question is never retried."""
    owner_grant = Path(owner_grant)
    lock = owner_grant.with_suffix(owner_grant.suffix + '.claim.lock')
    # Existing lock is a hard stop, including after a process crash. No recovery
    # automatically removes another operator's claim or authorizes a resend.
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode('ascii'))
        grant = read(owner_grant)
        validate_owner_grant(grant, package, case_id)
        claims = grant.setdefault('case_claims', {})
        if (case_id in claims or any(c['status'] == 'CLAIMED_NO_RETRY' for c in claims.values())
                or 3 * (len(claims) + 1) > grant['max_calls']):
            raise ValueError('S865_CASE_ALREADY_CLAIMED_OR_CALL_CAP')
        claims[case_id] = {'status': 'CLAIMED_NO_RETRY', 'output': str(output), 'calls_claimed': 3}
        write(owner_grant, grant)
    finally:
        os.close(fd)
        lock.unlink()


def case_request_prefix(grant, case_id):
    return grant['request_prefix'] + '/' + hashlib.sha256(case_id.encode('utf8')).hexdigest()[:16]


def usage_facts(entries):
    """Preserve known usage even on UNKNOWN; absent counters remain unknown."""
    known = [row['usage']['total_tokens'] for row in entries if isinstance(row.get('usage'), dict)
             and row['usage'].get('status') == 'REPORTED' and type(row['usage'].get('total_tokens')) is int]
    missing = len(entries) - len(known)
    return {'reported_total_tokens': sum(known), 'usage_missing_entries': missing,
            'actual_total_tokens': None if missing else sum(known),
            'unknown_entries': sum(row.get('status') == 'UNKNOWN' for row in entries),
            'unsettled_entries': sum(row.get('status') != 'SETTLED' for row in entries),
            'attempts': len(entries)}


async def run_case(package, case_id, *, app, owner_grant, output):
    """Compose normal Registry/SendGate/LocalExecution; one serial ABC, no retry.

    The caller assembles app with its existing execution_file and trusted
    principal. This function creates neither an authorization nor a ledger.
    """
    from backend.weknora_port.execution import LocalExecution
    grant = read(owner_grant)
    validate_owner_grant(grant, package, case_id)
    manifest = read_manifest(package)
    row = next(c for c in manifest['cases'] if c['case_id'] == case_id)
    execution = getattr(app, 'execution', None)
    if not isinstance(execution, LocalExecution) or execution.accounting_mode != ACCOUNTING:
        raise ValueError('S865_NORMAL_REPORTED_USAGE_EXECUTION_REQUIRED')
    if (app.principal.tenant_id, app.principal.subject_id) != (grant['tenant_id'], grant['subject_id']):
        raise ValueError('S865_TRUSTED_PRINCIPAL_MISMATCH')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)  # Permanent per-case claim prevents resend.
    claim_case(owner_grant, package, case_id, output)
    prefix = 's865-generation-' + uuid.uuid4().hex
    state = {'case_id': case_id, 'status': 'CLAIMED', 'request_prefix': prefix, 'arms': {},
             'owner_grant': fingerprint(owner_grant), 'manifest': fingerprint(Path(package) / 'manifest.json')}
    write(output / 'state.json', state)
    context = CallContext(prefix, TrustedScope(app.principal.subject_id, tuple(row['kb_scope']), tuple(row['document_scope'])),
                          required_origin_kbs=tuple(row['kb_scope']))
    registry = app.registry_for_context()  # Root tracing hooks the normal gate here.
    try:
        async with app.leases.lease() as guard:
            snapshot = await app._snapshot(guard)
        models = {role: await registry.chat(snapshot.model_id(role), context) for role in ('cheap', 'expensive')}
        # Grant binds current resolved role IDs; historical model names never select execution identity.
        bindings = {role: {k: getattr(model.config, k) for k in ('model_id', 'provider', 'model')}
                    for role, model in models.items()}
        if bindings != grant.get('resolved_role_bindings'):
            raise ValueError('S865_CURRENT_ROLE_BINDINGS_NOT_APPROVED')
        if manifest.get('current_model_inventory'):
            inventory = read(Path(package) / manifest['current_model_inventory']['file'])
            expected = {role['role']: {key: role[key] for key in ('model_id', 'provider', 'model')}
                        for role in inventory['configured_roles']}
            if bindings != expected:
                raise ValueError('S865_CURRENT_ROLES_DIFFER_FROM_FROZEN_INVENTORY')
        if manifest.get('current_model_configuration'):
            expected_models = models_from_safe_snapshot(Path(package) / manifest['current_model_configuration']['file'])
            if any(models[role].config != expected_models[role].config for role in models):
                raise ValueError('S865_CURRENT_CONFIG_DIFFERS_FROM_FROZEN_SAFE_SNAPSHOT')
        with execution._ledger() as ledger:
            if (ledger['status'] != 'OPEN' or ledger['entries'] or ledger['max_calls'] != 3
                    or ledger['tenant_id'] != app.principal.tenant_id or ledger['subject_id'] != app.principal.subject_id
                    or set(ledger['allowed_kbs']) != set(row['kb_scope'])
                    or ledger['allowed_request_prefixes'] != [case_request_prefix(grant, case_id)]):
                raise ValueError('S865_FRESH_EMPTY_CASE_LEDGER_REQUIRED')
        prefix = case_request_prefix(grant, case_id)
        state['request_prefix'] = prefix
        state['resolved_role_bindings'] = bindings
        for arm, (options, messages) in load_case(package, case_id).items():
            validate_owner_grant(read(owner_grant), package, case_id)
            model = models['expensive' if arm == 'C' else 'cheap']
            request_id = prefix + '/' + arm
            expected_wire = next(w for w in read(Path(package) / 'wire_manifest.json')['wires']
                                 if w['case_id'] == case_id and w['arm'] == arm)
            actual_wire_sha256 = digest(model.build_request_body(messages, options, stream=True))
            if actual_wire_sha256 != expected_wire['body_sha256']:
                raise ValueError('S865_CURRENT_RENDER_DIFFERS_FROM_FROZEN_WIRE')
            state['status'] = 'BEFORE_PROVIDER'
            state['arms'][arm] = {'status': 'CLAIMED_NO_RETRY', 'request_id': request_id,
                'wire_sha256': actual_wire_sha256}
            state['source_preflight'] = validate_source_proof(read(owner_grant), package, case_id)
            write(output / 'state.json', state)  # Durable status precedes every possible send.
            events = []
            ctx = CallContext(request_id, context.scope, required_origin_kbs=context.required_origin_kbs,
                deadline=asyncio.get_running_loop().time() + max(0, grant['batch_deadline_unix'] - time.time()),
                deadline_kind='run')
            started = time.monotonic()
            async for event in model.chat_stream(messages, options, ctx):
                events.append(asdict(event))
                write(output / f'{arm}.events.json', events)
            with execution._ledger() as ledger:
                matches = [e for e in ledger['entries'] if e['request_id'] == request_id]
                state['accounting'] = usage_facts(ledger['entries'])
                if len(matches) == 1:
                    state['arms'][arm]['ledger_status'] = matches[0]['status']
                    state['arms'][arm]['usage'] = matches[0].get('usage')
                if (len(matches) != 1 or matches[0]['status'] != 'SETTLED'
                        or matches[0].get('outcome') != 'RESPONSE_RECEIVED'
                        or not any(e.get('is_final') for e in events)
                        or any(e.get('response_type') == 'error' for e in events)
                        or any(e.get('finish_reason') in ('length', 'max_tokens', 'max_output_tokens') for e in events)):
                    raise RuntimeError('S865_UNKNOWN_INCOMPLETE_OR_TRUNCATED_STOP_NO_RETRY')
            state['arms'][arm].update(status='SETTLED', seconds=time.monotonic() - started,
                                     usage=matches[0]['usage'])
            write(output / 'state.json', state)
            latest_grant = read(owner_grant)
            latest_grant['reported_total_tokens'] = grant.get('reported_total_tokens', 0) + state['accounting']['reported_total_tokens']
            if latest_grant['reported_total_tokens'] >= latest_grant['max_tokens']:
                latest_grant['status'] = 'STOP'
            write(owner_grant, latest_grant)
            if latest_grant['status'] != 'OPEN':
                raise RuntimeError('S865_REPORTED_TOKEN_CAP_STOP_NO_RETRY')
        state['status'] = 'COMPLETE'
    except BaseException as error:
        state['status'] = 'STOP_NO_RETRY'
        state['error_class'] = type(error).__name__
        raise
    finally:
        # Close this new case ledger only. Any failed/incomplete run stops the
        # parent as well; no UNKNOWN record is released or replayed.
        with execution._ledger() as ledger:
            state['accounting'] = usage_facts(ledger['entries'])
            state['ledger_entries'] = ledger['entries']
            ledger['status'] = 'CLOSED' if state['status'] == 'COMPLETE' else 'STOP'
        latest_grant = read(owner_grant)
        latest_grant['case_claims'][case_id]['status'] = state['status']
        latest_grant['reported_total_tokens'] = grant.get('reported_total_tokens', 0) + state['accounting']['reported_total_tokens']
        latest_grant['usage_missing_entries'] = grant.get('usage_missing_entries', 0) + state['accounting']['usage_missing_entries']
        latest_grant['unknown_entries'] = grant.get('unknown_entries', 0) + state['accounting']['unknown_entries']
        latest_grant['unsettled_entries'] = grant.get('unsettled_entries', 0) + state['accounting']['unsettled_entries']
        latest_grant['actual_total_tokens'] = None if latest_grant['usage_missing_entries'] else latest_grant['reported_total_tokens']
        if state['status'] != 'COMPLETE' or latest_grant['reported_total_tokens'] >= latest_grant['max_tokens']:
            latest_grant['status'] = 'STOP'
        write(owner_grant, latest_grant)
        write(output / 'state.json', state)
        await registry.close()
    return state


async def run_from_config(args):
    """Existing development composition only; never a standalone send client."""
    grant = read(args.owner_grant)
    validate_owner_grant(grant, args.package, args.case)
    from scripts.local_runtime import load_config, verify_resources, environment
    from backend.weknora_port.development import DevelopmentSettings, assemble
    from backend.weknora_port.storage.history import LocalPrincipal
    # Preserve the exact approved observability environment through the normal
    # settings parser; never infer enablement or copy unrelated process secrets.
    snapshot_path = grant.get('observability_env_snapshot_path')
    if not snapshot_path or fingerprint(snapshot_path)['sha256'] != grant.get('observability_env_snapshot_sha256'):
        raise ValueError('S865_OBSERVABILITY_SNAPSHOT_NOT_APPROVED')
    approved_env = read(snapshot_path)
    if not isinstance(approved_env, dict) or any(not isinstance(k, str) or not isinstance(v, str)
            or not (k.startswith('RAG_LANGFUSE_') or k.startswith('LANGFUSE_')) for k, v in approved_env.items()):
        raise ValueError('S865_OBSERVABILITY_SNAPSHOT_INVALID')
    config = load_config(args.config)
    for key in list(config['environment']):
        if key.startswith(('RAG_LANGFUSE_', 'LANGFUSE_')):
            config['environment'].pop(key)
    config['environment'].update(approved_env)
    identity = await verify_resources(config)
    env = environment(config, identity['redis_run_id'])
    for key in list(env):
        if key.startswith(('RAG_LANGFUSE_', 'LANGFUSE_')):
            env.pop(key)
    env.update(approved_env)
    env.update(WEKNORA_PORT_EXECUTION_FILE=str(args.execution_file.resolve()), WEKNORA_PORT_CLOUD_ALLOWED='true')
    principal = LocalPrincipal(grant['tenant_id'], grant['subject_id'])
    async with assemble(DevelopmentSettings.from_environment(env), principal=principal) as app:
        return await run_case(args.package, args.case, app=app, owner_grant=args.owner_grant, output=args.output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'dry-run', 'run'))
    parser.add_argument('--package', type=Path, required=True, help='explicit private frozen package (not shipped)')
    parser.add_argument('--source-export', type=Path, help='private historical export required for prepare')
    parser.add_argument('--ecs40-snapshot', type=Path, help='private historical snapshot required for prepare')
    parser.add_argument('--model-inventory', type=Path, help='optional explicit current private model inventory')
    parser.add_argument('--model-config-snapshot', type=Path, help='optional explicit safe role configuration')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--owner-grant', type=Path)
    parser.add_argument('--execution-file', type=Path)
    parser.add_argument('--case')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare' and (args.source_export is None or args.ecs40_snapshot is None):
        parser.error('prepare requires --source-export --ecs40-snapshot from the private archive')
    if args.command == 'run':
        if any(getattr(args, name) is None for name in ('config', 'owner_grant', 'execution_file', 'case', 'output')):
            parser.error('run requires --config --owner-grant --execution-file --case --output')
        result = asyncio.run(run_from_config(args), loop_factory=asyncio.SelectorEventLoop)
    else:
        result = (prepare(args.package, export=args.source_export, snapshot=args.ecs40_snapshot,
                          inventory=args.model_inventory, safe_config=args.model_config_snapshot)
                  if args.command == 'prepare' else dry_run(args.package))
    if args.report:
        write(args.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
