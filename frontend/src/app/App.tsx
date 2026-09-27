import { useEffect, useMemo, useRef, useState } from "react";
import { readRunEvents } from "../api/sse";
import { api } from "../api/client";
import type { AppState, Conversation, DocumentItem, IngestionJob, KnowledgeBase, Message, ViewMode } from "./state";
import { ChatPanel } from "../components/ChatPanel";
import { GraphPanel } from "../components/GraphPanel";
import { KnowledgeBasePanel } from "../components/KnowledgeBasePanel";
import { Sidebar } from "../components/Sidebar";

const initialState: AppState = { selectedKnowledgeBaseId: null, selectedDocumentId: null, documentScope: [], viewMode: "document", activeConversationId: null };
const emptyDocuments: DocumentItem[] = [];
type ScopedDocuments = { knowledgeBaseId: string; items: DocumentItem[] };
type ScopedJob = IngestionJob & { knowledgeBaseId: string };

export default function App() {
  const [state, setState] = useState<AppState>(initialState);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [documentList, setDocumentList] = useState<ScopedDocuments | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [citation, setCitation] = useState<{ quote: string; current_status: string; locator: Record<string, unknown> } | null>(null);
  const [notice, setNotice] = useState("本地索引已连接");
  const scopeSyncRef = useRef(false);
  const sendPendingRef = useRef(false);
  const messageRequestEpoch = useRef(0);
  const previousMessageContext = useRef<{ conversationId: string | null; scopeKey: string }>({ conversationId: null, scopeKey: "" });
  const skipHistoryLoadFor = useRef<string | null>(null);
  const [scopeSyncing, setScopeSyncing] = useState(false);
  const [scopeBlocked, setScopeBlocked] = useState(false);
  const scopeBlockedRef = useRef(false);
  const markScopeBlocked = (blocked: boolean) => { scopeBlockedRef.current = blocked; setScopeBlocked(blocked); };
  const [sending, setSending] = useState(false);
  const [ingestionJobs, setIngestionJobs] = useState<Record<string, ScopedJob>>({});
  const [ingestionIssues, setIngestionIssues] = useState<Record<string, boolean>>({});
  const ingestionPolls = useRef<Record<string, number>>({});
  const uploadBusyRef = useRef<Set<string>>(new Set());
  const [uploadBusyBases, setUploadBusyBases] = useState<Set<string>>(new Set());
  const selectedBaseIdRef = useRef<string | null>(null);
  selectedBaseIdRef.current = state.selectedKnowledgeBaseId;

  const documents = documentList?.knowledgeBaseId === state.selectedKnowledgeBaseId ? documentList.items : emptyDocuments;
  const ingestionJob = state.selectedKnowledgeBaseId ? ingestionJobs[state.selectedKnowledgeBaseId] ?? null : null;
  const ingestionIssue = state.selectedKnowledgeBaseId ? Boolean(ingestionIssues[state.selectedKnowledgeBaseId]) : false;
  const uploadBusy = state.selectedKnowledgeBaseId ? uploadBusyBases.has(state.selectedKnowledgeBaseId) : false;
  const setDocumentsForBase = (kbId: string, items: DocumentItem[]) => {
    if (selectedBaseIdRef.current === kbId) setDocumentList({ knowledgeBaseId: kbId, items });
  };
  const setJobForBase = (kbId: string, job: IngestionJob) => {
    setIngestionJobs((current) => ({ ...current, [kbId]: { ...job, knowledgeBaseId: kbId } }));
  };
  const setUploadBusyForBase = (kbId: string, busy: boolean) => {
    const next = new Set(uploadBusyRef.current);
    if (busy) next.add(kbId); else next.delete(kbId);
    uploadBusyRef.current = next;
    setUploadBusyBases(next);
  };
  const setIngestionIssueForBase = (kbId: string, issue: boolean) => {
    setIngestionIssues((current) => ({ ...current, [kbId]: issue }));
  };

  const selectedBase = useMemo(() => bases.find((base) => base.id === state.selectedKnowledgeBaseId), [bases, state.selectedKnowledgeBaseId]);
  const activeConversation = useMemo(() => conversations.find((conversation) => conversation.id === state.activeConversationId), [conversations, state.activeConversationId]);
  const messageScopeKey = JSON.stringify([state.selectedKnowledgeBaseId, [...state.documentScope].sort()]);
  const activeKnowledgeBaseScope = activeConversation?.knowledge_base_scope?.length ? activeConversation.knowledge_base_scope : (state.selectedKnowledgeBaseId ? [state.selectedKnowledgeBaseId] : []);
  const graphEnabled = activeKnowledgeBaseScope.length > 0 && activeKnowledgeBaseScope.every((id) => bases.find((base) => base.id === id)?.graph_enabled === true);

  useEffect(() => {
    void Promise.all([api.listKnowledgeBases(), api.listConversations()]).then(([nextBases, nextConversations]) => {
      setBases(nextBases);
      setConversations(nextConversations);
      const firstConversation = nextConversations[0];
      setState((current) => ({
        ...current,
        selectedKnowledgeBaseId: current.selectedKnowledgeBaseId ?? firstConversation?.knowledge_base_scope?.[0] ?? nextBases[0]?.id ?? null,
        activeConversationId: current.activeConversationId ?? firstConversation?.id ?? null,
        selectedDocumentId: current.selectedDocumentId ?? firstConversation?.document_scope?.[0] ?? null,
        documentScope: current.documentScope.length ? current.documentScope : (firstConversation?.document_scope ?? []),
      }));
    }).catch(() => setNotice("后端尚未启动，先检查本地服务"));
  }, []);
  useEffect(() => {
    if (!state.selectedKnowledgeBaseId) { setDocumentList(null); return; }
    const kbId = state.selectedKnowledgeBaseId;
    let cancelled = false;
    void api.listDocuments(kbId).then((items) => { if (!cancelled) setDocumentsForBase(kbId, items); }).catch(() => { if (!cancelled) setDocumentsForBase(kbId, []); });
    return () => { cancelled = true; };
  }, [state.selectedKnowledgeBaseId]);
  useEffect(() => {
    const conversationId = state.activeConversationId;
    const previous = previousMessageContext.current;
    previousMessageContext.current = { conversationId, scopeKey: messageScopeKey };
    const epoch = ++messageRequestEpoch.current;
    if (!conversationId) { setMessages([]); return; }
    if (skipHistoryLoadFor.current === conversationId) { skipHistoryLoadFor.current = null; return; }
    // Changing scope in the same conversation clears the old transcript, but
    // must not re-fetch that conversation's A-scope history into the B view.
    if (previous.conversationId === conversationId && previous.scopeKey !== messageScopeKey) { setMessages([]); return; }
    let cancelled = false;
    setMessages([]);
    void api.listMessages(conversationId)
      .then((items) => { if (!cancelled && messageRequestEpoch.current === epoch && !sendPendingRef.current) setMessages(items); })
      .catch(() => { if (!cancelled && messageRequestEpoch.current === epoch && !sendPendingRef.current) setMessages([]); });
    return () => { cancelled = true; };
  }, [state.activeConversationId, messageScopeKey]);
  useEffect(() => {
    if (!ingestionJob || ingestionIssue || ["succeeded", "failed", "cancelled"].includes(ingestionJob.status)) return;
    const kbId = ingestionJob.knowledgeBaseId;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let consecutiveErrors = 0;
    const poll = async () => {
      if (cancelled) return;
      ingestionPolls.current[kbId] = (ingestionPolls.current[kbId] ?? 0) + 1;
      if (ingestionPolls.current[kbId] > 240) { setIngestionIssueForBase(kbId, true); if (selectedBaseIdRef.current === kbId) setNotice("摄取仍未结束；已停止自动查询，可手动刷新状态"); return; }
      try {
        const next = await api.getIngestionJob(ingestionJob.id);
        if (cancelled) return;
        if (next.id !== ingestionJob.id) throw new Error("摄取任务标识不匹配");
        consecutiveErrors = 0;
        setJobForBase(kbId, next);
        if (["succeeded", "failed", "cancelled"].includes(next.status)) {
          setUploadBusyForBase(kbId, false);
          if (selectedBaseIdRef.current === kbId) setNotice(next.status === "succeeded" ? "资料解析与索引已完成" : `摄取失败：${next.error_code ?? next.status}`);
        } else timer = setTimeout(() => void poll(), 1500);
      } catch {
        if (cancelled) return;
        if (++consecutiveErrors >= 3) { setIngestionIssueForBase(kbId, true); if (selectedBaseIdRef.current === kbId) setNotice("无法读取摄取任务状态，请手动刷新"); return; }
        timer = setTimeout(() => void poll(), 1500);
      }
    };
    timer = setTimeout(() => void poll(), 1500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [ingestionJob?.id, ingestionJob?.status, ingestionIssue, state.selectedKnowledgeBaseId]);
  useEffect(() => {
    if (!ingestionJob || !["succeeded", "failed", "cancelled"].includes(ingestionJob.status) || state.selectedKnowledgeBaseId !== ingestionJob.knowledgeBaseId) return;
    let cancelled = false;
    const kbId = ingestionJob.knowledgeBaseId;
    void api.listDocuments(kbId).then((items) => { if (!cancelled) setDocumentsForBase(kbId, items); }).catch(() => { if (!cancelled && selectedBaseIdRef.current === kbId) setNotice("任务已结束，但资料列表暂不可读"); });
    return () => { cancelled = true; };
  }, [ingestionJob?.id, ingestionJob?.status, ingestionJob?.knowledgeBaseId, state.selectedKnowledgeBaseId]);
  // After a page refresh the in-memory job is gone. Rebuild the latest
  // non-terminal or failed job from the document list so the task status,
  // error and retry entry stay visible instead of silently disappearing.
  // Once an in-session job owns this knowledge base we never override it from
  // the (possibly stale) document list.
  useEffect(() => {
    const kbId = state.selectedKnowledgeBaseId;
    if (!kbId || documentList?.knowledgeBaseId !== kbId || ingestionJob) return;
    const recovered = documents.find((document) => document.latest_job && !["succeeded", "cancelled"].includes(document.latest_job.status));
    const job = recovered?.latest_job;
    if (!job) return;
    const pending = !["succeeded", "failed", "cancelled"].includes(job.status);
    setUploadBusyForBase(kbId, pending);
    ingestionPolls.current[kbId] = 0;
    setIngestionIssueForBase(kbId, false);
    setJobForBase(kbId, job);
  }, [documents, documentList?.knowledgeBaseId, state.selectedKnowledgeBaseId, ingestionJob]);

  const syncScope = async (conversationId: string, patch: { knowledge_base_scope: string[]; document_scope: string[] }, successNotice: string, previous: AppState) => {
    try {
      const conversation = await api.updateConversation(conversationId, patch);
      setConversations((current) => current.map((item) => item.id === conversation.id ? conversation : item));
      markScopeBlocked(false);
      setNotice(successNotice);
    } catch (error) {
      try {
        const serverConversations = await api.listConversations();
        const actual = serverConversations.find((item) => item.id === conversationId);
        if (!actual) throw new Error("会话已不存在");
        setConversations(serverConversations);
        setState((current) => ({ ...current, selectedKnowledgeBaseId: actual.knowledge_base_scope[0] ?? null, selectedDocumentId: actual.document_scope[0] ?? null, documentScope: actual.document_scope, viewMode: "document" }));
        markScopeBlocked(true);
        setNotice(`${error instanceof Error ? error.message : "范围更新失败"}；已显示服务端范围，但写入结果不确定，请刷新后再提问`);
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
    if (scopeSyncRef.current || sendPendingRef.current || !id || id === state.selectedKnowledgeBaseId) return;
    messageRequestEpoch.current++;
    const conversationId = state.activeConversationId;
    const previous = state;
    if (conversationId) { scopeSyncRef.current = true; setScopeSyncing(true); }
    setState((current) => ({ ...current, selectedKnowledgeBaseId: id, selectedDocumentId: null, documentScope: [], viewMode: "document" }));
    setCitation(null);
    // The conversation scope now points at another knowledge base; drop the
    // displayed transcript so answers from the previous base are not mixed in.
    setMessages([]);
    if (!conversationId) return;
    await syncScope(conversationId, { knowledge_base_scope: [id], document_scope: [] }, "已切换知识库，会话检索范围已同步", previous);
  };
  const selectConversation = (id: string) => {
    if (scopeSyncRef.current || sendPendingRef.current) return;
    if (id === state.activeConversationId) return;
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    messageRequestEpoch.current++;
    setMessages([]);
    const documentScope = conversation.document_scope ?? [];
    setState((current) => ({ ...current, activeConversationId: id, selectedKnowledgeBaseId: conversation.knowledge_base_scope?.[0] ?? null, selectedDocumentId: documentScope[0] ?? null, documentScope, viewMode: "document" }));
    setCitation(null);
  };
  const newConversation = async () => {
    if (scopeSyncRef.current || sendPendingRef.current || scopeBlockedRef.current || !state.selectedKnowledgeBaseId) return;
    try {
      const conversation = await api.createConversation(state.selectedKnowledgeBaseId, state.documentScope);
      setConversations((current) => [conversation, ...current]);
      messageRequestEpoch.current++;
      skipHistoryLoadFor.current = conversation.id;
      setState((current) => ({ ...current, activeConversationId: conversation.id, documentScope: conversation.document_scope ?? current.documentScope }));
      setMessages([]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "新建会话失败");
    }
  };
  const toggleDocumentScope = async (documentId: string) => {
    if (scopeSyncRef.current || sendPendingRef.current) return;
    messageRequestEpoch.current++;
    setMessages([]);
    setCitation(null);
    const nextScope = state.documentScope.includes(documentId) ? [] : [documentId];
    const previous = state;
    const conversationId = state.activeConversationId;
    if (conversationId && state.selectedKnowledgeBaseId) { scopeSyncRef.current = true; setScopeSyncing(true); }
    setState((current) => ({ ...current, selectedDocumentId: documentId, documentScope: nextScope }));
    if (!conversationId || !state.selectedKnowledgeBaseId) {
      setNotice(nextScope.length ? "当前会话将仅检索此文档" : "当前会话将检索整个知识库");
      return;
    }
    await syncScope(conversationId, { knowledge_base_scope: [state.selectedKnowledgeBaseId], document_scope: nextScope }, nextScope.length ? "当前会话已限定为此文档" : "当前会话已恢复为整个知识库", previous);
  };
  const createBase = async (name: string): Promise<boolean> => {
    if (scopeSyncRef.current || sendPendingRef.current) return false;
    try {
      const created = await api.createKnowledgeBase(name);
      setBases((current) => [...current, created]);
      if (state.activeConversationId) await selectBase(created.id);
      else setState((current) => ({ ...current, selectedKnowledgeBaseId: created.id, selectedDocumentId: null, documentScope: [], viewMode: "document" }));
      if (!state.activeConversationId) setNotice(`已创建知识库：${created.name}`);
      return true;
    } catch (error) { setNotice(error instanceof Error ? error.message : "创建知识库失败"); return false; }
  };
  const upload = async (file: File) => {
    const kbId = state.selectedKnowledgeBaseId;
    if (!kbId || uploadBusyRef.current.has(kbId)) return;
    setUploadBusyForBase(kbId, true);
    setNotice(`正在接收 ${file.name}`);
    try {
      const receipt = await api.upload(kbId, file);
      ingestionPolls.current[kbId] = 0;
      setIngestionIssueForBase(kbId, false);
      setJobForBase(kbId, { id: receipt.job_id, status: "queued", stage: "queued", progress: 0, attempts: 0, max_attempts: 3, error_code: null });
      if (selectedBaseIdRef.current === kbId) setNotice("已接收，后台正在解析与索引");
      try {
        const nextDocuments = await api.listDocuments(kbId);
        setDocumentsForBase(kbId, nextDocuments);
      } catch { if (selectedBaseIdRef.current === kbId) setNotice("文件已接收；资料列表暂不可读，继续查询摄取任务"); }
    } catch (error) {
      setUploadBusyForBase(kbId, false);
      if (selectedBaseIdRef.current === kbId) setNotice(error instanceof Error ? error.message : "上传失败");
    }
  };
  const retryIngestion = async (jobId?: string) => {
    const kbId = state.selectedKnowledgeBaseId;
    const targetId = jobId ?? ingestionJob?.id;
    if (!targetId || !kbId) return;
    // A row retry must come from this knowledge base's scoped document list.
    // The status-panel retry must match the selected base's own tracked job.
    if (jobId ? !documents.some((document) => document.latest_job?.id === targetId) : ingestionJob?.knowledgeBaseId !== kbId) return;
    try {
      const next = await api.retryIngestionJob(targetId);
      if (next.id !== targetId) throw new Error("摄取任务标识不匹配");
      ingestionPolls.current[kbId] = 0;
      setIngestionIssueForBase(kbId, false);
      const pending = !["succeeded", "failed", "cancelled"].includes(next.status);
      setUploadBusyForBase(kbId, pending);
      setJobForBase(kbId, next);
      if (selectedBaseIdRef.current === kbId) setNotice(next.status === "queued" ? "重试已排队，等待摄取 Worker" : `任务状态：${next.status}`);
    } catch (error) { if (selectedBaseIdRef.current === kbId) setNotice(error instanceof Error ? error.message : "重试失败"); }
  };
  const refreshIngestion = async () => {
    if (!ingestionJob) return;
    const kbId = ingestionJob.knowledgeBaseId;
    ingestionPolls.current[kbId] = 0;
    setIngestionIssueForBase(kbId, false);
    try {
      const next = await api.getIngestionJob(ingestionJob.id);
      if (next.id !== ingestionJob.id) throw new Error("摄取任务标识不匹配");
      setUploadBusyForBase(kbId, !["succeeded", "failed", "cancelled"].includes(next.status));
      setJobForBase(kbId, next);
    }
    catch (error) { setIngestionIssueForBase(kbId, true); if (selectedBaseIdRef.current === kbId) setNotice(error instanceof Error ? error.message : "状态查询失败"); }
  };
  const send = async (content: string, mode: "quick" | "smart") => {
    const kbId = state.selectedKnowledgeBaseId;
    if (scopeSyncRef.current || sendPendingRef.current || scopeBlockedRef.current || !kbId) return;
    sendPendingRef.current = true;
    messageRequestEpoch.current++;
    setSending(true);
    const documentScope = [...state.documentScope];
    let conversationId = state.activeConversationId;
    try {
      if (!conversationId) {
        const conversation = await api.createConversation(kbId, documentScope);
        setConversations((current) => [conversation, ...current]);
        conversationId = conversation.id;
        skipHistoryLoadFor.current = conversation.id;
        setState((current) => ({ ...current, activeConversationId: conversation.id, documentScope: conversation.document_scope ?? current.documentScope }));
      }
      setMessages((current) => [...current, { role: "user", content }]);
      setNotice(mode === "smart" ? "智能推理正在读取当前知识库" : "正在检索当前知识库");
      const result = await api.sendMessage(conversationId, content, mode, [kbId], documentScope);
      const errorText = result.error_code ? `回答未完成：${result.error_code}` : result.answer;
      setMessages((current) => [...current, { role: "assistant", content: errorText, citations: result.citations, run_id: result.run_id }]);
      setNotice(result.error_code ? `问答失败：${result.error_code}` : "回答已完成，证据已冻结");
      if (!result.error_code && result.run_id && result.citations[0]) { await readRunEvents(result.run_id, () => undefined); const nextCitation = await api.getCitation(result.run_id, result.citations[0]); setCitation(nextCitation); }
    } catch (error) {
      if (error instanceof Error && error.message.includes("CONVERSATION_SCOPE_CHANGED") && conversationId) {
        markScopeBlocked(true);
        try {
          const serverConversations = await api.listConversations();
          const actual = serverConversations.find((item) => item.id === conversationId);
          if (!actual) throw new Error("会话已不存在");
          setConversations(serverConversations);
          setState((current) => ({ ...current, selectedKnowledgeBaseId: actual.knowledge_base_scope[0] ?? null, selectedDocumentId: actual.document_scope[0] ?? null, documentScope: actual.document_scope, viewMode: "document" }));
          setNotice("服务端范围已变化；已显示最新范围，请刷新后再提问");
        } catch { setNotice("服务端范围已变化且无法读取，请刷新后再提问"); }
      } else setNotice(error instanceof Error ? error.message : "问答失败");
    } finally { sendPendingRef.current = false; setSending(false); }
  };
  const changeView = (viewMode: ViewMode) => setState((current) => ({ ...current, viewMode }));
  const openCitation = async (runId: string, citationId: string) => { try { setCitation(await api.getCitation(runId, citationId)); } catch { setNotice("引用暂时无法回读"); } };
  return <div className="app-shell"><Sidebar conversations={conversations} activeId={state.activeConversationId} onSelect={selectConversation} onNew={() => void newConversation()} /><main className="main-column"><header className="topbar"><div className="crumb"><span className="crumb-dot" />工作台 <span>/</span> {selectedBase?.name ?? "未选择知识库"}</div><div className="topbar-actions"><span className="local-badge">本地优先</span><span className="notice">{notice}</span></div></header><div className="work-grid"><KnowledgeBasePanel bases={bases} selectedBaseId={state.selectedKnowledgeBaseId} documents={documents} selectedDocumentId={state.selectedDocumentId} documentScope={state.documentScope} scopeSyncing={scopeSyncing} questionPending={sending} uploadBusy={uploadBusy} ingestionJob={ingestionJob} ingestionIssue={ingestionIssue} onCreateBase={createBase} onSelectBase={(id) => void selectBase(id)} onSelectDocument={(id) => setState((current) => ({ ...current, selectedDocumentId: id }))} onToggleDocumentScope={(id) => void toggleDocumentScope(id)} onUpload={(file) => void upload(file)} onRetryIngestion={() => void retryIngestion()} onRetryJob={(jobId) => void retryIngestion(jobId)} onRefreshIngestion={() => void refreshIngestion()} /><ChatPanel messages={messages} disabled={!state.selectedKnowledgeBaseId || scopeSyncing || scopeBlocked || sending} disabledHint={scopeBlocked ? "无法确认检索范围，请刷新页面" : scopeSyncing ? "正在同步检索范围" : sending ? "正在回答，请稍候" : undefined} onSend={(content, mode) => void send(content, mode)} onCitation={(runId, citationId) => void openCitation(runId, citationId)} /></div></main><GraphPanel documentId={state.selectedDocumentId} graphEnabled={graphEnabled} viewMode={state.viewMode} onViewMode={changeView} citation={citation} /></div>;
}
