"""One real retry of the owner's exact question/scope, plus diagnostic UI proof."""
import json
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from smoke_normal_answer import call

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'var/reports/no-candidates-20261011/study-question-real-20261012.json'
CONVERSATION='313909f3-34d9-4f26-8778-3d5bd27f2c60'
ORIGINAL_RUN='56f2e64b-fdda-4387-81c2-57a854b54ff4'
QUESTION='我应该怎么学习'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--unit-guard',action='store_true');parser.add_argument('--guarded-connection',action='store_true');args=parser.parse_args()
    global REPORT
    if args.unit_guard:REPORT=REPORT.with_name('study-question-unit-guard-real-20261012.json')
    if args.guarded_connection:
        assert args.unit_guard
        REPORT=REPORT.with_name('study-question-guarded-connection-real-20261012.json')
    if REPORT.exists():raise RuntimeError('EXISTING_DISPATCH_RECEIPT_READ_ONLY_RECOVERY_REQUIRED')
    c=next(c for c in call('/conversations') if c['id']==CONVERSATION)
    history=call('/conversations/'+CONVERSATION+'/messages')
    original=next(m for m in history if m.get('run_id')==ORIGINAL_RUN)
    assert original['content']==QUESTION and original['presentation']['reason_code']=='MODEL_OUTPUT_TRUNCATED'
    report=dict(status='PENDING',conversation=c,original=original,posts=0,post_dispatched=False)
    def save():REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        try:
            page=browser.new_page();page.goto('http://127.0.0.1:14188/');page.wait_for_load_state('networkidle')
            page.get_by_role('button',name=c['title'],exact=True).click();page.wait_for_load_state('networkidle')
            expect(page.locator('article.user .message-body').first).to_have_text(QUESTION)
            diagnostic=page.locator('article.assistant.diagnostic[data-run-id="'+ORIGINAL_RUN+'"]')
            expect(diagnostic).to_contain_text(original['presentation']['text'])
            assert not page.locator('article.user .clarification-prompt').count()
            page.locator('.chat-page').screenshot(path=str(REPORT.with_suffix('.before.png')))
            page.get_by_label('输入问题').fill(QUESTION)
            def posted(request):
                if '/conversations/'+CONVERSATION+'/messages' in request.url and request.method=='POST':
                    report['posts']+=1;report['wire_request']=request.post_data_json;save()
            page.on('request',posted);report['post_dispatched']=True;save()
            with page.expect_response(lambda r:'/conversations/'+CONVERSATION+'/messages' in r.url and r.request.method=='POST',timeout=250000) as pending:
                page.get_by_role('button',name='发送问题',exact=True).click()
            answer=pending.value.json()['data'];report['answer']=answer;save()
            assert report['wire_request']['content']==QUESTION
            assert report['wire_request']['expected_document_scope']==c['document_scope']
            assert not answer.get('error_code') and answer['citations'],answer.get('error_code')
            if args.unit_guard:
                from backend.app.application.final_answer_commit import unsupported_duration_list
                from types import SimpleNamespace
                assert not unsupported_duration_list(answer['answer'],{label:SimpleNamespace(quote=call('/runs/'+answer['run_id']+'/citations/'+label)['quote']) for label in answer['citations']})
            citations=[call('/runs/'+answer['run_id']+'/citations/'+label) for label in answer['citations']]
            report['citations']=citations;save()
            version=call('/documents/'+c['document_scope'][0])['active_version_id']
            assert all(x['version_id']==version and x['quote'].strip() for x in citations)
            expect(page.locator('article.assistant:not(.diagnostic)')).to_have_count(2 if args.unit_guard else 1)
            with page.expect_response(lambda r:'/runs/'+answer['run_id']+'/citations/' in r.url) as clicked:
                page.locator('article.assistant:not(.diagnostic)').last.locator('.citation-pill').first.click()
            assert clicked.value.status==200
            page.reload();page.wait_for_load_state('networkidle');page.get_by_role('button',name=c['title'],exact=True).click()
            expect(page.locator('article.assistant:not(.diagnostic)')).to_have_count(2 if args.unit_guard else 1)
            expect(page.locator('article.assistant.diagnostic[data-run-id="'+ORIGINAL_RUN+'"]')).to_be_visible()
            assert not page.locator('article.user .clarification-prompt').count()
            report['history']=call('/conversations/'+CONVERSATION+'/messages')
            assert any(m['role']=='assistant' and m['run_id']==answer['run_id'] and m['content']==answer['answer'] for m in report['history'])
            assert report['posts']==1
            page.locator('.chat-page').screenshot(path=str(REPORT.with_suffix('.after.png')))
            report.update(status='CHECKS_PASSED',citation_http_status=200,reload_same_terminal=True,user_bubble_unchanged=True);save()
            print(json.dumps({'status':report['status'],'run_id':answer['run_id'],'posts':1,'citation_count':len(citations)}))
        finally:save();browser.close()

if __name__=='__main__':main()
