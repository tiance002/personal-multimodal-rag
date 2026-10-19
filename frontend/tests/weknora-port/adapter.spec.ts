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

for (const scenario of [
  { name: "truncated", text: "保留尚未写完的回答", finish_reason: "length", generation_issues: ["TRUNCATED"], label: "输出被截断；已有内容已保存，未重新生成。" },
  { name: "empty", text: "", finish_reason: "stop", generation_issues: ["EMPTY"], label: "模型未返回可用答案；运行终态已保存，未重新生成。" },
  { name: "selected-final", text: "升级后完整回答", finish_reason: "stop", generation_issues: [], label: "回答已由服务端完成并保存" },
]) {
  test(`live ${scenario.name} shows final output status without retry`, async ({ page }) => {
    await installCatalogRoutes(page);
    let posts = 0;
    let resumes = 0;
    await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope(route.request().method() === "POST"
      ? { id: "status-session", title: "状态测试", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" } : []) }));
    await page.route("**/api/v1/sessions/status-session/messages", async route => {
      if (route.request().method() !== "POST") return route.fulfill({ json: envelope([]) });
      posts++;
      const body = frame({ id: "status-run", response_type: "agent_query", content: "", done: false,
        data: { request_id: "status-run", session_id: "status-session", assistant_message_id: "status-answer" } }) +
        frame({ id: "status-run", response_type: "answer", content: scenario.text, done: true, data: { event_id: "final-answer" } }) +
        frame({ id: "status-run", response_type: "complete", content: "", done: false,
          data: { finish_reason: scenario.finish_reason, generation_issues: scenario.generation_issues,
            truncated: scenario.name === "truncated", final_content: scenario.text,
            attempts: [{ objective: ["TRUNCATED"] }, { objective: scenario.generation_issues }] } });
      return route.fulfill({ status: 200, contentType: "text/event-stream", body });
    });
    await page.route("**/api/v1/sessions/continue-stream/**", route => { resumes++; return route.fulfill({ status: 500 }); });
    await page.goto("/");
    await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
    await page.getByText("课程资料", { exact: true }).first().click();
    await page.getByPlaceholder("询问当前资料…").fill("状态测试");
    await page.getByRole("button", { name: "发送" }).click();
    const bubble = page.locator(".wp-message.assistant");
    await expect(bubble.getByText(scenario.label, { exact: true })).toBeVisible();
    await expect(bubble.locator(".wp-message-content")).toHaveText(scenario.text);
    if (scenario.name !== "selected-final") await expect(bubble.getByText("回答已由服务端完成并保存", { exact: true })).toHaveCount(0);
    expect(posts).toBe(1);
    expect(resumes).toBe(0);
  });

  test(`history ${scenario.name} restores persisted final output status`, async ({ page }) => {
    await installCatalogRoutes(page);
    let mutations = 0;
    await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope([
      { id: "status-history", title: "保存的状态", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
    ]) }));
    await page.route("**/api/v1/sessions/status-history/messages", route => {
      if (route.request().method() !== "GET") mutations++;
      return route.fulfill({ json: envelope([
        { id: "saved-status", role: "assistant", content: scenario.text, request_id: "saved-status-run", created_at: "now",
          is_completed: true, turn_status: "completed", finish_reason: scenario.finish_reason, generation_issues: scenario.generation_issues },
      ]) });
    });
    await page.goto("/");
    await page.getByRole("button", { name: /保存的状态/ }).click();
    const bubble = page.locator(".wp-message.assistant");
    await expect(bubble.getByText(scenario.label, { exact: true })).toBeVisible();
    await expect(bubble.locator(".wp-message-content")).toHaveText(scenario.text);
    if (scenario.name !== "selected-final") await expect(bubble.getByText("回答已由服务端完成并保存", { exact: true })).toHaveCount(0);
    expect(mutations).toBe(0);
  });
}

test("port adapter loads visible scope and five masked model roles without old app fallback", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "从你的资料中寻找答案" })).toBeVisible();
  await expect(page.getByText("选择检索范围，快速问答会显示实际检索与回答进度。")).toBeVisible();
  await expect(page.getByText("选择检索范围，快速问答会显示服务端实际发送的执行事件与引用。")).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "选择知识库和文件" })).toHaveCount(0);
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
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
  let answerLanguage = "";
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
      answerLanguage = route.request().headers()["accept-language"];
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
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("What does chapter three explain?");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("Hello world")).toBeVisible();
  await expect(page.getByText("回答已由服务端完成并保存")).toBeVisible();
  expect(posts).toBe(1);
  expect(gets).toBe(1);
  expect(postBody?.mode).toBe("quick");
  expect(postBody?.reasoning_effort).toBe("off");
  expect(postBody).not.toHaveProperty("accept_language");
  expect(answerLanguage).toBe("zh-CN");
  expect(postBody?.knowledge_base_scope).toEqual(["kb-1"]);
});

