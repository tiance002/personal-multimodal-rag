import { useEffect, useState } from "react";
import { api } from "../api/client";

type Props = { documentId: string | null; viewMode: "document" | "graph"; onViewMode: (mode: "document" | "graph") => void; citation?: { quote: string; current_status: string; locator: Record<string, unknown> } | null };

export function GraphPanel({ documentId, viewMode, onViewMode, citation }: Props) {
  const [content, setContent] = useState("");
  const [graph, setGraph] = useState<{ status: string; nodes: { label: string }[]; edges: { relation: string; quote: string }[] } | null>(null);
  const loadGraph = () => { if (documentId) void api.getGraph(documentId).then(setGraph).catch(() => setGraph(null)); };
  useEffect(() => { if (!documentId) { setContent(""); setGraph(null); return; } void api.getContent(documentId).then((data) => setContent(data.content)).catch(() => setContent("无法读取当前版本")); loadGraph(); }, [documentId]);
  const rebuildGraph = async () => { if (!documentId) return; await api.rebuildGraph(documentId); loadGraph(); };
  return <aside className="inspector"><div className="inspector-head"><div><div className="section-kicker">阅读台</div><h2>{documentId ? "当前资料" : "等待选择"}</h2></div><div className="view-tabs"><button className={viewMode === "document" ? "active" : ""} onClick={() => onViewMode("document")}>文档</button><button className={viewMode === "graph" ? "active" : ""} onClick={() => onViewMode("graph")}>图谱</button></div></div>{citation ? <div className="citation-card"><div className="citation-label">引用回读 · {citation.current_status}</div><p>{citation.quote}</p><small>定位 {JSON.stringify(citation.locator)}</small></div> : null}{viewMode === "document" ? <pre className="document-preview">{content || "选择一份资料，查看已索引的原文。"}</pre> : <div className="graph-preview"><div className="graph-status"><span className="status-pip" />{graph?.status ?? "未建立"}{documentId && graph?.status !== "ready" ? <button className="graph-build" onClick={() => void rebuildGraph()}>建立图谱</button> : null}</div>{graph?.nodes.map((node) => <div className="graph-node" key={node.label}>{node.label}</div>)}{graph?.edges.map((edge, index) => <div className="graph-edge" key={`${edge.relation}-${index}`}><span>↳ {edge.relation}</span><small>{edge.quote}</small></div>)}</div>}</aside>;
}
