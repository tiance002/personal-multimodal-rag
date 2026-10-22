"""Offline citation observations: no semantic validator or new escalation rule."""
import asyncio
import pytest

from backend.weknora_port.generation.coordinator import public_citation_quality
from backend.weknora_port.generation.context import Evidence
from backend.weknora_port.retrieval.context import FinalizedContext
from backend.weknora_port.models.types import ProviderError
from tests.weknora_port.test_generation import CTX, event, request, setup


@pytest.mark.parametrize(('answer', 'status', 'valid', 'invalid'), [
    ('Supported fact <ref id="c1"/>', 'VALID', 1, 0),
    ('Supported fact without citation.', 'MISSING', 0, 0),
    ('Bad source <ref id="c99"/>', 'INVALID', 0, 1),
    ('Both <ref id="c1"/> <ref id="c99"/>', 'INVALID', 1, 1),
    ('Example code `<ref id="c1"/>` is not a citation.', 'MISSING', 0, 0),
])
def test_citation_observation_is_persisted_without_claiming_semantic_support(answer, status, valid, invalid):
    co, cheap, expensive, store, commit, life = setup([event(answer, done=True, finish='stop', final=True)])
    result = asyncio.run(co.run(request('CHEAP_ONLY'), CTX))
    quality = public_citation_quality(result.attempts[0])
    assert quality == dict(status=status, valid_handle_count=valid, invalid_handle_count=invalid,
                          usable_evidence_count=1, assessment_complete=True, semantic_support='NOT_ASSESSED')
    assert store.events[-1].data['citation_quality'] == quality
    assert store.events[-1].data['attempts'][0]['citation_quality'] == quality
    assert public_citation_quality(life.finished[0][0]) == quality
    assert len(cheap.calls) == len(commit.calls) == 1 and not expensive.calls


def test_missing_citation_does_not_trigger_dynamic_expensive_upgrade():
    co, cheap, expensive, store, commit, life = setup([event('A fact without a citation.', done=True, finish='stop', final=True)])
    result = asyncio.run(co.run(request(), CTX))
    assert result.selected_role == 'cheap' and len(result.attempts) == 1 and not result.upgraded
    assert len(cheap.calls) == 1 and not expensive.calls
    assert result.attempts[0].objective == ()
    assert store.events[-1].data['citation_quality']['status'] == 'MISSING'
    assert store.events[-1].data['generation_issues'] == ['MISSING_CITATIONS']


def test_existing_invalid_handle_upgrade_keeps_attempt_observations_separate():
    co, cheap, expensive, store, commit, life = setup([event('Bad <ref id="c99"/>', done=True, finish='stop', final=True)])
    result = asyncio.run(co.run(request(), CTX))
    assert len(cheap.calls) == len(expensive.calls) == 1 and result.upgraded
    assert result.attempts[0].citation_quality.status == 'INVALID'
    assert result.attempts[1].citation_quality.status == 'VALID'
    assert result.attempts[1].upgrade_reason == 'INVALID_HANDLES'
    assert store.events[-1].data['citation_quality']['status'] == 'VALID'


@pytest.mark.parametrize('content', ['', ' \n\t'])
def test_empty_source_is_evidence_insufficient_not_proven_reasonable_refusal(content):
    evidence = Evidence('empty', content, 'doc', 'kb')
    req = request(evidence=(evidence,), finalized=FinalizedContext('Question', '', ('empty',)))
    co, cheap, expensive, store, commit, life = setup([event('There is insufficient evidence to answer.', done=True, finish='stop', final=True)])
    result = asyncio.run(co.run(req, CTX))
    quality = public_citation_quality(result.attempts[0])
    assert quality['status'] == 'EVIDENCE_INSUFFICIENT' and quality['usable_evidence_count'] == 0
    assert quality['semantic_support'] == 'NOT_ASSESSED'
    assert not result.upgraded and not expensive.calls


