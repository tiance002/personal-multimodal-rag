import type { Conversation } from "../app/state";

type Props = {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
};

export function Sidebar({ conversations, activeId, onSelect, onNew }: Props) {
  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="brand-lockup"><span className="brand-mark">✦</span><div><strong>索引台</strong><small>个人知识工作室</small></div></div>
      <nav className="primary-nav"><button className="nav-item active"><span>⌂</span>问答台</button><button className="nav-item"><span>◌</span>资料库</button><button className="nav-item"><span>⌁</span>系统设置</button></nav>
      <div className="history-head"><span>历史会话</span><button className="icon-button" aria-label="新建会话" onClick={onNew}>＋</button></div>
      <div className="conversation-list">
        {conversations.length === 0 ? <p className="empty-note">还没有会话。<br />从右侧资料开始提问。</p> : conversations.map((conversation) => <button key={conversation.id} onClick={() => onSelect(conversation.id)} className={`conversation-item ${activeId === conversation.id ? "selected" : ""}`}><span className="conversation-dot" />{conversation.title}</button>)}
      </div>
      <div className="sidebar-footer"><span className="status-pip" />本地模式 · 云端关闭</div>
    </aside>
  );
}
