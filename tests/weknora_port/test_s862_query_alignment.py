"""Offline QU v2/registry alignment; Provider and registry lookups are mocks."""
import asyncio
import importlib
import json
import re
from types import SimpleNamespace

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.query import (
    _REWRITE_GUARD_PROMPT, _REWRITE_GUARD_V2, requires_ambiguity_clarification,
    understand_query,
)


MODE = 'query_structure_v1'
REGISTRY_MODULE = 'backend.weknora_port.retrieval.rule_registry'
Q0 = 'Compare PostgreSQL and Redis in terms of persistence and concurrency.'
PARALLEL_Q0 = '分别介绍PostgreSQL和Redis的一致性'
CTX = CallContext('s862-qu', TrustedScope('offline-owner', ('offline-kb',)))
FIELDS = {'rewrite_query', 'intent', 'image_description', 'query_type'}


def compact_output(query=Q0, kind='SIMPLE_COMPARE', **extra):
    return json.dumps({'rewrite_query': query, 'intent': 'kb_search',
        'image_description': '', 'query_type': kind, **extra},
        ensure_ascii=False, separators=(',', ':'))


def current(query=Q0, mode=MODE, **kw):
    return PipelineState(query=query, rewrite_query=query, enable_rewrite=True,
        chat_model_id='final-expensive', query_understand_model_id='independent-cheap',
        retrieval_strategy_mode=mode, source_evidence_mode='bounded_source_v1', **kw)


def run_qu(state, output, finish='stop'):
    class Model:
        def __init__(self): self.calls = []
        async def chat(self, messages, options, context):
            self.calls.append((messages, options, context))
            return SimpleNamespace(content=output, finish_reason=finish)
    class Registry:
        def __init__(self): self.lookups = []; self.model = Model()
        async def chat(self, model_id, context):
            self.lookups.append(model_id)
            return self.model
    registry = Registry()
    asyncio.run(understand_query(state, CTX, registry))
    return registry


@pytest.fixture
def guidance_stub(monkeypatch):
    """Test the narrow renderer seam independently of registry implementation."""
    module = importlib.import_module(REGISTRY_MODULE)
    calls = []
    def render():
        calls.append(True)
        return 'REGISTRY_GUIDANCE_SENTINEL: compare/parallel definitions and rejects.'
    monkeypatch.setattr(module, 'render_classification_guidance', render)
    return calls


def test_actual_v2_messages_use_registry_once_and_preserve_one_cheap_call(guidance_stub):
    result = current(PARALLEL_Q0)
    result.rewrite_system_prompt = 'custom legacy THREE fields'
    result.rewrite_user_prompt = 'custom legacy {{query}}'
    registry = run_qu(result, compact_output(PARALLEL_Q0, 'SIMPLE_PARALLEL'))
    assert registry.lookups == ['independent-cheap'] and len(registry.model.calls) == 1
    messages, options, context = registry.model.calls[0]
    assert guidance_stub == [True]
    assert [message.role for message in messages] == ['system', 'user']
    assert messages[0].content.count('REGISTRY_GUIDANCE_SENTINEL') == 1
    assert 'custom legacy' not in messages[0].content
    assert 'exactly four fields' in messages[0].content
    assert 'latest independent authorized USER question' in messages[0].content
    assert 'Never take an antecedent only from an assistant answer' in messages[0].content
    assert PARALLEL_Q0 in messages[1].content
    assert options.max_completion_tokens == 256 and options.max_tokens == 0
    assert options.temperature == .3 and options.thinking is False
    assert context.request_id == CTX.request_id + '/query_rewrite'
    assert result.query == result.rewrite_query == PARALLEL_Q0
    assert result.query_type == 'SIMPLE_PARALLEL'
    assert result.query_trace[-1]['contract'] == 'qu_v2'
    assert result.query_trace[-1]['query_type'] == 'SIMPLE_PARALLEL'


def test_registered_family_definitions_are_not_duplicated_in_static_guard():
    for label in ('COMPARE_SHARED', 'PARALLEL_ATTRIBUTE', 'SIMPLE_COMPARE', 'SIMPLE_PARALLEL'):
        assert label not in _REWRITE_GUARD_V2
    assert 'PARALLEL=independent tasks' in _REWRITE_GUARD_V2
    assert 'JOINT, PARALLEL and MIXED do not enable a supported closed rule' in _REWRITE_GUARD_V2


def test_actual_registry_guidance_reaches_system_and_has_four_field_examples():
    guidance = importlib.import_module(REGISTRY_MODULE).render_classification_guidance()
    result = current(PARALLEL_Q0)
    registry = run_qu(result, compact_output(PARALLEL_Q0, 'SIMPLE_PARALLEL'))
    system = registry.model.calls[0][0][0].content
    assert system.count(guidance) == 1
    assert all(value in guidance for value in
        ('COMPARE_SHARED', 'PARALLEL_ATTRIBUTE', 'SIMPLE_COMPARE', 'SIMPLE_PARALLEL'))
    examples = [json.loads(match[0]) for match in re.finditer(r'\{[^{}]*\}', guidance)]
    assert examples and all(set(example) == FIELDS for example in examples)
    assert {'SIMPLE_COMPARE', 'SIMPLE_PARALLEL'} <= {example['query_type'] for example in examples}
    assert re.search(r'reject|unsupported|decline|不支持|拒绝', guidance, re.I)


