export type StreamEvent = { seq: number; event: string; data: Record<string, unknown> };
const TERMINAL_EVENTS = new Set(["answer.completed", "run.failed"]);

function parseEventBlock(block: string, afterSeq: number): StreamEvent | null {
  const lines = block.split(/\r?\n/);
  const field = (name: string) => lines.filter(line => line.startsWith(`${name}:`)).map(line => line.slice(name.length + 1).replace(/^ /, ""));
  const id = field("id")[0], event = field("event")[0], data = field("data").join("\n");
  const seq = Number(id);
  if (!id || !event || !data || !Number.isSafeInteger(seq) || seq <= afterSeq) return null;
  try {
    const parsed: unknown = JSON.parse(data);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return { seq, event, data: parsed as Record<string, unknown> };
  } catch { /* malformed blocks never move the cursor */ }
  return null;
}

export function parseStreamEvents(text: string, afterSeq = 0): StreamEvent[] {
  const events: StreamEvent[] = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const event = parseEventBlock(block, afterSeq);
    if (event) { events.push(event); afterSeq = event.seq; }
  }
  return events;
}

export type RunEventOptions = { signal?: AbortSignal; afterSeq?: number; waitTimeoutMs?: number; startupWaitMs?: number };

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
    if (signal.aborted) finish();
  });
}

/** Resume the same scoped Run. Normal server EOF does not spend error retries. */
export async function readRunEvents(runId: string, conversationId: string, onEvent: (event: StreamEvent) => void,
  options: RunEventOptions = {}): Promise<void> {
  let lastSeq = options.afterSeq ?? 0;
  const waitMs = Math.max(210_000, options.waitTimeoutMs ?? 300_000);
  const deadline = Date.now() + waitMs;
  const startupDeadline = Date.now() + Math.min(10_000, Math.max(0, options.startupWaitMs ?? 10_000));
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) abort();
  let resolveStop = () => {};
  const stopped = new Promise<null>(resolve => { resolveStop = () => resolve(null); });
  controller.signal.addEventListener("abort", resolveStop, { once: true });
  if (controller.signal.aborted) resolveStop();
  const timer = setTimeout(abort, waitMs);
  let errors = 0;
  try {
    while (!controller.signal.aborted && Date.now() < deadline) {
      let abnormal = false;
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      try {
        const response = await Promise.race([fetch(`/api/v1/runs/${encodeURIComponent(runId)}/events?conversation_id=${encodeURIComponent(conversationId)}`, {
          signal: controller.signal, ...(lastSeq ? { headers: { "Last-Event-ID": String(lastSeq) } } : {}),
        }), stopped]);
        if (!response || controller.signal.aborted) return;
        if (response.status === 404 && lastSeq === 0 && Date.now() < startupDeadline) {
          await pause(Math.min(250, startupDeadline - Date.now()), controller.signal);
          continue;
        }
        if ([400, 401, 403, 404, 409, 422].includes(response.status)) return;
        if (!response.ok || !response.body || !response.headers.get("Content-Type")?.includes("text/event-stream")) {
          abnormal = true;
        } else {
          reader = response.body.getReader();
          const cancel = () => { void reader?.cancel().catch(() => undefined); };
          controller.signal.addEventListener("abort", cancel, { once: true });
          try {
            const decoder = new TextDecoder();
            let buffer = "";
            while (!controller.signal.aborted) {
              const chunk = await Promise.race([reader.read(), stopped]);
              if (!chunk || controller.signal.aborted) return;
              if (chunk.done) break;
              buffer += decoder.decode(chunk.value, { stream: true });
              const blocks = buffer.split(/\r?\n\r?\n/);
              buffer = blocks.pop() ?? "";
              for (const block of blocks) {
                const event = parseEventBlock(block, lastSeq);
                if (!event) continue;
                lastSeq = event.seq;
                onEvent(event);
                if (TERMINAL_EVENTS.has(event.event)) return;
              }
            }
          } finally { controller.signal.removeEventListener("abort", cancel); }
        }
      } catch { abnormal = true; }
      finally { if (reader) void reader.cancel().catch(() => undefined); }
      if (controller.signal.aborted) return;
      if (abnormal && ++errors >= 3) return;
      if (!abnormal) errors = 0;
      // Yield even on an empty healthy stream; never spin or replay a POST.
      await pause(Math.min(abnormal ? 250 * 2 ** (errors - 1) : 250, Math.max(0, deadline - Date.now())), controller.signal);
    }
  } finally {
    clearTimeout(timer);
    controller.signal.removeEventListener("abort", resolveStop);
    options.signal?.removeEventListener("abort", abort);
    controller.abort();
  }
}
