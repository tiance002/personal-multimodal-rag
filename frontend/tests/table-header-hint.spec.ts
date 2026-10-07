import { test, expect, type Page } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

// Explicit offline bundle: every browser request is fulfilled or aborted locally.
test.beforeEach(async ({ page }) => {
  const bundle = process.env.RAG_OFFLINE_UI_BUNDLE;
  if (!bundle) return; // Normal project runner may supply its existing web server.
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== "https://offline.invalid") return route.abort();
    if (url.pathname.startsWith("/api/")) return route.fulfill({ status: 404, json: { error: { code: "UNEXPECTED_MOCK_ROUTE" } } });
    const root = path.resolve(bundle);
    const file = path.resolve(root, "." + (url.pathname === "/" ? "/index.html" : url.pathname));
    if (!file.startsWith(root + path.sep) || !existsSync(file)) return route.fulfill({ status: 404, body: "" });
    const contentType = file.endsWith(".js") ? "application/javascript" : file.endsWith(".css") ? "text/css" : "text/html";
    return route.fulfill({ contentType, body: readFileSync(file) });
  });
});

function payload(format: "pdf" | "docx", mode: "quick" | "smart" = "quick") {
  const dir = process.env.RAG_OFFLINE_HINT_ARTIFACTS;
  const file = dir && path.join(dir, `${format}${mode === "smart" ? "-smart" : ""}-mock-payload.json`);
  if (file && existsSync(file)) return JSON.parse(readFileSync(file, "utf8"));
  const text = `检索到相关 ${format.toUpperCase()} 表格，但目前无法可靠确认表头与数据列、单位的对应关系，因此无法核验本题的表格证据。请将相关表格另存为 XLSX 后上传，再提问；转换不保证能够答对。`;
  const hint = { kind: "evidence_hint", reason_code: "TABLE_HEADER_UNCONFIRMED", source_formats: [format], text };
  return { response: { run_id: "mock-run", answer: text, citations: [], error_code: "INSUFFICIENT_EVIDENCE", trace: { evidence_hint: hint } },
    history: [{ role: "user", content: "杉桥门店2026-09的营业额是多少？", run_id: "mock-run", citations: [], presentation: hint }] };
}

async function base(page: Page, response: any, history: any[] = [], mode: "quick" | "smart" = "quick") {
  let posts = 0;
  await page.route("**/api/v1/**", route => route.fulfill({ status: 404, json: { error: { code: "UNEXPECTED_MOCK_ROUTE" } } }));
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: { data: [{ id: "offline-kb", name: "合成库" }, { id: "other-kb", name: "其他库" }], meta: {} } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", route => route.fulfill({ json: { data: [{ id: "offline-document", file_name: "synthetic.pdf", media_type: "application/pdf", index_status: "ready", active_version_id: "offline-version" }], meta: {} } }));
  await page.route("**/api/v1/conversations", route => route.fulfill({ json: { data: [
    { id: "conv", title: "表格提示测试", knowledge_base_scope: ["offline-kb"], document_scope: ["offline-document"] },
    { id: "other", title: "其他会话", knowledge_base_scope: ["other-kb"], document_scope: [] },
  ], meta: {} } }));
  await page.route("**/api/v1/conversations/conv/messages", route => {
    if (route.request().method() === "POST") { expect(route.request().postDataJSON().mode).toBe(mode); posts++; return route.fulfill({ status: 201, json: { data: response, meta: {} } }); }
    return route.fulfill({ json: { data: history, meta: {} } });
  });
  await page.route("**/api/v1/conversations/other/messages", route => route.fulfill({ json: { data: [{ role: "assistant", content: "其他会话的历史" }], meta: {} } }));
  await page.goto("/");
  await page.getByRole("button", { name: "表格提示测试", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeEnabled();
  if (mode === "smart") {
    await page.getByRole("combobox", { name: "回答模式" }).click();
    await page.getByText("智能推理", { exact: true }).click();
  }
  return () => posts;
}
async function send(page: Page) {
  await page.getByRole("textbox", { name: "输入问题" }).fill("杉桥门店2026-09的营业额是多少？");
  await page.getByRole("button", { name: "发送问题" }).click();
}

for (const mode of ["quick", "smart"] as const) for (const format of ["pdf", "docx"] as const) {
  test(`${mode} ${format} hint reaches current UI, persisted refresh and scope switch without facts`, async ({ page }) => {
    const data = payload(format, mode), history: any[] = [];
    const posts = await base(page, data.response, history, mode); await send(page);
    await expect(page.getByText(data.response.answer, { exact: true })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("XLSX");
    await expect(page.locator(".message.assistant")).toHaveCount(0);
    await expect(page.locator(".citation-pill")).toHaveCount(0);
    expect(posts()).toBe(1);
    history.push(...data.history);
    await page.reload(); await page.getByRole("button", { name: "表格提示测试", exact: true }).click();
    await expect(page.getByText(data.response.answer, { exact: true })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("XLSX");
    expect(posts()).toBe(1);
    await page.getByRole("button", { name: "其他会话", exact: true }).click();
    await expect(page.getByText("其他会话的历史", { exact: true })).toBeVisible();
    await expect(page.getByText(data.response.answer, { exact: true })).toHaveCount(0);
  });
}

for (const [name, patch] of [
  ["no metadata", { trace: {} }],
  ["ordinary no candidates", { error_code: "NO_CANDIDATES" }],
  ["provider error", { error_code: "PROVIDER_UNAVAILABLE" }],
  ["cancelled", { error_code: "CANCELLED" }],
  ["cited reply", { citations: ["E1"] }],
  ["unrelated format", { trace: { evidence_hint: { ...payload("pdf").response.trace.evidence_hint, source_formats: ["html"] } } }],
] as const) {
  for (const mode of ["quick", "smart"] as const) test(`${mode} ${name} does not show an XLSX recommendation`, async ({ page }) => {
    const response = { ...payload("pdf").response, ...patch };
    await base(page, response, [], mode); await send(page);
    await expect(page.getByText(`回答未完成：${response.error_code}`, { exact: true })).toBeVisible();
    await expect(page.getByText(response.answer, { exact: true })).toHaveCount(0);
    await expect(page.getByRole("status")).not.toContainText("XLSX");
  });
}

for (const format of ["txt", "md", "html", "xlsx"]) {
  test(`${format} completed answer remains completed without conversion advice`, async ({ page }) => {
    const answer = "杉桥门店 2026-09 的营业额为 312 千元 [E1]。";
    await base(page, { run_id: format, answer, citations: ["E1"], error_code: null, trace: {} }); await send(page);
    await expect(page.getByRole("status")).toHaveText("回答已完成，证据已冻结");
    await expect(page.locator(".message.assistant")).toHaveCount(1);
    await expect(page.locator(".citation-pill")).toHaveCount(1);
    await expect(page.locator(".message.assistant")).toContainText("312 千元");
    await expect(page.getByRole("status")).not.toContainText("XLSX");
  });
}

test("existing clarification remains an object clarification", async ({ page }) => {
  await base(page, { run_id: "clarify", answer: "请补充问题对象。", citations: [], error_code: "NO_CANDIDATES", trace: { execution_mode: "clarification", clarification_required: true } }); await send(page);
  await expect(page.getByText("请补充问题对象。", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("请补充问题中的对象或上下文后再提问");
});
