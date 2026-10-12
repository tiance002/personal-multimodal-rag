"""Scope-bounded historical hashes and real failed-candidate activation check."""
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from smoke_normal_answer import call
from smoke_normal_upload import BASE
from upgrade_scoped_versions import DOCS

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'var/reports/no-candidates-20261011'
REPORT=OUT/'version-preservation-real-20261012.json'


def sql(query):
    result=subprocess.run(['docker','exec','raglocalfirst0930-db-1','psql','-U','rag','-d','rag',
        '-XAt','-v','ON_ERROR_STOP=1','-c',"BEGIN READ ONLY; SET LOCAL statement_timeout='3s'; "+query+'; COMMIT;'],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0, 'READ_ONLY_SCOPE_QUERY_FAILED'
    return json.loads(next(line for line in result.stdout.splitlines() if line.startswith('{')))


def main():
    if REPORT.exists():
        raise RuntimeError('EXISTING_VERSION_CHECK_RECEIPT_DO_NOT_REPEAT_UPLOAD')
    upgrades=json.loads((OUT/'scoped-new-versions-real.json').read_text(encoding='utf-8'))
    new_ids=[str(uuid.UUID(row['receipt']['version_id'])) for row in upgrades['documents'].values()]
    docs=','.join("'"+str(uuid.UUID(value))+"'" for value in DOCS)
    versions=','.join("'"+value+"'" for value in new_ids)
    hashes=sql(f"""SELECT json_build_object(
      'old_versions',(SELECT count(*) FROM document_versions WHERE document_id IN ({docs}) AND id NOT IN ({versions})),
      'old_chunks',(SELECT count(*) FROM chunks WHERE document_id IN ({docs}) AND version_id NOT IN ({versions})),
      'source_hash',(SELECT md5(coalesce(string_agg(id::text||':'||document_id::text||':'||source_sha256,',' ORDER BY id::text),'')) FROM document_versions WHERE document_id IN ({docs}) AND id NOT IN ({versions})),
      'chunk_hash',(SELECT md5(coalesce(string_agg(id::text||':'||version_id::text||':'||content,',' ORDER BY id::text),'')) FROM chunks WHERE document_id IN ({docs}) AND version_id NOT IN ({versions})),
      'old_identity_changed',(SELECT count(*) FROM document_versions WHERE document_id IN ({docs}) AND id NOT IN ({versions}) AND index_identity <> 'legacy/v1'))""")
    backup=json.loads((OUT/'migration-restore-isolated.json').read_text(encoding='utf-8'))['before']
    assert hashes['old_versions']==7 and hashes['old_chunks']==47 and hashes['old_identity_changed']==0
    assert hashes['source_hash']==backup['source_hash'] and hashes['chunk_hash']==backup['chunk_hash']
    report=dict(status='PENDING',historical_hashes=hashes,documents={})
    for document_id in DOCS:
        document=call('/documents/'+document_id)
        assert document['active_version_id']==upgrades['documents'][document_id]['receipt']['version_id']
        assert document['index_compatibility']['status']=='COMPATIBLE'
        readings={}
        for kind in ('source','preview'):
            with urlopen(BASE+'/documents/'+document_id+'/'+kind,timeout=20) as response:
                data=response.read(32*1024*1024+1)
                assert len(data)<=32*1024*1024
                readings[kind]=dict(http_status=response.status,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        assert readings['source']['sha256']==upgrades['documents'][document_id]['source_sha256']
        report['documents'][document_id]=readings
    public=json.loads((OUT/'normal-upload-real.json').read_text(encoding='utf-8'))['receipt']
    document_id=public['document_id']
    before=call('/documents/'+document_id)
    assert before['active_version_id']==public['version_id']
    count=sql("SELECT json_build_object('reservations',count(*)) FROM service_token_reservations")
    report.update(public_document_id=document_id,before_active=before['active_version_id'],before_count=count,
                  candidate_upload_dispatched=True)
    def save():
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save()
    boundary='codex-'+uuid.uuid4().hex
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="invalid-utf8.md"\r\nContent-Type: text/markdown\r\n\r\n'.encode()+b'\xff\xfe\xff'+f'\r\n--{boundary}--\r\n'.encode())
    request=Request(BASE+'/documents/'+document_id+'/versions',data=body,
        headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
    with urlopen(request,timeout=15) as response:
        report['candidate_receipt']=json.load(response)['data']
    save()
    for _ in range(30):
        report['job']=call('/ingestion-jobs/'+report['candidate_receipt']['job_id'])
        if report['job']['status'] in ('failed','succeeded'):break
        time.sleep(.5)
    save()
    assert report['job']['status']=='failed' and report['job']['error_code']=='TEXT_UTF8_DECODE_FAILED'
    after=call('/documents/'+document_id)
    assert after['active_version_id']==before['active_version_id'] and after['index_status']=='ready'
    report['after_active']=after['active_version_id']
    report['after_count']=sql("SELECT json_build_object('reservations',count(*)) FROM service_token_reservations")
    assert report['after_count']==count
    report.update(status='CHECKS_PASSED',provider_calls=0,new_failure_did_not_activate=True)
    save()
    print(json.dumps(dict(status=report['status'],historical_versions=7,historical_chunks=47,
                         source_preview_reads=12,provider_calls=0,new_failure_did_not_activate=True)))


if __name__=='__main__':
    main()
