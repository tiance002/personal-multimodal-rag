"""Refresh only Docker-assigned ports after verifying the exact test containers."""
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'var/reports/p5-redis-lifecycle-r1/isolated-target.json'
OUT=ROOT/'var/reports/no-candidates-20261011/isolated-port-refresh-20261012.json'


def run(*args):
    result=subprocess.run(['docker',*args],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode == 0, 'ISOLATED_CONTROL_PLANE_CHECK_FAILED'
    return result.stdout.strip()


def main():
    before=json.loads(PATH.read_text(encoding='utf-8'))
    after=dict(before)
    for prefix,internal in [('pg','5432/tcp'),('redis','6379/tcp')]:
        # No Config.Env inspection, credentials or image/container substitution.
        observed=run('inspect',before[prefix+'_container'],'--format','{{.Id}} {{.Image}} {{json .NetworkSettings.Ports}}')
        container,image,ports=observed.split(' ',2)
        assert (container,image)==(before[prefix+'_container'],before[prefix+'_image'])
        binding=json.loads(ports)[internal]
        assert len(binding)==1 and binding[0]['HostIp']=='127.0.0.1'
        after[prefix+'_port']=int(binding[0]['HostPort'])
    identity=run('exec',before['pg_container'],'psql','-U','p55a','-d','p55a_lifecycle_test','-XAt','-v','ON_ERROR_STOP=1','-c',
        'SELECT current_database(),oid,(SELECT system_identifier FROM pg_control_system()),(SELECT version_num FROM alembic_version) FROM pg_database WHERE datname=current_database();')
    assert identity=='p55a_lifecycle_test|16384|7694635764170571814|0022_memory_vector_index'
    after['verified_revision']='0022_memory_vector_index'
    OUT.write_text(json.dumps(dict(before=before,after=after,identity=identity,status='VERIFIED_TEST_TARGET_ONLY'),indent=2),encoding='utf-8')
    PATH.write_text(json.dumps(after,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='VERIFIED_TEST_TARGET_ONLY',pg_port=after['pg_port'],redis_port=after['redis_port'],provider_calls=0)))


if __name__=='__main__':
    main()
