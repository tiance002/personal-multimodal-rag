import { expect, test, type Page } from "@playwright/test";

const prompt = "请明确对象并重述完整问题；本次上下文解析不可用或指代不明确，未检索或生成事实答案。";
const clarify = { run_id: "clarify-run", answer: prompt, citations: [], error_code: "NO_CANDIDATES", trace: { execution_mode: "clarification", clarification_required: true } };

async function base(page: Page, response: Record<string, unknown>, history: unknown[] = [], status = 201) {
  // Every API request is fulfilled locally; there is no backend/model fallback.
  await page.route("**/api/v1/**", route => route.fulfill({ status: 404, json: { error: { code: "UNEXPECTED_MOCK_ROUTE" } } }));
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: { data: [{ id: "kb-a", name: "A" }, { id: "kb-b", name: "B" }], meta: {} } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", route => route.fulfill({ json: { data: [], meta: {} } }));
  await page.route("**/api/v1/conversations", route => route.fulfill({ json: { data: [
    { id: "conv-a", title: "Replay A", knowledge_base_scope: ["kb-a"], document_scope: [] },
    { id: "conv-b", title: "Replay B", knowledge_base_scope: ["kb-b"], document_scope: [] },
  ], meta: {} } }));
  await page.route("**/api/v1/conversations/conv-a/messages", route => route.fulfill({ status: route.request().method() === "POST" ? status : 200, json: route.request().method() === "POST" ? (status < 400 ? { data: response, meta: {} } : response) : { data: history, meta: {} } }));
  await page.route("**/api/v1/conversations/conv-b/messages", route => route.fulfill({ json: { data: [{ role: "assistant", content: "Other conversation" }], meta: {} } }));
  await page.goto("/");
  await page.getByRole("button", { name: "Replay A", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "输入问题" })).toBeEnabled();
}
async function send(page: Page) {
  await page.getByRole("textbox", { name: "输入问题" }).fill("它的下一次巡检日期是什么时候？");
  await page.getByRole("button", { name: "发送问题" }).click();
}

