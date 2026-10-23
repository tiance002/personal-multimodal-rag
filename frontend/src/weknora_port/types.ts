export type PortEnvelope<T> = { data: T; meta?: { request_id?: string } };
export type RuntimeIdentity = { implementation: string; api_version?: string };
export type KnowledgeBase = { id: string; name: string; cloud_allowed: boolean; document_count: number };
export type DocumentRow = { id: string; knowledge_base_id: string; filename: string; status: string; error_code?: string };
export type SessionRow = { id: string; title: string; knowledge_base_scope: string[]; document_scope: string[]; updated_at: string };
export type GenerationPreferences = { reasoning_mode: 'quick' | 'smart'; max_output_tokens: number };
export type GenerationSettings = {
  configuration_version: string; session_revision: number;
  saved: Partial<GenerationPreferences>; effective: GenerationPreferences; defaults: GenerationPreferences;
};
export type ImageProcessingSettings = {
  id: string; image_processing_config: Partial<GenerationPreferences> & { config_version?: number };
  effective_generation?: GenerationPreferences;
};
export type ImageOutcome = { asset_path?: string; asset_sha256?: string; caption_result?: string;
  caption?: { status?: string; error?: string; completion_kind?: string; finish_reason?: string;
    max_output_tokens?: number; usage?: { reasoning_tokens?: number | null; completion_tokens?: number | null; total_tokens?: number | null } } };
export type ImageTaskStatus = { id: string; state: string; source_version: string; error_code?: string; outcomes: Record<string, ImageOutcome> };
export type MessageRow = { id: string; role: "user" | "assistant" | "tool"; content: string; request_id: string; created_at: string; agent_steps?: { reasoning_content?: string }[]; is_completed?: boolean; turn_status?: string; turn_error_code?: string; finish_reason?: string | null; generation_issues?: string[] };
export type ModelRole = "embedding" | "rerank" | "cheap" | "expensive" | "vlm";
export type ModelConfigurationRole = {
  role: ModelRole;
  source?: string;
  provider: string;
  model: string;
  base_url: string;
  key_configured: boolean;
  credential_status?: "UNAVAILABLE" | "CONFIGURED" | "NOT_CONFIGURED";
  credential_error?: string;
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

export type SourceImageInfo = {
  page_number?: number | null;
  source_type?: string;
  caption?: string;
  ocr_text?: string;
  ocr_status?: { status?: string; error?: string };
  caption_status?: { status?: string; error?: string };
};
export type SourceChunk = {
  chunk_id: string;
  document_id: string;
  filename: string;
  text: string;
  source_url: string;
  knowledge_base_id?: string;
  source_version?: string;
  chunk_type?: string;
  chunk_index?: number;
  start?: number;
  end?: number;
  page_number?: number | null;
  image_info?: SourceImageInfo[];
};
