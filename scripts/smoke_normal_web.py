"""Actual browser: one POST, real generation, citation click, reload/history."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from smoke_normal_answer import REPORT as ANSWER_REPORT, QUESTION, call

REPORTS = Path(__file__).resolve().parents[1] / 'var/reports/no-candidates-20261011'
REPORT = REPORTS / 'normal-web-real.json'


def main():
    if REPORT.exists():
        raise RuntimeError('EXISTING_BROWSER_DISPATCH_RECEIPT_NO_DUPLICATE_EXECUTION')
    prior = json.loads(ANSWER_REPORT.read_text(encoding='utf-8'))
    conversation_id = prior['conversation']['id']
    title = next(row['title'] for row in call('/conversations') if row['id'] == conversation_id)
    report = dict(schema_version=1, entry='http://127.0.0.1:14188/',
        conversation_id=conversation_id, fixture_kind='PUBLIC_SYNTHETIC', status='PENDING', posts=0)
    def save():
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        try:
            page = browser.new_page()
            page.goto(report['entry'])
            page.wait_for_load_state('networkidle')
            page.get_by_role('button',name=title,exact=True).click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('article.assistant')).to_have_count(1)
            page.get_by_label('输入问题').fill(QUESTION)
            report['post_dispatched'] = True
            save()
            def is_post(response):
                return (f'/conversations/{conversation_id}/messages' in response.url
                        and response.request.method == 'POST')
            page.on('request', lambda request: report.update(posts=report['posts']+1)
                if f'/conversations/{conversation_id}/messages' in request.url and request.method == 'POST' else None)
            with page.expect_response(is_post, timeout=60_000) as pending:
                page.get_by_role('button',name='发送问题',exact=True).click()
            response = pending.value
            report['post_http_status'] = response.status
            report['answer'] = response.json()['data']
            save()
            answer = report['answer']
            assert not answer.get('error_code') and answer['citations']
            expect(page.locator('article.assistant')).to_have_count(2)
            expect(page.locator('article.assistant').last).to_contain_text('陈澄')
            expect(page.locator('article.assistant').last).to_contain_text('2026年11月5日')
            with page.expect_response(lambda r: f"/runs/{answer['run_id']}/citations/" in r.url) as citation:
                page.locator('article.assistant').last.locator('.citation-pill').first.click()
            report['citation_http_status'] = citation.value.status
            report['citation'] = citation.value.json()['data']
            assert citation.value.status == 200 and '陈澄' in report['citation']['quote']
            page.reload()
            page.wait_for_load_state('networkidle')
            page.get_by_role('button',name=title,exact=True).click()
            page.wait_for_load_state('networkidle')
            expect(page.locator('article.assistant')).to_have_count(2)
            expect(page.locator('article.assistant').last).to_contain_text('陈澄')
            assert report['posts'] == 1
            page.locator('.chat-page').screenshot(path=str(REPORTS / 'normal-web-real.png'))
            report.update(status='CHECKS_PASSED', reload_same_terminal=True, citation_readable=True)
            save()
            print(json.dumps(dict(status=report['status'],run_id=answer['run_id'],posts=report['posts'],
                citation_http_status=report['citation_http_status'],reload_same_terminal=True)))
        finally:
            save()
            browser.close()


if __name__ == '__main__':
    main()
