import { useEffect, useRef, useState } from "react";
import { api, type MemoryItem } from "../api/client";

export function MemoryPanel({ kbId, documentScope }: { kbId: string | null; documentScope: string[] }) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [settings, setSettings] = useState({ read_enabled: false, write_mode: "explicit_only", automatic_extraction: "MODEL_IDENTITY_NOT_CONFIGURED" });
  const [kind, setKind] = useState("preference");
  const [key, setKey] = useState("");
  const [content, setContent] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [available, setAvailable] = useState(false);
  const epoch = useRef(0);
  const [scopeType, setScopeType] = useState<'GLOBAL' | 'SCOPED'>('SCOPED');
  const scopeKey = JSON.stringify({ scopeType, documents: [...documentScope].sort() });
  const scopedBinding = { knowledge_base_scope: kbId ? [kbId] : [], document_scope: [...documentScope] };
  const scope = { scope_type: scopeType, ...(scopeType === 'GLOBAL' ? { knowledge_base_scope: [], document_scope: [] } : scopedBinding) };
  const canManage = scopeType === 'GLOBAL' || Boolean(kbId);
  useEffect(() => {
    const version = ++epoch.current;
    setItems([]); setEditing(null); setContent(""); setKey(""); setBusy(false);
    void api.memorySettings()
      .then(async config => {
        if (epoch.current !== version) return;
        const rows = canManage ? await api.memoryList(scope) : [];
        if (epoch.current === version) { setAvailable(true); setSettings(config); setItems(rows); setNotice(""); }
      })
      .catch((e: Error) => { if (epoch.current === version) setNotice(e.message); });
    return () => { ++epoch.current; };
  }, [kbId, scopeKey]);
  async function act(action: () => Promise<unknown>) {
    if (busy) return;
    const version = ++epoch.current;
    setBusy(true);
    try {
      await action();
      const rows = canManage ? await api.memoryList(scope) : [];
      if (version === epoch.current) { setItems(rows); setNotice("已保存"); setEditing(null); setContent(""); setKey(""); }
    } catch (e) { if (version === epoch.current) setNotice((e as Error).message); }
    finally { if (version === epoch.current) setBusy(false); }
  }
  if (!available || notice.includes("MEMORY_PRINCIPAL_REQUIRED")) return null;
  return <section aria-label="长期记忆管理">
    <h2>长期记忆</h2>
    <label>记忆范围 <select value={scopeType} onChange={(e) => { setScopeType(e.target.value as 'GLOBAL' | 'SCOPED'); setKind('preference'); }}>
      <option value="SCOPED">当前知识库/文档限定记忆</option><option value="GLOBAL">用户级全局记忆</option>
    </select></label>
    <p>记忆仅供理解偏好，不能替代资料引用。仅支持已配置的本机身份。</p>
    <p>自动提取：{settings.automatic_extraction === "MODEL_IDENTITY_NOT_CONFIGURED" ? "尚不可用（未配置可信提取模型，历史保留待后续处理）" : "真实模型准入尚未开放"}</p>
    <label><input type="checkbox" disabled={busy} checked={settings.read_enabled} onChange={(e) => {
      const next = { ...settings, read_enabled: e.target.checked };
      void act(async () => { await api.memoryUpdateSettings(next); setSettings(next); });
    }} />读取已确认记忆</label>
    <label>保存模式 <select disabled={busy} value={settings.write_mode} onChange={(e) => {
      const next = { ...settings, write_mode: e.target.value };
      void act(async () => { await api.memoryUpdateSettings(next); setSettings(next); });
    }}><option value="explicit_only">仅显式保存</option><option value="auto">自动候选（真实提取关闭）</option></select></label>
    <p>管理范围：{scopeType === 'GLOBAL' ? "本机受信用户的通用资料、偏好与兴趣（跨知识库）" : !kbId ? "请先选择知识库" : documentScope.length ? `当前会话限定的 ${documentScope.length} 份文档` : "当前知识库（未限定文档）"}</p>
    <p role="status">{notice}</p>
    <form onSubmit={(e) => { e.preventDefault(); if (!canManage) return; void act(() => api.memorySave({ ...scope, kind, fact_key: key, content }, editing)); }}>
      <label>分类 <select value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="profile">资料</option><option value="preference">偏好</option>{scopeType === 'SCOPED' && <><option value="fact">稳定事实</option><option value="task">事项</option></>}<option value="interest">兴趣</option>
      </select></label>
      <label>事实标识 <input required maxLength={120} value={key} disabled={Boolean(editing)} onChange={(e) => setKey(e.target.value)} /></label>
      <label>记忆内容 <textarea required maxLength={300} value={content} onChange={(e) => setContent(e.target.value)} /></label>
      <button disabled={busy || !canManage} type="submit">{editing ? "保存编辑" : "明确保存这条记忆"}</button>
    </form>
    <ul>{items.filter((r) => !["deleted", "superseded", "rejected"].includes(r.status)).map((row) => <li key={row.id}>
      <p>{row.content}</p><small>{row.kind} · {row.status} · {row.origin} · v{row.version} · 来源 {row.sources.length} 条</small>
      <details><summary>查看来源</summary><pre>{JSON.stringify(row.sources, null, 2)}</pre></details>
      <button disabled={busy} onClick={() => { setEditing(row.id); setKind(row.kind); setKey(row.fact_key); setContent(row.content); }}>编辑</button>
      {row.status === "pending" && <><button disabled={busy} onClick={() => void act(() => api.memoryTransition(row.id, "confirm", scope))}>确认</button>
        <button disabled={busy} onClick={() => void act(() => api.memoryTransition(row.id, "reject", scope))}>拒绝</button></>}
      <button disabled={busy} onClick={() => void act(() => api.memoryTransition(row.id, "delete", scope))}>删除</button>
    </li>)}</ul>
  </section>;
}
