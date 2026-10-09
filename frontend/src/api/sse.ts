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
export async function readRunEvents(runId: string, conversationId: string, onEvent: (event: StreamEvent) => void,
                                    options: { signal?: AbortSignal } = {}): Promise<void> {
  let lastSeq = 0;
  const startupDeadline = Date.now() + 10_000;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (options.signal?.aborted) return;
    let response: Response;
    try {
      response = await fetch(
        `/api/v1/runs/${runId}/events?conversation_id=${encodeURIComponent(conversationId)}`,
        { signal: options.signal, ...(lastSeq ? { headers: { "Last-Event-ID": String(lastSeq) } } : {}) },
      );
    } catch {
      continue;
    }
    // The synchronous POST may not have committed its claim yet. Wait only
    // for this known scoped identity; never create/replay a generation request.
    if (response.status === 404 && lastSeq === 0 && Date.now() < startupDeadline) {
      await new Promise<void>((resolve) => {
        const finish = () => { clearTimeout(timer); options.signal?.removeEventListener('abort', finish); resolve(); };
        const timer = setTimeout(finish, 250);
        options.signal?.addEventListener('abort', finish, { once: true });
        if (options.signal?.aborted) finish();
      });
      attempt -= 1;
      continue;
    }
    if (!response.ok || !response.body) return;

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    try {
      while (!terminal && !options.signal?.aborted) {
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
    } catch {
      // A dropped body reader is resumable just like an early EOF. Preserve
      // the scoped Run and last valid sequence; never restart generation.
    } finally {
      await reader.cancel().catch(() => undefined);
    }
    if (terminal) return;
  }
}
