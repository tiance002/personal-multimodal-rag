"""Zero-send trusted launcher contracts; no services or Provider."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.s8_trusted_api import arguments,binding_snapshot,execution_mode,resource_identity,verify_identity
from scripts.s8_evaluation_controller import canonical,sha
from scripts.s7_graph_generation import QWEN,QWEN_ID


def test_resource_and_trusted_principal_are_computed_not_copied_from_plan(tmp_path):
    actual={'postgres_system_id':'123','database':'weknora_test','redis_run_id':'a'*40,'redis_db':'15',
            'tenant_id':1,'redis_prefix':'daily'}
    principal=SimpleNamespace(tenant_id=332,subject_id='verified-existing')
    expected={'tenant_id':332,'subject_sha256':sha(principal.subject_id),'storage_root':str(tmp_path.resolve())}
    plan={'resource_identity':resource_identity(actual),'runtime_identity_sha256':sha(canonical(resource_identity(actual))),
          'principal':expected}
    assert verify_identity(plan,actual,principal,tmp_path)==expected
    with pytest.raises(ValueError,match='PRINCIPAL'):
        verify_identity(plan,actual,SimpleNamespace(tenant_id=1,subject_id='verified-existing'),tmp_path)
    with pytest.raises(ValueError,match='RESOURCE'):
        verify_identity(plan,dict(actual,redis_db='14'),principal,tmp_path)


def test_effective_qwen_is_isolated_from_stored_configured_roles():
    configured={'cheap':{'provider':'siliconflow','model':'Xing','model_id':'existing-xing'},
                'expensive':{'provider':'deepseek','model':'existing','model_id':'existing-expensive'}}
    binding=binding_snapshot(configured)
    assert binding['configured_roles']['cheap']['model']=='Xing'
    assert binding['effective_roles']['cheap']=={'provider':'siliconflow','model':QWEN,'model_id':QWEN_ID}
    assert binding['effective_roles']['expensive']==configured['expensive']
    assert binding['database_role_write'] is False
    assert configured['cheap']['model']=='Xing'


def test_cli_defaults_cloud_off_no_ledger_and_requires_explicit_future_grant():
    args=arguments(['--plan','plan','--config','config','--principal-state','state','--arm','A','--output','out'])
    assert execution_mode(args) is False and args.ledger is None
    args.cloud=True
    with pytest.raises(ValueError,match='AUTHORIZATION'):
        execution_mode(args)
    args.authorization=Path('root-owner.json');args.ledger=Path('root-registered.json');args.case_id='one'
    assert execution_mode(args) is True
    args.cloud=False
    with pytest.raises(ValueError,match='READONLY'):
        execution_mode(args)


def test_cloud_requires_explicit_case_id_but_readonly_does_not():
    args=arguments(['--plan','plan','--config','config','--principal-state','state','--arm','A','--output','out'])
    assert execution_mode(args) is False and args.case_id is None
    args.cloud=True;args.authorization=Path('owner');args.ledger=Path('ledger')
    with pytest.raises(ValueError,match='CLOUD_CASE_ID_REQUIRED'):
        execution_mode(args)


def test_only_selected_case_is_authorized_without_widening_single_KB_ledger(monkeypatch):
    from scripts import s8_trusted_api as launcher
    plan={'cases':[{'case_id':'one','knowledge_base_id':'kb-one','eligible_arms':['A']},
                   {'case_id':'two','knowledge_base_id':'kb-two','eligible_arms':['A']}]}
    parent={'approved_case_arms':[{'case_id':'one','arm':'A'},{'case_id':'two','arm':'A'}]}
    ledger={'allowed_kbs':['kb-one']};calls=[]
    def actual_guard(owner,path,frozen,identity,case,arm):
        calls.append(case['case_id'])
        assert {'case_id':case['case_id'],'arm':arm} in owner['approved_case_arms']
        if case['knowledge_base_id'] not in ledger['allowed_kbs']:
            raise ValueError('S8_EXECUTION_SCOPE_MODEL_LIMIT_MISMATCH')
    monkeypatch.setattr(launcher,'validate_authorization',actual_guard)
    assert launcher.authorize_case(parent,Path('ledger'),plan,{},'one','A')=='one'
    assert calls==['one'] and ledger=={'allowed_kbs':['kb-one']}
    with pytest.raises(ValueError,match='SCOPE_MODEL_LIMIT'):
        launcher.authorize_case(parent,Path('ledger'),plan,{},'two','A')
    with pytest.raises(ValueError,match='APPROVED_ARM_CASE'):
        launcher.authorize_case(parent,Path('ledger'),plan,{},'absent','A')
    assert ledger=={'allowed_kbs':['kb-one']}
