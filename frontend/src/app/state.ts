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

export type Message = {
  id?: string;
  role: "user" | "assistant";
  content: string;
  citations?: string[];
  run_id?: string;
  presentation?: { kind: "clarification"; text: string; clarification_required: true };
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

export type AppState = {
  selectedKnowledgeBaseId: string | null;
  selectedDocumentId: string | null;
  documentScope: string[];
  viewMode: ViewMode;
  activeConversationId: string | null;
};
