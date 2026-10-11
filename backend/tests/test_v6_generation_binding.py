"""SIMULATED V6 integration contracts. No model/DB/SSH/canonical ledger writes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

import pytest

from backend.app.adapters.models.deepseek import DeepSeekGateway, SimulatedDeepSeekTransport
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.models.v6_text_capacity import (
    DEEPSEEK_RENDERER_IDENTITY, DeepseekV41TextCapacityCounter, DeepseekV41RecipeRenderer,
)
from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
from backend.app.application.rule_router_v1 import (
    GenerationEnvelope, GenerationRole, MODELS, RouterPolicy, V6GenerationBinding, decide,
)
from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT, V6OwnerActivityGate
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer
from backend.app.ports.session_attempts import AttemptDenied
from backend.tests.test_v6_token_grant import _gate


class SimulatedRenderer:
    identity = DEEPSEEK_RENDERER_IDENTITY
    def __call__(self, body):
        return 'SIMULATED framing:' + body['messages'][0]['content']


class SimulatedCounter(DeepseekV41TextCapacityCounter):
    execution_kind = 'SIMULATED'
    def __init__(self):
        super().__init__(SimulatedRenderer())
        self.wires = []
    def measure(self, provider, model, body, *, scope):
        self.wires.append(json.dumps(body).encode('utf-8'))
        return super().measure(provider, model, body, scope=scope)


class SimulatedUsageGuard:
    def __init__(self):
        self.reservations, self.settlements = [], []
    def reserve(self, **kwargs):
        self.reservations.append(kwargs)
        return len(self.reservations)
    def settle(self, reservation, usage, *, sent):
        self.settlements.append((reservation, usage, sent))


@pytest.fixture(autouse=True)
def no_live_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Real transport is forbidden in SIMULATED contracts')
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener', forbidden)


def envelope(prompt='SIMULATED public question'):
    return GenerationEnvelope(prompt, prompt, prompt, 'SIMULATED-context', (), (), 512, 10)


def response(**changes):
    body = {'model': 'deepseek-flash',
            'choices': [{'message': {'content': 'SIMULATED answer'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
    body.update(changes)
    return body


def setup_binding(tmp_path, *, result=None, token_limit=1_000_000):
    gate, path = _gate(tmp_path, token_limit=token_limit)
    counter, guard, wires = SimulatedCounter(), SimulatedUsageGuard(), []
    def send(request, timeout):
        wires.append(request.data)
        if isinstance(result, Exception):
            raise result
        return response() if result is None else result
    factory = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('chat_expensive',))
    gateway = factory.build_v6_gateway(activity_gate=gate, gate_types=DEEPSEEK_GATE_TYPES,
        api_key='SIMULATED-ONLY', transport=SimulatedDeepSeekTransport(send))
    binding = V6GenerationBinding(gate.grant, gate, counter)
    role = GenerationRole('chat_expensive', gateway, usage_guard=guard, v6_binding=binding)
    return role, gate, path, counter, guard, wires


def invoke(role, env=None, request_id='SIMULATED-v6-q01'):
    return role.invoke(env or envelope(), request_id=request_id, run_id='SIMULATED-cohort',
                       scope=role.v6_binding.grant.scope)


def activity(path):
    return json.loads(path.read_text(encoding='utf-8'))['v6_owner_activity']


def test_exact_final_wire_and_single_normal_usage_reservation(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    before = json.loads(path.read_text(encoding='utf-8'))
    assert invoke(role) == 'SIMULATED answer'
    row = activity(path)['attempts'][0]
    assert wires == counter.wires and len(wires) == 1
    assert tuple(json.loads(wires[0])) == ('model', 'messages', 'thinking', 'max_tokens', 'temperature', 'stream')
    assert json.loads(wires[0])['temperature'] == 0
    assert row['request_budget']['wire_request_sha256'] == hashlib.sha256(wires[0]).hexdigest()
    assert role.gateway.last_receipt['wire_request_sha256'] == row['request_budget']['wire_request_sha256']
    assert role.gateway.last_receipt['response_actual_model'] == 'deepseek-flash'
    assert row['validation_tokens']['state'] == 'settled' and gate._used(json.loads(path.read_text())) == 65_584
    assert len(guard.reservations) == len(guard.settlements) == 1
    assert guard.reservations[0]['planned_tokens'] == row['validation_tokens']['reserved_input'] + 512
    after = json.loads(path.read_text(encoding='utf-8'))
    for key in before.keys() - {'v6_owner_activity'}:
        assert after[key] == before[key]


@pytest.mark.parametrize('field,value', [
    ('scope', 'wrong'), ('provider', 'siliconflow'), ('model', 'deepseek-v4-pro'),
    ('output_reservation', 896), ('context_window', 4096),
    ('prompt_text', 'another prompt'), ('wire_request_sha256', '0' * 64),
])
def test_counter_binding_mismatch_denied_before_both_reservations(tmp_path, field, value):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    measure = counter.measure
    counter.measure = lambda *a, **k: replace(measure(*a, **k), **{field: value})
    with pytest.raises(ProviderRequestNotSent):
        invoke(role)
    assert activity(path)['attempts'] == [] and guard.reservations == wires == []


@pytest.mark.parametrize('field,value', [
    ('capacity_source', 'UNKNOWN'), ('tokenizer_source', 'UNKNOWN'), ('window_tokens', 4096),
    ('input_token_bound', True), ('input_token_bound', 0), ('output_token_bound', 896),
    ('protocol_id', 'different-protocol'), ('activity_id', 'other'), ('scope', 'other'),
])
def test_invalid_budget_proof_stays_closed(tmp_path, monkeypatch, field, value):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    from backend.app.adapters.models.v6_text_capacity import TextCapacityEstimate
    original = TextCapacityEstimate.to_token_request_budget
    monkeypatch.setattr(TextCapacityEstimate, 'to_token_request_budget',
        lambda self, **kwargs: replace(original(self, **kwargs), **{field: value}))
    with pytest.raises(ProviderRequestNotSent):
        invoke(role)
    assert activity(path)['attempts'] == [] and guard.reservations == wires == []


@pytest.mark.parametrize('change', ['bool', 'wrong_grant', 'wrong_gate', 'cheap', 'unmarked_counter'])
def test_no_bool_or_misbound_grant_bypass(tmp_path, change):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    binding = role.v6_binding
    if change == 'bool':
        role = replace(role, v6_binding=True)
    elif change == 'wrong_grant':
        role = replace(role, v6_binding=replace(binding, grant=replace(binding.grant, scope='other')))
    elif change == 'wrong_gate':
        role = replace(role, v6_binding=replace(binding, activity_gate=object()))
    elif change == 'cheap':
        role = replace(role, role='chat_cheap')
    else:
        role = replace(role, v6_binding=replace(binding, capacity_counter=DeepseekV41TextCapacityCounter(SimulatedRenderer())))
    with pytest.raises(ProviderRequestNotSent):
        role.invoke(envelope(), request_id='SIMULATED-invalid', run_id='SIMULATED', scope=gate.grant.scope)
    assert activity(path)['attempts'] == [] and guard.reservations == wires == []


@pytest.mark.parametrize('scope', ['other', None, ('kb',)])
def test_exact_public_activity_scope_required(tmp_path, scope):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    with pytest.raises(ProviderRequestNotSent, match='V6_EVALUATION_SCOPE_REQUIRED'):
        role.invoke(envelope(), request_id='SIMULATED-scope', run_id='SIMULATED', scope=scope)
    assert guard.reservations == wires == [] and activity(path)['attempts'] == []


@pytest.mark.parametrize('changes', [
    {'max_tokens': 896}, {'max_tokens': True}, {'timeout_seconds': 121},
    {'prompt': 'literal <think>'}, {'timeout_seconds': 0},
])
def test_request_output_timeout_special_literals(tmp_path, changes):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    with pytest.raises(ProviderRequestNotSent):
        invoke(role, replace(envelope(), **changes))
    assert guard.reservations == wires == [] and activity(path)['attempts'] == []


def test_deadline_denial_before_reservations(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    gate.clock = lambda: datetime(2026, 10, 10, 4, 25, 40, tzinfo=timezone.utc)
    with pytest.raises(ProviderRequestNotSent, match='V6_REQUEST_BOUND_INVALID'):
        invoke(role)
    assert guard.reservations == wires == [] and activity(path)['attempts'] == []


@pytest.mark.parametrize('reported', ['deepseek-v4-pro', None, 'UNKNOWN'])
def test_response_model_identity_mismatch_retains_unknown(tmp_path, reported):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path, result=response(model=reported))
    with pytest.raises(ProviderUnavailable, match='ROUTER_MODEL_RESPONSE_MISMATCH'):
        invoke(role)
    assert len(wires) == 1 and activity(path)['attempts'][0]['validation_tokens']['state'] == 'unknown'
    assert guard.settlements[0][2] is True


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': True, 'completion_tokens': 5},
    {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 999},
    {'prompt_tokens': 10, 'completion_tokens': -1}])
def test_usage_missing_invalid_keeps_reservation_and_fails(tmp_path, usage):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path, result=response(usage=usage))
    with pytest.raises(ProviderUnavailable, match='SESSION_LEDGER_SETTLEMENT_FAILED'):
        invoke(role)
    assert len(wires) == 1 and activity(path)['attempts'][0]['validation_tokens']['state'] == 'unknown'
    assert guard.settlements[0][2] is True


def test_usage_overrun_blocks_next_send_without_new_callcap(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path,
        result=response(usage={'prompt_tokens': 10000, 'completion_tokens': 5, 'total_tokens': 10005}))
    with pytest.raises(ProviderUnavailable):
        invoke(role)
    assert activity(path)['blocked_reason'] == 'V6_USAGE_BOUND_EXCEEDED'
    with pytest.raises(ProviderRequestNotSent, match='V6_ACTIVITY_BLOCKED'):
        invoke(role, request_id='SIMULATED-next')
    assert len(wires) == 1 and guard.settlements[-1][2] is False


@pytest.mark.parametrize('failure', [TimeoutError('SIMULATED timeout'), ValueError('SIMULATED malformed')])
def test_timeout_unknown_no_retry(tmp_path, failure):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path, result=failure)
    with pytest.raises(ProviderUnavailable, match='DEEPSEEK_REQUEST_FAILED'):
        invoke(role)
    row = activity(path)['attempts'][0]
    assert row['status'] == 'unknown' and row['validation_tokens']['state'] == 'unknown'
    assert len(wires) == 1 and guard.settlements[0][2] is True


def test_length_keeps_full_reservation(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path,
        result=response(choices=[{'message': {'content': 'SIMULATED partial'}, 'finish_reason': 'length'}]))
    with pytest.raises(TruncatedAnswer):
        invoke(role)
    assert activity(path)['attempts'][0]['validation_tokens']['state'] == 'unknown'
    assert guard.settlements[0][2] is True and len(wires) == 1


def test_not_sent_after_atomic_reserve_releases_only_v6_and_monthly(tmp_path, monkeypatch):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    monkeypatch.setattr(gate, 'validate_before_send',
        lambda _: (_ for _ in ()).throw(AttemptDenied('SIMULATED pre-send rejection')))
    with pytest.raises(ProviderRequestNotSent):
        invoke(role)
    row = activity(path)['attempts'][0]
    assert row['status'] == 'not_sent' and row['validation_tokens']['state'] == 'released'
    assert wires == [] and guard.settlements[0][2] is False
    assert gate._used(json.loads(path.read_text())) == 65_569
    with pytest.raises(ProviderRequestNotSent, match='SESSION_REQUEST_ALREADY_RESERVED'):
        invoke(role)
    assert len(activity(path)['attempts']) == 1


def test_duplicate_concurrent_request_reaches_transport_once(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    def run(_):
        try:
            return invoke(role)
        except ProviderRequestNotSent:
            return 'DENIED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results.count('SIMULATED answer') == results.count('DENIED') == 1
    assert len(wires) == len(activity(path)['attempts']) == 1


def test_cumulative_token_cap_rejection_releases_monthly(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path, token_limit=65_570)
    with pytest.raises(ProviderRequestNotSent, match='V6_TOKEN_CAP'):
        invoke(role)
    assert guard.settlements[0][2] is False and wires == [] and activity(path)['attempts'] == []


def test_prepared_wire_tampering_denied_without_reservation(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    prepared = role.gateway.prepare_v6_request(envelope().prompt, 10, 512,
        grant=gate.grant, scope=gate.grant.scope, capacity_counter=counter)
    prepared = replace(prepared, wire_bytes=prepared.wire_bytes + b' ')
    with pytest.raises(ProviderRequestNotSent, match='V6_WIRE_REQUEST_BINDING_MISMATCH'):
        role.gateway.answer_with_v6_scope(envelope().prompt, 10, 512,
            prepared_request=prepared, scope=gate.grant.scope, request_id='SIMULATED-tamper', cloud_authorized=True)
    assert activity(path)['attempts'] == [] and wires == []


def test_unissued_typed_preflight_cannot_bypass_counter(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    prepared = role.gateway.prepare_v6_request(envelope().prompt, 10, 512,
        grant=gate.grant, scope=gate.grant.scope, capacity_counter=counter)
    forged = replace(prepared, budget=replace(prepared.budget, input_token_bound=1))
    with pytest.raises(ProviderRequestNotSent, match='V6_PREPARED_REQUEST_NOT_ISSUED'):
        role.gateway.answer_with_v6_scope(envelope().prompt, 10, 512,
            prepared_request=forged, scope=gate.grant.scope, request_id='SIMULATED-forged', cloud_authorized=True)
    assert activity(path)['attempts'] == [] and wires == []


def test_generic_bool_authorization_cannot_admit_v6(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    with pytest.raises(ProviderRequestNotSent, match='V6_REQUEST_BUDGET_REQUIRED'):
        role.gateway.answer_with_budget(envelope().prompt, 10, 512, cloud_authorized=True)
    assert activity(path)['attempts'] == [] and wires == []


def test_prepared_capability_cannot_be_replayed_under_new_request_id(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    prepared = role.gateway.prepare_v6_request(envelope().prompt, 10, 512,
        grant=gate.grant, scope=gate.grant.scope, capacity_counter=counter)
    kwargs = dict(prepared_request=prepared, scope=gate.grant.scope, cloud_authorized=True)
    assert role.gateway.answer_with_v6_scope(envelope().prompt, 10, 512,
        request_id='SIMULATED-first', **kwargs) == 'SIMULATED answer'
    with pytest.raises(ProviderRequestNotSent, match='V6_PREPARED_REQUEST_NOT_ISSUED'):
        role.gateway.answer_with_v6_scope(envelope().prompt, 10, 512,
            request_id='SIMULATED-replay', **kwargs)
    assert len(activity(path)['attempts']) == len(wires) == 1


@pytest.mark.parametrize('kind', ['credential', 'egress', 'monthly'])
def test_other_admission_guards_are_preserved(tmp_path, kind):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    if kind == 'credential':
        role.gateway._api_key = ''
    elif kind == 'egress':
        role.gateway.cloud_enabled = False
    else:
        guard.reserve = lambda **k: (_ for _ in ()).throw(ValueError('SIMULATED budget denied'))
    with pytest.raises(ProviderRequestNotSent):
        invoke(role)
    assert wires == [] and activity(path)['attempts'] == []
    if kind != 'monthly':
        assert guard.settlements[0][2] is False


def test_v6_monthly_settlement_failure_preserves_not_sent(tmp_path):
    from backend.app.application.rule_router_v1 import GenerationSettlementFailure
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    role.gateway.cloud_enabled = False
    guard.settle = lambda *a, **k: (_ for _ in ()).throw(OSError('SIMULATED receipt failure'))
    with pytest.raises(GenerationSettlementFailure) as failure:
        invoke(role)
    assert failure.value.not_sent is True and failure.value.primary_outcome == 'NOT_SENT'
    assert wires == [] and activity(path)['attempts'] == []


def test_simulated_grant_without_fixture_transport_stays_zero_send(tmp_path):
    role, gate, path, counter, guard, wires = setup_binding(tmp_path)
    gateway = DeepSeekGateway(api_key='SIMULATED', cloud_enabled=True, attempt_gate=gate, gate_types=DEEPSEEK_GATE_TYPES)
    role = replace(role, gateway=gateway)
    with pytest.raises(ProviderRequestNotSent, match='V6_EXECUTION_KIND_MISMATCH'):
        invoke(role)
    assert wires == guard.reservations == [] and activity(path)['attempts'] == []


def test_simulated_fixture_is_never_production_counter_proof(tmp_path, monkeypatch):
    # SIMULATED inert real-grant shape; no canonical path is opened or initialized.
    gate = object.__new__(V6OwnerActivityGate)
    gate.grant = OWNER_V6_TOKEN_GRANT
    gate.validate_request = lambda *a, **k: None
    gateway = DeepSeekGateway(api_key='SIMULATED', cloud_enabled=True, attempt_gate=gate, gate_types=DEEPSEEK_GATE_TYPES)
    with pytest.raises(ProviderRequestNotSent, match='V6_PRODUCTION_COUNTER_UNVERIFIED'):
        gateway.prepare_v6_request(envelope().prompt, 10, 512, grant=gate.grant,
            scope=gate.grant.scope, capacity_counter=SimulatedCounter())
    official = DeepseekV41TextCapacityCounter(DeepseekV41RecipeRenderer())
    simulated_estimate = SimulatedCounter().measure('deepseek', 'deepseek-flash',
        {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': envelope().prompt}],
         'thinking': {'type': 'disabled'}, 'max_tokens': 512, 'temperature': 0, 'stream': False}, scope=gate.grant.scope)
    monkeypatch.setattr(official, 'measure', lambda *a, **k: simulated_estimate)
    with pytest.raises(ProviderRequestNotSent, match='V6_PRODUCTION_COUNTER_UNVERIFIED'):
        gateway.prepare_v6_request(envelope().prompt, 10, 512, grant=gate.grant,
            scope=gate.grant.scope, capacity_counter=official)


def test_normal_factory_and_startup_never_initialize_grant(tmp_path, monkeypatch):
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    monkeypatch.setattr(V6OwnerActivityGate, 'initialize_grant',
        lambda _: pytest.fail('Normal startup must not initialize V6'))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'SIMULATED-ONLY')
    factory = ProviderFactory(ModelRegistry.frozen_defaults())
    cheap = factory.build('chat_cheap')
    assert (cheap.provider_name, cheap.spec.model_id) == MODELS['chat_cheap']
    container = build_container(Settings(database_url='sqlite+pysqlite:///:memory:',
        storage_root=tmp_path, rule_router_enabled=True), model=None)
    assert container.quick_chain.generation_roles['chat_expensive'].v6_binding is None
    with pytest.raises(ProviderRequestNotSent):
        container.quick_chain.generation_roles['chat_expensive'].check()


def test_three_arm_identity_threshold_and_cheap_capacity_remain_closed(tmp_path):
    assert RouterPolicy().threshold.as_tuple() == RouterPolicy(threshold=RouterPolicy().threshold).threshold.as_tuple()
    assert str(RouterPolicy().threshold) == '0.35'
    for mode, expected in [('CHEAP_ONLY', 'chat_cheap'), ('EXPENSIVE_ONLY', 'chat_expensive')]:
        assert decide('SIMULATED question', 'SIMULATED prompt', RouterPolicy(mode=mode)).role == expected
    cheap = ProviderFactory(ModelRegistry.frozen_defaults()).build('chat_cheap')
    with pytest.raises(ProviderRequestNotSent, match='P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY'):
        GenerationRole('chat_cheap', cheap).check()
    from backend.app.adapters.models.v6_text_capacity import CapacityCounterUnavailable
    with pytest.raises(CapacityCounterUnavailable, match='SILICONFLOW_HOSTED_TEMPLATE_UNVERIFIED'):
        SimulatedCounter().measure('siliconflow', MODELS['chat_cheap'][1], {}, scope='SIMULATED')
