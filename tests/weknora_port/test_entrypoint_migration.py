"""Non-deletion entry migration: rejection/dispatch contracts, never a supplier or database."""
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,subprocess,tomllib
import pytest
from scripts import weknora_port_verify_entry as entry
ROOT=Path(__file__).resolve().parents[2]


def args(tmp_path,mode='verify-existing',**changes):
    manifest=tmp_path/'manifest.json';manifest.write_text('{}',encoding='utf-8')
    result=tmp_path/'results.jsonl';result.write_text('{}\n',encoding='utf-8')
    value=dict(mode=mode,manifest=str(manifest),manifest_sha256=entry.sha(manifest),output=str(tmp_path/'new.json'),
        results=[str(result)],result_sha256=[entry.sha(result)],allow_isolated_read=True,allow_isolated_mock_execution=True)
    value.update(changes);return SimpleNamespace(**value)

ENV={'WEKNORA_PORT_TEST_DSN':'postgresql://synthetic:synthetic@127.0.0.1:56423/weknora_python_port_20261013',
     'WEKNORA_PORT_TEST_REDIS_RUN_ID':'a'*40}

@pytest.mark.parametrize('mode',['retrieval','quality','schema'])
def test_ambiguous_old_modes_never_dispatch_native_or_legacy(tmp_path,mode):
    with pytest.raises(entry.EntryBlocked,match='EXPLICIT_NATIVE_EVAL_MODE'):entry.eval_command(args(tmp_path,mode),ENV)

@pytest.mark.parametrize('change,code',[
    ({'allow_isolated_read':False},'ISOLATED_READ_AUTHORIZATION'),
    ({'manifest':None},'EXPLICIT_INPUT'),({'manifest_sha256':'0'*64},'INPUT_SHA256_MISMATCH'),
    ({'output':None},'EXPLICIT_NEW_OUTPUT'),({'results':[]},'EXPLICIT_RESULTS')])
def test_existing_verification_requires_explicit_inputs_and_authorization(tmp_path,change,code):
    with pytest.raises(entry.EntryBlocked,match=code):entry.eval_command(args(tmp_path,**change),ENV)


def test_existing_output_and_bad_input_hash_preserve_history(tmp_path):
    value=args(tmp_path);Path(value.output).write_text('keep',encoding='utf-8')
    with pytest.raises(entry.EntryBlocked,match='OUTPUT_EXISTS'):entry.eval_command(value,ENV)
    assert Path(value.output).read_text(encoding='utf-8')=='keep'
    value.output=str(tmp_path/'another.json');value.result_sha256=['f'*64]
    with pytest.raises(entry.EntryBlocked,match='INPUT_SHA256_MISMATCH'):entry.eval_command(value,ENV)


def test_verify_existing_dispatch_is_read_only_not_execution(tmp_path):
    command,output=entry.eval_command(args(tmp_path),ENV)
    assert command[2].endswith('weknora_port_six_cohort_verify.py')
    assert '--results' in command and 'weknora_port_six_mock.py' not in ' '.join(command)
    assert not output.exists()


def test_execute_native_dispatch_has_closed_model_and_redis_identity(tmp_path):
    value=args(tmp_path,'execute-native',results=[],result_sha256=[])
    command,output=entry.eval_command(value,ENV)
    assert command[2].endswith('weknora_port_six_mock.py')
    assert command[-4:]==['--redis-run-id','a'*40,'--redis-db','15']
    assert '--results' not in command and not output.exists()
    with pytest.raises(entry.EntryBlocked,match='AUTHORIZATION'):
        entry.eval_command(args(tmp_path,'execute-native',allow_isolated_mock_execution=False),ENV)
    with pytest.raises(entry.EntryBlocked,match='CANNOT_REUSE'):
        entry.eval_command(args(tmp_path,'execute-native'),ENV)
    with pytest.raises(entry.EntryBlocked,match='REDIS_RUN_ID'):
        entry.eval_command(value,{**ENV,'WEKNORA_PORT_TEST_REDIS_RUN_ID':''})


def test_default_verify_has_no_services_migrations_models_or_default_test_root(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(entry,'static_checks',lambda:{'schema_applied':False,'model_probe':'NOT_RUN'})
    monkeypatch.setattr(entry.subprocess,'run',lambda command,**kw:calls.append(command) or SimpleNamespace(returncode=0))
    output=tmp_path/'verify.json'
    assert entry.main(['verify','--output',str(output)])==0
    report=json.loads(output.read_text(encoding='utf-8'))
    assert report['scope']=='STATIC_AND_TARGETED_OFFLINE_ONLY_NOT_M0_ACCEPTANCE'
    assert report['schema_applied'] is False and report['model_probe']=='NOT_RUN'
    assert len(calls)==1 and calls[0][2:4]==['-m','pytest']
    assert '--noconftest' in calls[0] and not any('backend/tests' in arg or 'model_probe.py' in arg for arg in calls[0])


def test_schema_validation_is_not_native_execution(tmp_path):
    dataset=tmp_path/'core.jsonl';dataset.write_text(json.dumps({'question':'q','expected_chunk_ids':['chunk-fixture'],'answer_points':['fixture point'],'kb_scope':['kb']})+'\n',encoding='utf-8')
    output=tmp_path/'report.json'
    assert entry.main(['eval','--mode','schema','--dataset',str(dataset),'--output',str(output)])==0
    report=json.loads(output.read_text(encoding='utf-8'));assert report['operation']=='schema-validation' and report['executed_native'] is False


def test_new_and_legacy_test_configs_preserve_both_suites():
    config=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))
    assert config['tool']['pytest']['ini_options']['testpaths']==['tests/weknora_port']
    legacy=(ROOT/'pytest.legacy.ini').read_text(encoding='utf-8')
    assert 'backend/tests eval_center/tests' in legacy
    assert 'from backend.app.main import create_app' in (ROOT/'backend/tests/conftest.py').read_text(encoding='utf-8')
    default=(ROOT/'frontend/playwright.config.ts').read_text(encoding='utf-8')
    old=(ROOT/'frontend/playwright.legacy.config.ts').read_text(encoding='utf-8')
    assert 'testDir: "./tests/weknora-port"' in default and 'VITE_RAG_IMPLEMENTATION: undefined' in default
    assert 'VITE_RAG_IMPLEMENTATION: "legacy"' in old and 'testIgnore: "**/weknora-port/**"' in old
    assert 'reuseExistingServer: false' in default and 'reuseExistingServer: false' in old


def test_powershell_wrappers_do_not_invoke_old_probes_or_ddl():
    eval_text=(ROOT/'scripts/eval.ps1').read_text(encoding='utf-8')
    verify_text=(ROOT/'scripts/verify-m0.ps1').read_text(encoding='utf-8')
    for old in ('evaluate_rag_quality.py','evaluate_retrieval.py','model_probe.py','schema_check.ps1','alembic','compose'):
        assert old not in eval_text+verify_text
    assert 'Verify-Existing' in eval_text and 'Execute-Native' in eval_text
    assert 'AllowIsolatedRead' in eval_text and 'AllowIsolatedMockExecution' in eval_text
