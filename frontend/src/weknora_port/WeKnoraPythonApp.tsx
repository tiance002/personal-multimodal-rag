import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Alert, Button, ConfigProvider, Drawer, Empty, Input, Layout, Modal, Select, Spin, Tag, Tooltip, Upload } from "antd";
import { BookOutlined, DatabaseOutlined, FileOutlined, InboxOutlined, MenuOutlined, PlusOutlined, SearchOutlined, SendOutlined, StopOutlined } from '@ant-design/icons';
import { WorkbenchNavigation } from './WorkbenchNavigation';
import { DocumentPreview } from './DocumentPreview';
import { SourceReferenceDrawer, sourceReferences } from './SourceReferenceDrawer';
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PortApiError, portApi, resolveMarkdownImageURL } from "./api";
import { cloudErrorMessage } from "./cloudErrors";
import { resumePortChat, runPortChat } from "./sse";
import type {
  DocumentRow, KnowledgeBase, MessageRow, ModelConfiguration, ModelRole,
  PortRunIdentity, SessionRow, WeKnoraSSEMessage, GenerationPreferences, GenerationCapabilities, KnowledgeReference,
} from "./types";
import { KnowledgeBaseConsent } from "./KnowledgeBaseConsent";
import { MemoryPanel } from "./MemoryPanel";
import { GenerationControls, ImageGenerationSettings, DEFAULT_GENERATION } from './GenerationControls';
import { ImageTaskDetails } from './ImageTaskDetails';
import "./styles.css";

type Segment = { eventId: string; text: string; done: boolean };
type TimelineEvent = { type: string; text: string; eventId?: string };
type ChatLine = {
  id: string;
  role: "user" | "assistant";
  content: string;
  segments?: Segment[];
  progress?: TimelineEvent[];
  status?: "running" | "completed" | "truncated" | "empty" | "interrupted" | "failed" | "cancelled" | "unknown";
  error?: boolean;
  identity?: PortRunIdentity;
  references?: KnowledgeReference[];
};

const ROLE_NAMES: Record<ModelRole, string> = {
  embedding: "Embedding",
  rerank: "Rerank",
  cheap: "Cheap answer",
  expensive: "Expensive answer",
  vlm: "Vision",
};
const EMPTY_CONFIG: ModelConfiguration = { revision: "", roles: (Object.keys(ROLE_NAMES) as ModelRole[]).map(role => ({
  role, provider: "", model: "", base_url: "", key_configured: false, api_key: "",
})) };
const ANSWER_LANGUAGE_KEY = "weknora-python-answer-language";
const ANSWER_LANGUAGES = [
  ["zh-CN", "简体中文"], ["zh-TW", "繁體中文"], ["en-US", "English"],
  ["ja-JP", "日本語"], ["ko-KR", "한국어"],
] as const;

function savedAnswerLanguage(): string {
  try {
    const saved = localStorage.getItem(ANSWER_LANGUAGE_KEY);
    return ANSWER_LANGUAGES.some(([locale]) => locale === saved) ? saved! : "zh-CN";
  } catch { return "zh-CN"; }
}
const INTERNAL_SOURCE_ATTRIBUTION_LINE = /^[ \t]*(?:\*\*|__)?source:(?:\*\*|__)?[^\r\n]*\([ \t]*document[ \t]+d[1-9]\d*[ \t]*,[ \t]*chunk[ \t]+c[1-9]\d*[ \t]*\)[^\r\n]*(?:\r?\n|$)/i;
const INTERNAL_CHINESE_ATTRIBUTION = /^[ \t]*(?:\*\*|__)?依据说明(?:：|:)(?:\*\*|__)?[ \t]*\r?\n[ \t]*上述数据直接来源于文档[ \t]*`d[1-9]\d*`[ \t]*中的第[ \t]*\d+[ \t]*段[（(]chunk[ \t]*`c[1-9]\d*`[）)][^\r\n]*(?:\r?\n|$)/i;
const INLINE_SOURCE_ATTRIBUTION = /^(?:(根据提供的课件内容)[（(]文档[ \t]*`d[1-9]\d*`(?:[）)]|$)|(提供的文档片段)[（(]`d[1-9]\d*`(?:[）)]|$)|\(source:[ \t]*d[1-9]\d*[ \t]*,[ \t]*c[1-9]\d*[ \t]*(?:\)|$))/i;

function errorText(error: unknown): string {
  if (error instanceof PortApiError) {
    const mapped = cloudErrorMessage(error.code);
    if (mapped) return mapped;
  }
  return error instanceof Error ? error.message : "请求失败";
}

function assistantBubble(text: string): ChatLine {
  return { id: crypto.randomUUID(), role: "assistant", content: text, error: true, status: "failed" };
}

function answerFromSegments(segments: Segment[] = []) {
  return segments.map(segment => segment.text).join("");
}

function generationStatus(data: { truncated?: unknown; finish_reason?: unknown; generation_issues?: unknown }): "completed" | "truncated" | "empty" {
  const issues = Array.isArray(data.generation_issues) ? data.generation_issues : [];
  const finish = typeof data.finish_reason === "string" ? data.finish_reason.trim().toLowerCase() : "";
  if (data.truncated === true || issues.includes("TRUNCATED") || ["length", "max_tokens", "max_output_tokens"].includes(finish)) return "truncated";
  return issues.includes("EMPTY") ? "empty" : "completed";
}

function eventLabel(event: WeKnoraSSEMessage): string {
  if (event.response_type === "complete" && event.data.status !== "failed" && event.data.status !== "cancelled") {
    const status = generationStatus(event.data);
    if (status === "truncated") return "输出被截断";
    if (status === "empty") return "模型未返回可用答案";
  }
  const text = (event.response_type === "error" ? cloudErrorMessage(event.data.error_code) || event.content : event.content).trim();
  const labels: Record<string, string> = {
    agent_query: "收到问题",
    query_understand: "理解问题",
    tool_call: "调用检索工具",
    tool_result: "检索结果",
    thinking: "推理",
    reflection: "复核",
    references: "整理证据",
    error: "运行错误",
    complete: "完成并已保存",
  };
  return text ? `${labels[event.response_type] ?? event.response_type} · ${text}` : labels[event.response_type] ?? event.response_type;
}

