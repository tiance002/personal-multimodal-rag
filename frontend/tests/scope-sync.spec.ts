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

test("a delayed knowledge-base PATCH blocks sending until the server scope changes", async ({ page }) => {
  let releasePatch: (() => void) | undefined;
  const patchGate = new Promise<void>((resolve) => { releasePatch = resolve; });
  let serverScope = "kb-a";
  const sentScopes: string[] = [];
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话", knowledge_base_scope: [serverScope], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => {
    if (route.request().method() === "POST") {
      sentScopes.push(serverScope);
      return route.fulfill({ json: { data: { run_id: "", answer: "回答", citations: [] }, meta: { request_id: "r" } } });
    }
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", async (route) => {
    await patchGate;
    serverScope = "kb-b";
    await route.fulfill({ json: { data: { id: "conv-1", title: "会话", knowledge_base_scope: [serverScope], document_scope: [] }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("输入问题").fill("新库里的内容是什么？");
  await page.locator("#kb-select").selectOption("kb-b");
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(sentScopes).toEqual([]);
  releasePatch?.();
  await expect(page.getByRole("button", { name: "发送问题" })).toBeEnabled();
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect.poll(() => sentScopes).toEqual(["kb-b"]);
});

test("a delayed document-scope PATCH blocks sending and failure resyncs the actual server scope", async ({ page }) => {
  let releasePatch: (() => void) | undefined;
  const patchGate = new Promise<void>((resolve) => { releasePatch = resolve; });
  let reads = 0;
  let serverDocumentScope: string[] = [];
  let sends = 0;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => {
    reads++;
    return route.fulfill({ json: { data: [
      { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: serverDocumentScope },
    ], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/knowledge-bases/kb-a/documents", (route) => route.fulfill({ json: { data: [
    { id: "doc-1", file_name: "guide.md", media_type: "text/markdown", index_status: "ready", version_no: 1 },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => {
    if (route.request().method() === "POST") sends++;
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", async (route) => {
    await patchGate;
    serverDocumentScope = ["doc-1"];
    await route.fulfill({ status: 500, json: { error: { code: "PATCH_FAILED", message: "network response lost" }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("输入问题").fill("只问这份资料");
  await page.getByRole("button", { name: "仅此文档" }).click();
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(sends).toBe(0);
  releasePatch?.();
  await expect.poll(() => reads).toBe(2);
  await expect(page.getByRole("button", { name: "取消限定" })).toBeVisible();
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
});

test("a failed knowledge-base PATCH restores the server's old scope but keeps questions blocked", async ({ page }) => {
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => {
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", (route) => route.fulfill({ status: 500, json: { error: { code: "PATCH_FAILED", message: "未写入" }, meta: { request_id: "r" } } }));

  await page.goto("/");
  await page.getByLabel("输入问题").fill("当前库是哪一个？");
  await page.locator("#kb-select").selectOption("kb-b");
  await expect(page.locator("#kb-select")).toHaveValue("kb-a");
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
});

test("an unreadable server scope fails closed after PATCH failure", async ({ page }) => {
  let rejectScopeRead = false;
  let sends = 0;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => {
    if (rejectScopeRead) return route.fulfill({ status: 503, json: { error: { code: "DATABASE_UNAVAILABLE", message: "unavailable" }, meta: { request_id: "r" } } });
    return route.fulfill({ json: { data: [
      { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: [] },
    ], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => {
    if (route.request().method() === "POST") sends++;
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", (route) => {
    rejectScopeRead = true;
    return route.fulfill({ status: 500, json: { error: { code: "PATCH_FAILED", message: "unknown" }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("输入问题").fill("不能泄露的问题");
  await page.locator("#kb-select").selectOption("kb-b");
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
  await expect(page.getByText("无法确认服务端检索范围，请刷新页面后再提问")).toBeVisible();
  expect(sends).toBe(0);
});

test("a failed PATCH that commits after resync keeps questions blocked", async ({ page }) => {
  let patchFailed = false;
  let readsAfterFailure = 0;
  let sends = 0;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => {
    if (patchFailed) readsAfterFailure++;
    return route.fulfill({ json: { data: [
      { id: "conv-1", title: "会话", knowledge_base_scope: ["kb-a"], document_scope: [] },
    ], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => {
    if (route.request().method() === "POST") sends++;
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", (route) => {
    patchFailed = true;
    return route.fulfill({ status: 500, json: { error: { code: "PATCH_FAILED", message: "write may still commit" }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("输入问题").fill("仍然可能写入的问题");
  await page.locator("#kb-select").selectOption("kb-b");
  await expect.poll(() => readsAfterFailure).toBeGreaterThan(0);
  await expect(page.locator("#kb-select")).toHaveValue("kb-a");
  await expect(page.getByRole("button", { name: "发送问题" })).toBeDisabled();
  expect(sends).toBe(0);
});

test("an in-flight question cannot be moved to another knowledge base", async ({ page }) => {
  let releaseAnswer: (() => void) | undefined;
  const answerGate = new Promise<void>((resolve) => { releaseAnswer = resolve; });
  let serverScope = "kb-a";
  let sentScope: string | null = null;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话", knowledge_base_scope: [serverScope], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
    await answerGate;
    sentScope = serverScope;
    return route.fulfill({ status: 201, json: { data: { run_id: "", answer: "回答", citations: [] }, meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/conversations/conv-1", (route) => {
    serverScope = "kb-b";
    return route.fulfill({ json: { data: { id: "conv-1", title: "会话", knowledge_base_scope: [serverScope], document_scope: [] }, meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("输入问题").fill("只问 A");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator("#kb-select")).toBeDisabled();
  releaseAnswer?.();
  await expect(page.locator("#kb-select")).toBeEnabled();
  expect(sentScope).toBe("kb-a");
});

test("conversation creation in flight keeps the selected knowledge base fixed", async ({ page }) => {
  let releaseCreate: (() => void) | undefined;
  const createGate = new Promise<void>((resolve) => { releaseCreate = resolve; });
  let expectedScope: string[] | null = null;
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
    await createGate;
    return route.fulfill({ status: 201, json: { data: { id: "conv-1", title: "新对话", knowledge_base_scope: ["kb-a"], document_scope: [] }, meta: { request_id: "r" } } });
  });
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => {
    if (route.request().method() === "POST") {
      expectedScope = (route.request().postDataJSON() as { expected_knowledge_base_scope: string[] }).expected_knowledge_base_scope;
      return route.fulfill({ status: 201, json: { data: { run_id: "", answer: "A", citations: [] }, meta: { request_id: "r" } } });
    }
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await expect(page.locator("#kb-select")).toHaveValue("kb-a");
  await page.getByLabel("输入问题").fill("A 的问题");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator("#kb-select")).toBeDisabled();
  releaseCreate?.();
  await expect.poll(() => expectedScope).toEqual(["kb-a"]);
  await expect(page.locator("#kb-select")).toHaveValue("kb-a");
});

test("a server scope conflict reselects the actual knowledge base and blocks another send", async ({ page }) => {
  let serverScope = "kb-a";
  await page.route("**/api/v1/knowledge-bases", (route) => route.fulfill({ json: { data: [
    { id: "kb-a", name: "A", graph_enabled: false }, { id: "kb-b", name: "B", graph_enabled: false },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations", (route) => route.fulfill({ json: { data: [
    { id: "conv-1", title: "会话", knowledge_base_scope: [serverScope], document_scope: [] },
  ], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", (route) => route.fulfill({ json: { data: [], meta: { request_id: "r" } } }));
  await page.route("**/api/v1/conversations/conv-1/messages", (route) => {
    if (route.request().method() === "POST") {
      serverScope = "kb-b";
      return route.fulfill({ status: 409, json: { error: { code: "CONVERSATION_SCOPE_CHANGED", message: "scope changed" }, meta: { request_id: "r" } } });
    }
    return route.fulfill({ json: { data: [], meta: { request_id: "r" } } });
  });

  await page.goto("/");
  await page.getByLabel("输入问题").fill("只问 A");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator("#kb-select")).toHaveValue("kb-b");
  await expect(page.getByLabel("输入问题")).toBeDisabled();
});