def test_no_evidence_refusal_and_citations_disabled_are_distinct():
    co, *_ = setup([event('Insufficient sources.', done=True, finish='stop', final=True)])
    req = request(evidence=(), finalized=FinalizedContext('Question', '', ()))
    assert asyncio.run(co.run(req, CTX)).attempts[0].citation_quality.status == 'EVIDENCE_INSUFFICIENT'
    co, *_ = setup([event('Plain answer.', done=True, finish='stop', final=True)])
    assert asyncio.run(co.run(request(citations_enabled=False), CTX)).attempts[0].citation_quality.status == 'NOT_REQUIRED'


def test_thinking_only_citation_does_not_satisfy_final_answer_citation():
    co, *_ = setup([event('Thinking <ref id="c1"/>', done=True, kind='thinking'),
                   event('Uncited answer.', done=True, finish='stop', final=True)])
    assert asyncio.run(co.run(request('CHEAP_ONLY'), CTX)).attempts[0].citation_quality.status == 'MISSING'


def test_empty_answer_has_no_completed_citation_assessment():
    co, *_ = setup([event('', done=True, finish='stop', final=True)])
    result = asyncio.run(co.run(request('CHEAP_ONLY'), CTX))
    quality = public_citation_quality(result.attempts[0])
    assert quality['status'] == 'NOT_ASSESSED' and not quality['assessment_complete']


def test_failed_attempt_exposes_only_incomplete_observation_to_existing_progress_hook():
    co, cheap, expensive, store, commit, life = setup([
        event('Partial uncited answer.'), ProviderError('PROVIDER_TRANSPORT_ERROR', sent=True)])
    progress = []
    co.on_progress = lambda stage, status, **fields: progress.append((stage, status, fields))
    with pytest.raises(ProviderError):
        asyncio.run(co.run(request(), CTX))
    quality = public_citation_quality(life.finished[0][0])
    assert quality['status'] == 'NOT_ASSESSED' and not quality['assessment_complete']
    assert progress[-1][1] == 'failed' and progress[-1][2]['citation_quality'] == quality
    assert not expensive.calls and not commit.calls
    assert not any(e.type == 'complete' for e in store.events)


def test_fixed_fallback_complete_event_records_citations_not_required():
    co, cheap, expensive, store, commit, life = setup([])
    result = asyncio.run(co.run(request(fixed_fallback='No documents.'), CTX))
    assert not result.attempts and not cheap.calls and not expensive.calls
    assert store.events[-1].data['citation_quality']['status'] == 'NOT_REQUIRED'


@pytest.mark.parametrize('citations_enabled,expected', [(True, 'EVIDENCE_INSUFFICIENT'), (False, 'NOT_REQUIRED')])
def test_server_known_empty_evidence_fixed_refusal_retains_reason_in_result_commit_and_replay(citations_enabled, expected):
    from backend.weknora_port.streaming.replay import project_final_quality
    co, cheap, expensive, store, commit, life = setup([])
    req = request(citations_enabled=citations_enabled, evidence_insufficient=True,
        evidence=(), finalized=FinalizedContext('Question', '', ()), is_fallback=True, fixed_fallback='No matching evidence.')
    result = asyncio.run(co.run(req, CTX))
    assert not result.attempts and not cheap.calls and not expensive.calls
    assert result.citation_quality.status == expected
    assert result.citation_quality.semantic_support == 'NOT_ASSESSED'
    quality = commit.calls[0].quality
    assert quality['citation_quality']['status'] == store.events[-1].data['citation_quality']['status'] == expected
    persisted = dict(role='assistant', is_completed=True, request_id='run', agent_steps=[
        {'type': 'generation_quality', 'request_id': 'run', **quality}])
    assert project_final_quality(persisted)['citation_quality']['status'] == expected


def test_server_known_empty_evidence_model_fallback_is_observed_without_new_upgrade():
    co, cheap, expensive, store, commit, life = setup([event('I lack the evidence.', done=True, finish='stop', final=True)])
    result = asyncio.run(co.run(request(evidence_insufficient=True, is_fallback=True, evidence=(),
        finalized=FinalizedContext('Question', '', ())), CTX))
    assert result.attempts[0].citation_quality.status == result.citation_quality.status == 'EVIDENCE_INSUFFICIENT'
    assert result.attempts[0].objective == () and not result.upgraded and not expensive.calls
