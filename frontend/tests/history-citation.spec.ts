import { expect, test } from "@playwright/test";

// Reopening a historical conversation must re-expose the frozen run link and
// citation labels so the original evidence can be read back.
test("a reopened conversation keeps its run link and reads the frozen citation back", async ({ page }) => {
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-1", name: "演示库", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [{ id: "conv-1", title: "历史会话", knowledge_base_scope: ["kb-1"], document_scope: [] }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-1/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => route.fulfill({ json: { data: [
    { id: "m-1", role: "user", content: "这份资料的成本是多少？", citations: [] },
    { id: "m-2", role: "assistant", content: "成本是 1000 元 [E1]。", run_id: "run-hist-1", citations: ["E1"] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/runs/run-hist-1/citations/E1", (route) => route.fulfill({ json: { data: { citation_id: "E1", label: "E1", quote: "历史冻结证据可回读", locator: { page: 3 }, current_status: "current" }, meta: { request_id: "r" } } }));

  await page.goto("/");
  await expect(page.getByText("成本是 1000 元 [E1]。")).toBeVisible();
  await page.getByRole("button", { name: "E1" }).click();
  await expect(page.getByText("历史冻结证据可回读")).toBeVisible();
});
