import { expect, test } from '@playwright/test';

const envelope = (data: unknown) => ({ data });
const roles = ['embedding','rerank','cheap','expensive','vlm'].map(role => ({ role, provider: 'fixture', model: role, base_url: 'http://fixture.invalid', key_configured: false }));
const session = { id: 's88-session', title: '设置持久化会话', knowledge_base_scope: ['kb-1'], document_scope: [], updated_at: 'now' };
const frame = (event: unknown) => `event: message\ndata: ${JSON.stringify(event)}\n\n`;

async function install(page: import('@playwright/test').Page) {
  await page.route('**/api/v1/runtime', route => route.fulfill({ json: envelope({ implementation: 'weknora-python-port' }) }));
  await page.route('**/api/v1/knowledge-bases', route => route.fulfill({ json: envelope([{ id: 'kb-1', name: '设置验证资料', cloud_allowed: true, document_count: 1 }]) }));
  await page.route('**/api/v1/knowledge-bases/kb-1/documents', route => route.fulfill({ json: envelope([{ id: 'doc-1', knowledge_base_id: 'kb-1', filename: '图片资料.pdf', status: 'ready' }]) }));
  await page.route('**/api/v1/settings/model-configuration', route => route.fulfill({ json: envelope({ revision: '1', roles }) }));
  await page.route('**/api/v1/sessions', route => route.fulfill({ json: envelope(route.request().method() === 'POST' ? session : [session]) }));
  await page.route('**/api/v1/sessions/s88-session/messages', route => route.fulfill({ json: envelope([]) }));
}

test('saved session settings restore after reload, Smart uses ordinary mode and finite explicit cap', async ({ page }) => {
  await install(page);
  let saved = { reasoning_mode: 'quick', max_output_tokens: 0 };
  let revision = 0;
  let sent: Record<string, unknown> | undefined;
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/v1/sessions/s88-session/generation-settings', async route => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      saved = body.reset ? { reasoning_mode: 'quick', max_output_tokens: 0 } : { reasoning_mode: body.reasoning_mode, max_output_tokens: body.max_output_tokens };
      revision++;
    }
    return route.fulfill({ json: envelope({ saved, session_revision: revision, configuration_version: `fixture-${revision}`,
      effective: { ...saved, max_output_tokens: saved.max_output_tokens || (saved.reasoning_mode === 'smart' ? 4096 : 2048) },
      defaults: { reasoning_mode: 'quick', max_output_tokens: 2048 } }) });
  });
  await page.goto('/');
  await page.getByRole('button', { name: /设置持久化会话/ }).click();
  const mode = page.getByRole('combobox', { name: '回答设置推理模式' });
  const maximum = page.getByRole('spinbutton', { name: '回答设置最大输出 Token' });
  await expect(mode).toBeEnabled();
  await mode.selectOption('smart'); await maximum.fill('1500');
  await page.getByRole('button', { name: '保存本会话设置' }).click();
  await expect(page.getByText('当前有效值：Smart · 1500 Token')).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: /设置持久化会话/ }).click();
  await expect(mode).toHaveValue('smart'); await expect(maximum).toHaveValue('1500');
  await page.route('**/api/v1/sessions/s88-session/messages', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: envelope([]) });
    sent = route.request().postDataJSON();
    await wait;
    return route.fulfill({ contentType: 'text/event-stream', body:
      frame({ id: 'run', response_type: 'agent_query', content: '', done: false, data: { request_id: 'run', session_id: session.id, assistant_message_id: 'answer' } }) +
      frame({ id: 'run', response_type: 'answer', content: '设置已送达', done: true, data: { event_id: 'a' } }) +
      frame({ id: 'run', response_type: 'complete', content: '', done: false, data: { final_content: '设置已送达', finish_reason: 'stop' } }) });
  });
  await page.getByPlaceholder('询问当前资料…').fill('设置验证问题');
  await page.getByRole('button', { name: '发送' }).click();
  await expect(mode).toBeDisabled(); await expect(maximum).toBeDisabled();
  release();
  await expect(page.getByText('设置已送达')).toBeVisible();
  expect(sent).toMatchObject({ mode: 'quick', reasoning_mode: 'smart', max_output_tokens: 1500 });
  expect(sent).not.toHaveProperty('reasoning_effort');
  await page.getByRole('button', { name: '恢复默认', exact: true }).click();
  await expect(mode).toHaveValue('quick'); await expect(maximum).toHaveValue('0');
  expect(saved).toEqual({ reasoning_mode: 'quick', max_output_tokens: 0 });
});

