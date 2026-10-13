import { test, expect, type Page } from "@playwright/test";

async function offline(page: Page) {
  await page.route("**/api/v1/**", route => route.fulfill({ status: 404, json: { error: { code: "OFFLINE_ONLY" } } }));
  await page.goto("/");
}

for (const windows of [7, 9]) test(`${windows} healthy SSE windows preserve stages/cursor over simulated ${windows * 30} seconds`, async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async windows => {
    const { readRunEvents } = await import("/src/api/sse.ts");
    const { applyRunEvent } = await import("/src/app/state.ts");
    const originalNow = Date.now;
    const started = originalNow();
    let now = started, connections = 0;
    Date.now = () => now;
    const cursors: string[] = [], received: number[] = [];
    let timeline = { runId: "run", lastSeq: 0, status: "pending", stages: [] };
    window.fetch = async (_input, init) => {
      cursors.push(new Headers(init?.headers).get("Last-Event-ID") ?? "");
      connections++;
      now += 30_000;
      const stage = ["run", "understand", "retrieval", "evidence", "generation", "validation"][connections - 1];
      const data = { schema_version: "run-progress/v1", stage, status: "completed", candidate_count: stage === "retrieval" ? 12 : undefined };
      const fresh = connections === windows ? { event: "answer.completed", data: { answer: "committed", citations: [] } } : { event: "run.progress", data };
      const repeated = connections > 1 ? `id: ${connections - 1}\nevent: run.progress\ndata: ${JSON.stringify(data)}\n\n` : "";
      return new Response(`${repeated}id: ${connections}\nevent: ${fresh.event}\ndata: ${JSON.stringify(fresh.data)}\n\n`, { headers: { "Content-Type": "text/event-stream" } });
    };
    try {
      await readRunEvents("run", "conv", event => { received.push(event.seq); timeline = applyRunEvent(timeline, event); });
      return { connections, elapsed: now - started, cursors, received, timeline };
    } finally { Date.now = originalNow; }
  }, windows);
  expect(result.connections).toBe(windows);
  expect(result.elapsed).toBe(windows * 30_000);
  expect(result.received).toEqual(Array.from({ length: windows }, (_, i) => i + 1));
  expect(result.cursors).toEqual(Array.from({ length: windows }, (_, i) => i ? String(i) : ""));
  expect(result.timeline.stages).toHaveLength(6);
  expect(result.timeline.status).toBe("completed");
});

test("POST and SSE defaults allow 300 seconds while explicit windows retain the 210 second floor", async ({ page }) => {
  await offline(page);
  const timers = await page.evaluate(async () => {
    const { sendMessageWithEvents } = await import("/src/api/client.ts");
    const { readRunEvents } = await import("/src/api/sse.ts");
    const originalTimer = window.setTimeout;
    const deadlines: number[] = [];
    window.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) => {
      if (timeout && timeout >= 210_000) deadlines.push(timeout);
      return originalTimer(handler, timeout, ...args);
    }) as typeof window.setTimeout;
    window.fetch = async (_input, init) => init?.method === "POST"
      ? new Response(JSON.stringify({ error: { code: "SIM_REJECTED" } }), { status: 422 })
      : new Response("", { status: 404 });
    try {
      for (const waitTimeoutMs of [undefined, 240_000, 1]) {
        await readRunEvents("run", "conv", () => {}, { waitTimeoutMs, startupWaitMs: 0 });
        await sendMessageWithEvents("conv", "q", "quick", ["kb"], [], () => {},
          { waitTimeoutMs, startupWaitMs: 0 }).catch(() => undefined);
      }
      return deadlines;
    } finally { window.setTimeout = originalTimer; }
  });
  // Each POST owns a deadline and starts one SSE reader with that same deadline.
  expect(timers).toEqual([300_000, 300_000, 300_000, 240_000, 240_000, 240_000, 210_000, 210_000, 210_000]);
});

