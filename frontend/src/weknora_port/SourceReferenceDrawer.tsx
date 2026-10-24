import { useEffect, useState } from 'react';
import { Alert, Button, Drawer, Spin, Tag } from 'antd';
import { ExportOutlined, FileSearchOutlined } from '@ant-design/icons';
import { portApi } from './api';
import type { KnowledgeReference, SourceChunk } from './types';

// Only structured server references identify sources. Private model handles never do.
export function sourceReferences(value: unknown, knowledgeBaseIds?: readonly string[], documentIds?: readonly string[]): KnowledgeReference[] {
  if (!Array.isArray(value)) return [];
  const references = new Map<string, KnowledgeReference>();
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    if (String(row.knowledge_source ?? '').toLowerCase() === 'web_search' || String(row.chunk_type ?? '').toLowerCase() === 'web_search') continue;
    const id = row.chunk_id ?? row.id;
    const documentId = row.document_id ?? row.knowledge_id;
    const kbId = row.knowledge_base_id;
    if (typeof id !== 'string' || !id.trim() || /^[cdkwe]\d+$/i.test(id) || /^https?:/i.test(id) ||
        typeof documentId !== 'string' || !documentId.trim() || typeof kbId !== 'string' || !kbId.trim()) continue;
    if (knowledgeBaseIds && !knowledgeBaseIds.includes(kbId)) continue;
    if (documentIds?.length && !documentIds.includes(documentId)) continue;
    const title = row.knowledge_filename || row.filename || row.knowledge_title;
    references.set(id, { chunk_id: id, document_id: documentId, knowledge_base_id: kbId, title: typeof title === 'string' ? title : '' });
  }
  return [...references.values()];
}

export function SourceReferenceDrawer({ reference, onClose }: { reference: KnowledgeReference | null; onClose: () => void }) {
  const [source, setSource] = useState<SourceChunk | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    setSource(null); setError('');
    if (!reference) { setLoading(false); return; }
    let current = true;
    setLoading(true);
    void portApi.source(reference.chunk_id).then(row => {
      if (row.chunk_id !== reference.chunk_id || row.document_id !== reference.document_id ||
          row.knowledge_base_id !== reference.knowledge_base_id || typeof row.text !== 'string') throw new Error('来源身份与当前引用不一致，无法展示证据。');
      if (current) setSource(row);
    }).catch(failure => {
      if (current) setError(failure instanceof Error ? failure.message : '来源读取失败，请确认文档仍可访问且当前版本有效。');
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [reference]);
  return <Drawer rootClassName="wp-source-drawer-root" title={<span><FileSearchOutlined /> 引用来源</span>} open={Boolean(reference)} onClose={onClose} size="large" destroyOnHidden>
    {loading ? <div className="wp-drawer-loading"><Spin description="正在核验并读取来源" /></div> : error ?
      <Alert type="error" showIcon title="暂时无法读取引用" description={error} /> : source && <div className="wp-source-details">
        <h2>{source.filename}</h2>
        <div className="wp-source-meta">{source.page_number != null && <Tag>第 {source.page_number} 页</Tag>}{source.source_version && <Tag>版本 {source.source_version}</Tag>}</div>
        <pre>{source.text}</pre>
        {reference && <Button icon={<ExportOutlined />} href={portApi.originalDocumentURL(reference.knowledge_base_id, reference.document_id)} target="_blank" rel="noreferrer">打开原始文件</Button>}
      </div>}
  </Drawer>;
}
