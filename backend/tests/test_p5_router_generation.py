"""SIMULATED providers + real application validators and normal budget guard."""
from decimal import Decimal
import json
import pytest
from backend.app.adapters.models.cloud import CloudChat
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.application.rule_router_v1 import RouterPolicy, GenerationRole, MODELS, whole_abstention
from backend.app.application.run_metrics import collect_metrics
from backend.app.domain.models import ChunkRecord
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer
from backend.app.ports.model_usage import record_call

GOOD = "A方案成本1000元 [E1]"


class SimulatedDeepSeek:
    provider_name, chat_model = MODELS['chat_expensive']
    execution_kind = 'SIMULATED'
    def __init__(self, result=GOOD):
        self.result = result
        self.calls = []
    def answer_with_product_scope(self, prompt, timeout, max_tokens, **kwargs):
        self.calls.append((prompt, timeout, max_tokens, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        record_call(model=self.chat_model, status='ok', response={'prompt_eval_count':12,'eval_count':8,'done_reason':'stop'},latency_ms=1)
        return self.result


def make_chain(monkeypatch, *, cheap=GOOD, expensive=GOOD, policy=None, budget=100,
               contents=("A方案成本1000元。",), after_cheap=None):
    repo = InMemoryRetrievalRepository()
    for i, content in enumerate(contents):
        repo.add(ChunkRecord(f'c{i}', 'kb', f'doc{i}', 'v1', content, {'start':0}))
    registry = ModelRegistry.frozen_defaults()
    gate = InMemoryBudgetGate(budget)
    cheap_spec, expensive_spec = registry.select('chat_cheap'), registry.select('chat_expensive')
    captured = []
    def transport(request, timeout):
        captured.append(json.loads(request.data))
        if after_cheap:
            after_cheap()
        if isinstance(cheap, Exception):
            raise cheap
        return {'model':cheap_spec.model_id, 'choices':[{'message':{'content':cheap},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18}}
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-P5-KEY')
    cheap_gateway = CloudChat(cheap_spec, enabled=True, usage_guard=BudgetUsageGuard(gate, cheap_spec, 5), transport=transport)
    cheap_gateway.execution_kind = 'SIMULATED'
    exp = SimulatedDeepSeek(expensive)
    roles = {'chat_cheap': GenerationRole('chat_cheap', cheap_gateway, simulated=True),
             'chat_expensive': GenerationRole('chat_expensive', exp, simulated=True,
                 usage_guard=BudgetUsageGuard(gate, expensive_spec, 9))}
    chain = LangChainQuickChain(KnowledgeGateway(HybridRetriever(repo)),
        router_policy=policy or RouterPolicy(mode='DYNAMIC'), generation_roles=roles)
    from backend.tests.p55a_simulated_lifecycle import install_simulated_lifecycle
    install_simulated_lifecycle(chain)
    return chain, gate, captured, exp


def invoke(chain, *, run_id='p5-sim', question='A 的成本是多少？', allowed=True, cancel=None):
    with collect_metrics(run_id) as metrics:
        result = chain.invoke(question, Scope.from_ids(['kb']), run_id=run_id,
            settings=QuickSettings(cloud_enabled=True), cloud_allowed_by_kb={'kb':allowed}, is_cancelled=cancel)
        return result, metrics.snapshot(citations=result.citations, error=result.error_code)


def test_cheap_success_once_no_double_reservation_or_source_change(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    result, metrics = invoke(chain)
    assert result.error_code is None and result.answer == GOOD and result.citations == ('E1',)
    assert len(cheap) == 1 and exp.calls == [] and len(gate.reservations) == 1
    assert next(iter(gate.reservations.values()))['state'] == 'unknown'
    assert result.evidence[0].quote == 'A方案成本1000元。' and result.evidence[0].version_id == 'v1'
    row = metrics['rule_router']
    assert row['estimated_input_tokens'] is None and row['net_saving'] == 'NOT_AVAILABLE'
    assert row['attempts'][0]['actual_tokens_or_UNKNOWN'][0] == {'input':11,'output':7}
    assert 'SIMULATED-P5-KEY' not in json.dumps(metrics)
    assert 'A 的成本' not in json.dumps(row, ensure_ascii=False)


@pytest.mark.parametrize('candidate,status,validation,finish,send', [
    (GOOD, 'COMPLETED', 'PASS', 'stop', 'RESPONSE_RECEIVED'),
    ('bad [E99]', 'COMPLETED', 'INVALID_CITATION', 'stop', 'RESPONSE_RECEIVED'),
    (TruncatedAnswer('partial [E1]'), 'TRUNCATED', 'NOT_RUN', 'length', 'RESPONSE_RECEIVED'),
    (ProviderRequestNotSent('pretransport'), 'NOT_SENT', 'NOT_RUN', 'UNKNOWN', 'NOT_SENT'),
    (TimeoutError('secret'), 'FAILED', 'NOT_RUN', 'UNKNOWN', 'UNKNOWN'),
])
def test_owner_r1_generation_states_separate_validation_and_settlement(monkeypatch, candidate, status, validation, finish, send):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap=candidate, policy=RouterPolicy(mode='CHEAP_ONLY'))
    result, metrics = invoke(chain)
    a = metrics['rule_router']['attempts'][0]
    assert a['generation_status'] == status and a['result_validation'] == validation
    assert a['finish_reason'] == finish and a['send_status'] == send
    assert a['settlement_status'] == ('NOT_SENT' if send == 'NOT_SENT' else 'UNKNOWN')
    assert a['actual_cost'] == 'UNKNOWN' and len(cheap) == 1 and exp.calls == []
    if status == 'TRUNCATED':
        assert result.error_code == 'MODEL_OUTPUT_TRUNCATED'
        assert result.trace.model_calls == 1


def test_owner_r1_response_received_settlement_failure_is_not_success(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    monkeypatch.setattr(chain.generation_roles['chat_cheap'].gateway.usage_guard, 'settle',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('private path')))
    result, metrics = invoke(chain)
    a = metrics['rule_router']['attempts'][0]
    assert result.error_code and a['generation_status'] == 'FAILED'
    assert a['settlement_operation'] == 'FAILED'
    assert a['settlement_status'] == 'UNKNOWN' and a['result_validation'] == 'NOT_RUN'
    assert len(cheap) == 1 and exp.calls == []
    assert all(r['state'] == 'reserved' for r in gate.reservations.values())
    assert 'private path' not in json.dumps(metrics)


@pytest.mark.parametrize('primary,outcome,not_sent', [
    (GOOD, 'COMPLETED', False),
    (ProviderRequestNotSent('private reason'), 'NOT_SENT', True),
    (TruncatedAnswer('partial secret'), 'TRUNCATED', False),
    (TimeoutError('private reason'), 'PROVIDER_FAILURE', False),
])
def test_owner_r1_expensive_settlement_failure_preserves_primary_outcome(monkeypatch, primary, outcome, not_sent):
    chain, gate, cheap, exp = make_chain(monkeypatch, expensive=primary, policy=RouterPolicy(mode='EXPENSIVE_ONLY'))
    monkeypatch.setattr(chain.generation_roles['chat_expensive'].usage_guard, 'settle',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('private path')))
    result, metrics = invoke(chain)
    a = metrics['rule_router']['attempts'][0]
    assert result.error_code and a['settlement_operation'] == 'FAILED'
    assert a['settlement_status'] == 'UNKNOWN' and a['generation_status'] == 'FAILED'
    assert a['primary_outcome_before_settlement_failure'] == outcome
    assert a['send_status'] == ('NOT_SENT' if not_sent else 'UNKNOWN')
    assert result.trace.model_calls == int(not not_sent)
    assert a['result_validation'] == 'NOT_RUN'
    assert cheap == [] and len(exp.calls) == 1
    assert all(r['state'] == 'reserved' for r in gate.reservations.values())
    assert 'private' not in json.dumps(metrics) and 'partial secret' not in json.dumps(metrics)


def test_owner_r1_validator_exception_is_not_provider_failure(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    monkeypatch.setattr(chain.knowledge_gateway.evidence.hardening, 'check_candidate',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('private validation')))
    result, metrics = invoke(chain)
    a = metrics['rule_router']['attempts'][0]
    assert result.error_code and a['generation_status'] == 'COMPLETED'
    assert a['send_status'] == 'RESPONSE_RECEIVED' and a['finish_reason'] == 'stop'
    assert a['result_validation'] == 'VALIDATION_ERROR' and a['error_class'] == 'CandidateValidationFailure'
    assert metrics['rule_router']['provider_failure_reason'] is None
    assert len(cheap) == 1 and exp.calls == []


@pytest.mark.parametrize('fallback', [False, True])
def test_owner_r11_validator_internal_error_has_own_code_no_fallback(monkeypatch, fallback):
    chain, gate, cheap, exp = make_chain(monkeypatch,
        policy=RouterPolicy(mode='DYNAMIC', provider_fallback=fallback, abstention_escalation=True),
        cheap='证据不足')
    monkeypatch.setattr(chain.knowledge_gateway.evidence.hardening, 'check_candidate',
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('private validator payload')))
    result, metrics = invoke(chain)
    assert result.error_code == 'CANDIDATE_VALIDATION_INTERNAL_ERROR' and result.answer == ''
    attempt = metrics['rule_router']['attempts'][0]
    assert attempt['generation_status'] == 'COMPLETED' and attempt['result_validation'] == 'VALIDATION_ERROR'
    assert attempt['send_status'] == 'RESPONSE_RECEIVED' and attempt['finish_reason'] == 'stop'
    assert metrics['rule_router']['provider_failure_reason'] is None
    assert metrics['rule_router']['escalation_reason'] is None
    assert len(cheap) == 1 and exp.calls == [] and len(gate.reservations) == 1
    assert all(r['state'] == 'unknown' for r in gate.reservations.values())
    assert 'private validator payload' not in str(result) + json.dumps(metrics)


@pytest.mark.parametrize('candidate', [GOOD, 'bad [E99]'])
def test_owner_r11_validator_internal_error_cannot_be_overridden_by_finalize(monkeypatch, candidate):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap=candidate)
    audits = []
    class SavedLocalAudit:
        def save(self, row):
            audits.append(row)
            return True
    chain.knowledge_gateway.evidence.hardening.audit = SavedLocalAudit()
    monkeypatch.setattr(chain.knowledge_gateway.evidence.hardening, 'check_candidate',
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('private validator payload')))
    result, metrics = invoke(chain)
    assert result.error_code == 'CANDIDATE_VALIDATION_INTERNAL_ERROR' and result.answer == ''
    assert audits == []  # no rejected-candidate fallback can hide this internal failure
    assert len(cheap) == 1 and exp.calls == []
    assert all(r['state'] == 'unknown' for r in gate.reservations.values())
    assert 'private validator payload' not in str(result) + json.dumps(metrics)


