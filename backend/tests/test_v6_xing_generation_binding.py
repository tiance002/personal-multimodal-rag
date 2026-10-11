"""REAL OFFLINE Xing counter + SIMULATED hosted responses through normal paths."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os

import pytest

from backend.app.adapters.models.cloud import (
    CloudChat, SimulatedXingSend, XingAuthorizedTransport, XingChatGrant,
    XingZeroPriceContract, XingCohortCalibrationClaimStore, XingCalibrationPermit,
    XING_CALIBRATION_SCOPE, XING_CALIBRATION_REQUEST_ID, XING_CALIBRATION_DEADLINE,
    XING_CALIBRATION_WIRE_SHA256, XING_PRICE_SOURCE_SHA256,
)
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.models.siliconflow_xing_capacity import (
    SiliconFlowXingCapacityCounter, SILICONFLOW_XING_MODEL,
)
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.rule_router_v1 import (
    GenerationEnvelope, GenerationRole, RouterPolicy, V6XingGenerationBinding, decide,
)
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer

PUBLIC_SCOPE = Scope.from_ids(['SIMULATED-public-kb'], ['SIMULATED-public-doc'])
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
DEADLINE = '2026-10-10T04:25:44Z'


@pytest.fixture(scope='module')
def counter():
    assets = os.environ.get('XING_TEST_ASSETS')
    assert assets, 'XING_TEST_ASSETS required; counter must execute the pinned real resources'
    return SiliconFlowXingCapacityCounter.from_directory(assets)


@pytest.fixture(autouse=True)
def no_live_sender(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-ONLY')
    monkeypatch.setattr('backend.app.adapters.models.cloud._send',
        lambda *a, **k: pytest.fail('Live send is forbidden'))


def response(**changes):
    row = dict(model=SILICONFLOW_XING_MODEL,
        choices=[{'message': {'content': 'SIMULATED answer [E1]'}, 'finish_reason': 'stop'}],
        usage={'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7,
               'completion_tokens_details': {'reasoning_tokens': 0}})
    row.update(changes)
    return row


def setup(counter, *, result=None, scope=PUBLIC_SCOPE, purposes=('answer',), execution='SIMULATED',
          enabled=True, budget=100, grant_changes=None, mutate_request=None):
    registry = ModelRegistry.frozen_defaults()
    ledger = InMemoryBudgetGate(budget)
    guard = BudgetUsageGuard(ledger, registry.select('chat_cheap'), 3)
    grant = XingChatGrant('SIMULATED-xing-owner', 'SIMULATED-public-v6', scope,
        DEADLINE, purposes, execution)
    if grant_changes:
        grant = replace(grant, **grant_changes)
    wires = []
    def send(request, timeout):
        wires.append(request.data)
        if isinstance(result, Exception):
            raise result
        return response() if result is None else result
    transport = XingAuthorizedTransport(grant,
        sender=SimulatedXingSend(send) if execution == 'SIMULATED' else None, clock=lambda: NOW)
    binding = V6XingGenerationBinding(grant, counter, transport)
    factory = ProviderFactory(registry, enabled_roles=('chat_cheap',) if enabled else (),
        usage_guards={'chat_cheap': guard})
    gateway = factory.build('chat_cheap', xing_binding=binding)
    if mutate_request:
        original = gateway.transport
        # Negative contract: mutate the final Request between two normal checks.
        def mutation(request, timeout):
            mutate_request(request)
            return original(request, timeout)
        gateway.transport = mutation
    role = GenerationRole('chat_cheap', gateway, xing_binding=binding)
    return role, transport, ledger, wires, factory


def envelope(prompt='SIMULATED public hello', output=512, timeout=10):
    return GenerationEnvelope(prompt, prompt, prompt, 'SIMULATED-context', (), (), output, timeout)


def invoke(role, *, scope=PUBLIC_SCOPE, request_id='SIMULATED-q01-answer', env=None):
    return role.invoke(env or envelope(), scope=scope, request_id=request_id, run_id='SIMULATED-cohort')


def test_normal_factory_usage_guard_transport_and_final_wire(counter):
    role, transport, ledger, wires, factory = setup(counter)
    assert invoke(role) == 'SIMULATED answer [E1]'
    assert len(wires) == len(ledger.reservations) == len(role.gateway.receipts) == 1
    wire = wires[0]
    payload = json.loads(wire)
    assert tuple(payload) == ('model', 'messages', 'max_tokens', 'stream', 'enable_thinking')
    assert payload['model'] == SILICONFLOW_XING_MODEL and payload['enable_thinking'] is False
    estimate = counter.measure('siliconflow', SILICONFLOW_XING_MODEL, payload,
        scope=role.xing_binding.grant.scope_id, wire_bytes=wire)
    estimate.assert_wire_bytes(wire)
    assert estimate.production_ready is False and estimate.hosted_total_token_bound is None
    receipt = role.gateway.receipts[0]
    assert receipt['planned_tokens'] == estimate.local_total_reservation_bound
    assert receipt['wire_request_sha256'] == hashlib.sha256(wire).hexdigest()
    assert receipt['local_input_token_bound'] == estimate.reservation_bound
    assert receipt['hosted_revision'] == 'UNKNOWN' and receipt['settlement'] == 'UNKNOWN'
    assert receipt['generation_purpose'] == receipt['usage_stage'] == 'answer'
    assert next(iter(ledger.reservations.values()))['state'] == 'unknown'
    row = transport.attempts['SIMULATED-q01-answer']
    assert row['response_actual_model'] == SILICONFLOW_XING_MODEL and row['reasoning_tokens'] == 0
    assert 'SIMULATED-ONLY' not in str(receipt) + str(row)


@pytest.mark.parametrize('mode', ['CHEAP_ONLY', 'DYNAMIC'])
def test_both_cheap_arms_use_same_normal_role(counter, mode):
    role, transport, ledger, wires, factory = setup(counter)
    decision = decide('hello', 'hello', RouterPolicy(mode=mode))
    assert decision.role == 'chat_cheap' and str(RouterPolicy().threshold) == '0.35'
    assert invoke(role) == 'SIMULATED answer [E1]' and len(wires) == 1


@pytest.mark.parametrize('purpose', ['understand', 'rewrite', 'expansion'])
def test_explicit_auxiliary_purposes_are_cheap_and_share_normal_guard(counter, purpose):
    role, transport, ledger, wires, factory = setup(counter, purposes=(purpose,))
    from backend.app.ports.model_usage import capture_usage
    with capture_usage() as usage:
        result = role.invoke_auxiliary([
            {'role': 'system', 'content': 'SIMULATED instruction'},
            {'role': 'user', 'content': 'SIMULATED public query'}], purpose=purpose,
            request_id='SIMULATED-' + purpose, run_id='SIMULATED-cohort', scope=PUBLIC_SCOPE,
            max_tokens=256, timeout_seconds=10)
    assert result.model_requested == result.model_reported == SILICONFLOW_XING_MODEL
    assert len(wires) == len(ledger.reservations) == 1
    assert transport.attempts['SIMULATED-' + purpose]['purpose'] == purpose
    assert next(iter(ledger.reservations.values()))['state'] == 'unknown'
    assert len(usage.calls) == 1 and usage.calls[0].capability == 'chat_cheap'
    assert usage.calls[0].stage == ('query_understand' if purpose == 'understand' else 'query')
    receipt = role.gateway.receipts[0]
    assert receipt['generation_purpose'] == purpose
    assert receipt['usage_stage'] == usage.calls[0].stage
    summary = usage.summary()
    assert summary[usage.calls[0].stage]['call_count'] == 1
    assert summary['answer']['call_count'] == 0
    assert summary['business_total_tokens'] == 7


@pytest.mark.parametrize('capability,expected', [
    ('chat_cheap', 'answer'), ('chat_expensive', 'answer'),
    ('embedding', 'embedding'), ('rerank', 'rerank'), ('query_understand', 'query_understand'),
])
def test_provider_telemetry_default_behavior_unchanged(capability, expected):
    from backend.app.ports.model_usage import call_stage, capture_usage, record_provider_call
    with capture_usage() as usage, call_stage('query'):
        record_provider_call(model_key='SIMULATED', capability=capability,
            provider='SIMULATED', model='SIMULATED', status='ok',
            usage={'prompt_tokens': 5, 'completion_tokens': 2}, latency_ms=1)
    assert usage.calls[0].stage == expected
    assert usage.calls[0].capability == capability
    assert usage.calls[0].settlement == 'UNKNOWN'


@pytest.mark.parametrize('stage', ['', 'unknown', 'rewrite', 'expansion', 'QueryUnderstand', True, 1])
def test_unknown_provider_telemetry_stage_is_rejected_without_capture(stage):
    from backend.app.ports.model_usage import record_provider_call
    with pytest.raises(ValueError, match='invalid model call stage'):
        record_provider_call(model_key='SIMULATED', capability='chat_cheap',
            provider='SIMULATED', model='SIMULATED', status='ok', usage=None,
            latency_ms=1, stage=stage)


@pytest.mark.parametrize('purpose', ['understand', 'rewrite', 'expansion'])
def test_auxiliary_failure_keeps_declared_stage_and_purpose(counter, purpose):
    from backend.app.ports.model_usage import capture_usage
    role, transport, ledger, wires, factory = setup(counter, purposes=(purpose,),
        result=TimeoutError('SIMULATED timeout'))
    with capture_usage() as usage:
        with pytest.raises(ProviderUnavailable):
            role.invoke_auxiliary([{'role': 'user', 'content': 'SIMULATED public query'}],
                purpose=purpose, request_id='SIMULATED-error-' + purpose,
                run_id='SIMULATED-cohort', scope=PUBLIC_SCOPE,
                max_tokens=256, timeout_seconds=10)
    receipt = role.gateway.receipts[0]
    expected_stage = 'query_understand' if purpose == 'understand' else 'query'
    assert receipt['generation_purpose'] == purpose and receipt['usage_stage'] == expected_stage
    assert usage.calls[0].stage == expected_stage and usage.calls[0].capability == 'chat_cheap'
    assert usage.summary()[expected_stage]['availability'] == 'unavailable'
    assert usage.summary()['business_total_tokens'] is None
    assert receipt['settlement'] == 'UNKNOWN'
    assert next(iter(ledger.reservations.values()))['state'] == 'unknown'


@pytest.mark.parametrize('purpose', ['understand', 'rewrite', 'expansion'])
def test_normal_adapter_entry_preserves_purpose_without_ambient_stage(counter, purpose):
    from backend.app.ports.model_usage import capture_usage
    role, transport, ledger, wires, factory = setup(counter, purposes=(purpose,))
    with capture_usage() as usage, model_access('chat_cheap', allowed=True):
        role.gateway.generate_with_xing_scope([{'role': 'user', 'content': 'SIMULATED public query'}],
            timeout_seconds=10, max_tokens=256, scope=PUBLIC_SCOPE,
            purpose=purpose, request_id='SIMULATED-adapter-' + purpose)
    expected_stage = 'query_understand' if purpose == 'understand' else 'query'
    assert usage.calls[0].stage == expected_stage
    assert usage.calls[0].capability == 'chat_cheap'
    assert role.gateway.receipts[0]['generation_purpose'] == purpose
    assert role.gateway.receipts[0]['usage_stage'] == expected_stage


@pytest.mark.parametrize('purpose', ['answer', 'shell', 'UNDERSTAND', 'QueryUnderstand', True, None])
def test_undeclared_auxiliary_purpose_cannot_dispatch(counter, purpose):
    role, transport, ledger, wires, factory = setup(counter)
    with pytest.raises(ProviderRequestNotSent):
        role.invoke_auxiliary([], purpose=purpose, request_id='SIMULATED-bad',
            run_id='SIMULATED', scope=PUBLIC_SCOPE)
    assert not wires and not ledger.reservations and not transport.attempts


@pytest.mark.parametrize('scope', [Scope.from_ids(['other']), Scope.from_ids(['SIMULATED-public-kb']),
    Scope.from_ids(['SIMULATED-public-kb'], ['other']), None, True, 'SIMULATED-public-v6'])
def test_scope_exact_permission_denial_before_reservation(counter, scope):
    role, transport, ledger, wires, factory = setup(counter)
    with pytest.raises(ProviderRequestNotSent, match='XING_SCOPE_OR_PURPOSE_DENIED'):
        invoke(role, scope=scope)
    assert not wires and not ledger.reservations and not transport.attempts


@pytest.mark.parametrize('changes', [
    {'grant_id': ''}, {'scope_id': ''}, {'scope': True}, {'scope': Scope.from_ids([])},
    {'deadline_utc': 'no-date'}, {'deadline_utc': '2026-10-10'},
    {'allowed_purposes': ('shell',)}, {'allowed_purposes': True},
    {'allowed_purposes': ('answer', 'answer')}, {'execution_kind': 'OTHER'},
])
def test_invalid_typed_grant_rejected(counter, changes):
    with pytest.raises(ValueError, match='XING_GRANT_INVALID'):
        setup(counter, grant_changes=changes)


def test_real_binding_runs_local_counter_but_remains_zero_send(counter, monkeypatch):
    role, transport, ledger, wires, factory = setup(counter, execution='REAL')
    measured = []
    original = counter.measure
    def measure(*args, **kwargs):
        result = original(*args, **kwargs)
        measured.append(result)
        return result
    monkeypatch.setattr(counter, 'measure', measure)
    with pytest.raises(ProviderRequestNotSent, match='XING_HOSTED_CAPACITY_AND_THINKING_UNVERIFIED'):
        invoke(role)
    assert len(measured) == 1 and measured[0].reservation_bound > 0
    assert measured[0].production_ready is False and measured[0].hosted_revision == 'UNKNOWN'
    assert not wires and not ledger.reservations and not transport.attempts


@pytest.mark.parametrize('binding', [True, False, object()])
def test_bool_binding_cannot_authorize_factory(counter, binding):
    factory = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('chat_cheap',))
    with pytest.raises(ValueError, match='XING_BINDING_INVALID'):
        factory.build('chat_cheap', xing_binding=binding)


def test_simulated_sender_cannot_bind_real_grant(counter):
    grant = XingChatGrant('owner-real-shape', 'public', 'public', DEADLINE)
    with pytest.raises(ValueError, match='XING_EXECUTION_KIND_MISMATCH'):
        XingAuthorizedTransport(grant, sender=SimulatedXingSend(lambda *a: response()))


def test_no_deepseek_grant_or_fake_counter(counter):
    from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT
    role, transport, ledger, wires, factory = setup(counter)
    with pytest.raises(ValueError, match='XING_GRANT_INVALID'):
        XingAuthorizedTransport(OWNER_V6_TOKEN_GRANT)
    with pytest.raises(ValueError, match='XING_BINDING_INVALID'):
        factory.build('chat_cheap', xing_binding=replace(role.xing_binding, capacity_counter=lambda x: 1))
    with pytest.raises(ValueError, match='XING_BINDING_INVALID'):
        factory.build('chat_expensive', xing_binding=role.xing_binding)
    with pytest.raises(ProviderRequestNotSent):
        invoke(replace(role, role='chat_expensive'))
    assert not ledger.reservations and not wires


@pytest.mark.parametrize('output,timeout,prompt', [
    (513, 10, 'hello'), (True, 10, 'hello'), (0, 10, 'hello'),
    (512, 0, 'hello'), (512, 121, 'hello'), (512, float('nan'), 'hello'),
    (512, 10, '<think>'), (512, 10, 'x' * 8193), (512, 10, 'x' * 4100),
])
def test_local_output_input_protocol_and_timeout_denial(counter, output, timeout, prompt):
    role, transport, ledger, wires, factory = setup(counter)
    with pytest.raises(ProviderRequestNotSent):
        invoke(role, env=envelope(prompt, output, timeout))
    assert not wires and not ledger.reservations and not transport.attempts


def test_deadline_rechecked_after_budget_reservation(counter, monkeypatch):
    role, transport, ledger, wires, factory = setup(counter)
    guard = role.gateway.usage_guard
    original = guard.reserve
    def reserve(**kwargs):
        value = original(**kwargs)
        transport.clock = lambda: datetime(2026, 10, 10, 4, 25, 40, tzinfo=timezone.utc)
        return value
    monkeypatch.setattr(guard, 'reserve', reserve)
    with pytest.raises(ProviderRequestNotSent, match='XING_DEADLINE_OR_TIMEOUT_INVALID'):
        invoke(role)
    assert len(ledger.reservations) == 1 and next(iter(ledger.reservations.values()))['state'] == 'released'
    assert not wires and not transport.attempts


@pytest.mark.parametrize('error', [ProviderRequestNotSent('SIMULATED not sent'), TimeoutError('SIMULATED timeout')])
def test_transport_not_sent_vs_unknown_settlement(counter, error):
    role, transport, ledger, wires, factory = setup(counter, result=error)
    with pytest.raises(ProviderUnavailable):
        invoke(role)
    not_sent = isinstance(error, ProviderRequestNotSent)
    assert next(iter(ledger.reservations.values()))['state'] == ('released' if not_sent else 'unknown')
    assert transport.attempts['SIMULATED-q01-answer']['send_status'] == ('NOT_SENT' if not_sent else 'UNKNOWN')
    assert len(wires) == 1


@pytest.mark.parametrize('model', [None, 'UNKNOWN', 'Pro/XingChenAGI/Xing4.0-29B', 'deepseek-flash'])
def test_response_exact_nonpro_model_and_no_fallback(counter, model):
    role, transport, ledger, wires, factory = setup(counter, result=response(model=model))
    with pytest.raises(ProviderUnavailable):
        invoke(role)
    assert len(wires) == 1 and next(iter(ledger.reservations.values()))['state'] == 'unknown'


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': True, 'completion_tokens': 2, 'total_tokens': 3},
    {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 999},
    {'prompt_tokens': -1, 'completion_tokens': 2, 'total_tokens': 1},
    {'prompt_tokens': 9000, 'completion_tokens': 2, 'total_tokens': 9002},
    {'prompt_tokens': 5, 'completion_tokens': 513, 'total_tokens': 518}])
def test_missing_invalid_or_overbound_usage_never_releases_unknown(counter, usage):
    role, transport, ledger, wires, factory = setup(counter, result=response(usage=usage))
    with pytest.raises(ProviderUnavailable):
        invoke(role)
    assert len(wires) == 1 and next(iter(ledger.reservations.values()))['state'] == 'unknown'


@pytest.mark.parametrize('reasoning', ['visible', 'tokens'])
def test_observed_thinking_breaks_disabled_contract(counter, reasoning):
    result = response()
    if reasoning == 'visible':
        result['choices'][0]['message']['reasoning_content'] = 'SIMULATED hidden reasoning'
    else:
        result['usage']['completion_tokens_details']['reasoning_tokens'] = 1
    role, transport, ledger, wires, factory = setup(counter, result=result)
    with pytest.raises(ProviderUnavailable, match='XING_THINKING_DISABLE_UNVERIFIED'):
        invoke(role)
    assert len(wires) == 1 and next(iter(ledger.reservations.values()))['state'] == 'unknown'


def test_finish_length_not_success_and_full_unknown(counter):
    result = response(choices=[{'message': {'content': 'SIMULATED partial'}, 'finish_reason': 'length'}])
    role, transport, ledger, wires, factory = setup(counter, result=result)
    with pytest.raises(TruncatedAnswer):
        invoke(role)
    assert len(wires) == 1 and next(iter(ledger.reservations.values()))['state'] == 'unknown'


def test_monthly_guard_denial_prevents_transport(counter):
    role, transport, ledger, wires, factory = setup(counter, budget=0)
    with pytest.raises(ProviderRequestNotSent, match='MODEL_BUDGET_DENIED'):
        invoke(role)
    assert not wires and not ledger.reservations and not transport.attempts


def test_monthly_settlement_failure_cannot_report_success(counter, monkeypatch):
    role, transport, ledger, wires, factory = setup(counter)
    monkeypatch.setattr(role.gateway.usage_guard, 'settle',
        lambda *a, **k: (_ for _ in ()).throw(OSError('SIMULATED settlement failure')))
    with pytest.raises(ProviderUnavailable, match='MODEL_USAGE_SETTLEMENT_FAILED'):
        invoke(role)
    assert len(wires) == 1 and next(iter(ledger.reservations.values()))['state'] == 'reserved'


def test_concurrent_duplicate_once_only(counter):
    role, transport, ledger, wires, factory = setup(counter)
    def call(_):
        try:
            return invoke(role)
        except ProviderRequestNotSent:
            return 'DENIED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(call, range(2)))
    assert values.count('SIMULATED answer [E1]') == values.count('DENIED') == 1
    assert len(wires) == len(transport.attempts) == 1


def test_final_wire_tamper_denied_and_releases_own_reservation(counter, monkeypatch):
    role, transport, ledger, wires, factory = setup(counter)
    original = transport.__class__.__call__
    def changed(self, request, timeout):
        request.data += b' '
        return original(self, request, timeout)
    monkeypatch.setattr(XingAuthorizedTransport, '__call__', changed)
    with pytest.raises(ProviderRequestNotSent, match='XING_FINAL_WIRE_MISMATCH'):
        invoke(role)
    assert not wires and not transport.attempts
    assert next(iter(ledger.reservations.values()))['state'] == 'released'


def test_binding_mutation_cannot_change_authorized_scope(counter):
    role, transport, ledger, wires, factory = setup(counter)
    transport.grant = replace(transport.grant, scope=Scope.from_ids(['other']))
    with pytest.raises(ProviderRequestNotSent, match='XING_BINDING_INVALID'):
        invoke(role, scope=transport.grant.scope)
    assert not wires and not ledger.reservations


def test_generic_generate_and_transport_cannot_grant_scope(counter):
    role, transport, ledger, wires, factory = setup(counter)
    with model_access('chat_cheap', allowed=True):
        with pytest.raises(ProviderRequestNotSent, match='XING_EXPLICIT_SCOPE_REQUIRED'):
            role.gateway.generate([{'role': 'user', 'content': 'hello'}], timeout_seconds=10)
    from urllib.request import Request
    with pytest.raises(ProviderRequestNotSent, match='XING_AUTHORIZED_CONTEXT_REQUIRED'):
        transport(Request('https://api.siliconflow.cn/v1/chat/completions', data=b'{}'), 10)
    assert not wires and not ledger.reservations


def test_default_factory_startup_is_zero_call_and_real_cheap_closed(counter, tmp_path):
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    container = build_container(Settings(database_url='sqlite+pysqlite:///:memory:',
        storage_root=tmp_path, rule_router_enabled=True), model=None, agent_model=None)
    role = container.quick_chain.generation_roles['chat_cheap']
    assert role.xing_binding is None and role.gateway.requests == 0
    with pytest.raises(ProviderRequestNotSent, match='P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY'):
        role.check()
    with model_access('chat_cheap', allowed=True):
        with pytest.raises(ProviderRequestNotSent, match='XING_BINDING_REQUIRED'):
            role.gateway.generate([{'role': 'user', 'content': 'hello'}], timeout_seconds=10)


def test_explicit_bootstrap_binding_is_composition_only(counter, tmp_path):
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    role, transport, ledger, wires, factory = setup(counter)
    settings = Settings(database_url='sqlite+pysqlite:///:memory:', storage_root=tmp_path,
        rule_router_enabled=True, cloud_enabled=True, chat_egress_enabled=True)
    container = build_container(settings, model=None, agent_model=None,
        model_usage_guards=factory.usage_guards, v6_xing_binding=role.xing_binding)
    wired = container.quick_chain.generation_roles['chat_cheap']
    assert wired.xing_binding is role.xing_binding and not wires and not ledger.reservations
    assert invoke(wired) == 'SIMULATED answer [E1]' and len(wires) == 1


def calibration(counter, tmp_path, monkeypatch, *, result=None, path=None, execution='SIMULATED'):
    from eval_center.paired_state import register_run, SQLiteProviderSendClaims, provider_send_claim_record
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr('eval_center.paired_state.datetime', FixedDatetime)
    path = path or tmp_path / 'existing-center.sqlite3'
    if not path.exists():
        # Fixture uses the normal cohort registration, never a canonical ledger.
        register_run(path, run_id='v6-xing-public-smoke-r1', cohort_sha256='a' * 64,
            source_manifest_sha256='b' * 64, code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',
            deadline_utc=DEADLINE, now_utc=NOW.isoformat(),
            expected_qids=[XING_CALIBRATION_REQUEST_ID], execution_purpose='CALIBRATION')
    callback = SQLiteProviderSendClaims(path, 'v6-xing-public-smoke-r1',
        XING_CALIBRATION_REQUEST_ID, 'CHEAP_ONLY', XING_CALIBRATION_WIRE_SHA256)
    store = XingCohortCalibrationClaimStore(callback)
    quote = XingZeroPriceContract.from_frozen_html(
        os.path.join(os.environ['XING_TEST_ASSETS'], 'siliconflow-models.html'))
    permit = XingCalibrationPermit(quote, store)
    grant = XingChatGrant('owner-v6-xing-calibration-r1', XING_CALIBRATION_SCOPE,
        XING_CALIBRATION_SCOPE, XING_CALIBRATION_DEADLINE, ('calibration',), execution)
    wires = []
    def send(request, timeout):
        claim = provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)
        assert claim['status'] == 'pending' and claim['purpose'] == 'calibration'
        assert claim['provider'] == 'siliconflow' and claim['model'] == SILICONFLOW_XING_MODEL
        wires.append(request.data)
        if isinstance(result, Exception):
            raise result
        return response() if result is None else result
    transport = XingAuthorizedTransport(grant, sender=SimulatedXingSend(send)
        if execution == 'SIMULATED' else None, clock=lambda: NOW, calibration_permit=permit)
    registry = ModelRegistry.frozen_defaults()
    ledger = InMemoryBudgetGate(100)
    guard = BudgetUsageGuard(ledger, registry.select('chat_cheap'), 3)
    binding = V6XingGenerationBinding(grant, counter, transport)
    gateway = ProviderFactory(registry, enabled_roles=('chat_cheap',),
        usage_guards={'chat_cheap': guard}).build('chat_cheap', xing_binding=binding)
    return gateway, transport, ledger, wires, path


def invoke_calibration(gateway, **changes):
    args = dict(messages=[{'role': 'user', 'content': 'Reply exactly OK.'}],
        timeout_seconds=10, max_tokens=32, scope=XING_CALIBRATION_SCOPE,
        purpose='calibration', request_id=XING_CALIBRATION_REQUEST_ID)
    args.update(changes)
    with model_access('chat_cheap', allowed=True):
        return gateway.generate_with_xing_scope(**args)


def test_calibration_uses_normal_guard_final_wire_and_committed_cohort_claim(counter, tmp_path, monkeypatch):
    from eval_center.paired_state import provider_send_claim_record
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    result = invoke_calibration(gateway)
    assert result.model_requested == result.model_reported == SILICONFLOW_XING_MODEL
    assert len(wires) == len(ledger.reservations) == 1
    assert hashlib.sha256(wires[0]).hexdigest() == XING_CALIBRATION_WIRE_SHA256
    receipt = gateway.receipts[0]
    assert receipt['generation_purpose'] == 'calibration'
    assert receipt['local_input_token_bound'] <= 32 and receipt['local_output_token_bound'] == 32
    assert receipt['planned_tokens'] <= 64
    assert receipt['price_source_sha256'] == XING_PRICE_SOURCE_SHA256
    assert receipt['quoted_currency'] == 'CNY' and receipt['quoted_token_cost_micro_cny'] == 0
    assert receipt['actual_charge_verified'] is False and receipt['actual_charge'] == 'UNKNOWN'
    assert receipt['settlement'] == 'UNKNOWN' and receipt['production_ready'] is False
    assert next(iter(ledger.reservations.values()))['state'] == 'unknown'
    claim = provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)
    assert claim['status'] == 'simulated'
    saved = claim['result']
    assert saved['dispatch_contract']['execution_kind'] == 'SIMULATED'
    assert saved['dispatch_contract']['wire_request_sha256'] == XING_CALIBRATION_WIRE_SHA256
    assert saved['dispatch_contract']['price_source_sha256'] == XING_PRICE_SOURCE_SHA256
    assert saved['budget_reservation_id'] in ledger.reservations


@pytest.mark.parametrize('changes', [
    {'messages': [{'role': 'user', 'content': 'Other public text'}]},
    {'max_tokens': 31}, {'max_tokens': 33}, {'timeout_seconds': 9}, {'timeout_seconds': 11},
    {'request_id': 'new-id'}, {'scope': 'other'}, {'purpose': 'answer'},
])
def test_calibration_exact_contract_rejects_before_reserve(counter, tmp_path, monkeypatch, changes):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    with pytest.raises(ProviderRequestNotSent):
        invoke_calibration(gateway, **changes)
    assert not wires and not ledger.reservations


@pytest.mark.parametrize('field,value', [('source_sha256', '0' * 64), ('source_url', 'https://other'),
    ('model', 'Pro/XingChenAGI/Xing4.0-29B'), ('currency', 'USD'),
    ('prompt_per_million', 1), ('completion_per_million', 1), ('prompt_per_million', False)])
def test_calibration_price_binding_is_exact_and_not_a_free_switch(counter, tmp_path, monkeypatch, field, value):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    transport.calibration_permit = replace(transport.calibration_permit,
        price_contract=replace(transport.calibration_permit.price_contract, **{field: value}))
    with pytest.raises(ProviderRequestNotSent, match='XING_PRICE_SOURCE_UNVERIFIED'):
        invoke_calibration(gateway)
    assert not wires and not ledger.reservations


@pytest.mark.parametrize('error', [TimeoutError('SIMULATED unknown'), ProviderRequestNotSent('SIMULATED not sent')])
def test_calibration_restart_never_retries_unknown_or_not_sent(counter, tmp_path, monkeypatch, error):
    from eval_center.paired_state import provider_send_claim_record
    first, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch, result=error)
    with pytest.raises(ProviderUnavailable):
        invoke_calibration(first)
    claim = provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)
    not_sent = isinstance(error, ProviderRequestNotSent)
    assert claim['status'] == ('blocked' if not_sent else 'unknown')
    assert next(iter(ledger.reservations.values()))['state'] == ('released' if not_sent else 'unknown')
    second, _, second_ledger, second_wires, _ = calibration(counter, tmp_path, monkeypatch, path=path)
    with pytest.raises(ProviderRequestNotSent, match='XING_DURABLE_CLAIM_DENIED'):
        invoke_calibration(second)
    assert not second_wires and len(wires) == 1
    assert next(iter(second_ledger.reservations.values()))['state'] == 'released'
    assert provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID) == claim


def test_calibration_concurrent_instances_share_durable_claim(counter, tmp_path, monkeypatch):
    first, _, _, first_wires, path = calibration(counter, tmp_path, monkeypatch)
    second, _, _, second_wires, _ = calibration(counter, tmp_path, monkeypatch, path=path)
    def call(gateway):
        try:
            invoke_calibration(gateway)
            return 'SIMULATED'
        except ProviderRequestNotSent:
            return 'DENIED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(call, (first, second)))
    assert sorted(results) == ['DENIED', 'SIMULATED']
    assert len(first_wires) + len(second_wires) == 1


def test_calibration_observes_hosted_thinking_and_local_divergence_without_promoting_capacity(counter, tmp_path, monkeypatch):
    result = response(usage={'prompt_tokens': 40, 'completion_tokens': 100, 'total_tokens': 140,
        'completion_tokens_details': {'reasoning_tokens': 90}},
        choices=[{'message': {'content': 'OK', 'reasoning_content': 'SIMULATED reasoning'},
                  'finish_reason': 'stop'}])
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch, result=result)
    assert invoke_calibration(gateway).text == 'OK'
    receipt = gateway.receipts[0]
    assert receipt['reasoning_present'] is True and receipt['reasoning_tokens'] == 90
    assert receipt['observed_input_over_local_bound'] is True
    assert receipt['observed_output_over_local_reservation'] is True
    assert receipt['hosted_total_token_bound'] is None and receipt['production_ready'] is False
    assert receipt['actual_charge'] == receipt['settlement'] == 'UNKNOWN'


def test_calibration_zero_quote_cannot_skip_monthly_gate(counter, tmp_path, monkeypatch):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    ledger.monthly_budget_microunits = 0
    with pytest.raises(ProviderRequestNotSent, match='MODEL_BUDGET_DENIED'):
        invoke_calibration(gateway)
    assert not wires and not ledger.reservations


def test_calibration_quote_cannot_set_monthly_reservation_to_zero(counter, tmp_path, monkeypatch):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    gateway.usage_guard.estimate_microunits = 0
    with pytest.raises(ProviderRequestNotSent, match='XING_CALIBRATION_MONTHLY_GUARD_REQUIRED'):
        invoke_calibration(gateway)
    assert not wires and not ledger.reservations


def test_calibration_finalization_failure_keeps_unknown_and_cannot_report_success(counter, tmp_path, monkeypatch):
    from eval_center.paired_state import provider_send_claim_record
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    monkeypatch.setattr(transport.calibration_permit.claim_store, 'finish_dispatch',
        lambda *a: (_ for _ in ()).throw(OSError('SIMULATED persistence failure')))
    with pytest.raises(ProviderUnavailable, match='XING_DURABLE_FINALIZATION_FAILED'):
        invoke_calibration(gateway)
    assert len(wires) == 1
    assert next(iter(ledger.reservations.values()))['state'] == 'unknown'
    assert provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)['status'] == 'pending'
    assert gateway.receipts[0]['dispatch_persistence'] == 'UNKNOWN'


def test_calibration_permit_is_not_an_ordinary_real_generation_switch(counter, tmp_path, monkeypatch):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch, execution='REAL')
    # Deliberately keep the valid calibration permit while changing the grant
    # to ordinary answer. Hosted capacity must still reject REAL generation.
    grant = replace(transport.grant, allowed_purposes=('answer',))
    transport.grant = grant
    gateway.xing_admission.grant = grant
    binding = replace(gateway.xing_binding, grant=grant)
    gateway.xing_binding = binding
    with pytest.raises(ProviderRequestNotSent, match='XING_HOSTED_CAPACITY_AND_THINKING_UNVERIFIED'):
        invoke_calibration(gateway, purpose='answer')
    assert not wires and not ledger.reservations


def test_calibration_cross_process_replay_is_denied_after_unknown(counter, tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    from eval_center.paired_state import provider_send_claim_record
    gateway, _, _, wires, path = calibration(counter, tmp_path, monkeypatch,
        result=TimeoutError('SIMULATED unknown'))
    with pytest.raises(ProviderUnavailable):
        invoke_calibration(gateway)
    before = provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID)
    code = '''
import json, os, sys
from pathlib import Path
import pytest
from backend.tests.test_v6_xing_generation_binding import calibration, invoke_calibration
from backend.app.adapters.models.siliconflow_xing_capacity import SiliconFlowXingCapacityCounter
from backend.app.ports.providers import ProviderRequestNotSent
patch = pytest.MonkeyPatch()
patch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-ONLY')
patch.setattr('backend.app.adapters.models.cloud._send', lambda *a: (_ for _ in ()).throw(AssertionError('live send forbidden')))
path = Path(sys.argv[1])
counter = SiliconFlowXingCapacityCounter.from_directory(os.environ['XING_TEST_ASSETS'])
gateway, _, ledger, wires, _ = calibration(counter, path.parent, patch, path=path)
try:
    invoke_calibration(gateway)
    raise AssertionError('persistent replay was admitted')
except ProviderRequestNotSent as exc:
    assert str(exc) == 'XING_DURABLE_CLAIM_DENIED'
    assert not wires
    assert next(iter(ledger.reservations.values()))['state'] == 'released'
    print(json.dumps({'execution': 'SIMULATED', 'outcome': 'DENIED', 'wires': 0}))
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(path)],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
        encoding='utf-8', timeout=30)
    (tmp_path / 'cross-process-command.json').write_text(json.dumps({
        'argv': [sys.executable, '-B', '-c', code, str(path)],
        'exitcode': result.returncode, 'execution': 'SIMULATED'}, indent=2), encoding='utf-8')
    (tmp_path / 'cross-process-output.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1])['outcome'] == 'DENIED'
    assert provider_send_claim_record(path, request_id=XING_CALIBRATION_REQUEST_ID) == before
    assert len(wires) == 1


@pytest.mark.parametrize('bad', [True, False, object()])
def test_calibration_cannot_use_boolean_authority(counter, tmp_path, monkeypatch, bad):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='XING_CALIBRATION_AUTHORITY_INVALID'):
        XingAuthorizedTransport(transport.grant, sender=transport.sender,
            clock=lambda: NOW, calibration_permit=bad)
    assert not wires and not ledger.reservations


def test_calibration_requires_actual_cohort_claim_store(counter, tmp_path, monkeypatch):
    gateway, transport, ledger, wires, path = calibration(counter, tmp_path, monkeypatch)
    transport.calibration_permit = replace(transport.calibration_permit, claim_store=None)
    with pytest.raises(ProviderRequestNotSent, match='XING_CALIBRATION_AUTHORITY_INVALID'):
        invoke_calibration(gateway)
    assert not wires and not ledger.reservations


def test_calibration_rejects_changed_price_source(tmp_path):
    path = tmp_path / 'SIMULATED-price.html'
    path.write_text('SIMULATED-free-quote', encoding='utf-8')
    with pytest.raises(ValueError, match='XING_PRICE_SOURCE_UNVERIFIED'):
        XingZeroPriceContract.from_frozen_html(path)
