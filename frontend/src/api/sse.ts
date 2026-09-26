export type StreamEvent = { seq: number; event: string; data: Record<string, unknown> };

const TERMINAL_EVENTS = new Set(["answer.completed", "run.failed"]);

function parseEventBlock(block: string, afterSeq: number): StreamEvent | null {
  const lines = block.split(/\r?\n/);
  const id = lines.find((line) => line.startsWith("id: "))?.slice(4);
  const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
  const data = lines.find((line) => line.startsWith("data: "))?.slice(6);
  const seq = Number(id);
  if (!id || !event || !data || !Number.isInteger(seq) || seq <= afterSeq) return null;
  try {
    const parsed = JSON.parse(data) as Record<string, unknown>;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return { seq, event, data: parsed };
  } catch {
    // Ignore a malformed event and let the caller continue from the last valid seq.
  }
  return null;
}

export function parseStreamEvents(text: string, afterSeq = 0): StreamEvent[] {
  const events: StreamEvent[] = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const event = parseEventBlock(block, afterSeq);
    if (event) events.push(event);
  }
  return events;
}

/**
 * Read a run's SSE stream incrementally.
 *
 * Events are handed to `onEvent` as soon as a complete block arrives instead of
 * buffering the whole response, and a dropped stream is resumed with
 * `Last-Event-ID` so already-seen `seq` values are not replayed.
 */
export async function readRunEvents(runId: string, onEvent: (event: StreamEvent) => void): Promise<void> {
  let lastSeq = 0;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    let response: Response;
    try {
      response = await fetch(
        `/api/v1/runs/${runId}/events`,
        lastSeq ? { headers: { "Last-Event-ID": String(lastSeq) } } : undefined,
      );
    } catch {
      continue;
    }
    if (!response.ok || !response.body) return;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    try {
      while (!terminal) {
        const chunk = await reader.read();
        if (chunk.done) break;
        if (chunk.value) buffer += decoder.decode(chunk.value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const event = parseEventBlock(block, lastSeq);
          if (!event) continue;
          lastSeq = event.seq;
          onEvent(event);
          if (TERMINAL_EVENTS.has(event.event)) {
            terminal = true;
            break;
          }
        }
      }
    } finally {
      await reader.cancel().catch(() => undefined);
    }
    if (terminal) return;
  }
}
