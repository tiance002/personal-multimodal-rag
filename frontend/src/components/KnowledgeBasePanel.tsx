import { useState } from "react";
import { Button, Input, Upload } from "antd";
import {
  DatabaseOutlined,
  FolderOpenOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import type { DocumentItem, IngestionJob, KnowledgeBase } from "../app/state";

type Props = {
  bases: KnowledgeBase[];
  selectedBaseId: string | null;
  documents: DocumentItem[];
  selectedDocumentId: string | null;
  uploadBusy: boolean;
  ingestionJob: (IngestionJob & { knowledgeBaseId: string }) | null;
  ingestionIssue: boolean;
  onCreateBase: (name: string) => Promise<boolean>;
  onSelectBase: (id: string | null) => void;
  onSelectDocument: (id: string) => void;
  onUpload: (files: File[]) => Promise<string>;
  onRetryIngestion: () => void;
  onRetryJob: (jobId: string) => void;
  onRefreshIngestion: () => void;
};

type FileGroup = "全部文件" | "PDF" | "文档" | "图片" | "其他";
const groups: FileGroup[] = ["全部文件", "PDF", "文档", "图片", "其他"];
function formatIngestionStage(stage: string) {
  if (stage === "queued") return "排队中";
  if (stage === "processing") return "解析中";
  if (stage === "indexing") return "建立索引";
  if (stage === "ready") return "已就绪";
  return stage;
}

function groupOf(document: DocumentItem): FileGroup {
  const name = document.file_name.toLowerCase();
  if (name.endsWith(".pdf")) return "PDF";
  if (/\.(png|jpe?g|webp|gif|bmp|tiff?)$/.test(name)) return "图片";
  if (/\.(md|txt|docx|xlsx|html?|csv)$/.test(name)) return "文档";
  return "其他";
}

function KnowledgeBaseCard({
  base,
  onOpen,
}: {
  base: KnowledgeBase;
  onOpen: () => void;
}) {
  return (
    <button type="button" className="kb-card" onClick={onOpen}>
      <span className="kb-card-icon">
        <DatabaseOutlined />
      </span>
      <strong>{base.name}</strong>
      <span>{base.description || "打开查看资料与分类"}</span>
      <span className="kb-card-open">查看资料 →</span>
    </button>
  );
}

function FileCategoryButton({
  group,
  count,
  selected,
  onClick,
}: {
  group: FileGroup;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={selected ? "selected" : ""}
      onClick={onClick}
    >
      <span>
        <FolderOpenOutlined /> {group}
      </span>
      <small>{count}</small>
    </button>
  );
}

function LibraryDocumentRow({
  document,
  selected,
  onSelect,
  onRetry,
}: {
  document: DocumentItem;
  selected: boolean;
  onSelect: () => void;
  onRetry: (jobId: string) => void;
}) {
  const latestDiffers =
    document.latest_version_no !== undefined &&
    document.latest_index_status !== undefined &&
    document.latest_index_status !== document.index_status;
  const job = document.latest_job;
  const canRetry = job?.status === "failed" && job.attempts < job.max_attempts;
  return (
    <div className={`document-row ${selected ? "selected" : ""}`}>
      <button type="button" className="document-select" onClick={onSelect}>
        <span className="file-glyph">
          {groupOf(document) === "PDF"
            ? "PDF"
            : groupOf(document) === "图片"
              ? "IMG"
              : "DOC"}
        </span>
        <span className="document-name">
          <strong>{document.file_name}</strong>
          <small>
            版本 {document.version_no ?? "—"}
            {latestDiffers &&
              ` · 新版本 ${document.latest_version_no} ${document.latest_index_status}`}
          </small>
        </span>
      </button>
      <span
        className={`file-status ${document.index_status === "ready" && !latestDiffers ? "ready" : ""}`}
      >
        {document.index_status === "ready" && !latestDiffers
          ? "已完成"
          : (document.latest_index_status ??
            document.index_status ??
            "等待索引")}
      </span>
      {canRetry && (
        <Button size="small" onClick={() => onRetry(job.id)}>
          重试索引
        </Button>
      )}
    </div>
  );
}

export function KnowledgeBasePanel({
  bases,
  selectedBaseId,
  documents,
  selectedDocumentId,
  uploadBusy,
  ingestionJob,
  ingestionIssue,
  onCreateBase,
  onSelectBase,
  onSelectDocument,
  onUpload,
  onRetryIngestion,
  onRetryJob,
  onRefreshIngestion,
}: Props) {
  const [creating, setCreating] = useState(false);
  const [baseName, setBaseName] = useState("");
  const [createPending, setCreatePending] = useState(false);
  const [filter, setFilter] = useState<FileGroup>("全部文件");
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [uploadHint, setUploadHint] = useState("");
  const selectedBase = bases.find((base) => base.id === selectedBaseId);
  const shownDocuments = documents.filter(
    (document) =>
      (filter === "全部文件" || groupOf(document) === filter) &&
      document.file_name.toLowerCase().includes(query.toLowerCase()),
  );
  const submitCreate = async () => {
    const name = baseName.trim();
    if (!name || createPending) return;
    setCreatePending(true);
    try {
      if (await onCreateBase(name)) {
        setBaseName("");
        setCreating(false);
      }
    } finally {
      setCreatePending(false);
    }
  };
  const receiveFiles = async (files: File[]) => {
    if (!selectedBaseId) {
      setUploadHint("请先打开一个知识库，再上传资料");
      return;
    }
    if (uploadBusy) {
      setUploadHint("当前知识库正在接收资料，请稍候");
      return;
    }
    if (!files.length) {
      setUploadHint("未找到可上传的文件");
      return;
    }
    setUploadHint(`正在接收 ${files.length} 份资料…`);
    try {
      setUploadHint(await onUpload(files));
    } catch (error) {
      setUploadHint(error instanceof Error ? error.message : "上传失败");
    }
  };
  const interceptUpload = (file: File, fileList: File[]) => {
    if (file === fileList[0]) void receiveFiles(fileList);
    return Upload.LIST_IGNORE;
  };
  const activeJob =
    ingestionJob?.knowledgeBaseId === selectedBaseId ? ingestionJob : null;

  return (
    <section
      className={`library-page ${selectedBase ? "library-page-detail" : ""}`}
    >
      <header className="library-page-header">
        <div>
          <div className="section-kicker">个人资料空间</div>
          <h1>{selectedBase ? selectedBase.name : "知识库"}</h1>
          <p>
            {selectedBase
              ? "浏览和管理已上传的资料。分类按文件类型自动整理。"
              : "为不同主题建立独立的资料空间。"}
          </p>
        </div>
        <div className="library-actions">
          {selectedBase && (
            <Input
              className="library-search"
              aria-label="搜索资料"
              value={queryDraft}
              prefix={<SearchOutlined />}
              allowClear
              onChange={(event) => {
                const next = event.target.value;
                setQueryDraft(next);
                if (!next.trim()) setQuery("");
              }}
              onPressEnter={() => setQuery(queryDraft.trim())}
              placeholder="搜索文件名，按回车筛选"
            />
          )}
          {selectedBase && (
            <Button
              onClick={() => {
                setFilter("全部文件");
                setQueryDraft("");
                setQuery("");
                onSelectBase(null);
              }}
            >
              返回知识库
            </Button>
          )}
          <Button onClick={() => setCreating((current) => !current)}>
            ＋ 新建知识库
          </Button>
          {selectedBase && (
            <Upload
              multiple
              showUploadList={false}
              accept=".pdf,.md,.markdown,.txt,.html,.htm,.docx,.xlsx,.csv,image/*"
              disabled={uploadBusy}
              beforeUpload={interceptUpload}
            >
              <Button type="primary" disabled={uploadBusy}>
                ＋ 添加文件
              </Button>
            </Upload>
          )}
        </div>
      </header>
      {(creating || bases.length === 0) && (
        <form
          className="create-base"
          onSubmit={(event) => {
            event.preventDefault();
            void submitCreate();
          }}
        >
          <label htmlFor="new-kb-name">知识库名称</label>
          <div className="create-base-controls">
            <Input
              id="new-kb-name"
              value={baseName}
              maxLength={200}
              onChange={(event) => setBaseName(event.target.value)}
              placeholder="例如：我的资料"
            />
            <Button
              htmlType="submit"
              type="primary"
              disabled={createPending || !baseName.trim()}
            >
              创建知识库
            </Button>
          </div>
        </form>
      )}
      {!selectedBase ? (
        <div className="kb-overview">
          <div className="overview-head">
            <strong>我的知识库</strong>
            <span>共 {bases.length} 个</span>
          </div>
          <div className="kb-card-grid">
            {bases.map((base) => (
              <KnowledgeBaseCard
                key={base.id}
                base={base}
                onOpen={() => {
                  setFilter("全部文件");
                  setQueryDraft("");
                  setQuery("");
                  onSelectBase(base.id);
                }}
              />
            ))}
          </div>
          {bases.length === 0 && (
            <p className="empty-document">
              创建第一个知识库后，即可上传资料并开始问答。
            </p>
          )}
        </div>
      ) : (
        <div className="library-detail">
          <aside className="file-categories" aria-label="资料分类">
            <div className="category-head">
              分类 <span>{documents.length} 个文件</span>
            </div>
            {groups.map((group) => {
              const count =
                group === "全部文件"
                  ? documents.length
                  : documents.filter((document) => groupOf(document) === group)
                      .length;
              return (
                <FileCategoryButton
                  key={group}
                  group={group}
                  count={count}
                  selected={filter === group}
                  onClick={() => setFilter(group)}
                />
              );
            })}
          </aside>
          <div className="files-main">
            <div className="files-toolbar">
              <div>
                <strong>{filter}</strong>
                <span>{shownDocuments.length} 个文件</span>
              </div>
              {query && <span className="search-applied">匹配：{query}</span>}
            </div>
            <Upload.Dragger
              className="dropzone"
              multiple
              showUploadList={false}
              accept=".pdf,.md,.markdown,.txt,.html,.htm,.docx,.xlsx,.csv,image/*"
              disabled={uploadBusy}
              beforeUpload={interceptUpload}
            >
              <span className="drop-icon">↥</span>
              <strong>
                {uploadBusy ? "资料正在接收中" : "拖入资料，或点击选择文件"}
              </strong>
              <span>支持 PDF、Markdown、TXT、HTML、DOCX、XLSX、CSV、图片</span>
            </Upload.Dragger>
            {uploadHint && (
              <p className="upload-hint" role="status">
                {uploadHint}
              </p>
            )}
            {activeJob && (
              <div className="ingestion-status" role="status">
                <span>
                  索引任务 · {formatIngestionStage(activeJob.stage)} · {activeJob.progress}% ·{" "}
                  {activeJob.status === "succeeded"
                    ? "已完成"
                    : activeJob.status === "failed"
                      ? "失败"
                      : "进行中"}
                </span>
                {activeJob.error_code && (
                  <strong>{activeJob.error_code}</strong>
                )}
                {activeJob.status === "failed" &&
                  activeJob.attempts < activeJob.max_attempts && (
                    <button type="button" onClick={onRetryIngestion}>
                      重试摄取
                    </button>
                  )}
                {ingestionIssue && (
                  <button type="button" onClick={onRefreshIngestion}>
                    刷新状态
                  </button>
                )}
              </div>
            )}
            <div className="file-table-head">
              <span>文件名</span>
              <span>索引状态</span>
            </div>
            <div className="document-list">
              {shownDocuments.length === 0 ? (
                <div className="empty-document">
                  {documents.length === 0
                    ? "还没有资料。拖入文件或点击添加文件开始。"
                    : "此分类下没有匹配的文件。"}
                </div>
              ) : (
                shownDocuments.map((document) => (
                  <LibraryDocumentRow
                    key={document.id}
                    document={document}
                    selected={selectedDocumentId === document.id}
                    onSelect={() => onSelectDocument(document.id)}
                    onRetry={onRetryJob}
                  />
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