test("abnormal failures use bounded retries and resumed cursors without spinning", async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async () => {
    const { readRunEvents } = await import("/src/api/sse.ts");
    const started = Date.now();
    let calls = 0;
    window.fetch = async () => { calls++; throw new TypeError("SIMULATED transport failure"); };
    await readRunEvents("run", "conv", () => {});
    return { calls, elapsed: Date.now() - started };
  });
  expect(result.calls).toBe(3);
  expect(result.elapsed).toBeGreaterThanOrEqual(700);
});

test("startup scoped 404 grace is at most ten seconds and healthy windows respect overall deadline", async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async () => {
    const { readRunEvents } = await import("/src/api/sse.ts");
    const originalNow = Date.now;
    let now = originalNow(), calls = 0;
    Date.now = () => now;
    try {
      window.fetch = async () => { calls++; now += 2500; return new Response("", { status: 404 }); };
      await readRunEvents("known-run", "conv", () => {});
      const startupCalls = calls;
      calls = 0;
      window.fetch = async () => { calls++; now += 80_000; return new Response(": waiting\n\n", { headers: { "Content-Type": "text/event-stream" } }); };
      await readRunEvents("known-run", "conv", () => {}, { waitTimeoutMs: 240_000 });
      return { startupCalls, deadlineCalls: calls };
    } finally { Date.now = originalNow; }
  });
  expect(result.startupCalls).toBe(4);
  expect(result.deadlineCalls).toBe(3);
});

test("typed API rejection is prompt while mismatched POST identity waits for exact GET", async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async () => {
    const { sendMessageWithEvents } = await import("/src/api/client.ts");
    let identity = "", posts = 0;
    window.fetch = async (_input, init) => {
      if (init?.method === "POST") { posts++; return new Response(JSON.stringify({ error: { code: "OUTPUT_CAPACITY_UNSUPPORTED", message: "unsupported" } }), { status: 422 }); }
      return new Promise<Response>(() => {});
    };
    const start = Date.now();
    let error = "";
    try { await sendMessageWithEvents("conv", "q", "quick", ["kb"], [], () => {}); } catch (reason) { error = String(reason); }
    const elapsed = Date.now() - start;
    window.fetch = async (input, init) => {
      if (init?.method === "POST") { posts++; identity = JSON.parse(String(init.body)).request_id;
        return new Response(JSON.stringify({ data: { run_id: "wrong", answer: "WRONG RUN", citations: [] } })); }
      if (String(input).includes("/events?")) return new Response('id: 1\nevent: answer.completed\ndata: {"answer":"correct","citations":[]}\n\n', { headers: { "Content-Type": "text/event-stream" } });
      return new Response(JSON.stringify({ data: [{ role: "assistant", run_id: identity, content: "correct", citations: [] }] }));
    };
    const answer = await sendMessageWithEvents("conv", "q", "quick", ["kb"], [], () => {});
    return { error, elapsed, answer, posts };
  });
  expect(result.error).toContain("OUTPUT_CAPACITY_UNSUPPORTED");
  expect(result.elapsed).toBeLessThan(1000);
  expect(result.answer.answer).toBe("correct");
  expect(result.posts).toBe(2);
});

