"""Formal service/pipeline contracts with MockTransport and in-memory storage.

ProviderModel, retrieval, generation, replay and ordinary history are real code;
admission, persistence and usage gate are existing offline test doubles. No PG,
Redis, socket, hosted provider, production data or account authority is used.
"""
import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from backend.weknora_port.conversation.history import OrdinaryHistoryService
from backend.weknora_port.conversation.types import StoredMessage
from backend.weknora_port.generation import RoleBinding, RouterPolicy
from backend.weknora_port.models import ChatOptions, ProviderError, provider_config
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.service import TurnConfiguration
from test_pipeline import Repo, row, state
from test_provider_transport import Gate, setup as provider_setup
from test_service import REQ, runtime

FIXED = 'The available knowledge-base evidence is insufficient to answer this question.'
MODES = ('off', 'bounded_source_v1')
USAGE = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}


@asynccontextmanager
async def case(mode, *, strategy='fixed', scores='low', fault=None, unknown=False):
    wire = []
    class FixtureGate(Gate):
        async def authorize(self, call):
            if fault == 'denied' and call.capability == 'rerank':
                raise ProviderError('MODEL_ORIGIN_KB_EGRESS_DENIED')
            await super().authorize(call)
    gate = FixtureGate()
    models, transports = {}, []

    def handler(request):
        body = json.loads(request.content)
        wire.append(body)
        if body.get('stream'):
            if unknown:
                raise httpx.ReadError('synthetic response unknown', request=request)
            text = FIXED if scores == 'low' else 'Supported source answer <ref id="c1"/>'
            rows = [{'model': body['model'], 'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}], 'usage': USAGE}]
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content=''.join('data: '+json.dumps(r)+'\n\n' for r in rows)+'data: [DONE]\n\n')
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1., 0.]}], 'usage': USAGE})
        if request.url.path.endswith('/rerank'):
            if fault == 'http':
                return httpx.Response(503, json={'error': {'message': 'synthetic unavailable'}})
            if fault == 'invalid':
                # Duplicate indices are rejected by the actual pipeline validator.
                results = [{'index': 0, 'score': .1}, {'index': 0, 'score': .1}]
            else:
                results = [{'index': i, 'score': .8 if scores == 'normal' and i == 0 else .1}
                    for i in range(len(body['documents']))]
            return httpx.Response(200, json={'model': body['model'], 'results': results, 'usage': USAGE})
        return httpx.Response(200, json={'model': body['model'], 'choices': [{'message': {
            'content': json.dumps({'rewrite_query': REQ.content, 'intent': 'kb_search'})}, 'finish_reason': 'stop'}], 'usage': USAGE})

    for role in ('query', 'embedding', 'rerank'):
        config = provider_config('openai-compatible', 'synthetic-'+role,
            base_url='https://fixture.example/v1')
        models[role], transport, _ = provider_setup(handler, config=config, gate=gate)
        transports.append(transport)

    class Registry:
        async def chat(self, model_id, context):
            return models['query']
        async def rerank(self, model_id, context):
            return models['rerank']

    rows = [replace(row('chunk-'+str(i), 'Synthetic source '+str(i)+' has unique fact_'+str(i),
        metadata={'source_version': 'v1'}, chunk_metadata={'source_version': 'v1'}),
        knowledge_id='doc-'+str(i)) for i in range(30)]
    for item in rows:
        item.end_at = len(item.content)

    class Sources(Repo):
        async def get_source_unit(self, anchor, context):
            return [deepcopy(self.chunks[anchor.id])]

    sources = Sources(rows, chunks={r.id: r for r in rows})
    pipeline_state = state(enable_rewrite=True, chat_model_id='query', rerank_model_id='rerank',
        source_evidence_mode=mode, rerank_top_k=1)
    pipeline_state.targets = tuple(replace(t, embedding_model_key='embedding') for t in pipeline_state.targets)
    config = TurnConfiguration(pipeline_state,
        {'policy': RouterPolicy(mode='CHEAP_ONLY'), 'citations_enabled': True},
        {'strategy': strategy, 'prompt': 'Answer only if valid evidence is available: {{query}}',
         'response': FIXED, 'documents': lambda: '- title only', 'options': ChatOptions(max_tokens=64)})
    service, repository, _, coordinator, cheap, expensive, store, commit, counts = runtime(configuration=lambda _: config)
    cfg = models['query'].config
    coordinator.bindings['cheap'] = RoleBinding('cheap', cfg.provider, cfg.model, cfg.base_url, models['query'])

    async def embedding(key, query, context):
        return (await models['embedding'].embed((query,), context.child('query_embedding'))).vectors[0]

    def pipeline_factory(turn, progress):
        counts['pipeline'] += 1
        return RetrievalPipeline(sources, Registry(), embedding_resolver=embedding, on_progress=progress)
    service.pipeline_factory = pipeline_factory

    messages = [StoredMessage('user', 'run', 'session', 1, 'owner', 'user', REQ.content,
        datetime(2026, 10, 20, tzinfo=timezone.utc))]
    async def recent_messages(scope, session_id, limit):
        return messages[-limit:]
    service.history_service = OrdinaryHistoryService(SimpleNamespace(recent_messages=recent_messages))
    original_commit = commit.commit_final
    async def persist(data):
        await original_commit(data)
        messages.append(StoredMessage(data.message_id, data.request_id, data.session_id, 1,
            'owner', 'assistant', data.answer, messages[0].created_at,
            knowledge_references=tuple(data.references)))
    commit.commit_final = persist
    try:
        yield SimpleNamespace(service=service, repository=repository, state=pipeline_state,
            sources=sources, coordinator=coordinator, gate=gate, wire=wire, commit=commit,
            store=store, counts=counts, cheap=cheap, expensive=expensive)
    finally:
        for transport in transports:
            await transport.aclose()


