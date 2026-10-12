import { useEffect, useState } from "react";
import { Alert, Button, Input, Select } from "antd";
import { api, type ModelConfiguration, type ModelConfigurationRow } from "../api/client";

const roles = [
  ["embedding", "Embedding", "将资料和问题转换为检索向量"],
  ["rerank", "Rerank", "对检索结果重新排序"],
  ["chat_cheap", "Chat · cheap", "日常问答使用的模型"],
  ["chat_expensive", "Chat · expansive", "复杂问题使用的模型"],
  ["vision", "VLM", "读取图片与文档中的图像"],
];
const endpoints: Record<string, string> = { siliconflow: "https://api.siliconflow.cn/v1", deepseek: "https://api.deepseek.com" };

export function ModelSettingsPanel({ onSaved }: { onSaved: () => void }) {
  const [config, setConfig] = useState<ModelConfiguration | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true;
    void api.modelConfiguration().then(value => {
      if (!value || !Array.isArray(value.roles) || typeof value.revision !== "string") throw new Error("MODEL_CONFIGURATION_INVALID");
      if (active) setConfig(value);
    })
      .catch(() => { if (active) setError("无法读取模型配置，请检查本地服务连接。"); });
    return () => { active = false; };
  }, []);
  const update = (role: string, patch: Partial<ModelConfigurationRow>) => {
    setNotice("");
    setConfig(current => current && ({ ...current, roles: current.roles.map(row => row.role === role ? { ...row, ...patch } : row) }));
  };
  const save = async () => {
    if (!config) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const saved = await api.saveModelConfiguration(config);
      setConfig(saved); onSaved();
      const status = await api.models();
      const unavailable = Object.entries(status.roles ?? {}).filter(([role, item]) => role !== "chat" && item.status === "NOT_READY").map(([role]) => roles.find(([id]) => id === role)?.[1] ?? role);
      setNotice(unavailable.length ? `配置已保存在本机。${unavailable.join("、")} 尚不可用，系统尚未完成该模型的能力或用量配置。` : "模型配置已保存并生效。");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "保存失败，请重试。"); }
    finally { setBusy(false); }
  };
  return <section className="model-settings" aria-label="模型配置">
    <h2>模型配置</h2>
    <p>分别选择各个用途的模型。API Key 仅保存在本机，留空保留原密钥；更换提供商或网址时请重新填写。</p>
    {error && <Alert type="error" title={error} showIcon />}
    {notice && <Alert type="info" title={notice} showIcon />}
    {roles.map(([role, title, description]) => {
      const row = config?.roles.find(item => item.role === role);
      if (!row) return null;
      return <fieldset className="model-role-card" key={role} disabled={busy}>
        <legend>{title}</legend><p>{description}</p>
        <div className="model-fields">
          <label>模型提供商<Select aria-label={`${title} 模型提供商`} value={endpoints[row.provider] ? row.provider : "custom"}
            options={[{value:"siliconflow",label:"SiliconFlow"},{value:"deepseek",label:"DeepSeek"},{value:"custom",label:"其他提供商"}]}
            onChange={provider => update(role, {provider, base_url:endpoints[provider] ?? "", api_key:"", key_configured:false})} /></label>
          {!endpoints[row.provider] && <label>提供商名称<Input aria-label={`${title} 提供商名称`} value={row.provider === "custom" ? "" : row.provider} onChange={event => update(role, {provider:event.target.value})} /></label>}
          <label>模型名称<Input aria-label={`${title} 模型名称`} value={row.model} onChange={event => update(role, {model:event.target.value})} placeholder="填写提供商的完整模型名称" /></label>
          <label>接口网址<Input aria-label={`${title} 接口网址`} value={row.base_url} onChange={event => update(role, {base_url:event.target.value, api_key:"", key_configured:false})} placeholder="https://…/v1" /></label>
          <label>API Key<Input.Password aria-label={`${title} API Key`} autoComplete="new-password" value={row.api_key ?? ""} onChange={event => update(role, {api_key:event.target.value})} placeholder={row.key_configured ? "已设置 · 留空保留" : "填写 API Key"} /></label>
        </div>
      </fieldset>;
    })}
    <Button type="primary" loading={busy} disabled={!config} onClick={() => void save()}>保存模型配置</Button>
  </section>;
}
