import { expect, test, type Page, type Route } from '@playwright/test';

async function fixture(page: Page) {
  // Every API response is synthetic; this test cannot read the running KB.
  await page.route('**/api/v1/**', route => route.fulfill({json: {data: [], meta: {request_id: 'synthetic'}}}));
  await page.route('**/api/v1/knowledge-bases', route => route.fulfill({json: {data: [{id: 'kb', name: 'Synthetic KB', graph_enabled: false}], meta: {}}}));
  await page.route('**/api/v1/conversations', route => route.fulfill({json: {data: [
    {id: 'conv-1', title: 'Conversation one', knowledge_base_scope: ['kb'], document_scope: []},
    {id: 'conv-2', title: 'Conversation two', knowledge_base_scope: ['kb'], document_scope: []},
  ], meta: {}}}));
  await page.route('**/api/v1/conversations/conv-1/messages', route => route.fulfill({json: {data: [
    {id: 'm1', role: 'assistant', content: 'Synthetic first answer [E1][E2]', run_id: 'run-1', citations: ['E1', 'E2']},
  ], meta: {}}}));
  await page.route('**/api/v1/conversations/conv-2/messages', route => route.fulfill({json: {data: [
    {id: 'm2', role: 'assistant', content: 'Synthetic second answer', citations: []},
  ], meta: {}}}));
}

test('a late citation cannot reopen after switching conversation', async ({page}) => {
  await fixture(page);
  let pending: Route | undefined;
  await page.route('**/api/v1/runs/run-1/citations/E1', route => { pending = route; });
  await page.goto('/');
  await page.getByRole('button', {name: 'Conversation one', exact: true}).click();
  await page.getByRole('button', {name: 'E1', exact: true}).click();
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole('button', {name: 'Conversation two', exact: true}).click();
  await expect(page.getByText('Synthetic second answer')).toBeVisible();
  const response = page.waitForResponse('**/api/v1/runs/run-1/citations/E1');
  await pending!.fulfill({json: {data: {quote: 'Old synthetic citation', current_status: 'current', locator: {}}, meta: {}}});
  await response;
  await page.waitForTimeout(150);
  await expect(page.locator('.citation-drawer')).toHaveCount(0);
});

test('a late first citation cannot overwrite the newer selected citation', async ({page}) => {
  await fixture(page);
  let pending: Route | undefined;
  await page.route('**/api/v1/runs/run-1/citations/E1', route => { pending = route; });
  await page.route('**/api/v1/runs/run-1/citations/E2', route => route.fulfill({json: {data: {quote: 'New synthetic citation', current_status: 'current', locator: {}}, meta: {}}}));
  await page.goto('/');
  await page.getByRole('button', {name: 'Conversation one', exact: true}).click();
  await page.getByRole('button', {name: 'E1', exact: true}).click();
  await expect.poll(() => Boolean(pending)).toBe(true);
  await page.getByRole('button', {name: 'E2', exact: true}).click();
  await expect(page.getByText('New synthetic citation')).toBeVisible();
  const response = page.waitForResponse('**/api/v1/runs/run-1/citations/E1');
  await pending!.fulfill({json: {data: {quote: 'Old synthetic citation', current_status: 'current', locator: {}}, meta: {}}});
  await response;
  await page.waitForTimeout(150);
  await expect(page.getByText('New synthetic citation')).toBeVisible();
  await expect(page.getByText('Old synthetic citation')).toHaveCount(0);
});
