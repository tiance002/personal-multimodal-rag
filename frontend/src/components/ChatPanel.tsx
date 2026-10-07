import { useState } from "react";
import { Button, Popover, Select, Upload } from "antd";
import {
  DatabaseOutlined,
  FileTextOutlined,
  PaperClipOutlined,
  SendOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { DocumentItem, KnowledgeBase, Message } from "../app/state";
import { clarificationText, evidenceHintText } from "../app/state";

type Props = {
  messages: Message[];
  bases: KnowledgeBase[];
  documents: DocumentItem[];
  selectedBaseId: string | null;
  documentScope: string[];
  scopeBusy: boolean;
  disabled: boolean;
  disabledHint?: string;
  uploading: boolean;
  onSelectBase: (id: string) => void;
  onToggleDocumentScope: (id: string) => void;
  onUpload: (files: File[]) => void;
  onSend: (content: string, mode: "quick" | "smart") => void;
  onCitation: (runId: string, citationId: string) => void;
  onOpenLibrary: () => void;
};

type ScopeOptionProps = {
  kind: "base" | "document";
  label: string;
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
};

function ScopeOption({
  kind,
  label,
  selected,
  disabled,
  onClick,
}: ScopeOptionProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      className={selected ? "selected" : ""}
      onClick={onClick}
    >
      {kind === "base" ? <DatabaseOutlined /> : <FileTextOutlined />}
      <span>{label}</span>
      {selected && <span>✓</span>}
    </button>
  );
}

