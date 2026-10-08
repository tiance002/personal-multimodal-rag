"""Project-local API key loading; every key in this file is synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import scripts.with_clean_slate as clean_slate


def profile():
    return json.loads(Path('deploy/clean-slate/profile.json').read_text(encoding='utf-8'))


def test_project_secret_file_allows_only_two_keys_and_keeps_security_settings(monkeypatch,tmp_path):
    sf='SIMULATED-SILICONFLOW-KEY-NEVER-REAL'
    ds='SIMULATED-DEEPSEEK-KEY-NEVER-REAL'
    (tmp_path/'.env.local').write_text('\n'.join([
        f'SILICONFLOW_API_KEY="{sf}"',f'DEEPSEEK_API_KEY={ds}',
        'RAG_DATABASE_URL=postgresql://attacker/db','RAG_STORAGE_ROOT=C:/unsafe',
        'RAG_CLOUD_ENABLED=true','RAG_EMBEDDING_EGRESS_ENABLED=true',
        'POSTGRES_PASSWORD=SIMULATED-OTHER-SECRET','LANGFUSE_SECRET_KEY=SIMULATED-OTHER-SECRET',
    ]),encoding='utf-8')
    monkeypatch.setattr(clean_slate,'ROOT',tmp_path)
    inherited={'RAG_CLOUD_ENABLED':'true','RAG_EMBEDDING_EGRESS_ENABLED':'false',
        'RAG_DATABASE_URL':'postgresql://old/db','RAG_STORAGE_ROOT':'C:/old'}
    env=clean_slate.resolve_environment(profile(),inherited)
    assert env['SILICONFLOW_API_KEY']==sf and env['DEEPSEEK_API_KEY']==ds
    assert env['RAG_DATABASE_URL'].endswith('/rag_clean_dev_20261008t072656z_352f705b')
    assert env['RAG_STORAGE_ROOT']==profile()['storage_root']
    assert env['RAG_CLOUD_ENABLED']=='false' and env['RAG_EMBEDDING_EGRESS_ENABLED']=='false'
    assert 'POSTGRES_PASSWORD' not in env and 'LANGFUSE_SECRET_KEY' not in env
    assert inherited['RAG_DATABASE_URL']=='postgresql://old/db'
    assert os.environ.get('SILICONFLOW_API_KEY')!=sf and os.environ.get('DEEPSEEK_API_KEY')!=ds


def test_missing_project_keys_stay_missing_and_project_file_is_ignored(monkeypatch,tmp_path):
    monkeypatch.setattr(clean_slate,'ROOT',tmp_path)
    (tmp_path/'.env.local').write_text('RAG_CLOUD_ENABLED=true\n',encoding='utf-8')
    env=clean_slate.resolve_environment(profile(),{})
    assert 'SILICONFLOW_API_KEY' not in env and 'DEEPSEEK_API_KEY' not in env
    root=Path.cwd()
    assert subprocess.run(['git','check-ignore','--no-index','--quiet','.env.local'],cwd=root).returncode==0
    assert '/.env.local' in (root/'.dockerignore').read_text(encoding='utf-8')
    tracked=subprocess.run(['git','ls-files','--error-unmatch','.env.local'],cwd=root,
        capture_output=True,text=True)
    assert tracked.returncode!=0
    assert 'SIMULATED-' not in (root/'deploy/clean-slate/models.json').read_text(encoding='utf-8')


def test_key_values_do_not_appear_in_check_logs_or_error_echo(monkeypatch,tmp_path,capsys):
    secret='SIMULATED-DO-NOT-ECHO-KEY'
    local_profile=profile()
    (tmp_path/'deploy/clean-slate').mkdir(parents=True)
    (tmp_path/'deploy/clean-slate/profile.json').write_text(json.dumps(local_profile),encoding='utf-8')
    (tmp_path/'.env.local').write_text(f'SILICONFLOW_API_KEY="{secret}"\nDEEPSEEK_API_KEY={secret}',encoding='utf-8')
    monkeypatch.setattr(clean_slate,'ROOT',tmp_path)
    monkeypatch.setattr(clean_slate,'verify_database',lambda *_:{'status':'PASS','model_calls':0})
    monkeypatch.setattr(sys,'argv',['with_clean_slate.py','--mode','check'])
    assert clean_slate.main()==0
    output=capsys.readouterr().out
    assert '"status": "PASS"' in output and secret not in output

    (tmp_path/'.env.local').write_text(f'SILICONFLOW_API_KEY="{secret}',encoding='utf-8')
    assert clean_slate.main()==1
    output=capsys.readouterr().out
    assert 'ValueError' in output and secret not in output
    # The project report/config surfaces do not serialize child environment.
    assert secret not in json.dumps({'status':'PASS','model_calls':0})


def test_only_project_child_receives_local_keys_and_missing_file_is_optional(monkeypatch,tmp_path,capsys):
    (tmp_path/'deploy/clean-slate').mkdir(parents=True)
    (tmp_path/'deploy/clean-slate/profile.json').write_text(json.dumps(profile()),encoding='utf-8')
    monkeypatch.setattr(clean_slate,'ROOT',tmp_path)
    # Missing .env.local stays valid and cannot erase the provider's explicit
    # MODEL_CREDENTIAL_MISSING refusal when no inherited key exists.
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    assert clean_slate.resolve_environment(profile(),{})['RAG_CLOUD_ENABLED']=='false'
    (tmp_path/'.env.local').write_text(
        'SILICONFLOW_API_KEY=SIMULATED-CHILD-SF\nDEEPSEEK_API_KEY=SIMULATED-CHILD-DS\n'
        'RAG_EMBEDDING_EGRESS_ENABLED=true\n',encoding='utf-8')
    monkeypatch.setenv('RAG_EMBEDDING_EGRESS_ENABLED','false')
    monkeypatch.setattr(clean_slate,'verify_database',lambda *_:{'status':'PASS','model_calls':0})
    captured={}
    def fake_run(command,*,cwd,env):
        captured.update(command=command,cwd=cwd,env=env)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(clean_slate.subprocess,'run',fake_run)
    monkeypatch.setattr(sys,'argv',['with_clean_slate.py','--mode','worker'])
    assert clean_slate.main()==0
    assert captured['env']['SILICONFLOW_API_KEY']=='SIMULATED-CHILD-SF'
    assert captured['env']['DEEPSEEK_API_KEY']=='SIMULATED-CHILD-DS'
    assert captured['env']['RAG_EMBEDDING_EGRESS_ENABLED']=='false'
    assert os.environ.get('SILICONFLOW_API_KEY')!='SIMULATED-CHILD-SF'
    assert os.environ.get('DEEPSEEK_API_KEY')!='SIMULATED-CHILD-DS'
    output=capsys.readouterr().out
    assert 'SIMULATED-CHILD' not in output
