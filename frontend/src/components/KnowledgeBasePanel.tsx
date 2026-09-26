import { useRef } from "react";
import type { DocumentItem, KnowledgeBase } from "../app/state";

type Props = { bases: KnowledgeBase[]; selectedBaseId: string | null; documents: DocumentItem[]; onSelectBase: (id: string) => void; onSelectDocument: (id: string) => void; onUpload: (file: File) => void; selectedDocumentId: string | null };

export function KnowledgeBasePanel({ bases, selectedBaseId, documents, onSelectBase, onSelectDocument, onUpload, selectedDocumentId }: Props) {
  const input = useRef<HTMLInputElement>(null);
  return <section className="library-panel">
    <div className="section-kicker">资料台</div>
    <div className="library-heading"><div><h1>知识库</h1><p>选择一个空间，保持检索边界清晰。</p></div><button className="quiet-button" onClick={() => input.current?.click()}>＋ 导入</button><input ref={input} hidden type="file" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUpload(file); }} /></div>
    <label className="field-label" htmlFor="kb-select">当前空间</label>
    <select id="kb-select" className="kb-select" value={selectedBaseId ?? ""} onChange={(event) => onSelectBase(event.target.value)}><option value="" disabled>选择知识库</option>{bases.map((base) => <option key={base.id} value={base.id}>{base.name}</option>)}</select>
    <div className="dropzone" onClick={() => input.current?.click()} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file) onUpload(file); }}><div className="drop-icon">↥</div><strong>拖入资料</strong><span>PDF、Markdown、TXT、图片</span></div>
    <div className="document-list-head"><span>资料 {documents.length}</span><span className="muted">按最近更新</span></div>
    <div className="document-list">{documents.length === 0 ? <div className="empty-document"><span>○</span><p>这个空间还很安静<br /><small>拖入第一份资料开始建立索引</small></p></div> : documents.map((document) => <button key={document.id} className={`document-row ${selectedDocumentId === document.id ? "selected" : ""}`} onClick={() => onSelectDocument(document.id)}><span className="file-glyph">{document.media_type?.includes("pdf") ? "PDF" : document.media_type?.includes("image") ? "IMG" : "TXT"}</span><span className="document-name"><strong>{document.file_name}</strong><small>版本 {document.version_no ?? "—"} · {document.index_status ?? "等待索引"}</small></span><span className={`index-state ${document.index_status === "ready" ? "ready" : ""}`} /></button>)}</div>
  </section>;
}