test("answer language defaults to Simplified Chinese and persists an explicit selection for normal submissions", async ({ page }) => {
  await installCatalogRoutes(page);
  let posts = 0;
  let postBody: Record<string, unknown> | undefined;
  let answerLanguage = "";
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope(route.request().method() === "POST"
    ? { id: "language-session", title: "语言设置", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" } : []) }));
  await page.route("**/api/v1/sessions/language-session/messages", route => {
    if (route.request().method() !== "POST") return route.fulfill({ json: envelope([]) });
    posts++;
    postBody = route.request().postDataJSON() as Record<string, unknown>;
    answerLanguage = route.request().headers()["accept-language"];
    return route.fulfill({ contentType: "text/event-stream", body:
      frame({ id: "language-run", response_type: "agent_query", content: "", done: false,
        data: { request_id: "language-run", session_id: "language-session", assistant_message_id: "language-answer" } }) +
      frame({ id: "language-run", response_type: "answer", content: "The card reading is 37.", done: true, data: { event_id: "language-final" } }) +
      frame({ id: "language-run", response_type: "complete", content: "", done: false, data: { final_content: "The card reading is 37.", finish_reason: "stop" } }),
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "模型设置" }).click();
  const language = page.getByRole("combobox", { name: "回答语言", exact: true });
  await expect(language).toHaveValue("zh-CN");
  await expect(language.locator("option")).toHaveText(["简体中文", "繁體中文", "English", "日本語", "한국어"]);
  await language.selectOption("en-US");
  await expect(page.getByText("回答语言已更新，仅保存在此浏览器。", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "模型设置" }).click();
  await expect(language).toHaveValue("en-US");
  await page.getByRole("button", { name: "问答", exact: true }).click();
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("卡片中的读数是多少？");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("The card reading is 37.", { exact: true })).toBeVisible();
  expect(posts).toBe(1);
  expect(answerLanguage).toBe("en-US");
  expect(postBody?.reasoning_effort).toBe("off");
  expect(postBody?.knowledge_base_scope).toEqual(["kb-1"]);
  expect(postBody?.content).toBe("卡片中的读数是多少？");
  expect(postBody).not.toHaveProperty("accept_language");
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
  await expect(page.getByPlaceholder("选择知识库后提问…")).toBeDisabled();
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
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
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
  await expect(page.getByRole("link", { name: "查看原文件" })).toHaveAttribute("href", /knowledge-bases\/kb-1\/documents\/doc-1\/source$/);
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

test("normal answers suppress citation controls and strip internal SSE markup outside code", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") {
      await route.fulfill({ json: envelope({ id: "session-cite", title: "回答", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" }) });
    } else await route.fulfill({ json: envelope([]) });
  });
  const answer = '前文 <ref>E1</ref> <kb chunk_id="chunk-image" doc="结构图" /> <web url="https://example.test" /> 后文\n\n行内代码 `<ref>keep</ref>`\n\n```html\n<ref>keep</ref>\n<kb chunk_id="code-chunk" doc="样例" />\n```\n\n[链接](https://example.test/path) ![图](image.png)';
  let chunkReads = 0;
  await page.route("**/api/v1/sessions/session-cite/messages", route => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: frame({ id: "request-cite", response_type: "agent_query", content: "", done: false,
      data: { request_id: "request-cite", session_id: "session-cite", assistant_message_id: "assistant-cite" } }) +
      frame({ id: "request-cite", response_type: "answer", content: answer, done: true, data: { event_id: "answer-cite" } }) +
      frame({ id: "request-cite", response_type: "complete", content: "", done: false, data: { final_content: answer } }),
  }));
  await page.route("**/api/v1/chunks/**", route => { chunkReads++; return route.fulfill({ json: envelope({}) }); });

  await page.goto("/");
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("问题");
  await page.getByRole("button", { name: "发送" }).click();
  const renderedAnswer = page.locator(".wp-message.assistant .wp-message-content");
  await expect(renderedAnswer).toContainText("前文 E1  后文");
  await expect(renderedAnswer.locator("p > code")).toHaveText("<ref>keep</ref>");
  await expect(renderedAnswer).toContainText("<ref>keep</ref>");
  await expect(renderedAnswer).toContainText("<kb chunk_id=\"code-chunk\" doc=\"样例\" />");
  await expect(renderedAnswer.getByRole("link", { name: "链接" })).toHaveAttribute("href", "https://example.test/path");
  await expect(renderedAnswer.locator("img[alt='图']")).toHaveAttribute("src", "image.png");
  await expect(page.locator(".wp-citation, .wp-source-drawer")).toHaveCount(0);
  expect(chunkReads).toBe(0);
});

test("old history strips internal reference markers without changing inline or fenced code", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope([
    { id: "old-session", title: "旧回答", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
  ]) }));
  await page.route("**/api/v1/sessions/old-session/messages", route => route.fulfill({ json: envelope([
    { id: "old-answer", role: "assistant", request_id: "old-run", created_at: "now", is_completed: true,
      content: '历史 <ref>原始引文</ref> <kb chunk_id="old-chunk" doc="资料" /> <kb>块文字</kb> <web id="w">网页文字</web> <reference>普通标签</reference> 末尾\n\n**Source:** *11-21-16-Updated-Post-Election-Release* (document d1, chunk c1)\nSource: section c1 and dataset d1.\n\n**依据说明：**\n上述数据直接来源于文档 `d1` 中的第2段（chunk `c2`），该段明确列出了对应线性方程组的系数矩阵和增广矩阵。\n\n普通正文提到 d1 与 c2。\n\n**依据说明：**\n这里是普通解释，提到 c1/d1 但不符合来源句式。\n\n```text\n依据说明：\n上述数据直接来源于文档 `d1` 中的第2段（chunk `c2`）\n```\n\n`<ref>code</ref>`\n\n```txt\n<ref>code</ref>\nSource: code (document d1, chunk c1)\n```\n\n<ref id=' },
  ]) }));

  await page.goto("/");
  await page.getByRole("button", { name: /旧回答/ }).click();
  const renderedAnswer = page.locator(".wp-message.assistant .wp-message-content");
  await expect(renderedAnswer).toContainText("历史 原始引文");
  await expect(renderedAnswer).toContainText("块文字 网页文字 <reference>普通标签</reference> 末尾");
  await expect(renderedAnswer.locator("p").filter({ hasText: "document d1, chunk c1" })).toHaveCount(0);
  await expect(renderedAnswer.locator("p").filter({ hasText: "上述数据直接来源于文档" })).toHaveCount(0);
  await expect(renderedAnswer).toContainText("普通正文提到 d1 与 c2。");
  await expect(renderedAnswer).toContainText("这里是普通解释，提到 c1/d1 但不符合来源句式。");
  await expect(renderedAnswer.locator("pre code").filter({ hasText: "上述数据直接来源于文档" })).toContainText("上述数据直接来源于文档 `d1` 中的第2段（chunk `c2`）");
  await expect(renderedAnswer).toContainText("Source: section c1 and dataset d1.");
  await expect(renderedAnswer).toContainText("Source: code (document d1, chunk c1)");
  await expect(renderedAnswer).not.toContainText("<ref id=");
  await expect(renderedAnswer.locator("p > code")).toHaveText("<ref>code</ref>");
  await expect(renderedAnswer.locator("pre code").filter({ hasText: "Source: code" })).toContainText("<ref>code</ref>");
  await expect(renderedAnswer).toContainText("<ref>code</ref>");
  await expect(renderedAnswer).not.toContainText("<ref id=");
  await expect(page.locator(".wp-citation, .wp-source-drawer")).toHaveCount(0);
});

