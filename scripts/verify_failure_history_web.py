"""Read committed failed Runs in the real UI twice; never send another question."""
import json
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'var/reports/no-candidates-20261011'
BASE='http://127.0.0.1:14188'
CASES=[('hello','7eaa3fa1-cb4c-4fcf-ba9c-3b15f821068d','generation_error'),
       ('你好','4136783d-5e23-4c41-a12e-c78b1c430dc5','generation_error'),
       ('未许可外发公开验证','caf03196-21e3-4372-836e-ea248dbc64b3','permission_required')]


def main():
    report=dict(status='PENDING',provider_calls=0,cases=[])
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1400,'height':950})
        mutations=[]
        page.on('request',lambda request:mutations.append(request.method) if request.method in {'POST','PUT','PATCH','DELETE'} else None)
        for title,conversation_id,category in CASES:
            with urlopen(BASE+'/api/v1/conversations/'+conversation_id+'/messages',timeout=10) as response:
                messages=json.load(response)['data']
            failed=[message for message in messages if message.get('presentation',{}).get('kind')=='run_diagnostic']
            expected=failed[-1]
            assert expected['role']=='user' and not expected['citations'] and expected['presentation']['category']==category
            for cycle in range(2):
                page.goto(BASE)
                page.get_by_role('button',name=title,exact=True).click()
                expect(page.locator('article.user .clarification-prompt').last).to_have_text(expected['presentation']['text'])
            report['cases'].append(dict(conversation_id=conversation_id,run_id=expected['run_id'],
                                      category=category,refresh_same=True))
        assert not mutations
        browser.close()
    report.update(status='CHECKS_PASSED',mutating_requests=0)
    (OUT/'failure-history-real-ui-20261012.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    main()
