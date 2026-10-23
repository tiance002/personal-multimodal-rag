import { useState } from 'react';
import { portApi } from './api';
import type { DocumentRow, ImageTaskStatus } from './types';

export function ImageTaskDetails({ document, disabled }: { document: DocumentRow; disabled: boolean }) {
  const [task, setTask] = useState<ImageTaskStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const refresh = async () => {
    setOpen(true); setBusy(true); setError('');
    try { setTask(await portApi.imageTask(document.knowledge_base_id, document.id)); }
    catch (error) { setError(error instanceof Error ? error.message : '图片状态读取失败'); }
    finally { setBusy(false); }
  };
  const retry = async (assetPath: string, sha: string) => {
    if (!task) return;
    setBusy(true); setError('');
    try {
      await portApi.retryImageCaption(document.knowledge_base_id, document.id, task.source_version, assetPath, sha);
      setTask(await portApi.imageTask(document.knowledge_base_id, document.id));
    } catch (error) { setError(error instanceof Error ? error.message : '重新处理失败'); }
    finally { setBusy(false); }
  };
  return <div className="wp-image-task-details">
    <button type="button" disabled={disabled || busy} onClick={() => open ? setOpen(false) : void refresh()}>图片处理状态</button>
    {open && <div aria-label={`${document.filename} 图片状态`}>
      {task && <><p>任务状态：{task.state}{task.error_code ? ` · ${task.error_code}` : ''}</p>
        {Object.entries(task.outcomes).map(([index, item]) => {
          const caption = item.caption;
          if (!caption) return null;
          const usage = caption.usage;
          const canRetry = caption.status === 'failed' && Boolean(item.asset_path && item.asset_sha256 && task.source_version)
            && !['pending', 'processing', 'retry', 'unknown'].includes(task.state);
          return <div key={index} className="wp-image-outcome">
            <strong>图片 {Number(index) + 1} · Caption {caption.completion_kind ?? caption.status}</strong>
            {caption.error && <p>{caption.error}{caption.finish_reason ? ` · finish_reason=${caption.finish_reason}` : ''}</p>}
            {caption.max_output_tokens !== undefined && <p>本次额度 {caption.max_output_tokens} Token</p>}
            {usage?.reasoning_tokens != null && <p>思考消耗 {usage.reasoning_tokens} Token{!item.caption_result ? '，正文尚未产生' : ''}</p>}
            {usage?.completion_tokens != null && <p>输出用量 {usage.completion_tokens} Token</p>}
            {item.caption_result && <details><summary>查看已返回文本</summary><p>{item.caption_result}</p></details>}
            {canRetry && <button type="button" disabled={disabled || busy} onClick={() => void retry(item.asset_path!, item.asset_sha256!)}>用已保存图片设置重新处理此 Caption</button>}
          </div>;
        })}
        <button type="button" disabled={busy} onClick={() => void refresh()}>刷新图片状态</button></>}
      {error && <p role="alert">{error}</p>}
    </div>}
  </div>;
}
