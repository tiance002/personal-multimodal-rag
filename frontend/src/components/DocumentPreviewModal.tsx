import { useEffect, useRef } from "react";
import {
  CloseOutlined,
  DownloadOutlined,
  FileImageOutlined,
  FilePdfOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { api } from "../api/client";
import type { DocumentItem, ViewMode } from "../app/state";
import { GraphPanel } from "./GraphPanel";
import { useResizablePanelWidth } from "./useResizablePanelWidth";

type Props = {
  document: DocumentItem | null;
  open: boolean;
  graphEnabled: boolean;
  viewMode: ViewMode;
  onViewMode: (mode: ViewMode) => void;
  onClose: () => void;
};

function documentIcon(mediaType: string) {
  if (mediaType === "application/pdf") return <FilePdfOutlined />;
  if (mediaType.startsWith("image/")) return <FileImageOutlined />;
  return <FileTextOutlined />;
}

function needsOfficeConversion(fileName: string, mediaType: string) {
  const lowerName = fileName.toLowerCase();
  return (
    mediaType ===
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
    mediaType ===
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" ||
    /\.(docx?|xlsx?)$/.test(lowerName)
  );
}

export function DocumentPreviewModal({
  document: item,
  open,
  graphEnabled,
  viewMode,
  onViewMode,
  onClose,
}: Props) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const { width, handleProps } = useResizablePanelWidth({
    open,
    initialWidth: Math.round(window.innerWidth * 0.72),
    minWidth: 720,
  });
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    if (!open) return;
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("keydown", escape);
    };
  }, [open]);

  if (!open || !item) return null;
  const sourceUrl = api.documentSourceUrl(item.id);
  const previewUrl = api.documentPreviewUrl(item.id);
  const officePreview = needsOfficeConversion(item.file_name, item.media_type);
  return (
    <div
      className="document-preview-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="document-preview-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`预览 ${item.file_name}`}
        style={{ width }}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="document-preview-modal-head">
          <div className="document-preview-title">
            <span className="document-preview-icon">
              {documentIcon(item.media_type)}
            </span>
            <div>
              <strong title={item.file_name}>{item.file_name}</strong>
              <small>
                原始文件 · 版本 {item.version_no ?? item.latest_version_no ?? "—"}
              </small>
            </div>
          </div>
          <div className="document-preview-actions">
            <a
              className="document-preview-download"
              href={sourceUrl}
              target="_blank"
              rel="noreferrer"
              download={item.file_name}
              title="打开或下载原始文件"
            >
              <DownloadOutlined />
              <span>原文件</span>
            </a>
            <button
              type="button"
              className="document-preview-close"
              onClick={onClose}
              aria-label="关闭预览"
              title="关闭预览"
            >
              <CloseOutlined />
            </button>
          </div>
        </div>
        <div className="document-preview-modal-body">
          <GraphPanel
            documentId={item.id}
            graphEnabled={graphEnabled}
            viewMode={viewMode}
            onViewMode={onViewMode}
            originalPreview={{
              url: officePreview ? previewUrl : sourceUrl,
              fileName: item.file_name,
              mediaType: officePreview ? "application/pdf" : item.media_type,
            }}
            citation={null}
          />
        </div>
        <div
          className="document-preview-resize-handle"
          role="separator"
          aria-label="调整预览宽度"
          aria-orientation="vertical"
          {...handleProps}
        />
      </section>
    </div>
  );
}
