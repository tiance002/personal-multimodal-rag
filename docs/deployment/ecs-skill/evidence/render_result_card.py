"""Render an offline Darwin-style result card with no remote assets."""
from pathlib import Path
import hashlib,json,random,re
from playwright.sync_api import sync_playwright
R=Path(__file__).resolve().parent
template=Path(r'C:\Users\22088\.codex\skills\darwin-skill\templates\result-card.html')
# Reuse the installed card's typography/layout CSS, not its fictional scores or claims.
raw=template.read_text(encoding='utf-8')
css=re.search(r'<style>(.*?)</style>',raw,re.S).group(1)
theme=random.Random('ecs-skill-20261020').choice(['swiss','terminal','newspaper'])
baseline=json.loads((R/'baseline-triage.json').read_text(encoding='utf-8'))
names=['Frontmatter','工作流','失败分支','检查点','可执行性','资源整合','整体架构','实测表现','反例/黑名单']
bars=''.join(f'<tr><td>{n}</td><td><div class="bar" style="width:{v*10}%"></div></td><td>{v}/10</td></tr>' for n,v in zip(names,baseline['scores']))
body=f'''<div class="card theme-{theme}"><header>Darwin.skill · EviRoute RAG <span>2026-10-20</span></header>
<h1>ECS 增量部署 Skill</h1><p class="subtitle">项目级 · 真实历史复盘 · 离线验证</p>
<div class="numbers"><b>7<small>类历史故障</small></b><b>4<small>模拟场景通过</small></b><b>0<small>ECS / 业务Provider操作</small></b></div>
<section><h2>独立配对评估</h2><p>3名独立评审，比较相同任务的两版输出。候选只增加并发工作树检查。</p><p><strong>不采用候选扩写</strong>：两版四题均通过，未证明处理结果改善；保留新反例与差异。</p></section>
<h2>基线粗排 {baseline['weighted_total']}/100</h2><p class="note">仅作 triage，不表示真实部署收益，不用绝对分差决定保留。</p><table>{bars}</table>
<section><h2>已验证的能力边界</h2><ul><li>原生 Codex skills/list：repo、enabled；新上下文按 AGENTS 读取。</li><li>API-only、旧代码覆盖、故障沉淀与并发保护均为离线模拟。</li><li>真实发布、云端修复及回滚：本轮 NOT RUN。</li></ul></section>
<footer>Train your Skills like you train your models<br>github.com/alchaincyf/darwin-skill</footer></div>'''
html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>ECS Skill 验证</title><style>'+css+'''
body{padding:24px;background:#edf0ee;font-family:"Microsoft YaHei",sans-serif;color:#162d27}.card{width:840px;box-sizing:border-box;padding:38px 46px;background:#fff;border-radius:8px;box-shadow:0 8px 32px #0001}header{font-size:15px;font-weight:700;border-bottom:1px solid #b2c2b9;padding-bottom:16px}header span{float:right;color:#6f8075}h1{font-size:36px;margin:22px 0 4px}.subtitle{color:#61746b}.numbers{display:flex;justify-content:space-between;background:#edf5ef;margin:20px 0;padding:20px 30px}.numbers b{font-size:34px;text-align:center}.numbers small{display:block;font-size:13px;font-weight:400;margin-top:8px}section{margin:20px 0;padding:17px 20px;border:1px solid #dce7e0;background:#f9fbf9}h2{font-size:19px;margin-bottom:9px}p,li{font-size:15px;line-height:1.7}.note{font-size:12px;color:#66796d}table{width:100%;border-collapse:collapse;margin:10px 0}td{font-size:13px;padding:5px 7px}td:nth-child(1){width:95px}td:nth-child(2){width:75%}.bar{height:10px;background:#317d61;border-radius:3px}footer{font-size:11px;color:#819388;text-align:center;border-top:1px solid #cddbd2;padding-top:15px;margin-top:22px}ul{padding-left:20px}
</style>'''+body+'</html>'
(R/'result-card.html').write_text(html,encoding='utf-8')
with sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    try:
        ctx=browser.new_context(viewport={'width':940,'height':1600},device_scale_factor=1.5)
        blocked=[]
        ctx.route('**/*',lambda route:(blocked.append(route.request.url),route.abort()))
        page=ctx.new_page();page.set_content(html,wait_until='load')
        page.locator('.card').screenshot(path=str(R/'result-card.png'))
        box=page.locator('.card').bounding_box()
        out={'status':'PASS','template_path':str(template),'template_sha256':hashlib.sha256(template.read_bytes()).hexdigest(),'theme_seeded':theme,'render_method':'installed PythonPlaywright / existingEdge headless, inlineHTML only','network_requests_blocked':blocked,'new_dependencies':0,'css_card_bounds':box,'png_sha256':hashlib.sha256((R/'result-card.png').read_bytes()).hexdigest(),'scope':'Local static artifact only, no product/browser-session acceptance'}
        (R/'result-card-receipt.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8');print(json.dumps(out))
    finally:browser.close()
