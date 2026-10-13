"""SIMULATED framing and offline application checks; no hosted capacity claims."""
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import pytest
from backend.app.application.answer_hardening import detect_intents, ordinary_generation_budget
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.adapters.models.cloud import CloudChat
from backend.app.adapters.models.service_capacity import ServiceCapacity
from backend.app.adapters.models.v6_text_capacity import OrdinaryDeepseekV41TextCapacityCounter, DeepseekV41TextCapacityCounter, CapacityCounterUnavailable, DEEPSEEK_RENDERER_IDENTITY
from backend.app.ports.providers import TruncatedAnswer, ProviderRequestNotSent

@pytest.mark.parametrize('question,tier,cap', [
    ('缓存是什么？', 'SHORT', 1024),
    ('缓存是什么？备份如何操作？', 'STANDARD', 2048),
    ('详细回答：缓存是什么？', 'LONG', 4096),
    ('Compare the approaches', 'LONG', 4096),
    ('Explain step by step', 'LONG', 4096),
    ('Explain the principle', 'STANDARD', 2048),
    ('How should I study?', 'STANDARD', 2048),
    ('请给出完整步骤和代码', 'LONG', 4096),
    ('简短回答：缓存是什么？备份如何操作？', 'SHORT', 1024),
])
def test_tiers_use_explicit_length_and_intents_only(question, tier, cap):
    budget = ordinary_generation_budget(detect_intents(question))
    assert (budget.complexity, budget.max_tokens, budget.max_calls) == (tier, cap, 1)
    with pytest.raises(FrozenInstanceError):
        budget.max_tokens = 512


def test_prompt_and_envelope_share_complete_budget():
    bundle = SimpleNamespace(context='事实原文', labels=('E1',), snapshots=())
    settings = QuickSettings(ordinary_output_budget=True, concise_budgeted_answer=True)
    prompt, budget = LangChainQuickChain.render_answer_prompt('缓存是什么？', bundle, settings)
    assert budget.max_tokens == 1024
    assert '1024 tokens' in prompt
    assert '180 Chinese' not in prompt and '100 English' not in prompt and 'three most' not in prompt
    chain = LangChainQuickChain(None)
    envelope, _, _, _ = chain.render_generation_envelope({'bundle':bundle, 'settings':settings, 'question':'缓存是什么？'}, prompt, budget.max_tokens)
    assert envelope.max_tokens == budget.max_tokens
    with pytest.raises(ValueError, match='CONFLICT'):
        QuickSettings(ordinary_output_budget=True, answer_output_token_cap=512)


@pytest.mark.parametrize('question,cap', [
    ('请简短概括学习方法', 1024),
    ('我应该怎么学习', 2048),
    ('请详细解释学习方法', 4096),
])
@pytest.mark.parametrize('concise', [False, True])
def test_ordinary_tiers_retain_unit_witness_instruction(question, cap, concise):
    # The live q0 candidate invented days for an explicitly unitless list.
    # Increasing the output budget must retain this evidence constraint.
    source = '间隔时间为1，7，16，35 / 固定间隔'
    bundle = SimpleNamespace(context=source, labels=('E1',), snapshots=())
    prompt, budget = LangChainQuickChain.render_answer_prompt(
        question, bundle, QuickSettings(ordinary_output_budget=True, concise_budgeted_answer=concise))
    assert budget.max_tokens == cap
    assert prompt.index('Do not add units to numbers') > prompt.index(source)
    assert 'leave such values unitless' in prompt
    assert '180 Chinese' not in prompt and '100 English' not in prompt


class SimulatedRenderer:
    identity = DEEPSEEK_RENDERER_IDENTITY
    def __call__(self, body):
        return '<user>' + body['messages'][0]['content'] + '<assistant>'


def body(output=1024):
    return dict(model='deepseek-flash', messages=[{'role':'user','content':'??'}], thinking={'type':'disabled'}, max_tokens=output, temperature=0, stream=False)


def test_local_1024_measurement_matches_wire_and_reservation_and_preserves_frozen_contract():
    counter = OrdinaryDeepseekV41TextCapacityCounter(SimulatedRenderer())
    payload = body()
    measured = counter.measure('deepseek', 'deepseek-flash', payload, scope='SIMULATED')
    import hashlib
    assert measured.wire_request_sha256 == hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    assert measured.output_reservation == 1024 and measured.production_ready is False
    assert counter.endpoint_output_evidence == 'UNKNOWN'
    with pytest.raises(CapacityCounterUnavailable, match='OUTPUT_RESERVATION_UNSUPPORTED'):
        DeepseekV41TextCapacityCounter(SimulatedRenderer()).measure('deepseek','deepseek-flash',payload,scope='SIMULATED')


@pytest.mark.parametrize('output', [2048, 4096])
def test_unproved_higher_tiers_fail_without_silent_clamping(output):
    counter = OrdinaryDeepseekV41TextCapacityCounter(SimulatedRenderer())
    measured = counter.measure('deepseek','deepseek-flash',body(output),scope='SIMULATED')
    assert measured.output_reservation == output
    with pytest.raises(ProviderRequestNotSent, match='OUTPUT_CAPACITY_UNVERIFIED'):
        simulated_capacity(counter).assert_output_capacity(output, execution_kind='REAL')