for (const mode of ["lost", "pending", "late"] as const) {
  test(`committed scoped GET beats ${mode} POST with one generation and no duplicate completion`, async ({ page }) => {
    await offline(page);
    const result = await page.evaluate(async mode => {
      const { sendMessageWithEvents } = await import("/src/api/client.ts");
      let identity = "", posts = 0, getHistory = 0;
      let finishPost: (response: Response) => void = () => {};
      const received: string[] = [];
      window.fetch = async (input, init) => {
        if (init?.method === "POST") {
          posts++; identity = JSON.parse(String(init.body)).request_id;
          if (mode === "lost") throw new TypeError("SIMULATED lost POST");
          return new Promise<Response>(resolve => { finishPost = resolve; });
        }
        if (String(input).includes("/events?")) return new Response(
          'id: 1\nevent: answer.delta\ndata: {"answer":"DIRTY CANDIDATE"}\n\n' +
          'id: 2\nevent: answer.completed\ndata: {"answer":"saved answer","citations":["E1"]}\n\n' +
          'id: 2\nevent: answer.completed\ndata: {"answer":"duplicate dirty","citations":[]}\n\n',
          { headers: { "Content-Type": "text/event-stream" } });
        getHistory++;
        return new Response(JSON.stringify({ data: [
          { role: "assistant", run_id: "wrong-run", content: "WRONG ANSWER", citations: [] },
          { role: "assistant", run_id: identity, content: "saved answer", citations: ["E1"] },
        ] }), { headers: { "Content-Type": "application/json" } });
      };
      const answer = await sendMessageWithEvents("conv", "question", "quick", ["kb"], [], event => received.push(event.event));
      if (mode === "late") finishPost(new Response(JSON.stringify({ data: { run_id: identity, answer: "late wrong text", citations: [] } })));
      await Promise.resolve();
      return { answer, posts, getHistory, received };
    }, mode);
    expect(result.posts).toBe(1);
    expect(result.getHistory).toBe(1);
    expect(result.answer.answer).toBe("saved answer");
    expect(result.answer.citations).toEqual(["E1"]);
    expect(result.received.filter(value => value === "answer.completed")).toHaveLength(1);
  });
}

test("unmatched committed history cannot promote dirty terminal text to an answer", async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async () => {
    const { sendMessageWithEvents } = await import("/src/api/client.ts");
    let posts = 0;
    window.fetch = async (input, init) => {
      if (init?.method === "POST") { posts++; throw new TypeError("SIMULATED lost response"); }
      if (String(input).includes("/events?")) return new Response('id: 1\nevent: answer.completed\ndata: {"answer":"DIRTY","citations":[]}\n\n', { headers: { "Content-Type": "text/event-stream" } });
      return new Response(JSON.stringify({ data: [{ role: "assistant", run_id: "wrong", content: "DIRTY", citations: [] }] }));
    };
    try { await sendMessageWithEvents("conv", "question", "quick", ["kb"], [], () => {}); return { posts, error: "unexpected success" }; }
    catch (error) { return { posts, error: String(error) }; }
  });
  expect(result.posts).toBe(1);
  expect(result.error).toContain("RUN_STATUS_UNKNOWN");
});

test("cancellation fences late SSE and POST even when simulated IO ignores abort", async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async () => {
    const { sendMessageWithEvents } = await import("/src/api/client.ts");
    const controller = new AbortController();
    let posts = 0, events = 0;
    let releaseGet: (response: Response) => void = () => {};
    let releasePost: (response: Response) => void = () => {};
    window.fetch = async (_input, init) => {
      if (init?.method === "POST") { posts++; return new Promise<Response>(resolve => { releasePost = resolve; }); }
      return new Promise<Response>(resolve => { releaseGet = resolve; });
    };
    const pending = sendMessageWithEvents("conv", "q", "quick", ["kb"], [], () => { events++; }, { signal: controller.signal });
    controller.abort();
    let error = "";
    try { await pending; } catch (reason) { error = String(reason); }
    releaseGet(new Response('id: 1\nevent: answer.completed\ndata: {"answer":"LATE","citations":[]}\n\n', { headers: { "Content-Type": "text/event-stream" } }));
    releasePost(new Response(JSON.stringify({ data: { run_id: "late", answer: "LATE", citations: [] } })));
    await Promise.resolve(); await Promise.resolve();
    return { posts, events, error };
  });
  expect(result.posts).toBe(1);
  expect(result.events).toBe(0);
  expect(result.error).toContain("CANCELLED");
});