test("history titles stay inside the sidebar and remain selectable by mouse and Enter", async ({ page }) => {
  await installCatalogRoutes(page);
  const longChinese = "关于视觉检索与正文引用关系的历史会话标题".repeat(8);
  const longEnglish = "A long English title for reviewing source evidence and saved answers ".repeat(3);
  const continuous = "x".repeat(140);
  const sessions = [
    { id: "cjk", title: longChinese, knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
    { id: "english", title: longEnglish, knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
    { id: "continuous", title: continuous, knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
    { id: "ordinary", title: "普通会话", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
    ...Array.from({ length: 48 }, (_, index) => ({ id: `extra-${index}`, title: `第${index}个中文历史会话 Mixed English title`, knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" })),
  ];
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope(sessions) }));
  await page.route("**/api/v1/sessions/*/messages", route => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2);
    return route.fulfill({ json: envelope([
      { id: `message-${id}`, role: "assistant", request_id: `run-${id}`, created_at: "now", is_completed: true, content: `历史会话 ${id}` },
    ]) });
  });

  await page.goto("/");
  const sidebar = page.locator(".wp-sidebar");
  const historyButtons = page.locator(".wp-history-list button");
  await expect(historyButtons).toHaveCount(52);
  for (const button of await historyButtons.all()) {
    const dimensions = await button.evaluate(element => ({
      buttonWidth: element.getBoundingClientRect().width,
      sidebarWidth: element.closest(".wp-sidebar")!.getBoundingClientRect().width,
      titleWidth: element.querySelector("strong")!.clientWidth,
      titleScrollWidth: element.querySelector("strong")!.scrollWidth,
      buttonHeight: element.getBoundingClientRect().height,
      titleHeight: element.querySelector("strong")!.getBoundingClientRect().height,
      titleLineHeight: parseFloat(getComputedStyle(element.querySelector("strong")!).lineHeight),
      titleFontSize: parseFloat(getComputedStyle(element.querySelector("strong")!).fontSize),
      titleWhiteSpace: getComputedStyle(element.querySelector("strong")!).whiteSpace,
      titleOverflow: getComputedStyle(element.querySelector("strong")!).textOverflow,
      subtitleHeight: element.querySelector("small")!.getBoundingClientRect().height,
    }));
    expect(dimensions.buttonWidth).toBeLessThanOrEqual(dimensions.sidebarWidth);
    expect(dimensions.titleWidth).toBeGreaterThan(0);
    expect(dimensions.buttonHeight).toBeGreaterThanOrEqual(56);
    expect(dimensions.titleLineHeight).toBeGreaterThanOrEqual(dimensions.titleFontSize * 1.4);
    expect(dimensions.titleHeight).toBeGreaterThanOrEqual(dimensions.titleLineHeight);
    expect(dimensions.subtitleHeight).toBeGreaterThanOrEqual(14);
    expect(dimensions.buttonHeight).toBeGreaterThanOrEqual(dimensions.titleHeight + dimensions.subtitleHeight + 18);
    expect(dimensions.titleWhiteSpace).toBe("nowrap");
    expect(dimensions.titleOverflow).toBe("ellipsis");
  }
  expect(await historyButtons.nth(0).locator("strong").evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  expect(await historyButtons.nth(1).locator("strong").evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
  expect(await historyButtons.nth(2).locator("strong").evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);

  await historyButtons.nth(0).click();
  await expect(page.getByText("历史会话 cjk", { exact: true })).toBeVisible();
  await historyButtons.nth(3).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("历史会话 ordinary", { exact: true })).toBeVisible();
  await expect(historyButtons.nth(3)).toHaveClass(/selected/);
  await expect(sidebar).toBeVisible();
  expect(await page.locator(".wp-history-list").evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("ui-restoration-history.png"), fullPage: true });
});

test("history Markdown renders safely without leaking internal reference tags", async ({ page }) => {
  await installCatalogRoutes(page);
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC", "base64");
  await page.route("https://example.test/figure.png", route => route.fulfill({ contentType: "image/png", body: png }));
  const markdown = [
    "# 清洁步骤",
    "",
    "1. **关闭扫描仪**",
    "2. 使用无尘干布。",
    "",
    "普通说明文字。",
    "",
    "```python",
    "print('safe')",
    "```",
    "",
    "[维修手册](https://example.test/manual) ![示意图](https://example.test/figure.png)",
    "",
    "内部标记 <ref>原文证据</ref>",
    "",
    "![不支持的地址](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAA)",
    "",
    "<img src=x onerror=alert(1)>",
  ].join("\n");
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope([
    { id: "markdown-history", title: "格式验证", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
  ]) }));
  await page.route("**/api/v1/sessions/markdown-history/messages", route => route.fulfill({ json: envelope([
    { id: "markdown-answer", role: "assistant", request_id: "run-markdown", created_at: "now", is_completed: true, content: markdown },
  ]) }));

  await page.goto("/");
  await page.getByRole("button", { name: /格式验证/ }).click();
  const answer = page.locator(".wp-message.assistant .wp-message-content");
  await expect(answer.getByRole("heading", { name: "清洁步骤" })).toBeVisible();
  await expect(answer.locator("ol li strong")).toHaveText("关闭扫描仪");
  await expect(answer.getByText("普通说明文字。", { exact: true })).toBeVisible();
  await expect(answer.locator("pre code")).toHaveText("print('safe')");
  await expect(answer.getByRole("link", { name: "维修手册" })).toHaveAttribute("href", "https://example.test/manual");
  const image = answer.locator("img[alt='示意图']");
  await expect(image).toHaveAttribute("src", "https://example.test/figure.png");
  await expect(image).toHaveJSProperty("naturalWidth", 1);
  await expect(answer).toContainText("内部标记 原文证据");
  await expect(answer.locator("img[alt='不支持的地址']")).toHaveCount(0);
  await expect(answer.locator("img[onerror], script")).toHaveCount(0);
  await expect(page.locator(".wp-citation, .wp-source-drawer")).toHaveCount(0);
});

