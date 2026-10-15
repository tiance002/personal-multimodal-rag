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
    assert 'backend.app*' in config['tool']['setuptools']['packages']['find']['exclude']
    main=Path('frontend/src/main.tsx').read_text(encoding='utf-8')
    assert '?? "weknora_python"' in main and 'implementation === "legacy"' in main
