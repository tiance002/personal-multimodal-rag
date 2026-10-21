"""S8.5 deterministic conversation regressions; no hosted model/PG/ECS calls."""
import asyncio
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.weknora_port.conversation.history import OrdinaryHistoryService, process_history
from backend.weknora_port.conversation.types import MemoryContext, MemoryScope, MemoryScopeError, StoredMessage
from backend.weknora_port.models.types import CallCancelled, ModelCallTimeout, RunDeadlineExceeded, RerankResult
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.query import resolve_intent, understand_query
from backend.weknora_port.storage.memory import PostgresMemoryRepository
from test_pipeline import CTX, Model, Registry, Repo, row, state
from test_service import REQ, runtime

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
SCOPE = MemoryScope(1, 'owner')
MEMORY = MemoryContext(1, 'owner')


def run(value):
    return asyncio.run(value)


def messages(request, question, answer='completed answer', *, minute=0, **changes):
    common = dict(request_id=request, session_id='session', tenant_id=1, subject_id='owner',
                  created_at=NOW + timedelta(minutes=minute))
    user = StoredMessage(id=request+'-u', role='user', content=question, **common)
    assistant = StoredMessage(id=request+'-a', role='assistant', content=answer, **common)
    if changes:
        values = asdict(assistant); values.update(changes)
        assistant = SimpleNamespace(**values)
    return [user, assistant]


def test_history_filters_terminal_metadata_roles_current_turn_and_owners():
    rows = messages('one', 'first', minute=1)
    rows += messages('two', 'second', minute=2)
    rows += messages('partial', 'unfinished', is_completed=False)
    rows += messages('cancelled', 'cancelled', is_completed=True, turn_status='cancelled')
    rows += messages('error', 'failed', is_completed=True, turn_status='error')
    rows += messages('current', 'current question', minute=9)
    for field, value in [('tenant_id', 2), ('subject_id', 'other'), ('session_id', 'other-session')]:
        rows += [replace(m, **{field:value}) for m in messages('foreign-'+field, 'private')]
    out = process_history(list(reversed(rows)), 2, scope=SCOPE, session_id='session', exclude_request_id='current')
    assert [h.request_id for h in out] == ['one', 'two']


@pytest.mark.parametrize('error', [PermissionError('denied'), MemoryScopeError(), CallCancelled(),
                                  RunDeadlineExceeded(), TimeoutError(), ModelCallTimeout()])
def test_history_security_and_execution_failures_propagate(error):
    async def recent(*args):
        raise error
    with pytest.raises(type(error)):
        run(OrdinaryHistoryService(SimpleNamespace(recent_messages=recent)).load(MEMORY, 'session', 3))


def test_history_optional_generic_read_failure_still_degrades():
    async def recent(*args):
        raise RuntimeError('synthetic unavailable')
    assert run(OrdinaryHistoryService(SimpleNamespace(recent_messages=recent)).load(MEMORY, 'session', 3)) == ()


def test_disabled_history_cannot_authorize_history_transform():
    s = state(query='把上一条回答改成表格', intent='follow_up', max_rounds=0,
              history=[SimpleNamespace(query='old', answer='old')])
    repo = Repo([row()])
    run(RetrievalPipeline(repo, Registry()).run(s, CTX))
    assert not s.history and s.needs_retrieval and repo.calls


def test_history_repository_selects_completed_successful_pairs_before_limit():
    queries = []
    repo = object.__new__(PostgresMemoryRepository)
    repo.identity_guard = SimpleNamespace(require_verified=lambda:None)
    async def rows(sql, params):
        queries.append((sql, params)); return []
    repo._rows = rows
    run(repo.recent_messages(SCOPE, 'session', 16))
    sql, params = queries[0]
    assert 'is_completed=true' in sql and "NOT IN ('error','cancelled')" in sql
    assert 'tenant_id=%s AND subject_id=%s' in sql and 'session_id=%s' in sql
    assert params[:3] == (1, 'owner', 'session')


@pytest.mark.parametrize('question', ['它对特征有什么要求？', '它有什么缺点？', '它的依据在哪份文档中？',
                                     '上面第二点详细解释，并提供文档依据'])
@pytest.mark.parametrize('intent', ['follow_up', 'greeting', 'chitchat'])
def test_factual_or_source_followups_always_retrieve(question, intent):
    s = state(query=question, intent=intent, history=[SimpleNamespace(query='朴素贝叶斯分类有什么优点？', answer='历史解释')])
    assert resolve_intent(s) == 'kb_search'


