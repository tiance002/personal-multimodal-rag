"""Reviewer-added parser/plan counterexamples; assigned types, no actual QU/Provider."""
import pytest
from backend.weknora_port.retrieval.rule_registry import parse_rule
from backend.weknora_port.retrieval.strategy import plan_query

@pytest.mark.parametrize('query,kind', [
    ('分别介绍A和B之间的通信方式','SIMPLE_PARALLEL'),
    ('比较A和B之间的联系','SIMPLE_COMPARE'),
    ('比较PostgreSQL和Redis的区别','SIMPLE_COMPARE'),
    ('比较A和B的异同','SIMPLE_COMPARE'),
])
def test_relation_or_missing_attribute_must_not_split(query, kind):
    match=parse_rule(query,kind)
    plan=plan_query(query,query,kind)
    assert not match.matched, (match.objects, match.dimension, match.render_queries())
    assert plan.strategy=='PLAIN' and len(plan.subqueries)==1
    assert plan.subqueries[0].query_text==query

@pytest.mark.parametrize('query,kind', [
    ('比较A和B在安全性方面的区别','SIMPLE_COMPARE'),
    ('分别介绍A和B的通信方式','SIMPLE_PARALLEL'),
])
def test_supported_independent_attributes_remain_split(query, kind):
    match=parse_rule(query,kind)
    plan=plan_query(query,query,kind)
    assert match.matched and match.objects==('A','B')
    assert len(plan.subqueries)==2