test("source asset images keep version and path in live answers and restored history", async ({ page }) => {
  await installCatalogRoutes(page);
  const asset = "/api/v1/knowledge-bases/kb-1/documents/doc-1/source?source_version=version-2&asset_path=images%2Fslide%201%2Bcard.png";
  const ordinary = "https://example.test/original.png?size=1";
  const markdown = `Card reading: 37.\n\n来源文件：service brief.pptx\n\n![来源图片](${asset})\n\n![普通图片](${ordinary})\n\n普通 c1/d1；代码 \`c1/d1\`。`;
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC", "base64");
  const assetRequests: string[] = [];
  let apiOrigin = "";
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/runtime") apiOrigin = url.origin;
  });
  let posts = 0;
  await page.route("**/api/v1/knowledge-bases/kb-1/documents/doc-1/source?**", route => {
    const url = new URL(route.request().url());
    assetRequests.push(`${url.pathname}${url.search}`);
    return route.fulfill({ contentType: "image/png", body: png });
  });
  await page.route(ordinary, route => route.fulfill({ contentType: "image/png", body: png }));
  const session = { id: "asset-session", title: "图片来源测试", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" };
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope(route.request().method() === "POST" ? session : posts ? [session] : []) }));
  await page.route("**/api/v1/sessions/asset-session/messages", route => {
    if (route.request().method() !== "POST") return route.fulfill({ json: envelope([
      { id: "asset-answer", role: "assistant", content: markdown, request_id: "asset-run", created_at: "now", is_completed: true, turn_status: "completed" },
    ]) });
    posts++;
    return route.fulfill({ contentType: "text/event-stream", body:
      frame({ id: "asset-run", response_type: "agent_query", content: "", done: false,
        data: { request_id: "asset-run", session_id: "asset-session", assistant_message_id: "asset-answer" } }) +
      frame({ id: "asset-run", response_type: "answer", content: markdown, done: true, data: { event_id: "asset-final" } }) +
      frame({ id: "asset-run", response_type: "complete", content: "", done: false, data: { final_content: markdown, finish_reason: "stop" } }),
    });
  });
  const checkImages = async () => {
    const answer = page.locator(".wp-message.assistant .wp-message-content");
    expect(apiOrigin).not.toBe("");
    await expect(answer.locator("img[alt='来源图片']")).toHaveJSProperty("src", `${apiOrigin}${asset}`);
    await expect(answer.locator("img[alt='来源图片']")).toHaveJSProperty("naturalWidth", 1);
    await expect(answer.locator("img[alt='普通图片']")).toHaveAttribute("src", ordinary);
    await expect(answer.locator("img[alt='普通图片']")).toHaveJSProperty("naturalWidth", 1);
    await expect(answer).toContainText("来源文件：service brief.pptx");
    await expect(answer).toContainText("普通 c1/d1");
    await expect(answer.locator("code")).toHaveText("c1/d1");
    await expect(page.locator(".wp-citation, .wp-source-drawer")).toHaveCount(0);
  };
  await page.goto("/");
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("What is on this card?");
  await page.getByRole("button", { name: "发送" }).click();
  await checkImages();
  await page.reload();
  await page.getByRole("button", { name: /图片来源测试/ }).click();
  await checkImages();
  expect(posts).toBe(1);
  expect(assetRequests.length).toBeGreaterThanOrEqual(2);
  expect(assetRequests.every(url => url === asset)).toBe(true);
});