test("SSE cancellation resolves even when simulated fetch and body cleanup ignore abort", async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async () => {
    const { readRunEvents } = await import("/src/api/sse.ts");
    const first = new AbortController();
    window.fetch = async () => new Promise<Response>(() => {});
    const waitingFetch = readRunEvents("run", "conv", () => {}, { signal: first.signal });
    first.abort();
    await waitingFetch;
    const second = new AbortController();
    let received = 0;
    window.fetch = async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(new TextEncoder().encode('id: 1\nevent: run.progress\ndata: {"schema_version":"run-progress/v1","stage":"generation","status":"started"}\n\n')); },
      cancel() { return new Promise<void>(() => {}); },
    }), { headers: { "Content-Type": "text/event-stream" } });
    await readRunEvents("run", "conv", () => { received++; second.abort(); }, { signal: second.signal });
    return { received };
  });
  expect(result.received).toBe(1);
});

test("versioned stage reducer rejects invalid progress and closes pending without inventing success", async ({ page }) => {
  await offline(page);
  const result = await page.evaluate(async () => {
    const { applyRunEvent } = await import("/src/app/state.ts");
    let timeline = { runId: "exact-run", lastSeq: 0, status: "pending", stages: [] };
    const progress = (seq: number, data: Record<string, unknown>) => { timeline = applyRunEvent(timeline, { seq, event: "run.progress", data }); };
    progress(1, { schema_version: "old", stage: "retrieval", status: "completed" });
    progress(2, { schema_version: "run-progress/v1", stage: "retrieval", status: "completed", candidate_count: -1 });
    progress(3, { schema_version: "run-progress/v1", stage: "generation", status: "started", attempt_id: "first", role: "cheap", thoughts: "PRIVATE" });
    progress(4, { schema_version: "run-progress/v1", stage: "generation", status: "truncated", attempt_id: "first", role: "cheap" });
    progress(5, { schema_version: "run-progress/v1", stage: "generation", status: "started", attempt_id: "second", role: "expensive" });
    timeline = applyRunEvent(timeline, { seq: 6, event: "run.failed", data: { error_code: "MODEL_UNAVAILABLE" } });
    progress(7, { schema_version: "run-progress/v1", stage: "generation", status: "started", role: "cheap" });
    return timeline;
  });
  expect(result.runId).toBe("exact-run");
  expect(result.stages).toHaveLength(2);
  expect(result.stages.map(row => row.status)).toEqual(["truncated", "failed"]);
  expect(result.status).toBe("failed");
  expect(JSON.stringify(result)).not.toContain("PRIVATE");
});

