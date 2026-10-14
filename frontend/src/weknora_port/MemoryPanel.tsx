import { useCallback, useEffect, useState } from "react";
import { portApi, type MemoryItem } from "./api";

type Settings = Awaited<ReturnType<typeof portApi.memorySettings>>;
export function MemoryPanel({ disabled }: { disabled: boolean }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<MemoryItem | null>(null);
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
    if (!selected) return;
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
  return <section className="wp-page-content">
    <div className="wp-page-heading"><div><h1>长期记忆</h1><p>查看当前用户的记忆，或遗忘不再需要的内容。</p></div>
      <button disabled={disabled || busy} onClick={() => void refresh()}>刷新记忆</button></div>
    {settings && <p role="status">{settings.effective ? "记忆已启用" : "记忆未启用"} · {settings.write_mode === "auto" ? "自动提取" : "仅明确要求时记录"} · {settings.item_count} / {settings.max_items} 条有效记忆</p>}
    <label className="wp-inline-form"><input type="checkbox" aria-label="启用我的长期记忆" checked={settings?.user_enabled ?? false} disabled={disabled || busy || !settings} onChange={event => void toggle(event.target.checked)} />启用我的长期记忆</label>
    {error && <p className="wp-inline-error" role="alert">{error}</p>}
    {busy && <p role="status">正在处理记忆</p>}
    <div className="wp-panel">{items.map(item => <article className="wp-memory-item" key={item.id}>
      <strong>{item.topic || item.kind}</strong><p>{item.content}</p><small>{item.status} · {item.origin}</small>
      {item.status === "pending" && <><button disabled={disabled || busy} onClick={() => void review(item, true)} aria-label={`确认 ${item.topic || item.kind}`}>确认记忆</button><button disabled={disabled || busy} onClick={() => void review(item, false)} aria-label={`拒绝 ${item.topic || item.kind}`}>拒绝记忆</button></>}
      <button disabled={disabled || busy} onClick={() => setSelected(item)} aria-label={`遗忘 ${item.topic || item.kind}`}>遗忘</button>
    </article>)}{!busy && items.length === 0 && <p>本页没有记忆。</p>}</div>
    <div className="wp-inline-form"><button disabled={disabled || busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button>
      <span>第 {Math.floor(offset / 50) + 1} 页</span><button disabled={disabled || busy || items.length < 50} onClick={() => setOffset(offset + 50)}>下一页</button></div>
    {selected && <div className="wp-consent" role="dialog" aria-modal="true" aria-label="确认遗忘记忆"><p>确认遗忘这条记忆？</p><p>{selected.content}</p>
      <button disabled={busy} onClick={() => void forget()}>确认遗忘</button><button disabled={busy} onClick={() => setSelected(null)}>保留记忆</button></div>}
  </section>;
}
