import { expect, test } from "@playwright/test";

const envelope = (data: unknown) => ({ data, meta: { request_id: "api-request" } });
const roleRows = ["embedding", "rerank", "cheap", "expensive", "vlm"].map(role => ({
  role, provider: "local", model: `${role}-model`, base_url: "http://127.0.0.1:9999/v1",
  key_configured: role === "cheap",
}));

async function installCatalogRoutes(page: import("@playwright/test").Page, options: { implementation?: string } = {}) {
  await page.route("**/api/v1/runtime", route => route.fulfill({ json: envelope({ implementation: options.implementation ?? "weknora-python-port", api_version: "v1" }) }));
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: envelope([
    { id: "kb-1", name: "课程资料", cloud_allowed: false, document_count: 1 },
  ]) }));
  await page.route("**/api/v1/knowledge-bases/kb-1/documents", route => route.fulfill({ json: envelope([
    { id: "doc-1", knowledge_base_id: "kb-1", filename: "教材 第三章.pdf", status: "ready" },
  ]) }));
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope([]) }));
  await page.route("**/api/v1/settings/model-configuration", route => route.fulfill({ json: envelope({ revision: "rev-1", roles: roleRows }) }));
}

function frame(event: Record<string, unknown>) {
  return `event: message\ndata: ${JSON.stringify(event)}\n\n`;
}

test("port adapter loads visible scope and five masked model roles without old app fallback", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "从你的资料中寻找答案" })).toBeVisible();
  await expect(page.getByText("课程资料", { exact: true }).first()).toBeVisible();
  await page.getByText("课程资料", { exact: true }).first().click();
  await expect(page.getByText("教材 第三章.pdf")).toBeVisible();
  await expect(page.getByRole("button", { name: /智能推理/ })).toBeDisabled();
  await page.getByRole("button", { name: "模型设置" }).click();
  for (const role of ["Embedding", "Rerank", "Quick answer", "Smart answer", "Vision"]) {
    await expect(page.getByText(role, { exact: true })).toBeVisible();
  }
  await expect(page.getByText("API 密钥只写入，不会从服务端读回")).toBeVisible();
});

test("POST once and recover by offset-zero GET of the same assistant message without duplicate answer text", async ({ page }) => {
  await installCatalogRoutes(page);
  let posts = 0;
  let gets = 0;
  let postBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") {
      await route.fulfill({ json: envelope({ id: "session-1", title: "测试问题", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" }) });
      return;
    }
    await route.fulfill({ json: envelope([]) });
  });
  await page.route("**/api/v1/sessions/session-1/messages", async route => {
    if (route.request().method() === "POST") {
      posts++;
      postBody = route.request().postDataJSON() as Record<string, unknown>;
      const body = frame({ id: "request-1", response_type: "agent_query", content: "", done: false,
        data: { request_id: "request-1", session_id: "session-1", assistant_message_id: "assistant-1" } }) +
        frame({ id: "request-1", response_type: "answer", content: "Hello", done: false, data: { event_id: "answer-1" } });
      await route.fulfill({ status: 200, contentType: "text/event-stream", body });
      return;
    }
    await route.fulfill({ json: envelope([]) });
  });
  await page.route("**/api/v1/sessions/continue-stream/session-1?message_id=assistant-1", async route => {
    gets++;
    const body = frame({ id: "request-1", response_type: "agent_query", content: "", done: false,
      data: { request_id: "request-1", session_id: "session-1", assistant_message_id: "assistant-1" } }) +
      frame({ id: "request-1", response_type: "answer", content: "Hello", done: false, data: { event_id: "answer-1" } }) +
      frame({ id: "request-1", response_type: "answer", content: " world", done: true, data: { event_id: "answer-1" } }) +
      frame({ id: "request-1", response_type: "complete", content: "", done: false, data: { final_content: "Hello world" } });
    await route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });

  await page.goto("/");
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("What does chapter three explain?");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("Hello world")).toBeVisible();
  await expect(page.getByText("回答已由服务端完成并保存")).toBeVisible();
  expect(posts).toBe(1);
  expect(gets).toBe(1);
  expect(postBody?.mode).toBe("quick");
  expect(postBody?.knowledge_base_scope).toEqual(["kb-1"]);
});

test("runtime identity mismatch blocks mutations and remains visibly isolated from the existing backend", async ({ page }) => {
  await installCatalogRoutes(page, { implementation: "legacy-rag" });
  let postCount = 0;
  await page.route("**/api/v1/knowledge-bases", async route => {
    if (route.request().method() === "POST") postCount++;
    await route.fulfill({ json: envelope([]) });
  });
  await page.goto("/");
  await expect(page.getByRole("alert").getByText(/身份不匹配/)).toBeVisible();
  await expect(page.getByPlaceholder("先从上方选择知识库")).toBeDisabled();
  expect(postCount).toBe(0);
});

