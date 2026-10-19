"""Prompt/source presentation tests; all fixtures are local and synthetic."""
from dataclasses import replace
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.weknora_port.generation import Evidence, GenerationRequest, prepare_generation
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT, SOURCED_ANSWER_OUTPUT
from backend.weknora_port.references.registry import SourceRegistry
from backend.weknora_port.retrieval.context import FinalizedContext


def prepared(evidence):
    return prepare_generation(GenerationRequest(
        run_id='synthetic-run', session_id='synthetic-session', message_id='synthetic-message',
        q0='What does this card show?', language='Chinese (Simplified)', citations_enabled=False,
        finalized=FinalizedContext('Evidence\n' + evidence.content + '\nQuestion', evidence.content, (evidence.id,)),
        evidence=(evidence,)))


def image_evidence(**changes):
    row = Evidence('chunk', '![card](images/card.png)\nSITE: North Pier; READING: 37',
        knowledge_id='document', knowledge_base_id='knowledge-base',
        knowledge_title='Service brief', knowledge_filename='service_brief.pptx',
        metadata={'source_version': 'version-1'}, chunk_metadata={'source_version': 'version-1'},
        image_info=json.dumps([{'url': 'images/card.png', 'ocr_text': 'SITE: North Pier; READING: 37'}]))
    return replace(row, **changes)


def test_language_has_single_explicit_priority_and_no_self_check_loop_instruction():
    system = prepared(image_evidence()).messages[0].content
    assert system.startswith('你是 EviRoute RAG，面向个人用户的知识库问答助手。')
    assert 'Answer language: Chinese (Simplified)' in system
    assert 'regardless of the language of the question or reference materials' in system
    assert 'Use the language of the current user question' not in system
    assert 'Do not embed retrieved images' in system
    assert 'Copy a supplied handle' not in system
    assert 'Source citations are disabled' not in system
    assert '<ref' not in system
    assert 'Default response language:' not in DEFAULT_SYSTEM_PROMPT
    assert 'requested language' not in SOURCED_ANSWER_OUTPUT
    assert 'Before finishing' not in system and 'silently verify' not in system
    assert 'Return the final answer directly' in system


def test_selected_answer_language_is_rendered_without_a_question_language_rule():
    source=image_evidence()
    request=GenerationRequest(run_id='run',session_id='session',message_id='message',
        q0='请用资料回答',language='English',citations_enabled=False,
        finalized=FinalizedContext('Evidence\n'+source.content,source.content,(source.id,)),evidence=(source,))
    system=prepare_generation(request).messages[0].content
    assert 'Answer language: English' in system
    assert 'Chinese' not in system and 'explicit language request' not in system
    assert prepare_generation(request).messages[-1].content.endswith('Answer in English.')


def test_english_question_preserves_q0_but_receives_configured_chinese_instruction():
    result=prepared(image_evidence())
    assert result.messages[-1].content.endswith('请用简体中文回答。')
    assert 'SITE: North Pier; READING: 37' in result.messages[-1].content


def test_filename_is_supplied_separately_without_inventing_extension():
    wire = prepared(image_evidence()).messages[-1].content
    assert 'title="Service brief" filename="service_brief.pptx"' in wire
    assert ' id="c' not in wire and ' id="d' not in wire and ' kb="b' not in wire
    no_filename = prepared(image_evidence(knowledge_filename='')).messages[-1].content
    assert 'title="Service brief"' in no_filename and 'filename=' not in no_filename


def test_image_is_version_bound_and_resource_decoded_without_xml_query_damage():
    source = image_evidence()
    p = prepared(source)
    assert '![card](res://0001)' in p.messages[-1].content
    assert 'SITE: North Pier; READING: 37' in p.messages[-1].content
    answer = p.registry.decode_output_text('North Pier, 37.\n![card](res://0001)\nSources: service_brief.pptx')
    assert 'Sources: service_brief.pptx' in answer
    target = answer.split('![card](', 1)[1].split(')', 1)[0]
    parsed = urlsplit(target)
    assert parsed.path == '/api/v1/knowledge-bases/knowledge-base/documents/document/source'
    assert parse_qs(parsed.query) == {'source_version': ['version-1'], 'asset_path': ['images/card.png']}
    assert '&amp;' not in target
    assert source.content.startswith('![card](images/card.png)')


@pytest.mark.parametrize('changes', [
    {'metadata': {}}, {'metadata': {'source_version': 'version-2'}},
    {'knowledge_base_id': ''}, {'knowledge_id': ''}, {'image_info': '[]'},
    {'content': '![unknown](images/other.png)'}, {'content': '![bad](images/../secret.png)'},
])
def test_missing_or_mismatched_identity_does_not_advertise_a_source_asset(changes):
    p = prepared(image_evidence(**changes))
    assert not p.registry.resources._by_handle
    assert '/api/v1/knowledge-bases/' not in p.messages[-1].content


def test_existing_web_image_and_normal_filename_or_code_text_are_preserved():
    p = prepared(image_evidence(content='![chart](https://example.test/chart.png)', image_info='[]'))
    assert 'https://example.test/chart.png' in p.messages[-1].content
    answer = 'Sources: service_brief.pptx\nCode: `d1/c2`\n![chart](https://example.test/chart.png)'
    assert SourceRegistry(False).decode_output_text(answer) == answer