@pytest.mark.parametrize('question', ['把上一条回答改成表格', '把刚才的回答翻译成英文',
                                     '请把上一个回答换一种说法。'])
def test_strict_history_only_transformations_can_skip_search(question):
    s = state(query=question, intent='follow_up', history=[SimpleNamespace(answer='已有回答')])
    repo = Repo([row()])
    run(RetrievalPipeline(repo, Registry()).run(s, CTX))
    assert not repo.calls and s.intent == 'follow_up' and not s.needs_retrieval


@pytest.mark.parametrize('question,rewrite', [
    ('它在2020年之后有什么缺点？', '朴素贝叶斯分类在2021年之后有什么缺点？'),
    ('它有什么缺点？不要讨论多标签。', '朴素贝叶斯分类有什么缺点？讨论多标签。'),
    ('它与逻辑回归相比有什么缺点？', '朴素贝叶斯分类与决策树相比有什么缺点？'),
    ('只列出它的3项缺点', '列出朴素贝叶斯分类的3项缺点'),
    ('它的第二点依据是什么？', '朴素贝叶斯分类的第三点依据是什么？'),
    ('它有什么缺点？', '火星算法有什么缺点？'),
])
def test_invalid_rewrite_preserves_q0_and_records_safe_fallback(question, rewrite):
    s = state(query=question, enable_rewrite=True, chat_model_id='cheap',
              history=[SimpleNamespace(query='朴素贝叶斯分类有什么优点？', answer='讨论了朴素贝叶斯分类')])
    run(understand_query(s, CTX, Registry(Model([json.dumps({'rewrite_query':rewrite, 'intent':'follow_up'})]))))
    assert s.query == s.rewrite_query == question
    assert s.rewrite_fallback_reason
    assert s.query_trace[-1]['q0_sha256'] and s.query_trace[-1]['rewrite_sha256']
    assert question not in json.dumps(s.query_trace, ensure_ascii=False)


def test_ambiguous_reference_does_not_choose_one_model_suggestion():
    q0 = '它有什么缺点？'
    s = state(query=q0, enable_rewrite=True, chat_model_id='cheap', history=[SimpleNamespace(
        query='比较朴素贝叶斯分类和逻辑回归', answer='两种分类器各有优点')])
    run(understand_query(s, CTX, Registry(Model([json.dumps({'rewrite_query':'朴素贝叶斯分类有什么缺点？'})]))))
    assert s.rewrite_query == q0 and s.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'


@pytest.mark.parametrize('q0,rewrite', [
    ('它的阈值不能低于-1。', '朴素贝叶斯分类的阈值不能低于1。'),
    ('它在ISO-9001标准下有什么要求？', '朴素贝叶斯分类在ISO9001标准下有什么要求？'),
])
def test_signed_limits_and_identifier_punctuation_cannot_change(q0, rewrite):
    s = state(query=q0, enable_rewrite=True, chat_model_id='cheap', history=[SimpleNamespace(
        query='朴素贝叶斯分类有什么优点？', answer='已有解释')])
    run(understand_query(s, CTX, Registry(Model([json.dumps({'rewrite_query':rewrite})]))))
    assert s.rewrite_query == q0 and s.rewrite_fallback_reason


def test_answer_only_multiple_entities_are_not_an_identity_anchor():
    q0 = '它有什么缺点？'
    s = state(query=q0, enable_rewrite=True, chat_model_id='cheap', history=[SimpleNamespace(
        query='介绍分类方法', answer='朴素贝叶斯分类适用于文本。逻辑回归适用于分类。')])
    run(understand_query(s, CTX, Registry(Model([json.dumps({'rewrite_query':'逻辑回归有什么缺点？'})]))))
    assert s.rewrite_query == q0 and s.rewrite_fallback_reason == 'REFERENCE_NOT_GROUNDED'


@pytest.mark.parametrize('finish', ['length', 'tool_calls', 'content_filter'])
def test_partial_or_tool_rewrite_response_cannot_authorize_understanding(finish):
    s = state(query='它有什么缺点？', enable_rewrite=True, chat_model_id='cheap', history=[SimpleNamespace(
        query='朴素贝叶斯分类有什么优点？', answer='已有解释')])
    class Partial:
        async def chat(self, *args):
            return SimpleNamespace(content=json.dumps({'rewrite_query':'朴素贝叶斯分类有什么缺点？'}), finish_reason=finish)
    run(understand_query(s, CTX, Registry(Partial())))
    assert s.rewrite_query == s.query and s.rewrite_fallback_reason == 'REWRITE_RESPONSE_INCOMPLETE'