def test_direct_expensive_once(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch, policy=RouterPolicy(mode='DYNAMIC', threshold=Decimal('0')))
    result, row = invoke(chain)
    assert result.error_code is None and cheap == [] and len(exp.calls) == 1
    assert len(gate.reservations) == 1
    assert exp.calls[0][3]['cloud_authorized'] is True
    assert exp.calls[0][3]['run_id'] == 'p5-sim'


@pytest.mark.parametrize('bad', ['A方案成本1000元 [E99]', 'A方案成本2000元 [E1]', '无引用的事实'])
def test_raw_quality_error_upgrades_once_same_envelope(monkeypatch,bad):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap=bad)
    result, metrics = invoke(chain)
    assert result.error_code is None and result.answer == GOOD
    assert len(cheap) == len(exp.calls) == 1 and len(gate.reservations) == 2
    assert cheap[0]['messages'][0]['content'] == exp.calls[0][0]
    assert bad not in exp.calls[0][0]
    row = metrics['rule_router']
    assert row['escalation_reason'] == 'QUALITY_ESCALATION'
    assert row['attempts'][0]['envelope_identity'] == row['attempts'][1]['envelope_identity']
    assert row['attempts'][0]['attempt_id'] != row['attempts'][1]['attempt_id']
    assert result.trace.model_calls == 2


