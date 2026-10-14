import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { portApi, type MemoryItem } from "./api";

type Settings = Awaited<ReturnType<typeof portApi.memorySettings>>;
type Draft = { id?: string; kind: string; content: string; importance: number };
const kinds = ["profile", "preference", "fact", "task", "interest"];
export function MemoryPanel({ disabled }: { disabled: boolean }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<MemoryItem | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [clearSelected, setClearSelected] = useState(false);
  const refresh = useCallback(async () => {
    setBusy(true); setError("");
    try {
      const [nextSettings, nextItems] = await Promise.all([portApi.memorySettings(), portApi.memoryItems(offset)]);
      setSettings(nextSettings); setItems(nextItems);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "记忆读取失败"); }
    finally { setBusy(false); }
  }, [offset]);
  useEffect(() => { if (!disabled) void refresh(); }, [disabled, refresh]);
  const forget = async () => {
    if (!selected || disabled || busy) return;
    setBusy(true); setError("");
    try { await portApi.forgetMemory(selected.id); setSelected(null); await refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "遗忘失败"); setBusy(false); }
  };
  const toggle = async (enabled: boolean) => {
    const previous = settings;
    setBusy(true); setError("");
    if (previous) setSettings({ ...previous, user_enabled: enabled, effective: previous.workspace_enabled && enabled });
    try { setSettings(await portApi.updateMemorySettings(enabled)); }
    catch (failure) { setSettings(previous); setError(failure instanceof Error ? failure.message : "记忆设置更新失败"); }
    finally { setBusy(false); }
  };
  const review = async (item: MemoryItem, confirm: boolean) => {
    setBusy(true); setError("");
    try { if (confirm) await portApi.confirmMemory(item.id); else await portApi.rejectMemory(item.id); await refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "记忆审核失败"); setBusy(false); }
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft || disabled || busy || !draft.content.trim()) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (draft.id) await portApi.updateMemory(draft.id, draft.content, draft.importance);
      else await portApi.createMemory(draft.kind, draft.content, draft.importance);
      setDraft(null); setNotice("记忆已保存"); await refresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "记忆保存失败"); setBusy(false); }
  };
  const clear = async () => {
    if (!clearSelected || disabled || busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await portApi.clearMemory(); setClearSelected(false); setNotice("当前用户记忆已清空"); setOffset(0); await refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "记忆清空失败"); setBusy(false); }
  };
  const exportItems = async () => {
    if (disabled || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await portApi.exportMemory();
      const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json;charset=utf-8" }));
      const link = document.createElement("a"); link.href = url; link.download = "weknora-memories.json";
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(result.truncated ? `已导出 ${result.data.length} / ${result.total} 条，达到导出上限，文件包含截断标记。` : `已导出 ${result.data.length} 条记忆`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "记忆导出失败"); }
    finally { setBusy(false); }
  };
  return <section className="wp-page-content">
    <div className="wp-page-heading"><div><h1>长期记忆</h1><p>查看和维护当前用户的记忆。</p></div>
      <button disabled={disabled || busy} onClick={() => void refresh()}>刷新记忆</button></div>
    {settings && <p role="status">{settings.effective ? "记忆已启用" : "记忆未启用"} · {settings.write_mode === "auto" ? "自动提取" : "仅在明确要求时记录"} · {settings.item_count} / {settings.max_items} 条有效记忆</p>}
    <label className="wp-inline-form"><input type="checkbox" aria-label="启用我的长期记忆" checked={settings?.user_enabled ?? false} disabled={disabled || busy || !settings} onChange={event => void toggle(event.target.checked)} />启用我的长期记忆</label>
    <div className="wp-inline-form"><button disabled={disabled || busy || !!draft} onClick={() => setDraft({ kind: "fact", content: "", importance: 3 })}>新增记忆</button>
      <button disabled={disabled || busy} onClick={() => void exportItems()}>导出记忆</button>
      <button disabled={disabled || busy} onClick={() => setClearSelected(true)}>清空记忆</button></div>
    {error && <p className="wp-inline-error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">正在处理记忆…</p>}
    <div className="wp-panel">{items.map(item => <article className="wp-memory-item" key={item.id}>
      <strong>{item.topic || item.kind}</strong><p>{item.content}</p><small>{item.status} · {item.origin}</small>
      {item.status === "pending" && <><button disabled={disabled || busy} onClick={() => void review(item, true)} aria-label={`确认 ${item.topic || item.kind}`}>确认记忆</button><button disabled={disabled || busy} onClick={() => void review(item, false)} aria-label={`拒绝 ${item.topic || item.kind}`}>拒绝记忆</button></>}
      {(["active", "pending"].includes(item.status)) && <button disabled={disabled || busy || !!draft} onClick={() => setDraft({ id: item.id, kind: item.kind, content: item.content, importance: item.importance ?? 3 })} aria-label={`编辑 ${item.topic || item.kind}`}>编辑</button>}
      <button disabled={disabled || busy} onClick={() => setSelected(item)} aria-label={`遗忘 ${item.topic || item.kind}`}>遗忘</button>
    </article>)}{!busy && items.length === 0 && <p>此页没有记忆。</p>}</div>
    <div className="wp-inline-form"><button disabled={disabled || busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button>
      <span>第 {Math.floor(offset / 50) + 1} 页</span><button disabled={disabled || busy || items.length < 50} onClick={() => setOffset(offset + 50)}>下一页</button></div>
    {draft && <form className="wp-consent" role="dialog" aria-modal="true" aria-label={draft.id ? "编辑记忆" : "新增记忆"} onSubmit={event => void save(event)}>
      <label>类型<select aria-label="记忆类型" disabled={!!draft.id || busy} value={draft.kind} onChange={event => setDraft({ ...draft, kind: event.target.value })}>{kinds.map(kind => <option key={kind} value={kind}>{kind}</option>)}</select></label>
      <label>内容<textarea aria-label="记忆内容" autoFocus required disabled={busy} value={draft.content} onChange={event => setDraft({ ...draft, content: event.target.value })} /></label>
      <label>重要程度<input aria-label="记忆重要程度" type="number" min={1} max={5} step={1} required disabled={busy} value={draft.importance} onChange={event => setDraft({ ...draft, importance: Number(event.target.value) })} /></label>
      <button type="submit" disabled={disabled || busy || !draft.content.trim()}>保存记忆</button><button type="button" disabled={busy} onClick={() => setDraft(null)}>取消编辑</button>
    </form>}
    {selected && <div className="wp-consent" role="dialog" aria-modal="true" aria-label="确认遗忘记忆"><p>确认遗忘这条记忆？</p><p>{selected.content}</p>
      <button disabled={disabled || busy} onClick={() => void forget()}>确认遗忘</button><button disabled={busy} onClick={() => setSelected(null)}>保留记忆</button></div>}
    {clearSelected && <div className="wp-consent" role="dialog" aria-modal="true" aria-label="确认清空记忆"><p>确认清空当前用户的记忆？此操作会记录遗忘状态。</p>
      <button disabled={disabled || busy} onClick={() => void clear()}>确认清空</button><button disabled={busy} onClick={() => setClearSelected(false)}>取消清空</button></div>}
  </section>;
}
