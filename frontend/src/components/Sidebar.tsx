import type { Conversation } from "../app/state";
import type { ReactNode } from "react";
import {
  DatabaseOutlined,
  MessageOutlined,
  PlusOutlined,
  SettingOutlined,
} from "@ant-design/icons";

type Page = "chat" | "library" | "settings";

type Props = {
  conversations: Conversation[];
  activeId: string | null;
  page: Page;
  onPage: (page: Page) => void;
  onSelect: (id: string) => void;
  onNew: () => void;
};

function SidebarNavItem({
  label,
  icon,
  active,
  onClick,
}: {
  label: string;
  icon: ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`nav-item ${active ? "active" : ""}`}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      onClick={onClick}
    >
      <span>{icon}</span>
      {label}
    </button>
  );
}

function HistoryConversation({
  conversation,
  selected,
  onClick,
}: {
  conversation: Conversation;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`conversation-item ${selected ? "selected" : ""}`}
      title={conversation.title}
    >
      <span className="conversation-dot" />
      <span className="conversation-title">{conversation.title}</span>
    </button>
  );
}

export function Sidebar({
  conversations,
  activeId,
  page,
  onPage,
  onSelect,
  onNew,
}: Props) {
  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="brand-lockup">
        <span className="brand-mark">✦</span>
        <div>
          <strong>索引台</strong>
          <small>个人知识工作室</small>
        </div>
      </div>
      <nav className="primary-nav" aria-label="页面">
        <SidebarNavItem
          label="问答台"
          icon={<MessageOutlined />}
          active={page === "chat"}
          onClick={() => onPage("chat")}
        />
        <SidebarNavItem
          label="资料库"
          icon={<DatabaseOutlined />}
          active={page === "library"}
          onClick={() => onPage("library")}
        />
        <SidebarNavItem
          label="系统设置"
          icon={<SettingOutlined />}
          active={page === "settings"}
          onClick={() => onPage("settings")}
        />
      </nav>
      <div className="history-head">
        <span>历史会话</span>
        <button className="icon-button" aria-label="新建会话" onClick={onNew}>
          <PlusOutlined />
        </button>
      </div>
      <div className="conversation-list">
        {conversations.length === 0 ? (
          <p className="empty-note">
            还没有会话。
            <br />
            选择知识库后开始提问。
          </p>
        ) : (
          conversations.map((conversation) => (
            <HistoryConversation
              key={conversation.id}
              conversation={conversation}
              selected={page === "chat" && activeId === conversation.id}
              onClick={() => onSelect(conversation.id)}
            />
          ))
        )}
      </div>
      <div className="sidebar-footer">
        <span className="status-pip" />
        本地模式 · 云端关闭
      </div>
    </aside>
  );
}