test("live timeline sits on assistant side, collapses at commit and restores after refresh", async ({ page }, testInfo) => {
  let identity = "", committed = false, posts = 0;
  let releasePost: () => void = () => {};
  const postGate = new Promise<void>(resolve => { releasePost = resolve; });
  await page.route("**/api/v1/**", route => route.fulfill({ status: 404, json: { error: { code: "OFFLINE_ONLY" } } }));
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: { data: [{ id: "kb", name: "资料库" }] } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", route => route.fulfill({ json: { data: [] } }));
  await page.route("**/api/v1/conversations", route => route.fulfill({ json: { data: [{ id: "conv", title: "当前会话", knowledge_base_scope: ["kb"], document_scope: [] }] } }));
  await page.route("**/api/v1/conversations/conv/messages", async route => {
    if (route.request().method() === "POST") {
      posts++; identity = route.request().postDataJSON().request_id;
      await postGate;
      await route.fulfill({ json: { data: { run_id: identity, answer: "LATE POST", citations: [] } } }).catch(() => undefined);
    } else await route.fulfill({ json: { data: committed ? [
      { role: "user", content: "实际问题", run_id: identity },
      { role: "assistant", content: "已保存的答案", run_id: identity, citations: ["E1"] },
    ] : [] } });
  });
  await page.route("**/api/v1/runs/*/events?*", route => route.fulfill({ contentType: "text/event-stream", body:
    'id: 1\nevent: run.progress\ndata: {"schema_version":"run-progress/v1","stage":"retrieval","status":"completed","candidate_count":9}\n\n' +
    'id: 2\nevent: run.progress\ndata: {"schema_version":"run-progress/v1","stage":"evidence","status":"completed","selected_evidence_count":3}\n\n' +
    'id: 3\nevent: run.progress\ndata: {"schema_version":"run-progress/v1","stage":"generation","status":"started","role":"cheap","attempt_id":"first"}\n\n' +
    (committed ? 'id: 4\nevent: answer.completed\ndata: {"answer":"已保存的答案","citations":["E1"]}\n\n' : "") }));
  await page.goto("/");
  await page.getByRole("button", { name: "当前会话", exact: true }).click();
  await page.getByLabel("输入问题").fill("实际问题");
  await page.getByRole("button", { name: "发送问题" }).click();
  await expect(page.locator(".run-progress")).toBeVisible();
  await expect(page.locator(".run-progress-stages")).toContainText("候选片段 9");
  await expect(page.locator(".run-progress-stages")).toContainText("有效证据 3");
  await expect(page.locator(".stage-started")).toContainText("轻量模型");
  await expect(page.locator(".stage-started")).toContainText("正在连接模型并生成回答");
  await expect(page.locator(".message.user")).not.toContainText("生成回答");
  await expect(page.locator("article.assistant")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("offline-progress-pending.png"), fullPage: true });
  committed = true;
  await expect(page.getByText("已保存的答案", { exact: true })).toBeVisible();
  await expect(page.locator(".run-progress-header > button").first()).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("article.assistant")).toHaveCount(1);
  releasePost();
  await expect(page.getByText("LATE POST", { exact: true })).toHaveCount(0);
  await page.reload();
  await page.getByRole("button", { name: "当前会话", exact: true }).click();
  await expect(page.locator(".run-progress.completed")).toBeVisible();
  await page.locator(".run-progress-header > button").first().click();
  await expect(page.locator(".run-progress-stages")).toContainText("有效证据 3");
  await expect(page.locator("article.assistant")).toHaveCount(1);
  expect(posts).toBe(1);
  await page.screenshot({ path: testInfo.outputPath("offline-progress-restored.png"), fullPage: true });
});

test("switching conversation and KB fences delayed progress replay from old Run", async ({ page }) => {
  let releaseOld: () => void = () => {};
  const oldGate = new Promise<void>(resolve => { releaseOld = resolve; });
  await page.route("**/api/v1/**", route => route.fulfill({ status: 404, json: { error: { code: "OFFLINE_ONLY" } } }));
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: { data: [{ id: "kb-a", name: "A" }, { id: "kb-b", name: "B" }] } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", route => route.fulfill({ json: { data: [] } }));
  await page.route("**/api/v1/conversations", route => route.fulfill({ json: { data: [
    { id: "conv-a", title: "会话 A", knowledge_base_scope: ["kb-a"], document_scope: [] },
    { id: "conv-b", title: "会话 B", knowledge_base_scope: ["kb-b"], document_scope: [] },
  ] } }));
  await page.route("**/api/v1/conversations/conv-a/messages", route => route.fulfill({ json: { data: [{ role: "user", content: "A 的问题", run_id: "run-a" }] } }));
  await page.route("**/api/v1/conversations/conv-b/messages", route => route.fulfill({ json: { data: [{ role: "assistant", content: "B 的答案" }] } }));
  await page.route("**/api/v1/runs/run-a/events?*", async route => {
    await oldGate;
    await route.fulfill({ contentType: "text/event-stream", body: 'id: 1\nevent: run.progress\ndata: {"schema_version":"run-progress/v1","stage":"generation","status":"started","role":"expensive"}\n\n' }).catch(() => undefined);
  });
  await page.goto("/");
  const oldStream = page.waitForRequest("**/api/v1/runs/run-a/events?*");
  await page.getByRole("button", { name: "会话 A", exact: true }).click();
  await oldStream;
  await page.getByRole("button", { name: "会话 B", exact: true }).click();
  await expect(page.getByText("B 的答案", { exact: true })).toBeVisible();
  releaseOld();
  await expect(page.locator(".run-progress[data-run-id='run-a']")).toHaveCount(0);
  await expect(page.locator(".composer-scope")).toHaveText("B");
  await expect(page.getByText("A 的问题", { exact: true })).toHaveCount(0);
  await expect(page.getByText("高能力模型", { exact: false })).toHaveCount(0);
});

