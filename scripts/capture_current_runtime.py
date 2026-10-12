"""Public runtime identity/hash snapshot; never inspect Config.Env or secret values."""
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'var/reports/no-candidates-20261011/current-runtime-20261012.json'


def run(argv):
    result=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8')
    if result.returncode:raise RuntimeError('PUBLIC_RUNTIME_CAPTURE_FAILED')
    return result.stdout.strip()


def main():
    data=dict(head=run(['git','rev-parse','HEAD']),branch=run(['git','branch','--show-current']),
              dirty=True,entry='http://127.0.0.1:14188',provider_calls=0,containers={})
    for role in ('api','worker','db','redis'):
        name='raglocalfirst0930-'+role+'-1'
        raw=run(['docker','inspect',name,'--format','{{.Id}} {{.Image}} {{.State.Status}}'])
        identity,image,status=raw.split()
        item=dict(name=name,id=identity,image_id=image,status=status)
        if role in ('api','worker'):
            script="import sys,json,hashlib;from pathlib import Path;print(json.dumps(dict(python=sys.version.split()[0],executable=sys.executable,files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('/app/backend/app/main.py'),Path('/app/backend/app/bootstrap.py'),Path('/app/backend/app/workers/ingestion.py')]})))"
            item['runtime']=json.loads(run(['docker','exec',name,'python','-c',script]))
            for filename,digest in item['runtime']['files'].items():
                assert hashlib.sha256((ROOT/filename.removeprefix('/app/')).read_bytes()).hexdigest()==digest
        data['containers'][role]=item
    assert data['containers']['api']['image_id']==data['containers']['worker']['image_id']
    data['migration']=run(['docker','exec','raglocalfirst0930-db-1','psql','-U','rag','-d','rag','-XAt','-c','SELECT version_num FROM alembic_version'])
    with urlopen('http://127.0.0.1:8000/api/v1/settings/models',timeout=10) as response:
        data['model_readiness']=json.load(response)['data']
    with urlopen('http://127.0.0.1:8000/api/v1/settings/model-configuration',timeout=10) as response:
        data['model_configuration']=json.load(response)['data']
    assert all('api_key' not in row for row in data['model_configuration']['roles'])
    data['normal_ledger']=json.loads(run(['docker','exec','raglocalfirst0930-db-1','psql','-U','rag','-d','rag','-XAt','-c',
        "SELECT coalesce(json_agg(t),'[]') FROM (SELECT role,state,count(*) AS requests,sum(actual_input) AS actual_input,sum(actual_output) AS actual_output,sum(input_reserved+output_reserved) AS token_reserved FROM service_token_reservations WHERE policy_id IN ('owner-20261011-bge-m3-normal-v1','owner-20261011-xing-normal-v1','owner-20261011-deepseek-normal-v1') GROUP BY role,state ORDER BY role,state) t"]))
    OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='IDENTITY_CAPTURED',migration=data['migration'],api_worker_same_image=True,
                         config_revision=data['model_configuration']['revision'],provider_calls=0)))


if __name__=='__main__':
    main()