@pytest.mark.parametrize('kind', [
    'DIRECT', 'SIMPLE_COMPARE', 'SIMPLE_PARALLEL', 'JOINT', 'PARALLEL', 'DEPENDENT', 'MIXED', 'UNKNOWN',
])
def test_v2_legal_enum_labels_preserve_valid_rewrite_and_trace(guidance_stub, kind):
    result = current()
    registry = run_qu(result, compact_output(kind=kind))
    assert len(registry.model.calls) == 1
    assert result.query == result.rewrite_query == Q0 and not result.rewrite_fallback_reason
    assert result.query_type == kind and result.query_trace[-1]['query_type'] == kind


@pytest.mark.parametrize('kind', [None, 3, {}, [], 'simple_parallel', 'SIMPLE_PARALLEL ', 'AGENT'])
def test_invalid_enum_falls_back_to_unknown_without_discarding_q0(guidance_stub, kind):
    result = current()
    run_qu(result, compact_output(kind=kind))
    assert result.query == result.rewrite_query == Q0 and not result.rewrite_fallback_reason
    assert result.query_type == result.query_trace[-1]['query_type'] == 'UNKNOWN'


@pytest.mark.parametrize('output,finish', [
    ('preface ' + compact_output(kind='SIMPLE_PARALLEL'), 'stop'),
    (compact_output(kind='SIMPLE_PARALLEL'), 'length'),
    (compact_output(Q0[:-1].replace('Compare', 'compare'), 'SIMPLE_PARALLEL'), 'stop'),
])
def test_new_label_does_not_bypass_whole_json_completion_or_literal_guard(guidance_stub, output, finish):
    result = current()
    run_qu(result, output, finish)
    assert result.query == result.rewrite_query == Q0 and result.rewrite_fallback_reason
    assert result.query_type == 'UNKNOWN'


def test_legacy_v1_prompt_and_150_cap_do_not_invoke_registry(guidance_stub):
    result = current(mode='off', rewrite_system_prompt='custom system', rewrite_user_prompt='custom {{query}}')
    registry = run_qu(result, compact_output(kind='SIMPLE_PARALLEL'))
    messages, options, _ = registry.model.calls[0]
    assert guidance_stub == [] and len(registry.model.calls) == 1
    assert messages[0].content == 'custom system' + _REWRITE_GUARD_PROMPT
    assert messages[1].content.startswith('custom ' + Q0)
    assert options.max_completion_tokens == 150 and options.thinking is False
    assert result.query_type == 'UNKNOWN'


def history(query, answer='Generated dialogue is not source evidence.'):
    return SimpleNamespace(query=query, answer=answer)


def test_valid_literal_history_substitution_keeps_dependent_meaning(guidance_stub):
    q0 = 'What did she direct from 2007–2010? Do not include other years.'
    rewritten = q0.replace('she', 'Jeanne Marie Beaumont')
    result = current(q0, history=[history('Who is Jeanne Marie Beaumont?')])
    run_qu(result, compact_output(rewritten, 'DEPENDENT', intent='follow_up'))
    assert result.query == q0 and result.rewrite_query == rewritten
    assert result.query_type == 'DEPENDENT' and result.intent == 'kb_search'


@pytest.mark.parametrize('kind', ['SIMPLE_COMPARE', 'SIMPLE_PARALLEL', 'PARALLEL'])
def test_new_supported_label_cannot_choose_ambiguous_history_subject(guidance_stub, kind):
    q0 = 'What is it used for?'
    rounds = [history('Compare a Leyden jar and Franklin bells.')]
    result = current(q0, history=rounds)
    run_qu(result, compact_output('What are Franklin bells used for?', kind, intent='follow_up'))
    assert result.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'
    assert result.query == result.rewrite_query == q0 and result.query_type == 'UNKNOWN'
    assert result.intent == 'kb_search'
    transformed = current('Format the previous answer as a table.',
        history=rounds + [history(q0, 'Unsupported old guess about bells.')])
    run_qu(transformed, compact_output(transformed.query, kind, intent='follow_up'))
    assert requires_ambiguity_clarification(transformed)


def test_new_label_cannot_ground_a_name_only_in_generated_answer(guidance_stub):
    result = current('What did she direct?',
        history=[history('Who is Ada Rowan?', 'Jeanne Marie Beaumont directed a seminar.')])
    run_qu(result, compact_output('What did Jeanne Marie Beaumont direct?', 'SIMPLE_PARALLEL'))
    assert result.rewrite_query == result.query and result.query_type == 'UNKNOWN'
    assert result.rewrite_fallback_reason == 'REFERENCE_NOT_GROUNDED'


def test_legal_parallel_four_field_json_has_no_larger_character_or_byte_envelope():
    parallel = compact_output(PARALLEL_Q0, 'SIMPLE_PARALLEL')
    comparable = [compact_output(Q0, 'SIMPLE_COMPARE'),
        compact_output('What did Ada Rowan direct from 2011–2014?', 'DEPENDENT')]
    assert set(json.loads(parallel)) == FIELDS
    assert len(parallel) <= max(map(len, comparable))
    assert len(parallel.encode('utf8')) <= max(len(value.encode('utf8')) for value in comparable)
    # Characters/UTF-8 bytes are a format bound; actual model Tokens are not measured.
