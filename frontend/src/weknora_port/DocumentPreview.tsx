import { useEffect, useState } from 'react';
import { Alert, Button, Drawer, Empty, Spin, Tag } from 'antd';
import { ExportOutlined, FileTextOutlined } from '@ant-design/icons';
import { portApi } from './api';
import type { DocumentRow } from './types';

export function DocumentPreview({ document, onClose }: { document: DocumentRow | null; onClose: () => void }) {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const extension = document?.filename.split('.').pop()?.toLowerCase();
  const url = document ? portApi.originalDocumentURL(document.knowledge_base_id, document.id) : '';
  const plainText = ['txt', 'md', 'csv', 'json', 'log'].includes(extension ?? '');
  useEffect(() => {
    setText(''); setError('');
    if (!document || !plainText) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    void fetch(url, { credentials: 'same-origin', signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`原文件读取失败（HTTP ${response.status}）`);
      return response.text();
    }).then(value => { if (!controller.signal.aborted) setText(value.slice(0, 100_000)); })
      .catch(failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : '原文件读取失败'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [document?.id, plainText, url]);
  return <Drawer className="wp-document-preview" title={<span><FileTextOutlined /> 文档预览</span>} open={Boolean(document)} onClose={onClose} size="large" destroyOnHidden>
    {document && <>
      <div className="wp-preview-heading"><h2>{document.filename}</h2><Tag>{document.status}</Tag></div>
      <Button href={url} target="_blank" rel="noreferrer" icon={<ExportOutlined />}>打开原始文件</Button>
      <div className="wp-preview-content">
        {loading ? <Spin description="正在读取原文件" /> : error ? <Alert type="error" title={error} showIcon /> :
          plainText ? <pre>{text}</pre> : extension === 'pdf' ? <iframe title={`${document.filename} 原文件预览`} src={url} /> :
            ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'].includes(extension ?? '') ? <img src={url} alt={document.filename} /> :
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此文件类型请通过原始文件查看" />}
      </div>
      {plainText && text.length === 100_000 && <Alert type="info" title="预览显示前 100,000 个字符，完整内容请打开原始文件。" />}
      {extension === 'pdf' && <p className="wp-preview-help">若浏览器未显示 PDF，请打开原始文件查看。</p>}
    </>}
  </Drawer>;
}
