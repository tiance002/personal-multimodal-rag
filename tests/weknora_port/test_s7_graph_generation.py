"""Fixed-context renderer/fence contracts; no model or infrastructure calls."""
from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from scripts import s7_graph_generation as generation
from backend.weknora_port.generation.context import DEFAULT_SYSTEM_PROMPT, Evidence


def case():
    content = 'Oak Observatory is located in Cedar Bay.'
    evidence = Evidence('chunk', content, knowledge_id='doc', knowledge_base_id='kb',
                        knowledge_filename='original.txt', source_content=content,
                        start_at=0, end_at=len(content), metadata={'source_version': 'v1'},
                        chunk_metadata={'source_version': 'v1'})
    arm = {'finalized_context': {'user_content': content + '\nWhere is Oak Observatory?',
                                'rendered_contexts': content, 'result_ids': ['chunk']},
           'evidence': [asdict(evidence)], 'rendered_at': '2026-10-19T12:00:00+08:00',
           'language': 'Chinese (Simplified)', 'system_prompt': DEFAULT_SYSTEM_PROMPT, 'history': []}
    return {'id': 'case-one', 'question': 'Where is Oak Observatory?', 'arms': {'off': arm, 'on': deepcopy(arm)},
            'scoring_only': {'answers': ['A secret Gold answer that is not a source']}}


def test_formal_generation_renderer_preserves_source_and_excludes_gold():
    request, rendering = generation.prepare_case(case(), 'off')
    assert request.evidence[0].source_content == 'Oak Observatory is located in Cedar Bay.'
    assert request.evidence[0].id == request.finalized.result_ids[0]
    assert request.options.thinking is False and request.options.max_completion_tokens == 1024
    assert request.options.temperature == .3
    messages = '\n'.join(message.content for message in rendering.messages)
    assert 'Oak Observatory' in messages and 'Cedar Bay' in messages
    assert 'A secret Gold answer' not in messages
    assert 'EviRoute RAG' in messages


def test_post_scoring_metadata_cannot_change_prepared_prompt():
    original = case()
    _, before = generation.prepare_case(original, 'off')
    original['scoring_only'] = {'answers': ['different'], 'qrels': {'gold-chunk': 1}}
    _, after = generation.prepare_case(original, 'off')
    assert [asdict(message) for message in before.messages] == [asdict(message) for message in after.messages]
    assert before.prompt_sha256 == after.prompt_sha256


def test_off_on_with_identical_sources_have_identical_wire_rendering():
    original = case()
    off, off_rendering = generation.prepare_case(original, 'off')
    on, on_rendering = generation.prepare_case(original, 'on')
    assert off.run_id != on.run_id
    assert off_rendering.prompt_sha256 == on_rendering.prompt_sha256


@pytest.mark.parametrize('changes', [
    {'thinking': True}, {'max_completion_tokens': 2048}, {'temperature': .7}, {'unknown': 'option'},
])
def test_frozen_model_options_do_not_silently_drift(changes):
    original = case()
    original['generation_parameters'] = changes
    with pytest.raises(ValueError):
        generation.prepare_case(original, 'off')


def test_final_context_id_mismatch_and_missing_source_body_are_rejected():
    original = case()
    original['arms']['off']['finalized_context']['result_ids'] = ['wrong-chunk']
    with pytest.raises(ValueError, match='FINALIZED_EVIDENCE_IDENTITY_MISMATCH'):
        generation.prepare_case(original, 'off')
    original = case()
    del original['arms']['off']['evidence']
    with pytest.raises(ValueError, match='S7_RENDERED_SOURCE_RESULTS_REQUIRED'):
        generation.prepare_case(original, 'off')


def test_frozen_render_time_is_required_instead_of_current_time_replacement():
    original = case()
    del original['arms']['off']['rendered_at']
    with pytest.raises(ValueError, match='S7_FROZEN_RENDER_TIME_REQUIRED'):
        generation.prepare_case(original, 'off')


def test_selection_is_bounded_to_existing_off_on_arms_and_unique_case_calls():
    pack = {'cases': [case()]}
    assert len(generation.selected_calls(pack, ['case-one'], ['off', 'on'])) == 2
    with pytest.raises(ValueError, match='S7_UNKNOWN_ARM'):
        generation.selected_calls(pack, ['case-one'], ['gold'])
    with pytest.raises(ValueError, match='S7_FINITE_GENERATION_PLAN_REQUIRED'):
        generation.selected_calls(pack, ['case-one'], ['off', 'off'])
    with pytest.raises(ValueError, match='S7_CASE_IDENTITY_MISMATCH'):
        generation.selected_calls(pack, ['absent'], ['off'])
    del pack['cases'][0]['arms']['on']
    with pytest.raises(ValueError, match='S7_REQUESTED_ARM_MISSING'):
        generation.selected_calls(pack, ['case-one'], ['off', 'on'])


def test_phase_validation_only_consumes_current_root_registered_ledger(tmp_path):
    ledger_path, auth_path = tmp_path / 'ledger.json', tmp_path / 'authorization.json'
    ledger = {'scope': 'S7_20261019', 'status': 'OPEN', 'tenant_id': 332, 'subject_id': 'verified-subject',
              'allowed_kbs': ['kb'], 'capture_evidence': True, 'entries': [], 'max_calls': 2,
              'allowed_models': [{'provider': 'siliconflow', 'model': generation.QWEN,
                                  'model_id': generation.QWEN_ID, 'capability': 'chat'}]}
    parent = {'status': 'OPEN', 'owner_approval': {'answer': '批准上述 S7 有限验证'}, 'ledgers': [str(ledger_path)]}
    ledger_path.write_text(json.dumps(ledger))
    auth_path.write_text(json.dumps(parent))
    stage = SimpleNamespace(AUTH=auth_path, summary=lambda: {'unknown': 0, 'active': [str(ledger_path)]})
    principal = SimpleNamespace(tenant_id=332, subject_id='verified-subject')
    generation.validate_phase(stage, ledger_path, principal, 'kb', 2)
    assert json.loads(auth_path.read_text()) == parent and json.loads(ledger_path.read_text()) == ledger
    with pytest.raises(ValueError, match='S7_EXECUTION_SCOPE_MISMATCH'):
        generation.validate_phase(stage, ledger_path, principal, 'another-kb', 2)
    stage.summary = lambda: {'unknown': 1, 'active': [str(ledger_path)]}
    with pytest.raises(ValueError, match='S7_ROOT_OPEN_PHASE_REQUIRED'):
        generation.validate_phase(stage, ledger_path, principal, 'kb', 1)
