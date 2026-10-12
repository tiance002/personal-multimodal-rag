"""One real ordinary answer, same public fixture, durable no-repost receipt."""
import json
from pathlib import Path
import urllib.request
import uuid

from smoke_normal_upload import BASE, KB, REPORT as UPLOAD_REPORT


REPORT = Path(__file__).resolve().parents[1] / 'var/reports/no-candidates-20261011/normal-answer-real.json'
QUESTION = '北辰测试项目的负责人是谁，验收日期是什么时候？'


def call(path, body=None, *, timeout=45):
    request = urllib.request.Request(BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)['data']


def main():
    report = json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else dict(schema_version=1,
        entry=BASE, kb_scope=[KB], question=QUESTION, fixture_kind='PUBLIC_SYNTHETIC', status='PENDING')
    document = json.loads(UPLOAD_REPORT.read_text(encoding='utf-8'))['receipt']['document_id']
    if 'conversation' not in report:
        report['conversation'] = call('/conversations', dict(knowledge_base_scope=[KB],
            document_scope=[document], title='北辰公开样本真实验收'))
        report['request_id'] = str(uuid.uuid4())
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    conversation = report['conversation']['id']
    if not report.get('post_dispatched'):
        report['post_dispatched'] = True
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        report['answer'] = call(f'/conversations/{conversation}/messages', dict(content=QUESTION,
            mode='quick', request_id=report['request_id'], expected_knowledge_base_scope=[KB],
            expected_document_scope=[document]))
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if 'answer' not in report:
        raise RuntimeError('POST_OUTCOME_UNKNOWN_READ_ONLY_RECOVERY_REQUIRED')
    answer = report['answer']
    report['history'] = call(f'/conversations/{conversation}/messages')
    report['citations'] = [call(f"/runs/{answer['run_id']}/citations/{label}") for label in answer['citations']]
    report['status'] = 'CHECKS_PASSED' if (not answer.get('error_code') and answer['citations']
        and all(word in answer['answer'] for word in ('陈澄','2026','11','5'))) else 'FAIL'
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=report['status'], run_id=answer['run_id'], answer=answer['answer'],
        error_code=answer.get('error_code'), citation_count=len(report['citations'])),ensure_ascii=False))
    if report['status'] != 'CHECKS_PASSED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
