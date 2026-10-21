"""Closed S8.6.2 registry grammar contracts; no Provider, database or network."""
import json
import re

import pytest

from backend.weknora_port.retrieval.rule_registry import (
    RULES, parse_rule, render_classification_guidance, render_queries,
)


@pytest.mark.parametrize('query,kind,objects,dimension,language', [
    ('比较PostgreSQL和Redis的持久化与并发能力', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis'), '持久化与并发能力', 'cn'),
    ('对比PostgreSQL与Redis的一致性？', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis'), '一致性', 'cn'),
    ('PostgreSQL与Redis在一致性方面有何区别', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis'), '一致性', 'cn'),
    ('比较PostgreSQL和Redis在一致性方面的区别。', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis'), '一致性', 'cn'),
    ('比较PostgreSQL、Redis、MongoDB的持久化与并发能力', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis', 'MongoDB'), '持久化与并发能力', 'cn'),
    ('Compare PostgreSQL and Redis in terms of persistence and concurrency.', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis'), 'persistence and concurrency', 'en'),
    ('Compare PostgreSQL with Redis on persistence and concurrency', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis'), 'persistence and concurrency', 'en'),
    ('Compare PostgreSQL, Redis, and MongoDB in terms of consistency?', 'SIMPLE_COMPARE', ('PostgreSQL', 'Redis', 'MongoDB'), 'consistency', 'en'),
    ('compare Qwen3.5-4B and BGE-m3 in terms of architecture', 'SIMPLE_COMPARE', ('Qwen3.5-4B', 'BGE-m3'), 'architecture', 'en'),
    ('比较Qwen3.5-4B和Qwen3-8B的架构', 'SIMPLE_COMPARE', ('Qwen3.5-4B', 'Qwen3-8B'), '架构', 'cn'),
    ('分别介绍PostgreSQL和Redis的一致性', 'SIMPLE_PARALLEL', ('PostgreSQL', 'Redis'), '一致性', 'cn'),
    ('分别说明PostgreSQL与Redis的持久化与并发能力。', 'SIMPLE_PARALLEL', ('PostgreSQL', 'Redis'), '持久化与并发能力', 'cn'),
    ('分别查询PostgreSQL、Redis、MongoDB的一致性', 'SIMPLE_PARALLEL', ('PostgreSQL', 'Redis', 'MongoDB'), '一致性', 'cn'),
    ('PostgreSQL和Redis各自的持久化与并发能力是什么？', 'SIMPLE_PARALLEL', ('PostgreSQL', 'Redis'), '持久化与并发能力', 'cn'),
])
def test_closed_positive_slots_spans_and_exact_one_query_per_object(query, kind, objects, dimension, language):
    result = parse_rule(query, kind)
    assert result.status == 'MATCHED' and result.matched
    assert result.query_type == kind and result.language == language
    assert result.objects == objects and result.dimension == dimension
    assert result.full_match == query and result.reject_reason == ''
    assert result.rule_id in {'COMPARE_SHARED', 'PARALLEL_ATTRIBUTE'}
    assert result.rule_version == '1'
    assert tuple(query[slice(*span)] for span in result.object_spans) == objects
    assert query[slice(*result.dimension_span)] == dimension
    expected = tuple(name + '的' + dimension + '是什么？' if language == 'cn'
        else 'What is known about ' + name + ' regarding ' + dimension + '?'
        for name in objects)
    assert result.render_queries() == expected == render_queries(result)


@pytest.mark.parametrize('query', [
    '比较A和B和C的性能',
    '比较A和B与C的性能',
    '比较A、B、C、D的性能',
    '比较A、B的性能',
    '比较A和联合与集团的性能',
    '比较A和B的客户的性能',
    '比较A和B的非持久化能力',
    '比较A和B的性能或价格',
    '比较A和B在2024年的性能',
    '比较A和B的性能在2024年',
    '比较A和B的性能，并提供文档依据',
    '比较A和B的性能并给出表格',
    '比较A和B的仅限授权文档的性能',
    '比较A和B的总成本差额',
    '比较A和B的兼容性',
    '比较A和B的性能如果内存有限',
    '比较A和B的五年前性能',
    '比较A和B的最近性能',
    '比较A和B的延迟少于十毫秒',
    '比较比较A和B的性能',
    '比较A和B的价格差是多少',
    '比较A和B所主持会议的举办地点',
    '比较它和B的价格',
    '分别介绍A和它的价格',
    '比较A和B的一年成本',
    '比较A和B的三个月性能',
    '比较A和B在延迟方面有何区别',
    '比较A和B从开始到现在的性能',
    '比较A和B当天的性能',
    'Compare it and B in terms of latency',
    'Compare A and those in terms of latency',
    '比较A和A的性能',
    'Compare A and B and C in terms of latency',
    'Compare A and B with C in terms of latency',
    'Compare A, B, C, and D in terms of latency',
    'Compare A and B in terms of latency, only from document A.',
    'Compare A and B in terms of latency if memory is limited.',
    'Compare A and B in terms of latency and explain the result.',
    'Compare A and B in terms of latency or throughput.',
    'Compare A and B in terms of not persistence.',
    'Compare A and B in terms of non-persistence.',
    "Compare A\'s cluster and B in terms of latency.",
    'Compare A and B in terms of latency in 2024.',
    'Compare A and B in terms of total cost difference.',
    'Compare A and B in terms of compatibility.',
    'Compare A and B in terms of latency below 20 ms.',
    'Compare A and B in terms of latency in October.',
    'Compare A and B in terms of latency last summer.',
    'Compare A and B in terms of latency less than ten milliseconds.',
    'Compare A and B in terms of latency only for the authorized user.',
    'Compare A and A in terms of latency',
    '分别介绍A和B和C的性能',
    '分别说明A、B、C、D的性能',
    '分别查询A和B的性能及其文档依据',
    'A和B各自的客户的性能是什么',
    'A和B各自的协作方式是什么',
    '比较A和B的性能。然后说明C',
    ' Compare A and B in terms of latency',
    'Compare A and B in terms of latency\n',
])
def test_unsafe_or_unclosed_query_rejected_without_partial_slots(query):
    result = parse_rule(query)
    assert result.status == 'NOT_APPLICABLE' and not result.matched
    assert result.reject_reason
    assert result.objects == () and result.object_spans == ()
    assert result.dimension == '' and result.dimension_span is None
    assert result.render_queries() == ()


def test_compare_b_never_swallows_extra_objects_or_new_request():
    for query in (
        'Compare A and B and C in terms of latency',
        'Compare A and B with C on latency',
        'Compare A and B in terms of latency and provide sources',
    ):
        assert not parse_rule(query, 'SIMPLE_COMPARE').matched


@pytest.mark.parametrize('query_type', ['PARALLEL', 'JOINT', 'DEPENDENT', 'MIXED', 'UNKNOWN', 'DIRECT', 'made_up', 3])
def test_explicit_query_type_never_promotes_unsupported_model_advice(query_type):
    assert not parse_rule('分别介绍A和B的性能', query_type).matched
    assert not parse_rule('Compare A and B in terms of latency', query_type).matched


def test_family_mismatch_fails_but_diagnostic_none_can_match_either():
    compare = '比较A和B的性能'
    parallel = '分别介绍A和B的性能'
    assert parse_rule(compare).query_type == 'SIMPLE_COMPARE'
    assert parse_rule(parallel).query_type == 'SIMPLE_PARALLEL'
    assert not parse_rule(compare, 'SIMPLE_PARALLEL').matched
    assert not parse_rule(parallel, 'SIMPLE_COMPARE').matched


@pytest.mark.parametrize('kind,rule_id', [('SIMPLE_COMPARE', 'COMPARE_SHARED'), ('SIMPLE_PARALLEL', 'PARALLEL_ATTRIBUTE')])
@pytest.mark.parametrize('query', ['unknown grammar', '', None, '比较A和B和C的性能'])
def test_explicit_registered_family_retains_trace_metadata_without_partial_slots(query, kind, rule_id):
    result = parse_rule(query, kind)
    assert not result.matched and result.objects == () and result.dimension == ''
    assert result.rule_id == rule_id and result.rule_version == '1' and result.query_type == kind


@pytest.mark.parametrize('value', [None, 1, [], '', 'A' * 401])
def test_invalid_query_inputs_fail_closed(value):
    assert not parse_rule(value).matched


def test_registry_declarations_are_closed_and_self_validating_examples():
    assert isinstance(RULES, tuple) and len(RULES) == 2
    assert {row.rule_id for row in RULES} == {'COMPARE_SHARED', 'PARALLEL_ATTRIBUTE'}
    assert {row.query_type for row in RULES} == {'SIMPLE_COMPARE', 'SIMPLE_PARALLEL'}
    for rule in RULES:
        assert rule.rule_version == '1'
        assert rule.languages and rule.syntax and rule.required_attributes and rule.rejections
        assert rule.minimum_objects == 2 and rule.maximum_objects == 3
        assert rule.positive_examples and rule.negative_examples and callable(rule.parser)
        assert rule.templates
        for query in rule.positive_examples:
            result = parse_rule(query, rule.query_type)
            assert result.matched and result.rule_id == rule.rule_id
        for query in rule.negative_examples:
            assert not parse_rule(query, rule.query_type).matched


def test_fabricated_or_modified_match_cannot_render_unvalidated_slots():
    from dataclasses import replace
    match = parse_rule('比较A和B的性能', 'SIMPLE_COMPARE')
    assert match.matched
    assert replace(match, objects=('forged entity', 'B')).render_queries() == ()
    assert replace(match, rule_version='invented').render_queries() == ()
    assert replace(match, dimension='伪造关系').render_queries() == ()


def test_guidance_is_derived_from_only_enabled_families_and_full_qu_examples():
    guidance = render_classification_guidance()
    for rule in RULES:
        assert rule.rule_id in guidance and rule.rule_version in guidance and rule.query_type in guidance
        assert all(example in guidance for example in rule.positive_examples)
        assert all(example in guidance for example in rule.negative_examples)
        assert all(boundary in guidance for boundary in rule.rejections)
    examples = [json.loads(raw) for raw in re.findall(r'\{[^{}]*\}', guidance)]
    assert examples and {row['query_type'] for row in examples} == {'SIMPLE_COMPARE', 'SIMPLE_PARALLEL'}
    assert all(set(row) == {'rewrite_query', 'intent', 'image_description', 'query_type'} for row in examples)
    assert 'JOINT' in guidance and 'PARALLEL' in guidance and 'UNKNOWN' in guidance
