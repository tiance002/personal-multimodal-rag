"""Final backend meeting: native provider/QuickChain, SIMULATED senders/evidence/DB."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid5, NAMESPACE_URL
import pytest

from backend.app.application.rule_router_v1 import GenerationRole
from backend.app.adapters.models.cloud import V6XingPlannedCall, V6XingRequestWindow
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.session_attempts import request_identity
from backend.app.config import Settings
from backend.app.domain.scope import Scope
from backend.app.ports.providers import ProviderRequestNotSent
from backend.tests.test_v6_xing_generation_binding import counter, no_live_sender, setup as xing_setup
from backend.tests.test_v6_generation_binding import setup_binding as deepseek_setup, envelope
from eval_center.paired_simulation import build_simulated_container
from eval_center.paired_state import (ARMS, DATASETS, CurrentProviderSendClaims, SQLiteProviderSendClaims,
    provider_send_claim_scope, register_run, begin_attempt, provider_send_claim_record, PairedContractError)
from eval_center.public_benchmark_cli import _parser, _run, main
from eval_center.public_runner import (PairedRuntime, PairedDatasetComposition, PairedRuntimeComposition,
    _memory_free_container, _paired_runtime_admission, build_normal_paired_runtime, PairedRuntimeAssemblyBlocked)
from eval_center.public_runner import PairedProviderCallBudget
from eval_center.paired_state import load_frozen_cohort, sha256_file

SOURCE = Path('var/reports/v6-first-evaluation-20261010/source_manifest.json')
COHORT = Path('var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl')


@pytest.fixture(autouse=True)
def no_real_transport(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-ONLY')
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener',
        lambda *a, **k: pytest.fail('REAL DeepSeek forbidden in offline meeting'))


def native_fixture(root, counter):
    containers, scopes, receipts = {}, {}, {}
    shared_gate = None
    for name in DATASETS:
        scope = Scope.from_ids(['SIMULATED-' + name], ['SIMULATED-doc-' + name])
        container = build_simulated_container(ledger_dir=root / name / 'fixture-only-legacy-ledger')
        original_context = container.execution_context
        budget_links = []
        @contextmanager
        def execution_context(run_id, original=original_context, links=budget_links):
            from backend.app.ports.run_lifecycle import current_execution
            with original(run_id):
                # SIMULATED lifecycle repository; normal monthly guard is real code.
                current_execution.get()[1].repository.link_budget = lambda *args: links.append(args)
                yield
        container.execution_context = execution_context
        cheap, xing_transport, budget, sf_wires, _ = xing_setup(counter, scope=scope,
            purposes=('answer', 'understand', 'rewrite', 'expansion'))
        xing_transport.send_claim_callback = CurrentProviderSendClaims()
        expensive, gate, ledger, ds_counter, monthly, ds_wires = deepseek_setup(root / name)
        if shared_gate is None:
            shared_gate = gate
        else:
            # One SIMULATED activity/1M gate across all datasets, not three allowances.
            gateway = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('chat_expensive',)).build_v6_gateway(
                activity_gate=shared_gate, gate_types=expensive.gateway.gate_types,
                api_key='SIMULATED-ONLY', transport=expensive.gateway._simulated_transport)
            expensive = replace(expensive, gateway=gateway, v6_binding=replace(expensive.v6_binding,
                grant=shared_gate.grant, activity_gate=shared_gate))
        expensive.gateway.send_claim_callback = CurrentProviderSendClaims()
        expensive = replace(expensive, v6_binding=replace(expensive.v6_binding,
            server_scope=scope, send_claim_callback=expensive.gateway.send_claim_callback))
        container.quick_chain.generation_roles = {'chat_cheap': cheap, 'chat_expensive': expensive}
        containers[name], scopes[name] = container, scope
        receipts[name] = dict(sf_wires=sf_wires, ds_wires=ds_wires, budget=budget,
            ds_counter=ds_counter, monthly=monthly, xing_transport=xing_transport, budget_links=budget_links)
    def observations():
        return {'execution_kind': 'SIMULATED', 'real_provider_calls': 0,
            'cheap_fixture_calls': sum(len(item['sf_wires']) for item in receipts.values()),
            'deepseek_fixture_calls': sum(len(item['ds_wires']) for item in receipts.values())}
    runtime = PairedRuntime(containers, scopes,
        {kb: True for scope in scopes.values() for kb in scope.knowledge_base_ids},
        lambda: None, 'SIMULATED', database_queries=lambda: 0, provider_observations=observations)
    return runtime, receipts


def _answer_request(run, qid, arm, role):
    return request_identity(str(uuid5(NAMESPACE_URL, f'{run}:{qid}:{arm}')), 'quick.router.' + role, 1)


def planned_native_fixture(root, counter, run):
    """SIMULATED zero-send capture of real offline counter bounds, then freeze."""
    rows = load_frozen_cohort(COHORT)
    capture_run = run + '-ZERO-SEND-PLAN-CAPTURE'
    capture_runtime, _ = native_fixture(root / 'zero-send-plan', counter)
    identities = {_answer_request(capture_run, row['qid'], arm, role): (row['qid'], arm, role)
        for row in rows for arm in ARMS for role in ('chat_cheap', 'chat_expensive')}
    measured, prompts = {}, {}
    original = GenerationRole.invoke
    def capture_role(self, env, **kwargs):
        key = identities[kwargs['request_id']]
        prompts[(key[0], key[1])] = hashlib.sha256(env.prompt.encode()).hexdigest()
        if self.role == 'chat_expensive':
            self.check()
            binding = self.v6_binding
            prepared = self.gateway.prepare_v6_request(env.prompt, env.timeout_seconds, env.max_tokens,
                grant=binding.grant, scope=binding.grant.scope, capacity_counter=binding.capacity_counter)
            measured[key] = dict(input_token_bound=prepared.budget.input_token_bound,
                output_token_bound=prepared.budget.output_token_bound, safety_tokens=prepared.budget.safety_tokens,
                wire_sha256=prepared.budget.wire_request_sha256)
            raise ProviderRequestNotSent('SIMULATED_PLAN_CAPTURE_ZERO_DEEPSEEK_SEND')
        return original(self, env, **kwargs)
    for container in capture_runtime.containers.values():
        gateway = container.quick_chain.generation_roles['chat_cheap'].gateway
        def capture_sf(path, payload, timeout, validate, planned_tokens=None, *, request_policy=None, **kwargs):
            estimate = request_policy.estimate
            estimate.assert_wire_bytes(json.dumps(payload).encode())
            key = identities[request_policy.transport._context.get()[3]]
            measured[key] = dict(input_token_bound=estimate.reservation_bound,
                output_token_bound=estimate.output_reservation, wire_sha256=estimate.wire_request_sha256)
            raise ProviderRequestNotSent('SIMULATED_PLAN_CAPTURE_ZERO_SF_SEND')
        gateway._request = capture_sf
    with patch.object(GenerationRole, 'invoke', capture_role):
        capture_report = _run(_parser().parse_args(['--phase', 'paired-run', '--run-id', capture_run,
            '--state-path', str(root/'zero-send-plan/center.sqlite3')]), runtime=capture_runtime)
    assert capture_runtime.provider_observations()['cheap_fixture_calls'] == 0
    assert capture_runtime.provider_observations()['deepseek_fixture_calls'] == 0
    for row in rows:
        assert len({prompts[(row['qid'], arm)] for arm in ARMS}) == 1
    runtime, receipts = native_fixture(root, counter)
    plans, ds_plans = {}, {}
    for name, container in runtime.containers.items():
        role = container.quick_chain.generation_roles['chat_cheap']
        assert role.gateway.requests == role.gateway.planned_tokens == 0
        calls, ds_calls = [], []
        for row in rows:
            if row['dataset'] != name:
                continue
            qid = row['qid']
            # Envelope base bytes are verified equal across arms. Plan both
            # possible Dynamic providers; its actual decision is not assumed.
            for arm in ('CHEAP_ONLY', 'DYNAMIC'):
                bound = measured.get((qid, arm, 'chat_cheap'), measured[(qid, 'CHEAP_ONLY', 'chat_cheap')])
                calls.append(V6XingPlannedCall(_answer_request(run,qid,arm,'chat_cheap'), qid,arm,'answer',
                    bound['input_token_bound'],bound['output_token_bound']))
            for arm in ('EXPENSIVE_ONLY', 'DYNAMIC'):
                bound = measured.get((qid, arm, 'chat_expensive'), measured[(qid, 'EXPENSIVE_ONLY', 'chat_expensive')])
                ds_calls.append(PairedProviderCallBudget(_answer_request(run,qid,arm,'chat_expensive'),qid,arm,'answer',
                    bound['input_token_bound'],bound['output_token_bound'],bound['safety_tokens']))
        plan = V6XingRequestWindow(run, role.xing_binding.grant.grant_id, runtime.scopes[name],
            '2026-10-10T04:25:44Z', sha256_file(COHORT), sha256_file(SOURCE), tuple(calls), '0'*64)
        plan = replace(plan, frozen_plan_sha256=plan.fingerprint)
        binding = replace(role.xing_binding, request_window=plan)
        gateway = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('chat_cheap',),
            usage_guards={'chat_cheap': role.gateway.usage_guard}).build('chat_cheap', xing_binding=binding)
        container.quick_chain.generation_roles['chat_cheap'] = replace(role, gateway=gateway, xing_binding=binding)
        container._deepseek_planned_calls = tuple(ds_calls)
        plans[name], ds_plans[name] = plan, tuple(ds_calls)
    record = {'execution': 'SIMULATED_ZERO_PROVIDER_SEND_PLAN_CAPTURE_REAL_OFFLINE_SF_COUNTER',
        'capture_report': capture_report, 'capture_measurements': {':'.join(k):v for k,v in measured.items()},
        'sf_windows': {name: dict(fingerprint=p.fingerprint, planned_token_cap=p.planned_token_cap,
            calls=[vars(c) for c in p.calls], helper_counts={purpose:sum(c.purpose==purpose for c in p.calls)
                for purpose in ('understand','rewrite','expansion')}) for name,p in plans.items()},
        'deepseek_calls': {name:[vars(c) for c in calls] for name,calls in ds_plans.items()},
        'deepseek_worst_case_calls': sum(len(calls) for calls in ds_plans.values()),
        'deepseek_worst_case_tokens': sum(c.input_token_bound+c.output_token_bound+c.safety_tokens
            for calls in ds_plans.values() for c in calls),
        'baseline_used_tokens': next(iter(runtime.containers.values())).quick_chain.generation_roles[
            'chat_expensive'].v6_binding.grant.baseline_used_tokens,
        'deepseek_limit': 1_000_000}
    (root/'frozen-window-plan.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    return runtime, receipts


def composition_for(runtime):
    settings = Settings(cloud_enabled=True, chat_egress_enabled=True, rule_router_enabled=True,
        rule_router_quality_escalation=False, rule_router_abstention_escalation=False,
        rule_router_provider_fallback=False, local_query_enabled=False, local_answer_enabled=False)
    items = {}
    for name, container in runtime.containers.items():
        roles = container.quick_chain.generation_roles
        items[name] = PairedDatasetComposition(settings, runtime.scopes[name],
            roles['chat_expensive'].v6_binding, roles['chat_cheap'].xing_binding,
            lambda c, run_id, scope: c._fixture_execution_context(run_id),
            {'chat_cheap': roles['chat_cheap'].gateway.usage_guard,
             'chat_expensive': roles['chat_expensive'].usage_guard}, embedding_provider=None,
            deepseek_calls=getattr(container,'_deepseek_planned_calls',()))
    return PairedRuntimeComposition(items, runtime.cloud_allowed_by_kb, runtime.dispatch_guard,
        execution_kind='SIMULATED', database_queries=runtime.database_queries,
        provider_observations=runtime.provider_observations)


@pytest.fixture(scope='module')
def native_completed(counter, tmp_path_factory):
    root = tmp_path_factory.mktemp('native-final-cohort')
    # Module fixtures execute before function-scoped autouse fixtures. Install
    # the existing explicit SIMULATED credential before zero-send plan capture.
    os.environ['SILICONFLOW_API_KEY'] = 'SIMULATED-ONLY'
    runtime, receipts = planned_native_fixture(root, counter, 'SIMULATED-native-meet')
    state = root / 'center.sqlite3'
    args = _parser().parse_args(['--phase', 'paired-run', '--run-id', 'SIMULATED-native-meet',
                                '--state-path', str(state)])
    report = _run(args, runtime=runtime)
    (root / 'observed-report.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    return root, state, args, runtime, receipts, report


def test_native_full_three_arm_cohort_has_one_claim_per_actual_send(native_completed):
    root, state, args, runtime, receipts, report = native_completed
    assert report['status'] == 'SIMULATED_PAIRED_COHORT_COMPLETED'
    assert report['qid_count'] == 24 and report['quickchain_invocations'] == 72
    assert report['provider_claim_counts'] == {'answer/simulated': 72}
    assert report['provider_calls'] == report['database_queries'] == 0
    observed = report['current_provider_observations']
    assert observed['execution_kind'] == 'SIMULATED' and observed['real_provider_calls'] == 0
    assert observed['cheap_fixture_calls'] + observed['deepseek_fixture_calls'] == 72
    with sqlite3.connect(state) as db:
        children = [json.loads(row[0]) for row in db.execute('SELECT result_json FROM paired_provider_calls')]
        counts = dict(db.execute('SELECT provider,COUNT(*) FROM paired_provider_calls GROUP BY provider'))
    # Native receipts bind actual final wire/budget; outer fallback receipt is empty.
    assert len(children) == 72 and all(child for child in children)
    assert counts == {'siliconflow':observed['cheap_fixture_calls'], 'deepseek':observed['deepseek_fixture_calls']}
    for item in receipts.values():
        assert len(item['sf_wires']) == len(item['budget'].reservations) == len(item['budget_links'])
        assert len(item['ds_wires']) == len(item['monthly'].reservations) == len(item['monthly'].settlements)
        assert item['ds_wires'] == item['ds_counter'].wires
    assert report['answer_gold_denominator'] == 8


def test_native_same_id_resume_never_reclaims_or_resends(native_completed):
    root, state, args, runtime, receipts, first = native_completed
    before = runtime.provider_observations()
    resumed = _run(args, runtime=runtime)
    assert resumed['results_sha256'] == first['results_sha256']
    assert resumed['new_quickchain_invocations'] == 0 and resumed['reused_qids'] == 24
    assert runtime.provider_observations() == before
    (root / 'observed-resume.json').write_text(json.dumps(resumed, ensure_ascii=False), encoding='utf-8')


@pytest.mark.parametrize('role_name', ['chat_cheap', 'chat_expensive'])
@pytest.mark.parametrize('scope_kind', ['other_kb', 'wider_docs'])
def test_wrapper_rejects_wrong_or_broader_scope_without_a_native_send(tmp_path, counter, role_name, scope_kind):
    runtime, receipts = native_fixture(tmp_path, counter)
    container = _memory_free_container(runtime.containers['scifact'])
    exact = runtime.scopes['scifact']
    scope = Scope.from_ids(['other']) if scope_kind == 'other_kb' else Scope.from_ids(exact.knowledge_base_ids)
    with pytest.raises(ProviderRequestNotSent, match='PAIRED_BOUND_SERVER_SCOPE_MISMATCH'):
        container.quick_chain.generation_roles[role_name].invoke(envelope(),
            request_id='SIMULATED-scope-denied', run_id='SIMULATED', scope=scope)
    assert not receipts['scifact']['sf_wires'] and not receipts['scifact']['ds_wires']


def test_wrapper_passes_exact_deepseek_server_scope_instead_of_grant_string(tmp_path, counter):
    runtime, receipts = native_fixture(tmp_path, counter)
    role = _memory_free_container(runtime.containers['scifact']).quick_chain.generation_roles['chat_expensive']
    state = tmp_path / 'scope-center.sqlite3'
    register_run(state, run_id='scope', cohort_sha256='a'*64, source_manifest_sha256='b'*64,
        code_sha='c'*64, deadline_utc='2026-10-10T04:25:44Z', expected_qids=['q'])
    begin_attempt(state, run_id='scope', qid='q', arm='EXPENSIVE_ONLY', input_identity_sha256='d'*64)
    bound = SQLiteProviderSendClaims(state, 'scope', 'q', 'EXPENSIVE_ONLY', 'd'*64)
    with provider_send_claim_scope(bound):
        answer = role.invoke(envelope(), request_id='SIMULATED-exact-scope', run_id='SIMULATED',
                             scope=runtime.scopes['scifact'])
    assert answer == 'SIMULATED answer' and len(receipts['scifact']['ds_wires']) == 1
    child = provider_send_claim_record(state, request_id='SIMULATED-exact-scope')
    assert child['status'] == 'simulated'


@pytest.mark.parametrize('purpose', ['understand', 'rewrite', 'expansion'])
def test_native_helpers_keep_separate_slots_without_repeat(tmp_path, counter, purpose):
    runtime, receipts = native_fixture(tmp_path, counter)
    state = tmp_path / 'helper-center.sqlite3'
    register_run(state, run_id='helper', cohort_sha256='a'*64, source_manifest_sha256='b'*64,
        code_sha='c'*64, deadline_utc='2026-10-10T04:25:44Z', expected_qids=['q'])
    begin_attempt(state, run_id='helper', qid='q', arm='DYNAMIC', input_identity_sha256='d'*64)
    bound = SQLiteProviderSendClaims(state, 'helper', 'q', 'DYNAMIC', 'd'*64)
    role = runtime.containers['scifact'].quick_chain.generation_roles['chat_cheap']
    request_id = bound.request_id(purpose=purpose)
    with provider_send_claim_scope(bound):
        role.invoke_auxiliary([{'role': 'user', 'content': 'SIMULATED public query'}], purpose=purpose,
            request_id=request_id, run_id='helper', scope=runtime.scopes['scifact'], timeout_seconds=10, max_tokens=256)
        with pytest.raises(ProviderRequestNotSent):
            role.invoke_auxiliary([{'role': 'user', 'content': 'SIMULATED public query'}], purpose=purpose,
                request_id=request_id, run_id='helper', scope=runtime.scopes['scifact'], timeout_seconds=10, max_tokens=256)
    assert len(receipts['scifact']['sf_wires']) == 1
    child = provider_send_claim_record(state, request_id=request_id)
    assert child['purpose'] == purpose and child['status'] == 'simulated'


def test_normal_cli_composition_builds_backend_then_shared_cohort(tmp_path, counter, monkeypatch, capsys):
    runtime, receipts = planned_native_fixture(tmp_path, counter, 'SIMULATED-normal-composition')
    composition = composition_for(runtime)
    calls = []
    def build(settings, **kwargs):
        scope = kwargs['v6_generation_binding'].server_scope
        name = next(name for name in DATASETS if runtime.scopes[name] == scope)
        assert kwargs['v6_xing_binding'].grant.scope == scope
        assert type(kwargs['v6_generation_binding'].send_claim_callback) is CurrentProviderSendClaims
        assert type(kwargs['v6_xing_binding'].request_window) is V6XingRequestWindow
        container = runtime.containers[name]
        container._fixture_execution_context = container.execution_context
        calls.append(name)
        cheap = container.quick_chain.generation_roles['chat_cheap']
        gateway = ProviderFactory(ModelRegistry.frozen_defaults(),enabled_roles=('chat_cheap',),
            usage_guards=kwargs['model_usage_guards']).build('chat_cheap',xing_binding=kwargs['v6_xing_binding'])
        container.quick_chain.generation_roles['chat_cheap'] = replace(cheap,gateway=gateway,
            xing_binding=kwargs['v6_xing_binding'])
        return container
    monkeypatch.setattr('eval_center.public_runner.build_container', build)
    state = tmp_path / 'normal-cli-center.sqlite3'
    # Exercise the real argv loader -> typed composition -> normal bootstrap
    # branch -> native provider/QuickChain scheduler, with ONLY DB/evidence fakes.
    monkeypatch.setitem(globals(), '_COMPOSITION_FIXTURE', composition)
    entry = tmp_path / 'reviewed-offline-composition.py'
    entry.write_text('def compose_public_paired_runtime(*, state_path, index_manifest_path):\n'
        '    from ' + __name__ + ' import _COMPOSITION_FIXTURE\n'
        '    return _COMPOSITION_FIXTURE\n', encoding='utf-8')
    argv = ['--phase', 'paired-run', '--run-id', 'SIMULATED-normal-composition', '--state-path', str(state),
            '--runtime-entry', str(entry), '--runtime-entry-sha256', hashlib.sha256(entry.read_bytes()).hexdigest()]
    assert main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert calls == list(DATASETS) and first['scheduler'] == 'run_public_paired_cohort'
    assert first['status'] == 'SIMULATED_PAIRED_COHORT_COMPLETED'
    assert first['qid_count'] == 24 and first['new_quickchain_invocations'] == 72
    assert first['execution_kind'] == 'SIMULATED' and first['provider_calls'] == 0
    assert main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert second['new_quickchain_invocations'] == 0 and second['results_sha256'] == first['results_sha256']
    (tmp_path / 'normal-cli-first.json').write_text(json.dumps(first), encoding='utf-8')
    (tmp_path / 'normal-cli-resume.json').write_text(json.dumps(second), encoding='utf-8')
    (tmp_path / 'normal-cli-command.json').write_text(json.dumps({'argv': argv, 'exit_codes': [0, 0],
        'execution': 'IN_PROCESS_CLI_SIMULATED_DB_EVIDENCE_NATIVE_PROVIDERS'}), encoding='utf-8')


def test_real_composition_with_unverified_capacity_or_scope_stops_before_bootstrap(tmp_path, counter, monkeypatch):
    runtime, _ = native_fixture(tmp_path, counter)
    composition = replace(composition_for(runtime), execution_kind='REAL',
        capacity_probe=lambda: {'chat_cheap': SimpleNamespace(production_ready=False),
                                'chat_expensive': SimpleNamespace(production_ready=False)})
    monkeypatch.setattr('eval_center.public_runner.build_container',
        lambda *a, **k: pytest.fail('not admitted: no Settings key reads/DB/bootstrap'))
    report = _run(_parser().parse_args(['--phase', 'paired-run', '--state-path', str(tmp_path/'never.sqlite3')]),
                  composition=composition)
    assert report['status'] == 'BLOCKED_ZERO_SEND'
    assert 'production_capacity:chat_cheap' in report['missing']
    assert 'real_typed_grants:scifact' in report['missing']
    assert not (tmp_path/'never.sqlite3').exists()


def test_hash_mismatch_never_executes_operator_entry(tmp_path):
    path, marker = tmp_path / 'operator.py', tmp_path / 'should-not-exist'
    path.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\n', encoding='utf-8')
    args = _parser().parse_args(['--phase', 'paired-run', '--runtime-entry', str(path),
        '--runtime-entry-sha256', '0'*64, '--state-path', str(tmp_path/'never.sqlite3')])
    report = _run(args)
    assert report['status'] == 'BLOCKED_ZERO_SEND'
    assert report['missing'] == ['reviewed_runtime_entry_hash_mismatch']
    assert not marker.exists()


def test_hash_frozen_entry_returns_typed_composition_for_normal_assembly(tmp_path, monkeypatch):
    path = tmp_path / 'reviewed_operator.py'
    path.write_text('def compose_public_paired_runtime(*, state_path, index_manifest_path):\n'
                    '    from eval_center.public_runner import PairedRuntimeComposition\n'
                    '    return PairedRuntimeComposition({}, {}, lambda: None)\n', encoding='utf-8')
    monkeypatch.setattr('eval_center.public_runner.build_container', lambda *a, **k: pytest.fail('empty composition'))
    args = _parser().parse_args(['--phase', 'paired-run', '--runtime-entry', str(path),
        '--runtime-entry-sha256', hashlib.sha256(path.read_bytes()).hexdigest(),
        '--state-path', str(tmp_path/'never.sqlite3')])
    report = _run(args)
    assert report['status'] == 'BLOCKED_ZERO_SEND' and report['missing'] == ['exact_dataset_composition']


@pytest.mark.parametrize('change', ['missing_window', 'run', 'cohort', 'dynamic', 'undeclared_helper'])
def test_composition_rejects_unfrozen_or_incomplete_window_before_bootstrap(native_completed, monkeypatch, change):
    root, state, args, runtime, receipts, first = native_completed
    composition = composition_for(runtime)
    item = composition.datasets['scifact']
    plan = item.xing_binding.request_window
    if change == 'missing_window':
        plan = None
    else:
        if change == 'run':
            plan = replace(plan, run_id='other-run')
        elif change == 'cohort':
            plan = replace(plan, cohort_sha256='f'*64)
        elif change == 'dynamic':
            plan = replace(plan, calls=tuple(c for c in plan.calls if c.arm != 'DYNAMIC'))
        else:
            helper = replace(plan.calls[0],request_id='SIMULATED-unimplemented-purpose',purpose='shell')
            plan = replace(plan,calls=plan.calls+(helper,))
        plan = replace(plan,frozen_plan_sha256=plan.fingerprint)
    composition = replace(composition,datasets=dict(composition.datasets,scifact=replace(item,
        xing_binding=replace(item.xing_binding,request_window=plan))))
    monkeypatch.setattr('eval_center.public_runner.build_container',lambda *a,**k:pytest.fail('window not admitted'))
    before = runtime.provider_observations()
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:
        build_normal_paired_runtime(composition,source_manifest_path=SOURCE,index_manifest_path=None,
                                    cohort_path=COHORT,run_id=args.run_id)
    assert 'frozen_xing_request_window:scifact' in exc.value.missing
    assert runtime.provider_observations() == before


@pytest.mark.parametrize('change', ['missing_dynamic', 'over_1m', 'independent_activity'])
def test_deepseek_plan_covers_dynamic_in_one_shared_bounded_activity(native_completed, monkeypatch, change):
    root, state, args, runtime, receipts, first = native_completed
    composition = composition_for(runtime)
    item = composition.datasets['scifact']
    if change == 'missing_dynamic':
        item = replace(item,deepseek_calls=tuple(c for c in item.deepseek_calls if c.arm!='DYNAMIC'))
        expected = 'deepseek_worst_case_calls:scifact'
    elif change == 'over_1m':
        item = replace(item,deepseek_calls=tuple(replace(c,input_token_bound=100_000) for c in item.deepseek_calls))
        expected = 'shared_deepseek_worst_case_1m_token_budget'
    else:
        item = replace(item,v6_binding=replace(item.v6_binding,activity_gate=SimpleNamespace(grant=item.v6_binding.grant)))
        expected = 'shared_deepseek_worst_case_1m_token_budget'
    composition = replace(composition,datasets=dict(composition.datasets,scifact=item))
    monkeypatch.setattr('eval_center.public_runner.build_container',lambda *a,**k:pytest.fail('forecast not admitted'))
    before = runtime.provider_observations()
    with pytest.raises(PairedRuntimeAssemblyBlocked) as exc:
        build_normal_paired_runtime(composition,source_manifest_path=SOURCE,index_manifest_path=None,
                                    cohort_path=COHORT,run_id=args.run_id)
    assert expected in exc.value.missing and runtime.provider_observations()==before


def test_native_plan_retains_both_dynamic_choices_and_zero_implicit_helpers(native_completed):
    root, state, args, runtime, receipts, first = native_completed
    record = json.loads((root/'frozen-window-plan.json').read_text())
    assert record['capture_report']['current_provider_observations']['cheap_fixture_calls']==0
    assert record['capture_report']['current_provider_observations']['deepseek_fixture_calls']==0
    assert record['deepseek_worst_case_calls']==48
    assert record['deepseek_worst_case_tokens']+record['baseline_used_tokens']<=record['deepseek_limit']
    assert sum(len(row['calls']) for row in record['sf_windows'].values())==48
    assert all(row['helper_counts']==dict.fromkeys(('understand','rewrite','expansion'),0)
               for row in record['sf_windows'].values())
    gates = [c.quick_chain.generation_roles['chat_expensive'].v6_binding.activity_gate
             for c in runtime.containers.values()]
    assert all(g is gates[0] for g in gates)