test("partial SSE reference tags stay hidden until the delta is complete", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") {
      await route.fulfill({ json: envelope({ id: "stream-session", title: "流式问题", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" }) });
    } else await route.fulfill({ json: envelope([]) });
  });
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const requestUrl = new URL(typeof input === "string" ? input : input instanceof Request ? input.url : input.toString(), location.href);
      if (requestUrl.pathname !== "/api/v1/sessions/stream-session/messages" || init?.method !== "POST")
        return nativeFetch(input, init);
      const encoder = new TextEncoder();
      const event = (response_type: string, content: string, data: Record<string, string> = {}) =>
        `event: message\ndata: ${JSON.stringify({ id: "stream-request", response_type, content, done: response_type === "answer", data })}\n\n`;
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(event("agent_query", "", {
            request_id: "stream-request", session_id: "stream-session", assistant_message_id: "stream-assistant",
          })));
          controller.enqueue(encoder.encode(event("answer", "<re", { event_id: "answer" })));
          setTimeout(() => controller.enqueue(encoder.encode(event("answer",
            'f id="x">visible</ref> <reference>ordinary</reference> <kb>chunk</kb> <web>web</web> trailing </re',
            { event_id: "answer" }))), 900);
          setTimeout(() => {
            controller.enqueue(encoder.encode(event("answer", "f> compare a <", { event_id: "answer" })));
          }, 1400);
          setTimeout(() => {
            controller.enqueue(encoder.encode(event("complete", "", { final_content: "visible <reference>ordinary</reference> chunk web trailing  compare a <" })));
            controller.close();
          }, 1600);
        },
      });
      return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    };
  });

  await page.goto("/");
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("流式问题");
  await page.getByRole("button", { name: "发送" }).click();
  const renderedAnswer = page.locator(".wp-message.assistant .wp-message-content");
  await expect(page.locator(".wp-running")).toBeVisible();
  await expect(renderedAnswer).toBeEmpty();
  await expect(renderedAnswer).toContainText("visible <reference>ordinary</reference> chunk web trailing");
  const partialClosingText = await renderedAnswer.textContent();
  expect(partialClosingText?.endsWith("</re")).toBe(false);
  await expect(renderedAnswer).toContainText("compare a <");
  await expect(renderedAnswer).not.toContainText("<ref id=");
  await expect(renderedAnswer).not.toContainText("<kb");
  await expect(renderedAnswer).not.toContainText("<web");
});

