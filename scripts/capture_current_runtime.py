"""Public runtime identity/hash snapshot; never inspect Config.Env or secret values."""
import hashlib
import json
import subprocess
import argparse
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'var/reports/no-candidates-20261011/current-runtime-20261012.json'


def run(argv):
    result=subprocess.run(argv,capture_output=True,text=True,encoding='utf-8')
    if result.returncode:raise RuntimeError('PUBLIC_RUNTIME_CAPTURE_FAILED')
    return result.stdout.strip()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--after-tls-fix',action='store_true')
    parser.add_argument('--after-input-route',action='store_true')
    parser.add_argument('--final-input-route',action='store_true')
    parser.add_argument('--core-final',action='store_true')
    parser.add_argument('--study-fix',action='store_true')
    options=parser.parse_args()
    destination=OUT.with_name('current-runtime-after-tls-20261012.json') if options.after_tls_fix else OUT
    if options.after_input_route: destination=OUT.with_name('current-runtime-after-input-route-20261012.json')
    if options.final_input_route: destination=OUT.with_name('current-runtime-final-input-route-20261012.json')
    if options.core_final: destination=OUT.with_name('current-runtime-core-final-20261012.json')
    if options.study_fix: destination=OUT.with_name('current-runtime-study-fix-20261012.json')
    data=dict(head=run(['git','rev-parse','HEAD']),branch=run(['git','branch','--show-current']),
              dirty=True,entry='http://127.0.0.1:14188',provider_calls=0,containers={})
    for role in ('api','worker','db','redis'):
        name='raglocalfirst0930-'+role+'-1'
        raw=run(['docker','inspect',name,'--format','{{.Id}} {{.Image}} {{.State.Status}}'])
        identity,image,status=raw.split()
        item=dict(name=name,id=identity,image_id=image,status=status)
        if role in ('api','worker'):
            script="import sys,json,hashlib;from pathlib import Path;print(json.dumps(dict(python=sys.version.split()[0],executable=sys.executable,files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('/app/backend/app/main.py'),Path('/app/backend/app/bootstrap.py'),Path('/app/backend/app/workers/ingestion.py'),Path('/app/backend/app/adapters/models/cloud.py'),Path('/app/backend/app/ports/model_usage.py'),Path('/app/backend/app/application/answer_service.py'),Path('/app/backend/app/adapters/postgres/knowledge_repository.py'),Path('/app/backend/app/application/quick_chain.py'),Path('/app/backend/app/application/final_answer_commit.py')]})))"
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
    destination.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='IDENTITY_CAPTURED',migration=data['migration'],api_worker_same_image=True,
                         config_revision=data['model_configuration']['revision'],provider_calls=0)))


if __name__=='__main__':
    main()