function stripInternalReferenceMarkup(content: string): string {
  const output: string[] = [];
  let cursor = 0;
  let lineStart = 0;
  let insideFence: { marker: "`" | "~"; length: number } | null = null;

  while (cursor < content.length) {
    if (cursor === lineStart) {
      const lineEnd = content.indexOf("\n", cursor);
      const end = lineEnd < 0 ? content.length : lineEnd + 1;
      const line = content.slice(cursor, end);
      const fence = line.match(/^[ \t]{0,3}(`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)/);
      const closingFence = line.match(/^[ \t]{0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n|$)/);
      if (insideFence) {
        output.push(line);
        if (closingFence && closingFence[1][0] === insideFence.marker && closingFence[1].length >= insideFence.length)
          insideFence = null;
        cursor = end;
        lineStart = end;
        continue;
      }
      const attribution = content.slice(cursor).match(INTERNAL_SOURCE_ATTRIBUTION_LINE)
        ?? content.slice(cursor).match(INTERNAL_CHINESE_ATTRIBUTION);
      if (attribution) {
        cursor += attribution[0].length;
        lineStart = cursor;
        continue;
      }
      if (fence) {
        output.push(line);
        insideFence = { marker: fence[1][0] as "`" | "~", length: fence[1].length };
        cursor = end;
        lineStart = end;
        continue;
      }
    }

    const inlineAttribution = content.slice(cursor).match(INLINE_SOURCE_ATTRIBUTION);
    if (inlineAttribution) {
      output.push(inlineAttribution[1] ?? inlineAttribution[2] ?? "");
      cursor += inlineAttribution[0].length;
      continue;
    }

    if (content[cursor] === "`") {
      const run = content.slice(cursor).match(/^`+/)?.[0] ?? "`";
      const close = content.indexOf(run, cursor + run.length);
      if (close >= 0 && !content.slice(cursor + run.length, close).includes("\n")) {
        output.push(content.slice(cursor, close + run.length));
        cursor = close + run.length;
        continue;
      }
    }

    if (content[cursor] === "\n") {
      output.push("\n");
      cursor++;
      lineStart = cursor;
      continue;
    }

    const tag = content.slice(cursor).match(/^<\/?(?:ref|kb|web)\b[^>]*>/i)?.[0];
    if (tag) {
      cursor += tag.length;
      continue;
    }
    if (content[cursor] === "<" && isIncompleteInternalTagSuffix(content.slice(cursor))) break;

    output.push(content[cursor]);
    cursor++;
  }

  return output.join("");
}

function isIncompleteInternalTagSuffix(value: string): boolean {
  const body = value.startsWith("</") ? value.slice(2) : value.slice(1);
  const name = body.match(/^[a-z]*/i)?.[0].toLowerCase() ?? "";
  if (!name) return false;
  const knownNames = ["ref", "kb", "web"];
  const remainder = body.slice(name.length);
  if (knownNames.includes(name)) return !remainder.includes(">");
  if (remainder.length > 0) return false;
  return knownNames.some(candidate => candidate.startsWith(name));
}

function MarkdownImage({ src, alt }: { src?: string; alt?: string }) {
  const resolved = src ? resolveMarkdownImageURL(src) : undefined;
  return resolved ? <img src={resolved} alt={alt ?? ""} /> : null;
}

function MessageContent({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml={false} components={{ img: MarkdownImage }}>
    {stripInternalReferenceMarkup(content)}
  </ReactMarkdown>;
}

export function WeKnoraPythonApp() {
  const [loading, setLoading] = useState(true);
  const [runtimeError, setRuntimeError] = useState("");
  const [page, setPage] = useState<"chat" | "library" | "models" | "memory">("chat");
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [activeSession, setActiveSession] = useState<SessionRow | null>(null);
  const [messages, setMessages] = useState<ChatLine[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedBases, setSelectedBases] = useState<string[]>([]);
  const [selectedDocuments, setSelectedDocuments] = useState<string[]>([]);
  const [scopeSaving, setScopeSaving] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const scopeControl = useRef<HTMLDivElement | null>(null);
  const [config, setConfig] = useState<ModelConfiguration>(EMPTY_CONFIG);
  const [answerLanguage, setAnswerLanguage] = useState(savedAnswerLanguage);
  const [busy, setBusy] = useState(false);
  const [generation, setGeneration] = useState<GenerationPreferences>(DEFAULT_GENERATION);
  const [effectiveGeneration, setEffectiveGeneration] = useState<GenerationPreferences | null>(null);
  const [generationCapabilities, setGenerationCapabilities] = useState<GenerationCapabilities | null>(null);
  const [generationSaving, setGenerationSaving] = useState(false);
  const settingsRead = useRef(0);
  const [notice, setNotice] = useState("正在确认 Python 服务身份");
  const [query, setQuery] = useState("");
  const [newKbName, setNewKbName] = useState("");
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [createKbOpen, setCreateKbOpen] = useState(false);
  const [creatingKb, setCreatingKb] = useState(false);
  const [createKbError, setCreateKbError] = useState("");
  const [librarySearch, setLibrarySearch] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [sourceReference, setSourceReference] = useState<KnowledgeReference | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const transcriptPositions = useRef(new Map<string, number>());
  const followAnswer = useRef(true);
  const restoringTranscript = useRef(false);
  const sessionRead = useRef(0);
  const [uploadKbId, setUploadKbId] = useState("");
  const [documentRefresh, setDocumentRefresh] = useState(0);
  const [familiarDocument, setFamiliarDocument] = useState<DocumentRow | null>(null);
  const [configError, setConfigError] = useState("");
  const activeAbort = useRef<AbortController | null>(null);
  const activeIdentity = useRef<PortRunIdentity | null>(null);
  const terminalErrorMessages = useRef(new Set<string>());

  const refreshBases = useCallback(async () => setBases(await portApi.knowledgeBases()), []);
  const refreshSessions = useCallback(async () => setSessions(await portApi.sessions()), []);
  useEffect(() => {
    let disposed = false;
    void (async () => {
      try {
        await portApi.runtime();
        const [kbRows, sessionRows, modelConfig] = await Promise.all([
          portApi.knowledgeBases(), portApi.sessions(), portApi.modelConfiguration(),
        ]);
        if (disposed) return;
        setBases(kbRows);
        setSessions(sessionRows);
        setConfig({ ...modelConfig, roles: (Object.keys(ROLE_NAMES) as ModelRole[]).map(role => ({
          ...(modelConfig.roles.find(row => row.role === role) ?? EMPTY_CONFIG.roles.find(row => row.role === role)!),
          api_key: "",
        })) });
        setNotice("已连接到 WeKnora Python API");
      } catch (error) {
        if (!disposed) {
          setRuntimeError(errorText(error));
          setNotice("Python 服务身份未确认；所有写入已停用");
        }
      } finally { if (!disposed) setLoading(false); }
    })();
    return () => { disposed = true; activeAbort.current?.abort(); };
  }, []);

  useEffect(() => {
    if (!scopeOpen) return;
    const outside = (event: PointerEvent) => {
      if (!scopeControl.current?.contains(event.target as Node)) setScopeOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setScopeOpen(false);
        scopeControl.current?.querySelector<HTMLButtonElement>(".wp-scope-trigger")?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [scopeOpen]);

  const knowledgeBaseScopeLocked = Boolean(activeSession);
  const documentScopeDirty = Boolean(activeSession) && JSON.stringify([...selectedDocuments].sort()) !== JSON.stringify([...(activeSession?.document_scope ?? [])].sort());
  const canSubmit = !busy && !generationSaving && !scopeSaving && !documentScopeDirty && !loading && !runtimeError && selectedBases.length > 0 && query.trim().length > 0;

  const documentKnowledgeBaseIds = page === "library"
    ? (uploadKbId ? [uploadKbId] : [])
    : page === "chat" ? selectedBases : [];
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setDocuments([]);
    if (documentKnowledgeBaseIds.length === 0) return () => { cancelled = true; };

    const loadDocuments = async () => {
      try {
        const groups = await Promise.all(documentKnowledgeBaseIds.map(id => portApi.documents(id)));
        if (cancelled) return;
        const rows = groups.flat();
        setDocuments(rows);
        if (rows.some(row => row.status === "pending" || row.status === "processing")) {
          timer = setTimeout(() => void loadDocuments(), 1000);
        }
      } catch (error) {
        if (!cancelled) setNotice(`资料列表读取失败：${errorText(error)}`);
      }
    };
    void loadDocuments();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [documentKnowledgeBaseIds.join("\u0000"), page, documentRefresh]);

  const openSession = async (session: SessionRow) => {
    if (scopeSaving || busy) return;
    const readTicket = ++sessionRead.current;
    restoringTranscript.current = true;
    setPage("chat"); setNavigationOpen(false); setSourceReference(null);
    setScopeOpen(false);
    setActiveSession(session);
    const settingsTicket = ++settingsRead.current;
    setGeneration(DEFAULT_GENERATION); setEffectiveGeneration(null); setGenerationCapabilities(null); setGenerationSaving(true);
    void portApi.generationSettings(session.id).then(row => {
      if (settingsTicket !== settingsRead.current) return;
      setGeneration({ reasoning_mode: row.saved.reasoning_mode ?? row.defaults.reasoning_mode,
        max_output_tokens: row.saved.max_output_tokens ?? 0 });
      setEffectiveGeneration(row.effective);
      setGenerationCapabilities(row.capabilities ?? null);
    }).catch(error => { if (settingsTicket === settingsRead.current) setNotice(`回答设置读取失败：${errorText(error)}`); })
      .finally(() => { if (settingsTicket === settingsRead.current) setGenerationSaving(false); });
    setSelectedBases(session.knowledge_base_scope);
    setSelectedDocuments(session.document_scope);
    setMessages([]); setHistoryLoading(true);
    setNotice("正在读取会话历史");
    try {
      const history = await portApi.messages(session.id);
      if (readTicket !== sessionRead.current) return;
      setMessages(history.map(row => {
        const mappedError = row.turn_status === "error" ? cloudErrorMessage(row.turn_error_code) : "";
        return { id: row.id, role: row.role === "user" ? "user" : "assistant", content: mappedError || row.content,
          status: row.role === "user" ? "completed" : row.turn_error_code === "MODEL_CALL_CANCELLED" ? "cancelled" : row.turn_status === "error" ? "failed" : row.is_completed ? generationStatus(row) : "unknown",
          error: row.turn_status === "error",
          references: row.turn_status === "error" ? [] : sourceReferences(row.knowledge_references, row.knowledge_base_scope ?? session.knowledge_base_scope, row.document_scope),
          progress: (row.agent_steps ?? []).filter(step => step.reasoning_content).map(step => ({ type: "thinking", text: step.reasoning_content ?? "" })),
        };
      }));
      setNotice("已读取保存的会话历史；实时阶段仅显示当前连接收到的事件");
    } catch (error) { if (readTicket === sessionRead.current) setMessages([assistantBubble(`历史读取失败：${errorText(error)}`)]); }
    finally { if (readTicket === sessionRead.current) setHistoryLoading(false); }
  };

  const newSession = () => {
    if (scopeSaving || busy) return;
    sessionRead.current++; restoringTranscript.current = true; setHistoryLoading(false); setNavigationOpen(false); setSourceReference(null);
    setScopeOpen(false);
    activeAbort.current?.abort();
    activeAbort.current = null;
    activeIdentity.current = null;
    setActiveSession(null);
    settingsRead.current++;
    setGeneration(DEFAULT_GENERATION); setEffectiveGeneration(null); setGenerationCapabilities(null); setGenerationSaving(false);
    setMessages([]);
    setSelectedBases([]);
    setSelectedDocuments([]);
    setNotice("新对话：选择知识库和资料范围后提问");
    setPage("chat");
  };

  const toggleBase = (id: string) => {
    setSelectedBases(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]);
    setSelectedDocuments([]);
  };
  const toggleDocument = (id: string) => setSelectedDocuments(current => current.includes(id)
    ? current.filter(value => value !== id) : [...current, id]);

  const applyDocumentScope = async (documentIds: string[]) => {
    if (!activeSession || busy || scopeSaving) return;
    setScopeSaving(true);
    try {
      const saved = await portApi.updateDocumentScope(activeSession.id, documentIds);
      setActiveSession(saved);
      setSelectedDocuments(saved.document_scope);
      setSessions(current => current.map(session => session.id === saved.id ? saved : session));
      setScopeOpen(false);
      setNotice(saved.document_scope.length ? "已锁定所选文档；后续问题使用此范围" : "已取消文档限定；后续问题检索当前知识库");
    } catch (error) { setNotice(`文档范围未保存：${errorText(error)}`); }
    finally { setScopeSaving(false); }
  };

  const updateMessage = (messageId: string, update: (message: ChatLine) => ChatLine) => {
    setMessages(current => current.map(message => message.id === messageId ? update(message) : message));
  };

  const applyEvent = (messageId: string, event: WeKnoraSSEMessage) => {
    if (event.response_type === 'generation_settings' && event.data.effective) {
      const effective = event.data.effective as GenerationPreferences;
      if (['quick', 'smart'].includes(effective.reasoning_mode) && Number.isSafeInteger(effective.max_output_tokens)) setEffectiveGeneration(effective);
    }

    if (event.response_type === "complete") {
      const hadTerminalError = terminalErrorMessages.current.has(messageId);
      terminalErrorMessages.current.delete(messageId);
      if (event.data.status === "cancelled") setNotice("生成已取消，服务端已保存终态");
      else if (event.data.status === "failed" && !hadTerminalError) setNotice("生成失败，服务端已保存终态");
      else if (!hadTerminalError && generationStatus(event.data) === "truncated") setNotice("输出被截断，已有内容已保存");
      else if (!hadTerminalError && generationStatus(event.data) === "empty") setNotice("模型未返回可用答案，运行终态已保存");
      else if (!hadTerminalError) setNotice("回答已完成并保存");
    } else if (event.response_type === "error") {
      const nonTerminalStreamError = event.data.non_terminal === true && event.data.stage === "chat_completion_stream";
      if (!nonTerminalStreamError) {
        terminalErrorMessages.current.add(messageId);
        setNotice(cloudErrorMessage(event.data.error_code) || event.content || "运行发生错误");
      }
    }
    updateMessage(messageId, previous => {
      const progress = [...(previous.progress ?? [])];
      if (event.response_type === 'references') {
        return { ...previous, references: sourceReferences(event.knowledge_references ?? event.data.references, selectedBases, selectedDocuments), progress: [...progress, { type: 'references', text: eventLabel(event) }] };
      }
      if (event.response_type !== "answer") progress.push({
        type: event.response_type,
        text: eventLabel(event),
        eventId: typeof event.data.event_id === "string" ? event.data.event_id : undefined,
      });
      if (event.response_type === "answer") {
        const eventId = event.data.event_id;
        if (typeof eventId !== "string" || !eventId) return { ...previous, status: "unknown", error: true };
        const segments = [...(previous.segments ?? [])];
        const index = segments.findIndex(segment => segment.eventId === eventId);
        if (index < 0) segments.push({ eventId, text: event.content, done: event.done });
        else segments[index] = { ...segments[index], text: segments[index].text + event.content, done: segments[index].done || event.done };
        return { ...previous, segments, content: answerFromSegments(segments), progress, status: "running" };
      }
      if (event.response_type === "complete") {
        if (event.data.status === "cancelled") {
          return { ...previous, progress, status: "cancelled" };
        }
        if (event.data.status === "failed" || previous.error) {
          return { ...previous, progress, status: "failed", error: true, references: [] };
        }
        const finalContent = typeof event.data.final_content === "string" ? event.data.final_content : answerFromSegments(previous.segments);
        return { ...previous, content: finalContent, progress, status: generationStatus(event.data) };
      }
      if (event.response_type === "error") {
        if (event.data.non_terminal === true && event.data.stage === "chat_completion_stream") {
          return { ...previous, progress: progress.slice(-80) };
        }
        return { ...previous, content: cloudErrorMessage(event.data.error_code) || event.content || "运行发生错误", progress: progress.slice(-80), error: true, references: [] };
      }
      return { ...previous, progress: progress.slice(-80) };
    });
  };

  const run = async (line: ChatLine, sessionId: string, requestId: string, content: string) => {
    const controller = new AbortController();
    activeAbort.current = controller;
    setBusy(true);
    try {
      await runPortChat(sessionId, {
        content, request_id: requestId, mode: "quick", ...generation,
        accept_language: answerLanguage,
        knowledge_base_scope: selectedBases, document_scope: selectedDocuments,
      }, {
        signal: controller.signal,
        onRunIdentity: identity => {
          activeIdentity.current = identity;
          updateMessage(line.id, previous => ({ ...previous, identity }));
        },
        onReplayStart: () => updateMessage(line.id, previous => ({ ...previous, content: "", segments: [], progress: [], references: [], status: "running" })),
        onMessage: event => applyEvent(line.id, event),
      });
      if (controller.signal.aborted) updateMessage(line.id, previous => ["completed", "truncated", "empty", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "interrupted" });
      else updateMessage(line.id, previous => ["completed", "truncated", "empty", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "unknown" });
      await refreshSessions();
    } catch (error) {
      if (controller.signal.aborted) updateMessage(line.id, previous => ["completed", "truncated", "empty", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "interrupted" });
      else updateMessage(line.id, previous => ({ ...previous, status: "unknown", error: true,
        content: previous.content || errorText(error),
        progress: [...(previous.progress ?? []), { type: "error", text: errorText(error) }] }));
      setNotice(errorText(error));
    } finally {
      if (activeAbort.current === controller) activeAbort.current = null;
      setBusy(false);
    }
  };

  const saveAnswerSettings = async (reset: boolean) => {
    const next = reset ? DEFAULT_GENERATION : generation;
    if (!activeSession) { setGeneration(next); setEffectiveGeneration(null); return; }
    setGenerationSaving(true);
    try {
      const row = await portApi.saveGenerationSettings(activeSession.id, next, reset);
      setGeneration({ reasoning_mode: row.saved.reasoning_mode ?? row.defaults.reasoning_mode,
        max_output_tokens: row.saved.max_output_tokens ?? 0 });
      setEffectiveGeneration(row.effective);
      setGenerationCapabilities(row.capabilities ?? null);
      setNotice('回答设置已保存，仅影响后续请求。');
    } catch (error) { setNotice(`回答设置保存失败：${errorText(error)}`); }
    finally { setGenerationSaving(false); }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setScopeOpen(false);
    followAnswer.current = true;
    const content = query.trim();
    setQuery("");
    setRuntimeError("");
    const requestId = crypto.randomUUID();
    let session = activeSession;
    const userLine: ChatLine = { id: crypto.randomUUID(), role: "user", content };
    const assistantLine: ChatLine = { id: crypto.randomUUID(), role: "assistant", content: "", segments: [], progress: [], references: [], status: "running" };
    setNotice("正在提交问题");
    setMessages(current => [...current, userLine, assistantLine]);
    try {
      if (!session) {
        session = await portApi.createSession(selectedBases, selectedDocuments, content.slice(0, 48), generation);
        setActiveSession(session);
        setSessions(current => [session!, ...current.filter(row => row.id !== session!.id)]);
      }
      await run(assistantLine, session.id, requestId, content);
    } catch (error) {
      updateMessage(assistantLine.id, previous => ({ ...previous, content: errorText(error), status: "failed", error: true }));
      setNotice(errorText(error));
      setBusy(false);
    }
  };

  const resume = async (line: ChatLine) => {
    if (!line.identity || busy) return;
    setNotice("正在续读原回答");
    const controller = new AbortController();
    activeAbort.current = controller;
    setBusy(true);
    try {
      await resumePortChat(line.identity, {
        signal: controller.signal,
        onRunIdentity: () => {},
        onReplayStart: () => updateMessage(line.id, previous => ({ ...previous, content: "", segments: [], progress: [], references: [], status: "running" })),
        onMessage: event => applyEvent(line.id, event),
      });
      if (!controller.signal.aborted) updateMessage(line.id, previous => ["completed", "truncated", "empty", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "unknown" });
    } catch (error) {
      updateMessage(line.id, previous => ({ ...previous, status: controller.signal.aborted ? "interrupted" : "unknown", error: true,
        content: previous.content || errorText(error),
        progress: [...(previous.progress ?? []), { type: "error", text: errorText(error) }] }));
    } finally { if (activeAbort.current === controller) activeAbort.current = null; setBusy(false); }
  };

  const upload = async (file?: File) => {
    if (!file || !uploadKbId || uploading) return;
    setUploading(true); setUploadError("");
    const targetKbId = uploadKbId;
    try {
      const row = await portApi.upload(targetKbId, file);
      if (page === "library" && uploadKbId === targetKbId) {
        setDocuments(current => [...current.filter(item => item.id !== row.id), row]);
        setDocumentRefresh(current => current + 1);
      }
      setBases(await portApi.knowledgeBases());
      setNotice(`已提交资料：${row.filename} · ${row.status}`);
    } catch (error) { setUploadError(errorText(error)); setNotice(`上传失败：${errorText(error)}`); }
    finally { setUploading(false); }
  };

  const createKb = async (event: FormEvent) => {
    event.preventDefault();
    if (!newKbName.trim() || creatingKb) return;
    setCreatingKb(true); setCreateKbError("");
    try {
      const row = await portApi.createKnowledgeBase(newKbName.trim());
      setBases(current => [...current, row]);
      setNewKbName(""); setCreateKbOpen(false); setUploadKbId(row.id);
      setNotice(`已创建知识库：${row.name}`);
    } catch (error) { setCreateKbError(errorText(error)); setNotice(`创建失败：${errorText(error)}`); }
    finally { setCreatingKb(false); }
  };

  const saveConfig = async (event: FormEvent) => {
    event.preventDefault();
    setConfigError("");
    try {
      const saved = await portApi.saveModelConfiguration(config);
      setConfig({ ...saved, roles: saved.roles.map(row => ({ ...row, api_key: "" })) });
      setNotice("模型角色配置已保存；服务端只返回密钥配置状态");
    } catch (error) { setConfigError(errorText(error)); }
  };

  const openMemoryDocument = async (document: import("./api").MemoryDocument) => {
    await portApi.runtime();
    const rows = await portApi.documents(document.knowledge_base_id);
    const original = rows.find(row => row.id === document.knowledge_id);
    if (!original) throw new Error("文档不存在或当前无权访问");
    setFamiliarDocument(original); setUploadKbId(document.knowledge_base_id); setPage("library");
    // Browsing a document must not change an existing chat's saved retrieval scope.
  };

  const stopGeneration = async () => {
    const identity = activeIdentity.current;
    if (!identity) { setNotice("正在确认原始运行，请稍后停止"); return; }
    setNotice("正在请求停止原始生成");
    try {
      await portApi.stopSession(identity.sessionId, identity.assistantMessageId);
    } catch (failure) { setNotice(`停止失败：${errorText(failure)}`); }
  };

  const stopReading = () => {
    activeAbort.current?.abort();
    setNotice("已停止读取当前 SSE；服务端运行不变，可继续读取同一 assistant message");
  };

  const visibleDocuments = documents.filter(doc => documentKnowledgeBaseIds.includes(doc.knowledge_base_id));
  const selectedNames = useMemo(() => bases.filter(base => selectedBases.includes(base.id)).map(base => base.name), [bases, selectedBases]);

  const historyKey = activeSession?.id ?? 'new';
  useEffect(() => {
    const node = transcript.current;
    if (!node || page !== 'chat' || historyLoading) return;
    const savedPosition = transcriptPositions.current.get(historyKey);
    const frame = requestAnimationFrame(() => {
      node.scrollTop = savedPosition ?? node.scrollHeight;
      restoringTranscript.current = false;
      followAnswer.current = node.scrollHeight - node.scrollTop - node.clientHeight < 120;
    });
    return () => cancelAnimationFrame(frame);
  }, [historyKey, page, historyLoading]);
  useEffect(() => {
    const node = transcript.current;
    if (node && !historyLoading && !restoringTranscript.current && followAnswer.current) node.scrollTop = node.scrollHeight;
  }, [messages, historyLoading]);
  const navigation = <WorkbenchNavigation page={page} sessions={sessions} activeId={activeSession?.id} disabled={busy || scopeSaving}
    onPage={next => { setPage(next); setNavigationOpen(false); }} onNew={newSession} onSession={session => void openSession(session)} />;
  const activeLibrary = bases.find(base => base.id === uploadKbId);
  const matchingBases = bases.filter(base => base.name.toLocaleLowerCase().includes(librarySearch.toLocaleLowerCase()));
  const matchingDocuments = visibleDocuments.filter(doc => doc.filename.toLocaleLowerCase().includes(librarySearch.toLocaleLowerCase()));

  return <ConfigProvider theme={{ token: { colorPrimary: '#16834c', colorInfo: '#16834c', colorText: '#20342a', colorTextSecondary: '#66756c', colorBorder: '#dfe7e2', borderRadius: 8, fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif', fontSize: 14 }, components: { Menu: { itemSelectedBg: '#e8f5ed', itemSelectedColor: '#16834c', itemHeight: 42 }, Button: { defaultShadow: 'none', primaryShadow: 'none' } } }}>
  <Layout className="wp-shell">
    <Layout.Sider width={256} className="wp-sidebar" theme="light">{navigation}</Layout.Sider>
    <Drawer className="wp-mobile-navigation" title="导航与历史会话" placement="left" open={navigationOpen} onClose={() => setNavigationOpen(false)} size={280} destroyOnHidden><div className="wp-sidebar-content">{navigation}</div></Drawer>
    <Layout.Content className="wp-main">
      <header className="wp-topbar"><div className="wp-topbar-title"><Button className="wp-mobile-menu" type="text" icon={<MenuOutlined aria-hidden="true" />} aria-label="打开导航" onClick={() => setNavigationOpen(true)} /><span className="wp-live-dot" /><span>{page === "chat" ? activeSession?.title || "新对话" : page === "library" ? "知识库与资料" : page === "memory" ? "长期记忆" : "模型角色"}</span></div><Tooltip title={notice}><span className="wp-notice" role="status">{loading ? "正在加载" : notice}</span></Tooltip></header>
      {runtimeError && <Alert className="wp-banner" type="error" showIcon title="Python API 不可用" description={runtimeError} action={<Button onClick={() => { setLoading(true); setRuntimeError(""); void portApi.runtime().then(() => location.reload()).catch(error => { setRuntimeError(errorText(error)); setLoading(false); }); }}>重新检查</Button>} />}
      {loading && <div className="wp-page-loading"><Spin description="正在加载知识工作台" /></div>}

      {page === "chat" && <section className="wp-chat-page">


        <div className="wp-transcript" ref={transcript} aria-live="polite" onScroll={event => {
          if (historyLoading || restoringTranscript.current) return;
          const node = event.currentTarget;
          transcriptPositions.current.set(historyKey, node.scrollTop);
          followAnswer.current = node.scrollHeight - node.scrollTop - node.clientHeight < 120;
        }}>
          {historyLoading && <div className="wp-drawer-loading"><Spin description="正在读取会话" /></div>}
          {!historyLoading && messages.length === 0 && <div className="wp-welcome"><span className="wp-welcome-icon"><BookOutlined aria-hidden="true" /></span><h1>从你的资料中寻找答案</h1><p>选择检索范围，快速问答会显示实际检索与回答进度。</p><div className="wp-scope-tags">{selectedNames.map(name => <span key={name}>{name}</span>)}</div></div>}
          {messages.map(message => <article key={message.id} className={`wp-message ${message.role} ${message.error ? "error" : ""}`}>
            <div className="wp-message-label">{message.role === "user" ? "你" : message.error ? "运行提示" : "WeKnora"}</div>
            {message.role === "user" ? <p className="wp-message-content">{message.content}</p> : <>
              {message.progress?.length ? <details className="wp-progress" open={message.status === "running"}><summary>实际执行事件 · {message.progress.length}</summary><ol>{message.progress.map((event, index) => <li key={`${event.eventId ?? event.type}-${index}`}><span>{event.type}</span><p>{event.text}</p></li>)}</ol></details> : null}
              <div className="wp-message-content"><MessageContent content={message.content} /></div>
              {Boolean(message.references?.length) && <div className="wp-reference-list" aria-label="回答引用"><span>引用来源</span>{message.references!.map((reference, index) => <Tooltip key={reference.chunk_id} title={reference.title || '查看原始片段'}><Button className="wp-citation" size="small" icon={<FileOutlined aria-hidden="true" />} onClick={() => setSourceReference(reference)}>{index + 1}. {reference.title || '来源片段'}</Button></Tooltip>)}</div>}
              {message.status === "running" && <span className="wp-running"><Spin size="small" />正在接收同一运行的回答</span>}
              {message.status === "interrupted" && <div className="wp-run-actions"><span>已断开读取，服务端运行未被重发</span>{message.identity && <Button size="small" onClick={() => void resume(message)} disabled={busy}>继续读取原回答</Button>}</div>}
              {message.status === "cancelled" && <p className="wp-run-actions">已停止生成；原始运行已保存取消状态。</p>}
              {message.status === "failed" && <p className="wp-run-actions">生成失败；原始运行的错误已保存。</p>}
              {message.status === "truncated" && <p className="wp-run-actions">输出被截断；已有内容已保存，未重新生成。</p>}
              {message.status === "empty" && <p className="wp-run-actions">模型未返回可用答案；运行终态已保存，未重新生成。</p>}
              {message.status === "unknown" && <div className="wp-run-actions"><span>尚未收到 complete，结果状态待确认；没有重新生成。</span>{message.identity && <Button size="small" onClick={() => void resume(message)} disabled={busy}>GET 续读原回答</Button>}</div>}
              {message.role === "assistant" && message.status === "completed" && <small className="wp-complete">回答已由服务端完成并保存</small>}
            </>}
          </article>)}
        </div>
        <div className="wp-composer-wrap">
          <div className="wp-answer-preferences">
            <GenerationControls value={generation} effective={effectiveGeneration} capabilities={generationCapabilities} disabled={busy || generationSaving}
              onChange={next => { setGeneration(next); setEffectiveGeneration(null); }}/>
            <div className="wp-generation-actions">
              <Button size="small" disabled={busy || generationSaving || !activeSession} onClick={() => void saveAnswerSettings(false)}>保存本会话设置</Button>
              <Button size="small" type="text" disabled={busy || generationSaving} onClick={() => void saveAnswerSettings(true)}>恢复默认</Button>
              <small>{activeSession ? '保存后刷新及重新进入会话仍有效；本次发送使用当前设置。' : '首次发送时保存到新会话。'}</small>
            </div>
          </div>
          <form className="wp-composer" onSubmit={event => void submit(event)}>
            <Input.TextArea aria-label="问题" autoSize={{ minRows: 2, maxRows: 7 }} value={query} onChange={event => setQuery(event.target.value)} placeholder={selectedBases.length ? "询问当前资料…" : "选择知识库后提问…"} disabled={busy || Boolean(runtimeError)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
            <div className="wp-composer-actions">
              <div className="wp-composer-tools">
                <div className="wp-scope-control" ref={scopeControl}>
                  <Button className="wp-scope-trigger" aria-label="选择知识库和文件" aria-haspopup="dialog" aria-expanded={scopeOpen} aria-controls="wp-scope-picker" onClick={() => setScopeOpen(current => !current)} icon={<DatabaseOutlined aria-hidden="true" />}>选择资料</Button>
                  {scopeOpen && <div className="wp-scope-card" id="wp-scope-picker" role="dialog" aria-label="选择知识库和文件">
          <div className="wp-scope-heading"><div><strong>检索范围</strong><small>{knowledgeBaseScopeLocked ? "知识库范围已保存；文档范围可切换或取消，应用后用于后续问题" : "先选择一个或多个知识库，再限定文档"}</small></div><span>{selectedBases.length} 个知识库 · {selectedDocuments.length} 个资料</span><button type="button" className="wp-scope-close" disabled={scopeSaving} onClick={() => setScopeOpen(false)}>完成选择</button></div>
          <div className="wp-scope-grid">
            <div className="wp-scope-column"><label>知识库</label>{bases.map(base => <label key={base.id} className="wp-check-row"><input type="checkbox" checked={selectedBases.includes(base.id)} disabled={knowledgeBaseScopeLocked || busy || scopeSaving} onChange={() => toggleBase(base.id)} /><span><strong>{base.name}</strong><small>{base.document_count} 份资料 · {base.cloud_allowed ? "允许外发" : "仅本地（该库内容不外发）"}</small></span></label>)}{!bases.length && <p className="wp-empty-small">尚无知识库，请到资料库创建</p>}</div>
            <div className="wp-scope-column"><label>资料文件</label>{visibleDocuments.map(doc => <label key={doc.id} className="wp-check-row"><input type="checkbox" checked={selectedDocuments.includes(doc.id)} disabled={busy || scopeSaving || !selectedBases.includes(doc.knowledge_base_id)} onChange={() => toggleDocument(doc.id)} /><span><strong>{doc.filename}</strong><small>{doc.status}{doc.error_code ? ` · 错误码 ${doc.error_code}` : ""}</small></span></label>)}{selectedBases.length > 0 && visibleDocuments.length === 0 && <p className="wp-empty-small">所选知识库暂无资料</p>}</div>
          </div>
          {activeSession && <div className="wp-scope-heading"><button type="button" disabled={busy || scopeSaving || !documentScopeDirty} onClick={() => void applyDocumentScope(selectedDocuments)}>{scopeSaving ? "正在保存…" : "应用文档范围"}</button><button type="button" disabled={busy || scopeSaving || (!selectedDocuments.length && !activeSession.document_scope.length)} onClick={() => void applyDocumentScope([])}>取消文档限定</button><small>{documentScopeDirty ? "文档选择尚未应用，保存后可继续提问" : "后续追问继承已保存的文档范围"}</small></div>}
        </div>}
                </div>
                <span className="wp-composer-scope" title={selectedNames.join("、")}>{selectedNames.length ? `${selectedNames.join("、")}${selectedDocuments.length ? ` · ${selectedDocuments.length} 个文件` : " · 全部文件"}${documentScopeDirty ? " · 待应用" : ""}` : "未选择知识库"}</span>
              </div>
              {busy ? <><Button danger icon={<StopOutlined aria-hidden="true" />} onClick={() => void stopGeneration()}>停止生成</Button><Button onClick={stopReading}>停止读取</Button></> : <span className="wp-composer-hint">Enter 发送 · Shift+Enter 换行</span>}
              <Button type="primary" htmlType="submit" icon={<SendOutlined aria-hidden="true" />} disabled={!canSubmit}>发送</Button>
            </div>
          </form>
        </div>
      </section>}

      {page === "library" && <section className="wp-page-content wp-library-page">
        <div className="wp-page-heading"><div><h1><DatabaseOutlined aria-hidden="true" /> 知识库</h1><p>管理资料与处理状态，问答时按知识库和文档限定范围。</p></div><Button type="primary" icon={<PlusOutlined aria-hidden="true" />} disabled={Boolean(runtimeError)} onClick={() => { setCreateKbError(''); setCreateKbOpen(true); }}>新建知识库</Button></div>
        <div className="wp-library-toolbar"><span>{bases.length} 个知识库</span><Input allowClear prefix={<SearchOutlined aria-hidden="true" />} placeholder="搜索知识库或文件" aria-label="搜索知识库或文件" value={librarySearch} onChange={event => setLibrarySearch(event.target.value)} /></div>
        <div className="wp-library-grid">
          <div className="wp-panel wp-library-bases">
            <h2>知识库</h2>
            <div className="wp-kb-list">{matchingBases.map(base => <button type="button" key={base.id} className="wp-library-row" aria-pressed={uploadKbId === base.id} onClick={() => { setUploadKbId(base.id); setUploadError(''); }}><span className="wp-kb-icon"><DatabaseOutlined aria-hidden="true" /></span><span className="wp-kb-description"><strong title={base.name}>{base.name}</strong><span>{base.document_count} 份资料 · {base.cloud_allowed ? "允许外发" : "仅本地"}</span></span></button>)}{!matchingBases.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={bases.length ? "没有匹配的知识库" : "还没有知识库"} />}</div>
          </div>
          <div className="wp-panel wp-library-files">
            <div className="wp-panel-heading"><h2>资料文件</h2>{activeLibrary && <Tag color="green">{visibleDocuments.length} 份资料</Tag>}</div>
            <div className="wp-upload-row"><Select aria-label="上传目标知识库" placeholder="选择目标知识库" value={uploadKbId || undefined} onChange={id => { setUploadKbId(id); setUploadError(''); }} disabled={uploading} options={bases.map(base => ({ value: base.id, label: base.name }))} /></div>
            {activeLibrary ? <>
              <KnowledgeBaseConsent key={uploadKbId} base={activeLibrary} disabled={loading || busy || uploading || Boolean(runtimeError)} onUpdated={updated => setBases(current => current.map(base => base.id === updated.id ? updated : base))}/>
              <Upload.Dragger className="wp-upload-dropzone" disabled={!uploadKbId || uploading || Boolean(runtimeError)} showUploadList={false} multiple={false} beforeUpload={file => { void upload(file); return false; }}>
                <div className="wp-upload-invitation">{uploading ? <Spin size="small" /> : <InboxOutlined aria-hidden="true" />}<span>{uploading ? '正在上传资料…' : '拖放文件到此处，或点击上传资料'}</span></div><p>上传后自动解析和建立索引，可在下方查看处理状态。</p>
              </Upload.Dragger>
              {uploadError && <Alert type="error" title="上传失败" description={uploadError} showIcon closable onClose={() => setUploadError('')} />}
              {uploadKbId && <details className="wp-image-settings-drawer"><summary>图片处理设置</summary><ImageGenerationSettings key={uploadKbId} kbId={uploadKbId} disabled={busy || loading || Boolean(runtimeError)}/></details>}
              <div className="wp-document-summary"><span>{visibleDocuments.filter(doc => ['ready', 'completed'].includes(doc.status)).length} 份可检索</span><span>{visibleDocuments.filter(doc => ['pending', 'processing'].includes(doc.status)).length} 份处理中</span>{visibleDocuments.some(doc => doc.status === 'failed' || doc.error_code) && <span className="wp-document-error">{visibleDocuments.filter(doc => doc.status === 'failed' || doc.error_code).length} 份处理失败</span>}</div>
              <div className="wp-library-documents">{matchingDocuments.map(doc => <div className="wp-library-row wp-document-row" key={doc.id}><FileOutlined className="wp-document-icon" /><div className="wp-document-info"><strong title={doc.filename}>{doc.filename}</strong><span className={doc.status === 'failed' || doc.error_code ? 'wp-document-error' : undefined}>{['pending', 'processing'].includes(doc.status) && <Spin size="small" />} {doc.status === 'ready' ? '可检索' : doc.status === 'pending' ? '等待处理' : doc.status === 'processing' ? '正在处理' : doc.status}{doc.error_code ? ` · 错误码 ${doc.error_code}` : ''}</span></div><Button size="small" onClick={() => setFamiliarDocument(doc)}>预览</Button><a href={portApi.originalDocumentURL(doc.knowledge_base_id, doc.id)} target="_blank" rel="noreferrer">查看原文件</a><ImageTaskDetails document={doc} disabled={busy || Boolean(runtimeError)}/></div>)}{!matchingDocuments.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={visibleDocuments.length ? '没有匹配的文件' : '还没有资料，上传后即可开始问答'} />}</div>
            </> : <div className="wp-library-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={bases.length ? '选择知识库，查看和上传资料' : '创建第一个知识库，开始整理资料'}>{!bases.length && <Button type="primary" icon={<PlusOutlined aria-hidden="true" />} disabled={Boolean(runtimeError)} onClick={() => setCreateKbOpen(true)}>新建知识库</Button>}</Empty></div>}
          </div>
        </div>
      </section>}

      {page === "models" && <section className="wp-page-content"><div className="wp-page-heading"><div><h1>模型角色</h1><p>配置 embedding、rerank、cheap、expensive 和 VLM。API 密钥只写入，不会从服务端读回。</p></div></div><form className="wp-model-form" onSubmit={event => void saveConfig(event)}>
        <fieldset className="wp-role-card"><legend>回答设置</legend><div className="wp-role-grid"><label className="wide">回答语言<select aria-label="回答语言" value={answerLanguage} disabled={busy} onChange={event => {
          const locale = event.target.value;
          setAnswerLanguage(locale);
          try {
            localStorage.setItem(ANSWER_LANGUAGE_KEY, locale);
            setNotice("回答语言已更新，仅保存在此浏览器。");
          } catch { setNotice("回答语言已更新，但浏览器不允许保存；刷新后恢复简体中文。"); }
        }}>{ANSWER_LANGUAGES.map(([locale, label]) => <option key={locale} value={locale}>{label}</option>)}</select><small>即刻生效，无需保存模型设置。回答使用所选语言，不随提问语言切换。</small></label></div></fieldset>
        {config.roles.map((role, index) => <fieldset className="wp-role-card" key={role.role}><legend>{ROLE_NAMES[role.role]}</legend><div className="wp-role-grid"><label>Source<select aria-label={`Source ${role.role}`} value={role.source ?? ""} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, source: event.target.value } : row) }))}><option value="">Choose source / preserve legacy</option><option value="remote">Remote</option><option value="local">Local (calls unavailable)</option></select></label><label>Provider<input value={role.provider} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, provider: event.target.value } : row) }))}/></label><label>Model<input value={role.model} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, model: event.target.value } : row) }))}/></label><label className="wide">Base URL<input value={role.base_url} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, base_url: event.target.value } : row) }))}/></label><label className="wide">API key <small>{role.credential_status === "UNAVAILABLE" ? `凭据不可用（${role.credential_error}）；可输入新密钥替换` : role.key_configured ? "已配置；留空保留已保存密钥" : "尚未配置"}</small><input type="password" autoComplete="new-password" value={role.api_key ?? ""} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, api_key: event.target.value } : row) }))} placeholder={role.key_configured ? "留空保留" : "输入密钥"}/></label></div></fieldset>)}{configError && <p className="wp-inline-error" role="alert">{configError}</p>}<Button type="primary" htmlType="submit" className="wp-save" disabled={busy || Boolean(runtimeError)}>保存模型设置</Button></form></section>}
      {page === "memory" && <MemoryPanel disabled={loading || Boolean(runtimeError)} onOpenDocument={openMemoryDocument} />}
    </Layout.Content>
    <Modal title="新建知识库" open={createKbOpen} onCancel={() => { if (!creatingKb) setCreateKbOpen(false); }} footer={null} destroyOnHidden>
      <form className="wp-create-kb-form" onSubmit={event => void createKb(event)}><label htmlFor="wp-kb-name">知识库名称</label><Input id="wp-kb-name" autoFocus value={newKbName} onChange={event => setNewKbName(event.target.value)} placeholder="例如：课程资料" disabled={creatingKb} />{createKbError && <Alert type="error" title={createKbError} showIcon />}<p>新建知识库默认仅在本地处理。外发许可可在资料管理中明确设置。</p><div><Button disabled={creatingKb} onClick={() => setCreateKbOpen(false)}>取消</Button><Button type="primary" htmlType="submit" loading={creatingKb} disabled={!newKbName.trim() || Boolean(runtimeError)}>创建</Button></div></form>
    </Modal>
    <DocumentPreview document={page === 'library' ? familiarDocument : null} onClose={() => setFamiliarDocument(null)} />
    <SourceReferenceDrawer reference={page === 'chat' ? sourceReference : null} onClose={() => setSourceReference(null)} />
  </Layout>
  </ConfigProvider>;
}
