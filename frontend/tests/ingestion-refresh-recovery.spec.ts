import { expect, test } from "@playwright/test";

// A refresh drops all in-memory job state. The document list must still expose
// the newest ingestion failure, its error, and a usable retry entry — and a
// failed new version must not hide behind an older `ready` active version.
test("a failed ingestion stays visible with a retry entry after a page refresh", async ({ page }) => {
  let retried = 0;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: { data: [{
    id: "doc-1", file_name: "scan.png", media_type: "image/png", index_status: "ready", version_no: 1, active_version_id: "v1",
    latest_version_no: 2, latest_index_status: "failed",
    latest_job: { id: "job-9", status: "failed", stage: "parsing", progress: 20, error_code: "OCR_UNAVAILABLE", attempts: 1, max_attempts: 3 },
  }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/ingestion-jobs/job-9", (route) => route.fulfill({ json: { data: { id: "job-9", status: "succeeded", stage: "ready", progress: 100, attempts: 2, max_attempts: 3, error_code: null }, meta: { request_id: "r" } } }));
  await page.route("**/api/v1/ingestion-jobs/job-9/retry", (route) => {
    retried++;
    return route.fulfill({ status: 202, json: { data: { id: "job-9", status: "queued", stage: "queued", progress: 0, attempts: 1, max_attempts: 3, error_code: null }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await expect(page.getByText(/摄取任务 · parsing · 20% · 失败/)).toBeVisible();
  await expect(page.getByText("OCR_UNAVAILABLE", { exact: true }).first()).toBeVisible();
  // The newest version's failure is shown next to the still-ready active version.
  await expect(page.getByText(/新版本 2 · failed/)).toBeVisible();
  await expect(page.getByText(/版本 1 · ready/)).toBeVisible();

  await page.reload();
  await expect(page.getByText(/摄取任务 · parsing · 20% · 失败/)).toBeVisible();
  await expect(page.getByRole("button", { name: "重试索引" })).toBeVisible();

  await page.getByRole("button", { name: "重试索引" }).click();
  await expect.poll(() => retried).toBe(1);
  await expect(page.getByText(/摄取任务.*100%.*已完成/)).toBeVisible();
});
