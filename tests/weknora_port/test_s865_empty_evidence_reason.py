"""The known SearchNothing reason is server-owned; no text refusal classifier."""
import asyncio
import json

import pytest

from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.models.types import ChatOptions, ProviderError
from backend.weknora_port.retrieval.search import SearchNothing
from backend.weknora_port.service import TurnConfiguration
from tests.weknora_port.test_service import REQ, runtime, state


def test_only_non_ambiguous_search_nothing_marks_server_known_evidence_gap():
    async def scenario():
        config = lambda turn: TurnConfiguration(state(), {'citations_enabled': True, 'policy': RouterPolicy(mode='DYNAMIC')},
            fallback={'strategy': 'fixed', 'response': 'No matching evidence.', 'prompt': '', 'options': ChatOptions()})
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(configuration=config)
        class EmptyPipeline:
            async def run(self, state, context): raise SearchNothing()
        service.pipeline_factory = lambda turn, progress: EmptyPipeline()
        turn = await repo.accept(REQ, 'owner')
        prepared = await service.prepare_turn(turn)
        assert prepared.request.evidence_insufficient is True
        assert prepared.request.evidence == () and prepared.request.fixed_fallback == 'No matching evidence.'
        result = await co.run(prepared.request, prepared.context)
        assert result.citation_quality.status == 'EVIDENCE_INSUFFICIENT'
        assert not cheap.calls and not expensive.calls
        # The existing PostgreSQL adapter's scripted boundary persists the same
        # quality as the complete event, even without any generation attempt.
        from tests.weknora_port.test_history_storage import HistoryConnection, adapter
        from backend.weknora_port.streaming.replay import project_final_quality
        connection = HistoryConnection({'is_completed': False, 'agent_steps': []})
        await adapter(connection).commit_final(commit.calls[0])
        updates = [args for sql, args in connection.calls if sql.startswith('UPDATE')]
        steps = next(json.loads(value) for value in updates[0]
                     if isinstance(value, str) and 'generation_quality' in value)
        projected = project_final_quality(dict(role='assistant', is_completed=True,
            request_id='run', agent_steps=steps))
        assert projected['citation_quality'] == store.events[-1].data['citation_quality']
        assert projected['citation_quality']['semantic_support'] == 'NOT_ASSESSED'
    asyncio.run(scenario())


def test_document_selection_clarification_keeps_reason_false():
    async def scenario():
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime()
        service.document_clarifier = lambda turn, language: 'Which document?'
        prepared = await service.prepare_turn(await repo.accept(REQ, 'owner'))
        assert prepared.request.evidence_insufficient is False
        assert prepared.request.fixed_fallback == 'Which document?' and counts['pipeline'] == 0
    asyncio.run(scenario())


@pytest.mark.parametrize('clarification', [None, 'Which document?'])
def test_generation_options_cannot_spoof_server_known_evidence_reason(clarification):
    async def scenario():
        service, repo, *rest = runtime(configuration=lambda turn: TurnConfiguration(state(), {'evidence_insufficient': True}))
        service.document_clarifier = lambda turn, language: clarification
        with pytest.raises(ProviderError, match='TURN_GENERATION_IDENTITY_OVERRIDE'):
            await service.prepare_turn(await repo.accept(REQ, 'owner'))
    asyncio.run(scenario())
