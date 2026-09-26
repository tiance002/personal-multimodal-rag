import { test, expect } from "@playwright/test";

test("switching knowledge bases patches the active conversation scope", async ({ page }) => {
  let patchBody: Record<string, unknown> | null = null;
  await page.route("**/api/v1/knowledge-bases", async (route) => {
    await route.fulfill({ json: { data: [
      { id: "kb-a", name: "A", graph_enabled: false },
      { id: "kb-b", name: "B", graph_enabled: false },
    ], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations", async (route) => {
    await route.fulfill({ json: { data: [{ id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: [] }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/knowledge-bases/*/documents", async (route) => {
    await route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1/messages", async (route) => {
    await route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", async (route) => {
    patchBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { data: { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-b"], document_scope: [] }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.locator("#kb-select").selectOption("kb-b");
  await expect.poll(() => patchBody).toEqual({ knowledge_base_scope: ["kb-b"], document_scope: [] });
});

test("marking a document as the retrieval scope patches the active conversation", async ({ page }) => {
  let patchBody: Record<string, unknown> | null = null;
  await page.route("**/api/v1/knowledge-bases", async (route) => {
    await route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations", async (route) => {
    await route.fulfill({ json: { data: [{ id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: [] }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", async (route) => {
    await route.fulfill({ json: { data: [{ id: "doc-1", file_name: "guide.md", media_type: "text/markdown", index_status: "ready", version_no: 1 }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1/messages", async (route) => {
    await route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", async (route) => {
    patchBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { data: { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: ["doc-1"] }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await expect(page.getByRole("button", { name: "会话", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "仅此文档" })).toBeVisible();
  await page.getByRole("button", { name: "仅此文档" }).click();
  await expect.poll(() => patchBody).toEqual({ knowledge_base_scope: ["kb-a"], document_scope: ["doc-1"] });
});
