# Historical v1 synthetic control, NOT formal Q04 v2 gold.
# Original evidence SHA256: 8a4d9468a44076dbd627649cc817550a6325e8add1bd3bb7478f08a8d9640450
QUESTION = '在本题限定的青禾运维KB当前有效的入库异常流程下，未扫描到货物时应标记什么状态、由哪个角色登记？应在多久内通知什么角色？'
CONTEXT = '[E1] 入库异常处理流程（版本v1，2026-08-01生效）：未扫描到货物时，立即暂停入库，并电话联系值班主管。'
RAW_ANSWER = '- **状态与登记角色**：证据中未说明应标记的具体状态及由哪个角色登记 [E#] 缺失。  \n- **通知时限与对象**：证据中未说明应在多久内通知以及通知哪个角色 [E#] 缺失。  \n\n现有证据仅表明：未扫描到货物时应立即暂停入库，并电话联系值班主管 [E1]。'

import re
from types import SimpleNamespace
import pytest
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.execution_routing import ExecutionRoute
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.citations import CitationChunk, CitationService, InMemoryCitationStore
from backend.app.application.run_metrics import collect_metrics
from backend.app.application.answer_hardening import detect_intents
from backend.tests.test_quick_chain_quality import RecordingAnswerModel, _chain
from backend.app.domain.scope import Scope


@pytest.mark.parametrize('labels', [('E1',), ('E3', 'E7', 'E12')])
def test_prompt_advertises_only_bundle_labels(labels):
    model = RecordingAnswerModel('SIMULATED answer')
    context = '\n'.join(f'[{label}] synthetic evidence' for label in labels)
    chain = LangChainQuickChain(None, answer_gateway=model)
    payload = dict(question=QUESTION, settings=QuickSettings(), run_id='simulated',
        plan=EvidenceService().plan(QUESTION), bundle=SimpleNamespace(context=context, labels=labels),
        reservation=None, model_calls=0, evidence_only=False, answer_gateway=model,
        execution_route=ExecutionRoute('LOCAL', 'LOCAL_DEFAULT'))
    with collect_metrics('simulated') as metrics:
        generated = chain._generate(payload)
        prompt = model.prompts[0]
        instructions = prompt.split('\nQuestion:', 1)[0]
        assert set(re.findall(r'\[([^\]]+)\]', instructions)) == set(labels)
        assert '[E#]' not in prompt
        assert 'answer supported sub-points' in instructions.lower()
        assert 'without a citation' in instructions.lower()
        assert detect_intents(QUESTION).checklist() in prompt
        assert generated['model_calls'] == 1
        assert 0 < metrics.hardening['max_output_tokens'] <= 1536


def control():
    service = EvidenceService()
    citations = CitationService(InMemoryCitationStore())
    chunk = CitationChunk('v1-control', 'synthetic-doc', 'synthetic-kb', 'v1', CONTEXT.split('] ',1)[1], {})
    citations.freeze('simulated', chunk)
    return service, chunk, tuple(citations.snapshots.values())


def test_frozen_v1_raw_answer_remains_invalid():
    service, chunk, snapshots = control()
    with collect_metrics('simulated') as metrics:
        result = service.finalize_answer('simulated', RAW_ANSWER, snapshots, [chunk], service.plan(QUESTION))
        assert result.error == result.rejection == 'INVALID_CITATION'
        assert result.answer == ''
        assert metrics.hardening['validator_rejection'] == 'INVALID_CITATION'


@pytest.mark.parametrize('marker', ['[E9]', '[E1-E2]', '[E2]', '[E#]'])
def test_v1_control_invalid_labels_are_rejected(marker):
    service, chunk, snapshots = control()
    candidate = '未扫描到货物时，立即暂停入库，并电话联系值班主管。' + marker
    result = service.finalize_answer('simulated', candidate, snapshots, [chunk], service.plan(QUESTION))
    assert result.error == result.rejection == 'INVALID_CITATION'
    assert result.answer == ''


def test_simulated_v1_supported_partial_answer_is_accepted_unchanged():
    candidate = '未扫描到货物时，立即暂停入库，并电话联系值班主管 [E1]。具体状态、登记角色和通知时限：资料未说明，未知。'
    model = RecordingAnswerModel(candidate)
    chain = _chain([CONTEXT.split('] ',1)[1]], model)
    with collect_metrics('simulated') as metrics:
        result = chain.invoke(QUESTION, Scope.from_ids(['kb']), run_id='simulated')
        assert result.error_code is None
        assert result.answer == candidate
        assert result.citations == ('E1',)
        assert result.trace.model_calls == 1
        assert metrics.hardening['validator_rejection'] is None
        assert metrics.hardening['fallback'] is None
