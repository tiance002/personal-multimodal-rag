"""SIMULATED generation with normal Quick budget/envelope/wire propagation."""
from dataclasses import replace
import json
import pytest
from backend.tests.test_p5_router_generation import make_chain,GOOD
from backend.tests.test_v6_generation_binding import setup_binding as deepseek_setup,no_live_transport
from backend.app.application.quick_chain import QuickSettings
from backend.app.application.rule_router_v1 import RouterPolicy
from backend.app.application.run_metrics import collect_metrics
from backend.app.domain.scope import Scope
from backend.app.application.answer_hardening import generation_budget,detect_intents


QUESTION='What is A cost? What evidence supports A cost?'

@pytest.mark.parametrize('mode',['CHEAP_ONLY','DYNAMIC','EXPENSIVE_ONLY'])
@pytest.mark.parametrize('cap',[None,512])
def test_uniform_cap_896_plan_reaches_normal_generation_and_same_envelope(monkeypatch,mode,cap):
    assert generation_budget(detect_intents(QUESTION),32).max_tokens==896
    chain,gate,wires,expensive=make_chain(monkeypatch,policy=RouterPolicy(mode=mode))
    planned=896 if cap is None else 512
    with collect_metrics('SIMULATED-output-cap') as metrics:
        result=chain.invoke(QUESTION,Scope.from_ids(['kb']),run_id='SIMULATED-output-cap',
            settings=QuickSettings(cloud_enabled=True,answer_output_token_cap=cap),cloud_allowed_by_kb={'kb':True})
    if wires:assert all(w['max_tokens']==planned and w['model']=='XingChenAGI/Xing4.0-29B' for w in wires)
    if expensive.calls:assert all(c[2]==planned for c in expensive.calls)
    assert wires or expensive.calls
    assert metrics.hardening['generation_complexity']=='NORMAL' and metrics.hardening['max_output_tokens']==planned
    assert all(r['state']=='unknown' for r in gate.reservations.values())


def test_cap_same_512_binds_v6_counter_monthly_activity_and_final_wire(monkeypatch,tmp_path):
    role,activity,path,counter,guard,wires=deepseek_setup(tmp_path,result={
        'model':'deepseek-flash','choices':[{'message':{'content':GOOD},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
    chain,_,_,_=make_chain(monkeypatch,policy=RouterPolicy(mode='EXPENSIVE_ONLY'))
    scope=Scope.from_ids(['kb'])
    role=replace(role,v6_binding=replace(role.v6_binding,server_scope=scope))
    chain.generation_roles['chat_expensive']=role
    with collect_metrics('SIMULATED-cap-v6'):
        chain.invoke(QUESTION,scope,run_id='SIMULATED-cap-v6',
            settings=QuickSettings(cloud_enabled=True,answer_output_token_cap=512),cloud_allowed_by_kb={'kb':True})
    assert len(wires)==1 and json.loads(wires[0])['max_tokens']==512
    assert counter.wires[-1]==wires[0]
    data=json.loads(path.read_text());row=data['v6_owner_activity']['attempts'][0]
    assert row['request_budget']['output_token_bound']==row['validation_tokens']['reserved_output']==512
    assert guard.reservations[0]['planned_tokens']==sum(row['validation_tokens'][x] for x in ['reserved_input','reserved_output'])


@pytest.mark.parametrize('cap',[True,False,0,-1,511,513,896,512.0,'512'])
def test_invalid_cap_rejected_at_settings_before_provider_budget(cap):
    with pytest.raises(ValueError,match='OUTPUT_CAP_INVALID'):QuickSettings(answer_output_token_cap=cap)
