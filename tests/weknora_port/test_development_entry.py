"""New default selection/configuration gates; no infrastructure on import."""
from pathlib import Path
from dataclasses import replace
import tomllib,pytest
from backend.weknora_port.development import DevelopmentSettings,DevelopmentConfigurationError,own_storage

def settings(tmp_path):
    key=tmp_path/'key';key.write_bytes(b's'*32)
    return DevelopmentSettings('postgresql://synthetic:synthetic@127.0.0.1:56423/weknora_test','7696088403504697382','weknora_test',
        'redis://127.0.0.1:56424/15','fa7bde76dcd020d396902fa924d0ea3e7b26ceb2','weknora-python-test:'+tmp_path.name,tmp_path/'storage',key)

def test_missing_settings_never_fall_back_to_legacy():
    with pytest.raises(DevelopmentConfigurationError,match='DATABASE_URL_REQUIRED'):
        DevelopmentSettings.from_environment({'RAG_DATABASE_URL':'postgresql://legacy/old'})

def development_environment(cfg):
    return {'WEKNORA_PORT_'+name:str(value) for name,value in {
        'DATABASE_URL':cfg.database_url,'DATABASE_SYSTEM_ID':cfg.system_id,'DATABASE_NAME':cfg.database,
        'REDIS_URL':cfg.redis_url,'REDIS_RUN_ID':cfg.redis_run_id,'REDIS_PREFIX':cfg.redis_prefix,
        'STORAGE_ROOT':cfg.storage,'SECRET_KEY_FILE':cfg.secret_key_file}.items()}

def test_fallback_strategy_keeps_model_default(tmp_path):
    cfg=settings(tmp_path)
    assert cfg.fallback_strategy=='model'
    assert DevelopmentSettings.from_environment(development_environment(cfg)).fallback_strategy=='model'


def test_source_units_are_explicit_startup_opt_in(tmp_path):
    cfg=settings(tmp_path);env=development_environment(cfg)
    assert cfg.source_evidence_mode=='off'
    env['WEKNORA_PORT_SOURCE_EVIDENCE_MODE']='bounded_source_v1'
    assert DevelopmentSettings.from_environment(env).source_evidence_mode=='bounded_source_v1'
    env['WEKNORA_PORT_SOURCE_EVIDENCE_MODE']='unbounded'
    with pytest.raises(DevelopmentConfigurationError,match='SOURCE_EVIDENCE_MODE_INVALID'):
        DevelopmentSettings.from_environment(env)

def test_query_understand_model_id_loads_from_environment(tmp_path):
    env=development_environment(settings(tmp_path))
    env['WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID']='owner-model-id'
    cfg=DevelopmentSettings.from_environment(env)
    assert cfg.query_understand_model_id=='owner-model-id'
    env.pop('WEKNORA_PORT_QUERY_UNDERSTAND_MODEL_ID')
    assert DevelopmentSettings.from_environment(env).query_understand_model_id==''


@pytest.mark.parametrize('value',['0','127','32769','-1','1.5','True',''])
def test_thinking_budget_rejects_invalid_environment_before_assembly(tmp_path,value):
    env=development_environment(settings(tmp_path))
    env['WEKNORA_PORT_THINKING_BUDGET_TOKENS']=value
    with pytest.raises(DevelopmentConfigurationError,match='THINKING_BUDGET_TOKENS_INVALID'):
        DevelopmentSettings.from_environment(env)


