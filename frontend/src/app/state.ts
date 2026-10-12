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
  index_compatibility?: { schema_version: "index-compatibility-v1"; status: "COMPATIBLE" | "INCOMPATIBLE" | "NOT_INDEXED"; reason_code: string | null };
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
  presentation?: ClarificationPresentation | EvidenceHint | RunDiagnostic;
};

export type ProgressStage = "run" | "understand" | "retrieval" | "evidence" | "generation" | "validation";
export type ProgressStatus = "started" | "completed" | "failed" | "truncated";
export type RunProgressData = { schema_version: "run-progress/v1"; stage: ProgressStage; status: ProgressStatus;
  attempt_id?: string; role?: "cheap" | "expensive"; candidate_count?: number; selected_evidence_count?: number;
  reason_code?: string; duration_ms?: number };
export type RunTimeline = { runId: string; lastSeq: number;
  status: "pending" | "completed" | "failed" | "cancelled" | "unknown";
  stages: (Omit<RunProgressData, "status"> & { seq: number; status: ProgressStatus | "ended" })[] };

export function validRunProgress(value: unknown): value is RunProgressData {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Partial<RunProgressData>;
  return row.schema_version === "run-progress/v1" &&
    ["run", "understand", "retrieval", "evidence", "generation", "validation"].includes(row.stage ?? "") &&
    ["started", "completed", "failed", "truncated"].includes(row.status ?? "") &&
    (row.role === undefined || ["cheap", "expensive"].includes(row.role)) &&
    (row.attempt_id === undefined || (typeof row.attempt_id === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(row.attempt_id))) &&
    (row.reason_code === undefined || (typeof row.reason_code === "string" && /^[A-Z_]{1,80}$/.test(row.reason_code))) &&
    [row.candidate_count, row.selected_evidence_count, row.duration_ms].every(n => n === undefined || (Number.isSafeInteger(n) && n >= 0));
}

export function finishRunTimeline(timeline: RunTimeline, status: RunTimeline["status"]): RunTimeline {
  return { ...timeline, status, stages: timeline.stages.map(row => row.status === "started"
    ? { ...row, status: status === "failed" ? "failed" : "ended" } : row) };
}

/** Run identity belongs to the scoped request, never untrusted event fields. */
export function applyRunEvent(timeline: RunTimeline, event: { seq: number; event: string; data: Record<string, unknown> }): RunTimeline {
  if (timeline.status !== "pending" || event.seq <= timeline.lastSeq || !Number.isSafeInteger(event.seq)) return timeline;
  const next = { ...timeline, lastSeq: event.seq };
  if (event.event === "answer.completed") return finishRunTimeline(next, "completed");
  if (event.event === "run.failed") return finishRunTimeline(next, event.data.error_code === "CANCELLED" ? "cancelled" : "failed");
  if (event.event !== "run.progress" || !validRunProgress(event.data)) return next;
  const data = event.data;
  // Copy the public bounded fields only; provider text/thoughts are never stored.
  const row = { schema_version: data.schema_version, stage: data.stage, status: data.status, seq: event.seq,
    attempt_id: data.attempt_id, role: data.role, candidate_count: data.candidate_count,
    selected_evidence_count: data.selected_evidence_count, reason_code: data.reason_code, duration_ms: data.duration_ms };
  const index = next.stages.findIndex(old => old.stage === row.stage && old.attempt_id === row.attempt_id && old.role === row.role);
  const stages = [...next.stages];
  if (index >= 0) {
    // Duplicate/later started cannot reopen a completed stage in the same attempt.
    if (stages[index].status !== "started" && row.status === "started") return next;
    stages[index] = { ...stages[index], ...row };
  } else stages.push(row);
  return { ...next, stages };
}

export type ClarificationPresentation = { schema_version?: "run-presentation-v1"; kind: "clarification"; text: string; clarification_required: true };

export function validClarificationPresentation(value: unknown): value is ClarificationPresentation {
  if (!value || typeof value !== "object") return false;
  const row=value as Partial<ClarificationPresentation>;
  return row.kind==="clarification" && row.clarification_required===true &&
    (row.schema_version===undefined || row.schema_version==="run-presentation-v1") &&
    typeof row.text==="string" && row.text.trim().length>0 && row.text.length<=300;
}

export type RunDiagnostic = { schema_version: "run-presentation-v1"; kind: "run_diagnostic";
  category: "permission_required" | "retrieval_error" | "no_match" | "evidence_unavailable" | "generation_error" | "input_limit" | "failed";
  reason_code: string; text: string };

export function validRunDiagnostic(value: unknown): value is RunDiagnostic {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<RunDiagnostic>;
  return row.schema_version === "run-presentation-v1" && row.kind === "run_diagnostic" &&
    typeof row.category === "string" && ["permission_required", "retrieval_error", "no_match", "evidence_unavailable", "generation_error", "input_limit", "failed"].includes(row.category) &&
    typeof row.reason_code === "string" && /^[A-Z_]{1,80}$/.test(row.reason_code) &&
    typeof row.text === "string" && row.text.trim().length > 0 && row.text.length <= 300;
}

export function runDiagnosticText(message: Message | undefined): string | null {
  return message?.role === "user" && message.run_id && !message.citations?.length && validRunDiagnostic(message.presentation)
    ? message.presentation.text : null;
}

// A diagnostic attached to an exact user/run, never a factual assistant message.
export function clarificationText(message: Message | undefined): string | null {
  const presentation = message?.presentation;
  return message?.role === "user" && !!message.run_id &&
    !message.citations?.length && validClarificationPresentation(presentation)
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
