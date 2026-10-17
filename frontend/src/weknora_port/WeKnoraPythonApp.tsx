import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { PortApiError, portApi } from "./api";
import { resumePortChat, runPortChat } from "./sse";
import type {
  DocumentRow, KnowledgeBase, MessageRow, ModelConfiguration, ModelRole,
  PortRunIdentity, SessionRow, WeKnoraSSEMessage,
} from "./types";
import { KnowledgeBaseConsent } from "./KnowledgeBaseConsent";
import { MemoryPanel } from "./MemoryPanel";
import "./styles.css";

type Segment = { eventId: string; text: string; done: boolean };
type TimelineEvent = { type: string; text: string; eventId?: string };
type ChatLine = {
  id: string;
  role: "user" | "assistant";
  content: string;
  segments?: Segment[];
  progress?: TimelineEvent[];
  status?: "running" | "completed" | "interrupted" | "failed" | "cancelled" | "unknown";
  error?: boolean;
  identity?: PortRunIdentity;
};

const ROLE_NAMES: Record<ModelRole, string> = {
  embedding: "Embedding",
  rerank: "Rerank",
  cheap: "Quick answer",
  expensive: "Smart answer",
  vlm: "Vision",
};
const EMPTY_CONFIG: ModelConfiguration = { revision: "", roles: (Object.keys(ROLE_NAMES) as ModelRole[]).map(role => ({
  role, provider: "", model: "", base_url: "", key_configured: false, api_key: "",
})) };
const INTERNAL_SOURCE_ATTRIBUTION_LINE = /^[ \t]*(?:\*\*|__)?source:(?:\*\*|__)?[^\r\n]*\([ \t]*document[ \t]+d[1-9]\d*[ \t]*,[ \t]*chunk[ \t]+c[1-9]\d*[ \t]*\)[^\r\n]*(?:\r?\n|$)/i;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败";
}

function assistantBubble(text: string): ChatLine {
  return { id: crypto.randomUUID(), role: "assistant", content: text, error: true, status: "failed" };
}

function answerFromSegments(segments: Segment[] = []) {
  return segments.map(segment => segment.text).join("");
}

