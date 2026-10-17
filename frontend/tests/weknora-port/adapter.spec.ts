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
  await expect(page.getByText("选择检索范围，快速问答会显示实际检索与回答进度。")).toBeVisible();
  await expect(page.getByText("选择检索范围，快速问答会显示服务端实际发送的执行事件与引用。")).toHaveCount(0);
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
      content: '历史 <ref>原始引文</ref> <kb chunk_id="old-chunk" doc="资料" /> <kb>块文字</kb> <web id="w">网页文字</web> <reference>普通标签</reference> 末尾\n\n`<ref>code</ref>`\n\n```txt\n<ref>code</ref>\n```\n\n<ref id=' },
  ]) }));

  await page.goto("/");
  await page.getByRole("button", { name: /旧回答/ }).click();
  const renderedAnswer = page.locator(".wp-message.assistant .wp-message-content");
  await expect(renderedAnswer).toContainText("历史 原始引文");
  await expect(renderedAnswer).toContainText("块文字 网页文字 <reference>普通标签</reference> 末尾");
  await expect(renderedAnswer).not.toContainText("<ref id=");
  await expect(renderedAnswer.locator("p > code")).toHaveText("<ref>code</ref>");
  await expect(renderedAnswer.locator("pre code")).toContainText("<ref>code</ref>");
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
  await expect(historyButtons).toHaveCount(4);
  for (const button of await historyButtons.all()) {
    const dimensions = await button.evaluate(element => ({
      buttonWidth: element.getBoundingClientRect().width,
      sidebarWidth: element.closest(".wp-sidebar")!.getBoundingClientRect().width,
      titleWidth: element.querySelector("strong")!.clientWidth,
      titleScrollWidth: element.querySelector("strong")!.scrollWidth,
    }));
    expect(dimensions.buttonWidth).toBeLessThanOrEqual(dimensions.sidebarWidth);
    expect(dimensions.titleWidth).toBeGreaterThan(0);
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
