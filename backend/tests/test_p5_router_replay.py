import copy
from decimal import Decimal
import json
from pathlib import Path
import pytest
from scripts.replay_p5_router import ROOT, replay, replay_case, net_saving, envelope_for, cache_identity


def fixture():
    return json.loads((ROOT/'evaluations/router_dev/cases-v1.json').read_text(encoding='utf8'))['cases']


def test_all_arms_share_envelope_context_model_and_no_fake_savings():
    result = replay(fixture())
    assert result['real_calls'] == 0
    assert len(result['arms']) == 4
    rows = [v['rows'] for v in result['arms'].values()]
    assert all(len(r) == 36 for r in rows)
    for index in range(36):
        assert len({r[index]['envelope_identity'] for r in rows}) == 1
        assert len({r[index]['context_hash'] for r in rows}) == 1
    for arm in result['arms'].values():
        assert arm['summary']['net_saving'] == 'NOT_AVAILABLE'
        assert arm['summary']['semantic_correctness'] == 'NOT_REVIEWED'


@pytest.mark.parametrize('changed', ['prompt','context','model','parameters','postprocess'])
def test_changed_inputs_invalidate_cache(changed,monkeypatch):
    case = copy.deepcopy(fixture()[0])
    if changed in ('prompt','context'):
        case[changed] += ' changed'
    elif changed == 'model':
        monkeypatch.setitem(__import__('scripts.replay_p5_router',fromlist=['MODELS']).MODELS,'chat_cheap',('siliconflow','different'))
    elif changed == 'parameters':
        case['max_tokens'] += 1
    else:
        monkeypatch.setattr('scripts.replay_p5_router.postprocess_identity',lambda:'changed')
    with pytest.raises(ValueError,match='REPLAY_CACHE_IDENTITY_MISMATCH'):
        replay_case(case,'CHEAP_ONLY')


@pytest.mark.parametrize('dynamic,expensive,extra,expected', [(None,Decimal(10),Decimal(0),'NOT_AVAILABLE'),
    (Decimal(3),Decimal(10),None,'NOT_AVAILABLE'),(Decimal(3),Decimal(0),Decimal(0),'NOT_AVAILABLE'),
    (Decimal(3),Decimal(10),Decimal(1),'0.6')])
def test_saving_formula_with_unknown_never_zero(dynamic,expensive,extra,expected):
    # Pure arithmetic scenario, not evidence of actual bill savings.
    assert net_saving(dynamic,expensive,extra) == expected


def test_cli_exclusive_output_and_simulated_only(tmp_path):
    from scripts.replay_p5_router import main
    out = tmp_path/'replay.json'
    assert main(['--output',str(out)]) == 0
    with pytest.raises(FileExistsError):
        main(['--output',str(out)])
    assert json.loads(out.read_text(encoding='utf8'))['real_calls'] == 0
