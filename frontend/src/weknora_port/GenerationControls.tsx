import { useEffect, useRef, useState } from 'react';
import { Alert, Button, InputNumber, Tooltip } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';
import { portApi } from './api';
import type { GenerationCapabilities, GenerationPreferences } from './types';

export const DEFAULT_GENERATION: GenerationPreferences = { reasoning_mode: 'quick', max_output_tokens: 0 };
const CAPABILITY_GUIDANCE: Record<string, string> = {
  MODEL_GENERATION_CAPACITY_UNKNOWN: '当前模型的容量信息尚未确认，请在模型设置中检查配置。',
  MODEL_OUTPUT_LIMIT_EXCEEDED: '运行输出额度超出当前模型配置，请调低额度或检查模型设置。',
  MODEL_ROLE_NOT_CONFIGURED: '路由所需的模型尚未配置，请检查模型设置。',
  CHAT_MODEL_CONFIGURATION_REQUIRED: '尚未选择回答模型，请检查模型设置。',
  CONFIGURED_CHAT_MODEL_UNAVAILABLE: '已选择的回答模型当前不可用，请检查模型设置。',
  MODEL_SMART_MODE_UNSUPPORTED: '当前模型无法开启 Smart 思考，请选择 Quick 或检查模型设置。',
  MODEL_SMART_MODE_UNKNOWN: '当前模型的 Smart 能力尚未核验，请在模型设置中明确选择已支持的模型。',
};

export function GenerationControls({ value, onChange, disabled, effective, capabilities, label = '回答设置' }: {
  value: GenerationPreferences; onChange: (value: GenerationPreferences) => void;
  disabled: boolean; effective?: GenerationPreferences | null; capabilities?: GenerationCapabilities | null; label?: string;
}) {
  return <fieldset className="wp-generation-controls" disabled={disabled}>
    <legend className="wp-sr-only">{label}</legend>
    <div className="wp-generation-fields">
      <label><span className="wp-sr-only">推理模式</span><select aria-label={`${label}推理模式`} value={value.reasoning_mode}
        onChange={event => onChange({ ...value, reasoning_mode: event.target.value as GenerationPreferences['reasoning_mode'] })}>
        <option value="quick">Quick · 关闭思考</option><option value="smart" disabled={capabilities?.thinking === 'unsupported'}>Smart · 开启思考</option>
      </select></label>
      <label className="wp-token-control"><span>输出 Token</span><InputNumber aria-label={`${label}最大输出 Token`} min={0} step={1} precision={0} controls={false} disabled={disabled}
        value={value.max_output_tokens} onChange={maximum => {
          if (typeof maximum === 'number' && Number.isSafeInteger(maximum) && maximum >= 0) onChange({ ...value, max_output_tokens: maximum });
        }}/></label>
      <Tooltip title="0 使用服务端默认有限额度；Token 不是字数。Quick / Smart 控制最终生成是否思考，模型仍由服务端路由。"><InfoCircleOutlined tabIndex={0} aria-label="回答额度说明" /></Tooltip>
    </div>
    <p className="wp-effective-settings">{effective ? `当前有效值：${effective.reasoning_mode === 'smart' ? 'Smart' : 'Quick'} · ${effective.max_output_tokens} Token` : `待发送：${value.reasoning_mode === 'smart' ? 'Smart' : 'Quick'} · ${value.max_output_tokens || '服务端默认'} Token`}</p>
    <details className="wp-generation-details"><summary>模型能力与额度说明</summary>
    <small>0 使用服务端默认有限额度；Token 不是字数。部分模型的思考与正文共享额度。Quick / Smart 仅切换最终生成的思考，不代表价格档位；模型角色仍由服务端路由。</small>
    <p className="wp-generation-capabilities">思考能力：{capabilities?.thinking === 'supported' ? '支持' : capabilities?.thinking === 'unsupported' ? '当前模型不支持 Smart' : '未知，尚未确认'}；运行输出额度：{capabilities?.operating_max_output_tokens ?? effective?.max_output_tokens ?? '待服务端确认'}{(capabilities?.operating_max_output_tokens ?? effective?.max_output_tokens) ? ' Token' : ''}；已认证模型输出上限：{capabilities?.certified_max_output_tokens ? `${capabilities.certified_max_output_tokens} Token` : '未知'}。运行额度不代表模型能力上限。</p>
    {capabilities?.selection_scope === 'configured-routing-roles'
      ? <p>服务端路由：{capabilities.routing_mode}；候选模型：{capabilities.candidate_models?.map(model => `${model.role} · ${model.provider} / ${model.model}`).join('；') || '尚未配置'}。实际角色由服务端为每次请求确定。</p>
      : capabilities?.selected_model && <p>当前模型：{capabilities.selected_model.provider} / {capabilities.selected_model.model}（{capabilities.selected_model.role}）</p>}
    {capabilities?.declared_max_output_tokens != null && <p>目录声明输出上限：{capabilities.declared_max_output_tokens} Token；此声明不代表当前部署已认证。</p>}
    {capabilities?.error_code && <p>{CAPABILITY_GUIDANCE[capabilities.error_code] || '当前模型配置需要检查，请打开模型设置。'}（{capabilities.error_code}）</p>}
    </details>
    {capabilities?.error_code && <Alert type="warning" showIcon title={CAPABILITY_GUIDANCE[capabilities.error_code] || '当前模型配置需要检查，请打开模型设置。'} />}
  </fieldset>;
}