@pytest.mark.parametrize('output', ['not JSON', '{}', '{"rewrite_query":""}'])
def test_parse_and_empty_rewrite_have_auditable_fallback(output):
    s = state(enable_rewrite=True, chat_model_id='cheap')
    run(understand_query(s, CTX, Registry(Model([output]))))
    assert s.rewrite_query == s.query and s.rewrite_fallback_reason


async def build_multiturn_trace():
    """Reusable deterministic trace producer for the implementation report."""
    history, audit = [], []
    turns = [('朴素贝叶斯分类有什么优点？', '朴素贝叶斯分类有什么优点？'),
             ('它对特征有什么要求？', '朴素贝叶斯分类对特征有什么要求？'),
             ('它有什么缺点？', '朴素贝叶斯分类有什么缺点？'),
             ('逻辑回归的适用条件是什么？', '逻辑回归的适用条件是什么？'),
             ('它有什么缺点？', '逻辑回归有什么缺点？')]
    for index, (q0, effective) in enumerate(turns):
        source = row('current-'+str(index), 'current original source '+str(index)+'x'*400)
        s = state(query=q0, enable_rewrite=True, chat_model_id='cheap', rerank_model_id='bge', history=list(history))
        s.targets = tuple(replace(t, embedding_model_key='embed') for t in s.targets)
        model = Model([json.dumps({'rewrite_query':effective, 'intent':'follow_up'}), [RerankResult(0, .8)]])
        repo, embedded = Repo([source]), []
        async def embedding(key, query, context):
            embedded.append(query); return [1.0]
        final = await RetrievalPipeline(repo, Registry(model), embedding_resolver=embedding).run(s, CTX)
        assert s.query == q0 and s.rewrite_query == effective
        assert embedded == [effective] and [call[1].query_text for call in repo.calls] == [effective]
        assert model.calls[-1][0] == effective and final.result_ids == (source.id,)
        assert 'STALE HISTORICAL BODY' not in final.rendered_contexts
        audit.append({'q0':q0, 'rewrite_query':s.rewrite_query, 'embedding_query':embedded[0],
                      'keyword_query':repo.calls[0][1].query_text, 'rerank_query':model.calls[-1][0],
                      'context_ids':list(final.result_ids), 'query_trace':s.query_trace})
        history.append(SimpleNamespace(query=q0, answer='已完成回答', knowledge_references=(
            row('stale-'+str(index), 'STALE HISTORICAL BODY '+effective, 100.),)))
    return audit


def test_multiturn_actual_embedding_hybrid_and_rerank_queries_and_no_old_citations():
    audit = run(build_multiturn_trace())
    assert len(audit) == 5


def test_active_merge_does_not_inject_similar_historical_sources():
    stale = row('stale', 'question evidence STALE HISTORICAL BODY', 100.)
    s = state(history=[SimpleNamespace(query='old', answer='old', knowledge_references=(stale,))])
    result = run(RetrievalPipeline(Repo([row()]), Registry()).run(s, CTX))
    assert result.result_ids == ('a',) and 'STALE HISTORICAL BODY' not in result.rendered_contexts


def test_preparation_excludes_current_run_even_if_repository_returns_it():
    async def scenario():
        service, repository, *rest = runtime()
        turn = await repository.accept(REQ, 'owner')
        async def load(*args, **kwargs):
            return (SimpleNamespace(request_id=turn.run_id, query='CURRENT', answer='CURRENT'),
                    SimpleNamespace(request_id='old', query='previous', answer='previous',knowledge_base_scope=('kb',),document_scope=()))
        service.history_service = SimpleNamespace(load=load)
        prepared = await service.prepare_turn(turn)
        assert [h.query for h in prepared.request.history] == ['previous']
    run(scenario())