async def completed(fixture):
    accepted = await fixture.service.accept_turn(REQ, 'owner')
    await fixture.service.drain_background()
    assert not fixture.repository.failures and not fixture.service.background_errors
    assert len(fixture.commit.calls) == 1
    return accepted, fixture.commit.calls[0]


async def assert_no_resend(fixture, accepted):
    before = (len(fixture.wire), len(fixture.gate.calls), len(fixture.gate.finishes))
    replay = await fixture.service.accept_turn(REQ, 'owner')
    assert replay.replay_only and await fixture.service.run_turn(replay) is None
    events = [e async for e in fixture.service.replay(accepted, 'owner')]
    assert sum(e.type == 'complete' for e in events) == 1
    if fixture.commit.calls:
        assert ''.join(e.content for e in events if e.type == 'answer') == fixture.commit.calls[0].answer
    assert before == (len(fixture.wire), len(fixture.gate.calls), len(fixture.gate.finishes))
    assert fixture.counts['pipeline'] == 1


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('strategy', ('fixed', 'model'))
def test_threshold_rejection_closes_no_evidence_turn_without_resurrecting_search(mode, strategy):
    async def scenario():
        async with case(mode, strategy=strategy) as f:
            accepted, persisted = await completed(f)
            assert len(f.state.search_results) == 30 and not f.state.rerank_results and not f.state.merge_results
            assert len(f.sources.calls) == 1
            rank = next(body for body in f.wire if 'documents' in body)
            assert len(rank['documents']) == 30
            assert persisted.answer == FIXED and persisted.references == []
            assert ''.join(e.content for e in f.store.events if e.type == 'answer') == FIXED
            assert sum(e.type == 'answer' and e.done for e in f.store.events) == 1
            assert not any(e.type in ('error', 'references') for e in f.store.events)
            assert f.store.trace[-2:] == ['COMMIT', 'complete']
            complete_event = f.store.events[-1]
            assert complete_event.data['is_fallback'] is True
            assert len(complete_event.data['attempts']) == (strategy == 'model')
            history = await f.service.history_service.load(accepted.memory_context, accepted.session_id, 5)
            assert len(history) == 1 and history[0].answer == FIXED and not history[0].knowledge_references
            # Fixed strategy avoids final generation; it does not erase the
            # three actual preceding QU/embedding/rerank calls or their usage.
            assert [call.capability for call in f.gate.calls[:3]] == ['chat', 'embedding', 'rerank']
            assert len(f.gate.calls) == 3 + (strategy == 'model')
            assert [usage.total_tokens for _, _, usage in f.gate.finishes[:3]] == [15, 15, 15]
            assert all(outcome == 'RESPONSE_RECEIVED' for _, outcome, _ in f.gate.finishes[:3])
            streams = [body for body in f.wire if body.get('stream')]
            assert len(streams) == (strategy == 'model')
            if streams:
                assert 'Synthetic source' not in str(streams[0]['messages'])
            assert not f.cheap.calls and not f.expensive.calls
            await assert_no_resend(f, accepted)
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', MODES)
def test_model_fallback_unknown_keeps_single_attempt_and_replay_never_sends(mode):
    async def scenario():
        async with case(mode, strategy='model', unknown=True) as f:
            accepted, persisted = await completed(f)
            assert persisted.answer == FIXED and persisted.references == []
            assert len(f.gate.calls) == 4 and f.gate.finishes[-1][1] == 'UNKNOWN'
            attempt = f.coordinator.lifecycle.finished[0][0]
            assert attempt.status == 'FAILED' and attempt.cost_status == 'UNKNOWN'
            await assert_no_resend(f, accepted)
            assert f.gate.finishes[-1][1] == 'UNKNOWN'
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('fault', ('http', 'denied', 'invalid'))
def test_rerank_fault_is_not_misreported_as_empty_evidence(mode, fault):
    async def scenario():
        async with case(mode, scores='normal', fault=fault) as f:
            accepted = await f.service.accept_turn(REQ, 'owner')
            await f.service.drain_background()
            assert not any(e.data.get('is_fallback') for e in f.store.events)
            if fault == 'http':
                # Existing source policy degrades rerank HTTP faults to Search.
                assert len(f.commit.calls) == 1 and f.commit.calls[0].references
                assert f.commit.calls[0].answer != FIXED
                assert any(e.type == 'references' for e in f.store.events)
            else:
                assert not f.commit.calls and len(f.repository.failures) == 1
                expected = 'MODEL_ORIGIN_KB_EGRESS_DENIED' if fault == 'denied' else 'ORDINARY_CHAIN_FAILED'
                assert f.repository.failures[0][1] == expected
                assert not any(body.get('stream') for body in f.wire)
            await assert_no_resend(f, accepted)
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', MODES)
def test_normal_admitted_evidence_still_generates_with_real_reference(mode):
    async def scenario():
        async with case(mode, scores='normal') as f:
            accepted, persisted = await completed(f)
            assert len(f.state.search_results) == 30 and len(f.state.rerank_results) == 1
            assert [r['id'] for r in persisted.references] == ['chunk-0']
            assert 'chunk_id="chunk-0"' in persisted.answer and persisted.answer != FIXED
            assert f.store.events[-1].data['is_fallback'] is False
            assert len(f.gate.calls) == 4 and all(usage.total_tokens == 15 for _, _, usage in f.gate.finishes)
            await assert_no_resend(f, accepted)
    asyncio.run(scenario())
