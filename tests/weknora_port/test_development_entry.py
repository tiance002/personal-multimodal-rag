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
    # The MinerU bridge uses only the existing pure domain DTO. The app parent
    # stays a namespace in wheels, excluding its old bootstrap/API/service code.
    assert 'backend.app.domain' in discovery['include']
    assert 'backend.app' not in discovery['include'] and 'backend.app*' not in discovery['include']
    assert 'backend.tests*' in discovery['exclude']
    main=Path('frontend/src/main.tsx').read_text(encoding='utf-8')
    assert '?? "weknora_python"' in main and 'implementation === "legacy"' in main
