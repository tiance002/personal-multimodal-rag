import { expect, test, type Page } from '@playwright/test';
const envelope = (data: unknown) => ({ data, meta: { request_id: 'ui-r3-fixture' } });
const reference = { id: 'chunk-original-1', knowledge_id: 'doc-1', knowledge_base_id: 'kb-1', knowledge_filename: '原文依据.pdf' };
async function setup(page: Page, refs: unknown[] = [reference]) {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = [];
    if (path.endsWith('/runtime')) data = { implementation: 'weknora-python-port', api_version: 'v1' };
    else if (path.endsWith('/knowledge-bases')) data = [{ id: 'kb-1', name: '范围内资料', cloud_allowed: false, document_count: 1 }];
    else if (path.endsWith('/documents')) data = [{ id: 'doc-1', knowledge_base_id: 'kb-1', filename: '原文依据.pdf', status: 'ready' }];
    else if (path.endsWith('/sessions')) data = [{ id: 'r3-history', title: '引用回读测试', knowledge_base_scope: ['kb-1'], document_scope: ['doc-1'], updated_at: 'now' }];
    else if (path.endsWith('/messages')) data = [{ id: 'answer-1', role: 'assistant', content: '这是有原文支持的结论。', request_id: 'saved-run', created_at: 'now', is_completed: true, turn_status: 'completed', knowledge_references: refs }];
    else if (path.endsWith('/model-configuration')) data = { revision: 'fixture', roles: ['embedding','rerank','cheap','expensive','vlm'].map(role => ({ role, provider: 'fixture', model: role, base_url: 'https://example.invalid', key_configured: true })) };
    else if (path.endsWith('/generation-settings')) data = { saved: {}, effective: { reasoning_mode: 'quick', max_output_tokens: 2048 }, defaults: { reasoning_mode: 'quick', max_output_tokens: 2048 }, capabilities: { thinking: 'supported', operating_max_output_tokens: 2048, certified_max_output_tokens: 8192 } };
    return route.fulfill({ json: envelope(data) });
  });
  await page.goto('/');
}
async function history(page: Page) { await page.getByRole('button', {name: /引用回读测试/}).first().click(); }
const source = {chunk_id:'chunk-original-1', document_id:'doc-1', knowledge_base_id:'kb-1', filename:'原文依据.pdf', text:'原文：这一条件只适用于已指定的对象。', source_version:'version-1', source_url:'/safe-original', page_number:3};
for (const mode of ['valid', 'denied', 'identity-mismatch'] as const) {
  test(`structured history citation readback ${mode}`, async ({page}) => {
    await setup(page);
    let reads=0;
    await page.route('**/api/v1/chunks/chunk-original-1', route => {
      reads++;
      if(mode==='denied') return route.fulfill({status:403,json:{error:{message:'当前来源访问被拒绝'}}});
      return route.fulfill({json:envelope(mode==='identity-mismatch'?{...source,knowledge_base_id:'other-kb',text:'不得展示的跨库文本'}:source)});
    });
    await history(page);
    await page.locator('.wp-citation').click();
    if(mode==='valid') {
      await expect(page.getByText(source.text,{exact:true})).toBeVisible();
      await expect(page.getByRole('link',{name:'打开原始文件'})).toHaveAttribute('href',/knowledge-bases\/kb-1\/documents\/doc-1\/source$/);
    } else {
      await expect(page.getByText('暂时无法读取引用',{exact:true})).toBeVisible();
      await expect(page.getByText('不得展示的跨库文本',{exact:true})).toHaveCount(0);
      await expect(page.getByRole('link',{name:'打开原始文件'})).toHaveCount(0);
    }
    expect(reads).toBe(1);
  });
}
test('private handles and incomplete source identity do not become citations', async ({page}) => {
  await setup(page,[{id:'c1',knowledge_id:'d1',knowledge_base_id:'kb-1'},{id:'untrusted-no-document'}, {id:'https://example.invalid',knowledge_id:'doc-1',knowledge_base_id:'kb-1'}]);
  await history(page); await expect(page.locator('.wp-citation')).toHaveCount(0);
});
test('narrow layout can restore a session and close navigation without horizontal overflow', async ({page})=>{
  await page.setViewportSize({width:390,height:844}); await setup(page);
  await page.getByRole('button',{name:'打开导航'}).click(); await history(page);
  await expect(page.getByText('这是有原文支持的结论。',{exact:true})).toBeVisible();
  await expect(page.getByRole('dialog',{name:'导航与历史会话'})).toHaveCount(0);
  const width=await page.evaluate(()=>({body:document.documentElement.scrollWidth,view:innerWidth})); expect(width.body).toBeLessThanOrEqual(width.view);
});

test('live structured references reach readback without a second question POST', async ({page}) => {
  await setup(page, []);
  let posts=0;
  const frame=(data: unknown)=>`event: message\ndata: ${JSON.stringify(data)}\n\n`;
  await page.route('**/api/v1/sessions/r3-history/messages', route=>{
    if(route.request().method()==='GET') return route.fulfill({json:envelope([])});
    posts++;
    const body=frame({id:'new-run',response_type:'agent_query',content:'',done:false,data:{request_id:'new-run',session_id:'r3-history',assistant_message_id:'new-answer'}})
      +frame({id:'new-run',response_type:'references',content:'',done:false,knowledge_references:[reference],data:{references:[reference]}})
      +frame({id:'new-run',response_type:'answer',content:'这次回答有可回读的原文。',done:true,data:{event_id:'new-answer-event'}})
      +frame({id:'new-run',response_type:'complete',content:'',done:false,data:{finish_reason:'stop',final_content:'这次回答有可回读的原文。'}});
    return route.fulfill({status:200,contentType:'text/event-stream',body});
  });
  await page.route('**/api/v1/chunks/chunk-original-1',route=>route.fulfill({json:envelope(source)}));
  await history(page);
  await page.getByPlaceholder('询问当前资料…').fill('这次请核对来源');
  await page.getByRole('button',{name:'发送',exact:true}).click();
  await expect(page.getByText('这次回答有可回读的原文。',{exact:true})).toBeVisible();
  await page.locator('.wp-citation').click(); await expect(page.getByText(source.text,{exact:true})).toBeVisible();
  expect(posts).toBe(1);
});

test('structured references outside the persisted message scope stay unavailable', async ({page})=>{
  await setup(page);
  let reads=0;
  await page.route('**/api/v1/sessions/r3-history/messages',route=>route.fulfill({json:envelope([{id:'scoped-answer',role:'assistant',content:'保留原回答，但不提供范围外引用。',request_id:'old-run',created_at:'now',is_completed:true,turn_status:'completed',knowledge_base_scope:['kb-1'],document_scope:['doc-1'],knowledge_references:[{...reference,knowledge_id:'other-doc'}]}])}));
  await page.route('**/api/v1/chunks/**',route=>{reads++;return route.fulfill({json:envelope(source)});});
  await history(page);await expect(page.getByText('保留原回答，但不提供范围外引用。',{exact:true})).toBeVisible();
  await expect(page.locator('.wp-citation')).toHaveCount(0);expect(reads).toBe(0);
});
