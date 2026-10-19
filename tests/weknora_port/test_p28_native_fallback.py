"""P28 offline orchestration tests. All provider/storage doubles are synthetic."""
import asyncio

import pytest

from backend.weknora_port.generation import RouterPolicy, prepare_generation
from backend.weknora_port.models.types import ChatOptions, ProviderError, RerankResult
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.search import RetrievalScopeError
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.service import TurnConfiguration
from test_pipeline import Model, Registry, state
from test_service import REQ, runtime


def configuration(strategy='model', **settings):
    return TurnConfiguration(state(**settings), {'policy': RouterPolicy(mode='CHEAP_ONLY')},
        {'strategy': strategy, 'prompt': 'NATIVE FALLBACK {{query}} {{kb_documents}}',
         'response': 'CONFIGURED FIXED', 'documents': lambda: '- title metadata',
         'options': ChatOptions(thinking=False)})


def test_default_model_template_retains_metadata_and_general_knowledge_boundaries():
    from backend.weknora_port.conversation_configuration import source_prompt_templates
    source = ConversationSettings()
    assert source.fallback_strategy == 'model'
    prompt = next(row['content'] for row in source_prompt_templates()['fallback']
                  if row['id'] == source.fallback_prompt_id)
    assert 'Treat the document listing as metadata' in prompt
    assert 'cannot support a summary of unread contents or detailed factual claims' in prompt
    assert 'answer is not from the knowledge base' in prompt


def test_native_rerank_disable_is_explicit_local_configuration():
    from backend.weknora_port.conversation_configuration import resolve_saved_conversation
    source = ConversationSettings()
    assert source.enable_rerank is True
    saved = {'context_config': {'conversation': {'enable_rerank': False}}}
    assert resolve_saved_conversation(source, saved)['enable_rerank'] is False
    assert resolve_saved_conversation(source, {})['enable_rerank'] is True
    assert source.rerank_threshold == .3


@pytest.mark.parametrize('strategy', ['model', 'fixed'])
def test_native_rerank_empty_short_circuits_merge_and_respects_fallback(strategy):
    async def scenario():
        cfg = configuration(strategy, rerank_model_id='reranker')
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(configuration=lambda _: cfg)
        # A successful low score is now Top1 evidence in this experiment;
        # an actually empty response still preserves the native fallback.
        model = Model([[]])
        def pipeline(turn, progress):
            return RetrievalPipeline(retrieval, Registry(model), on_progress=progress)
        service.pipeline_factory = pipeline
        await service.accept_turn(REQ, 'owner')
        await service.drain_background()
        assert not repo.failures and len(model.calls) == 1
        assert cfg.pipeline_state.search_results and not cfg.pipeline_state.rerank_results
        assert not cfg.pipeline_state.merge_results
        assert not any(e.type == 'progress' and e.data.get('stage') == 'merge' for e in store.events)
        assert len(cheap.calls) == (strategy == 'model') and not expensive.calls
        assert commit.calls[0].answer == ('partial' if strategy == 'model' else 'CONFIGURED FIXED')
        assert not commit.calls[0].references
    asyncio.run(scenario())


def test_normal_hit_does_not_use_fallback_even_when_configured_fixed():
    async def scenario():
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(
            configuration=lambda _: configuration('fixed'))
        turn = await repo.accept(REQ, 'owner')
        prepared = await service.prepare_turn(turn)
        assert not prepared.request.is_fallback and prepared.request.fixed_fallback is None
        assert prepared.request.finalized.result_ids == ('chunk-real',)
        assert 'real source source evidence' in '\n'.join(m.content for m in prepare_generation(prepared.request).messages)
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', [PermissionError('private'), RetrievalScopeError(),
    ProviderError('SOURCE_EVIDENCE_UNAVAILABLE'), ProviderError('SOURCE_VERSION_MISMATCH')])
def test_source_failure_is_terminal_not_native_fallback(failure):
    async def scenario():
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(
            configuration=lambda _: configuration())
        async def project(state, context):
            raise failure
        service.pipeline_factory = lambda turn, progress: RetrievalPipeline(
            retrieval, Registry(), on_progress=progress, final_evidence_projection=project)
        turn = await repo.accept(REQ, 'owner')
        with pytest.raises(type(failure)):
            await service.prepare_turn(turn)
        assert not cheap.calls and not expensive.calls and not commit.calls
    asyncio.run(scenario())


def test_absent_fallback_does_not_invent_a_fixed_policy():
    async def scenario():
        from backend.weknora_port.retrieval.search import SearchNothing
        service, repo, retrieval, *_ = runtime()
        retrieval.rows = []
        turn = await repo.accept(REQ, 'owner')
        with pytest.raises(SearchNothing):
            await service.prepare_turn(turn)
    asyncio.run(scenario())
