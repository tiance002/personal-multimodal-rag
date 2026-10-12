import json
from pathlib import Path
from backend.app.application.run_progress import RunProgress
from backend.tests.test_answer_service import RecordingRunStore, _conversation
from backend.app.application.answer_service import AnswerService
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord


def test_progress_projection_excludes_content_and_unknown_errors():
    store = RecordingRunStore()
    RunProgress(store, 'r').emit('generation', 'failed', role='cheap', attempt_id='attempt-1',
        reason_code='raw private exception', question='PRIVATE', answer='PRIVATE',
        candidate_count=True, selected_evidence_count=-1)
    data = store.events[0][2]
    assert data == dict(schema_version='run-progress/v1', stage='generation', status='failed',
        role='cheap', attempt_id='attempt-1', reason_code='STAGE_FAILED')
    schema = json.loads(Path('contracts/sse/events.schema.json').read_text())
    contract = schema['allOf'][0]['then']['properties']['data']
    assert set(data) <= set(contract['properties'])
    assert set(contract['required']) <= set(data)
    assert contract['additionalProperties'] is False
    assert 'answer' not in contract['properties']


def test_progress_store_failure_does_not_change_pipeline():
    class Broken:
        def append_event(self, *args):
            raise RuntimeError('database unavailable')
    RunProgress(Broken(), 'r').emit('retrieval', 'started')


def test_retrieval_and_prepared_progress_precede_frozen_final_facts():
    store = RecordingRunStore()
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord('c', 'kb', 'doc', 'ver', 'evidence passage', {'start': 0}))
    result = AnswerService(retriever=HybridRetriever(repo), runs=store).answer(_conversation(), 'evidence')
    assert result.error_code is None
    rows = store.events
    found = next(i for i, (_, e, d) in enumerate(rows)
        if e == 'run.progress' and d['stage'] == 'retrieval' and d['status'] == 'completed')
    prepared = next(i for i, (_, e, d) in enumerate(rows)
        if e == 'run.progress' and d['stage'] == 'evidence')
    final = next(i for i, (_, e, _) in enumerate(rows) if e == 'evidence.frozen')
    assert found < prepared < final
    assert rows[found][2]['candidate_count'] == 1
    assert rows[prepared][2]['selected_evidence_count'] == 1
    assert [r for _, r, _ in store.messages] == ['user', 'assistant']


def test_zero_candidates_is_real_count_then_authoritative_failure():
    store = RecordingRunStore()
    result = AnswerService(retriever=HybridRetriever(InMemoryRetrievalRepository()), runs=store).answer(_conversation(), 'missing evidence')
    assert result.error_code == 'NO_CANDIDATES'
    assert any(e == 'run.progress' and d.get('candidate_count') == 0 for _, e, d in store.events)
    assert store.events[-1][1] == 'run.failed'
    assert [r for _, r, _ in store.messages] == ['user']