test("SSE cloud error code stays specific after a failed complete event", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/sessions", async route => {
    if (route.request().method() === "POST") await route.fulfill({ json: envelope({ id: "cloud-session", title: "云端权限", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" }) });
    else await route.fulfill({ json: envelope([]) });
  });
  await page.route("**/api/v1/sessions/cloud-session/messages", route => route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    body: frame({ id: "run-cloud", response_type: "agent_query", content: "", done: false,
      data: { request_id: "run-cloud", session_id: "cloud-session", assistant_message_id: "assistant-cloud" } }) +
      frame({ id: "run-cloud", response_type: "error", content: "不安全的后端异常文本", done: false,
        data: { error_code: "MODEL_CLOUD_DISABLED" } }) +
      frame({ id: "run-cloud", response_type: "complete", content: "", done: false, data: { status: "failed" } }),
  }));

  await page.goto("/");
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await page.getByPlaceholder("询问当前资料…").fill("请回答");
  await page.getByRole("button", { name: "发送" }).click();
  const localized = "全局云端调用已关闭。请在 local.json 中明确授权后重启 wk，再重试。";
  await expect(page.locator(".wp-message.assistant.error .wp-message-content")).toContainText(localized);
  await expect(page.getByRole("status")).toContainText(localized);
  await expect(page.locator(".wp-message.assistant.error")).not.toContainText("不安全的后端异常文本");
});

test("history refresh localizes a saved turn error code even when content is empty", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/sessions", route => route.fulfill({ json: envelope([
    { id: "history-error", title: "外发受限", knowledge_base_scope: ["kb-1"], document_scope: [], updated_at: "now" },
  ]) }));
  await page.route("**/api/v1/sessions/history-error/messages", route => route.fulfill({ json: envelope([
    { id: "saved-error", role: "assistant", request_id: "saved-run", created_at: "now", is_completed: false,
      turn_status: "error", turn_error_code: "MODEL_ORIGIN_KB_EGRESS_DENIED", content: "" },
  ]) }));

  await page.goto("/");
  await page.getByRole("button", { name: /外发受限/ }).click();
  await expect(page.locator(".wp-message.assistant.error .wp-message-content")).toContainText("当前知识库不允许向云端外发内容。");
});

