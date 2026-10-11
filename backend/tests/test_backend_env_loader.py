"""Project backend loader contracts. Every credential here is SIMULATED."""
import os
import pytest
from backend.app import env_loader


@pytest.fixture(autouse=True)
def clean_sf_environment(monkeypatch):
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    monkeypatch.delenv('RAG_SILICONFLOW_API_KEY',raising=False)
    yield
    os.environ.pop('SILICONFLOW_API_KEY',None)
    os.environ.pop('RAG_SILICONFLOW_API_KEY',None)


def test_normal_root_env_loads_exact_sf_registered_key(tmp_path,monkeypatch,capsys):
    secret='SIMULATED-ROOT-SF-KEY'
    (tmp_path/'.env').write_text(f'SILICONFLOW_API_KEY="{secret}"\n',encoding='utf-8')
    monkeypatch.setattr(env_loader,'PROJECT_ROOT',tmp_path)
    loaded=env_loader.load_backend_env()
    observed=os.environ.get('SILICONFLOW_API_KEY')
    assert observed==secret
    assert loaded==('SILICONFLOW_API_KEY',)
    output=capsys.readouterr()
    assert output.out==output.err==''


@pytest.mark.parametrize('canonical,alias,file_text,expected',[
    ('SIMULATED-PROCESS-CANONICAL','SIMULATED-PROCESS-ALIAS','SILICONFLOW_API_KEY=SIMULATED-FILE\n','SIMULATED-PROCESS-CANONICAL'),
    ('','SIMULATED-PROCESS-ALIAS','SILICONFLOW_API_KEY=SIMULATED-FILE\n',''),
    (None,'SIMULATED-PROCESS-ALIAS','SILICONFLOW_API_KEY=SIMULATED-FILE\n','SIMULATED-PROCESS-ALIAS'),
    (None,'','SILICONFLOW_API_KEY=SIMULATED-FILE\n',''),
    (None,None,'RAG_SILICONFLOW_API_KEY=SIMULATED-ALIAS\nSILICONFLOW_API_KEY=SIMULATED-CANONICAL\n','SIMULATED-CANONICAL'),
    (None,None,'SILICONFLOW_API_KEY=\nRAG_SILICONFLOW_API_KEY=SIMULATED-ALIAS\n',''),
    (None,None,'RAG_SILICONFLOW_API_KEY=SIMULATED-FILE-ALIAS\n','SIMULATED-FILE-ALIAS'),
])
def test_sf_explicit_precedence(canonical,alias,file_text,expected,tmp_path,monkeypatch,capsys):
    if canonical is not None:monkeypatch.setenv('SILICONFLOW_API_KEY',canonical)
    if alias is not None:monkeypatch.setenv('RAG_SILICONFLOW_API_KEY',alias)
    path=tmp_path/'.env';path.write_text(file_text,encoding='utf-8')
    loaded=env_loader.load_backend_env(path)
    observed=os.environ.get('SILICONFLOW_API_KEY')
    assert observed==expected
    assert ('SILICONFLOW_API_KEY' in loaded)==(canonical is None)
    if alias is not None:
        observed_alias=os.environ.get('RAG_SILICONFLOW_API_KEY')
        assert observed_alias==alias
    assert capsys.readouterr().out==''


def test_exact_sf_alias_only_no_arbitrary_key_discovery(tmp_path,monkeypatch,capsys):
    names=('SILICONFLOW_API_KEY_EXTRA','SILICONFLOW_SECRET_KEY','SF_API_KEY','OTHER_API_KEY',
        'RAG_SILICONFLOW_API_KEY_EXTRA')
    for name in names:monkeypatch.delenv(name,raising=False)
    path=tmp_path/'.env';path.write_text('\n'.join(f'{n}=SIMULATED-IGNORED' for n in names),encoding='utf-8')
    loaded=env_loader.load_backend_env(path)
    observed=os.environ.get('SILICONFLOW_API_KEY')
    assert observed is None
    assert set(loaded)=={'RAG_SILICONFLOW_API_KEY_EXTRA'}  # Existing RAG config namespace, never forwarded.
    for name in names[:-1]:
        present=name in os.environ
        assert not present
    assert capsys.readouterr().out==''


def test_process_alias_normalizes_when_file_absent(tmp_path,monkeypatch):
    monkeypatch.setenv('RAG_SILICONFLOW_API_KEY','SIMULATED-INHERITED-ALIAS')
    loaded=env_loader.load_backend_env(tmp_path/'absent.env')
    observed=os.environ.get('SILICONFLOW_API_KEY')
    assert observed=='SIMULATED-INHERITED-ALIAS'
    assert loaded==('SILICONFLOW_API_KEY',)


def test_file_canonical_repeated_load_never_overwrites_process_value(tmp_path):
    path=tmp_path/'.env';path.write_text('SILICONFLOW_API_KEY=SIMULATED-FIRST\n',encoding='utf-8')
    assert env_loader.load_backend_env(path)==('SILICONFLOW_API_KEY',)
    path.write_text('SILICONFLOW_API_KEY=SIMULATED-SECOND\n',encoding='utf-8')
    assert env_loader.load_backend_env(path)==()
    observed=os.environ.get('SILICONFLOW_API_KEY')
    assert observed=='SIMULATED-FIRST'


def test_parse_failure_is_atomic_and_never_echoes_secret(tmp_path,capsys):
    secret='SIMULATED-NO-ERROR-ECHO'
    path=tmp_path/'.env';path.write_text(f'SILICONFLOW_API_KEY={secret}\nSILICONFLOW_API_KEY="{secret}\n',encoding='utf-8')
    with pytest.raises(ValueError) as error:env_loader.load_backend_env(path)
    assert str(error.value)=='BACKEND_ENV_INVALID'
    observed=os.environ.get('SILICONFLOW_API_KEY')
    assert observed is None
    output=capsys.readouterr()
    assert secret not in output.out+output.err+str(error.value)


def test_no_file_no_sf_values_loads_nothing(tmp_path,capsys):
    assert env_loader.load_backend_env(tmp_path/'absent.env')==()
    observed=os.environ.get('SILICONFLOW_API_KEY')
    assert observed is None
    assert capsys.readouterr().out==''
