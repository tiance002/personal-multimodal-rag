import { useEffect, useMemo, useRef, useState } from "react";
import { ConfigProvider, Switch } from "antd";
import { api } from "../api/client";
import type {
  AppState,
  Conversation,
  DocumentItem,
  IngestionJob,
  KnowledgeBase,
  Message,
  ViewMode,
} from "./state";
import { ChatPanel } from "../components/ChatPanel";
import { DocumentPreviewModal } from "../components/DocumentPreviewModal";
import { KnowledgeBasePanel } from "../components/KnowledgeBasePanel";
import { Sidebar } from "../components/Sidebar";
import { useResizablePanelWidth } from "../components/useResizablePanelWidth";

const initialState: AppState = {
  selectedKnowledgeBaseId: null,
  selectedDocumentId: null,
  documentScope: [],
  viewMode: "document",
  activeConversationId: null,
};
const emptyDocuments: DocumentItem[] = [];
type ScopedDocuments = { knowledgeBaseId: string; items: DocumentItem[] };
type ScopedJob = IngestionJob & { knowledgeBaseId: string };

function formatCitationLocator(locator: Record<string, unknown>) {
  const page = typeof locator.page === "number" ? `第 ${locator.page} 页` : "";
  const start = typeof locator.start === "number" ? locator.start : null;
  const end = typeof locator.end === "number" ? locator.end : null;
  const range =
    start !== null && end !== null
      ? `字符 ${start}–${end}`
      : start !== null
        ? `字符 ${start} 起`
        : "";
  return [page, range].filter(Boolean).join(" · ") || "已定位到当前版本的对应切块";
}

