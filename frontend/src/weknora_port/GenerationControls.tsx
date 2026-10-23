import { useEffect, useState } from 'react';
import { portApi } from './api';
import type { GenerationPreferences } from './types';

export const DEFAULT_GENERATION: GenerationPreferences = { reasoning_mode: 'quick', max_output_tokens: 0 };

export function GenerationControls({ value, onChange, disabled, effective, label = '回答设置' }: {
  value: GenerationPreferences; onChange: (value: GenerationPreferences) => void;
  disabled: boolean; effective?: GenerationPreferences | null; label?: string;
}) {
  return <fieldset className="wp-generation-controls" disabled={disabled}>
    <legend>{label}</legend>
    <div className="wp-generation-fields">
      <label>推理模式<select aria-label={`${label}推理模式`} value={value.reasoning_mode}
        onChange={event => onChange({ ...value, reasoning_mode: event.target.value as GenerationPreferences['reasoning_mode'] })}>
        <option value="quick">Quick · 关闭思考</option><option value="smart">Smart · 开启思考</option>
      </select></label>
      <label>最大输出 Token<input aria-label={`${label}最大输出 Token`} type="number" min="0" step="1"
        value={value.max_output_tokens} onChange={event => {
          const maximum = event.target.valueAsNumber;
          if (Number.isSafeInteger(maximum) && maximum >= 0) onChange({ ...value, max_output_tokens: maximum });
        }}/></label>
    </div>
    <small>0 使用服务端默认有限额度；Token 不是字数。部分模型的思考与正文共享额度。Smart 仅开启最终生成的思考，模型角色仍由服务端路由。</small>
    <p className="wp-effective-settings">{effective ? `当前有效值：${effective.reasoning_mode === 'smart' ? 'Smart' : 'Quick'} · ${effective.max_output_tokens} Token` : `待发送：${value.reasoning_mode === 'smart' ? 'Smart' : 'Quick'} · ${value.max_output_tokens || '服务端默认'} Token`}</p>
  </fieldset>;
}

export function ImageGenerationSettings({ kbId, disabled }: { kbId: string; disabled: boolean }) {
  const [value, setValue] = useState<GenerationPreferences>(DEFAULT_GENERATION);
  const [effective, setEffective] = useState<GenerationPreferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    let stale = false;
    setLoading(true); setError(''); setNotice('');
    void portApi.imageProcessing(kbId).then(row => {
      if (stale) return;
      const cfg = row.image_processing_config;
      setValue({ reasoning_mode: cfg.reasoning_mode ?? 'quick', max_output_tokens: cfg.max_output_tokens ?? 0 });
      setEffective(row.effective_generation ?? null);
    }).catch(error => { if (!stale) setError(error instanceof Error ? error.message : '图片设置读取失败'); })
      .finally(() => { if (!stale) setLoading(false); });
    return () => { stale = true; };
  }, [kbId]);
  const save = async (reset = false) => {
    setSaving(true); setError('');
    try {
      const next = reset ? DEFAULT_GENERATION : value;
      await portApi.saveImageProcessing(kbId, next);
      const row = await portApi.imageProcessing(kbId);
      setValue(next); setEffective(row.effective_generation ?? null);
      setNotice('图片设置已保存，仅用于新的 VLM / Caption 处理尝试。');
    } catch (error) { setError(error instanceof Error ? error.message : '图片设置保存失败'); }
    finally { setSaving(false); }
  };
  return <section className="wp-image-generation" aria-label="图片处理设置">
    <GenerationControls label="图片处理" value={value} effective={effective} disabled={disabled || loading || saving}
      onChange={next => { setValue(next); setEffective(null); }}/>
    <p>独立保存本知识库的 VLM / Caption 配置；本地 MinerU OCR 继续使用其当前配置。</p>
    <button type="button" disabled={disabled || loading || saving || Boolean(error)} onClick={() => void save()}>保存图片设置</button>
    <button type="button" disabled={disabled || loading || saving || Boolean(error)} onClick={() => void save(true)}>恢复图片默认</button>
    {error && <p role="alert" className="wp-inline-error">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>;
}
