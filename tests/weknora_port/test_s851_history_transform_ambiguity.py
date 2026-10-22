"""Pure history transforms do not establish a new factual antecedent; offline only."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.models.types import ChatOptions, RerankResult
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.query import (
    known_ambiguous_reference, requires_ambiguity_clarification, resolve_intent,
    understand_query, validate_rewrite,
)
from backend.weknora_port.service import TurnConfiguration
from test_pipeline import Repo, Registry, row, state
from test_service import REQ, runtime

Q0 = 'What is it used for?'
COMPARE = 'Compare a Leyden jar and Franklin bells.'
FORMAT = 'Format the previous answer as a table.'
REPHRASE = 'Rephrase the previous answer.'


def previous(question, answer='Completed dialogue only'):
    return SimpleNamespace(query=question, answer=answer,
        knowledge_base_scope=('kb',), document_scope=())


@pytest.mark.parametrize('transforms', [(FORMAT,), (FORMAT, REPHRASE)])
def test_factual_pronoun_after_pure_transforms_preserves_comparison_ambiguity(transforms):
    s = state(query=Q0, history=[previous(COMPARE), *(previous(t) for t in transforms)])
    assert known_ambiguous_reference(s) is True
    assert requires_ambiguity_clarification(s) is True
    assert validate_rewrite(s, Q0) == 'AMBIGUOUS_REFERENCE'
    assert validate_rewrite(s, 'What is a Leyden jar used for?') == 'AMBIGUOUS_REFERENCE'
    s.intent = 'follow_up'
    assert resolve_intent(s) == 'kb_search'


def test_repeated_format_of_legacy_ambiguous_answer_is_still_fixed_clarification():
    s = state(query=REPHRASE, history=[previous(COMPARE), previous(Q0, 'Franklin bells are selected.'), previous(FORMAT)])
    assert requires_ambiguity_clarification(s) is True


@pytest.mark.parametrize('history', [
    [previous('What is a Leyden jar?'), previous(FORMAT)],
    [previous(COMPARE), previous(FORMAT), previous('What is a Leyden jar?'), previous(REPHRASE)],
])
def test_pure_transform_does_not_hide_latest_single_subject_or_topic_reset(history):
    s = state(query=Q0, history=history)
    assert not known_ambiguous_reference(s)
    assert not requires_ambiguity_clarification(s)
    assert validate_rewrite(s, 'What is a Leyden jar used for?') == ''


def test_only_closed_transform_is_skipped_and_independent_question_still_retrieves():
    s = state(query='What is a battery?', history=[previous(COMPARE), previous(FORMAT)])
    assert not requires_ambiguity_clarification(s)
    s.intent = 'unknown'
    assert resolve_intent(s) == 'kb_search'
    # A format-like factual instruction is a new question, not the closed
    # previous-answer transform allowlist. Do not scan across it.
    s = state(query=Q0, history=[previous(COMPARE), previous('Explain the previous answer using a capacitor.')])
    assert not known_ambiguous_reference(s)
    assert validate_rewrite(s, 'What is a Leyden jar used for?') == 'REFERENCE_NOT_GROUNDED'


@pytest.mark.parametrize('score', [.01, .9])
def test_actual_prepare_turn_retrieves_after_format_then_clarifies_without_model_answer(score):
    async def scenario():
        service, repo, _, coordinator, cheap, expensive, *_ = runtime()
        rows = [row('source-' + str(i), 'Original candidate number ' + str(i) + ' about unresolved objects.') for i in range(11)]
        retrieval = Repo(rows, chunks={r.id: r for r in rows})
        calls = {'rewrite': 0, 'rerank': 0, 'catalog': 0}
        class Models:
            async def chat(self, messages, options, context):
                calls['rewrite'] += 1
                return SimpleNamespace(content=json.dumps({'rewrite_query': Q0, 'intent': 'kb_search', 'image_description': ''}), finish_reason='stop')
            async def rerank(self, query, documents, context):
                calls['rerank'] += 1
                assert query == Q0
                return SimpleNamespace(results=[RerankResult(i, score) for i in range(len(documents))])
        async def loaded(*args, **kwargs):
            return (previous(COMPARE), previous(FORMAT, '| Item | Franklin bells |'))
        async def catalog():
            calls['catalog'] += 1
            return 'Catalog metadata only'
        service.history_service = SimpleNamespace(load=loaded)
        s = state(enable_rewrite=True, chat_model_id='cheap', rerank_model_id='rerank', language='English')
        service.configuration = lambda turn: TurnConfiguration(s, {'policy': RouterPolicy(mode='CHEAP_ONLY')},
            fallback={'strategy': 'model', 'response': 'Insufficient evidence', 'prompt': 'Evidence gap {{kb_documents}}',
                      'documents': catalog, 'options': ChatOptions(thinking=False)})
        async def pipeline(turn, progress):
            return RetrievalPipeline(retrieval, Registry(Models()), on_progress=progress)
        service.pipeline_factory = pipeline
        accepted = await repo.accept(replace(REQ, content=Q0), 'owner')
        prepared = await service.prepare_turn(accepted)
        assert prepared.request.fixed_fallback
        assert prepared.request.evidence_insufficient is False
        assert prepared.request.evidence == prepared.request.history == ()
        assert prepared.request.memory_prompt == ''
        assert prepared.request.finalized.result_ids == ()
        assert s.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'
        assert len(retrieval.calls) == 1 and retrieval.calls[0][1].query_text == Q0
        assert calls == {'rewrite': 1, 'rerank': 1, 'catalog': 0}
        result = await coordinator.run(prepared.request, prepared.context)
        assert not cheap.calls and not expensive.calls and not result.references
        assert 'Franklin' not in result.answer and 'specify' in result.answer.lower()
    asyncio.run(scenario())
