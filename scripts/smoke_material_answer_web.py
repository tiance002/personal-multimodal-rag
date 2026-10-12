"""One bounded real material QA; earlier failed attempts are preserved."""
import json
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from smoke_normal_answer import call

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'var/reports/no-candidates-20261011/material-answer-web-real-20261012.json'
KB='d78f3434-0e8f-40bf-afcb-f1c2c311ff51'
DOC='1fd5a928-d3d7-4bae-aa00-fc74701f61e5'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--kb-wide',action='store_true');parser.add_argument('--core-only',action='store_true');args=parser.parse_args()
    global REPORT
    if args.kb_wide:REPORT=REPORT.with_name('material-answer-kb-wide-web-real-20261012.json')
    if args.core_only:REPORT=REPORT.with_name('material-answer-core-web-real-20261012.json')
    if REPORT.exists():raise RuntimeError('EXISTING_RECEIPT_NO_REPOST')
    assert '间隔时间为1，7，16，35' in call('/documents/'+DOC+'/content')['content']
    title='学习资料限定范围真实验收1012'
    if args.kb_wide:title='学习资料全库范围真实验收1012'
    if args.core_only:title='原任务资料问答正式入口验收1012'
    c=call('/conversations',dict(knowledge_base_scope=[KB],document_scope=[] if args.kb_wide else [DOC],title=title))
    report=dict(status='PENDING',conversation=c,posts=0,post_dispatched=False)
    def save():REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        try:
            page=browser.new_page();page.goto('http://127.0.0.1:14188/');page.wait_for_load_state('networkidle')
            page.get_by_role('button',name=title,exact=True).click();page.wait_for_load_state('networkidle')
            question='请按原文列出资料中分散学习的复习间隔数值，不补充原文未注明的单位。'
            report['question']=question;page.get_by_label('输入问题').fill(question)
            def posted(request):
                if '/conversations/'+c['id']+'/messages' in request.url and request.method=='POST':
                    report['posts']+=1;report['wire_request']=request.post_data_json;save()
            page.on('request',posted);report['post_dispatched']=True;save()
            with page.expect_response(lambda r:'/conversations/'+c['id']+'/messages' in r.url and r.request.method=='POST',timeout=250000) as pending:
                page.get_by_role('button',name='发送问题',exact=True).click()
            answer=pending.value.json()['data'];report['answer']=answer;save()
            if args.core_only:
                assert answer['trace'].get('input_route_status','NOT_RUN')=='NOT_RUN'
                assert all(row['stage']!='input_route' for row in answer['trace']['metrics']['model_calls'])
            assert not answer.get('error_code') and answer['citations'],answer.get('error_code')
            for word in ('1','7','16','35'):assert word in answer['answer']
            citations=[call('/runs/'+answer['run_id']+'/citations/'+label) for label in answer['citations']]
            report['citations']=citations
            version=call('/documents/'+DOC)['active_version_id']
            assert all(row['version_id']==version for row in citations)
            assert any('1，7，16，35' in row['quote'] for row in citations)
            expect(page.locator('article.assistant')).to_have_count(1)
            with page.expect_response(lambda r:'/runs/'+answer['run_id']+'/citations/' in r.url) as clicked:
                page.locator('article.assistant').last.locator('.citation-pill').first.click()
            assert clicked.value.status==200
            page.reload();page.wait_for_load_state('networkidle');page.get_by_role('button',name=title,exact=True).click()
            expect(page.locator('article.assistant')).to_have_count(1)
            history=call('/conversations/'+c['id']+'/messages');report['history']=history
            assert any(m['role']=='assistant' and m['run_id']==answer['run_id'] and m['content']==answer['answer'] and m['citations']==answer['citations'] for m in history)
            assert report['posts']==1
            report.update(status='CHECKS_PASSED',citation_http_status=200,reload_same_terminal=True);save()
            print(json.dumps({'status':report['status'],'run_id':answer['run_id'],'posts':1,'citation_count':len(citations)}))
        finally:save();browser.close()

if __name__=='__main__':main()
