import { test, expect } from "@playwright/test";

test("SSE client deduplicates seq and resumes with Last-Event-ID", async ({ page }) => {
  // The workbench may load its catalog on navigation; keep this SSE test
  // entirely offline and prevent the development proxy reaching any backend.
  await page.route('**/api/v1/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{"data":[]}' }));
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { readRunEvents } = await import("/src/api/sse.ts");
    const requests: string[] = [];
    const urls: string[] = [];
    let attempt = 0;
    window.fetch = async (_input, init) => {
      urls.push(String(_input));
      const headers = new Headers(init?.headers);
      requests.push(headers.get("Last-Event-ID") ?? "");
      attempt += 1;
      const body = attempt === 1
        ? "id: 1\nevent: run.created\ndata: {}\n\nid: 2\nevent: retrieval.completed\ndata: {}\n\n"
        : "id: 2\nevent: retrieval.completed\ndata: {}\n\nid: 3\nevent: answer.completed\ndata: {}\n\n";
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    };
    const received: number[] = [];
    await readRunEvents("run-1", "conversation-1", (event) => received.push(event.seq));
    return { received, requests, urls };
  });

  expect(result.received).toEqual([1, 2, 3]);
  expect(result.requests).toEqual(["", "2"]);
  expect(result.urls.every(url => url.endsWith('/events?conversation_id=conversation-1'))).toBe(true);
});

test("lost POST recovers clarification only from the same committed user run with one POST", async ({ page }) => {
  await page.route('**/api/v1/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{"data":[]}' }));
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { sendMessageWithEvents } = await import("/src/api/client.ts");
    let posts = 0;
    let identity = "";
    window.fetch = async (input, init) => {
      if (init?.method === "POST") {
        posts++;
        identity = JSON.parse(String(init.body)).request_id;
        throw new TypeError("SIMULATED lost response");
      }
      if (String(input).includes("/events?")) {
        return new Response('id: 1\nevent: run.failed\ndata: {"answer":"","citations":[],"error_code":"NO_CANDIDATES"}\n\n',
          { headers: { "Content-Type": "text/event-stream" } });
      }
      return new Response(JSON.stringify({ data: [
        { role: "user", run_id: "other", citations: [], presentation: { kind: "clarification", text: "WRONG RUN", clarification_required: true } },
        { role: "user", run_id: identity, citations: [], presentation: { kind: "clarification", text: "请明确资料中的对象", clarification_required: true } },
      ] }), { headers: { "Content-Type": "application/json" } });
    };
    const answer = await sendMessageWithEvents("conversation", "它是什么", "quick", ["kb"], [], () => {});
    return { posts, answer };
  });
  expect(result.posts).toBe(1);
  expect(result.answer.answer).toBe("请明确资料中的对象");
  expect(result.answer.trace?.clarification_required).toBe(true);
  expect(result.answer.error_code).toBe("NO_CANDIDATES");
  expect(result.answer.citations).toEqual([]);
});

test("retrieval failure, empty recall and absent history diagnostics are distinct", async ({ page }) => {
  await page.route('**/api/v1/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{"data":[]}' }));
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { retrievalFailureText } = await import("/src/api/client.ts");
    const base = { run_id: "SIMULATED", answer: "", citations: [], error_code: "NO_CANDIDATES" };
    const response = (status: string, reason: string | null) => ({ ...base, trace: { metrics: { retrieval_diagnostics: [
      { schema_version: "retrieval-diagnostic-v1", channels: {
        keyword: { status: "EMPTY", count: 0, reason: null }, vector: { status, count: status === "EMPTY" ? 0 : null, reason },
      } },
    ] } } });
    return [retrievalFailureText(response("NOT_SENT", "MODEL_ROLE_EGRESS_DENIED")),
      retrievalFailureText(response("EMPTY", null)), retrievalFailureText(base)];
  });
  expect(result[0]).toContain("MODEL_ROLE_EGRESS_DENIED");
  expect(result[1]).toContain("没有检索到相关内容");
  expect(result[2]).toContain("缺少阶段诊断");
});
