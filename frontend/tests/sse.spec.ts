import { test, expect } from "@playwright/test";

test("SSE client deduplicates seq and resumes with Last-Event-ID", async ({ page }) => {
  // The workbench may load its catalog on navigation; keep this SSE test
  // entirely offline and prevent the development proxy reaching any backend.
  await page.route('**/api/**', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{"data":[]}' }));
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
