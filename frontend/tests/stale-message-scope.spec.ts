import { expect, test } from "@playwright/test";

test("a late historical message response cannot overwrite the newer conversation", async ({ page }) => {
  let releaseFirst: (() => void) | undefined;
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话一", knowledge_base_scope: ["kb-a"], document_scope: [] },
    { id: "conv-2", title: "会话二", knowledge_base_scope: ["kb-a"], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", async (route) => {
    await firstGate;
    return route.fulfill({ json: { data: [{ id: "m-1", role: "assistant", content: "会话一 的旧回答" }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-2/messages", (route) => route.fulfill({ json: { data: [{ id: "m-2", role: "assistant", content: "会话二 的新回答" }], meta: { request_id: "r" } } }));

  await page.goto("/");
  await expect(page.getByRole("button", { name: "会话二" })).toBeVisible();
  await page.getByRole("button", { name: "会话二" }).click();
  await expect(page.getByText("会话二 的新回答")).toBeVisible();

  releaseFirst?.();
  await page.waitForTimeout(300);
  await expect(page.getByText("会话一 的旧回答")).toHaveCount(0);
  await expect(page.getByText("会话二 的新回答")).toBeVisible();
});

test("switching knowledge bases drops the previous transcript", async ({ page }) => {
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => route.fulfill({ json: { data: [{ id: "m-1", role: "assistant", content: "A 库的回答" }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1", (route) => route.fulfill({ json: { data: { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-b"], document_scope: [] }, meta: { request_id: "r" } } }));

  await page.goto("/");
  await expect(page.getByText("A 库的回答")).toBeVisible();
  await page.locator("#kb-select").selectOption("kb-b");
  await expect(page.locator("#kb-select")).toHaveValue("kb-b");
  await expect(page.getByText("A 库的回答")).toHaveCount(0);
});
