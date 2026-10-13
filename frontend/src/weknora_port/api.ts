import type {
  DocumentRow, KnowledgeBase, MessageRow, ModelConfiguration, PortEnvelope,
  RuntimeIdentity, SessionRow, SourceChunk,
} from "./types";

const configured = import.meta.env.VITE_WEKNORA_API_BASE?.trim().replace(/\/$/, "");
const API = configured
  ? configured.endsWith("/api/v1") ? configured : `${configured}/api/v1`
  : "/api/v1";
const EXPECTED_IMPLEMENTATION = "weknora-python-port";

export class PortApiError extends Error {
  constructor(message: string, readonly status = 0, readonly code = "REQUEST_FAILED") { super(message); }
}

async function jsonRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, {
      credentials: "same-origin",
      ...init,
      headers: { ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(init.headers ?? {}) },
    });
  } catch (error) {
    throw new PortApiError(error instanceof Error ? error.message : "无法连接新 Python API");
  }
  let body: unknown;
  try { body = await response.json() as unknown; }
  catch { throw new PortApiError(`服务器响应无法解析 (${response.status})`, response.status); }
  if (!response.ok) {
    const detail = body as { error?: { code?: string; message?: string } };
    throw new PortApiError(detail.error?.message ?? `API 请求失败 (${response.status})`, response.status, detail.error?.code ?? "REQUEST_FAILED");
  }
  const envelope = body as PortEnvelope<T>;
  if (!envelope || !("data" in envelope)) throw new PortApiError("API 返回缺少 data 字段", response.status, "API_CONTRACT_INVALID");
  return envelope.data;
}

export async function assertPortRuntime(): Promise<RuntimeIdentity> {
  const runtime = await jsonRequest<RuntimeIdentity>("/runtime");
  if (runtime.implementation !== EXPECTED_IMPLEMENTATION) {
    throw new PortApiError("当前 API 实例身份不匹配；已阻止访问和修改，未切换到旧服务", 409, "RUNTIME_IDENTITY_MISMATCH");
  }
  return runtime;
}

async function mutate<T>(path: string, init: RequestInit): Promise<T> {
  await assertPortRuntime();
  return jsonRequest<T>(path, init);
}

export const portApi = {
  runtime: assertPortRuntime,
  knowledgeBases: () => jsonRequest<KnowledgeBase[]>("/knowledge-bases"),
  createKnowledgeBase: (name: string) => mutate<KnowledgeBase>("/knowledge-bases", { method: "POST", body: JSON.stringify({ name }) }),
  updateKnowledgeBaseConsent: (kbId: string, cloudAllowed: boolean) => mutate<KnowledgeBase>(`/knowledge-bases/${encodeURIComponent(kbId)}/external-processing`, {
    method: "PATCH", body: JSON.stringify({ cloud_allowed: cloudAllowed }),
  }),
  documents: (kbId: string) => jsonRequest<DocumentRow[]>(`/knowledge-bases/${encodeURIComponent(kbId)}/documents`),
  upload: async (kbId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return mutate<DocumentRow>(`/knowledge-bases/${encodeURIComponent(kbId)}/documents`, { method: "POST", body: form });
  },
  modelConfiguration: () => jsonRequest<ModelConfiguration>("/settings/model-configuration"),
  saveModelConfiguration: (config: ModelConfiguration) => mutate<ModelConfiguration>("/settings/model-configuration", {
    method: "PUT",
    body: JSON.stringify({ revision: config.revision, roles: config.roles.map(({ role, provider, model, base_url, api_key }) => ({
      role, provider, model, base_url, ...(api_key?.trim() ? { api_key } : {}),
    })) }),
  }),
  sessions: () => jsonRequest<SessionRow[]>("/sessions"),
  createSession: (kbIds: string[], documentIds: string[], title = "新对话") => mutate<SessionRow>("/sessions", {
    method: "POST", body: JSON.stringify({ knowledge_base_scope: kbIds, document_scope: documentIds, title }),
  }),
  messages: (sessionId: string, signal?: AbortSignal) => jsonRequest<MessageRow[]>(`/sessions/${encodeURIComponent(sessionId)}/messages`, { signal }),
  source: (chunkId: string) => jsonRequest<SourceChunk>(`/chunks/${encodeURIComponent(chunkId)}`),
};

export function apiBaseForStream(path: string): string {
  return `${API}${path}`;
}
