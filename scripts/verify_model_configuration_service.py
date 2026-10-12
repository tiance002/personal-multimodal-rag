"""Real same-model save/reload/restart check, without reading keys or calling models."""
import ast
import json
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT/'var/reports/no-candidates-20261011/model-config-save-real-20261012.json'
BASE = 'http://127.0.0.1:8000'
EXPECTED = {
    'embedding': ('siliconflow','BAAI/bge-m3','https://api.siliconflow.cn/v1'),
    'rerank': ('siliconflow','BAAI/bge-reranker-v2-m3','https://api.siliconflow.cn/v1'),
    'chat_cheap': ('siliconflow','XingChenAGI/Xing4.0-29B','https://api.siliconflow.cn/v1'),
    'chat_expensive': ('deepseek','deepseek-flash','https://api.deepseek.com'),
    'vision': ('deepseek','deepseek-flash','https://api.deepseek.com'),
}


def docker(*args):
    result = subprocess.run(['docker',*args], capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('DOCKER_OPERATION_FAILED')
    return result.stdout


def api(path, payload=None):
    request = Request(BASE+path, data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type':'application/json'}, method='PUT' if payload is not None else 'GET')
    with urlopen(request, timeout=30) as response:
        return json.load(response)['data']


def counters():
    sql = """SELECT json_build_object('queued_jobs',(SELECT count(*) FROM ingestion_jobs WHERE status IN ('queued','leased','running')),
      'reservations',(SELECT count(*) FROM service_token_reservations),
      'old_unknown',(SELECT count(*) FROM service_token_reservations WHERE state='unknown'),
      'active_sha',(SELECT md5(string_agg(id::text||':'||coalesce(active_version_id::text,''),',' ORDER BY id)) FROM documents))"""
    return json.loads(docker('exec','raglocalfirst0930-db-1','psql','-U','rag','-d','rag','-XAt','-v','ON_ERROR_STOP=1','-c',sql))


def wait_ready(revision):
    for _ in range(30):
        try:
            value = api('/api/v1/settings/models')
            if value.get('configuration_revision') == revision and value['status'] == 'READY_CONFIG':
                return value
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('CONFIGURATION_REVISION_NOT_READY')


def worker_loaded(revision):
    for _ in range(20):
        result = subprocess.run(['docker','logs','--tail','40','raglocalfirst0930-worker-1'], capture_output=True,text=True,encoding='utf-8')
        for line in result.stdout.splitlines():
            if 'model_configuration.loaded' not in line:
                continue
            event = ast.literal_eval(line)
            if event.get('revision') == revision:
                return event
        time.sleep(1)
    raise RuntimeError('WORKER_REVISION_NOT_LOADED')


def main():
    if REPORT.exists():
        raise RuntimeError('EXISTING_RECEIPT_RECOVER_READ_ONLY_DO_NOT_REPEAT_SAVE')
    before = api('/api/v1/settings/model-configuration')
    assert {r['role']:(r['provider'],r['model'],r['base_url']) for r in before['roles']} == EXPECTED
    count = counters()
    assert count['queued_jobs'] == 0, 'QUEUED_JOBS_REQUIRE_SEPARATE_REVIEW'
    report = dict(status='PENDING', before=before, before_counters=count, provider_calls=0)
    def save():
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report['save_dispatched'] = True
    save()
    payload = dict(revision=before['revision'], roles=[{k:r[k] for k in ('role','provider','model','base_url')} for r in before['roles']])
    after = api('/api/v1/settings/model-configuration', payload)
    assert all('api_key' not in row for row in after['roles'])
    report['after'] = after
    save()
    revision = after['revision']
    report['ready'] = wait_ready(revision)
    report['worker_loaded'] = worker_loaded(revision)
    report['restart_dispatched'] = True
    save()
    docker('restart','raglocalfirst0930-api-1','raglocalfirst0930-worker-1')
    report['after_restart_ready'] = wait_ready(revision)
    report['after_restart_worker_loaded'] = worker_loaded(revision)
    report['after_restart_config'] = api('/api/v1/settings/model-configuration')
    assert report['after_restart_config'] == after
    report['after_counters'] = counters()
    assert count == report['after_counters']
    report.update(status='CHECKS_PASSED', secrets_read=False, keys_returned=False)
    save()
    print(json.dumps(dict(status=report['status'],revision=revision,provider_calls=0,
                         api_worker_same_revision=True,restart_preserved=True,old_unknown_unchanged=True)))


if __name__ == '__main__':
    main()
