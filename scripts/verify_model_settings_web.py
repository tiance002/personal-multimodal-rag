"""Read-only real UI/config verification. No secrets read and no model calls."""
import json
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'var/reports/no-candidates-20261011/model-settings-real-ui.json'
ENTRY = 'http://127.0.0.1:14188'


def main():
    with urlopen(ENTRY+'/api/v1/settings/model-configuration', timeout=10) as response:
        configured = json.load(response)['data']
    assert len(configured['roles']) == 5
    assert all('api_key' not in row for row in configured['roles'])
    report = dict(status='PENDING', entry=ENTRY, roles=configured['roles'], provider_calls=0,
                  secret_values_read=False, revision=configured['revision'])
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1100})
        mutations = []
        page.on('request', lambda request: mutations.append(request.method) if request.method in {'POST','PUT','PATCH','DELETE'} else None)
        page.goto(ENTRY)
        page.get_by_role('button', name='系统设置', exact=True).click()
        expect(page.locator('.model-role-card')).to_have_count(5)
        expect(page.get_by_role('button', name='保存模型配置', exact=True)).to_be_enabled()
        expect(page.locator('[aria-label="长期记忆管理"]')).to_have_count(0)
        assert all(value == '' for value in page.locator('input[type="password"]').evaluate_all('(items) => items.map(item=>item.value)'))
        page.screenshot(path=str(REPORT.with_name('model-settings-real-ui.png')))
        page.get_by_role('button', name='问答台', exact=True).click()
        page.get_by_role('button', name='选择知识库和文件', exact=True).click()
        expect(page.locator('.scope-option-label').first).to_be_visible()
        geometry = page.locator('.scope-popover').evaluate('''element => ({
            horizontal_overflow:element.scrollWidth > element.clientWidth,
            names:[...element.querySelectorAll('.scope-option-label')].map(label=>({
                name:label.textContent,width:label.getBoundingClientRect().width,
                height:label.getBoundingClientRect().height}))})''')
        assert not geometry['horizontal_overflow'] and all(row['width']>180 for row in geometry['names'])
        page.screenshot(path=str(REPORT.with_name('scope-names-real-ui.png')))
        report.update(status='CHECKS_PASSED', geometry=geometry, mutating_requests=mutations)
        assert not mutations
        browser.close()
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status':report['status'],'roles':5,'names':len(geometry['names']),
                      'provider_calls':0,'mutations':0}))


if __name__ == '__main__':
    main()