def simulated_capacity(counter):
    capacity = object.__new__(ServiceCapacity)
    capacity.counter = counter
    capacity.provider, capacity.model, capacity.kind = 'deepseek', 'deepseek-flash', 'deepseek_text'
    capacity._output_evidence = ()
    return capacity


def test_real_output_capacity_fails_closed_before_provider_send():
    capacity = simulated_capacity(OrdinaryDeepseekV41TextCapacityCounter(SimulatedRenderer()))
    capacity.assert_output_capacity(1024, execution_kind='SIMULATED')
    with pytest.raises(ProviderRequestNotSent, match='OUTPUT_CAPACITY_UNVERIFIED'):
        capacity.assert_output_capacity(1024, execution_kind='REAL')
    assert capacity.output_readiness['hosted_recipe_equivalence'] == 'UNKNOWN'


@pytest.mark.parametrize('text', ['', 'partial [E1]', None])
def test_length_is_truncation_before_generic_no_content(text):
    response = {'choices':[{'message':{'content':text},'finish_reason':'length'}]}
    with pytest.raises(TruncatedAnswer) as error:
        CloudChat._validate(response)
    assert error.value.candidate == (text or '')


def test_progress_callback_failure_is_nonfatal_and_roles_are_public():
    captured = []
    LangChainQuickChain._progress({'on_progress':lambda stage,status,**kw:captured.append((stage,status,kw))}, 'generation','started',role='chat_cheap',attempt_id='a')
    assert captured == [('generation','started',{'role':'cheap','attempt_id':'a'})]
    def broken(*args, **kw):
        raise RuntimeError('sink unavailable')
    LangChainQuickChain._progress({'on_progress':broken},'retrieval','started')


def test_ordinary_router_1024_cheap_then_expensive_share_budget_not_attempt_envelope(monkeypatch):
    from backend.tests.test_p5_router_generation import make_chain
    from backend.app.application.rule_router_v1 import RouterPolicy
    from backend.app.application.run_metrics import collect_metrics
    from backend.app.domain.scope import Scope
    chain, _, captured, expensive = make_chain(monkeypatch,
        cheap=TruncatedAnswer('partial [E1]'), policy=RouterPolicy(mode='DYNAMIC', truncated_escalation=True))
    events = []
    with collect_metrics('ordinary-test') as metrics:
        result = chain.invoke('A 的成本是多少？', Scope.from_ids(['kb']), run_id='ordinary-test',
            settings=QuickSettings(cloud_enabled=True, ordinary_output_budget=True),
            cloud_allowed_by_kb={'kb':True},
            on_progress=lambda stage,status,**fields: events.append((stage,status,fields)))
        attempts = metrics.rule_router['attempts']
    assert result.error_code is None
    assert len(captured) == len(expensive.calls) == 1
    assert captured[0]['max_tokens'] == expensive.calls[0][2] == 1024
    assert attempts[0]['attempt_id'] != attempts[1]['attempt_id']
    assert attempts[0]['envelope_identity'] != attempts[1]['envelope_identity']
    assert ('retrieval','started',{}) in events
    assert ('evidence','completed',{'selected_evidence_count':1}) in events
    assert any(stage == 'generation' and status == 'truncated' and fields['role'] == 'cheap' for stage,status,fields in events)
    assert any(stage == 'generation' and status == 'completed' and fields['role'] == 'expensive' for stage,status,fields in events)


def test_real_local_xing_1024_wire_measurement_is_not_endpoint_proof(monkeypatch):
    from pathlib import Path
    from backend.app.adapters.models.siliconflow_xing_capacity import OrdinarySiliconFlowXingCapacityCounter
    assets = Path('var/model-assets/xing4.0/baae3c3e813cad5f888f1f485cfff659c89076c5')
    counter = OrdinarySiliconFlowXingCapacityCounter(assets)
    capacity = ServiceCapacity('siliconflow','XingChenAGI/Xing4.0-29B',counter,kind='xing_text')
    messages = [{'role':'user','content':'证据事实'}]
    payload = capacity.render_chat(messages,1024)
    wire = json.dumps(payload).encode()
    input_bound, output_bound = capacity.measure(payload,wire)
    assert input_bound > 0 and output_bound == 1024
    assert capacity.model_window().check(messages,1024)['output_reserve'] == 1024
    assert capacity.output_readiness['endpoint_output_evidence'] == 'UNKNOWN'
    with pytest.raises(ProviderRequestNotSent, match='OUTPUT_CAPACITY_UNVERIFIED'):
        capacity.assert_output_capacity(4096, execution_kind='REAL')
    payload4096 = capacity.render_chat(messages,4096)
    assert capacity.measure(payload4096, json.dumps(payload4096).encode())[1] == 4096