function eventLabel(event: WeKnoraSSEMessage): string {
  const text = event.content.trim();
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
      if (INTERNAL_SOURCE_ATTRIBUTION_LINE.test(line)) {
        cursor = end;
        lineStart = end;
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
  return src ? <img src={src} alt={alt ?? ""} /> : null;
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
  const [selectedBases, setSelectedBases] = useState<string[]>([]);
  const [selectedDocuments, setSelectedDocuments] = useState<string[]>([]);
  const [config, setConfig] = useState<ModelConfiguration>(EMPTY_CONFIG);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("正在确认 Python 服务身份");
  const [query, setQuery] = useState("");
  const [newKbName, setNewKbName] = useState("");
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

  const documentScopeLocked = Boolean(activeSession);
  const canSubmit = !busy && !loading && !runtimeError && selectedBases.length > 0 && query.trim().length > 0;

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
    setActiveSession(session);
    setSelectedBases(session.knowledge_base_scope);
    setSelectedDocuments(session.document_scope);
    setMessages([]);
    setNotice("正在读取会话历史");
    try {
      const history = await portApi.messages(session.id);
      setMessages(history.map(row => ({ id: row.id, role: row.role === "user" ? "user" : "assistant", content: row.content,
        status: row.role === "user" ? "completed" : row.turn_error_code === "MODEL_CALL_CANCELLED" ? "cancelled" : row.is_completed ? "completed" : row.turn_status === "error" ? "failed" : "unknown",
        error: row.turn_status === "error",
        progress: (row.agent_steps ?? []).filter(step => step.reasoning_content).map(step => ({ type: "thinking", text: step.reasoning_content ?? "" })),
      })));
      setNotice("已读取保存的会话历史；实时阶段仅显示当前连接收到的事件");
    } catch (error) { setMessages([assistantBubble(`历史读取失败：${errorText(error)}`)]); }
  };

  const newSession = () => {
    activeAbort.current?.abort();
    activeAbort.current = null;
    activeIdentity.current = null;
    setActiveSession(null);
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

  const updateMessage = (messageId: string, update: (message: ChatLine) => ChatLine) => {
    setMessages(current => current.map(message => message.id === messageId ? update(message) : message));
  };

  const applyEvent = (messageId: string, event: WeKnoraSSEMessage) => {
    if (event.response_type === "complete") {
      const hadTerminalError = terminalErrorMessages.current.has(messageId);
      terminalErrorMessages.current.delete(messageId);
      setNotice(event.data.status === "cancelled"
        ? "生成已取消，服务端已保存终态"
        : event.data.status === "failed" || hadTerminalError ? "生成失败，服务端已保存终态" : "回答已完成并保存");
    } else if (event.response_type === "error") {
      const nonTerminalStreamError = event.data.non_terminal === true && event.data.stage === "chat_completion_stream";
      if (!nonTerminalStreamError) {
        terminalErrorMessages.current.add(messageId);
        setNotice(event.content || "运行发生错误");
      }
    }
    updateMessage(messageId, previous => {
      const progress = [...(previous.progress ?? [])];
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
          return { ...previous, progress, status: "failed", error: true };
        }
        const finalContent = typeof event.data.final_content === "string" ? event.data.final_content : answerFromSegments(previous.segments);
        return { ...previous, content: finalContent, progress, status: "completed" };
      }
      if (event.response_type === "error") {
        if (event.data.non_terminal === true && event.data.stage === "chat_completion_stream") {
          return { ...previous, progress: progress.slice(-80) };
        }
        return { ...previous, content: event.content || "运行发生错误", progress: progress.slice(-80), error: true };
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
        content, request_id: requestId, mode: "quick",
        knowledge_base_scope: selectedBases, document_scope: selectedDocuments,
      }, {
        signal: controller.signal,
        onRunIdentity: identity => {
          activeIdentity.current = identity;
          updateMessage(line.id, previous => ({ ...previous, identity }));
        },
        onReplayStart: () => updateMessage(line.id, previous => ({ ...previous, content: "", segments: [], progress: [], status: "running" })),
        onMessage: event => applyEvent(line.id, event),
      });
      if (controller.signal.aborted) updateMessage(line.id, previous => ["completed", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "interrupted" });
      else updateMessage(line.id, previous => ["completed", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "unknown" });
      await refreshSessions();
    } catch (error) {
      if (controller.signal.aborted) updateMessage(line.id, previous => ["completed", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "interrupted" });
      else updateMessage(line.id, previous => ({ ...previous, status: "unknown", error: true,
        content: previous.content || errorText(error),
        progress: [...(previous.progress ?? []), { type: "error", text: errorText(error) }] }));
      setNotice(errorText(error));
    } finally {
      if (activeAbort.current === controller) activeAbort.current = null;
      setBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    const content = query.trim();
    setQuery("");
    setRuntimeError("");
    const requestId = crypto.randomUUID();
    let session = activeSession;
    const userLine: ChatLine = { id: crypto.randomUUID(), role: "user", content };
    const assistantLine: ChatLine = { id: crypto.randomUUID(), role: "assistant", content: "", segments: [], progress: [], status: "running" };
    setNotice("正在提交问题");
    setMessages(current => [...current, userLine, assistantLine]);
    try {
      if (!session) {
        session = await portApi.createSession(selectedBases, selectedDocuments, content.slice(0, 48));
        setActiveSession(session);
        setSessions(current => [session!, ...current.filter(row => row.id !== session!.id)]);
      }
      await run(assistantLine, session.id, requestId, content);
    } catch (error) {
      updateMessage(assistantLine.id, previous => ({ ...previous, content: errorText(error), status: "failed", error: true }));
      setNotice(errorText(error));
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
        onReplayStart: () => updateMessage(line.id, previous => ({ ...previous, content: "", segments: [], progress: [], status: "running" })),
        onMessage: event => applyEvent(line.id, event),
      });
      if (!controller.signal.aborted) updateMessage(line.id, previous => ["completed", "failed", "cancelled"].includes(previous.status ?? "") ? previous : { ...previous, status: "unknown" });
    } catch (error) {
      updateMessage(line.id, previous => ({ ...previous, status: controller.signal.aborted ? "interrupted" : "unknown", error: true,
        content: previous.content || errorText(error),
        progress: [...(previous.progress ?? []), { type: "error", text: errorText(error) }] }));
    } finally { if (activeAbort.current === controller) activeAbort.current = null; setBusy(false); }
  };

  const upload = async (file?: File) => {
    if (!file || !uploadKbId) return;
    const targetKbId = uploadKbId;
    try {
      const row = await portApi.upload(targetKbId, file);
      if (page === "library" && uploadKbId === targetKbId) {
        setDocuments(current => [...current.filter(item => item.id !== row.id), row]);
        setDocumentRefresh(current => current + 1);
      }
      setBases(await portApi.knowledgeBases());
      setNotice(`已提交资料：${row.filename} · ${row.status}`);
    } catch (error) { setNotice(`上传失败：${errorText(error)}`); }
  };

  const createKb = async (event: FormEvent) => {
    event.preventDefault();
    if (!newKbName.trim()) return;
    try {
      const row = await portApi.createKnowledgeBase(newKbName.trim());
      setBases(current => [...current, row]);
      setNewKbName("");
      setNotice(`已创建知识库：${row.name}`);
    } catch (error) { setNotice(`创建失败：${errorText(error)}`); }
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

  return <div className="wp-shell">
    <aside className="wp-sidebar">
      <div className="wp-brand"><span className="wp-brand-mark">W</span><span><strong>WeKnora</strong><small>Python adapter</small></span></div>
      <nav className="wp-nav" aria-label="主导航">
        <button className={page === "chat" ? "active" : ""} onClick={() => setPage("chat")}>问答</button>
        <button className={page === "library" ? "active" : ""} onClick={() => setPage("library")}>资料库</button>
        <button className={page === "models" ? "active" : ""} onClick={() => setPage("models")}>模型设置</button>
        <button className={page === "memory" ? "active" : ""} onClick={() => setPage("memory")}>长期记忆</button>
      </nav>
      <button className="wp-new-chat" onClick={newSession}>＋ 新对话</button>
      <div className="wp-history-title">历史会话</div>
      <div className="wp-history-list">
        {sessions.map(session => <button key={session.id} className={activeSession?.id === session.id ? "selected" : ""} onClick={() => void openSession(session)}>
          <strong>{session.title || "新对话"}</strong><small>{session.knowledge_base_scope.length} 个知识库 · {session.updated_at || ""}</small>
        </button>)}
        {!sessions.length && <p className="wp-empty-small">还没有会话</p>}
      </div>
      <div className="wp-sidebar-footer">本地知识工作台</div>
    </aside>

    <main className="wp-main">
      <header className="wp-topbar"><div><span className="wp-live-dot" />{page === "chat" ? `问答台${selectedNames.length ? ` / ${selectedNames.join("、")}` : ""}` : page === "library" ? "知识库与资料" : page === "memory" ? "长期记忆" : "模型角色"}</div><span className="wp-notice" role="status">{loading ? "正在加载" : notice}</span></header>
      {runtimeError && <div className="wp-banner" role="alert"><strong>Python API 不可用</strong><span>{runtimeError}</span><button onClick={() => { setLoading(true); setRuntimeError(""); void portApi.runtime().then(() => location.reload()).catch(error => { setRuntimeError(errorText(error)); setLoading(false); }); }}>重新检查</button></div>}

      {page === "chat" && <section className="wp-chat-page">
        <div className="wp-scope-card">
          <div className="wp-scope-heading"><div><strong>检索范围</strong><small>{documentScopeLocked ? "会话使用已保存的知识库范围；新范围请新建会话" : "先选择一个或多个知识库，再限定文档"}</small></div><span>{selectedBases.length} 个知识库 · {selectedDocuments.length} 个资料</span></div>
          <div className="wp-scope-grid">
            <div className="wp-scope-column"><label>知识库</label>{bases.map(base => <label key={base.id} className="wp-check-row"><input type="checkbox" checked={selectedBases.includes(base.id)} disabled={documentScopeLocked || busy} onChange={() => toggleBase(base.id)} /><span><strong>{base.name}</strong><small>{base.document_count} 份资料 · {base.cloud_allowed ? "允许外发" : "仅本地"}</small></span></label>)}{!bases.length && <p className="wp-empty-small">尚无知识库，请到资料库创建</p>}</div>
            <div className="wp-scope-column"><label>资料文件</label>{visibleDocuments.map(doc => <label key={doc.id} className="wp-check-row"><input type="checkbox" checked={selectedDocuments.includes(doc.id)} disabled={documentScopeLocked || busy || !selectedBases.includes(doc.knowledge_base_id)} onChange={() => toggleDocument(doc.id)} /><span><strong>{doc.filename}</strong><small>{doc.status}{doc.error_code ? ` · 错误码 ${doc.error_code}` : ""}</small></span></label>)}{selectedBases.length > 0 && visibleDocuments.length === 0 && <p className="wp-empty-small">所选知识库暂无资料</p>}</div>
          </div>
        </div>

        <div className="wp-transcript" aria-live="polite">
          {messages.length === 0 && <div className="wp-welcome"><span className="wp-welcome-icon">✳</span><h1>从你的资料中寻找答案</h1><p>选择检索范围，快速问答会显示实际检索与回答进度。</p><div className="wp-scope-tags">{selectedNames.map(name => <span key={name}>{name}</span>)}</div></div>}
          {messages.map(message => <article key={message.id} className={`wp-message ${message.role} ${message.error ? "error" : ""}`}>
            <div className="wp-message-label">{message.role === "user" ? "你" : message.error ? "运行提示" : "WeKnora"}</div>
            {message.role === "user" ? <p className="wp-message-content">{message.content}</p> : <>
              {message.progress?.length ? <details className="wp-progress" open={message.status === "running"}><summary>实际执行事件 · {message.progress.length}</summary><ol>{message.progress.map((event, index) => <li key={`${event.eventId ?? event.type}-${index}`}><span>{event.type}</span><p>{event.text}</p></li>)}</ol></details> : null}
              <div className="wp-message-content"><MessageContent content={message.content} /></div>
              {message.status === "running" && <span className="wp-running"><i />正在接收同一运行的回答</span>}
              {message.status === "interrupted" && <div className="wp-run-actions"><span>已断开读取，服务端运行未被重发</span>{message.identity && <button onClick={() => void resume(message)} disabled={busy}>继续读取原回答</button>}</div>}
              {message.status === "cancelled" && <p className="wp-run-actions">已停止生成；原始运行已保存取消状态。</p>}
              {message.status === "failed" && <p className="wp-run-actions">生成失败；原始运行的错误已保存。</p>}
              {message.status === "unknown" && <div className="wp-run-actions"><span>尚未收到 complete，结果状态待确认；没有重新生成。</span>{message.identity && <button onClick={() => void resume(message)} disabled={busy}>GET 续读原回答</button>}</div>}
              {message.role === "assistant" && message.status === "completed" && <small className="wp-complete">回答已由服务端完成并保存</small>}
            </>}
          </article>)}
        </div>
        <div className="wp-composer-wrap">
          <div className="wp-mode-row"><span className="wp-mode-active">快速问答</span><button className="wp-mode-disabled" disabled title="Python 适配器尚未接入智能推理执行">智能推理 <small>暂不可用</small></button><span className="wp-composer-hint">一次提问只发一次 POST；中断后只 GET 续读</span></div>
          <form className="wp-composer" onSubmit={event => void submit(event)}><textarea value={query} onChange={event => setQuery(event.target.value)} placeholder={selectedBases.length ? "询问当前资料…" : "先从上方选择知识库"} disabled={busy || Boolean(runtimeError)} rows={3} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} /><div className="wp-composer-actions">{busy ? <><button type="button" className="wp-stop" onClick={() => void stopGeneration()}>停止生成</button><button type="button" onClick={stopReading}>停止读取</button></> : <span>Enter 发送 · Shift+Enter 换行</span>}<button type="submit" disabled={!canSubmit}>发送 ↗</button></div></form>
        </div>
      </section>}

      {page === "library" && <section className="wp-page-content"><div className="wp-page-heading"><div><h1>知识库</h1><p>选择资料范围并查看文件名、索引状态。</p></div></div><div className="wp-library-grid"><div className="wp-panel"><h2>知识库</h2>{bases.map(base => <button key={base.id} className="wp-library-row" onClick={() => setUploadKbId(base.id)}><strong>{base.name}</strong><span>{base.document_count} 份 · {base.cloud_allowed ? "允许外发" : "仅本地"}</span></button>)}<form className="wp-inline-form" onSubmit={event => void createKb(event)}><input value={newKbName} onChange={event => setNewKbName(event.target.value)} placeholder="新知识库名称"/><button disabled={!newKbName.trim() || Boolean(runtimeError)}>创建</button></form></div><div className="wp-panel"><h2>资料文件</h2><KnowledgeBaseConsent key={uploadKbId} base={bases.find(base => base.id === uploadKbId)} disabled={loading || busy || Boolean(runtimeError)} onUpdated={updated => setBases(current => current.map(base => base.id === updated.id ? updated : base))}/><div className="wp-upload-row"><select value={uploadKbId} onChange={event => setUploadKbId(event.target.value)}><option value="">选择目标知识库</option>{bases.map(base => <option key={base.id} value={base.id}>{base.name}</option>)}</select><label className="wp-button">上传资料<input type="file" onChange={event => void upload(event.target.files?.[0])} disabled={!uploadKbId || Boolean(runtimeError)}/></label></div>{visibleDocuments.map(doc => <div className="wp-library-row" key={doc.id}><strong>{doc.filename}</strong><span>{doc.status}{doc.error_code ? ` · 错误码 ${doc.error_code}` : ""}</span></div>)}{!visibleDocuments.length && <p className="wp-empty">选择知识库后显示文件名称和状态。</p>}</div></div></section>}

      {page === "models" && <section className="wp-page-content"><div className="wp-page-heading"><div><h1>模型角色</h1><p>配置 embedding、rerank、cheap、expensive 和 VLM。API 密钥只写入，不会从服务端读回。</p></div></div><form className="wp-model-form" onSubmit={event => void saveConfig(event)}>{config.roles.map((role, index) => <fieldset className="wp-role-card" key={role.role}><legend>{ROLE_NAMES[role.role]}</legend><div className="wp-role-grid"><label>Source<select aria-label={`Source ${role.role}`} value={role.source ?? ""} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, source: event.target.value } : row) }))}><option value="">Choose source / preserve legacy</option><option value="remote">Remote</option><option value="local">Local (calls unavailable)</option></select></label><label>Provider<input value={role.provider} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, provider: event.target.value } : row) }))}/></label><label>Model<input value={role.model} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, model: event.target.value } : row) }))}/></label><label className="wide">Base URL<input value={role.base_url} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, base_url: event.target.value } : row) }))}/></label><label className="wide">API key <small>{role.key_configured ? "已配置；留空保留已保存密钥" : "尚未配置"}</small><input type="password" autoComplete="new-password" value={role.api_key ?? ""} onChange={event => setConfig(current => ({ ...current, roles: current.roles.map((row, i) => i === index ? { ...row, api_key: event.target.value } : row) }))} placeholder={role.key_configured ? "留空保留" : "输入密钥"}/></label></div></fieldset>)}{configError && <p className="wp-inline-error" role="alert">{configError}</p>}<button className="wp-save" disabled={busy || Boolean(runtimeError)}>保存模型设置</button></form></section>}
      {page === "memory" && <MemoryPanel disabled={loading || Boolean(runtimeError)} onOpenDocument={openMemoryDocument} />}
    </main>

    {page === "library" && familiarDocument && <aside className="wp-source-drawer" aria-label="常用文档详情"><button className="wp-close" onClick={() => setFamiliarDocument(null)}>关闭文档</button><strong>{familiarDocument.filename}</strong><p>{familiarDocument.status}</p><a href={portApi.originalDocumentURL(familiarDocument.knowledge_base_id, familiarDocument.id)} target="_blank" rel="noreferrer">打开原始文件</a></aside>}
  </div>;
}
