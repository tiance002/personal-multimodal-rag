import { test, expect } from "@playwright/test";

test("SSE client deduplicates seq and resumes with Last-Event-ID", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const { readRunEvents } = await import("/src/api/sse.ts");
    const requests: string[] = [];
    let attempt = 0;
    window.fetch = async (_input, init) => {
      const headers = new Headers(init?.headers);
      requests.push(headers.get("Last-Event-ID") ?? "");
      attempt += 1;
      const body = attempt === 1
        ? "id: 1\nevent: run.created\ndata: {}\n\nid: 2\nevent: retrieval.completed\ndata: {}\n\n"
        : "id: 2\nevent: retrieval.completed\ndata: {}\n\nid: 3\nevent: answer.completed\ndata: {}\n\n";
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    };
    const received: number[] = [];
    await readRunEvents("run-1", (event) => received.push(event.seq));
    return { received, requests };
  });

  expect(result.received).toEqual([1, 2, 3]);
  expect(result.requests).toEqual(["", "2"]);
});
