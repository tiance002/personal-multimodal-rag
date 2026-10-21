"""History supports reference resolution; only current sources supply evidence."""
from backend.weknora_port.generation.context import Evidence, History, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from backend.weknora_port.retrieval.context import FinalizedContext


def test_old_answer_is_dialogue_and_does_not_register_an_evidence_source():
    evidence = Evidence('current', 'current original fact', 'doc', 'kb')
    request = GenerationRequest('run', 'session', 'answer', 'Which document supports it?',
        FinalizedContext('Which document supports it?', '', ('current',)),
        evidence=(evidence,), history=(History('previous question', 'OLD_GENERATED_CLAIM'),))
    prepared = prepare_generation(request)
    system = prepared.messages[0].content
    assert 'Prior assistant answers are dialogue context only' in system
    assert 'current retrieved original sources' in system
    assert 'OLD_GENERATED_CLAIM' not in system
    assert any(m.role == 'assistant' and m.content == 'OLD_GENERATED_CLAIM'
               for m in prepared.messages)
    assert 'OLD_GENERATED_CLAIM' not in prepared.messages[-1].content
