import { useState } from "react";
import { portApi } from "./api";
import type { KnowledgeBase } from "./types";

export function KnowledgeBaseConsent({ base, disabled, onUpdated }: {
  base?: KnowledgeBase; disabled: boolean; onUpdated: (base: KnowledgeBase) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const save = async (cloudAllowed: boolean) => {
    if (!base || saving || disabled) return;
    setSaving(true);
    setError("");
    try {
      const updated = await portApi.updateKnowledgeBaseConsent(base.id, cloudAllowed);
      if (updated.id !== base.id || typeof updated.cloud_allowed !== "boolean") throw new Error("知识库许可响应无效");
      onUpdated({ ...base, ...updated });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "外发许可保存失败");
    } finally { setSaving(false); }
  };
  if (!base) return null;
  return <fieldset disabled={disabled || saving} className="wp-consent">
    <legend>{base.name} · 隐私许可</legend>
    <label><input type="checkbox" role="switch" aria-label="允许当前知识库使用云端模型" checked={base.cloud_allowed}
      onChange={event => void save(event.target.checked)}/>允许使用云端模型</label>
    <p>开启后，问题、检索片段和图片可发送至已配置的云端模型。关闭后禁止这些内容外发。</p>
    <small>{saving ? "正在保存许可…" : base.cloud_allowed ? "当前允许外发" : "当前禁止外发"}</small>
    {error && <p className="wp-inline-error" role="alert">{error}</p>}
  </fieldset>;
}