test("runtime mismatch during a run is shown in its assistant bubble and never starts generation", async ({ page }) => {
  await installCatalogRoutes(page);
  let runtimeReads = 0;
  let generationPosts = 0;
  await page.route("**/api/v1/runtime", route => {
    runtimeReads++;
    const implementation = runtimeReads <= 2 ? "weknora-python-port" : "legacy-rag";
    return route.fulfill({ json: envelope({ implementation, api_version: "v1" }) });
  });
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") {
      await route.fulfill({ json: envelope({ id: "session-2", title: "Scope", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" }) });
    } else await route.fulfill({ json: envelope([]) });
  });
  await page.route("**/api/v1/sessions/session-2/messages", async route => {
    if (route.request().method() === "POST") generationPosts++;
    await route.fulfill({ status: 409, json: { error: { code: "RUNTIME_IDENTITY_MISMATCH", message: "身份不匹配" } } });
  });
  await page.goto("/");
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("Question");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator(".wp-message.assistant.error").getByText(/身份不匹配/)).toBeVisible();
  expect(generationPosts).toBe(0);
});

test("library selection reads its KB scope and upload polling exposes terminal completion and error codes", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: envelope([
    { id: "kb-1", name: "课程资料", cloud_allowed: false, document_count: 1 },
    { id: "kb-2", name: "图片资料", cloud_allowed: false, document_count: 1 },
  ]) }));
  const documentReads: string[] = [];
  let uploadReads = 0;
  let uploaded = false;
  await page.route("**/api/v1/knowledge-bases/*/documents", async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      uploaded = true;
      await route.fulfill({ json: envelope({ id: "doc-upload", knowledge_base_id: "kb-2", filename: "figure.png", status: "pending" }) });
      return;
    }
    documentReads.push(path);
    if (path.endsWith("/kb-1/documents")) {
      await route.fulfill({ json: envelope([{ id: "doc-1", knowledge_base_id: "kb-1", filename: "chapter.pdf", status: "completed" }]) });
      return;
    }
    if (!uploaded) {
      await route.fulfill({ json: envelope([]) });
      return;
    }
    uploadReads++;
    const rows = uploadReads === 1
      ? [{ id: "doc-upload", knowledge_base_id: "kb-2", filename: "figure.png", status: "pending" }]
      : [{ id: "doc-upload", knowledge_base_id: "kb-2", filename: "figure.png", status: "completed" },
        { id: "doc-failed", knowledge_base_id: "kb-2", filename: "bad-image.png", status: "failed", error_code: "OCR_UNAVAILABLE" }];
    await route.fulfill({ json: envelope(rows) });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "资料库" }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await expect(page.getByText("chapter.pdf", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^图片资料/ }).click();
  await expect(page.getByText("chapter.pdf", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("combobox")).toHaveValue("kb-2");
  await expect(page.getByText("资料文件", { exact: true })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({ name: "figure.png", mimeType: "image/png", buffer: Buffer.from("image") });
  await expect(page.getByText("figure.png", { exact: true })).toBeVisible();
  await expect(page.getByText(/completed/)).toBeVisible();
  await expect(page.getByText(/OCR_UNAVAILABLE/)).toBeVisible();
  expect(documentReads.some(path => path.endsWith("/kb-1/documents"))).toBe(true);
  expect(documentReads.some(path => path.endsWith("/kb-2/documents"))).toBe(true);
  expect(uploadReads).toBeGreaterThanOrEqual(2);
});

test("citation drawer renders scoped version, chunk position and image OCR/caption metadata", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") {
      await route.fulfill({ json: envelope({ id: "session-cite", title: "引用", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" }) });
    } else await route.fulfill({ json: envelope([]) });
  });
  const citedAnswer = '参见 <kb chunk_id="chunk-image" doc="结构图" />。';
  await page.route("**/api/v1/sessions/session-cite/messages", route => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: frame({ id: "request-cite", response_type: "agent_query", content: "", done: false,
      data: { request_id: "request-cite", session_id: "session-cite", assistant_message_id: "assistant-cite" } }) +
      frame({ id: "request-cite", response_type: "answer", content: citedAnswer, done: true, data: { event_id: "answer-cite" } }) +
      frame({ id: "request-cite", response_type: "complete", content: "", done: false, data: { final_content: citedAnswer } }),
  }));
  await page.route("**/api/v1/chunks/chunk-image", route => route.fulfill({ json: envelope({
    knowledge_base_id: "kb-1", document_id: "doc-1", chunk_id: "chunk-image", filename: "结构图.pdf", text: "图中展示模块关系",
    source_url: "/api/v1/source", source_version: "version-7", chunk_type: "image_caption", chunk_index: 4,
    start: 120, end: 168, page_number: 3,
    image_info: [{ page_number: 3, source_type: "embedded_image", caption: "结构依赖关系", ocr_text: "入口 服务 数据库",
      ocr_status: { status: "completed" }, caption_status: { status: "failed", error: "CAPTION_TIMEOUT" } }],
  }) }));

  await page.goto("/");
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("问题");
  await page.getByRole("button", { name: "发送" }).click();
  await page.getByRole("button", { name: "结构图" }).click();
  await expect(page.getByText("kb-1", { exact: true })).toBeVisible();
  await expect(page.getByText("version-7")).toBeVisible();
  await expect(page.getByText("第 4 段 · 字符 120–168 · 第 3 页")).toBeVisible();
  await expect(page.getByText("embedded_image")).toBeVisible();
  await expect(page.getByText("OCR 文本：入口 服务 数据库")).toBeVisible();
  await expect(page.getByText("图片说明：failed · CAPTION_TIMEOUT")).toBeVisible();
});
