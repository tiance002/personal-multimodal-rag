import { expect, test, type Page } from "@playwright/test";

const envelope = (data: unknown) => ({ data, meta: { request_id: "r" } });
const bases = [
  { id: "kb-a", name: "A", graph_enabled: false },
  { id: "kb-b", name: "B", graph_enabled: false },
];

async function mockEmptyConversations(page: Page) {
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: envelope(bases) }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: envelope([]) }));
}

function aDocument(status: "failed" | "running") {
  return {
    id: "doc-a", knowledge_base_id: "kb-a", file_name: "A-only.md", media_type: "text/markdown",
    index_status: status, version_no: 1, latest_version_no: 1, latest_index_status: status,
    latest_job: { id: "job-a", status, stage: "indexing", progress: 40, attempts: 1, max_attempts: 3, error_code: status === "failed" ? "A_FAILED" : null },
  };
}

test("a failed A job cannot become B's task after switching libraries", async ({ page }) => {
  await mockEmptyConversations(page);
  let releaseB: (() => void) | undefined;
  const bGate = new Promise<void>((resolve) => { releaseB = resolve; });
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: envelope([aDocument("failed")]) }));
  await page.route("**/api/v1/knowledge-bases/kb-b/documents", async (route) => { await bGate; await route.fulfill({ json: envelope([]) }); });

  await page.goto("/");
  await expect(page.getByText("A-only.md")).toBeVisible();
  await expect(page.getByText("A_FAILED", { exact: true }).first()).toBeVisible();
  const bRequest = page.waitForRequest("**/api/v1/knowledge-bases/kb-b/documents");
  await page.locator("#kb-select").selectOption("kb-b");
  await bRequest;
  await expect(page.getByRole("button", { name: "重试索引" })).toHaveCount(0);
  releaseB?.();
  await expect(page.getByText("资料 0")).toBeVisible();
  await expect(page.getByText("A-only.md")).toHaveCount(0);
  await expect(page.getByText(/摄取任务/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重试索引" })).toHaveCount(0);
});

test("a running A job cannot block an upload to B", async ({ page }) => {
  await mockEmptyConversations(page);
  let bUploads = 0;
  let releaseB: (() => void) | undefined;
  const bGate = new Promise<void>((resolve) => { releaseB = resolve; });
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: envelope([aDocument("running")]) }));
  await page.route("**/api/v1/knowledge-bases/kb-b/documents", async (route) => {
    if (route.request().method() === "POST") {
      bUploads++;
      return route.fulfill({ status: 202, json: envelope({ job_id: "job-b", document_id: "doc-b", status: "stored" }) });
    }
    await bGate;
    return route.fulfill({ json: envelope([]) });
  });
  await page.route("**/api/v1/ingestion-jobs/job-a", (route) => route.fulfill({ json: envelope(aDocument("running").latest_job) }));
  await page.route("**/api/v1/ingestion-jobs/job-b", (route) => route.fulfill({ json: envelope({ id: "job-b", status: "running", stage: "indexing", progress: 20, attempts: 1, max_attempts: 3, error_code: null }) }));

  await page.goto("/");
  await expect(page.getByText(/摄取任务.*进行中/)).toBeVisible();
  const bRequest = page.waitForRequest("**/api/v1/knowledge-bases/kb-b/documents");
  await page.locator("#kb-select").selectOption("kb-b");
  await bRequest;
  releaseB?.();
  await expect(page.getByText("资料 0")).toBeVisible();
  await expect(page.getByRole("button", { name: "＋ 导入" })).toBeEnabled();
  const bListAfterUpload = page.waitForResponse((response) => response.url().endsWith("/kb-b/documents") && response.request().method() === "GET");
  await page.locator('input[type="file"]').setInputFiles({ name: "B-only.md", mimeType: "text/markdown", buffer: Buffer.from("B fact") });
  await expect.poll(() => bUploads).toBe(1);
  await bListAfterUpload;
});

test("a late A document response cannot replace B's scoped list", async ({ page }) => {
  await mockEmptyConversations(page);
  let releaseA: (() => void) | undefined;
  const aGate = new Promise<void>((resolve) => { releaseA = resolve; });
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", async (route) => {
    await aGate;
    await route.fulfill({ json: envelope([aDocument("failed")]) });
  });
  await page.route("**/api/v1/knowledge-bases/kb-b/documents", (route) => route.fulfill({ json: envelope([{
    id: "doc-b", knowledge_base_id: "kb-b", file_name: "B-only.md", media_type: "text/markdown", index_status: "ready", version_no: 1,
  }]) }));

  const aRequest = page.waitForRequest("**/api/v1/knowledge-bases/kb-a/documents");
  await page.goto("/");
  await aRequest;
  await page.locator("#kb-select").selectOption("kb-b");
  await expect(page.getByText("B-only.md")).toBeVisible();
  const aResponse = page.waitForResponse("**/api/v1/knowledge-bases/kb-a/documents");
  releaseA?.();
  await aResponse;
  await page.waitForTimeout(150);
  await expect(page.getByText("A-only.md")).toHaveCount(0);
  await expect(page.getByText("B-only.md")).toBeVisible();
  await expect(page.getByText(/摄取任务/)).toHaveCount(0);
});