test("an explicit safe clarification retains the actual prompt and is not a failure notice", async ({ page }) => {
  await base(page, clarify); await send(page);
  await expect(page.getByText(prompt, { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("请补充问题中的对象或上下文后再提问");
  await expect(page.locator(".citation-pill")).toHaveCount(0);
  await expect(page.getByText("回答未完成：NO_CANDIDATES", { exact: true })).toHaveCount(0);
});

test("a completed response still displays the answer and completed status", async ({ page }) => {
  await base(page, { run_id: "complete", answer: "Synthetic complete answer", citations: [], error_code: null }); await send(page);
  await expect(page.getByText("Synthetic complete answer", { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("回答已完成，证据已冻结");
});

for (const [label, response, code] of [
  ["plain no candidates", { ...clarify, trace: { execution_mode: "quick" } }, "NO_CANDIDATES"],
  ["missing explicit flag", { ...clarify, trace: { execution_mode: "clarification" } }, "NO_CANDIDATES"],
  ["false flag", { ...clarify, trace: { execution_mode: "clarification", clarification_required: false } }, "NO_CANDIDATES"],
  ["wrong error", { ...clarify, error_code: "PROVIDER_UNAVAILABLE" }, "PROVIDER_UNAVAILABLE"],
  ["cancelled", { ...clarify, error_code: "CANCELLED" }, "CANCELLED"],
  ["empty hint", { ...clarify, answer: "  " }, "NO_CANDIDATES"],
  ["claimed factual citation", { ...clarify, citations: ["E1"] }, "NO_CANDIDATES"],
] as const) {
  test(`${label} remains an ordinary error`, async ({ page }) => {
    await base(page, response); await send(page);
    await expect(page.getByText(`回答未完成：${code}`, { exact: true })).toBeVisible();
    await expect(page.getByRole("status")).toHaveText(`问答失败：${code}`);
    await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
  });
}

test("a duplicate API rejection is shown without inventing another assistant reply", async ({ page }) => {
  await base(page, { error: { code: "DUPLICATE_REQUEST", message: "request not regenerated" } }, [], 409); await send(page);
  await expect(page.getByRole("status")).toContainText("DUPLICATE_REQUEST");
  await expect(page.locator(".message.assistant")).toHaveCount(0);
});

test("refresh of a user-only history uses a generic unsaved-reply hint, not a reconstructed clarification", async ({ page }) => {
  await base(page, clarify, [{ role: "user", content: "Historical unanswered question" }]);
  await expect(page.getByRole("status")).toHaveText("暂无已保存的回复；请求状态暂无法确认。");
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "Replay A", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("暂无已保存的回复；请求状态暂无法确认。");
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
});

test("a pending replay cannot be duplicated or switched into another conversation", async ({ page }) => {
  let resolve!: () => void;
  const gate = new Promise<void>(r => { resolve = r; });
  let posts = 0;
  await base(page, clarify);
  await page.route("**/api/v1/conversations/conv-a/messages", async route => {
    if (route.request().method() !== "POST") return route.fulfill({ json: { data: [], meta: {} } });
    posts++; await gate; await route.fulfill({ status: 201, json: { data: clarify, meta: {} } });
  });
  await send(page); await expect.poll(() => posts).toBe(1);
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
  await page.getByRole("button", { name: "Replay B", exact: true }).click();
  await expect(page.getByText("Other conversation", { exact: true })).toHaveCount(0);
  resolve(); await expect(page.getByText(prompt, { exact: true })).toBeVisible();
  expect(posts).toBe(1);
  await page.getByRole("button", { name: "Replay B", exact: true }).click();
  await expect(page.getByText("Other conversation", { exact: true })).toBeVisible();
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
});


test("running refresh remains neutral and never implies an incomplete question", async ({ page }) => {
  const history: { role: string; content: string }[] = [];
  await base(page, clarify, history);
  let release!: () => void;
  const gate = new Promise<void>(r => { release = r; });
  let posts = 0;
  let running = false;
  await page.route("**/api/v1/conversations/conv-a/messages", async route => {
    if (route.request().method() !== "POST") return route.fulfill({ json: { data: history, meta: {} } });
    posts++; running = true; history.push({ role: "user", content: "Running question" });
    await gate; running = false;
    await route.fulfill({ status: 201, json: { data: clarify, meta: {} } }).catch(() => undefined);
  });
  try {
    await send(page); await expect.poll(() => posts).toBe(1);
    await page.reload();
    await page.getByRole("button", { name: "Replay A", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("暂无已保存的回复；请求状态暂无法确认。");
    expect(running).toBe(true); expect(posts).toBe(1);
    await expect(page.getByText("请检查问题是否完整", { exact: false })).toHaveCount(0);
  } finally { release(); }
});

for (const [label, response] of [
  ["failed clarification", clarify],
  ["genuine provider error", { ...clarify, error_code: "PROVIDER_UNAVAILABLE", trace: { execution_mode: "quick", clarification_required: false } }],
] as const) {
  test(`${label} refresh also remains neutral without persisted terminal metadata`, async ({ page }) => {
    const history: { role: string; content: string }[] = [];
    await base(page, response, history); await send(page);
    await expect(page.getByRole("status")).toHaveText(label === "failed clarification" ? "请补充问题中的对象或上下文后再提问" : "问答失败：PROVIDER_UNAVAILABLE");
    history.push({ role: "user", content: "Saved unanswered question" });
    await page.reload();
    await page.getByRole("button", { name: "Replay A", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("暂无已保存的回复；请求状态暂无法确认。");
    await expect(page.getByText("请检查问题是否完整", { exact: false })).toHaveCount(0);
    await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
  });
}


test("persisted terminal clarification restores the exact prompt after refresh without an assistant fact", async ({ page }) => {
  const history = [{ role: "user", content: "它何时巡检？", run_id: "clarify-run", citations: [], presentation: { kind: "clarification", text: prompt, clarification_required: true } }];
  await base(page, clarify, history);
  await expect(page.getByText(prompt, { exact: true })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("请补充问题中的对象或上下文后再提问");
  await expect(page.locator(".message.assistant")).toHaveCount(0);
  await expect(page.locator(".citation-pill")).toHaveCount(0);
  await page.reload(); await page.getByRole("button", { name: "Replay A", exact: true }).click();
  await expect(page.getByText(prompt, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Replay B", exact: true }).click();
  await expect(page.getByText("Other conversation", { exact: true })).toBeVisible();
  await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
});

for (const [label, patch] of [
  ["false marker", { presentation: { kind: "clarification", text: prompt, clarification_required: false } }],
  ["ordinary error", { presentation: { kind: "error", text: prompt, clarification_required: true } }],
  ["missing run", { run_id: undefined }],
  ["empty hint", { presentation: { kind: "clarification", text: "  ", clarification_required: true } }],
  ["citation", { citations: ["E1"] }],
] as const) {
  test(`invalid persisted ${label} remains neutral`, async ({ page }) => {
    await base(page, clarify, [{ role: "user", content: "Unanswered", run_id: "run", citations: [], presentation: { kind: "clarification", text: prompt, clarification_required: true }, ...patch }]);
    await expect(page.getByRole("status")).toHaveText("暂无已保存的回复；请求状态暂无法确认。");
    await expect(page.getByText(prompt, { exact: true })).toHaveCount(0);
  });
}
