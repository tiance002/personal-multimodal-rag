"""S8.6.2 actual orchestration with synthetic original sources and Mock models."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import GenerationRequest, Evidence, RouterPolicy
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.strategy_executor import record_delivered_coverage
from test_generation import setup, event
from test_s86_pipeline_integration import CTX, Models, Registry, Repo, raw, state


class OriginalRepo(Repo):
    async def hybrid_search(self, targets, params, context, query_embedding=None):
        self.calls.append((params.query_text, context.request_id, tuple(targets), query_embedding))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            name = next((name for name in ('Alpha', 'Beta', 'Gamma') if name in params.query_text), 'Original')
            source = raw(name.lower(), name + ' original durability and concurrency description.')
            self.sources[source.id] = source
            return [deepcopy(source)]
        finally:
            self.active -= 1


class Understanding(Models):
    def __init__(self, query, kind):
        super().__init__()
        self.query, self.kind = query, kind

    async def chat(self, messages, options, context):
        self.qu.append((messages, options, context.request_id))
        return SimpleNamespace(content=json.dumps({'rewrite_query': self.query,
            'intent': 'kb_search', 'image_description': '', 'query_type': self.kind}), finish_reason='stop')


@pytest.mark.parametrize('question,kind,count,rule_id', [
    ('Compare Alpha, Beta, and Gamma in terms of durability and concurrency', 'SIMPLE_COMPARE', 3, 'COMPARE_SHARED'),
    ('分别说明Alpha、Beta、Gamma的持久化与并发能力', 'SIMPLE_PARALLEL', 3, 'PARALLEL_ATTRIBUTE'),
    ('分别介绍Alpha和Beta的持久化与并发能力', 'SIMPLE_PARALLEL', 2, 'PARALLEL_ATTRIBUTE'),
])
def test_new_rules_one_qu_serial_units_original_delivery_one_final_generation(question, kind, count, rule_id):
    async def run():
        from backend.weknora_port.retrieval.search import SearchTarget
        embeddings, children, persisted = [], [], []
        async def embed(key, query, context):
            embeddings.append((query, context.request_id))
            return [1.]
        current = state(query=question, targets=(SearchTarget('kb', embedding_model_key='emb'),),
                        user_message_id='original-user')
        models, repo = Understanding(question, kind), OriginalRepo()
        pipeline = RetrievalPipeline(repo, Registry(models), embedding_resolver=embed,
            persist_sink=lambda *args: persisted.append(args))
        original_once = pipeline.retrieve_once
        async def observe_once(child, context, **kwargs):
            children.append((child.enable_expansion, child.query, child.rewrite_query, context.scope))
            return await original_once(child, context, **kwargs)
        pipeline.retrieve_once = observe_once
        finalized = await pipeline.run(current, CTX)
        assert len(models.qu) == 1 and models.qu[0][1].thinking is False
        assert models.qu[0][1].max_completion_tokens == 256
        assert len(repo.calls) == len(models.ranks) == len(embeddings) == len(children) == count <= 3
        assert repo.max_active == 1 and all(not expanded for expanded, *_ in children)
        queries = [sub.query_text for sub in current.strategy_plan.subqueries]
        assert [q for q, _ in embeddings] == [q for q, _ in models.ranks] == [q for q, *_ in repo.calls] == queries
        assert all(q == rewritten and scope == CTX.scope for _, q, rewritten, scope in children)
        assert current.query == current.rewrite_query == question
        assert current.enable_expansion is True and current.strategy_units_used == count
        assert len(persisted) == 1 and len(finalized.result_ids) == count
        trace = next(row for row in current.strategy_trace if row['stage'] == 'strategy_rule')
        assert trace['rule_id'] == rule_id and trace['rule_version']
        assert trace['rule_status'] == 'MATCHED' and trace['subquery_count'] == count
        assert trace['full_match'] is True and len(trace['object_spans']) == count
        assert question not in json.dumps(trace, ensure_ascii=False)
        prepared = []
        def observe_prepared(value):
            prepared.append(value)
            record_delivered_coverage(current, value)
        request = GenerationRequest('root', 'session', 'assistant', question, finalized,
            evidence=tuple(Evidence.from_result(row) for row in current.merge_results),
            policy=RouterPolicy(mode='DYNAMIC'), citations_enabled=False,
            on_context_prepared=observe_prepared)
        answer = 'The independent original descriptions provide durability and concurrency details for each named object.'
        coordinator, cheap, expensive, *_ = setup(
            [event(answer, done=True, finish='stop', final=True)],
            [event(answer, done=True, finish='stop', final=True)])
        await coordinator.run(request, CTX)
        assert len(prepared) == len(cheap.calls) + len(expensive.calls) == 1
        messages = (cheap.calls or expensive.calls)[0][0]
        assert messages == prepared[0].messages and question in messages[-1].content
        assert all(row.content in messages[-1].content for row in current.merge_results)
        assert all(value['delivery'] == 'DELIVERED' and value['semantic_support'] == 'NOT_CHECKED'
                   for value in current.strategy_coverage.values())
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['DIRECT', 'JOINT', 'PARALLEL', 'MIXED', 'UNKNOWN', 'SIMPLE_PARALLEL_EXTRA'])
def test_other_or_invalid_qu_types_never_promote_a_local_comparison(kind):
    async def run():
        question = 'Compare Alpha and Beta in terms of durability'
        repo, models = OriginalRepo(), Understanding(question, kind)
        current = state(query=question)
        await RetrievalPipeline(repo, Registry(models)).run(current, CTX)
        assert current.strategy_plan.strategy == 'PLAIN'
        assert len(models.qu) == len(models.ranks) == len(repo.calls) == current.strategy_units_used == 1
        assert repo.calls[0][0] == current.query == question
        trace = next(row for row in current.strategy_trace if row['stage'] == 'strategy_rule')
        assert trace['rule_status'] == 'NOT_EVALUATED' and trace['subquery_count'] == 0
    asyncio.run(run())


def test_rule_rejection_records_reason_and_runs_one_original_query():
    async def run():
        question = '比较Alpha和Beta在2024年的延迟'
        repo, models = OriginalRepo(), Understanding(question, 'SIMPLE_COMPARE')
        current = state(query=question)
        await RetrievalPipeline(repo, Registry(models)).run(current, CTX)
        assert current.query == current.rewrite_query == question and repo.calls[0][0] == question
        assert len(models.qu) == len(repo.calls) == len(models.ranks) == 1
        trace = next(row for row in current.strategy_trace if row['stage'] == 'strategy_rule')
        assert trace['rule_id'] == 'COMPARE_SHARED' and trace['rule_version']
        assert trace['rule_status'] == 'NOT_APPLICABLE' and trace['reject_reason']
        assert trace['subquery_count'] == 0 and trace['retrieval_units_planned'] == 1
    asyncio.run(run())
