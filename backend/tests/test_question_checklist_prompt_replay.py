"""SIMULATED frozen-source replay: prompt contracts, never answer quality."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.application.answer_hardening import detect_intents
from backend.app.application.answer_service import AnswerService
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.question_checklist import explicit_question_checklist
from backend.app.application.quick_chain import QuickSettings
from backend.app.application.retrieval import RetrievalItem, RetrievalResult
from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query
from backend.tests.test_answer_service import RecordingRunStore


FIXTURE = Path(__file__).with_name('fixtures') / 'question_checklist_prompt_replay.json'
DATA = json.loads(FIXTURE.read_text(encoding='utf-8'))
CASES = DATA['cases']


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class FrozenRetriever:
    """Query-independent SIMULATED pool; real coverage checks remain active."""

    top_k = 5

    def __init__(self, case):
        self.case = copy.deepcopy(case)
        self.calls = []

    def retrieve(self, scope, question, query_plan=None):
        assert scope == Scope.from_ids(self.case['kb_scope'], self.case['document_scope'])
        self.calls.append(question)
        items = []
        for rank, source in enumerate(self.case['sources'], 1):
            assert scope.contains(source['knowledge_base_id'], source['document_id'])
            assert sha(source['content']) == source['quote_sha256']
            chunk = ChunkRecord(source['chunk_id'], source['knowledge_base_id'],
                                source['document_id'], source['version_id'],
                                source['content'], copy.deepcopy(source['locator']))
            items.append(RetrievalItem(chunk, RankedHit(chunk_id=chunk.chunk_id, rank=rank)))
        return RetrievalResult(query_plan or normalize_query(question), items,
                               ('SIMULATED_FROZEN_POOL',))


class RecordingModel:
    provider_kind = 'local'
    provider_name = 'SIMULATED'
    chat_model = 'SIMULATED_NO_MODEL'

    def __init__(self):
        self.calls = []

    def answer_with_budget(self, prompt, timeout_seconds, max_tokens):
        self.calls.append((prompt, timeout_seconds, max_tokens))
        # Deliberately invalid citation exercises the unchanged final validator.
        return 'SIMULATED citation probe [E999].'


def run_arm(case, enabled=None):
    retriever = FrozenRetriever(case)
    model = RecordingModel()
    runs = RecordingRunStore()
    settings = QuickSettings() if enabled is None else QuickSettings(
        explicit_question_checklist_enabled=enabled)
    service = AnswerService(knowledge_gateway=KnowledgeGateway(retriever), runs=runs,
                            answer_gateway=model, quick_settings=settings)
    conversation = {'id': 'SIMULATED-conversation', 'knowledge_base_scope': case['kb_scope'],
                    'document_scope': case['document_scope']}
    outcome = service.answer(conversation, case['question'])
    return outcome, model, retriever, runs


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['case_id'])
def test_frozen_prompt_pair(case, record_property):
    default, off, on = [run_arm(case, flag) for flag in (None, False, True)]
    assert default[1].calls == off[1].calls
    assert default[0].error_code == off[0].error_code == on[0].error_code
    assert default[0].answer == off[0].answer == on[0].answer
    assert off[2].calls == on[2].calls
    assert off[3] is not on[3] and off[2] is not on[2] and off[1] is not on[1]
    assert len(off[1].calls) == len(on[1].calls) <= 1
    appendix = explicit_question_checklist(case['question'], len(detect_intents(case['question']).intents))
    assert bool(appendix) is case['expect_appendix']
    assert len(appendix) <= 512
    record_property('simulation', 'SIMULATED_NO_MODEL_NO_DB')
    record_property('generation_status', 'REACHED' if off[1].calls else 'GATE_BLOCKED')
    record_property('terminal_error', off[0].error_code or 'NONE')
    record_property('retrieval_calls_per_arm', len(off[2].calls))
    if not off[1].calls:
        assert off[0].trace['model_calls'] == on[0].trace['model_calls'] == 0
        assert off[3].evidence == on[3].evidence
        return
    for outcome, _, _, runs in (default, off, on):
        assert outcome.error_code == 'INVALID_CITATION'
        assert outcome.citations == ()
        assert outcome.answer == ''
        assert runs.completed == [('run-1', 'failed', 'INVALID_CITATION')]
        assert all(role != 'assistant' for _, role, _ in runs.messages)
        assert 'evidence.frozen' not in runs.event_names()
        assert runs.event_names()[-1] == 'run.failed'
        assert runs.evidence == []
    before, timeout, budget = off[1].calls[0]
    after, on_timeout, on_budget = on[1].calls[0]
    assert (timeout, budget) == (on_timeout, on_budget)
    assert after.replace(appendix, '', 1) == before if appendix else after == before
    assert '\nQuestion: ' + case['question'] in before
    context = before.split('\nEvidence:\n', 1)[1]
    assert context == after.split('\nEvidence:\n', 1)[1]
    assert sha(context) == case['context_sha256']
    assert off[3].hits == on[3].hits == [('run-1', [s['chunk_id'] for s in case['sources']])]
    assert off[3].evidence == on[3].evidence == []
    assert off[0].error_code == 'INVALID_CITATION'
    assert off[0].citations == on[0].citations == ()
    record_property('context_sha256', sha(context))
    record_property('off_prompt_sha256', sha(before))
    record_property('on_prompt_sha256', sha(after))
    record_property('appendix_chars', len(appendix))
    record_property('max_output_tokens', budget)


def test_fixture_identity_and_no_gold():
    assert DATA['simulation'] == 'SIMULATED'
    assert [c['case_id'] for c in CASES] == ['real-019', 'real-022', 'real-008', 'real-030',
                                          'derived-022-no-answer', 'derived-022-condition']
    forbidden = {'answer_points', 'expected_chunk_ids', 'expected_document_ids',
                 'reference_answer_points', 'gold', 'expected_answer'}
    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    check(DATA)
    for case in CASES:
        context = '\n\n'.join(f"[{source['label']}] {source['content']}" for source in case['sources'])
        assert sha(context) == case['context_sha256']
        assert [s['label'] for s in case['sources']] == [f'E{i}' for i in range(1, 6)]
        assert all(sha(s['content']) == s['quote_sha256'] for s in case['sources'])
    assert CASES[4]['question'] == '不要回答以下问题：' + CASES[1]['question']
    assert CASES[5]['question'] == '仅在原文明确确认原生 uvicorn 会自动加载 .env 时回答：' + CASES[1]['question']


def test_frozen_retriever_rejects_scope_mismatch():
    retriever = FrozenRetriever(CASES[0])
    with pytest.raises(AssertionError):
        retriever.retrieve(Scope.from_ids(['wrong-kb']), CASES[0]['question'])
    assert retriever.calls == []
