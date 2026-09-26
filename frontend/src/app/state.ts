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
};

export type AppState = {
  selectedKnowledgeBaseId: string | null;
  selectedDocumentId: string | null;
  documentScope: string[];
  viewMode: ViewMode;
  activeConversationId: string | null;
};