def test_second_invalid_never_third_or_success(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap='bad', expensive='bad [E99]')
    result, _ = invoke(chain)
    assert result.error_code == 'INVALID_CITATION' and result.answer == ''
    assert len(cheap) == len(exp.calls) == 1


@pytest.mark.parametrize('failure', [TimeoutError('secret text'),ProviderUnavailable('429 secret text'),
    ProviderRequestNotSent('pretransport'), TruncatedAnswer('partial [E1]')])
def test_provider_failure_no_default_upgrade_preserves_unknown(monkeypatch,failure):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap=failure)
    result, metrics = invoke(chain)
    assert result.error_code and exp.calls == [] and len(cheap) == 1
    assert 'secret text' not in json.dumps(metrics)
    state = next(iter(gate.reservations.values()))['state']
    assert state == ('released' if isinstance(failure, ProviderRequestNotSent) else 'unknown')


def test_provider_fallback_separate_opt_in_once(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap=TimeoutError(), policy=RouterPolicy(mode='DYNAMIC',provider_fallback=True))
    result, row = invoke(chain)
    assert result.error_code is None and len(exp.calls) == 1
    assert row['rule_router']['escalation_reason'] == 'PROVIDER_FALLBACK'
    assert all(v['state'] == 'unknown' for v in gate.reservations.values())