export default function App() {
  const [state, setState] = useState<AppState>(initialState);
  const [page, setPage] = useState<"chat" | "library" | "settings">("chat");
  const [libraryBaseId, setLibraryBaseId] = useState<string | null>(null);
  const [libraryDocumentId, setLibraryDocumentId] = useState<string | null>(
    null,
  );
  const [compactSidebar, setCompactSidebar] = useState(
    () => window.localStorage.getItem("index-desk-compact-sidebar") === "true",
  );
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [documentList, setDocumentList] = useState<ScopedDocuments | null>(
    null,
  );
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [citation, setCitation] = useState<{
    quote: string;
    current_status: string;
    locator: Record<string, unknown>;
  } | null>(null);
  const { width: citationWidth, handleProps: citationResizeHandleProps } =
    useResizablePanelWidth({
      open: Boolean(citation),
      initialWidth: 460,
      minWidth: 420,
    });
  const [notice, setNotice] = useState("本地索引已连接");
  const scopeSyncRef = useRef(false);
  const sendPendingRef = useRef(false);
  const messageRequestEpoch = useRef(0);
  const previousMessageContext = useRef<{
    conversationId: string | null;
    scopeKey: string;
  }>({ conversationId: null, scopeKey: "" });
  const skipHistoryLoadFor = useRef<string | null>(null);
  const [scopeSyncing, setScopeSyncing] = useState(false);
  const [scopeBlocked, setScopeBlocked] = useState(false);
  const scopeBlockedRef = useRef(false);
  const markScopeBlocked = (blocked: boolean) => {
    scopeBlockedRef.current = blocked;
    setScopeBlocked(blocked);
  };
  const [sending, setSending] = useState(false);
  const [ingestionJobs, setIngestionJobs] = useState<Record<string, ScopedJob>>(
    {},
  );
  const [ingestionIssues, setIngestionIssues] = useState<
    Record<string, boolean>
  >({});
  const ingestionPolls = useRef<Record<string, number>>({});
  const uploadBusyRef = useRef<Set<string>>(new Set());
  const [uploadBusyBases, setUploadBusyBases] = useState<Set<string>>(
    new Set(),
  );
  const selectedBaseIdRef = useRef<string | null>(null);
  const visibleBaseId =
    page === "library" ? libraryBaseId : state.selectedKnowledgeBaseId;
  selectedBaseIdRef.current = visibleBaseId;

  const documents =
    documentList?.knowledgeBaseId === visibleBaseId
      ? documentList.items
      : emptyDocuments;
  const libraryPreviewDocument = libraryDocumentId
    ? documents.find((document) => document.id === libraryDocumentId) ?? null
    : null;
  const ingestionJob = visibleBaseId
    ? (ingestionJobs[visibleBaseId] ?? null)
    : null;
  const ingestionIssue = visibleBaseId
    ? Boolean(ingestionIssues[visibleBaseId])
    : false;
  const uploadBusy = visibleBaseId ? uploadBusyBases.has(visibleBaseId) : false;
  const setDocumentsForBase = (kbId: string, items: DocumentItem[]) => {
    if (selectedBaseIdRef.current === kbId)
      setDocumentList({ knowledgeBaseId: kbId, items });
  };
  const setJobForBase = (kbId: string, job: IngestionJob) => {
    setIngestionJobs((current) => ({
      ...current,
      [kbId]: { ...job, knowledgeBaseId: kbId },
    }));
  };
  const setUploadBusyForBase = (kbId: string, busy: boolean) => {
    const next = new Set(uploadBusyRef.current);
    if (busy) next.add(kbId);
    else next.delete(kbId);
    uploadBusyRef.current = next;
    setUploadBusyBases(next);
  };
  const setIngestionIssueForBase = (kbId: string, issue: boolean) => {
    setIngestionIssues((current) => ({ ...current, [kbId]: issue }));
  };

  const selectedBase = useMemo(
    () => bases.find((base) => base.id === state.selectedKnowledgeBaseId),
    [bases, state.selectedKnowledgeBaseId],
  );
  const messageScopeKey = JSON.stringify([
    state.selectedKnowledgeBaseId,
    [...state.documentScope].sort(),
  ]);

  useEffect(() => {
    window.localStorage.setItem(
      "index-desk-compact-sidebar",
      String(compactSidebar),
    );
  }, [compactSidebar]);

  useEffect(() => {
    void Promise.all([api.listKnowledgeBases(), api.listConversations()])
      .then(([nextBases, nextConversations]) => {
        setBases(nextBases);
        setConversations(nextConversations);
        setState((current) => ({
          ...current,
          selectedKnowledgeBaseId:
            current.selectedKnowledgeBaseId ?? nextBases[0]?.id ?? null,
        }));
      })
      .catch(() => setNotice("后端尚未启动，先检查本地服务"));
  }, []);
  useEffect(() => {
    if (!visibleBaseId) {
      setDocumentList(null);
      return;
    }
    const kbId = visibleBaseId;
    let cancelled = false;
    void api
      .listDocuments(kbId)
      .then((items) => {
        if (!cancelled) setDocumentsForBase(kbId, items);
      })
      .catch(() => {
        if (!cancelled) setDocumentsForBase(kbId, []);
      });
    return () => {
      cancelled = true;
    };
  }, [visibleBaseId]);
  useEffect(() => {
    const conversationId = state.activeConversationId;
    const previous = previousMessageContext.current;
    previousMessageContext.current = {
      conversationId,
      scopeKey: messageScopeKey,
    };
    const epoch = ++messageRequestEpoch.current;
    if (!conversationId) {
      setMessages([]);
      return;
    }
    if (skipHistoryLoadFor.current === conversationId) {
      skipHistoryLoadFor.current = null;
      return;
    }
    // Changing scope in the same conversation clears the old transcript, but
    // must not re-fetch that conversation's A-scope history into the B view.
    if (
      previous.conversationId === conversationId &&
      previous.scopeKey !== messageScopeKey
    ) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setMessages([]);
    void api
      .listMessages(conversationId)
      .then((items) => {
        if (
          !cancelled &&
          messageRequestEpoch.current === epoch &&
          !sendPendingRef.current
        )
          setMessages(items);
      })
      .catch(() => {
        if (
          !cancelled &&
          messageRequestEpoch.current === epoch &&
          !sendPendingRef.current
        )
          setMessages([]);
      });
    return () => {
      cancelled = true;
    };
  }, [state.activeConversationId, messageScopeKey]);
  useEffect(() => {
    if (
      !ingestionJob ||
      ingestionIssue ||
      ["succeeded", "failed", "cancelled"].includes(ingestionJob.status)
    )
      return;
    const kbId = ingestionJob.knowledgeBaseId;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let consecutiveErrors = 0;
    const poll = async () => {
      if (cancelled) return;
      ingestionPolls.current[kbId] = (ingestionPolls.current[kbId] ?? 0) + 1;
      if (ingestionPolls.current[kbId] > 240) {
        setIngestionIssueForBase(kbId, true);
        if (selectedBaseIdRef.current === kbId)
          setNotice("摄取仍未结束；已停止自动查询，可手动刷新状态");
        return;
      }
      try {
        const next = await api.getIngestionJob(ingestionJob.id);
        if (cancelled) return;
        if (next.id !== ingestionJob.id) throw new Error("摄取任务标识不匹配");
        consecutiveErrors = 0;
        setJobForBase(kbId, next);
        if (["succeeded", "failed", "cancelled"].includes(next.status)) {
          if (selectedBaseIdRef.current === kbId)
            setNotice(
              next.status === "succeeded"
                ? "资料解析与索引已完成"
                : `摄取失败：${next.error_code ?? next.status}`,
            );
        } else timer = setTimeout(() => void poll(), 1500);
      } catch {
        if (cancelled) return;
        if (++consecutiveErrors >= 3) {
          setIngestionIssueForBase(kbId, true);
          if (selectedBaseIdRef.current === kbId)
            setNotice("无法读取摄取任务状态，请手动刷新");
          return;
        }
        timer = setTimeout(() => void poll(), 1500);
      }
    };
    timer = setTimeout(() => void poll(), 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [ingestionJob?.id, ingestionJob?.status, ingestionIssue, visibleBaseId]);
  useEffect(() => {
    if (
      !ingestionJob ||
      !["succeeded", "failed", "cancelled"].includes(ingestionJob.status) ||
      visibleBaseId !== ingestionJob.knowledgeBaseId
    )
      return;
    let cancelled = false;
    const kbId = ingestionJob.knowledgeBaseId;
    void api
      .listDocuments(kbId)
      .then((items) => {
        if (!cancelled) setDocumentsForBase(kbId, items);
      })
      .catch(() => {
        if (!cancelled && selectedBaseIdRef.current === kbId)
          setNotice("任务已结束，但资料列表暂不可读");
      });
    return () => {
      cancelled = true;
    };
  }, [
    ingestionJob?.id,
    ingestionJob?.status,
    ingestionJob?.knowledgeBaseId,
    visibleBaseId,
  ]);
  useEffect(() => {
    if (
      !visibleBaseId ||
      !documents.some(
        (document) =>
          document.latest_job &&
          !["succeeded", "failed", "cancelled"].includes(
            document.latest_job.status,
          ),
      )
    )
      return;
    const kbId = visibleBaseId;
    const timer = window.setInterval(() => {
      void api
        .listDocuments(kbId)
        .then((items) => setDocumentsForBase(kbId, items))
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [visibleBaseId, documents]);
  // After a page refresh the in-memory job is gone. Rebuild the latest
  // non-terminal or failed job from the document list so the task status,
  // error and retry entry stay visible instead of silently disappearing.
  // Once an in-session job owns this knowledge base we never override it from
  // the (possibly stale) document list.
  useEffect(() => {
    const kbId = visibleBaseId;
    if (!kbId || documentList?.knowledgeBaseId !== kbId || ingestionJob) return;
    const recovered = documents.find(
      (document) =>
        document.latest_job &&
        !["succeeded", "cancelled"].includes(document.latest_job.status),
    );
    const job = recovered?.latest_job;
    if (!job) return;
    ingestionPolls.current[kbId] = 0;
    setIngestionIssueForBase(kbId, false);
    setJobForBase(kbId, job);
  }, [documents, documentList?.knowledgeBaseId, visibleBaseId, ingestionJob]);

  const syncScope = async (
    conversationId: string,
    patch: { knowledge_base_scope: string[]; document_scope: string[] },
    successNotice: string,
    previous: AppState,
  ) => {
    try {
      const conversation = await api.updateConversation(conversationId, patch);
      setConversations((current) =>
        current.map((item) =>
          item.id === conversation.id ? conversation : item,
        ),
      );
      markScopeBlocked(false);
      setNotice(successNotice);
    } catch (error) {
      try {
        const serverConversations = await api.listConversations();
        const actual = serverConversations.find(
          (item) => item.id === conversationId,
        );
        if (!actual) throw new Error("会话已不存在");
        setConversations(serverConversations);
        setState((current) => ({
          ...current,
          selectedKnowledgeBaseId: actual.knowledge_base_scope[0] ?? null,
          selectedDocumentId: actual.document_scope[0] ?? null,
          documentScope: actual.document_scope,
          viewMode: "document",
        }));
        markScopeBlocked(true);
        setNotice(
          `${error instanceof Error ? error.message : "范围更新失败"}；已显示服务端范围，但写入结果不确定，请刷新后再提问`,
        );
      } catch {
        setState(previous);
        markScopeBlocked(true);
        setNotice("无法确认服务端检索范围，请刷新页面后再提问");
      }
    } finally {
      scopeSyncRef.current = false;
      setScopeSyncing(false);
    }
  };

  const selectBase = async (id: string) => {
    if (
      scopeSyncRef.current ||
      sendPendingRef.current ||
      !id ||
      id === state.selectedKnowledgeBaseId
    )
      return;
    messageRequestEpoch.current++;
    const conversationId = state.activeConversationId;
    const previous = state;
    if (conversationId) {
      scopeSyncRef.current = true;
      setScopeSyncing(true);
    }
    setState((current) => ({
      ...current,
      selectedKnowledgeBaseId: id,
      selectedDocumentId: null,
      documentScope: [],
      viewMode: "document",
    }));
    setCitation(null);
    // The conversation scope now points at another knowledge base; drop the
    // displayed transcript so answers from the previous base are not mixed in.
    setMessages([]);
    if (!conversationId) return;
    await syncScope(
      conversationId,
      { knowledge_base_scope: [id], document_scope: [] },
      "已切换知识库，会话检索范围已同步",
      previous,
    );
  };
  const selectConversation = (id: string) => {
    if (scopeSyncRef.current || sendPendingRef.current) return;
    setPage("chat");
    if (id === state.activeConversationId) return;
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    messageRequestEpoch.current++;
    setMessages([]);
    const documentScope = conversation.document_scope ?? [];
    setState((current) => ({
      ...current,
      activeConversationId: id,
      selectedKnowledgeBaseId: conversation.knowledge_base_scope?.[0] ?? null,
      selectedDocumentId: documentScope[0] ?? null,
      documentScope,
      viewMode: "document",
    }));
    setCitation(null);
  };
  const newConversation = () => {
    if (scopeSyncRef.current || sendPendingRef.current) return;
    messageRequestEpoch.current++;
    setState((current) => ({
      ...current,
      activeConversationId: null,
      selectedDocumentId: null,
      documentScope: [],
    }));
    setMessages([]);
    setCitation(null);
    setPage("chat");
  };
  const toggleDocumentScope = async (documentId: string) => {
    if (scopeSyncRef.current || sendPendingRef.current) return;
    messageRequestEpoch.current++;
    setMessages([]);
    setCitation(null);
    const nextScope = state.documentScope.includes(documentId)
      ? []
      : [documentId];
    const previous = state;
    const conversationId = state.activeConversationId;
    if (conversationId && state.selectedKnowledgeBaseId) {
      scopeSyncRef.current = true;
      setScopeSyncing(true);
    }
    setState((current) => ({
      ...current,
      selectedDocumentId: documentId,
      documentScope: nextScope,
    }));
    if (!conversationId || !state.selectedKnowledgeBaseId) {
      setNotice(
        nextScope.length
          ? "当前会话将仅检索此文档"
          : "当前会话将检索整个知识库",
      );
      return;
    }
    await syncScope(
      conversationId,
      {
        knowledge_base_scope: [state.selectedKnowledgeBaseId],
        document_scope: nextScope,
      },
      nextScope.length
        ? "当前会话已限定为此文档"
        : "当前会话已恢复为整个知识库",
      previous,
    );
  };
  const createBase = async (name: string): Promise<boolean> => {
    if (scopeSyncRef.current || sendPendingRef.current) return false;
    try {
      const created = await api.createKnowledgeBase(name);
      setBases((current) => [...current, created]);
      setLibraryBaseId(created.id);
      setLibraryDocumentId(null);
      if (!state.selectedKnowledgeBaseId)
        setState((current) => ({
          ...current,
          selectedKnowledgeBaseId: created.id,
        }));
      setNotice(`已创建知识库：${created.name}`);
      return true;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建知识库失败");
      return false;
    }
  };
  const upload = async (
    files: File[],
    kbId: string | null,
  ): Promise<string> => {
    if (!kbId) {
      const message = "请先选择或创建知识库，再上传资料";
      setNotice(message);
      return message;
    }
    if (uploadBusyRef.current.has(kbId)) {
      const message = "当前知识库正在接收资料，请稍候";
      setNotice(message);
      return message;
    }
    if (!files.length) return "没有可上传的文件";
    setUploadBusyForBase(kbId, true);
    setNotice(`正在接收 ${files.length} 份资料`);
    let accepted = 0;
    let listUnavailable = false;
    const failures: string[] = [];
    try {
      for (const file of files) {
        try {
          const receipt = await api.upload(kbId, file);
          accepted++;
          ingestionPolls.current[kbId] = 0;
          setIngestionIssueForBase(kbId, false);
          setJobForBase(kbId, {
            id: receipt.job_id,
            status: "queued",
            stage: "queued",
            progress: 0,
            attempts: 0,
            max_attempts: 3,
            error_code: null,
          });
        } catch (error) {
          failures.push(
            `${file.name}：${error instanceof Error ? error.message : "上传失败"}`,
          );
        }
      }
      if (accepted) {
        const nextDocuments = await api.listDocuments(kbId);
        setDocumentsForBase(kbId, nextDocuments);
      }
    } catch {
      listUnavailable = true;
    } finally {
      setUploadBusyForBase(kbId, false);
    }
    const message = `${accepted} 份资料已接收${failures.length ? `；${failures.length} 份失败：${failures.join("；")}` : "，后台正在解析与索引"}${listUnavailable ? "；资料列表暂不可读，请刷新页面" : ""}`;
    setNotice(message);
    return message;
  };
  const retryIngestion = async (jobId?: string) => {
    const kbId = visibleBaseId;
    const targetId = jobId ?? ingestionJob?.id;
    if (!targetId || !kbId) return;
    // A row retry must come from this knowledge base's scoped document list.
    // The status-panel retry must match the selected base's own tracked job.
    if (
      jobId
        ? !documents.some((document) => document.latest_job?.id === targetId)
        : ingestionJob?.knowledgeBaseId !== kbId
    )
      return;
    try {
      const next = await api.retryIngestionJob(targetId);
      if (next.id !== targetId) throw new Error("摄取任务标识不匹配");
      ingestionPolls.current[kbId] = 0;
      setIngestionIssueForBase(kbId, false);
      setJobForBase(kbId, next);
      if (selectedBaseIdRef.current === kbId)
        setNotice(
          next.status === "queued"
            ? "重试已排队，等待摄取 Worker"
            : `任务状态：${next.status}`,
        );
    } catch (error) {
      if (selectedBaseIdRef.current === kbId)
        setNotice(error instanceof Error ? error.message : "重试失败");
    }
  };
  const refreshIngestion = async () => {
    if (!ingestionJob) return;
    const kbId = ingestionJob.knowledgeBaseId;
    ingestionPolls.current[kbId] = 0;
    setIngestionIssueForBase(kbId, false);
    try {
      const next = await api.getIngestionJob(ingestionJob.id);
      if (next.id !== ingestionJob.id) throw new Error("摄取任务标识不匹配");
      setJobForBase(kbId, next);
    } catch (error) {
      setIngestionIssueForBase(kbId, true);
      if (selectedBaseIdRef.current === kbId)
        setNotice(error instanceof Error ? error.message : "状态查询失败");
    }
  };
  const send = async (content: string, mode: "quick" | "smart") => {
    const kbId = state.selectedKnowledgeBaseId;
    if (
      scopeSyncRef.current ||
      sendPendingRef.current ||
      scopeBlockedRef.current ||
      !kbId
    )
      return;
    sendPendingRef.current = true;
    setCitation(null);
    messageRequestEpoch.current++;
    setSending(true);
    const documentScope = [...state.documentScope];
    let conversationId = state.activeConversationId;
    try {
      if (!conversationId) {
        const conversation = await api.createConversation(kbId, documentScope);
        const titled = { ...conversation, title: content.slice(0, 36) };
        setConversations((current) => [titled, ...current]);
        conversationId = conversation.id;
        skipHistoryLoadFor.current = conversation.id;
        setState((current) => ({
          ...current,
          activeConversationId: conversation.id,
          documentScope: conversation.document_scope ?? current.documentScope,
        }));
        void api
          .updateConversation(conversation.id, { title: titled.title })
          .then((updated) =>
            setConversations((current) =>
              current.map((item) => (item.id === updated.id ? updated : item)),
            ),
          )
          .catch(() => setNotice("会话已创建，标题暂未同步"));
      }
      setMessages((current) => [...current, { role: "user", content }]);
      setNotice(
        mode === "smart" ? "智能推理正在读取当前知识库" : "正在检索当前知识库",
      );
      const result = await api.sendMessage(
        conversationId,
        content,
        mode,
        [kbId],
        documentScope,
      );
      const errorText = result.error_code
        ? `回答未完成：${result.error_code}`
        : result.answer;
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: errorText,
          citations: result.citations,
          run_id: result.run_id,
        },
      ]);
      setNotice(
        result.error_code
          ? `问答失败：${result.error_code}`
          : "回答已完成，证据已冻结",
      );
    } catch (error) {
      if (
        error instanceof Error &&
        error.message.includes("CONVERSATION_SCOPE_CHANGED") &&
        conversationId
      ) {
        markScopeBlocked(true);
        try {
          const serverConversations = await api.listConversations();
          const actual = serverConversations.find(
            (item) => item.id === conversationId,
          );
          if (!actual) throw new Error("会话已不存在");
          setConversations(serverConversations);
          setState((current) => ({
            ...current,
            selectedKnowledgeBaseId: actual.knowledge_base_scope[0] ?? null,
            selectedDocumentId: actual.document_scope[0] ?? null,
            documentScope: actual.document_scope,
            viewMode: "document",
          }));
          setNotice("服务端范围已变化；已显示最新范围，请刷新后再提问");
        } catch {
          setNotice("服务端范围已变化且无法读取，请刷新后再提问");
        }
      } else setNotice(error instanceof Error ? error.message : "问答失败");
    } finally {
      sendPendingRef.current = false;
      setSending(false);
    }
  };
  const changeView = (viewMode: ViewMode) =>
    setState((current) => ({ ...current, viewMode }));
  const openCitation = async (runId: string, citationId: string) => {
    try {
      setCitation(await api.getCitation(runId, citationId));
    } catch {
      setNotice("引用暂时无法回读");
    }
  };
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#09b873",
          borderRadius: 7,
          fontFamily:
            '"Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif',
        },
      }}
    >
      <div className={`app-shell ${compactSidebar ? "compact-sidebar" : ""}`}>
        <Sidebar
          conversations={conversations}
          activeId={state.activeConversationId}
          page={page}
          onPage={setPage}
          onSelect={selectConversation}
          onNew={newConversation}
        />
        <main className="main-column">
          <header className="topbar">
            <div className="crumb">
              <span className="crumb-dot" />
              {page === "chat"
                ? "问答台"
                : page === "library"
                  ? "知识库"
                  : "系统设置"}
              {page === "chat" && (
                <>
                  <span>/</span>
                  {selectedBase?.name ?? "未选择知识库"}
                </>
              )}
            </div>
            <div className="topbar-actions">
              <span className="local-badge">本地模式</span>
              <span className="notice" role="status">
                {notice}
              </span>
            </div>
          </header>
          {page === "chat" ? (
            <div className="chat-page">
              <ChatPanel
                messages={messages}
                bases={bases}
                documents={documents}
                selectedBaseId={state.selectedKnowledgeBaseId}
                documentScope={state.documentScope}
                scopeBusy={scopeSyncing || sending || scopeBlocked}
                onToggleDocumentScope={(id) => void toggleDocumentScope(id)}
                disabled={
                  !state.selectedKnowledgeBaseId ||
                  scopeSyncing ||
                  scopeBlocked ||
                  sending
                }
                disabledHint={
                  scopeBlocked
                    ? "无法确认检索范围，请刷新页面"
                    : scopeSyncing
                      ? "正在同步检索范围"
                      : sending
                        ? "正在回答，请稍候"
                        : !state.selectedKnowledgeBaseId
                          ? "先选择一个知识库"
                          : undefined
                }
                uploading={
                  state.selectedKnowledgeBaseId
                    ? uploadBusyBases.has(state.selectedKnowledgeBaseId)
                    : false
                }
                onSelectBase={(id) => void selectBase(id)}
                onUpload={(files) =>
                  void upload(files, state.selectedKnowledgeBaseId)
                }
                onSend={(content, mode) => void send(content, mode)}
                onCitation={(runId, citationId) =>
                  void openCitation(runId, citationId)
                }
                onOpenLibrary={() => setPage("library")}
              />
            </div>
          ) : page === "library" ? (
            <div
              className={`library-layout ${libraryBaseId && libraryDocumentId ? "with-preview" : ""}`}
            >
              <KnowledgeBasePanel
                bases={bases}
                selectedBaseId={libraryBaseId}
                documents={documents}
                selectedDocumentId={libraryDocumentId}
                uploadBusy={uploadBusy}
                ingestionJob={ingestionJob}
                ingestionIssue={ingestionIssue}
                onCreateBase={createBase}
                onSelectBase={(id) => {
                  setLibraryBaseId(id);
                  setLibraryDocumentId(null);
                }}
                onSelectDocument={(id) => {
                  setLibraryDocumentId(id);
                  changeView("document");
                }}
                onUpload={(files) => upload(files, libraryBaseId)}
                onRetryIngestion={() => void retryIngestion()}
                onRetryJob={(jobId) => void retryIngestion(jobId)}
                onRefreshIngestion={() => void refreshIngestion()}
              />
            </div>
          ) : (
            <section className="settings-page">
              <h1>系统设置</h1>
              <p>调整本机工作台的显示方式。</p>
              <label className="setting-row">
                <span>
                  <strong>紧凑侧栏</strong>
                  <small>为资料和会话留出更多空间</small>
                </span>
                <Switch checked={compactSidebar} onChange={setCompactSidebar} />
              </label>
              <div className="setting-note">
                <strong>检索范围</strong>
                <p>
                  每次提问只使用会话中选定的知识库。切换知识库时会同步服务端范围。
                </p>
              </div>
            </section>
          )}
        </main>
        <DocumentPreviewModal
          document={libraryPreviewDocument}
          open={page === "library" && Boolean(libraryPreviewDocument)}
          graphEnabled={
            libraryBaseId
              ? bases.find((base) => base.id === libraryBaseId)?.graph_enabled ===
                true
              : false
          }
          viewMode={state.viewMode}
          onViewMode={changeView}
          onClose={() => setLibraryDocumentId(null)}
        />
        {page === "chat" && citation && (
          <div className="citation-drawer" style={{ width: citationWidth }}>
            <button
              type="button"
              className="preview-close"
              onClick={() => setCitation(null)}
            >
              关闭引用 ×
            </button>
            <div className="citation-card">
              <strong>引用回读 · {citation.current_status}</strong>
              <pre className="citation-quote">{citation.quote}</pre>
              <small>{formatCitationLocator(citation.locator)}</small>
            </div>
            <div
              className="citation-resize-handle"
              role="separator"
              aria-label="调整引用宽度"
              aria-orientation="vertical"
              {...citationResizeHandleProps}
            />
          </div>
        )}
      </div>
    </ConfigProvider>
  );
}
