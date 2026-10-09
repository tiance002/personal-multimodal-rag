"""P5 integration boundaries; all provider and billing observations SIMULATED."""
from dataclasses import replace
from decimal import Decimal
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.tests.test_p5_router_generation import make_chain, invoke, GOOD
from backend.app.application.rule_router_v1 import RouterPolicy, GenerationRole


def test_full_prompt_estimator_not_context_only_and_failure_unknown(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    prompts = []
    def estimate(prompt):
        prompts.append(prompt)
        return 6001, 'SIMULATED_ESTIMATOR_NOT_PROVIDER_ACTUAL'
    chain.input_token_estimator = estimate
    result, metrics = invoke(chain)
    assert result.error_code is None
    assert prompts == [cheap[0]['messages'][0]['content']]
    assert 'Question:' in prompts[0] and 'Allowed citation markers:' in prompts[0]
    assert metrics['rule_router']['L']['value'] == .05
    chain.input_token_estimator = lambda _: (_ for _ in ()).throw(ValueError('secret'))
    result, metrics = invoke(chain,run_id='another')
    assert metrics['rule_router']['estimated_input_tokens'] is None
    assert metrics['rule_router']['L']['status'] == 'UNKNOWN'
    assert 'secret' not in json.dumps(metrics)


def test_dual_attempt_usage_totals_and_no_legacy_local_cost_metadata(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch,cheap='bad [E99]')
    result,row=invoke(chain)
    assert result.error_code is None
    assert row['input_tokens']==23 and row['output_tokens']==15
    assert 'generation_routes' not in row['answer_hardening']
    assert all(a['actual_cost']=='UNKNOWN' for a in row['rule_router']['attempts'])


@pytest.mark.parametrize('estimate,expected_send',[(100,1),(101,0),(None,0)])
def test_simulated_capacity_normal_equal_over_limit_and_unknown(monkeypatch,estimate,expected_send):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    chain.generation_roles['chat_cheap'] = replace(chain.generation_roles['chat_cheap'],simulated_input_limit=100)
    if estimate is not None:
        chain.input_token_estimator=lambda prompt:(estimate,'SIMULATED_CAPACITY')
    result, row = invoke(chain)
    assert len(cheap) == expected_send and len(gate.reservations) == expected_send
    if expected_send:
        assert result.error_code is None
    else:
        assert result.error_code
    assert exp.calls == []  # capacity rejection does not silently change role


def test_same_run_changed_route_cannot_send_again(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    invoke(chain)
    chain.router_policy = RouterPolicy(mode='DYNAMIC',threshold=Decimal('0'))
    result, _ = invoke(chain)
    assert result.error_code == 'DUPLICATE_REQUEST' and exp.calls == [] and len(cheap) == 1


def test_mismatched_role_binding_cannot_change_provider(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    chain.generation_roles['chat_cheap']=chain.generation_roles['chat_expensive']
    result, _ = invoke(chain)
    assert result.error_code=='ROUTER_ROLE_BINDING_MISMATCH'
    assert cheap==exp.calls==[] and gate.reservations=={}


def test_global_egress_disabled_and_all_kb_scope_required(monkeypatch):
    from backend.app.application.quick_chain import QuickSettings
    from backend.app.domain.scope import Scope
    chain, gate, cheap, exp = make_chain(monkeypatch)
    result=chain.invoke('A 的成本是多少？',Scope.from_ids(['kb']),run_id='egress-off',settings=QuickSettings())
    assert result.error_code=='CLOUD_EGRESS_DISABLED'
    result=chain.invoke('A 的成本是多少？',Scope.from_ids(['kb','other']),run_id='kb-denied',
        settings=QuickSettings(cloud_enabled=True),cloud_allowed_by_kb={'kb':True,'other':False})
    assert result.error_code=='CLOUD_EGRESS_DISABLED' and cheap==exp.calls==[] and gate.reservations=={}


def test_concurrent_duplicate_one_generation(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:invoke(chain)[0],range(4)))
    assert sum(r.error_code is None for r in results) == 1
    assert len(cheap) == 1 and len(gate.reservations) == 1


def test_raw_candidate_check_has_no_audit_side_effect_and_marker_compat(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch,cheap='A方案成本1000元 [ E1 ]')
    class Audit:
        def save(self,row):
            raise AssertionError('valid candidate must not audit rejected output')
    chain.knowledge_gateway.evidence.hardening.audit = Audit()
    result, row = invoke(chain)
    assert result.error_code is None and result.citations == ('E1',) and exp.calls == []


def test_history_unknown_reduces_remaining_budget_and_upgrade_denied(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch,cheap='bad [E99]',budget=14)
    previous=gate.reserve(run_id='history',provider='deepseek',model_name='deepseek-flash',capability='chat',estimate_microunits=9)
    gate.mark_unknown(previous.reservation_id)
    result, row = invoke(chain)
    assert result.error_code == 'MODEL_BUDGET_DENIED' and len(cheap) == 1 and exp.calls == []
    assert len(gate.reservations) == 2
    assert gate._used(previous.month) == 14
    assert gate.reservations[previous.reservation_id]['state'] == 'unknown'


@pytest.mark.parametrize('stage',['cheap_settlement','expensive_settlement','expensive_receipt'])
def test_persistence_or_settlement_failure_never_retry_or_refund(monkeypatch,stage):
    chain, gate, cheap, exp = make_chain(monkeypatch,cheap='bad [E99]' if stage!='cheap_settlement' else GOOD)
    if stage.endswith('settlement'):
        guard = chain.generation_roles['chat_cheap'].gateway.usage_guard if stage=='cheap_settlement' else chain.generation_roles['chat_expensive'].usage_guard
        def fail(*a,**k):
            raise OSError('secret filesystem path')
        monkeypatch.setattr(guard,'settle',fail)
    else:
        original=exp.answer_with_product_scope
        def receipt_failure(*a,**k):
            original(*a,**k)
            raise OSError('secret receipt path')
        monkeypatch.setattr(exp,'answer_with_product_scope',receipt_failure)
    result, row = invoke(chain)
    assert result.error_code and len(cheap) == 1
    assert len(exp.calls) == (0 if stage=='cheap_settlement' else 1)
    assert all(r['state'] in {'reserved','unknown'} for r in gate.reservations.values())
    assert 'secret' not in json.dumps(row)


def test_final_business_commit_once_and_durable_duplicate_across_chains(monkeypatch):
    from backend.app.application.answer_service import AnswerService
    from backend.app.application.quick_chain import QuickSettings
    from backend.tests.test_answer_service import RecordingRunStore, _conversation
    class Runs(RecordingRunStore):
        def __init__(self):
            super().__init__();self.claims=set();self.commits=[]
        def get_knowledge_base(self,kb):return {'id':kb,'cloud_allowed':True}
        def create_run_once(self,conversation,kbs,docs,q0,request_id):
            if request_id in self.claims:return 'durable-run',False
            self.claims.add(request_id);return 'durable-run',True
        def finalize_answer(self,**kwargs):
            self.commits.append(kwargs);return True
    runs=Runs()
    chain, gate, cheap, exp = make_chain(monkeypatch,cheap='bad [E99]')
    service=AnswerService(knowledge_gateway=chain.knowledge_gateway,quick_chain=chain,runs=runs,quick_settings=QuickSettings(cloud_enabled=True))
    outcome=service.answer(_conversation(),'A 的成本是多少？',request_id='stable-request')
    assert outcome.error_code is None and len(runs.commits)==1
    assert runs.commits[0]['answer']==GOOD and len(cheap)==len(exp.calls)==1
    assert all('bad' not in str(msg) for msg in runs.messages)
    new_chain, _, new_cheap, new_exp = make_chain(monkeypatch)
    new_service=AnswerService(knowledge_gateway=new_chain.knowledge_gateway,quick_chain=new_chain,runs=runs,quick_settings=QuickSettings(cloud_enabled=True))
    duplicate=new_service.answer(_conversation(),'A 的成本是多少？',request_id='stable-request')
    assert duplicate.error_code=='DUPLICATE_REQUEST'
    assert new_cheap==new_exp.calls==[] and len(runs.commits)==1


def test_bootstrap_default_off_and_expensive_never_generic_cloud(tmp_path,monkeypatch):
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    from backend.app.adapters.models.deepseek import DeepSeekGateway
    off=build_container(Settings(database_url='sqlite+pysqlite:///:memory:',storage_root=tmp_path),model=None)
    assert off.quick_chain.router_policy.mode=='OFF' and off.quick_chain.generation_roles=={}
    on=build_container(Settings(database_url='sqlite+pysqlite:///:memory:',storage_root=tmp_path,
        cloud_enabled=True,chat_egress_enabled=True,rule_router_enabled=True,cloud_cost_estimate_microunits=1),model=None)
    binding=on.quick_chain.generation_roles['chat_expensive']
    assert type(binding.gateway) is DeepSeekGateway
    with pytest.raises(Exception,match='P5_BLOCKED_REAL'):
        binding.check()
    assert not binding.simulated


def test_invalid_policy_config_and_explicit_off_are_effective(monkeypatch):
    from backend.app.config import Settings
    from backend.app.application.quick_chain import QuickSettings
    from backend.app.domain.scope import Scope
    monkeypatch.setenv('RAG_RULE_ROUTER_ENABLED','garbage')
    with pytest.raises(ValueError):Settings.from_env()
    with pytest.raises(ValueError):RouterPolicy(threshold=Decimal('NaN'))
    chain, gate, cheap, exp = make_chain(monkeypatch)
    result=chain.invoke('A 的成本是多少？',Scope.from_ids(['kb']),settings=QuickSettings(router_policy=RouterPolicy(mode='OFF')))
    assert result.error_code is None and cheap==exp.calls==[]


def test_durable_unit_claims_never_evict_or_resend(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch, cheap='bad [E99]',budget=1000)
    for run_id in ('one', 'two'):
        assert invoke(chain, run_id=run_id)[0].error_code is None
    assert len(cheap) == len(exp.calls) == 2 and len(gate.reservations) == 4
    assert invoke(chain, run_id='one')[0].error_code == 'DUPLICATE_REQUEST'
    assert len(cheap) == len(exp.calls) == 2 and len(gate.reservations) == 4
    assert not hasattr(chain,'_router_runs') and not hasattr(chain,'_router_attempts')


def test_durable_unit_concurrent_claims_have_no_history_capacity(monkeypatch):
    chain, gate, cheap, exp = make_chain(monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: invoke(chain, run_id=f'capacity-{i}')[0], range(8)))
    assert all(r.error_code is None for r in results)
    assert len(cheap) == len(gate.reservations) == 8


def test_dynamic_without_durable_authority_cannot_send(monkeypatch):
    from backend.app.application.quick_chain import LangChainQuickChain
    original, gate, cheap, exp = make_chain(monkeypatch)
    chain=LangChainQuickChain(original.knowledge_gateway,router_policy=original.router_policy,generation_roles=original.generation_roles)
    assert invoke(chain)[0].error_code=='RUN_LIFECYCLE_UNAVAILABLE'
    assert cheap == exp.calls == [] and gate.reservations == {}
