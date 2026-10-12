import type { Conversation, DocumentItem, IngestionJob, KnowledgeBase, Message, EvidenceHint } from "../app/state";
import { clarificationText, tableHeaderHint, validRunDiagnostic } from "../app/state";
import { readRunEvents, type StreamEvent } from "./sse";

const API = "/api/v1";
export type MemoryItem = { id: string; scope_type: 'GLOBAL' | 'SCOPED'; kind: string; fact_key: string; content: string; status: string; origin: string; version: number; sources: unknown[] };
type MemoryScope = { scope_type?: 'GLOBAL' | 'SCOPED'; knowledge_base_scope: string[]; document_scope: string[] };
type RetrievalChannel = { status: string; count: number | null; reason: string | null };
type AnswerResponse = { run_id: string; answer: string; citations: string[]; error_code?: string; presentation?: import("../app/state").RunDiagnostic | null; trace?: { execution_mode?: string; clarification_required?: boolean; evidence_hint?: EvidenceHint | null; metrics?: { retrieval_diagnostics?: { schema_version: string; channels: Record<string, RetrievalChannel> }[] } } };
export type ModelReadiness = { provider: string; status?: string; check_kind?: string; roles?: Record<string, { provider: string; model: string; status: string; reasons: string[] }> };

export function retrievalFailureText(result: AnswerResponse): string | null {
  if (result.error_code !== "NO_CANDIDATES") return null;
  const rows = result.trace?.metrics?.retrieval_diagnostics;
  if (!rows?.length || rows.some(row => row.schema_version !== "retrieval-diagnostic-v1")) {
    return "检索未完成：运行记录缺少阶段诊断，请检查模型与索引状态";
  }
  const channels = rows.flatMap(row => Object.values(row.channels));
  const failed = channels.find(row => row.status === "FAILED" || row.status === "NOT_SENT");
  if (failed) return `检索能力不可用：${failed.reason ?? "RETRIEVAL_UNAVAILABLE"}；请检查模型配置、知识库外发许可和索引状态`;
  const selected = channels.filter(row => row.status !== "NOT_SELECTED");
  if (selected.length > 0 && selected.every(row => row.status === "EMPTY")) {
    return "没有检索到相关内容，请提出与当前资料有关的具体问题";
  }
  return "候选证据未通过回读或选择，请检查索引与版本状态";
}

type Envelope<T> = { data: T; meta: { request_id: string } };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = (await response.json()) as Envelope<T> | { error?: { code?: string; message?: string }; detail?: { type?: string; loc?: unknown[] }[] };
  if (response.status === 422 && "detail" in body && Array.isArray(body.detail) &&
      body.detail.some(row => row.type === "string_too_long" && row.loc?.join(".") === "body.content")) {
    throw new Error("INPUT_LIMIT：问题最多允许 4096 个字符，请缩短或分次提问");
  }
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

export type ModelConfigurationRow = { role: string; provider: string; model: string; base_url: string; key_configured: boolean; api_key?: string };
export type ModelConfiguration = { revision: string; roles: ModelConfigurationRow[] };
export const api = {
  modelConfiguration: () => request<ModelConfiguration>("/settings/model-configuration"),
  saveModelConfiguration: (config: ModelConfiguration) => request<ModelConfiguration>("/settings/model-configuration", {
    method: "PUT", body: JSON.stringify({ revision: config.revision, roles: config.roles.map(({role, provider, model, base_url, api_key}) => ({role, provider, model, base_url, api_key: api_key ?? ""})) }),
  }),
  models: () => request<ModelReadiness>("/settings/models"),
  memorySettings: () => request<{read_enabled: boolean; write_mode: string; automatic_extraction: string}>("/memory/settings"),
  memoryUpdateSettings: (value: {read_enabled: boolean; write_mode: string}) => request("/memory/settings", {method: "PATCH", body: JSON.stringify({read_enabled: value.read_enabled, write_mode: value.write_mode})}),
  memoryList: (scope: MemoryScope) => request<MemoryItem[]>("/memory/list", {method: "POST", body: JSON.stringify(scope)}),
  memorySave: (value: MemoryScope & {kind: string; fact_key: string; content: string}, id: string | null = null) => request(id ? `/memory/items/${id}` : "/memory/items", {method: id ? "PATCH" : "POST", body: JSON.stringify(value)}),
  memoryTransition: (id: string, operation: string, scope: MemoryScope) => request(`/memory/items/${id}/${operation}`, {method: "POST", body: JSON.stringify(scope)}),
  listKnowledgeBases: () => request<KnowledgeBase[]>("/knowledge-bases"),
  createKnowledgeBase: (name: string) => request<KnowledgeBase>("/knowledge-bases", { method: "POST", body: JSON.stringify({ name }) }),
  setKnowledgeBaseCloudAllowed: (id: string, allowed: boolean) => request<KnowledgeBase>(`/knowledge-bases/${id}`, { method: "PATCH", body: JSON.stringify({ cloud_allowed: allowed }) }),
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
    // A server-confirmed scope rejection precedes Run creation. Resync the
    // authoritative scope immediately instead of waiting for a nonexistent Run.
    if (error instanceof Error && error.message.includes("CONVERSATION_SCOPE_CHANGED")) {
      controller.abort();
      throw error;
    }
    // A lost POST response is not evidence of a failed/unsent model request.
    await stream;
    if (recovered) {
      // Recover presentation from scoped committed history, never candidate SSE
      // text, an error-code guess, or another POST that could regenerate.
      if (recovered.error_code && recovered.citations.length === 0) {
        try {
          const history = await api.listMessages(conversationId);
          const message = history.find(row => row.role === "user" && row.run_id === identity);
          const clarification = clarificationText(message);
          if (message?.run_id === identity && validRunDiagnostic(message.presentation)) {
            recovered = { ...recovered, presentation: message.presentation };
          }
          if (recovered.error_code === "NO_CANDIDATES" && clarification) {
            recovered = { ...recovered, answer: clarification,
              trace: { execution_mode: "clarification", clarification_required: true } };
          } else if (recovered.error_code === "INSUFFICIENT_EVIDENCE" && tableHeaderHint(message?.presentation)) {
            recovered = { ...recovered, answer: message.presentation.text,
              trace: { evidence_hint: message.presentation } };
          }
        } catch { /* Unavailable authority retains the explicit unknown result. */ }
      }
      return recovered;
    }
    throw error;
  } finally {
    controller.abort();
    await stream;
  }
}