test('image settings save independently and selected failed Caption starts one explicit retry', async ({ page }) => {
  await install(page);
  let imageSaved = { reasoning_mode: 'quick', max_output_tokens: 0 };
  let retried: Record<string, unknown> | undefined;
  let posts = 0;
  await page.route('**/api/v1/knowledge-bases/kb-1/image-processing', route => {
    if (route.request().method() === 'PATCH') imageSaved = route.request().postDataJSON();
    return route.fulfill({ json: envelope({ id: 'kb-1', image_processing_config: imageSaved,
      effective_generation: { ...imageSaved, max_output_tokens: imageSaved.max_output_tokens || 5000 } }) });
  });
  await page.route('**/api/v1/knowledge-bases/kb-1/documents/doc-1/image-task', route => route.fulfill({ json: envelope({
    id: 'image-task', state: posts ? 'pending' : 'failed', source_version: 'version-1', outcomes: {
      '0': { asset_path: 'assets/selected.png', asset_sha256: 'a'.repeat(64), caption_result: '',
        caption: { status: 'failed', completion_kind: 'truncated', error: 'IMAGE_CAPTION_TRUNCATED', finish_reason: 'length', max_output_tokens: 5000,
          usage: { reasoning_tokens: 5000, completion_tokens: 5000, total_tokens: null } } },
      '1': { caption: { status: 'failed', error: 'IMAGE_CAPTION_COMPLETION_MISSING', usage: { reasoning_tokens: null, completion_tokens: null } } },
    },
  }) }));
  await page.route('**/api/v1/knowledge-bases/kb-1/documents/doc-1/image-task/retry', route => {
    posts++; retried = route.request().postDataJSON();
    return route.fulfill({ json: envelope({ id: 'new-attempt', state: 'pending' }) });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '资料库', exact: true }).click();
  await page.getByRole('combobox', { name: '上传目标知识库' }).selectOption('kb-1');
  await page.getByText('图片处理设置', { exact: true }).click();
  await expect(page.getByRole('combobox', { name: '图片处理推理模式' })).toBeEnabled();
  await page.getByRole('combobox', { name: '图片处理推理模式' }).selectOption('smart');
  await page.getByRole('spinbutton', { name: '图片处理最大输出 Token' }).fill('6000');
  await page.getByRole('button', { name: '保存图片设置' }).click();
  await expect(page.getByText('当前有效值：Smart · 6000 Token')).toBeVisible();
  await page.getByRole('button', { name: '图片处理状态' }).click();
  await expect(page.getByText('思考消耗 5000 Token，正文尚未产生')).toBeVisible();
  await expect(page.getByText('思考消耗 0 Token')).toHaveCount(0);
  await page.getByRole('button', { name: '用已保存图片设置重新处理此 Caption' }).click();
  await expect(page.getByText('任务状态：pending')).toBeVisible();
  expect(posts).toBe(1);
  expect(retried).toMatchObject({ source_version: 'version-1', asset_path: 'assets/selected.png', asset_sha256: 'a'.repeat(64), kind: 'caption' });
  expect(retried?.idempotency_key).toBeTruthy();
  await page.getByRole('button', { name: '问答', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '回答设置推理模式' })).toHaveValue('quick');
  await expect(page.getByRole('spinbutton', { name: '回答设置最大输出 Token' })).toHaveValue('0');
});
