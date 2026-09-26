import { expect, test } from "@playwright/test";

test("an empty installation can create a knowledge base, upload its first file, and see indexing finish", async ({ page }) => {
  let created = false;
  let jobReads = 0;
  await page.route("**/api/v1/knowledge-bases", (route) => {
    if (route.request().method() === "POST") {
      created = true;
      return route.fulfill({ status: 201, json: { data: { id: "kb-new", name: "第一知识库", graph_enabled: false, cloud_allowed: false }, meta: { request_id: "r" } } });
    }
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-new/documents", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 202, json: { data: { job_id: "job-1", document_id: "doc-1", status: "stored" }, meta: { request_id: "r" } } });
    }
    return route.fulfill({ json: { data: created ? [{ id: "doc-1", file_name: "first.md", media_type: "text/markdown", index_status: jobReads > 1 ? "ready" : "processing", version_no: 1 }] : [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/ingestion-jobs/job-1", (route) => {
    jobReads++;
    return route.fulfill({ json: { data: { id: "job-1", status: jobReads > 1 ? "succeeded" : "running", stage: jobReads > 1 ? "ready" : "indexing", progress: jobReads > 1 ? 100 : 60, attempts: 1, max_attempts: 3, error_code: null }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("知识库名称").fill("第一知识库");
  await page.getByRole("button", { name: "创建知识库" }).click();
  await expect(page.locator("#kb-select")).toHaveValue("kb-new");
  await page.locator('input[type="file"]').setInputFiles({ name: "first.md", mimeType: "text/markdown", buffer: Buffer.from("# First\nFirst fact") });
  await expect(page.getByText(/摄取任务.*60%/)).toBeVisible();
  await expect(page.getByText(/摄取任务.*100%.*已完成/)).toBeVisible();
  await expect(page.getByText(/版本 1 · ready/)).toBeVisible();
  const readsAfterCompletion = jobReads;
  await page.waitForTimeout(1800);
  expect(jobReads).toBe(readsAfterCompletion);
});

test("a failed ingestion job shows its error and can be queued for worker retry", async ({ page }) => {
  let retried = false;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 202, json: { data: { job_id: "job-2", document_id: "doc-2", status: "stored" }, meta: { request_id: "r" } } });
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/ingestion-jobs/job-2", (route) => route.fulfill({ json: { data: {
    id: "job-2", status: retried ? "succeeded" : "failed", stage: retried ? "ready" : "parsing", progress: retried ? 100 : 20, attempts: retried ? 2 : 1, max_attempts: 3, error_code: retried ? null : "OCR_UNAVAILABLE",
  }, meta: { request_id: "r" } } }));
  await page.route("**/api/v1/ingestion-jobs/job-2/retry", (route) => {
    retried = true;
    return route.fulfill({ status: 202, json: { data: { id: "job-2", status: "queued", stage: "queued", progress: 0, attempts: 1, max_attempts: 3, error_code: null }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await expect(page.locator("#kb-select")).toHaveValue("kb-a");
  await page.locator('input[type="file"]').setInputFiles({ name: "scan.png", mimeType: "image/png", buffer: Buffer.from("image") });
  await expect(page.getByText("OCR_UNAVAILABLE", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重试摄取" }).click();
  await expect(page.getByText(/摄取任务.*100%.*已完成/)).toBeVisible();
  expect(retried).toBe(true);
});

test("a second upload cannot replace the first job while its receipt or ingestion is pending", async ({ page }) => {
  let releaseUpload: (() => void) | undefined;
  const uploadGate = new Promise<void>((resolve) => { releaseUpload = resolve; });
  let uploads = 0;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", async (route) => {
    if (route.request().method() === "POST") {
      uploads++;
      await uploadGate;
      return route.fulfill({ status: 202, json: { data: { job_id: "job-1", document_id: "doc-1", status: "stored" }, meta: { request_id: "r" } } });
    }
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/ingestion-jobs/job-1", (route) => route.fulfill({ json: { data: {
    id: "job-1", status: "running", stage: "indexing", progress: 40, attempts: 1, max_attempts: 3, error_code: null,
  }, meta: { request_id: "r" } } }));

  await page.goto("/");
  await expect(page.locator("#kb-select")).toHaveValue("kb-a");
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles({ name: "one.md", mimeType: "text/markdown", buffer: Buffer.from("first") });
  await expect(page.getByRole("button", { name: "＋ 导入" })).toBeDisabled();
  await fileInput.setInputFiles({ name: "two.md", mimeType: "text/markdown", buffer: Buffer.from("second") });
  expect(uploads).toBe(1);
  releaseUpload?.();
  await expect(page.getByText(/摄取任务.*40%/)).toBeVisible();
  await expect(page.getByRole("button", { name: "＋ 导入" })).toBeDisabled();
  expect(uploads).toBe(1);
});
