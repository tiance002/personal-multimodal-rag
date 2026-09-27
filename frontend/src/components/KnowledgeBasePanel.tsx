import { useRef, useState } from "react";
import type { DocumentItem, IngestionJob, KnowledgeBase } from "../app/state";

type Props = {
  bases: KnowledgeBase[];
  selectedBaseId: string | null;
  documents: DocumentItem[];
  selectedDocumentId: string | null;
  documentScope: string[];
  scopeSyncing: boolean;
  questionPending: boolean;
  uploadBusy: boolean;
  ingestionJob: (IngestionJob & { knowledgeBaseId: string }) | null;
  ingestionIssue: boolean;
  onCreateBase: (name: string) => Promise<boolean>;
  onSelectBase: (id: string) => void;
  onSelectDocument: (id: string) => void;
  onToggleDocumentScope: (id: string) => void;
  onUpload: (file: File) => void;
  onRetryIngestion: () => void;
  onRetryJob: (jobId: string) => void;
  onRefreshIngestion: () => void;
};

export function KnowledgeBasePanel({ bases, selectedBaseId, documents, selectedDocumentId, documentScope, scopeSyncing, questionPending, uploadBusy, ingestionJob, ingestionIssue, onCreateBase, onSelectBase, onSelectDocument, onToggleDocumentScope, onUpload, onRetryIngestion, onRetryJob, onRefreshIngestion }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [creating, setCreating] = useState(false);
  const [baseName, setBaseName] = useState("");
  const [createPending, setCreatePending] = useState(false);
  const submitCreate = async () => {
    const name = baseName.trim();
    if (!name || createPending) return;
    setCreatePending(true);
    try {
      if (await onCreateBase(name)) { setBaseName(""); setCreating(false); }
    } finally { setCreatePending(false); }
  };
  const activeJob = ingestionJob?.knowledgeBaseId === selectedBaseId ? ingestionJob : null;

  return <section className="library-panel">
    <div className="section-kicker">资料台</div>
    <div className="library-heading"><div><h1>知识库</h1><p>选择一个空间，保持检索边界清晰。</p></div><div className="library-actions"><button className="quiet-button" type="button" disabled={scopeSyncing || questionPending} onClick={() => setCreating((current) => !current)}>＋ 创建</button><button className="quiet-button" type="button" disabled={!selectedBaseId || uploadBusy} onClick={() => input.current?.click()}>＋ 导入</button></div><input ref={input} hidden type="file" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); event.target.value = ""; }} /></div>
    {(creating || bases.length === 0) && <form className="create-base" onSubmit={(event) => { event.preventDefault(); void submitCreate(); }}><label className="field-label" htmlFor="new-kb-name">知识库名称</label><div className="create-base-controls"><input id="new-kb-name" value={baseName} maxLength={200} onChange={(event) => setBaseName(event.target.value)} placeholder="例如：我的资料" /><button type="submit" disabled={createPending || scopeSyncing || questionPending || !baseName.trim()}>创建知识库</button></div></form>}
    <label className="field-label" htmlFor="kb-select">当前空间</label>
    <select id="kb-select" className="kb-select" value={selectedBaseId ?? ""} disabled={scopeSyncing || questionPending} onChange={(event) => onSelectBase(event.target.value)}><option value="" disabled>选择知识库</option>{bases.map((base) => <option key={base.id} value={base.id}>{base.name}</option>)}</select>
    <div className="scope-summary" aria-label="当前检索范围">{scopeSyncing ? "正在同步检索范围，暂不可提问" : questionPending ? "正在回答，暂不可切换检索范围" : `当前检索范围：${documentScope.length ? `仅 ${documents.find((document) => documentScope.includes(document.id))?.file_name ?? "选定资料"}` : "当前知识库的全部已索引资料"}`}</div>
    <div className="dropzone" onClick={() => { if (selectedBaseId && !uploadBusy) input.current?.click(); }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file && selectedBaseId && !uploadBusy) onUpload(file); }}><div className="drop-icon">↥</div><strong>{uploadBusy ? "等待当前资料完成" : "拖入资料"}</strong><span>PDF、Markdown、TXT、图片</span></div>
    {activeJob && <div className="ingestion-status" role="status"><span>摄取任务 · {activeJob.stage} · {activeJob.progress}% · {activeJob.status === "succeeded" ? "已完成" : activeJob.status === "failed" ? "失败" : activeJob.status === "cancelled" ? "已取消" : "进行中"}</span>{activeJob.error_code && <strong>{activeJob.error_code}</strong>}{activeJob.status === "failed" && activeJob.attempts < activeJob.max_attempts && <button type="button" onClick={onRetryIngestion}>重试摄取</button>}{ingestionIssue && <button type="button" onClick={onRefreshIngestion}>刷新状态</button>}</div>}
    <div className="document-list-head"><span>资料 {documents.length}</span><span className="muted">按最近更新</span></div>
    <div className="document-list">{documents.length === 0 ? <div className="empty-document"><span>○</span><p>这个空间还很安静<br /><small>拖入第一份资料开始建立索引</small></p></div> : documents.map((document) => {
      const latestDiffers = document.latest_version_no !== undefined && document.latest_index_status !== undefined && document.latest_index_status !== document.index_status;
      const job = document.latest_job;
      const canRetry = job?.status === "failed" && job.attempts < job.max_attempts;
      return <div key={document.id} className={`document-row ${selectedDocumentId === document.id ? "selected" : ""}`}><button type="button" className="document-select" onClick={() => onSelectDocument(document.id)}><span className="file-glyph">{document.media_type?.includes("pdf") ? "PDF" : document.media_type?.includes("image") ? "IMG" : "TXT"}</span><span className="document-name"><strong>{document.file_name}</strong><small>版本 {document.version_no ?? "—"} · {document.index_status ?? "等待索引"}</small>{latestDiffers && <small className="document-warning">新版本 {document.latest_version_no} · {document.latest_index_status}{job?.error_code ? ` · ${job.error_code}` : ""}</small>}</span><span className={`index-state ${document.index_status === "ready" && !latestDiffers ? "ready" : ""}`} /></button>{canRetry && <button type="button" className="document-retry" onClick={() => onRetryJob(job.id)}>重试索引</button>}<button type="button" disabled={scopeSyncing || questionPending} className={`document-scope-toggle ${documentScope.includes(document.id) ? "active" : ""}`} aria-pressed={documentScope.includes(document.id)} onClick={() => onToggleDocumentScope(document.id)}>{documentScope.includes(document.id) ? "取消限定" : "仅此文档"}</button></div>;
    })}</div>
  </section>;
}
