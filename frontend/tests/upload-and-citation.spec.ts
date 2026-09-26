import { test, expect } from "@playwright/test";

test("citation inspector has a readable evidence surface", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".inspector")).toBeVisible();
  await expect(page.getByText("等待选择")).toBeVisible();
});

test("clicking an answer citation opens the frozen evidence", async ({ page }) => {
  await page.route("**/api/v1/knowledge-bases", async (route) => {
    await route.fulfill({ json: { data: [{ id: "kb-1", name: "演示库", description: "", graph_enabled: false, cloud_allowed: false }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { data: [{ id: "conv-1", title: "演示对话" }], meta: { request_id: "r" } } });
    }
  });
  await page.route("**/api/v1/knowledge-bases/kb-1/documents", async (route) => {
    await route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1/messages", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
    } else {
      await route.fulfill({ json: { data: { run_id: "run-1", answer: "答案 [E1]", citations: ["E1"] }, meta: { request_id: "r" } } });
    }
  });
  await page.route("**/api/v1/runs/run-1/events", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: "id: 1\nevent: answer.completed\ndata: {}\n\n" });
  });
  await page.route("**/api/v1/runs/run-1/citations/E1", async (route) => {
    await route.fulfill({ json: { data: { citation_id: "E1", label: "E1", quote: "可回读证据", locator: { page: 1 }, current_status: "current" }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await expect(page.locator("#kb-select")).toHaveValue("kb-1");
  await page.getByLabel("输入问题").fill("问题");
  await page.getByRole("button", { name: "发送问题" }).click();
  await page.getByRole("button", { name: "E1" }).click();
  await expect(page.getByText("可回读证据")).toBeVisible();
});
