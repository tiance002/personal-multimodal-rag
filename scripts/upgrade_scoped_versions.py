"""New immutable versions for the six server-confirmed legacy Scope documents.

Uses ordinary APIs, never SQL writes/identity edits. Source bytes stay private;
report contains only identities, hashes, Job results and pointer checks. Receipts
prevent duplicate uploads on resume. Stop at the first unsuccessful Job.
"""
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import uuid

from smoke_normal_upload import BASE, KB, get

DOCS = ('f7ee4505-2a5a-40c4-8fbc-5cc1b5c781e9','d283fecc-3287-4a77-805d-c687635d1ece',
    '87c2d4e7-5a24-49e4-9c0c-e8694d61d2f3','c11b9f37-d4b3-4089-837d-55bdd38623cb',
    '6d686ffe-7ec8-4ee4-8ca9-b7e2e451cc51','1fd5a928-d3d7-4bae-aa00-fc74701f61e5')
REPORT = Path(__file__).resolve().parents[1] / 'var/reports/no-candidates-20261011/scoped-new-versions-real.json'


def main():
    report = json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else dict(schema_version=1,
        kb_scope=[KB], confirmed_document_ids=list(DOCS), status='PENDING', documents={})
    def save():
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for document_id in DOCS:
        if document_id not in report['documents']:
            before = get('/documents/' + document_id)
            assert before['knowledge_base_id'] == KB
            assert before['index_compatibility']['status'] == 'INCOMPATIBLE'
            row = report['documents'][document_id] = dict(before_active_version=before['active_version_id'],
                before_version_no=before['version_no'], before_identity=before['index_identity'],
                source_sha256=before['source_sha256'], upload_dispatched=False)
            save()
        row = report['documents'][document_id]
        if row.get('status') == 'CHECKS_PASSED':
            continue
        if row['upload_dispatched'] and 'receipt' not in row:
            raise RuntimeError('UPLOAD_OUTCOME_UNKNOWN_READ_ONLY_RECOVERY_REQUIRED')
        if not row['upload_dispatched']:
            before = get('/documents/' + document_id)
            assert before['active_version_id'] == row['before_active_version']
            with urllib.request.urlopen(BASE + '/documents/' + document_id + '/source',timeout=30) as response:
                raw = response.read(32*1024*1024+1)
            assert len(raw) <= 32*1024*1024
            assert hashlib.sha256(raw).hexdigest() == row['source_sha256']
            filename = before['file_name']
            assert not any(character in filename for character in ('\r','\n','"'))
            boundary = 'codex-' + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {before["media_type"]}\r\n\r\n'.encode()
                + raw + f'\r\n--{boundary}--\r\n'.encode())
            row['upload_dispatched'] = True
            save()
            request = urllib.request.Request(BASE + f'/documents/{document_id}/versions',data=body,
                headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
            with urllib.request.urlopen(request,timeout=30) as response:
                row['upload_http_status'] = response.status
                row['receipt'] = json.load(response)['data']
            save()
        for _ in range(100):
            row['job'] = get('/ingestion-jobs/' + row['receipt']['job_id'])
            if row['job']['status'] in ('succeeded','failed','cancelled'):
                break
            time.sleep(.5)
        after = get('/documents/' + document_id)
        row['after_active_version'] = after['active_version_id']
        row['after_identity'] = after['index_identity']
        if row['job']['status'] != 'succeeded':
            assert after['active_version_id'] == row['before_active_version']
            row['status'] = 'FAIL_OR_PENDING_OLD_ACTIVE_PRESERVED'
            report['status'] = 'FAIL_OR_PENDING'
            save()
            print(json.dumps(dict(status=report['status'],document_id=document_id,job=row['job'],old_active_preserved=True)))
            raise SystemExit(1)
        assert after['source_sha256'] == row['source_sha256']
        assert after['version_no'] == row['before_version_no'] + 1
        assert after['active_version_id'] == row['receipt']['version_id']
        assert after['index_compatibility']['status'] == 'COMPATIBLE'
        row['status'] = 'CHECKS_PASSED'
        save()
        print(json.dumps(dict(document_id=document_id,status=row['status'],job_id=row['receipt']['job_id'])),flush=True)
    report['status'] = 'CHECKS_PASSED'
    save()
    print(json.dumps(dict(status=report['status'],documents=len(DOCS),historical_identity_updates=0)))


if __name__ == '__main__':
    main()
