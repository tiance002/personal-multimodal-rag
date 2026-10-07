export type ViewMode = "document" | "graph";

export type KnowledgeBase = {
  id: string;
  name: string;
  description?: string;
  graph_enabled?: boolean;
  cloud_allowed?: boolean;
};

export type DocumentItem = {
  id: string;
  file_name: string;
  media_type: string;
  index_status?: string;
  version_no?: number;
  active_version_id?: string;
  latest_version_no?: number;
  latest_index_status?: string;
  latest_job?: IngestionJob | null;
};

export type IngestionJob = {
  id: string;
  status: string;
  stage: string;
  progress: number;
  attempts: number;
  max_attempts: number;
  error_code: string | null;
};

export type Conversation = {
  id: string;
  title: string;
  knowledge_base_scope: string[];
  document_scope: string[];
};

export type EvidenceHint = {
  kind: "evidence_hint";
  text: string;
  reason_code: "TABLE_HEADER_UNCONFIRMED";
  source_formats: ("pdf" | "docx")[];
};

export function tableHeaderHint(value: unknown): value is EvidenceHint {
  if (!value || typeof value !== "object") return false;
  const hint = value as Partial<EvidenceHint>;
  return hint.kind === "evidence_hint" && hint.reason_code === "TABLE_HEADER_UNCONFIRMED" &&
    typeof hint.text === "string" && hint.text.trim().length > 0 && hint.text.length <= 600 &&
    Array.isArray(hint.source_formats) && hint.source_formats.length > 0 && hint.source_formats.length <= 2 &&
    hint.source_formats.every((format) => format === "pdf" || format === "docx") &&
    new Set(hint.source_formats).size === hint.source_formats.length;
}

export type Message = {
  id?: string;
  role: "user" | "assistant";
  content: string;
  citations?: string[];
  run_id?: string;
  presentation?: { kind: "clarification"; text: string; clarification_required: true } | EvidenceHint;
};

// A diagnostic attached to an exact user/run, never a factual assistant message.
export function clarificationText(message: Message | undefined): string | null {
  const presentation = message?.presentation;
  return message?.role === "user" && !!message.run_id &&
    !message.citations?.length && presentation?.kind === "clarification" &&
    presentation.clarification_required === true &&
    typeof presentation.text === "string" && presentation.text.trim().length > 0
    ? presentation.text : null;
}

export function evidenceHintText(message: Message | undefined): string | null {
  return message?.role === "user" && !!message.run_id && !message.citations?.length &&
    tableHeaderHint(message.presentation) ? message.presentation.text : null;
}

export type AppState = {
  selectedKnowledgeBaseId: string | null;
  selectedDocumentId: string | null;
  documentScope: string[];
  viewMode: ViewMode;
  activeConversationId: string | null;
};
