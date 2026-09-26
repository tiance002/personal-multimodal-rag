export type StreamEvent = { seq: number; event: string; data: Record<string, unknown> };

export function parseStreamEvents(text: string, afterSeq = 0): StreamEvent[] {
  const events: StreamEvent[] = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const lines = block.split("\n");
    const id = lines.find((line) => line.startsWith("id: "))?.slice(4);
    const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
    const data = lines.find((line) => line.startsWith("data: "))?.slice(6);
    const seq = Number(id);
    if (!id || !event || !data || !Number.isInteger(seq) || seq <= afterSeq) continue;
    try {
      const parsed = JSON.parse(data) as Record<string, unknown>;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) events.push({ seq, event, data: parsed });
    } catch {
      // Ignore a malformed event and let the caller continue from the last valid seq.
    }
  }
  return events;
}

export async function readRunEvents(runId: string, onEvent: (event: StreamEvent) => void): Promise<void> {
  let lastSeq = 0;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(`/api/v1/runs/${runId}/events`, lastSeq ? { headers: { "Last-Event-ID": String(lastSeq) } } : undefined);
    } catch {
      continue;
    }
    if (!response.ok || !response.body) return;
    const events = parseStreamEvents(await response.text(), lastSeq);
    for (const event of events) {
      lastSeq = event.seq;
      onEvent(event);
    }
    if (events.some((event) => event.event === "answer.completed" || event.event === "run.failed")) return;
  }
}
