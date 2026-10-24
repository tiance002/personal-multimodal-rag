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

async function selectUploadKnowledgeBase(page: import('@playwright/test').Page, name: string) {
  await page.getByRole('combobox', { name: '上传目标知识库' }).click();
  await page.locator('.ant-select-dropdown').getByText(name, { exact: true }).click();
}

async function openImageSettings(page: import('@playwright/test').Page, knowledgeBase = '设置验证资料') {
  await page.goto('/');
  await page.getByRole('menuitem', { name: '资料库', exact: true }).click();
  await selectUploadKnowledgeBase(page, knowledgeBase);
  await page.getByText('图片处理设置', { exact: true }).click();
}

test('image save validation failure allows correction, retry and restoring defaults', async ({ page }) => {
  await install(page);
  let saved = { reasoning_mode: 'quick', max_output_tokens: 0 };
  let patches = 0;
  await page.route('**/api/v1/knowledge-bases/kb-1/image-processing', route => {
    if (route.request().method() === 'PATCH') {
      patches++;
      const body = route.request().postDataJSON();
      if (body.max_output_tokens === 999999) return route.fulfill({ status: 422, json: { error: { message: '图片输出额度超出当前配置' } } });
      saved = body;
    }
    return route.fulfill({ json: envelope({ image_processing_config: saved,
      effective_generation: { ...saved, max_output_tokens: saved.max_output_tokens || 5000 } }) });
  });
  await openImageSettings(page);
  const maximum = page.getByRole('spinbutton', { name: '图片处理最大输出 Token' });
  const save = page.getByRole('button', { name: '保存图片设置' });
  const reset = page.getByRole('button', { name: '恢复图片默认' });
  await expect(maximum).toBeEnabled();
  await maximum.fill('999999'); await save.click();
  await expect(page.getByRole('alert')).toContainText('图片输出额度超出当前配置');
  await expect(save).toBeEnabled(); await expect(reset).toBeEnabled();
  await maximum.fill('4000'); await save.click();
  await expect(page.getByRole('region', { name: '图片处理设置', exact: true }).getByRole('status')).toContainText('图片设置已保存');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await maximum.fill('999999'); await save.click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('region', { name: '图片处理设置', exact: true }).getByRole('status')).toHaveCount(0);
  await reset.click();
  await expect(maximum).toHaveValue('0');
  expect(saved).toEqual({ reasoning_mode: 'quick', max_output_tokens: 0 });
  expect(patches).toBe(4);
});

test('image load failure is blocked until explicit load retry succeeds', async ({ page }) => {
  await install(page);
  let reads = 0;
  let available = false;
  await page.route('**/api/v1/knowledge-bases/kb-1/image-processing', route => {
    reads++;
    return !available ? route.fulfill({ status: 503, json: { error: { message: '图片设置暂时不可读取' } } })
      : route.fulfill({ json: envelope({ image_processing_config: { reasoning_mode: 'smart', max_output_tokens: 3000 } }) });
  });
  await openImageSettings(page);
  await expect(page.getByRole('alert')).toContainText('图片设置暂时不可读取');
  await expect(page.getByRole('button', { name: '保存图片设置' })).toBeDisabled();
  await expect(page.getByRole('combobox', { name: '图片处理推理模式' })).toBeDisabled();
  const failedReads = reads; available = true;
  await page.getByRole('button', { name: '重新读取图片设置' }).click();
  await expect(page.getByRole('combobox', { name: '图片处理推理模式' })).toHaveValue('smart');
  await expect(page.getByRole('button', { name: '保存图片设置' })).toBeEnabled();
  expect(reads).toBe(failedReads + 1);
});

test('transient image save failure can retry the same unsaved values', async ({ page }) => {
  await install(page);
  let patches = 0;
  await page.route('**/api/v1/knowledge-bases/kb-1/image-processing', route => {
    if (route.request().method() === 'PATCH' && ++patches === 1) {
      return route.fulfill({ status: 503, json: { error: { message: '图片设置保存暂时不可用' } } });
    }
    return route.fulfill({ json: envelope({ image_processing_config: {},
      effective_generation: { reasoning_mode: 'quick', max_output_tokens: 3500 } }) });
  });
  await openImageSettings(page);
  const maximum = page.getByRole('spinbutton', { name: '图片处理最大输出 Token' });
  const save = page.getByRole('button', { name: '保存图片设置' });
  await expect(maximum).toBeEnabled(); await maximum.fill('3500'); await save.click();
  await expect(page.getByRole('alert')).toContainText('图片设置保存暂时不可用');
  await expect(maximum).toHaveValue('3500');
  await save.click();
  await expect(page.getByText('当前有效值：Quick · 3500 Token')).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(patches).toBe(2);
});

