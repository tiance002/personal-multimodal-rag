import type { Conversation, DocumentItem, IngestionJob, KnowledgeBase, Message, EvidenceHint } from "../app/state";
import { readRunEvents, type StreamEvent } from "./sse";

const API = "/api/v1";
export type MemoryItem = { id: string; kind: string; fact_key: string; content: string; status: string; origin: string; version: number; sources: unknown[] };
type MemoryScope = { knowledge_base_scope: string[]; document_scope: string[] };
type AnswerResponse = { run_id: string; answer: string; citations: string[]; error_code?: string; trace?: { execution_mode?: string; clarification_required?: boolean; evidence_hint?: EvidenceHint | null } };

type Envelope<T> = { data: T; meta: { request_id: string } };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = (await response.json()) as Envelope<T> | { error?: { code?: string; message?: string } };
  if (!response.ok) throw new Error("error" in body ? `${body.error?.code ?? "REQUEST_FAILED"}: ${body.error?.message ?? "请求失败"}` : "请求失败");
  return (body as Envelope<T>).data;
}

async function responseBody(response: Response): Promise<unknown> {
  const raw = await response.text();
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    const status = `${response.status} ${response.statusText}`.trim();
    throw new Error(`服务器返回了无法解析的响应（${status}），请检查服务端或上传文件大小限制`);
  }
}

export const api = {
  memorySettings: () => request<{read_enabled: boolean; write_mode: string}>("/memory/settings"),
  memoryUpdateSettings: (value: {read_enabled: boolean; write_mode: string}) => request("/memory/settings", {method: "PATCH", body: JSON.stringify(value)}),
  memoryList: (scope: MemoryScope) => request<MemoryItem[]>("/memory/list", {method: "POST", body: JSON.stringify(scope)}),
  memorySave: (value: MemoryScope & {kind: string; fact_key: string; content: string}, id: string | null = null) => request(id ? `/memory/items/${id}` : "/memory/items", {method: id ? "PATCH" : "POST", body: JSON.stringify(value)}),
  memoryTransition: (id: string, operation: string, scope: MemoryScope) => request(`/memory/items/${id}/${operation}`, {method: "POST", body: JSON.stringify(scope)}),
  listKnowledgeBases: () => request<KnowledgeBase[]>("/knowledge-bases"),
  createKnowledgeBase: (name: string) => request<KnowledgeBase>("/knowledge-bases", { method: "POST", body: JSON.stringify({ name }) }),
  listDocuments: (kbId: string) => request<DocumentItem[]>(`/knowledge-bases/${kbId}/documents`),
  getIngestionJob: (jobId: string) => request<IngestionJob>(`/ingestion-jobs/${jobId}`),
  retryIngestionJob: (jobId: string) => request<IngestionJob>(`/ingestion-jobs/${jobId}/retry`, { method: "POST" }),
  listConversations: () => request<Conversation[]>("/conversations"),
  createConversation: (kbId: string, documentScope: string[] = []) => request<Conversation>("/conversations", { method: "POST", body: JSON.stringify({ knowledge_base_scope: [kbId], document_scope: documentScope, title: "新对话" }) }),
  listMessages: (conversationId: string) => request<Message[]>(`/conversations/${conversationId}/messages`),
  updateConversation: (conversationId: string, patch: { knowledge_base_scope?: string[]; document_scope?: string[]; title?: string }) => request<Conversation>(`/conversations/${conversationId}`, { method: "PATCH", body: JSON.stringify(patch) }),
  sendMessage: (conversationId: string, content: string, mode: "quick" | "smart", knowledgeBaseScope: string[], documentScope: string[], requestId: string = crypto.randomUUID()) => request<AnswerResponse>(`/conversations/${conversationId}/messages`, { method: "POST", body: JSON.stringify({ content, mode, request_id: requestId, expected_knowledge_base_scope: knowledgeBaseScope, expected_document_scope: documentScope }) }),
  getContent: (documentId: string) => request<{ content: string; assets: unknown[] }>(`/documents/${documentId}/content`),
  documentSourceUrl: (documentId: string) => `${API}/documents/${documentId}/source`,
  documentPreviewUrl: (documentId: string) => `${API}/documents/${documentId}/preview`,
  getGraph: (documentId: string) => request<{ status: string; nodes: { label: string }[]; edges: { relation: string; quote: string }[] }>(`/documents/${documentId}/graph`),
  rebuildGraph: (documentId: string) => request<{ status: string }>(`/documents/${documentId}/graph/rebuild`, { method: "POST" }),
  getCitation: (runId: string, citationId: string) => request<{ quote: string; current_status: string; locator: Record<string, unknown> }>(`/runs/${runId}/citations/${citationId}`),
  upload: async (kbId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API}/knowledge-bases/${kbId}/documents`, { method: "POST", body: form });
    const body = (await responseBody(response)) as { data?: unknown; error?: { message?: string; code?: string } };
    if (!response.ok) throw new Error(body.error?.message ?? "上传失败");
    return body.data as { job_id: string; document_id: string; status: string };
  },
};

/** One POST, concurrent scoped GET recovery; only committed terminal text. */
export async function sendMessageWithEvents(conversationId: string, content: string, mode: "quick" | "smart",
  knowledgeBaseScope: string[], documentScope: string[], onEvent: (event: StreamEvent) => void): Promise<AnswerResponse> {
  const identity = crypto.randomUUID();
  const controller = new AbortController();
  let recovered: AnswerResponse | undefined;
  const post = api.sendMessage(conversationId, content, mode, knowledgeBaseScope, documentScope, identity);
  const stream = readRunEvents(identity, conversationId, (event) => {
    if ((event.event === 'answer.completed' || event.event === 'run.failed') &&
        typeof event.data.answer === 'string' && Array.isArray(event.data.citations) &&
        event.data.citations.every((label: unknown) => typeof label === 'string')) {
      recovered = { run_id: identity, answer: event.data.answer, citations: event.data.citations as string[],
        error_code: typeof event.data.error_code === 'string' ? event.data.error_code : undefined };
    }
    onEvent(event);
  }, { signal: controller.signal });
  try {
    return await post;
  } catch (error) {
    // A lost POST response is not evidence of a failed/unsent model request.
    await stream;
    if (recovered) return recovered;
    throw error;
  } finally {
    controller.abort();
    await stream;
  }
}
