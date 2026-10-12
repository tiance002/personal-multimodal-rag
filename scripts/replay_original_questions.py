"""Original q0, same server-confirmed conversations/Scope; one fresh POST each."""
import json
import argparse
from pathlib import Path
import uuid

from smoke_normal_answer import call
from smoke_normal_upload import KB

REPORT = Path(__file__).resolve().parents[1] / 'var/reports/no-candidates-20261011/original-question-replay-real.json'
CASES = (
    ('hello','7eaa3fa1-cb4c-4fcf-ba9c-3b15f821068d','d04fed31-22d1-4285-8e1f-9fb97cfdb965'),
    ('你好','4136783d-5e23-4c41-a12e-c78b1c430dc5','eb3b6fc5-b743-4274-991e-281657de3da1'),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', type=int, choices=(1, 2), default=1)
    parser.add_argument('--diagnostic-only', action='store_true', help='One new hello Run after fixed transport diagnostics; no retries')
    parser.add_argument('--after-tls-fix', action='store_true', help='Both original cases after evidence-backed normal TLS change; no retries')
    parser.add_argument('--after-timeout-fix', action='store_true', help='Only original 你好 after bounded normal timeout change; no retries')
    parser.add_argument('--verify-greeting-presentation',action='store_true',help='Original q0/Scope non-factual clarification; zero model dispatch expected')
    options = parser.parse_args()
    report_path = REPORT if options.attempt == 1 else REPORT.with_name('original-question-replay-real-r2.json')
    if options.diagnostic_only:
        report_path=REPORT.with_name('original-hello-diagnostic-20261012.json')
    if options.after_tls_fix:
        if options.diagnostic_only:
            parser.error('Choose only one controlled replay purpose')
        report_path=REPORT.with_name('original-questions-after-tls-fix-20261012.json')
    if options.after_timeout_fix:
        if options.diagnostic_only or options.after_tls_fix:
            parser.error('Choose only one controlled replay purpose')
        report_path=REPORT.with_name('original-nihao-after-timeout-fix-20261012.json')
    if options.verify_greeting_presentation:
        if options.diagnostic_only or options.after_tls_fix or options.after_timeout_fix:
            parser.error('Choose only one controlled replay purpose')
        report_path=REPORT.with_name('original-greetings-clarification-20261012.json')
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else dict(
        schema_version=1, entry='http://127.0.0.1:14188/api/v1', status='PENDING', cases={})
    def save():
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    conversations = call('/conversations')
    cases=CASES[:1] if options.diagnostic_only else CASES[1:] if options.after_timeout_fix else CASES
    for question, conversation_id, old_run in cases:
        conversation = next(row for row in conversations if row['id'] == conversation_id)
        assert conversation['knowledge_base_scope'] == [KB] and conversation['document_scope'] == []
        row = report['cases'].setdefault(old_run,dict(question=question,conversation_id=conversation_id,
            kb_scope=[KB],document_scope=[],request_id=str(uuid.uuid4()),post_dispatched=False))
        save()
        if not row['post_dispatched']:
            row['post_dispatched'] = True
            save()
            row['answer'] = call(f'/conversations/{conversation_id}/messages',dict(content=question,
                mode='quick',request_id=row['request_id'],expected_knowledge_base_scope=[KB],expected_document_scope=[]),
                timeout=210 if options.after_timeout_fix else 45)
            save()
        if 'answer' not in row:
            raise RuntimeError('POST_OUTCOME_UNKNOWN_READ_ONLY_RECOVERY_REQUIRED')
        row['history'] = [message for message in call(f'/conversations/{conversation_id}/messages')
            if message.get('run_id') in (old_run,row['request_id'])]
        answer = row['answer']
        row['citations'] = [call(f"/runs/{answer['run_id']}/citations/{label}") for label in answer['citations']]
        save()
        print(json.dumps(dict(original_run_id=old_run,question=question,new_run_id=answer['run_id'],
            error_code=answer.get('error_code'),citation_count=len(row['citations']),same_scope=True)),flush=True)
    report['status'] = 'REPLAY_COMPLETED_REQUIRES_SEMANTIC_REVIEW'
    save()


if __name__ == '__main__':
    main()
