"""Offline denial propagation through the normal retrieval/service contracts."""
import asyncio
import json

import pytest

from backend.weknora_port.http_api import _error_response
from backend.weknora_port.models.cloud_errors import CLOUD_ERRORS
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.retrieval.query import understand_query
from backend.weknora_port.retrieval.rerank import rerank
from backend.weknora_port.retrieval.search import SearchTarget, search
from test_pipeline import CTX, Model, Registry, Repo, row, state
from test_service import REQ, runtime


@pytest.mark.parametrize('code', ['MODEL_CLOUD_DISABLED', 'MODEL_ORIGIN_KB_EGRESS_DENIED',
    'LOCAL_EXECUTION_LIMIT_DENIED', 'LOCAL_EXECUTION_UNKNOWN_STOP'])
@pytest.mark.parametrize('stage', ['rewrite', 'embedding', 'rerank'])
def test_denials_are_not_optional_model_fallbacks(stage, code):
    async def scenario():
        error = ProviderError(code)
        current = state(enable_rewrite=True, chat_model_id='chat', rerank_model_id='rr')
        model = Model([error])
        repo = Repo([row()])
        if stage == 'rewrite':
            operation = understand_query(current, CTX, Registry(model))
        elif stage == 'embedding':
            current.targets = (SearchTarget('kb', embedding_model_key='embedding'),)
            async def embed(*args): raise error
            operation = search(current, CTX, repo, embedding_resolver=embed)
        else:
            current.search_results = [row()]
            operation = rerank(current, CTX, Registry(model))
        with pytest.raises(ProviderError) as caught:
            await operation
        assert caught.value is error
        # Embedding denial must not silently become keyword-only retrieval.
        assert not repo.calls
    asyncio.run(scenario())


def test_model_lookup_denial_preserves_identity_instead_of_trying_another_role():
    class DeniedRegistry:
        async def chat(self, *args): raise ProviderError('MODEL_NOT_FOUND')
        async def rerank(self, *args): raise ProviderError('LOCAL_EXECUTION_SCOPE_DENIED')
    async def scenario():
        current = state(enable_rewrite=True, chat_model_id='chat', rerank_model_id='rr')
        with pytest.raises(ProviderError, match='MODEL_NOT_FOUND'):
            await understand_query(current, CTX, DeniedRegistry())
        current.search_results = [row()]
        with pytest.raises(ProviderError, match='LOCAL_EXECUTION_SCOPE_DENIED'):
            await rerank(current, CTX, DeniedRegistry())
    asyncio.run(scenario())


@pytest.mark.parametrize('code', ['MODEL_CLOUD_DISABLED', 'LOCAL_EXECUTION_LIMIT_DENIED',
    'LOCAL_EXECUTION_UNKNOWN_STOP', 'LOCAL_EXECUTION_SETTLEMENT_FAILED'])
def test_normal_service_persists_safe_failure_and_terminal_sse(code):
    async def scenario():
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(error=ProviderError(code))
        await service.accept_turn(REQ, 'owner')
        await service.drain_background()
        errors = [event for event in store.events if event.type == 'error']
        assert len(errors) == 1 and errors[0].data['error_code'] == code
        assert errors[0].content == CLOUD_ERRORS[code]
        assert store.events[-1].type == 'complete' and store.events[-1].data['status'] == 'failed'
        assert repo.failures == [('assistant', code)]
        assert not cheap.calls and not expensive.calls and not commit.calls
    asyncio.run(scenario())


def test_http_error_message_is_static_and_unknown_codes_stay_unchanged():
    body = json.loads(_error_response('MODEL_CLOUD_DISABLED', 403, 'request').body)
    assert body['error'] == {'code': 'MODEL_CLOUD_DISABLED', 'message': CLOUD_ERRORS['MODEL_CLOUD_DISABLED']}
    assert body['meta']['request_id'] == 'request'
    assert json.loads(_error_response('UNRECOGNIZED_ERROR', 422, 'request').body)['error']['message'] == 'UNRECOGNIZED_ERROR'


@pytest.mark.parametrize('code', ['LOCAL_EXECUTION_LIMIT_DENIED', 'LOCAL_EXECUTION_SETTLEMENT_FAILED'])
def test_stream_denial_after_answer_done_cannot_be_a_success_or_upgrade(code):
    from test_generation import setup, request, event, CTX as GENERATION_CONTEXT
    coordinator, cheap, expensive, store, commit, lifecycle = setup([
        event('completed draft', done=True, finish='stop'),
        event(kind='error', final=True, data={'error_code': code, 'error_class': 'ProviderError'}),
    ])
    with pytest.raises(ProviderError, match=code):
        asyncio.run(coordinator.run(request(), GENERATION_CONTEXT))
    assert len(cheap.calls) == 1 and not expensive.calls and not commit.calls
    assert lifecycle.finished[0][0].status == 'FAILED'
