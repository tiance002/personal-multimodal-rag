import { useEffect, useMemo, useState } from "react";
import { readRunEvents } from "../api/sse";
import { api } from "../api/client";
import type { AppState, Conversation, DocumentItem, KnowledgeBase, Message, ViewMode } from "./state";
import { ChatPanel } from "../components/ChatPanel";
import { GraphPanel } from "../components/GraphPanel";
import { KnowledgeBasePanel } from "../components/KnowledgeBasePanel";
import { Sidebar } from "../components/Sidebar";

const initialState: AppState = { selectedKnowledgeBaseId: null, selectedDocumentId: null, viewMode: "document", activeConversationId: null };

export default function App() {
  const [state, setState] = useState<AppState>(initialState);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [citation, setCitation] = useState<{ quote: string; current_status: string; locator: Record<string, unknown> } | null>(null);
  const [notice, setNotice] = useState("本地索引已连接");

  const selectedBase = useMemo(() => bases.find((base) => base.id === state.selectedKnowledgeBaseId), [bases, state.selectedKnowledgeBaseId]);
  useEffect(() => { void Promise.all([api.listKnowledgeBases(), api.listConversations()]).then(([nextBases, nextConversations]) => { setBases(nextBases); setConversations(nextConversations); if (nextBases[0]) setState((current) => ({ ...current, selectedKnowledgeBaseId: current.selectedKnowledgeBaseId ?? nextBases[0].id })); if (nextConversations[0]) setState((current) => ({ ...current, activeConversationId: current.activeConversationId ?? nextConversations[0].id })); }).catch(() => setNotice("后端尚未启动，先检查本地服务")); }, []);
  useEffect(() => { if (!state.selectedKnowledgeBaseId) return; void api.listDocuments(state.selectedKnowledgeBaseId).then(setDocuments).catch(() => setDocuments([])); }, [state.selectedKnowledgeBaseId]);
  useEffect(() => { if (!state.activeConversationId) { setMessages([]); return; } void api.listMessages(state.activeConversationId).then(setMessages).catch(() => setMessages([])); }, [state.activeConversationId]);

  const selectBase = (id: string) => setState((current) => ({ ...current, selectedKnowledgeBaseId: id, selectedDocumentId: null, viewMode: "document" }));
  const selectConversation = (id: string) => setState((current) => ({ ...current, activeConversationId: id }));
  const newConversation = async () => { if (!state.selectedKnowledgeBaseId) return; const conversation = await api.createConversation(state.selectedKnowledgeBaseId); setConversations((current) => [conversation, ...current]); setState((current) => ({ ...current, activeConversationId: conversation.id })); setMessages([]); };
  const upload = async (file: File) => { if (!state.selectedKnowledgeBaseId) return; setNotice(`正在接收 ${file.name}`); try { await api.upload(state.selectedKnowledgeBaseId, file); setNotice("已接收，后台正在解析与索引"); setDocuments(await api.listDocuments(state.selectedKnowledgeBaseId)); } catch (error) { setNotice(error instanceof Error ? error.message : "上传失败"); } };
  const send = async (content: string, mode: "quick" | "smart") => { let conversationId = state.activeConversationId; if (!conversationId) { if (!state.selectedKnowledgeBaseId) return; const conversation = await api.createConversation(state.selectedKnowledgeBaseId); setConversations((current) => [conversation, ...current]); conversationId = conversation.id; setState((current) => ({ ...current, activeConversationId: conversation.id })); } setMessages((current) => [...current, { role: "user", content }]); setNotice(mode === "smart" ? "智能推理正在读取当前知识库" : "正在检索当前知识库"); try { const result = await api.sendMessage(conversationId, content, mode); setMessages((current) => [...current, { role: "assistant", content: result.answer, citations: result.citations, run_id: result.run_id }]); setNotice("回答已完成，证据已冻结"); if (result.run_id && result.citations[0]) { await readRunEvents(result.run_id, () => undefined); const nextCitation = await api.getCitation(result.run_id, result.citations[0]); setCitation(nextCitation); } } catch (error) { setNotice(error instanceof Error ? error.message : "问答失败"); } };
  const changeView = (viewMode: ViewMode) => setState((current) => ({ ...current, viewMode }));
  const openCitation = async (runId: string, citationId: string) => { try { setCitation(await api.getCitation(runId, citationId)); } catch { setNotice("引用暂时无法回读"); } };
  return <div className="app-shell"><Sidebar conversations={conversations} activeId={state.activeConversationId} onSelect={selectConversation} onNew={() => void newConversation()} /><main className="main-column"><header className="topbar"><div className="crumb"><span className="crumb-dot" />工作台 <span>/</span> {selectedBase?.name ?? "未选择知识库"}</div><div className="topbar-actions"><span className="local-badge">本地优先</span><span className="notice">{notice}</span></div></header><div className="work-grid"><KnowledgeBasePanel bases={bases} selectedBaseId={state.selectedKnowledgeBaseId} documents={documents} selectedDocumentId={state.selectedDocumentId} onSelectBase={selectBase} onSelectDocument={(id) => setState((current) => ({ ...current, selectedDocumentId: id }))} onUpload={(file) => void upload(file)} /><ChatPanel messages={messages} disabled={!state.selectedKnowledgeBaseId} onSend={(content, mode) => void send(content, mode)} onCitation={(runId, citationId) => void openCitation(runId, citationId)} /></div></main><GraphPanel documentId={state.selectedDocumentId} viewMode={state.viewMode} onViewMode={changeView} citation={citation} /></div>;
}
