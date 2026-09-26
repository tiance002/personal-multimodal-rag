import { test, expect } from "@playwright/test";

test("three-column workbench hides graph controls when the selected knowledge base is default-off", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("历史会话")).toBeVisible();
  await expect(page.getByRole("heading", { name: "知识库" })).toBeVisible();
  await expect(page.getByRole("button", { name: "文档" })).toBeVisible();
  await expect(page.getByRole("button", { name: "图谱" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "智能推理" })).toBeVisible();
});

test("graph-enabled knowledge base exposes the opt-in graph tab", async ({ page }) => {
  await page.route("**/api/v1/knowledge-bases", async (route) => {
    await route.fulfill({ json: { data: [{ id: "kb-graph", name: "图谱库", graph_enabled: true }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations", async (route) => {
    await route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/knowledge-bases/kb-graph/documents", async (route) => {
    await route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await expect(page.getByRole("button", { name: "图谱" })).toBeVisible();
});
