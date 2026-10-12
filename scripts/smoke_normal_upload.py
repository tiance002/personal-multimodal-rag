"""One public synthetic document via the ordinary UI proxy/API and Worker.

Uses the owner's scoped cloud authorization. Existing receipt prevents reposting.
No credentials are read by this runner; provider calls occur in the service.
"""
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'var/reports/no-candidates-20261011/normal-upload-real.json'
BASE = 'http://127.0.0.1:14188/api/v1'
KB = 'd78f3434-0e8f-40bf-afcb-f1c2c311ff51'
CONTENT = '# 北辰测试项目\n\n北辰测试项目的负责人是陈澄，验收日期为2026年11月5日。交付物是离线资料检索工具。\n'.encode()


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)['data']


def main():
    report = json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else dict(
        schema_version=1, entry=BASE, kb_scope=[KB], document_kind='PUBLIC_SYNTHETIC',
        source_sha256=hashlib.sha256(CONTENT).hexdigest(), status='PENDING')
    if 'receipt' not in report:
        boundary = 'codex-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="no-candidates-public-smoke-20261011.md"\r\nContent-Type: text/markdown\r\n\r\n'.encode()
            + CONTENT + f'\r\n--{boundary}--\r\n'.encode())
        request = urllib.request.Request(BASE + f'/knowledge-bases/{KB}/documents', data=body,
            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary}, method='POST')
        with urllib.request.urlopen(request, timeout=30) as response:
            report['upload_http_status'] = response.status
            report['receipt'] = json.load(response)['data']
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    receipt = report['receipt']
    for _ in range(50):
        job = get('/ingestion-jobs/' + receipt['job_id'])
        report['job'] = job
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            break
        time.sleep(.5)
    report['document'] = get('/documents/' + receipt['document_id'])
    report['status'] = 'CHECKS_PASSED' if report['job']['status'] == 'succeeded' else 'FAIL_OR_PENDING'
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=report['status'], receipt=receipt, job=report['job']), ensure_ascii=False))
    if report['status'] != 'CHECKS_PASSED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
