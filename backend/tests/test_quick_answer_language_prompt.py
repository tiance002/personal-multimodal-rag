"""SIMULATED payload checks only; generated language adherence is NOT verified."""
from types import SimpleNamespace

import pytest

from backend.app.application.execution_routing import ExecutionRoute
from backend.app.application.knowledge_gateway import EvidenceService
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.tests.test_quick_chain_quality import RecordingAnswerModel


@pytest.mark.parametrize("question,context", [
    ("Which color is the indicator?", "[E7] 指示灯为蓝色。"),
    ("指示灯是什么颜色？", "[E7] The indicator is blue."),
    ("指示灯是什么颜色？请用英文回答。", "[E7] 指示灯为蓝色。"),
    ("Which color is the indicator? Please answer in Chinese.", "[E7] The indicator is blue."),
])
def test_answer_payload_contains_language_priority(question, context):
    model = RecordingAnswerModel("SIMULATED response; no language adherence claim")
    chain = LangChainQuickChain(None, answer_gateway=model)
    payload = dict(question=question, settings=QuickSettings(), run_id="simulated-language",
        plan=EvidenceService().plan(question), bundle=SimpleNamespace(context=context, labels=("E7",)),
        reservation=None, model_calls=0, evidence_only=False, answer_gateway=model,
        execution_route=ExecutionRoute("LOCAL", "LOCAL_DEFAULT"))

    chain._generate(payload)

    assert len(model.prompts) == 1
    instructions, supplied = model.prompts[0].split("\nQuestion:", 1)
    assert "Answer in the language of the user question by default" in instructions
    assert "follow any explicit user request for a different output language instead" in instructions
    assert "Do not switch answer language to match the evidence" in instructions
    assert "Keep original quotations, proper names, numeric units and citation labels unchanged where necessary" in instructions
    assert question in supplied
    assert supplied.endswith("\nEvidence:\n" + context)
    assert "answer supported sub-points with citations" in instructions
    assert "genuinely missing sub-points as unknown without a citation" in instructions
    assert "Allowed citation markers: [E7]." in instructions
    assert "[E1]" not in instructions and "[E#]" not in instructions
