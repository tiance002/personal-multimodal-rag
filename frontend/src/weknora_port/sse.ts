import { apiBaseForStream, assertPortRuntime, PortApiError } from "./api";
import type { PortRunIdentity, WeKnoraSSEMessage } from "./types";

function decodeBlock(block: string): WeKnoraSSEMessage | null {
  const lines = block.split(/\r?\n/);
  const eventName = lines.find(line => line.startsWith("event:"))?.slice(6).trim();
  if (eventName !== "message") return null;
  const data = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).replace(/^ /, "")).join("\n");
  if (!data) return null;
  let raw: unknown;
  try { raw = JSON.parse(data) as unknown; }
  catch { throw new PortApiError("SSE message JSON 无效", 502, "SSE_MESSAGE_INVALID"); }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new PortApiError("SSE message 结构无效", 502, "SSE_MESSAGE_INVALID");
  const item = raw as Partial<WeKnoraSSEMessage>;
  if (typeof item.id !== "string" || typeof item.response_type !== "string" ||
      typeof item.content !== "string" || typeof item.done !== "boolean" ||
      !item.data || typeof item.data !== "object" || Array.isArray(item.data)) {
    throw new PortApiError("SSE message 缺少必需字段", 502, "SSE_MESSAGE_INVALID");
  }
  if (item.response_type === "answer" && (typeof item.data.event_id !== "string" || !item.data.event_id)) {
    throw new PortApiError("answer SSE 缺少 data.event_id，无法安全拼接片段", 502, "SSE_EVENT_ID_MISSING");
  }
  return item as WeKnoraSSEMessage;
}