export function ChatPanel({
  messages,
  bases,
  documents,
  selectedBaseId,
  documentScope,
  scopeBusy,
  disabled,
  disabledHint,
  uploading,
  onSelectBase,
  onToggleDocumentScope,
  onUpload,
  onSend,
  onCitation,
  onOpenLibrary,
}: Props) {
  const [value, setValue] = useState("");
  const [mode, setMode] = useState<"quick" | "smart">("quick");
  const [scopeOpen, setScopeOpen] = useState(false);
  const selectedBase = bases.find((base) => base.id === selectedBaseId);
  const selectedDocument = documents.find((document) =>
    documentScope.includes(document.id),
  );
  const submit = () => {
    const content = value.trim();
    if (!content || disabled) return;
    onSend(content, mode);
    setValue("");
  };
  const scopeContent = (
    <div className="scope-popover">
      <strong>选择问答范围</strong>
      <div className="scope-section-label">知识库 {bases.length}</div>
      <div className="scope-options">
        {bases.map((base) => (
          <ScopeOption
            key={base.id}
            kind="base"
            label={base.name}
            selected={base.id === selectedBaseId}
            disabled={scopeBusy}
            onClick={() => {
              onSelectBase(base.id);
              setScopeOpen(false);
            }}
          />
        ))}
        {bases.length === 0 && <p>还没有知识库，请先创建。</p>}
      </div>
      {selectedBaseId && (
        <>
          <div className="scope-section-label">文件 {documents.length}</div>
          <div className="scope-options">
            <ScopeOption
              kind="document"
              label="当前库的全部文件"
              selected={documentScope.length === 0}
              disabled={scopeBusy || documentScope.length === 0}
              onClick={() => {
                if (documentScope[0]) onToggleDocumentScope(documentScope[0]);
                setScopeOpen(false);
              }}
            />
            {documents.map((document) => (
              <ScopeOption
                key={document.id}
                kind="document"
                label={document.file_name}
                selected={documentScope.includes(document.id)}
                disabled={scopeBusy || document.index_status !== "ready"}
                onClick={() => {
                  if (!documentScope.includes(document.id))
                    onToggleDocumentScope(document.id);
                  setScopeOpen(false);
                }}
              />
            ))}
          </div>
        </>
      )}
      <button
        type="button"
        className="scope-manage"
        onClick={() => {
          setScopeOpen(false);
          onOpenLibrary();
        }}
      >
        管理知识库 →
      </button>
    </div>
  );

  return (
    <section
      className={`chat-panel ${messages.length ? "has-messages" : "is-empty"}`}
    >
      {messages.length > 0 && (
        <header className="chat-conversation-head">
          <strong>
            {messages
              .find((message) => message.role === "user")
              ?.content.slice(0, 48) ?? "新会话"}
          </strong>
          <span>{selectedBase?.name ?? "未选择知识库"}</span>
        </header>
      )}
      <div className="message-stream" aria-live="polite">
        {messages.length === 0 ? (
          <div className="chat-empty">
            <span className="welcome-symbol">✦</span>
            <h1>让资料，回答你的问题</h1>
            <p>选择一个知识库，开始一段有依据的对话。</p>
            <div className="suggestion-list">
              <button
                type="button"
                onClick={() => setValue("请概括当前知识库中的主要内容")}
              >
                概括资料中的主要内容
              </button>
              <button
                type="button"
                onClick={() => setValue("这份资料有哪些关键结论？请附上出处")}
              >
                找出关键结论和出处
              </button>
            </div>
          </div>
        ) : (
          messages.map((message, index) => (
            <article
              key={message.id ?? index}
              className={`message ${message.role}`}
            >
              <div className="message-role">
                {message.role === "user" ? "你" : "索引台"}
              </div>
              <div className="message-body">
                {message.role === "assistant" ? (
                  <div className="markdown-content">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {message.content}
                    </ReactMarkdown>
                  </div>
                ) : (
                  message.content
                )}
                {clarificationText(message) && (
                  <p className="clarification-prompt">{clarificationText(message)}</p>
                )}
                {evidenceHintText(message) && (
                  <p className="clarification-prompt">{evidenceHintText(message)}</p>
                )}
                {message.citations?.length ? (
                  <div className="citation-strip">
                    {message.citations.map((citation) => (
                      <button
                        key={citation}
                        type="button"
                        className="citation-pill"
                        onClick={() =>
                          message.run_id && onCitation(message.run_id, citation)
                        }
                      >
                        {citation}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            </article>
          ))
        )}
      </div>
      <div className="composer-wrap">
        <div className="composer">
          <textarea
            aria-label="输入问题"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            placeholder={
              disabledHint && disabledHint !== "先选择一个知识库"
                ? disabledHint
                : "输入问题或描述任务…"
            }
          />
          <div className="composer-toolbar">
            <div className="composer-tools">
              <Select
                className="mode-select"
                aria-label="回答模式"
                value={mode}
                options={[
                  { label: "快速问答", value: "quick" },
                  { label: "智能推理", value: "smart" },
                ]}
                onChange={(next) => setMode(next as "quick" | "smart")}
              />
              {selectedBaseId ? (
                <Upload
                  multiple
                  showUploadList={false}
                  disabled={uploading}
                  beforeUpload={(file, fileList) => {
                    if (file === fileList[0]) onUpload(fileList);
                    return Upload.LIST_IGNORE;
                  }}
                >
                  <Button
                    type="text"
                    className="tool-button"
                    aria-label="上传附件"
                    title="上传附件到当前知识库"
                    disabled={uploading}
                  >
                    <PaperClipOutlined />
                  </Button>
                </Upload>
              ) : (
                <Button
                  type="text"
                  className="tool-button"
                  aria-label="上传附件"
                  title="先选择知识库"
                  onClick={() => setScopeOpen(true)}
                >
                  <PaperClipOutlined />
                </Button>
              )}
              <Popover
                open={scopeOpen}
                onOpenChange={setScopeOpen}
                trigger="click"
                placement="topLeft"
                content={scopeContent}
              >
                <Button
                  type="text"
                  className="scope-trigger"
                  aria-label="选择知识库和文件"
                >
                  <DatabaseOutlined />
                  <span>选择资料</span>
                </Button>
              </Popover>
              <span className="composer-scope">
                {selectedDocument
                  ? `仅 ${selectedDocument.file_name}`
                  : (selectedBase?.name ?? "未选择知识库")}
              </span>
            </div>
            <Button
              type="primary"
              className="send-button"
              aria-label="发送问题"
              disabled={disabled || !value.trim()}
              onClick={submit}
            >
              <SendOutlined />
            </Button>
          </div>
        </div>
        <small className="composer-hint">
          {uploading
            ? "资料正在上传和索引…"
            : (disabledHint ?? "Enter 发送 · Shift + Enter 换行")}
        </small>
      </div>
    </section>
  );
}
