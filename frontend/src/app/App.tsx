import { useEffect, useMemo, useState } from "react";
import { readRunEvents } from "../api/sse";
import { api } from "../api/client";
import type { AppState, Conversation, DocumentItem, KnowledgeBase, Message, ViewMode } from "./state";
import { ChatPanel } from "../components/ChatPanel";
import { GraphPanel } from "../components/GraphPanel";
import { KnowledgeBasePanel } from "../components/KnowledgeBasePanel";
import { Sidebar } from "../components/Sidebar";

const initialState: AppState = { selectedKnowledgeBaseId: null, selectedDocumentId: null, documentScope: [], viewMode: "document", activeConversationId: null };

export default function App() {
  const [state, setState] = useState<AppState>(initialState);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [citation, setCitation] = useState<{ quote: string; current_status: string; locator: Record<string, unknown> } | null>(null);
  const [notice, setNotice] = useState("本地索引已连接");

  const selectedBase = useMemo(() => bases.find((base) => base.id === state.selectedKnowledgeBaseId), [bases, state.selectedKnowledgeBaseId]);
  const activeConversation = useMemo(() => conversations.find((conversation) => conversation.id === state.activeConversationId), [conversations, state.activeConversationId]);
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
  useEffect(() => { if (!state.selectedKnowledgeBaseId) { setDocuments([]); return; } void api.listDocuments(state.selectedKnowledgeBaseId).then(setDocuments).catch(() => setDocuments([])); }, [state.selectedKnowledgeBaseId]);
  useEffect(() => { if (!state.activeConversationId) { setMessages([]); return; } void api.listMessages(state.activeConversationId).then(setMessages).catch(() => setMessages([])); }, [state.activeConversationId]);

  const selectBase = async (id: string) => {
    const previousBaseId = state.selectedKnowledgeBaseId;
    const previousDocumentScope = state.documentScope;
    const conversationId = state.activeConversationId;
    setState((current) => ({ ...current, selectedKnowledgeBaseId: id, selectedDocumentId: null, documentScope: [], viewMode: "document" }));
    setCitation(null);
    if (!conversationId) return;
    try {
      const conversation = await api.updateConversation(conversationId, { knowledge_base_scope: [id], document_scope: [] });
      setConversations((current) => current.map((item) => item.id === conversation.id ? conversation : item));
      setNotice("已切换知识库，会话检索范围已同步");
    } catch (error) {
      setState((current) => ({ ...current, selectedKnowledgeBaseId: previousBaseId, documentScope: previousDocumentScope }));
      setNotice(error instanceof Error ? error.message : "知识库切换失败，会话范围未改变");
    }
  };
  const selectConversation = (id: string) => {
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    const documentScope = conversation.document_scope ?? [];
    setState((current) => ({ ...current, activeConversationId: id, selectedKnowledgeBaseId: conversation.knowledge_base_scope?.[0] ?? null, selectedDocumentId: documentScope[0] ?? null, documentScope, viewMode: "document" }));
    setCitation(null);
  };
  const newConversation = async () => {
    if (!state.selectedKnowledgeBaseId) return;
    try {
      const conversation = await api.createConversation(state.selectedKnowledgeBaseId, state.documentScope);
      setConversations((current) => [conversation, ...current]);
      setState((current) => ({ ...current, activeConversationId: conversation.id, documentScope: conversation.document_scope ?? current.documentScope }));
      setMessages([]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "新建会话失败");
    }
  };
  const toggleDocumentScope = async (documentId: string) => {
    const nextScope = state.documentScope.includes(documentId) ? [] : [documentId];
    const previousScope = state.documentScope;
    const conversationId = state.activeConversationId ?? conversations[0]?.id;
    setState((current) => ({ ...current, selectedDocumentId: documentId, documentScope: nextScope }));
    if (!conversationId || !state.selectedKnowledgeBaseId) {
      setNotice(nextScope.length ? "当前会话将仅检索此文档" : "当前会话将检索整个知识库");
      return;
    }
    try {
      const conversation = await api.updateConversation(conversationId, { knowledge_base_scope: [state.selectedKnowledgeBaseId], document_scope: nextScope });
      setConversations((current) => current.map((item) => item.id === conversation.id ? conversation : item));
      setNotice(nextScope.length ? "当前会话已限定为此文档" : "当前会话已恢复为整个知识库");
    } catch (error) {
      setState((current) => ({ ...current, documentScope: previousScope }));
      setNotice(error instanceof Error ? error.message : "文档范围更新失败");
    }
  };
  const upload = async (file: File) => { if (!state.selectedKnowledgeBaseId) return; setNotice(`正在接收 ${file.name}`); try { await api.upload(state.selectedKnowledgeBaseId, file); setNotice("已接收，后台正在解析与索引"); setDocuments(await api.listDocuments(state.selectedKnowledgeBaseId)); } catch (error) { setNotice(error instanceof Error ? error.message : "上传失败"); } };
  const send = async (content: string, mode: "quick" | "smart") => {
    let conversationId = state.activeConversationId;
    if (!conversationId) {
      if (!state.selectedKnowledgeBaseId) return;
      const conversation = await api.createConversation(state.selectedKnowledgeBaseId, state.documentScope);
      setConversations((current) => [conversation, ...current]);
      conversationId = conversation.id;
      setState((current) => ({ ...current, activeConversationId: conversation.id, documentScope: conversation.document_scope ?? current.documentScope }));
    }
    setMessages((current) => [...current, { role: "user", content }]);
    setNotice(mode === "smart" ? "智能推理正在读取当前知识库" : "正在检索当前知识库");
    try {
      const result = await api.sendMessage(conversationId, content, mode);
      const errorText = result.error_code ? `回答未完成：${result.error_code}` : result.answer;
      setMessages((current) => [...current, { role: "assistant", content: errorText, citations: result.citations, run_id: result.run_id }]);
      setNotice(result.error_code ? `问答失败：${result.error_code}` : "回答已完成，证据已冻结");
      if (!result.error_code && result.run_id && result.citations[0]) { await readRunEvents(result.run_id, () => undefined); const nextCitation = await api.getCitation(result.run_id, result.citations[0]); setCitation(nextCitation); }
    } catch (error) { setNotice(error instanceof Error ? error.message : "问答失败"); }
  };
  const changeView = (viewMode: ViewMode) => setState((current) => ({ ...current, viewMode }));
  const openCitation = async (runId: string, citationId: string) => { try { setCitation(await api.getCitation(runId, citationId)); } catch { setNotice("引用暂时无法回读"); } };
  return <div className="app-shell"><Sidebar conversations={conversations} activeId={state.activeConversationId} onSelect={selectConversation} onNew={() => void newConversation()} /><main className="main-column"><header className="topbar"><div className="crumb"><span className="crumb-dot" />工作台 <span>/</span> {selectedBase?.name ?? "未选择知识库"}</div><div className="topbar-actions"><span className="local-badge">本地优先</span><span className="notice">{notice}</span></div></header><div className="work-grid"><KnowledgeBasePanel bases={bases} selectedBaseId={state.selectedKnowledgeBaseId} documents={documents} selectedDocumentId={state.selectedDocumentId} documentScope={state.documentScope} onSelectBase={(id) => void selectBase(id)} onSelectDocument={(id) => setState((current) => ({ ...current, selectedDocumentId: id }))} onToggleDocumentScope={(id) => void toggleDocumentScope(id)} onUpload={(file) => void upload(file)} /><ChatPanel messages={messages} disabled={!state.selectedKnowledgeBaseId} onSend={(content, mode) => void send(content, mode)} onCitation={(runId, citationId) => void openCitation(runId, citationId)} /></div></main><GraphPanel documentId={state.selectedDocumentId} graphEnabled={graphEnabled} viewMode={state.viewMode} onViewMode={changeView} citation={citation} /></div>;
}
