"""Minimal mocked-API product selection flow; run through with_server.py."""
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


ARTIFACTS=Path('E:/RAG quention/var/reports/s8-6-4')


def main():
    session={'id':'scope-session','title':'范围选择会话','knowledge_base_scope':['kb'],
             'document_scope':[],'updated_at':'now'}
    patches=[]
    posts=[]
    errors=[]
    history=[{'id':'clarify','role':'assistant','content':'当前知识库中有多份文档，你想查询哪一份？\n\n- HJ 1134—2020 / a.pdf（Document ID: doc-a）\n- HJ 348—2007 / b.pdf（Document ID: doc-b）',
              'request_id':'clarification','is_completed':True,'turn_status':'completed'}]
    def envelope(value):return {'data':value,'meta':{'request_id':'mock-request'}}
    def handle(route):
        path=route.request.url.split('/api/v1',1)[1]
        if path=='/runtime':value={'implementation':'weknora-python-port'}
        elif path=='/knowledge-bases':value=[{'id':'kb','name':'技术规范','document_count':2,'cloud_allowed':False}]
        elif path=='/knowledge-bases/kb/documents':value=[{'id':identifier,'knowledge_base_id':'kb','filename':filename,'status':'ready'} for identifier,filename in [('doc-a','a.pdf'),('doc-b','b.pdf')]]
        elif path=='/settings/model-configuration':value={'revision':'1','roles':[{'role':role,'model':role,'provider':'local','base_url':'http://127.0.0.1:9999/v1','key_configured':False} for role in ['embedding','rerank','cheap','expensive','vlm']]}
        elif path=='/sessions':value=[session]
        elif path=='/sessions/scope-session/document-scope':
            assert route.request.method=='PATCH'
            payload=route.request.post_data_json
            assert set(payload)=={'document_scope'}
            session['document_scope']=payload['document_scope']
            patches.append(payload)
            value=session
        elif path=='/sessions/scope-session/messages':
            if route.request.method=='GET':value=history
            else:
                payload=route.request.post_data_json
                posts.append(payload)
                run='run-'+str(len(posts))
                answer='回答'+str(len(posts))
                history.extend([{'id':'user-'+run,'role':'user','content':payload['content'],'is_completed':True},
                                {'id':'answer-'+run,'role':'assistant','content':answer,'is_completed':True,'turn_status':'completed'}])
                events=[{'id':run,'response_type':'agent_query','content':'','done':False,'data':{'request_id':run,'assistant_message_id':'answer-'+run,'session_id':session['id']}},
                        {'id':run,'response_type':'answer','content':answer,'done':True,'data':{'event_id':'event-'+run}},
                        {'id':run,'response_type':'complete','content':'','done':False,'data':{'status':'completed','final_content':answer}}]
                route.fulfill(status=200,content_type='text/event-stream',body=''.join('event: message\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n' for event in events))
                return
        else:raise AssertionError('Unexpected mock API request: '+route.request.method+' '+path)
        route.fulfill(json=envelope(value))
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/api/v1/**',handle)
            page.goto('http://127.0.0.1:5196')
            page.wait_for_load_state('networkidle')
            page.screenshot(path=str(ARTIFACTS/'scope-ui-initial.png'),full_page=True)
            page.get_by_role('button',name='范围选择会话').click()
            expect(page.get_by_text('当前知识库中有多份文档，你想查询哪一份？',exact=False)).to_be_visible()
            trigger=page.get_by_role('button',name='选择知识库和文件',exact=True)
            dialog=page.get_by_role('dialog',name='选择知识库和文件',exact=True)
            trigger.click()
            expect(dialog.get_by_role('checkbox',name='技术规范')).to_be_disabled()
            expect(dialog.get_by_role('checkbox',name='a.pdf')).to_be_enabled()
            dialog.get_by_role('checkbox',name='a.pdf').check()
            page.get_by_placeholder('询问当前资料…').fill('这份规范的实施日期是什么？')
            expect(page.get_by_role('button',name='发送')).to_be_disabled()
            dialog.get_by_role('button',name='应用文档范围').click()
            expect(dialog).to_have_count(0)
            expect(page.get_by_role('button',name='发送')).to_be_enabled()
            page.get_by_role('button',name='发送').click()
            expect(page.get_by_text('回答1',exact=True)).to_be_visible()
            expect(page.get_by_role('button',name='停止生成')).to_have_count(0)
            assert posts[-1]['document_scope']==['doc-a']
            trigger.click()
            dialog.get_by_role('checkbox',name='a.pdf').uncheck()
            dialog.get_by_role('checkbox',name='b.pdf').check()
            dialog.get_by_role('button',name='应用文档范围').click()
            page.get_by_placeholder('询问当前资料…').fill('现在这份规范的日期？')
            page.get_by_role('button',name='发送').click()
            expect(page.get_by_text('回答2',exact=True)).to_be_visible()
            assert posts[-1]['document_scope']==['doc-b']
            page.reload()
            page.wait_for_load_state('networkidle')
            page.get_by_role('button',name='范围选择会话').click()
            trigger.click()
            expect(dialog.get_by_role('checkbox',name='b.pdf')).to_be_checked()
            expect(dialog.get_by_role('checkbox',name='a.pdf')).not_to_be_checked()
            page.screenshot(path=str(ARTIFACTS/'scope-ui-selected.png'),full_page=True)
            dialog.get_by_role('button',name='取消文档限定').click()
            expect(dialog).to_have_count(0)
            assert patches[-1]=={'document_scope':[]}
            page.reload()
            page.wait_for_load_state('networkidle')
            page.get_by_role('button',name='范围选择会话').click()
            trigger.click()
            expect(dialog.get_by_role('checkbox',name='a.pdf')).not_to_be_checked()
            expect(dialog.get_by_role('checkbox',name='b.pdf')).not_to_be_checked()
            page.screenshot(path=str(ARTIFACTS/'scope-ui-cleared.png'),full_page=True)
            assert not errors,errors
        finally:browser.close()
    report={'status':'PASS','mock_api':True,'patches':patches,'post_document_scopes':[post['document_scope'] for post in posts],
            'reload_preserved_selection':True,'reload_preserved_clear':True,'page_errors':errors,'actual_provider_calls':0}
    (ARTIFACTS/'scope-ui.results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
