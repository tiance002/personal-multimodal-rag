"""Fixed-source document summary/profile adapter, separate from P5 Chat routing.

Text path: knowledge_process.go:getSummary, sampleLongContent,
parseDocumentSummaryOutput, ProcessSummaryGeneration; KnowledgeProfile.Normalize.
Explicit source-compatible generated summary indexing approved 2026-10-13.
No model identity or fabricated completion when an adapter is unavailable.
"""
from __future__ import annotations
import json,re,uuid,hashlib
from pathlib import Path
from .documents import DocumentLifecycleError
from .models.types import CallContext,TrustedScope,Message,ChatOptions
from .retrieval.query import go_strip,go_fields,go_lower,_UNICODE_P,_category_contains
from .retrieval.merge import append_overlap
from .conversation.unicode15 import WHITE_SPACE,contains
from .storage.knowledge import ScopedKnowledgeRepository,custom_metadata_text
from .storage.chunks import StoredChunk,ChunkPlan
from .storage.indexing import IndexInfo,HybridIndexer

PROMPT_PATH=Path(__file__).with_name('profile_assets')/'generate_summary.yaml'

def sample_long_content(content,limit):
    if len(content)<=limit:return content
    marker='\n\n[...content omitted...]\n\n';usable=limit-2*len(marker)
    if usable<100:return content[:limit]
    head=usable*60//100;tail=usable*20//100;middle=usable-head-tail
    start=max(len(content)//2-middle//2,head);end=start+middle
    if end>len(content)-tail:
        end=len(content)-tail;start=max(end-middle,head)
    return content[:head]+marker+content[start:end]+marker+content[len(content)-tail:]

def real_text(content):
    content=re.sub(r'(?is)<image_original\b[^>]*>.*?</image_original>','',content)
    content=re.sub(r'!\[[^\]]*\]\([^)]*\)','',content)
    content=re.sub(r'(?i)<img\b[^>]*/?>','',content)
    return go_strip(re.sub(r'(?i)</?image[a-z_]*\b[^>]*/?>','',content))

def normalize_profile(raw):
    output={}
    for name,limit in [('gist',120),('doc_type',40),('typical_question',160)]:
        value=go_strip(' '.join(go_fields(raw.get(name,'')))[:limit])
        if value:output[name]=value
    topics=[];seen=set()
    for item in raw.get('topics') or []:
        display=go_strip(' '.join(go_fields(item))[:40]);key=go_lower(display)
        while key and (_category_contains(_UNICODE_P,key[0]) or contains(WHITE_SPACE,key[0])):key=key[1:]
        while key and (_category_contains(_UNICODE_P,key[-1]) or contains(WHITE_SPACE,key[-1])):key=key[:-1]
        if key and key not in seen:
            topics.append(display);seen.add(key)
        if len(topics)>=5:break
    if topics:output['topics']=topics
    return output or None

def parse_summary(content):
    content=go_strip(content)
    try:
        clean=content
        if clean.startswith('```json'):clean=clean[7:]
        elif clean.startswith('```'):clean=clean[3:]
        if clean.endswith('```'):clean=clean[:-3]
        raw=json.loads(clean)
        if not isinstance(raw,dict):raise ValueError()
        if any(name in raw and raw[name] is not None and not isinstance(raw[name],str)
               for name in ('summary','gist','doc_type','typical_question')):raise ValueError()
        if raw.get('topics') is not None and (not isinstance(raw['topics'],list) or
                any(not isinstance(v,str) for v in raw['topics'])):raise ValueError()
        raw={k:v for k,v in raw.items() if v is not None}
        profile=normalize_profile(raw)
        summary=go_strip(raw.get('summary','')) or (profile or {}).get('gist','')
        return (summary,profile) if summary else (content,None)
    except (ValueError,TypeError):return content,None

def reconstruct(rows):
    edited=any(getattr(c,'content_revision',0)>0 for c in rows)
    ordered=sorted(rows,key=lambda c:(c.chunk_index,c.id) if edited else (c.start_at,c.chunk_index))
    if edited:return '\n\n'.join(c.content for c in ordered if getattr(c,'is_enabled',True) and go_strip(c.content))
    result='';end=-1
    for row in ordered:
        if not row.content:continue
        if not result:result=row.content;end=row.end_at if row.end_at>0 else end
        elif row.start_at>end or row.end_at==0:
            result+=row.content
            if row.end_at>0:end=row.end_at
        elif row.end_at>end:
            result=append_overlap(result,row.content,end-row.start_at);end=row.end_at
    return result

class SourceDocumentPostprocessor:
    def __init__(self,application,*,prompt_path=PROMPT_PATH,language='English'):
        self.app=application;self.prompt_path=Path(prompt_path);self.language=language

    async def __call__(self,principal,kb,document,ingest,chunks):
        app=self.app;app._owner(principal)
        if kb.get('tenant_id')!=principal.tenant_id or document['knowledge_base_id']!=kb['id']:
            raise DocumentLifecycleError('PROFILE_SCOPE_MISMATCH')
        if any(c.scope.tenant_id!=principal.tenant_id or c.scope.owner_id!=principal.subject_id or
               c.scope.knowledge_base_id!=kb['id'] or c.scope.knowledge_id!=document['id'] or
               c.scope.source_version!=document['source_version'] for c in chunks):
            raise DocumentLifecycleError('PROFILE_CHUNK_SCOPE_MISMATCH')
        text=[c for c in chunks if c.chunk_type=='text']
        if not text:return True
        # Unsupported configured fan-out is retained pending, never silently skipped.
        strategy=kb.get('indexing_strategy') or {}
        if isinstance(strategy,str):strategy=json.loads(strategy)
        if strategy.get('graph_enabled') or strategy.get('wiki_enabled') or (kb.get('question_generation_config') or {}).get('enabled'):
            return False
        if not kb.get('summary_model_id') or not self.prompt_path.is_file():return False
        content=reconstruct(text)
        if any(c.chunk_type in ('image_ocr','image_caption') for c in chunks):
            return False  # image enrichment binding not yet equivalent
        settings=app.conversation.summary
        limit=settings.get('max_input_chars',8192)
        limit=limit if isinstance(limit,int) and limit>0 else 8192
        content=sample_long_content(content,limit)
        if len(real_text(content))<10:
            await app.documents._rows('UPDATE knowledges SET description=%s,profile=NULL,summary_status=%s '
                'WHERE tenant_id=%s AND knowledge_base_id=%s AND id=%s AND deleted_at IS NULL RETURNING id',
                ('','failed',principal.tenant_id,kb['id'],document['id']),
                writable_knowledge_base_ids=frozenset({kb['id']}))
            return True
        import yaml
        prompt=yaml.safe_load(self.prompt_path.read_text(encoding='utf-8'))['templates'][0]['content']
        prompt=prompt.replace('{{language}}',self.language)
        # Only explicit user metadata enters the model; internal ingestion state excluded.
        async with app.leases.lease() as guard:
            rows=await (await guard.connection.execute('SELECT custom_metadata,metadata FROM knowledges '
                'WHERE tenant_id=%s AND knowledge_base_id=%s AND id=%s AND deleted_at IS NULL',
                (principal.tenant_id,kb['id'],document['id']))).fetchall()
        if len(rows)!=1:raise DocumentLifecycleError('PROFILE_DOCUMENT_SCOPE_MISMATCH')
        custom=custom_metadata_text(rows[0]['custom_metadata'])
        if custom:content=sample_long_content('Document metadata:\n'+custom+'\n\nDocument content:\n'+content,limit)
        context=CallContext(document['source_version']+'/document-summary',
            TrustedScope(principal.subject_id,(kb['id'],),(document['id'],)))
        registry=app.registry_for_context()
        try:
            model=await registry.chat(kb['summary_model_id'],context)
            response=await model.chat((Message('system',prompt),Message('user',content)),
                ChatOptions(temperature=.3,max_tokens=settings.get('max_completion_tokens') or 1024,thinking=False),context)
            if response is None or not go_strip(response.content):return False
            summary,profile=parse_summary(response.content)
            content='# Summary\n'+summary
            gist=(profile or {}).get('gist','')
            if gist and go_lower(gist)!=go_lower(go_strip(summary)):content='# Summary\n'+gist+'\n\n'+summary
            scope=text[0].scope
            summary_id=str(uuid.uuid5(uuid.NAMESPACE_URL,scope.source_version+'/summary'))
            row=StoredChunk(summary_id,scope,content,max(c.chunk_index for c in chunks)+1,0,0,'summary',content,
                parent_chunk_id=sorted(text,key=lambda c:c.chunk_index)[0].id,
                metadata={'origin':'model_generated_summary','upstream_parent_link':True})
            index=IndexInfo(summary_id,0,summary_id,document['id'],kb['id'],content,is_enabled=False)
            embedder=await app.documents.embedder_for_kb(principal,kb) if strategy.get('vector_enabled') else None
            async with app.leases.lease(writable_knowledge_base_ids=frozenset({kb['id']})) as guard:
                async with guard.connection.transaction():
                    current=await (await guard.connection.execute('SELECT metadata,custom_metadata FROM knowledges WHERE tenant_id=%s '
                        'AND knowledge_base_id=%s AND id=%s AND deleted_at IS NULL FOR UPDATE',
                        (principal.tenant_id,kb['id'],document['id']))).fetchall()
                    if len(current)!=1 or current[0]['metadata'].get('source_version')!=document['source_version']:
                        raise DocumentLifecycleError('PROFILE_SOURCE_VERSION_CHANGED')
                    if current[0]['custom_metadata']!=rows[0]['custom_metadata']:
                        raise DocumentLifecycleError('PROFILE_CUSTOM_METADATA_CHANGED')
                    actual=await (await guard.connection.execute('SELECT id,content,content_revision FROM chunks '
                        'WHERE tenant_id=%s AND knowledge_base_id=%s AND knowledge_id=%s AND chunk_type=%s '
                        'AND deleted_at IS NULL',(principal.tenant_id,kb['id'],document['id'],'text'))).fetchall()
                    if {r['id']:r['content'] for r in actual}!={r.id:r.content for r in text} or any(r['content_revision'] for r in actual):
                        raise DocumentLifecycleError('PROFILE_CHUNK_REVISION_CHANGED')
                    async def resolve_tenant(owner):
                        if owner!=principal.subject_id:raise DocumentLifecycleError('PROFILE_SCOPE_MISMATCH')
                        return principal.tenant_id
                    existing=await (await guard.connection.execute('SELECT id,tenant_id,knowledge_base_id,knowledge_id,content FROM chunks WHERE id=%s',(summary_id,))).fetchall()
                    if existing and (existing[0]['tenant_id'],existing[0]['knowledge_base_id'],existing[0]['knowledge_id'],existing[0]['content'])!=(principal.tenant_id,kb['id'],document['id'],content):
                        raise DocumentLifecycleError('PROFILE_SUMMARY_IDENTITY_CONFLICT')
                    rag=bool(strategy.get('vector_enabled') or strategy.get('keyword_enabled'))
                    if not existing and rag:
                        await ScopedKnowledgeRepository(guard,resolve_tenant).create_chunks(ChunkPlan((row,),()),context)
                    if strategy.get('vector_enabled') or strategy.get('keyword_enabled'):
                        await HybridIndexer(guard).batch_index((index,),vector_enabled=strategy.get('vector_enabled',False),embedder=embedder)
                    await guard.connection.execute('UPDATE knowledges SET description=%s,profile=%s::jsonb,summary_status=%s,updated_at=now() '
                        'WHERE tenant_id=%s AND knowledge_base_id=%s AND id=%s AND deleted_at IS NULL',
                        (summary,json.dumps(profile), 'completed',principal.tenant_id,kb['id'],document['id']))
            return True
        finally:await registry.close()
