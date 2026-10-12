"""Real ordinary unconsented KB: index/query fail closed and zero sends."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid
import sys

from smoke_normal_answer import call
from smoke_normal_upload import BASE, CONTENT

REPORT = Path(__file__).resolve().parents[1] / 'var/reports/no-candidates-20261011/normal-privacy-real.json'


def calls():
    sql = "SELECT count(*) FROM service_token_reservations WHERE policy_id IN ('owner-20261011-bge-m3-normal-v1','owner-20261011-xing-normal-v1','owner-20261011-deepseek-normal-v1');"
    result = subprocess.run(['docker','exec','raglocalfirst0930-db-1','psql','-U','rag','-d','rag',
        '-XAt','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True)
    assert result.returncode == 0
    return int(result.stdout)


def main():
    if REPORT.exists():
        raise RuntimeError('PRIVACY_RECEIPT_ALREADY_EXISTS_NO_DUPLICATE_EXECUTION')
    report = dict(schema_version=1, status='PENDING', before_prepared_requests=calls())
    def save():
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report['kb'] = call('/knowledge-bases',dict(name='公开样本未许可外发验证-20261011'))
    assert report['kb']['cloud_allowed'] is False
    kb = report['kb']['id']
    save()
    boundary = 'codex-' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="unconsented-public.md"\r\nContent-Type: text/markdown\r\n\r\n'.encode()
        + CONTENT + f'\r\n--{boundary}--\r\n'.encode())
    report['upload_dispatched'] = True
    save()
    request = urllib.request.Request(BASE+f'/knowledge-bases/{kb}/documents',data=body,
        headers={'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
    with urllib.request.urlopen(request,timeout=15) as response:
        report['receipt'] = json.load(response)['data']
    save()
    for _ in range(30):
        report['job'] = call('/ingestion-jobs/'+report['receipt']['job_id'])
        if report['job']['status'] in ('failed','succeeded'):
            break
        time.sleep(.5)
    assert report['job']['status']=='failed' and report['job']['error_code']=='MODEL_ROLE_EGRESS_DENIED'
    report['document'] = call('/documents/'+report['receipt']['document_id'])
    assert report['document']['active_version_id'] is None
    report['conversation'] = call('/conversations',dict(knowledge_base_scope=[kb],document_scope=[],title='未许可外发公开验证'))
    report['request_id'] = str(uuid.uuid4())
    report['question_dispatched'] = True
    save()
    report['answer'] = call('/conversations/'+report['conversation']['id']+'/messages',dict(
        content='北辰测试项目的负责人是谁？',mode='quick',request_id=report['request_id'],
        expected_knowledge_base_scope=[kb],expected_document_scope=[]))
    save()
    report['after_prepared_requests'] = calls()
    save()
    assert report['before_prepared_requests']==report['after_prepared_requests']
    assert report['answer']['error_code'] and not report['answer']['citations']
    assert report['answer']['presentation']['category']=='permission_required'
    assert report['answer']['presentation']['reason_code']=='CLOUD_EGRESS_DISABLED'
    history = call('/conversations/'+report['conversation']['id']+'/messages')
    row = next(message for message in history if message.get('run_id')==report['request_id'])
    assert row['role']=='user' and row['presentation']==report['answer']['presentation']
    report.update(status='CHECKS_PASSED',provider_calls=0,post_get_same_presentation=True)
    save()
    print(json.dumps(dict(status=report['status'],kb_id=kb,job_error=report['job']['error_code'],
        provider_calls=0,post_get_same_presentation=True)))


def verify_fixed_presentation():
    """Reuse the failed ingest; a fresh denied run, not a repeated model send."""
    original=json.loads(REPORT.read_text(encoding='utf-8'))
    destination=REPORT.with_name('normal-privacy-real-r2.json')
    if destination.exists():
        raise RuntimeError('EXISTING_REPAIR_RECEIPT_DO_NOT_REPEAT')
    kb=original['kb']['id']
    actual=next(row for row in call('/knowledge-bases') if row['id']==kb)
    assert actual['cloud_allowed'] is False
    document=call('/documents/'+original['receipt']['document_id'])
    assert document['active_version_id'] is None
    run_id=str(uuid.uuid4())
    report=dict(status='PENDING',original_receipt=REPORT.name,original_run_id=original['request_id'],
                original_post_not_captured=True,request_id=run_id,kb_id=kb,
                before_prepared_requests=calls(),question_dispatched=True)
    def save():
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save()
    report['answer']=call('/conversations/'+original['conversation']['id']+'/messages',dict(
        content='北辰测试项目的负责人是谁？',mode='quick',request_id=run_id,
        expected_knowledge_base_scope=[kb],expected_document_scope=[]))
    save()
    report['after_prepared_requests']=calls()
    assert report['before_prepared_requests']==report['after_prepared_requests']
    presentation=report['answer']['presentation']
    assert presentation['category']=='permission_required' and presentation['reason_code']=='CLOUD_EGRESS_DISABLED'
    history=call('/conversations/'+original['conversation']['id']+'/messages')
    row=next(message for message in history if message.get('run_id')==run_id)
    assert row['role']=='user' and row['presentation']==presentation and not row['citations']
    report.update(status='CHECKS_PASSED',provider_calls=0,post_get_same_presentation=True)
    save()
    print(json.dumps(dict(status=report['status'],provider_calls=0,post_get_same_presentation=True,
                         kb_id=kb,request_id=run_id)))


if __name__=='__main__':
    verify_fixed_presentation() if sys.argv[1:]==['--resume-presentation'] else main()
