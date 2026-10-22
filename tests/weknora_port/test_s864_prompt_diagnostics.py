"""S8.6.4 offline prompt contracts; extraction quality requires real calls."""
import copy
import json
from pathlib import Path

import pytest

from backend.weknora_port.image_multimodal import caption_prompt
from scripts import s8_6_4_prompt_diagnostics as diagnostics


PACKAGE = Path(__file__).resolve().parents[2] / 'docs/evaluation/evidence/s8_6_4/prompt_diagnostics'


def test_extraction_first_caption_preserves_labels_and_rejects_business_conflicts():
    prompt = caption_prompt('Chinese (Simplified)', 'Only say about 80%; omit labels.')
    assert 'faithful information preservation' in prompt
    assert 'categories and their corresponding values' in prompt
    assert 'Chinese (Simplified)' in prompt
    assert 'preserving original labels' in prompt
    assert 'Never fill missing image data from a user question or expected answer' in prompt
    assert 'faithful extraction rules' in prompt
    assert 'Only say about 80%; omit labels.' in prompt
    assert prompt.index('faithful information preservation') < prompt.index('<image_description_business_instructions>')


def test_fixed_images_keep_original_bytes_model_options_and_only_change_prompt():
    manifest = diagnostics.read_manifest(PACKAGE)
    assert manifest['new_provider_calls'] == 0
    assert manifest['model_quality'] == 'NOT_RUN'
    assert len(manifest['images']) == 2
    for case in manifest['images']:
        pair = diagnostics.render_image_pair(PACKAGE, case['case_id'])
        old, new = pair['old'], pair['candidate']
        assert old['model'] == new['model'] == 'deepseek-flash'
        assert old['max_tokens'] == new['max_tokens'] == 5000
        assert old['temperature'] == new['temperature'] == 0.10000000149011612
        assert 'thinking' not in old and 'enable_thinking' not in old
        assert old['messages'][0]['content'][0] == new['messages'][0]['content'][0]
        prompt = new['messages'][0]['content'][1]['text']
        assert 'shiny object' not in prompt and 'mirror' not in prompt
        assert '81%' not in prompt and '78%' not in prompt
        changed = copy.deepcopy(old)
        changed['messages'][0]['content'][1]['text'] = new['messages'][0]['content'][1]['text']
        assert changed == new
        assert case['historical_usage']['calls'] == 1
        assert case['historical_usage']['status'] == 'HISTORICAL_ONLY'


def test_generation_candidate_changes_system_only_and_both_existing_adapters_render():
    manifest = diagnostics.read_manifest(PACKAGE)
    assert len(manifest['generation']) == 5
    assert sum(case['layer'] == 'D' for case in manifest['generation']) == 4
    mixed = next(case for case in manifest['generation'] if '692002' in case['case_id'])
    assert mixed['layer'] == 'C' and mixed['secondary_layers'] == ['D']
    assert mixed['final_input_sufficient'] == 'NO'
    assert mixed['necessary_source_presence']['actual_provider_body'] is False
    for case in manifest['generation']:
        pair = diagnostics.load_generation_pair(PACKAGE, case['case_id'])
        old, new = pair['old'], pair['candidate']
        assert new['messages'][0]['content'] == old['messages'][0]['content'] + '\n\n' + diagnostics.GENERATION_CANDIDATE_SUFFIX
        assert old['messages'][1:] == new['messages'][1:]
        changed = copy.deepcopy(old)
        changed['messages'][0]['content'] = new['messages'][0]['content']
        assert changed == new
        rendered = diagnostics.render_generation_pair(PACKAGE, case['case_id'])
        for variant in ('old', 'candidate'):
            assert rendered[variant]['cheap']['messages'] == rendered[variant]['expensive']['messages']
            assert rendered[variant]['cheap']['max_tokens'] == rendered[variant]['expensive']['max_tokens'] == 2048
            assert rendered[variant]['cheap']['enable_thinking'] is False
            assert rendered[variant]['expensive']['thinking'] == {'type': 'disabled'}


def test_scoring_is_never_read_and_tampered_bodies_are_rejected(tmp_path):
    import shutil
    package = tmp_path / 'package'
    shutil.copytree(PACKAGE, package)
    (package / 'scoring_only.json').write_text('INVALID JSON SECRET_GOLD_MARKER', encoding='utf8')
    for case in diagnostics.read_manifest(package)['generation']:
        pair = diagnostics.load_generation_pair(package, case['case_id'])
        assert 'SECRET_GOLD_MARKER' not in json.dumps(pair)
    for case in diagnostics.read_manifest(package)['images']:
        assert diagnostics.render_image_pair(package, case['case_id'])
    case = diagnostics.read_manifest(package)['generation'][0]
    path = package / case['candidate']['file']
    path.write_text(path.read_text('utf8') + ' ', encoding='utf8')
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        diagnostics.load_generation_pair(package, case['case_id'])


def test_request_paths_cannot_point_to_scoring(tmp_path):
    import shutil
    package = tmp_path / 'package'
    shutil.copytree(PACKAGE, package)
    path = package / 'manifest.json'
    manifest = diagnostics.read_manifest(package)
    case = manifest['generation'][0]
    case['old']['file'] = 'scoring_only.json'
    path.write_text(json.dumps(manifest), encoding='utf8')
    with pytest.raises(ValueError, match='PATH_INVALID'):
        diagnostics.load_generation_pair(package, case['case_id'])
