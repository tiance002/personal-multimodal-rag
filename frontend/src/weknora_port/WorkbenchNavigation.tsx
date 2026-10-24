import { useEffect, useRef } from 'react';
import { Button, Empty, Menu, Tooltip } from 'antd';
import { BookOutlined, CommentOutlined, DatabaseOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons';
import type { SessionRow } from './types';

export type WorkbenchPage = 'chat' | 'library' | 'models' | 'memory';
const HISTORY_SCROLL_KEY = 'weknora-history-scroll';

export function WorkbenchNavigation({ page, sessions, activeId, disabled, onPage, onNew, onSession }: {
  page: WorkbenchPage; sessions: SessionRow[]; activeId?: string; disabled: boolean;
  onPage: (page: WorkbenchPage) => void; onNew: () => void; onSession: (session: SessionRow) => void;
}) {
  const history = useRef<HTMLDivElement>(null);
  const scrollPosition = useRef(0);
  useEffect(() => {
    try { scrollPosition.current = Number(sessionStorage.getItem(HISTORY_SCROLL_KEY)) || 0; } catch { /* Session storage is optional. */ }
  }, []);
  useEffect(() => {
    if (history.current) history.current.scrollTop = scrollPosition.current;
  }, [sessions.length]);
  return <>
    <div className="wp-brand"><span className="wp-brand-mark"><BookOutlined aria-hidden="true" /></span><span><strong>WeKnora</strong><small>个人知识工作台</small></span></div>
    <nav className="wp-nav" aria-label="主导航"><Menu mode="inline" selectedKeys={[page]} onClick={({ key }) => onPage(key as WorkbenchPage)} items={[
      { key: 'chat', icon: <CommentOutlined aria-hidden="true" />, label: '问答' },
      { key: 'library', icon: <DatabaseOutlined aria-hidden="true" />, label: '资料库' },
      { key: 'memory', icon: <BookOutlined aria-hidden="true" />, label: '长期记忆' },
      { key: 'models', icon: <SettingOutlined aria-hidden="true" />, label: '模型设置' },
    ]} /></nav>
    <Button className="wp-new-chat" icon={<PlusOutlined aria-hidden="true" />} disabled={disabled} onClick={onNew}>新对话</Button>
    <div className="wp-history-title"><span>历史会话</span><span>{sessions.length}</span></div>
    <div className="wp-history-list" ref={history} aria-label="历史会话" onScroll={event => {
      scrollPosition.current = event.currentTarget.scrollTop;
      try { sessionStorage.setItem(HISTORY_SCROLL_KEY, String(scrollPosition.current)); } catch { /* Session storage is optional. */ }
    }}>
      {sessions.map(session => <Tooltip key={session.id} title={session.title || '新对话'} placement="right" mouseEnterDelay={0.7}>
        <button type="button" title={session.title || '新对话'} aria-current={activeId === session.id ? 'page' : undefined}
          className={activeId === session.id ? 'selected' : ''} disabled={disabled} onClick={() => onSession(session)}>
          <strong>{session.title || '新对话'}</strong><small>{session.knowledge_base_scope.length} 个知识库{session.updated_at ? ` · ${session.updated_at.replace('T', ' ').slice(0, 16)}` : ''}</small>
        </button>
      </Tooltip>)}
      {!sessions.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有会话" />}
    </div>
    <div className="wp-sidebar-footer"><span className="wp-live-dot" />本地知识工作台</div>
  </>;
}