test("default cloud_allowed false is described as keeping KB contents local", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.goto("/");
  await page.getByRole("button", { name: "选择知识库和文件", exact: true }).click();
  await page.getByText("课程资料", { exact: true }).first().click();
  await expect(page.getByText("1 份资料 · 仅本地（该库内容不外发）", { exact: true })).toBeVisible();
});


test("composer scope picker defaults closed, filters multiple KBs and keeps saved session locked", async ({ page }) => {
  await installCatalogRoutes(page);
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: envelope([
    { id: "kb-1", name: "课程资料", cloud_allowed: false, document_count: 1 },
    { id: "kb-2", name: "图片资料", cloud_allowed: false, document_count: 1 },
  ]) }));
  await page.route("**/api/v1/knowledge-bases/kb-2/documents", route => route.fulfill({ json: envelope([
    { id: "doc-2", knowledge_base_id: "kb-2", filename: "产品图.png", status: "ready" },
  ]) }));
  let sessionBody: Record<string, unknown> | undefined;
  let messageBody: Record<string, unknown> | undefined;
  await page.route("**/api/v1/sessions", route => {
    if (route.request().method() === "POST") {
      sessionBody = route.request().postDataJSON();
      return route.fulfill({ json: envelope({ id: "scope-session", title: "范围测试", knowledge_base_scope: ["kb-2", "kb-1"], document_scope: ["doc-1", "doc-2"], updated_at: "now" }) });
    }
    return route.fulfill({ json: envelope([]) });
  });
  await page.route("**/api/v1/sessions/scope-session/messages", route => {
    messageBody = route.request().postDataJSON();
    return route.fulfill({ contentType: "text/event-stream", body: frame({ id: "scope-run", response_type: "answer", content: "范围已确认", done: true, data: { event_id: "scope-answer" } }) + frame({ id: "scope-run", response_type: "complete", content: "", done: false, data: { final_content: "范围已确认" } }) });
  });
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "选择知识库和文件", exact: true });
  const dialog = page.getByRole("dialog", { name: "选择知识库和文件", exact: true });
  await expect(page.locator(".wp-composer").getByRole("button", { name: "选择知识库和文件" })).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".wp-chat-page > .wp-scope-card")).toHaveCount(0);
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await dialog.getByRole("checkbox", { name: /课程资料/ }).check();
  await dialog.getByRole("checkbox", { name: /教材 第三章/ }).check();
  await dialog.getByRole("checkbox", { name: /图片资料/ }).check();
  await expect(dialog.getByRole("checkbox", { name: /教材 第三章/ })).not.toBeChecked();
  await expect(dialog.getByRole("checkbox", { name: /产品图/ })).toBeVisible();
  await dialog.getByRole("checkbox", { name: /课程资料/ }).uncheck();
  await expect(dialog.getByRole("checkbox", { name: /教材 第三章/ })).toHaveCount(0);
  await dialog.getByRole("checkbox", { name: /课程资料/ }).check();
  await dialog.getByRole("checkbox", { name: /教材 第三章/ }).check();
  await dialog.getByRole("checkbox", { name: /产品图/ }).check();
  await page.screenshot({ path: test.info().outputPath("ui-restoration-scope.png"), fullPage: true });
  await dialog.getByRole("button", { name: "完成选择" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".wp-composer-scope")).toContainText("2 个文件");
  await page.getByPlaceholder("询问当前资料…").fill("范围测试");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("范围已确认", { exact: true })).toBeVisible();
  expect(sessionBody?.knowledge_base_scope).toEqual(["kb-2", "kb-1"]);
  expect(sessionBody?.document_scope).toEqual(["doc-1", "doc-2"]);
  expect(messageBody?.knowledge_base_scope).toEqual(["kb-2", "kb-1"]);
  expect(messageBody?.document_scope).toEqual(["doc-1", "doc-2"]);
  await trigger.click();
  await expect(dialog.getByText("会话使用已保存的知识库范围；新范围请新建会话")).toBeVisible();
  for (const checkbox of await dialog.getByRole("checkbox").all()) await expect(checkbox).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});


