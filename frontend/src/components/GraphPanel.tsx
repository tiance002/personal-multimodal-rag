import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

type Props = {
  documentId: string | null;
  graphEnabled: boolean;
  viewMode: "document" | "graph";
  onViewMode: (mode: "document" | "graph") => void;
  originalPreview?: {
    url: string;
    fileName: string;
    mediaType: string;
  } | null;
  citation?: {
    quote: string;
    current_status: string;
    locator: Record<string, unknown>;
  } | null;
};

function supportsNativePreview(fileName: string, mediaType: string) {
  const lowerName = fileName.toLowerCase();
  return (
    mediaType === "application/pdf" ||
    mediaType.startsWith("image/") ||
    mediaType.startsWith("text/") ||
    mediaType === "application/xhtml+xml" ||
    /\.(html?|md|markdown|txt|csv)$/.test(lowerName)
  );
}

function OriginalDocumentPreview({
  source,
  fallbackContent,
}: {
  source: NonNullable<Props["originalPreview"]>;
  fallbackContent: string;
}) {
  if (!supportsNativePreview(source.fileName, source.mediaType)) {
    return (
      <div className="original-preview-fallback">
        <div className="original-preview-empty">
          <strong>浏览器无法直接还原此格式的原始排版</strong>
          <span>原文件仍保存在本地，可打开或下载查看。</span>
          <a href={source.url} target="_blank" rel="noreferrer">
            打开原始文件
          </a>
        </div>
        <div className="fallback-content-label">索引文本预览</div>
        <pre>{fallbackContent || "暂时没有可显示的索引文本。"}</pre>
      </div>
    );
  }

  if (source.mediaType.startsWith("image/")) {
    return (
      <div className="original-image-wrap">
        <img src={source.url} alt={source.fileName} />
      </div>
    );
  }

  const isHtml =
    source.mediaType === "text/html" ||
    source.mediaType === "application/xhtml+xml" ||
    /\.html?$/.test(source.fileName.toLowerCase());
  return (
    <iframe
      className="original-preview-frame"
      title={`原始预览：${source.fileName}`}
      src={source.mediaType === "application/pdf" ? `${source.url}#navpanes=0&view=FitH` : source.url}
      sandbox={isHtml ? "" : undefined}
    />
  );
}

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

export function GraphPanel({
  documentId,
  graphEnabled,
  viewMode,
  onViewMode,
  originalPreview,
  citation,
}: Props) {
  const [content, setContent] = useState("");
  const [graph, setGraph] = useState<{
    status: string;
    nodes: { label: string }[];
    edges: { relation: string; quote: string }[];
  } | null>(null);
  const currentDocument = useRef(documentId);
  currentDocument.current = documentId;
  const loadGraph = (id: string) => {
    void api
      .getGraph(id)
      .then((next) => {
        if (currentDocument.current === id) setGraph(next);
      })
      .catch(() => {
        if (currentDocument.current === id) setGraph(null);
      });
  };
  useEffect(() => {
    setContent("");
    setGraph(null);
    if (!documentId) return;
    const id = documentId;
    let cancelled = false;
    void api
      .getContent(id)
      .then((data) => {
        if (!cancelled) setContent(data.content);
      })
      .catch(() => {
        if (!cancelled) setContent("无法读取当前版本");
      });
    if (graphEnabled) loadGraph(id);
    return () => {
      cancelled = true;
    };
  }, [documentId, graphEnabled]);
  const rebuildGraph = async () => {
    if (!documentId) return;
    const id = documentId;
    await api.rebuildGraph(id);
    if (currentDocument.current === id) loadGraph(id);
  };
  const effectiveView = graphEnabled ? viewMode : "document";
  return (
    <aside className="inspector">
      <div className="inspector-head">
        <div>
          <div className="section-kicker">阅读台</div>
          <h2>{documentId ? "当前资料" : "等待选择"}</h2>
        </div>
        <div className="view-tabs">
          <button
            className={effectiveView === "document" ? "active" : ""}
            onClick={() => onViewMode("document")}
          >
            文档
          </button>
          {graphEnabled ? (
            <button
              className={effectiveView === "graph" ? "active" : ""}
              onClick={() => onViewMode("graph")}
            >
              图谱
            </button>
          ) : null}
        </div>
      </div>
      {citation ? (
        <div className="citation-card">
          <div className="citation-label">
            引用回读 · {citation.current_status}
          </div>
          <pre className="citation-quote">{citation.quote}</pre>
          <small>{formatCitationLocator(citation.locator)}</small>
        </div>
      ) : null}
      {effectiveView === "document" ? (
        originalPreview ? (
          <OriginalDocumentPreview
            source={originalPreview}
            fallbackContent={content}
          />
        ) : (
          <pre className="document-preview">
            {content || "选择一份资料，查看已索引的原文。"}
          </pre>
        )
      ) : (
        <div className="graph-preview">
          <div className="graph-status">
            <span className="status-pip" />
            {graph?.status ?? "未建立"}
            {documentId && graph?.status !== "ready" ? (
              <button
                className="graph-build"
                onClick={() => void rebuildGraph()}
              >
                建立图谱
              </button>
            ) : null}
          </div>
          {graph?.nodes.map((node) => (
            <div className="graph-node" key={node.label}>
              {node.label}
            </div>
          ))}
          {graph?.edges.map((edge, index) => (
            <div className="graph-edge" key={`${edge.relation}-${index}`}>
              <span>↳ {edge.relation}</span>
              <small>{edge.quote}</small>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
