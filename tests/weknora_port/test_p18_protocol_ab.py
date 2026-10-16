import copy
import json
from pathlib import Path
import pytest
from scripts.weknora_port_p18_protocol_ab import validate, enhanced, registry_from
from scripts.weknora_port_p13_rerank import claim


def plan():
    return json.loads((Path(__file__).parent/'fixtures/p18_protocol_ab.json').read_text(encoding='utf-8'))


def test_frozen_pair_changes_only_protocol_and_preserves_registry():
    data=plan(); validate(data)
    for q in data['questions']:
        assert registry_from(q['registry']).snapshot().chunks[0][0]=='c1'
        assert q['original_body']['temperature']==.3
        assert q['original_body']['max_tokens']==1024


@pytest.mark.parametrize('field', ['temperature','model','max_tokens','context','question','arm','wire'])
def test_control_variable_mutations_are_denied(field):
    data=plan();c=data['cases'][1]
    if field in ['temperature','model','max_tokens']:c['body'][field]='changed'
    elif field in ['context','question']:c['body']['messages'][1]['content']+='changed'
    elif field=='arm':c['arm']='A'
    else:c['wire_sha256']='0'*64
    with pytest.raises(ValueError): validate(data)


def test_unknown_registry_handle_or_protocol_boundary_denied():
    q=plan()['questions'][0];row=copy.deepcopy(q['registry']);row['chunks'][0][0]='c9'
    with pytest.raises(ValueError):registry_from(row)
    with pytest.raises(ValueError):enhanced('missing citation protocol')


def test_uncertain_call_intent_cannot_be_replayed(tmp_path):
    case=plan()['cases'][0];claim(tmp_path,case)
    with pytest.raises(FileExistsError):claim(tmp_path,case)
