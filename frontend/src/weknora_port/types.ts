export type PortEnvelope<T> = { data: T; meta?: { request_id?: string } };
export type RuntimeIdentity = { implementation: string; api_version?: string };
export type KnowledgeBase = { id: string; name: string; cloud_allowed: boolean; document_count: number };
export type DocumentRow = { id: string; knowledge_base_id: string; filename: string; status: string };
export type SessionRow = { id: string; title: string; knowledge_base_scope: string[]; document_scope: string[]; updated_at: string };
export type MessageRow = { id: string; role: "user" | "assistant" | "tool"; content: string; request_id: string; created_at: string; is_completed?: boolean; turn_status?: string; turn_error_code?: string };
export type ModelRole = "embedding" | "rerank" | "cheap" | "expensive" | "vlm";
export type ModelConfigurationRole = {
  role: ModelRole;
  provider: string;
  model: string;
  base_url: string;
  key_configured: boolean;
  api_key?: string;
};
export type ModelConfiguration = { revision: string; roles: ModelConfigurationRole[] };

export type WeKnoraSSEMessage = {
  id: string;
  response_type: string;
  content: string;
  done: boolean;
  data: Record<string, unknown>;
  usage?: Record<string, unknown> | null;
  knowledge_references?: unknown[];
};
export type PortRunIdentity = { requestId: string; assistantMessageId: string; sessionId: string };

export type SourceChunk = { chunk_id: string; document_id: string; filename: string; text: string; source_url: string };
