"""Developer regressions for two known rule defects; not production accuracy."""
import asyncio

import pytest

from backend.weknora_port.retrieval.rule_registry import RULES, parse_rule, render_classification_guidance
from backend.weknora_port.retrieval.strategy import plan_query, plan_rule_trace


@pytest.mark.parametrize('query,kind,reason', [
    ('分别介绍A和B之间的通信方式', 'SIMPLE_PARALLEL', 'RELATIONAL_OBJECT_LIST'),
    ('比较A和B之间的联系', 'SIMPLE_COMPARE', 'RELATIONAL_OBJECT_LIST'),
    ('比较A、B、C之间的联系', 'SIMPLE_COMPARE', 'RELATIONAL_OBJECT_LIST'),
    ('分别查询A、B、C 之间的通信方式', 'SIMPLE_PARALLEL', 'RELATIONAL_OBJECT_LIST'),
    ('比较PostgreSQL和Redis的区别', 'SIMPLE_COMPARE', 'SHARED_DIMENSION_UNSPECIFIED'),
    ('比较A和B的异同', 'SIMPLE_COMPARE', 'SHARED_DIMENSION_UNSPECIFIED'),
    ('分别介绍A和B的异同', 'SIMPLE_PARALLEL', 'SHARED_DIMENSION_UNSPECIFIED'),
])
def test_rejection_preserves_root_and_actual_single_query_plan(query, kind, reason):
    match = parse_rule(query, kind)
    plan = plan_query(query, query, kind)
    assert not match.matched and match.reject_reason == reason
    assert match.objects == () and not match.full_match and not match.render_queries()
    assert plan.strategy == 'PLAIN' and plan.reason == 'RULE_NOT_APPLICABLE'
    assert plan.q0 == plan.effective_query == query
    assert [sub.query_text for sub in plan.subqueries] == [query]
    trace = plan_rule_trace(plan)
    assert trace['rule_version'] == '2' and trace['reject_reason'] == reason
    assert trace['rule_status'] == 'NOT_APPLICABLE' and trace['subquery_count'] == 0
    assert trace['retrieval_units_planned'] == 1


@pytest.mark.parametrize('query,kind,objects,dimension', [
    ('比较A和B在安全性方面的区别', 'SIMPLE_COMPARE', ('A', 'B'), '安全性'),
    ('分别介绍A和B的通信方式', 'SIMPLE_PARALLEL', ('A', 'B'), '通信方式'),
    ('比较空间引擎和中间件的性能', 'SIMPLE_COMPARE', ('空间引擎', '中间件'), '性能'),
    ('分别介绍空间引擎和中间件的通信方式', 'SIMPLE_PARALLEL', ('空间引擎', '中间件'), '通信方式'),
])
def test_existing_independent_attributes_and_single_jian_remain_admitted(query, kind, objects, dimension):
    match = parse_rule(query, kind)
    plan = plan_query(query, query, kind)
    assert match.matched and match.objects == objects and match.dimension == dimension
    assert len(plan.subqueries) == 2
    assert all(query[slice(*sub.object_span)] == obj for sub, obj in zip(plan.subqueries, objects))


@pytest.mark.parametrize('dimension', ['日志机制', '误差', '无锁机制'])
def test_known_conservative_rejections_still_fall_back(dimension):
    query = '比较A和B的' + dimension
    assert not parse_rule(query, 'SIMPLE_COMPARE').matched
    plan = plan_query(query, query, 'SIMPLE_COMPARE')
    assert plan.strategy == 'PLAIN' and [sub.query_text for sub in plan.subqueries] == [query]


def test_both_changed_rule_versions_and_generated_guidance_share_rejections():
    guidance = render_classification_guidance()
    assert len(RULES) == 2
    for declaration in RULES:
        assert declaration.rule_version == '2'
        assert declaration.rule_id + '/v2' in guidance
    assert 'relational object lists ending in 之间' in guidance
    assert '区别 or 异同 alone' in guidance


@pytest.mark.parametrize('query,kind', [
    ('分别介绍A和B之间的通信方式', 'SIMPLE_PARALLEL'),
    ('比较A和B之间的联系', 'SIMPLE_COMPARE'),
    ('比较PostgreSQL和Redis的区别', 'SIMPLE_COMPARE'),
    ('比较A和B的异同', 'SIMPLE_COMPARE'),
])
def test_pipeline_rejected_rule_retrieves_once_with_unmodified_question(query, kind):
    from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
    from test_s862_pipeline import OriginalRepo, Understanding
    from test_s86_pipeline_integration import CTX, Registry, state
    async def run():
        repo, model = OriginalRepo(), Understanding(query, kind)
        current = state(query=query)
        await RetrievalPipeline(repo, Registry(model)).run(current, CTX)
        assert current.query == current.rewrite_query == query
        assert current.strategy_plan.strategy == 'PLAIN'
        assert len(model.qu) == len(model.ranks) == len(repo.calls) == 1
        assert repo.calls[0][0] == query
        assert current.strategy_units_used == 1
    asyncio.run(run())