test("new send closes only confirmed history when its SSE replay is still pending", async ({ page }) => {
  let releaseHistory: () => void = () => {};
  const historyGate = new Promise<void>(resolve => { releaseHistory = resolve; });
  let identity = "", posts = 0;
  const history = [
    { role: "user", content: "saved question", run_id: "saved-run" },
    { role: "assistant", content: "saved answer", run_id: "saved-run", citations: [] },
    { role: "user", content: "failed question", run_id: "failed-run", presentation: {
      schema_version: "run-presentation-v1", kind: "run_diagnostic", category: "generation_error",
      reason_code: "MODEL_UNAVAILABLE", text: "Confirmed failure" } },
    { role: "user", content: "unknown question", run_id: "unknown-run" },
  ];
  await page.route("**/api/v1/**", route => route.fulfill({ status: 404, json: { error: { code: "OFFLINE_ONLY" } } }));
  await page.route("**/api/v1/knowledge-bases", route => route.fulfill({ json: { data: [{ id: "kb", name: "KB" }] } }));
  await page.route("**/api/v1/knowledge-bases/*/documents", route => route.fulfill({ json: { data: [] } }));
  await page.route("**/api/v1/conversations", route => route.fulfill({ json: { data: [{ id: "conv", title: "History", knowledge_base_scope: ["kb"], document_scope: [] }] } }));
  await page.route("**/api/v1/conversations/conv/messages", route => {
    if (route.request().method() === "POST") {
      posts++; identity = route.request().postDataJSON().request_id;
      return route.fulfill({ json: { data: { run_id: identity, answer: "new answer", citations: [] } } });
    }
    return route.fulfill({ json: { data: [...history, ...(identity ? [
      { role: "user", content: "new question", run_id: identity },
      { role: "assistant", content: "new answer", run_id: identity, citations: [] },
    ] : [])] } });
  });
  await page.route("**/api/v1/runs/*/events?*", async route => {
    if (/\/runs\/(saved-run|failed-run|unknown-run)\//.test(route.request().url())) {
      await historyGate;
      await route.fulfill({ contentType: "text/event-stream", body: ': stopped\n\n' }).catch(() => undefined);
    } else await route.fulfill({ contentType: "text/event-stream", body:
      'id: 1\nevent: answer.completed\ndata: {"answer":"new answer","citations":[]}\n\n' });
  });
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "History", exact: true }).click();
    await expect(page.getByText("saved answer", { exact: true })).toBeVisible();
    await expect(page.locator('.run-progress.pending')).toHaveCount(3);
    await page.getByLabel("输入问题").fill("new question");
    await page.getByRole("button", { name: "发送问题" }).click();
    await expect(page.getByText("new answer", { exact: true })).toBeVisible();
    await expect(page.locator('.run-progress[data-run-id="saved-run"]')).toHaveClass('run-progress completed');
    await expect(page.locator('.run-progress[data-run-id="failed-run"]')).toHaveClass('run-progress failed');
    await expect(page.locator('.run-progress[data-run-id="unknown-run"]')).not.toHaveClass('run-progress completed');
    expect(posts).toBe(1);
  } finally { releaseHistory(); }
});
