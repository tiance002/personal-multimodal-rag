import { expect, test } from "@playwright/test";

test("a late historical message response cannot overwrite the newer conversation", async ({ page }) => {
  let releaseFirst: (() => void) | undefined;
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { data: [], meta: {} } }));
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

  const firstRequest = page.waitForRequest("**/api/v1/conversations/conv-1/messages");
  await page.goto("/");
  await page.locator(".conversation-item").first().click();
  await firstRequest;
  await expect(page.getByRole("button", { name: "会话二" })).toBeVisible();
  await page.getByRole("button", { name: "会话二" }).click();
  await expect(page.getByText("会话二 的新回答")).toBeVisible();

  releaseFirst?.();
  await page.waitForTimeout(300);
  await expect(page.getByText("会话一 的旧回答")).toHaveCount(0);
  await expect(page.getByText("会话二 的新回答")).toBeVisible();
});

test("switching knowledge bases drops the previous transcript", async ({ page }) => {
  await page.route("**/api/v1/**", route => route.fulfill({ json: { data: [], meta: {} } }));
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
  await page.locator(".conversation-item").first().click();
  await expect(page.getByText("A 库的回答")).toBeVisible();
  await page.locator(".scope-trigger").click();
  await page.locator(".scope-options").getByRole("button", { name: "database B", exact: true }).click();
  await expect(page.locator(".composer-scope")).toHaveText("B");
  await expect(page.getByText("A 库的回答")).toHaveCount(0);
});

test("a delayed A transcript cannot reappear after the same conversation switches to B", async ({ page }) => {
  let releaseA: (() => void) | undefined;
  const aGate = new Promise<void>((resolve) => { releaseA = resolve; });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { data: [], meta: {} } }));
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", async (route) => {
    await aGate;
    await route.fulfill({ json: { data: [{ id: "old-a", role: "assistant", content: "只属于 A 的旧回答" }], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", (route) => route.fulfill({ json: { data: {
    id: "conv-1", title: "会话", knowledge_base_scope: ["kb-b"], document_scope: [],
  }, meta: { request_id: "r" } } }));

  const oldRequest = page.waitForRequest("**/api/v1/conversations/conv-1/messages");
  await page.goto("/");
  await page.locator(".conversation-item").first().click();
  await oldRequest;
  await expect(page.locator(".composer-scope")).toHaveText("A");
  await page.locator(".scope-trigger").click();
  await page.locator(".scope-options").getByRole("button", { name: "database B", exact: true }).click();
  await expect(page.locator(".composer-scope")).toHaveText("B");
  const oldResponse = page.waitForResponse("**/api/v1/conversations/conv-1/messages");
  releaseA?.();
  await oldResponse;
  await page.waitForTimeout(150);
  await expect(page.getByText("只属于 A 的旧回答")).toHaveCount(0);
});

test("selecting another history conversation clears the old transcript before its load finishes", async ({ page }) => {
  let releaseSecond: (() => void) | undefined;
  const secondGate = new Promise<void>((resolve) => { releaseSecond = resolve; });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { data: [], meta: {} } }));
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话一", knowledge_base_scope: ["kb-a"], document_scope: [] },
    { id: "conv-2", title: "会话二", knowledge_base_scope: ["kb-a"], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => route.fulfill({ json: { data: [{ id: "m-1", role: "assistant", content: "会话一旧内容" }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-2/messages", async (route) => { await secondGate; await route.fulfill({ json: { data: [{ id: "m-2", role: "assistant", content: "会话二当前内容" }], meta: { request_id: "r" } } }); });

  await page.goto("/");
  await page.locator(".conversation-item").first().click();
  await expect(page.getByText("会话一旧内容")).toBeVisible();
  const secondRequest = page.waitForRequest("**/api/v1/conversations/conv-2/messages");
  await page.getByRole("button", { name: "会话二" }).click();
  await secondRequest;
  await expect(page.getByText("会话一旧内容")).toHaveCount(0);
  releaseSecond?.();
  await expect(page.getByText("会话二当前内容")).toBeVisible();
});

test("a new conversation draft creates on send without an empty history reload", async ({ page }) => {
  let historyReads = 0;
  let releaseNew: (() => void) | undefined;
  const newGate = new Promise<void>((resolve) => { releaseNew = resolve; });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { data: [], meta: {} } }));
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 201, json: { data: { id: "conv-new", title: "新对话", knowledge_base_scope: ["kb-a"], document_scope: [] }, meta: { request_id: "r" } } })
    : route.fulfill({ json: { data: [{ id: "conv-old", title: "旧会话", knowledge_base_scope: ["kb-a"], document_scope: [] }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-old/messages", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-new/messages", async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 201, json: { data: { run_id: "", answer: "新会话回答", citations: [] }, meta: { request_id: "r" } } });
    historyReads++;
    await newGate;
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "新建会话" }).click();
  await expect(page.locator(".conversation-item.selected")).toHaveCount(0);
  await page.getByLabel("输入问题").fill("新会话问题");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator(".message-body").getByText("新会话问题")).toBeVisible();
  await expect(page.locator(".message-body").getByText("新会话回答")).toBeVisible();
  releaseNew?.();
  await page.waitForTimeout(150);
  await expect(page.locator(".message-body").getByText("新会话问题")).toBeVisible();
  await expect(page.locator(".message-body").getByText("新会话回答")).toBeVisible();
  await expect.poll(() => historyReads).toBe(0);
});

test("implicit conversation creation skips history reload and retains its answer", async ({ page }) => {
  let historyReads = 0;
  let releaseNew: (() => void) | undefined;
  const newGate = new Promise<void>((resolve) => { releaseNew = resolve; });
  await page.route("**/api/v1/**", route => route.fulfill({ json: { data: [], meta: {} } }));
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [{ id: "kb-a", name: "A", graph_enabled: false }], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.request().method() === "POST"
    ? route.fulfill({ status: 201, json: { data: { id: "conv-first", title: "新对话", knowledge_base_scope: ["kb-a"], document_scope: [] }, meta: { request_id: "r" } } })
    : route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-first/messages", async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 201, json: { data: { run_id: "", answer: "首次回答", citations: [] }, meta: { request_id: "r" } } });
    historyReads++;
    await newGate;
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("输入问题").fill("首次问题");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator(".message-body").getByText("首次问题")).toBeVisible();
  await expect(page.locator(".message-body").getByText("首次回答")).toBeVisible();
  releaseNew?.();
  await page.waitForTimeout(150);
  await expect(page.locator(".message-body").getByText("首次问题")).toBeVisible();
  await expect(page.locator(".message-body").getByText("首次回答")).toBeVisible();
  await expect.poll(() => historyReads).toBe(0);
});