def test_restart_and_sse_replay_do_not_repeat_completed_rewrite():
    from backend.weknora_port.generation import RouterPolicy
    from backend.weknora_port.service import TurnConfiguration
    async def scenario():
        model = Model([json.dumps({'rewrite_query':REQ.content, 'intent':'kb_search'})])
        service, repository, retrieval, co, cheap, expensive, store, commit, counts = runtime(
            configuration=lambda _:TurnConfiguration(state(enable_rewrite=True, chat_model_id='query'),
                                                     {'policy':RouterPolicy(mode='CHEAP_ONLY')}))
        async def pipeline_factory(turn, progress):
            counts['pipeline'] += 1
            return RetrievalPipeline(retrieval, Registry(model), on_progress=progress)
        service.pipeline_factory = pipeline_factory
        await service.accept_turn(REQ, 'owner')
        await service.drain_background()
        assert len(model.calls) == 1 and len(cheap.calls) == 1 and not expensive.calls
        understood = next(e.data for e in store.events if e.type == 'progress'
                          and e.data.get('stage') == 'understand' and e.data.get('status') == 'completed')
        assert understood['q0_sha256'] and understood['rewrite_sha256']
        assert 'rewrite_reason' in understood and REQ.content not in json.dumps(understood)
        replacement, _, _, _, _, _, replacement_store, _, replacement_counts = runtime(repo=repository)
        replacement_store.events = store.events.copy()
        replay = await replacement.accept_turn(REQ, 'owner')
        assert replay.replay_only and await replacement.run_turn(replay) is None
        assert [e async for e in replacement.replay(replay, 'owner')] == store.events
        assert replacement_counts == {'pipeline':0, 'generation':0} and len(model.calls) == 1
    run(scenario())


@pytest.mark.parametrize('old_kbs,old_docs,current_docs,refs,keep', [
    (('kb',), (), (), (), True),
    (('kb',), ('doc',), ('doc',), (), True),
    (('kb',), (), ('doc',), (), False),
    (('other-kb',), (), (), (), False),
    (None, None, (), (), False),
    (None, None, (), ({'knowledge_base_id':'kb', 'knowledge_id':'doc'},), False),
    (('kb',), (), (), ({'knowledge_base_id':'other-kb', 'knowledge_id':'secret'},), False),
    (('kb',), ('other-doc',), ('doc',), (), False),
])
def test_history_origin_checked_before_rewrite_and_generation(old_kbs, old_docs, current_docs, refs, keep):
    from backend.weknora_port.service import TurnConfiguration
    from backend.weknora_port.retrieval.search import SearchTarget
    async def scenario():
        config = state(enable_rewrite=True, chat_model_id='rewrite')
        if current_docs:
            config.targets = (SearchTarget('kb', 'knowledge', current_docs),)
        service, repository, retrieval, *rest = runtime(configuration=lambda _:TurnConfiguration(config))
        accepted = await repository.accept(REQ, 'owner')
        accepted = replace(accepted, context=replace(accepted.context, scope=replace(accepted.context.scope, document_ids=current_docs)),
                           request=replace(accepted.request, document_scope=current_docs))
        async def load(*args, **kwargs):
            return (SimpleNamespace(request_id='previous', query='HISTORY_SECRET_QUESTION', answer='HISTORY_SECRET_ANSWER',
                    knowledge_base_scope=old_kbs, document_scope=old_docs, knowledge_references=refs),)
        service.history_service = SimpleNamespace(load=load)
        model = Model([json.dumps({'rewrite_query':REQ.content, 'intent':'kb_search'})])
        async def pipeline_factory(turn, progress):
            return RetrievalPipeline(retrieval, Registry(model), on_progress=progress)
        service.pipeline_factory = pipeline_factory
        prepared = await service.prepare_turn(accepted)
        rendered = '\n'.join(m.content for m in model.calls[0][0])
        assert ('HISTORY_SECRET_QUESTION' in rendered) is keep
        assert bool(prepared.request.history) is keep
        assert prepared.context.scope == accepted.context.scope
        history_event = next(e.data for e in service.store.events if e.type == 'progress'
                             and e.data.get('stage') == 'history' and e.data.get('status') == 'completed')
        assert history_event['history_origin_dropped'] == (0 if keep else 1)
        assert bool(history_event['history_origin_reasons']) is (not keep)
    run(scenario())


def test_history_pair_cannot_mix_lineage_snapshots():
    user, assistant = messages('previous', 'previous question')
    user = SimpleNamespace(**{**asdict(user), 'knowledge_base_scope':('kb',), 'document_scope':()})
    assistant = SimpleNamespace(**{**asdict(assistant), 'knowledge_base_scope':('other-kb',), 'document_scope':()})
    with pytest.raises(PermissionError, match='HISTORY_ORIGIN_MISMATCH'):
        process_history([user, assistant], 5, scope=SCOPE, session_id='session')