async function* readMessages(response: Response): AsyncGenerator<WeKnoraSSEMessage> {
  if (!response.ok || !response.body || !response.headers.get("content-type")?.includes("text/event-stream")) {
    const text = await response.text().catch(() => "");
    throw new PortApiError(text || `SSE 请求失败 (${response.status})`, response.status, "SSE_REQUEST_FAILED");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const message = decodeBlock(block);
        if (message) yield message;
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      const message = decodeBlock(buffer);
      if (message) yield message;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

type RunCallbacks = {
  signal: AbortSignal;
  onMessage: (event: WeKnoraSSEMessage) => void;
  onReplayStart: () => void;
  onRunIdentity: (identity: PortRunIdentity) => void;
};

function readIdentity(event: WeKnoraSSEMessage): PortRunIdentity | null {
  const requestId = event.data.request_id;
  const assistantMessageId = event.data.assistant_message_id;
  const sessionId = event.data.session_id;
  if (typeof requestId !== "string" || !requestId || requestId !== event.id ||
      typeof assistantMessageId !== "string" || !assistantMessageId ||
      typeof sessionId !== "string" || !sessionId) return null;
  return { requestId, assistantMessageId, sessionId };
}

async function continuePortRun(identity: PortRunIdentity, callbacks: RunCallbacks): Promise<void> {
  let completed = false;
  let requestId = identity.requestId;
  const accept = (event: WeKnoraSSEMessage) => {
    if (event.id !== requestId) throw new PortApiError("SSE request ID 在同一运行中发生变化", 502, "RUN_IDENTITY_CHANGED");
    if (event.data.assistant_message_id !== undefined && event.data.assistant_message_id !== identity.assistantMessageId) {
      throw new PortApiError("SSE assistant message ID 发生变化", 502, "RUN_IDENTITY_CHANGED");
    }
    if (event.data.session_id !== undefined && event.data.session_id !== identity.sessionId) {
      throw new PortApiError("SSE session ID 发生变化", 502, "RUN_IDENTITY_CHANGED");
    }
    if (event.response_type === "complete") completed = true;
    callbacks.onMessage(event);
  };
  let lastFailure: unknown;
  for (let attempt = 0; attempt < 4 && !callbacks.signal.aborted && !completed; attempt++) {
    if (attempt > 0) await new Promise(resolve => setTimeout(resolve, Math.min(250 * 2 ** (attempt - 1), 1500)));
    if (callbacks.signal.aborted) return;
    callbacks.onReplayStart(); // continuation is full replay from offset zero
    try {
      const continuation = await fetch(apiBaseForStream(`/sessions/continue-stream/${encodeURIComponent(identity.sessionId)}?message_id=${encodeURIComponent(identity.assistantMessageId)}`), {
        credentials: "same-origin", signal: callbacks.signal, headers: { Accept: "text/event-stream" },
      });
      for await (const event of readMessages(continuation)) {
        accept(event);
        if (completed) return;
      }
      lastFailure = new Error("续接流在 complete 前结束");
    } catch (error) {
      if (callbacks.signal.aborted) return;
      lastFailure = error;
    }
  }
  if (!completed && !callbacks.signal.aborted) {
    throw new PortApiError(`RUN_STATUS_UNKNOWN：原运行尚未返回 complete；未重新生成。${lastFailure instanceof Error ? lastFailure.message : ""}`, 0, "RUN_STATUS_UNKNOWN");
  }
}

/** One POST, then bounded GET-only resume of the original assistant message. */
export async function runPortChat(sessionId: string, requestBody: {
  content: string; request_id: string; mode: "quick" | "smart";
  knowledge_base_scope: string[]; document_scope: string[];
  reasoning_effort?: string;
  reasoning_mode?: 'quick' | 'smart';
  max_output_tokens?: number;
  accept_language?: string;
}, callbacks: RunCallbacks): Promise<void> {
  await assertPortRuntime();
  const { accept_language, ...body } = requestBody;
  let identity: PortRunIdentity | null = null;
  let requestId = "";
  let postFailure: unknown;
  try {
    const postResponse = await fetch(apiBaseForStream(`/sessions/${encodeURIComponent(sessionId)}/messages`), {
      method: "POST", credentials: "same-origin", signal: callbacks.signal,
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(accept_language ? { "Accept-Language": accept_language } : {}) },
      body: JSON.stringify(body),
    });
    for await (const event of readMessages(postResponse)) {
      if (!requestId) requestId = event.id;
      if (event.id !== requestId) throw new PortApiError("SSE request ID 在同一运行中发生变化", 502, "RUN_IDENTITY_CHANGED");
      if (!identity) {
        const found = readIdentity(event);
        if (found) {
          if (found.sessionId !== sessionId) throw new PortApiError("SSE session ID 与请求不匹配", 502, "RUN_IDENTITY_CHANGED");
          identity = found;
          callbacks.onRunIdentity(found);
        }
      }
      if (identity && event.data.assistant_message_id !== undefined && event.data.assistant_message_id !== identity.assistantMessageId) {
        throw new PortApiError("SSE assistant message ID 发生变化", 502, "RUN_IDENTITY_CHANGED");
      }
      if (identity && event.data.session_id !== undefined && event.data.session_id !== identity.sessionId) {
        throw new PortApiError("SSE session ID 发生变化", 502, "RUN_IDENTITY_CHANGED");
      }
      callbacks.onMessage(event);
      if (event.response_type === "complete") return;
    }
  } catch (error) {
    if (callbacks.signal.aborted) return;
    if (error instanceof PortApiError) throw error;
    postFailure = error;
  }
  if (callbacks.signal.aborted) return;
  if (!identity) {
    throw new PortApiError(`流中断前服务端未提供原 assistant message_id；为避免重复生成，不会重发 POST。${postFailure instanceof Error ? postFailure.message : ""}`, 0, "RUN_IDENTITY_UNAVAILABLE");
  }
  await continuePortRun(identity, callbacks);
}

/** Explicit reconnect control is GET-only and cannot cause a generation POST. */
export async function resumePortChat(identity: PortRunIdentity, callbacks: RunCallbacks): Promise<void> {
  await continuePortRun(identity, callbacks);
}
