"""Source headers affect scoring input, never MMR redundancy or source quotes."""
import asyncio
import importlib
import json
from dataclasses import replace

import httpx
import pytest

from backend.weknora_port.models import provider_config, ProviderError
from backend.weknora_port.retrieval.rerank import enriched_passage, model_passage, rerank
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.conversation_configuration import resolve_saved_conversation
from tests.weknora_port.test_pipeline import CTX, TARGET, Model, Registry
from tests.weknora_port.test_provider_transport import setup, context
from tests.weknora_port.test_knowledge_storage import SourceConnection, fixture_row, tenant


def row(id='a', body='source body', **kw):
    return SearchResult(id, body, .5, knowledge_id='doc', knowledge_base_id='kb', **kw)


def test_title_and_persisted_breadcrumb_precede_body_without_index_text_substitution():
    source = row(knowledge_title='Source title', context_header='Chapter > Section',
                 matched_content='different expanded index text')
    assert model_passage(source) == 'Source title\n\nChapter > Section\n\nsource body'
    assert enriched_passage(source) == 'source body'
    assert source.content == 'source body' and source.context_header == 'Chapter > Section'
    assert 'expanded' not in model_passage(source)


@pytest.mark.parametrize('body', ['', '  ', '# '])
def test_title_cannot_turn_an_empty_body_into_evidence(body):
    assert model_passage(row(body=body, knowledge_title='title', context_header='header')) == ''


def test_faq_keeps_its_independent_question_and_no_filename_is_inferred_as_a_header():
    assert model_passage(row(chunk_type='faq', knowledge_title='title', context_header='header')) == 'source body'
    assert model_passage(row(knowledge_filename='not-a-chapter.pdf')) == 'source body'


def test_only_exact_image_repetition_is_deduplicated_in_enhanced_input():
    source = row(body='same text', image_info=json.dumps([
        {'ocr_text': ' same text ', 'caption': 'Different meaning'},
        {'ocr_text': 'same text', 'caption': 'Different meaning'},
        {'ocr_text': 'same text with a qualifier'}]))
    actual = enriched_passage(source, deduplicate_images=True)
    assert actual == 'same text\n\nDifferent meaning\nsame text with a qualifier'
    assert enriched_passage(source).count('Different meaning') == 2  # legacy input frozen


def test_source_repository_maps_header_and_preserves_version_and_raw_body():
    async def run():
        stored = fixture_row()
        stored.update(context_header='Persisted section', knowledge_title='Trusted title',
                      knowledge_metadata={'source_version': 'v1'})
        conn = SourceConnection([stored])
        retriever = PostgresRetriever(conn, IsolationPolicy('new', frozenset({'old'})))
        await retriever.verify_identity()
        actual = (await ScopedKnowledgeRepository(retriever, tenant).get_chunks(['chunk'], CTX))['chunk']
        assert actual.context_header == 'Persisted section'
        assert actual.metadata['source_version'] == 'v1'
        assert actual.content == stored['content'] and actual.start_at == stored['start_at']
        assert model_passage(actual).startswith('Trusted title\n\nPersisted section\n\n')
    asyncio.run(run())


def test_model_scoring_and_mmr_use_distinct_passages_and_preserve_provider_indices(monkeypatch):
    module = importlib.import_module('backend.weknora_port.retrieval.rerank')
    observed = []
    original = module.mmr
    def capture(candidates, passages, k, segmenter=None):
        observed.append(passages.copy())
        return original(candidates, passages, k, segmenter)
    monkeypatch.setattr(module, 'mmr', capture)
    async def run():
        from backend.weknora_port.models.types import RerankResult
        values = [row('empty', '', knowledge_title='title'),
                  row('one', 'alpha independent fact', knowledge_title='Common title', context_header='A'),
                  row('two', 'beta different fact', knowledge_title='Common title', context_header='B')]
        model = Model([[RerankResult(1, .91), RerankResult(0, .9)]])
        state = PipelineState(query='q', rewrite_query='q', targets=(TARGET,), search_results=values,
                              rerank_model_id='rank', rerank_context_enabled=True)
        await rerank(state, CTX, Registry(model))
        assert model.calls[0][1] == ['Common title\n\nA\n\nalpha independent fact',
                                     'Common title\n\nB\n\nbeta different fact']
        assert observed == [['beta different fact', 'alpha independent fact']]
        assert {r.id for r in state.rerank_results} == {'one', 'two'}
        assert {r.id: r.metadata['model_score'] for r in state.rerank_results} == {'one': '0.9000', 'two': '0.9100'}
    asyncio.run(run())


def test_real_adapter_wire_contains_passage_and_applies_existing_length_limit():
    async def run():
        seen = []
        cfg = provider_config('siliconflow', 'fixture')
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'results': [{'index': 0, 'relevance_score': .8}],
                                             'meta': {'tokens': {'input_tokens': 12, 'output_tokens': 0}}})
        model, transport, gate = setup(handler, config=cfg)
        try:
            await model.rerank('query', [model_passage(row(knowledge_title='Title', context_header='Chapter'))], context())
            assert seen[0]['documents'] == ['Title\n\nChapter\n\nsource body']
            assert len(gate.calls) == 1
        finally:
            await transport.aclose()
        cfg = replace(cfg, reranking=replace(cfg.reranking, max_document_chars=5))
        model, transport, gate = setup(handler, config=cfg)
        try:
            with pytest.raises(ProviderError, match='LIMIT|LARGE|EXCEED'):
                await model.rerank('query', ['a document beyond limit'], context('too-long'))
            assert not gate.calls and len(seen) == 1
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_server_conversation_option_is_explicit_and_strictly_typed():
    cfg = ConversationSettings()
    assert resolve_saved_conversation(cfg, {})['rerank_context_enabled'] is False
    saved = {'context_config': {'conversation': {'rerank_context_enabled': True}}}
    assert resolve_saved_conversation(cfg, saved)['rerank_context_enabled'] is True
    assert cfg.rerank_context_enabled is False
    with pytest.raises(ProviderError, match='RERANK_CONTEXT_SETTING_INVALID'):
        resolve_saved_conversation(cfg, {'context_config': {'conversation': {'rerank_context_enabled': 'true'}}})


def test_normal_startup_loads_context_option_without_enabling_it_by_default(tmp_path):
    from backend.weknora_port.development import DevelopmentSettings
    from tests.weknora_port.test_development_entry import settings, development_environment
    env = development_environment(settings(tmp_path))
    assert DevelopmentSettings.from_environment(env).rerank_context_enabled is False
    env['WEKNORA_PORT_RERANK_CONTEXT_ENABLED'] = 'true'
    assert DevelopmentSettings.from_environment(env).rerank_context_enabled is True