test('image pending save disables concurrent edits, save and reset', async ({ page }) => {
  await install(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let patches = 0;
  await page.route('**/api/v1/knowledge-bases/kb-1/image-processing', async route => {
    if (route.request().method() === 'PATCH') { patches++; await held; }
    return route.fulfill({ json: envelope({ image_processing_config: {} }) });
  });
  await openImageSettings(page);
  const save = page.getByRole('button', { name: '保存图片设置' });
  await expect(save).toBeEnabled(); await save.click();
  await expect(save).toBeDisabled();
  await expect(page.getByRole('button', { name: '恢复图片默认' })).toBeDisabled();
  await expect(page.getByRole('spinbutton', { name: '图片处理最大输出 Token' })).toBeDisabled();
  release();
  await expect(save).toBeEnabled(); expect(patches).toBe(1);
});

test('late image save from another knowledge base cannot replace current settings', async ({ page }) => {
  await install(page);
  await page.route('**/api/v1/knowledge-bases', route => route.fulfill({ json: envelope([
    { id: 'kb-1', name: '旧资料库', cloud_allowed: true, document_count: 1 },
    { id: 'kb-2', name: '新资料库', cloud_allowed: true, document_count: 0 },
  ]) }));
  await page.route('**/api/v1/knowledge-bases/kb-2/documents', route => route.fulfill({ json: envelope([]) }));
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let patchStarted!: () => void;
  const started = new Promise<void>(resolve => { patchStarted = resolve; });
  let oldReads = 0;
  await page.route('**/api/v1/knowledge-bases/*/image-processing', async route => {
    const old = route.request().url().includes('/kb-1/');
    if (old && route.request().method() === 'GET') oldReads++;
    if (route.request().method() === 'PATCH') { patchStarted(); await held; }
    return route.fulfill({ json: envelope({ image_processing_config: { reasoning_mode: old ? 'smart' : 'quick', max_output_tokens: old ? 4000 : 2000 } }) });
  });
  await openImageSettings(page, '旧资料库');
  const maximum = page.getByRole('spinbutton', { name: '图片处理最大输出 Token' });
  await expect(maximum).toHaveValue('4000');
  await page.getByRole('button', { name: '保存图片设置' }).click(); await started;
  const priorReads = oldReads;
  await selectUploadKnowledgeBase(page, '新资料库');
  await expect(maximum).toHaveValue('2000');
  // Flush the pending response through the browser before checking the new scope.
  const finished = page.waitForResponse(response => response.url().includes('/kb-1/image-processing') && response.request().method() === 'PATCH');
  release(); await finished;
  await expect(maximum).toHaveValue('2000');
  await expect(page.getByRole('combobox', { name: '图片处理推理模式' })).toHaveValue('quick');
  await expect(page.getByText('图片设置已保存，仅用于新的 VLM / Caption 处理尝试。')).toHaveCount(0);
  expect(oldReads).toBe(priorReads);
});

test('late image load cannot overwrite a newly selected knowledge base', async ({ page }) => {
  await install(page);
  await page.route('**/api/v1/knowledge-bases', route => route.fulfill({ json: envelope([
    { id: 'kb-1', name: '旧资料库', cloud_allowed: true, document_count: 1 },
    { id: 'kb-2', name: '新资料库', cloud_allowed: true, document_count: 0 },
  ]) }));
  await page.route('**/api/v1/knowledge-bases/kb-2/documents', route => route.fulfill({ json: envelope([]) }));
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let loadStarted!: () => void;
  const started = new Promise<void>(resolve => { loadStarted = resolve; });
  await page.route('**/api/v1/knowledge-bases/*/image-processing', async route => {
    const old = route.request().url().includes('/kb-1/');
    if (old) { loadStarted(); await held; }
    return route.fulfill({ json: envelope({ image_processing_config: { reasoning_mode: old ? 'smart' : 'quick', max_output_tokens: old ? 9000 : 2000 } }) });
  });
  await openImageSettings(page, '旧资料库'); await started;
  await expect(page.getByRole('button', { name: '保存图片设置' })).toBeDisabled();
  await selectUploadKnowledgeBase(page, '新资料库');
  const maximum = page.getByRole('spinbutton', { name: '图片处理最大输出 Token' });
  await expect(maximum).toHaveValue('2000');
  const finished = page.waitForResponse(response => response.url().includes('/kb-1/image-processing'));
  release(); await finished;
  await expect(maximum).toHaveValue('2000');
  await expect(page.getByRole('button', { name: '保存图片设置' })).toBeEnabled();
});

for (const thinking of ['unknown', 'unsupported', 'supported'] as const) {
  test(`answer capability display preserves ${thinking} and distinguishes operating budget from certified maximum`, async ({ page }) => {
    await install(page);
    await page.route('**/api/v1/sessions/s88-session/generation-settings', route => route.fulfill({ json: envelope({
      saved: {}, defaults: { reasoning_mode: 'quick', max_output_tokens: 2048 },
      effective: { reasoning_mode: 'quick', max_output_tokens: 2048 },
      capabilities: { thinking, operating_max_output_tokens: 2048, certified_max_output_tokens: null },
    }) }));
    await page.goto('/');
    await page.getByRole('button', { name: /设置持久化会话/ }).click();
    const controls = page.getByRole('group', { name: '回答设置', exact: true });
    await expect(controls).toContainText(thinking === 'unknown' ? '思考能力：未知，尚未确认' : thinking === 'unsupported' ? '思考能力：当前模型不支持 Smart' : '思考能力：支持');
    await expect(controls).toContainText('运行输出额度：2048 Token；已认证模型输出上限：未知');
    await expect(controls).toContainText('不代表价格档位');
    const smart = controls.getByRole('option', { name: 'Smart · 开启思考' });
    await expect(smart).toHaveJSProperty('disabled', thinking === 'unsupported');
  });
}

test('dynamic answer routing shows candidate roles and catalog capacity without certification', async ({ page }) => {
  await install(page);
  await page.route('**/api/v1/sessions/s88-session/generation-settings', route => route.fulfill({ json: envelope({
    saved: {}, defaults: { reasoning_mode: 'quick', max_output_tokens: 2048 },
    effective: { reasoning_mode: 'quick', max_output_tokens: 2048 },
    capabilities: { thinking: 'supported', operating_max_output_tokens: 2048, certified_max_output_tokens: null,
      declared_max_output_tokens: 65536, capacity_source: 'fixed-source-catalog', selected_model: null,
      routing_mode: 'DYNAMIC', selection_scope: 'configured-routing-roles', candidate_models: [
        { model_id: null, role: 'cheap', provider: 'fixture', model: 'low-cost-model', source: 'cloud' },
        { model_id: null, role: 'expensive', provider: 'fixture', model: 'high-cost-model', source: 'cloud' },
      ] },
  }) }));
  await page.goto('/'); await page.getByRole('button', { name: /设置持久化会话/ }).click();
  const controls = page.getByRole('group', { name: '回答设置', exact: true });
  await expect(controls).toContainText('服务端路由：DYNAMIC；候选模型：cheap · fixture / low-cost-model；expensive · fixture / high-cost-model');
  await expect(controls).toContainText('目录声明输出上限：65536 Token；此声明不代表当前部署已认证');
  await expect(controls).toContainText('已认证模型输出上限：未知');
  await expect(controls).not.toContainText('当前模型：');
});

test('image capability guidance names the explicit caption model without substituting roles', async ({ page }) => {
  await install(page);
  await page.route('**/api/v1/knowledge-bases/kb-1/image-processing', route => route.fulfill({ json: envelope({
    image_processing_config: {}, effective_generation: { reasoning_mode: 'quick', max_output_tokens: 5000 },
    capabilities: { thinking: 'unknown', operating_max_output_tokens: 5000, certified_max_output_tokens: null,
      selected_model: { model_id: 'explicit-caption', role: 'vlm', provider: 'fixture', model: 'selected-caption', source: 'cloud' },
      selection_scope: 'explicit-caption-model', error_code: 'MODEL_GENERATION_CAPACITY_UNKNOWN' },
  }) }));
  await openImageSettings(page);
  const controls = page.getByRole('group', { name: '图片处理', exact: true });
  await expect(controls).toContainText('当前模型：fixture / selected-caption（vlm）');
  await expect(controls).toContainText('当前模型的容量信息尚未确认，请在模型设置中检查配置');
  await expect(controls).toContainText('思考能力：未知，尚未确认');
});

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
  await page.getByRole('menuitem', { name: '资料库', exact: true }).click();
  await selectUploadKnowledgeBase(page, '设置验证资料');
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
  await page.getByRole('menuitem', { name: '问答', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '回答设置推理模式' })).toHaveValue('quick');
  await expect(page.getByRole('spinbutton', { name: '回答设置最大输出 Token' })).toHaveValue('0');
});