@pytest.mark.parametrize('enabled', [False,True])
def test_whole_abstention_separate_default_off(monkeypatch,enabled):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap='没有足够证据', policy=RouterPolicy(mode='DYNAMIC',abstention_escalation=enabled))
    result, row = invoke(chain)
    assert len(exp.calls) == int(enabled)
    assert row['rule_router']['escalation_reason'] == ('ABSTENTION_ESCALATION' if enabled else None)


@pytest.mark.parametrize('answer', ['引文：“证据不足” [E1]','A有证据；B证据不足','部分无法回答，A成立 [E1]'])
def test_quoted_or_partial_refusal_not_whole(answer):
    assert not whole_abstention(answer)


@pytest.mark.parametrize('contents,question,allowed', [
    ((), '没有命中', True), (('A方案成本1000元。',),'A 与 B 两种方案各自的成本是多少？',True),
    (('A方案成本1000元。',), 'A 的成本是多少？',False)])
def test_upstream_gate_zero_generation(monkeypatch,contents,question,allowed):
    chain, gate, cheap, exp = make_chain(monkeypatch,contents=contents)
    result, metrics = invoke(chain, question=question,allowed=allowed)
    assert cheap == exp.calls == [] and gate.reservations == {}
    assert metrics['rule_router'] is None


def test_cancel_between_attempts_never_second(monkeypatch):
    cancelled = [False]
    chain, gate, cheap, exp = make_chain(monkeypatch,cheap='bad [E99]',after_cheap=lambda:cancelled.__setitem__(0,True))
    result, _ = invoke(chain, cancel=lambda:cancelled[0])
    assert result.error_code == 'CANCELLED' and len(cheap) == 1 and exp.calls == []


def test_duplicate_identity_no_extra_request(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    invoke(chain)
    result, _ = invoke(chain)
    assert result.error_code == 'DUPLICATE_REQUEST' and len(cheap) == 1


@pytest.mark.parametrize('failure', ['budget','identity','real'])
def test_pretransport_fail_closed(monkeypatch,failure):
    chain, gate, cheap, exp = make_chain(monkeypatch,budget=0 if failure=='budget' else 100)
    if failure=='identity':
        chain.generation_roles['chat_cheap'].gateway.provider_name='other'
    if failure=='real':
        chain.generation_roles['chat_cheap'] = GenerationRole('chat_cheap',chain.generation_roles['chat_cheap'].gateway)
    result, row = invoke(chain)
    assert result.error_code and cheap == exp.calls == [] and gate.reservations == {}
    assert result.trace.model_calls == 0


def test_input_capacity_not_invented_real_blocked_before_reserve(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    chain.generation_roles['chat_expensive'] = GenerationRole('chat_expensive',exp,usage_guard=chain.generation_roles['chat_expensive'].usage_guard)
    chain.router_policy = RouterPolicy(mode='DYNAMIC',threshold=Decimal('0'))
    result, row = invoke(chain)
    assert result.error_code == 'P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY'
    assert not gate.reservations and cheap == exp.calls == []


def test_unchecked_confident_error_is_documented_undetected(monkeypatch):
    # Existing checks do not establish general semantic entailment.
    chain, gate, cheap, exp = make_chain(monkeypatch,cheap='月亮是奶酪做的 [E1]', contents=('月亮是石头做的。',))
    result, row = invoke(chain,question='月亮是什么')
    assert result.error_code is None and exp.calls == []  # UNDETECTED limitation