test("library keeps upload and readable filenames in view with 50 KBs and independent document scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 968, height: 728 });
  await installCatalogRoutes(page);
  const kbRows = Array.from({ length: 50 }, (_, index) => ({
    id: `many-kb-${index + 1}`, name: `第${index + 1}个知识库 · 项目现场长期归档资料`,
    cloud_allowed: false, document_count: index === 49 ? 35 : 0,
  }));
  const documents = Array.from({ length: 35 }, (_, index) => ({
    id: `long-document-${index + 1}`, knowledge_base_id: "many-kb-50",
    filename: `第${index + 1}份现场巡检报告_北码头设备维护与原始扫描记录_Long_document_filename_2026.pdf`, status: "ready",
  }));
  const documentReads: string[] = [];
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: envelope(kbRows) }));
  await page.route("**/api/v1/knowledge-bases/*/documents", route => {
    const path = new URL(route.request().url()).pathname;
    documentReads.push(path);
    return route.fulfill({ json: envelope(path.endsWith("/many-kb-50/documents") ? documents : []) });
  });
  let originalReads = 0;
  await page.context().route("**/api/v1/knowledge-bases/many-kb-50/documents/long-document-35/source", route => {
    originalReads++;
    return route.fulfill({ contentType: "text/plain; charset=utf-8", body: "原始文件 mock source — document 35" });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "资料库", exact: true }).click();
  const library = page.locator("section.wp-library-page");
  const heading = library.getByRole("heading", { name: "知识库", exact: true, level: 1 });
  const kbList = library.locator(".wp-kb-list");
  const docList = library.locator(".wp-library-documents");
  const upload = library.locator(".wp-upload-row");
  await expect(kbList.getByRole("button")).toHaveCount(50);
  const lastBase = kbList.getByRole("button").last();
  await lastBase.click();
  await expect(lastBase).toHaveAttribute("aria-pressed", "true");
  await expect(library.getByRole("combobox")).toHaveValue("many-kb-50");
  await expect(docList.locator(".wp-document-row")).toHaveCount(35);
  await expect(heading).toBeInViewport({ ratio: 1 });
  await expect(upload).toBeInViewport({ ratio: 1 });
  await expect(upload.locator('input[type="file"]')).toBeEnabled();
  const uploadTop = (await upload.boundingBox())!.y;
  const kbScrollTop = await kbList.evaluate(element => element.scrollTop);
  expect(kbScrollTop).toBeGreaterThan(0);
  for (const list of [kbList, docList]) {
    expect(await list.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
    expect(await list.evaluate(element => ["auto", "scroll"].includes(getComputedStyle(element).overflowY))).toBe(true);
  }
  const firstTitle = docList.locator(".wp-document-row").first().locator("strong");
  await expect(firstTitle).toHaveText(documents[0].filename);
  const titleSpace = await firstTitle.evaluate(element => ({
    width: element.getBoundingClientRect().width,
    height: element.getBoundingClientRect().height,
    lineHeight: parseFloat(getComputedStyle(element).lineHeight),
    fontSize: parseFloat(getComputedStyle(element).fontSize),
  }));
  expect(titleSpace.width).toBeGreaterThanOrEqual(180);
  expect(titleSpace.height).toBeGreaterThanOrEqual(titleSpace.fontSize * 1.3);
  if (Number.isFinite(titleSpace.lineHeight)) expect(titleSpace.height).toBeGreaterThanOrEqual(titleSpace.lineHeight);

  const lastDocument = docList.locator(".wp-document-row").last();
  await lastDocument.scrollIntoViewIfNeeded();
  expect(await docList.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
  expect(await kbList.evaluate(element => element.scrollTop)).toBe(kbScrollTop);
  await expect(heading).toBeInViewport({ ratio: 1 });
  await expect(upload).toBeInViewport({ ratio: 1 });
  expect(Math.abs((await upload.boundingBox())!.y - uploadTop)).toBeLessThanOrEqual(1);
  await expect(library.getByRole("combobox")).toHaveValue("many-kb-50");
  await expect(lastBase).toHaveAttribute("aria-pressed", "true");
  const overflow = await page.locator("main").evaluate(element => ({ scroll: element.scrollWidth, client: element.clientWidth }));
  expect(overflow.scroll).toBeLessThanOrEqual(overflow.client + 1);
  await page.screenshot({ path: test.info().outputPath("library-50-kbs-35-long-documents.png"), fullPage: true });

  const original = lastDocument.getByRole("link", { name: "查看原文件", exact: true });
  await expect(original).toHaveAttribute("href", /knowledge-bases\/many-kb-50\/documents\/long-document-35\/source$/);
  const popupOpened = page.waitForEvent("popup");
  await original.click();
  const popup = await popupOpened;
  await expect(popup.getByText("原始文件 mock source — document 35", { exact: true })).toBeVisible();
  await popup.close();
  expect(originalReads).toBe(1);
  expect(documentReads.at(-1)).toBe("/api/v1/knowledge-bases/many-kb-50/documents");
});