export function ImageGenerationSettings({ kbId, disabled }: { kbId: string; disabled: boolean }) {
  const [value, setValue] = useState<GenerationPreferences>(DEFAULT_GENERATION);
  const [effective, setEffective] = useState<GenerationPreferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [capabilities, setCapabilities] = useState<GenerationCapabilities | null>(null);
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [notice, setNotice] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const scope = useRef(0);
  const savingScope = useRef<number | null>(null);
  const currentKb = useRef(kbId);
  currentKb.current = kbId;
  useEffect(() => {
    const ticket = ++scope.current;
    const current = () => scope.current === ticket && currentKb.current === kbId;
    savingScope.current = null;
    setLoading(true); setSaving(false); setLoadError(''); setSaveError(''); setNotice('');
    setValue(DEFAULT_GENERATION); setEffective(null); setCapabilities(null);
    void portApi.imageProcessing(kbId).then(row => {
      if (!current()) return;
      const cfg = row.image_processing_config;
      setValue({ reasoning_mode: cfg.reasoning_mode ?? 'quick', max_output_tokens: cfg.max_output_tokens ?? 0 });
      setEffective(row.effective_generation ?? null);
      setCapabilities(row.capabilities ?? null);
    }).catch(error => { if (current()) setLoadError(error instanceof Error ? error.message : '图片设置读取失败'); })
      .finally(() => { if (current()) setLoading(false); });
    return () => { scope.current++; };
  }, [kbId, loadAttempt]);
  const save = async (reset = false) => {
    const ticket = scope.current;
    if (disabled || loading || loadError || savingScope.current !== null) return;
    const current = () => scope.current === ticket && currentKb.current === kbId;
    savingScope.current = ticket;
    setSaving(true); setSaveError(''); setNotice('');
    try {
      const next = reset ? DEFAULT_GENERATION : value;
      await portApi.saveImageProcessing(kbId, next);
      if (!current()) return;
      const row = await portApi.imageProcessing(kbId);
      if (!current()) return;
      setValue(next); setEffective(row.effective_generation ?? null);
      setCapabilities(row.capabilities ?? null);
      setNotice('图片设置已保存，仅用于新的 VLM / Caption 处理尝试。');
    } catch (error) { if (current()) setSaveError(error instanceof Error ? error.message : '图片设置保存失败'); }
    finally { if (current()) { savingScope.current = null; setSaving(false); } }
  };
  return <section className="wp-image-generation" aria-label="图片处理设置">
    <GenerationControls label="图片处理" value={value} effective={effective} capabilities={capabilities} disabled={disabled || loading || saving || Boolean(loadError)}
      onChange={next => { setValue(next); setEffective(null); setSaveError(''); setNotice(''); }}/>
    <p>独立保存本知识库的 VLM / Caption 配置；本地 MinerU OCR 继续使用其当前配置。</p>
    <Button type="primary" loading={saving} disabled={disabled || loading || saving || Boolean(loadError)} onClick={() => void save()}>保存图片设置</Button>
    <Button disabled={disabled || loading || saving || Boolean(loadError)} onClick={() => void save(true)}>恢复图片默认</Button>
    {loadError && <Button disabled={disabled || loading || saving} onClick={() => setLoadAttempt(attempt => attempt + 1)}>重新读取图片设置</Button>}
    {(loadError || saveError) && <p role="alert" className="wp-inline-error">{loadError || saveError}</p>}{notice && <p role="status">{notice}</p>}
  </section>;
}
