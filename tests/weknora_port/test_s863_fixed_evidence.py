"""Offline paired-wire identity; no model transport and no scoring-file reads."""
import json
from pathlib import Path
import shutil

import pytest

from backend.weknora_port.models import provider_config
from backend.weknora_port.models.provider import ProviderModel
from scripts.s8_6_3_fixed_evidence import load_inputs, render_pair, digest

PACKAGE = Path(__file__).resolve().parents[2] / 'docs/evaluation/evidence/s8_6_3/fixed_evidence'
CASES = json.loads((PACKAGE / 'manifest.json').read_text(encoding='utf8'))['cases']


@pytest.mark.parametrize('case', CASES, ids=lambda c:c['case_id'])
def test_exact_frozen_messages_and_equivalent_options_for_both_existing_adapters(case):
    models = {'cheap': ProviderModel(provider_config('siliconflow','Qwen/Qwen3.5-4B'),None),
              'expensive':ProviderModel(provider_config('deepseek','deepseek-flash'),None)}
    bodies = render_pair(PACKAGE, case['case_id'], models)
    assert digest(bodies['cheap']) == case['body_sha256']
    assert bodies['cheap']['messages'] == bodies['expensive']['messages']
    assert bodies['cheap']['max_tokens'] == bodies['expensive']['max_tokens'] == 2048
    assert bodies['cheap']['enable_thinking'] is False
    assert bodies['expensive']['thinking'] == {'type':'disabled'}


def test_gold_is_not_loaded_or_appended(tmp_path):
    target = tmp_path/'package'; shutil.copytree(PACKAGE,target)
    (target/'scoring_only.json').write_text('NOT EVEN VALID JSON; SECRET_GOLD_MARKER',encoding='utf8')
    for case in CASES:
        messages, _, _ = load_inputs(target,case['case_id'])
        assert 'SECRET_GOLD_MARKER' not in '\n'.join(m.content for m in messages)


def test_changed_body_is_rejected(tmp_path):
    target = tmp_path/'package'; shutil.copytree(PACKAGE,target)
    case=CASES[0];path=target/case['request_file']
    body=json.loads(path.read_text(encoding='utf8'));body['messages'][-1]['content'] += ' injected answer'
    path.write_text(json.dumps(body),encoding='utf8')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):load_inputs(target,case['case_id'])


def test_scoring_path_cannot_be_used_as_request(tmp_path):
    target = tmp_path/'package'; shutil.copytree(PACKAGE,target)
    path=target/'manifest.json';manifest=json.loads(path.read_text(encoding='utf8'))
    manifest['cases'][0]['request_file']='scoring_only.json'
    path.write_text(json.dumps(manifest),encoding='utf8')
    with pytest.raises(ValueError,match='PATH_INVALID'):load_inputs(target,CASES[0]['case_id'])