def test_thinking_budget_environment_reaches_real_assembly_options(tmp_path,monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import backend.weknora_port.runtime as runtime_module
    from backend.weknora_port.development import assemble
    from backend.weknora_port.conversation_configuration import resolved_chat_options
    from backend.weknora_port.models import Message,provider_config,build_chat_body
    from backend.weknora_port.storage.history import LocalPrincipal
    import backend.weknora_port.storage.connections as connections
    from redis.asyncio import Redis

    env=development_environment(settings(tmp_path))
    assert DevelopmentSettings.from_environment(env).thinking_budget_tokens==8192
    env['WEKNORA_PORT_THINKING_BUDGET_TOKENS']='4096'
    cfg=DevelopmentSettings.from_environment(env)
    guard=SimpleNamespace(connection=SimpleNamespace(execute=AsyncMock(return_value=
        SimpleNamespace(fetchone=AsyncMock(return_value={'principal':'present','image':'present','calls':'present'})))))
    class Leases:
        def __init__(self,*_args,**_kwargs):pass
        @asynccontextmanager
        async def lease(self,**_kwargs):yield guard
    redis=SimpleNamespace(info=AsyncMock(return_value={'run_id':cfg.redis_run_id}),aclose=AsyncMock())
    class Runtime:
        def __init__(self,*_args,**kwargs):self.conversation=kwargs['conversation']
        async def stop_background_workers(self):pass
    monkeypatch.setattr(connections,'IsolatedConnectionFactory',Leases)
    monkeypatch.setattr(Redis,'from_url',lambda *_args,**_kwargs:redis)
    monkeypatch.setattr(runtime_module,'ApplicationRuntime',Runtime)

    async def exercise():
        async with assemble(cfg,principal=LocalPrincipal(81013,'synthetic-owner')) as app:
            summary=app.conversation.summary
            assert summary['temperature']==.3 and summary['max_completion_tokens']==1024
            assert summary['thinking_budget_tokens']==4096
            options=resolved_chat_options(summary,'auto')
            body=build_chat_body(provider_config('siliconflow','Qwen/Qwen3.5-4B'),
                (Message('user','question'),),options,stream=True)
            assert body['enable_thinking'] is True and body['thinking_budget']==4096
            assert body['max_tokens']==1024
    asyncio.run(exercise())

@pytest.mark.parametrize('strategy',['model','fixed'])
def test_fallback_strategy_loads_explicit_environment(tmp_path,strategy):
    env=development_environment(settings(tmp_path))
    env['WEKNORA_PORT_FALLBACK_STRATEGY']=strategy
    assert DevelopmentSettings.from_environment(env).fallback_strategy==strategy

@pytest.mark.parametrize('strategy',['','unknown','FIXED'])
def test_fallback_strategy_rejects_invalid_environment_and_direct_settings(tmp_path,strategy):
    cfg=settings(tmp_path);env=development_environment(cfg)
    env['WEKNORA_PORT_FALLBACK_STRATEGY']=strategy
    with pytest.raises(DevelopmentConfigurationError,match='FALLBACK_STRATEGY_INVALID'):
        DevelopmentSettings.from_environment(env)
    with pytest.raises(DevelopmentConfigurationError,match='FALLBACK_STRATEGY_INVALID'):
        replace(cfg,fallback_strategy=strategy)

@pytest.mark.parametrize('change',[{'system_id':'7691227493754040358'},{'redis_url':'redis://127.0.0.1:56424/0'},
    {'redis_run_id':'not-an-id'},{'database':'old'},{'redis_prefix':'old-prefix'},{'storage':Path('relative')}])
def test_explicit_resource_identity_validation(tmp_path,change):
    with pytest.raises(DevelopmentConfigurationError):replace(settings(tmp_path),**change)

def test_storage_refuses_existing_unowned_and_wrong_database(tmp_path):
    cfg=settings(tmp_path);cfg.storage.mkdir();(cfg.storage/'original.txt').write_text('keep')
    with pytest.raises(DevelopmentConfigurationError,match='UNOWNED_STORAGE'):own_storage(cfg)
    assert (cfg.storage/'original.txt').read_text()=='keep'
    clean=replace(cfg,storage=tmp_path/'new-storage');own_storage(clean);own_storage(clean)
    with pytest.raises(DevelopmentConfigurationError,match='STORAGE_DATABASE_IDENTITY_MISMATCH'):
        own_storage(replace(clean,database='weknora_other',database_url='postgresql://s:s@127.0.0.1/weknora_other'))

def test_new_package_and_frontend_default_selectors():
    config=tomllib.loads(Path('pyproject.toml').read_text())
    assert config['project']['scripts']['weknora-python-api']=='backend.weknora_port.entrypoint:main'
    assert config['project']['scripts']['weknora-python-worker']=='backend.weknora_port.worker:main'
    discovery=config['tool']['setuptools']['packages']['find']
    # Shared parser DTOs now live in the new chain; no legacy parent is packaged.
    assert 'backend.app.domain' not in discovery['include']
    assert 'backend.app' not in discovery['include'] and 'backend.app*' not in discovery['include']
    assert 'backend.tests*' in discovery['exclude']
    main=Path('frontend/src/main.tsx').read_text(encoding='utf-8')
    assert 'WeKnoraPythonApp' in main
    assert 'VITE_RAG_IMPLEMENTATION' not in main and './app/App' not in main


def test_passive_evidence_opt_in_and_execution_conflict(tmp_path):
    env=development_environment(settings(tmp_path))
    assert DevelopmentSettings.from_environment(env).evidence_dir is None
    env['WEKNORA_PORT_EVIDENCE_DIR']='E:/WK/run/s3-product-20261019/evidence'
    assert DevelopmentSettings.from_environment(env).evidence_dir==Path(env['WEKNORA_PORT_EVIDENCE_DIR'])
    env['WEKNORA_PORT_EXECUTION_FILE']='E:/WK/run/execution.json'
    with pytest.raises(DevelopmentConfigurationError,match='EXECUTION_AND_PASSIVE_EVIDENCE_CONFLICT'):
        DevelopmentSettings.from_environment(env)
    env.pop('WEKNORA_PORT_EXECUTION_FILE')
    env['WEKNORA_PORT_EVIDENCE_DIR']='C:/evidence'
    with pytest.raises(DevelopmentConfigurationError,match='EVIDENCE_ABSOLUTE_D_E_PATH_REQUIRED'):
        DevelopmentSettings.from_environment(env)
