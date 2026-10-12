"""Run the unchanged boundary tests against a separate tracked HEAD snapshot."""
import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'var/reports/no-candidates-20261011/context-pool-baseline-head'


def main():
    if OUT.exists():
        raise RuntimeError('BASELINE_ALREADY_EXPORTED_USE_EXISTING_EVIDENCE')
    result=subprocess.run(['git','archive','--format=zip','HEAD','backend','pyproject.toml'],capture_output=True)
    assert result.returncode == 0
    OUT.mkdir()
    with zipfile.ZipFile(io.BytesIO(result.stdout)) as archive:
        for name in archive.namelist():
            destination=(OUT/name).resolve()
            assert destination.is_relative_to(OUT.resolve())
        archive.extractall(OUT)
    runner=OUT/'run_baseline.py'
    runner.write_text('''import sys, os, json, socket
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
def guard(event,args):
    if event in {'socket.connect','socket.sendto'}:raise RuntimeError('BASELINE_NETWORK_FORBIDDEN')
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        name=Path(os.fsdecode(args[0])).name.lower()
        if name.startswith('.env') or name in {'auth.json','token'}:raise RuntimeError('BASELINE_SECRET_FORBIDDEN')
sys.addaudithook(guard)
import backend.app.application.retrieval as source
assert Path(source.__file__).resolve().is_relative_to(Path(__file__).resolve().parent)
print('BASELINE_SOURCE='+source.__file__)
import pytest
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','backend/tests/test_context_pool_policy.py']))
''',encoding='utf-8')
    env={key:value for key,value in os.environ.items() if not key.startswith(('RAG_','DEEPSEEK_','SILICONFLOW_','LANGFUSE_','OLLAMA_','PYTHONPATH'))}
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    executed=subprocess.run([str(ROOT/'.venv/Scripts/python.exe'),str(runner)],cwd=OUT,env=env,capture_output=True)
    log=OUT.parent/'context-pool-baseline-head.txt'
    log.write_bytes(executed.stdout+executed.stderr)
    report=dict(head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                argv=[str(ROOT/'.venv/Scripts/python.exe'),str(runner)],cwd=str(OUT),exit_code=executed.returncode,
                archive_sha256=hashlib.sha256(result.stdout).hexdigest(),provider_calls=0)
    (OUT.parent/'context-pool-baseline-head.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    return executed.returncode


if __name__=='__main__':
    raise SystemExit(main())
