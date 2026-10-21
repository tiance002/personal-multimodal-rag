"""Bounded source image OCR/caption main-entry binding, independent of P5."""
from __future__ import annotations
import asyncio,base64,json,mimetypes,re,uuid
from pathlib import Path
from .documents import DocumentLifecycleError,_json_object
from .image_configuration import resolve_image_selection,validate_pinned_selection
from .ingestion.core import DocumentRequest
from .document_profile import language_name
from .conversation_configuration import resolve_language
from .models.types import CallContext,TrustedScope,Message,ProviderError
from .storage.configuration import ModelConfigurationError
from .retrieval.query import go_strip,go_lower
from .storage.chunks import StoredChunk,ChunkPlan
from .storage.indexing import IndexInfo

PROMPTS=json.loads((Path(__file__).with_name('profile_assets')/'vlm_ocr_prompts.json').read_text())


async def _parse_image_ocr(app,principal,kb,scope,asset):
    filename=Path(asset.path).name
    if not Path(filename).suffix:
        filename+=mimetypes.guess_extension(asset.media_type) or ''
    if not Path(filename).suffix:
        raise DocumentLifecycleError('IMAGE_MINERU_SOURCE_TYPE_UNSUPPORTED')
    parsers=getattr(app.documents,'parser_registry',None)
    if parsers is None:
        raise DocumentLifecycleError('IMAGE_MINERU_PARSER_REQUIRED')
    try:
        parsers.resolve('mineru-basic',filename)
    except Exception:
        raise DocumentLifecycleError('IMAGE_MINERU_PARSER_REQUIRED') from None
    request=DocumentRequest(filename=filename,data=asset.data,owner_id=principal.subject_id,
        knowledge_base_id=kb['id'],document_id=scope.knowledge_id,
        source_version=scope.source_version,parser_engine='mineru-basic')
    try:
        engine,parsed=await asyncio.to_thread(parsers.parse,request)
    except Exception as error:
        code=getattr(error,'code','')
        raise DocumentLifecycleError(code or 'IMAGE_MINERU_PARSE_FAILED') from None
    if engine!='mineru-basic':
        raise DocumentLifecycleError('IMAGE_MINERU_PARSER_MISMATCH')
    if parsed.failures:
        code=parsed.failures[0].code if parsed.failures else 'IMAGE_MINERU_PARSE_FAILED'
        raise DocumentLifecycleError(code or 'IMAGE_MINERU_PARSE_FAILED')
    if not parsed.content.strip():
        raise DocumentLifecycleError('IMAGE_OCR_EMPTY')
    metadata=dict(parsed.metadata)
    mineru_source=metadata.get('mineru_source')
    if not isinstance(mineru_source,dict):mineru_source={}
    source_map={'schema':'mineru-image-source-map-v1','parser_engine':engine,
        'parser_version':mineru_source.get('parser_version',''),
        'mineru_source':mineru_source,
        'source_locators':metadata.get('source_locators',[]),
        'mineru_blocks':metadata.get('mineru_blocks',[])}
    return parsed.content,source_map


def caption_prompt(language,instructions=''):
    prompt=f'Provide a brief and concise description of the main content of the image in {language}.'
    instructions=go_strip(instructions)
    if not instructions:return prompt
    return prompt+'\n\n<image_description_business_instructions>\n'+instructions+'\n</image_description_business_instructions>\nApply these business instructions only when they do not conflict with the system-owned output format, citation, safety, or factuality rules.'


def sanitize_ocr(raw):
    text=go_strip(raw)
    match=re.match(r'^[\t\n\f\r ]*```[a-zA-Z]*[\t\n\f\r ]*\n(.*?)\n[\t\n\f\r ]*```[\t\n\f\r ]*$',text,re.S)
    if match:text=go_strip(match[1])
    # HTML conversion is a separate source dependency: do not approximate it.
    tags=re.findall(r'<[^>]+>',text)
    plain=go_strip(re.sub(r'<[^>]+>','',text))
    if tags and len(plain.encode('utf-8'))<10:return ''
    if tags:
        # Source looksLikeHTML counts UTF-8 bytes, not Unicode code points.
        document=bool(re.match(r'^\s*(<!DOCTYPE|<html|<body|<div|<p[\s>]|<table|<h[1-6][\s>])',text,re.I|re.ASCII))
        dominant=sum(len(tag.encode('utf-8')) for tag in tags)/len(text.encode('utf-8'))>0.3
        if document or dominant:
            from .ingestion.html_conversion import convert_html,ConversionFailed
            try:text=go_strip(convert_html(text))
            except ConversionFailed:pass  # Fixed source ocrHTMLToMarkdown returns original on renderer conversion error.
            if not text:return ''
    from .ingestion.html_conversion import normalize_html_tables
    text=normalize_html_tables(text)
    empty={'无文字内容','无法识别','no text','no text content','no content','empty','图片中没有文字','图片中没有可识别文字'}
    if go_lower(go_strip(text)).rstrip('.!?。！？') in empty:return ''
    return go_strip(re.sub(r'\n{3,}','\n\n',text))


def enrich_summary_images(content,infos,full):
    lookup={}
    for info in infos:
        for key in ('url','original_url'):
            if info.get(key):lookup[info[key]]=info
    seen=set()
    def block(info):
        values=[]
        for key,tag in [('caption','image_caption'),('ocr_text','image_ocr')]:
            if key=='ocr_text' and not full:continue
            if info.get(key):values.append(f'<{tag}>{info[key]}</{tag}>')
        return '\n'.join(values)
    for match in list(re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)',content)):
        url=match[2];seen.add(url);value=block(lookup.get(url,{}))
        if value:content=content.replace(match[0],match[0]+'\n'+value,1)
    extras=[block(info) for info in infos if info.get('url') not in seen and info.get('original_url') not in seen and block(info)]
    if extras:content+=('\n' if content else '')+'\n'.join(extras)
    return content


class SourceImageProcessor:
    def __init__(self,application):self.app=application

    async def process_plan(self,principal,kb,scope,assets,plan,*,override=None,
                           selection_snapshot=None,on_before=None,on_result=None,prior=None):
        app=self.app;app._owner(principal)
        if scope.tenant_id!=principal.tenant_id or scope.owner_id!=principal.subject_id or scope.knowledge_base_id!=kb['id']:
            raise DocumentLifecycleError('IMAGE_CHUNK_SCOPE_MISMATCH')
        cfg=_json_object(kb.get('vlm_config'))
        image_cfg=_json_object(kb.get('image_processing_config'))
        if override:
            image_cfg={**image_cfg, **{k:v for k,v in override.items() if v not in (None,'')}}
        rows=await app.documents._rows('SELECT metadata,parse_status FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s AND id=%s AND deleted_at IS NULL',
            (principal.tenant_id,kb['id'],scope.knowledge_id))
        if len(rows)!=1 or rows[0]['parse_status']!='processing' or rows[0]['metadata'].get('source_version')!=scope.source_version:
            raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
        context=CallContext(scope.source_version+'/image/config',TrustedScope(principal.subject_id,(kb['id'],),(scope.knowledge_id,)))
        selection=(await validate_pinned_selection(selection_snapshot,app.models,app.catalogue,context)
                   if selection_snapshot is not None else
                   await resolve_image_selection(kb,override,app.models,app.catalogue,context))
        registry=app.registry_for_context();created=[];indexed=[]
        try:
            for index,asset in enumerate(assets):
                parent=next((chunk for chunk in plan.persisted if chunk.chunk_type=='text' and asset.path in chunk.content),None)
                if parent is None:raise DocumentLifecycleError('IMAGE_PARENT_CHUNK_NOT_FOUND')
                context=CallContext(scope.source_version+'/image/'+str(index),TrustedScope(principal.subject_id,(kb['id'],),(scope.knowledge_id,)))
                image='data:'+asset.media_type+';base64,'+base64.b64encode(asset.data).decode('ascii')
                ocr_prompt=PROMPTS['vlmOCRScannedPDFPrompt' if asset.source_type=='scanned_pdf' else 'vlmOCRPrompt']
                results={};states={};layout={}
                # Source buildVLMCaptionPrompt falls back to LanguageNameFromContext
                # (ResolveLanguage->DefaultLanguage), which never yields an empty
                # language; an empty interpolated value degrades the prompt to
                # "in ." and lets the model pick a language.
                language=go_strip(image_cfg.get('description_language') or cfg.get('description_language','')) or resolve_language(app.conversation.language_locale,'')
                for kind,prompt in [('ocr_text',ocr_prompt),('caption',caption_prompt(language,image_cfg.get('custom_instructions') or cfg.get('custom_instructions','')))]:
                    previous=(prior or {}).get(str(index),{})
                    prior_state=previous.get(kind,{})
                    if prior_state.get('status') in {'completed','failed','unavailable'}:
                        states[kind]=prior_state
                        results[kind]=previous.get('ocr_result' if kind=='ocr_text' else 'caption_result','')
                        if kind=='ocr_text':layout=previous.get('ocr_layout',{})
                        continue
                    binding=selection.ocr if kind=='ocr_text' else selection.caption
                    if binding is None:
                        states[kind]={'status':'unavailable','error':selection.caption_error}
                        continue
                    try:
                        if kind=='ocr_text':
                            if binding.adapter!='mineru':
                                raise DocumentLifecycleError('IMAGE_OCR_ADAPTER_RETIRED')
                            value,layout=await _parse_image_ocr(app,principal,kb,scope,asset)
                        else:
                            if binding.adapter!='vlm':
                                raise DocumentLifecycleError('IMAGE_CAPTION_ADAPTER_INVALID')
                            # The task pins identity; authorization and current
                            # model availability are checked again per send.
                            await app.models.resolve(binding.model_id)
                            model=await registry.get(binding.model_id,context)
                            if on_before is not None:await on_before(index,kind)
                            response=await model.vision((Message('user',prompt,images=(image,)),),None,context.child(kind))
                            value=response.content if response else ''
                            value=go_strip(value)
                        if not value:raise DocumentLifecycleError('IMAGE_'+('OCR' if kind=='ocr_text' else 'CAPTION')+'_EMPTY')
                        results[kind]=value
                        states[kind]={'status':'completed','error':'','binding':binding.public()}
                    except ProviderError as error:
                        # Source handles ordinary OCR/caption errors independently;
                        # permission/fee/identity denials remain fatal safety boundaries.
                        if error.sent or any(token in error.code for token in ('DENIED','AUTHORIZATION','PROVENANCE','BUDGET','IDENTITY','SCOPE','CANCELLED','DEADLINE')):raise
                        states[kind]={'status':'failed','error':error.code,'binding':binding.public()}
                    except DocumentLifecycleError as error:
                        states[kind]={'status':'failed','error':error.code,'binding':binding.public()}
                    except ModelConfigurationError as error:
                        # Revocation after task creation invalidates only this
                        # binding; an already valid sibling result is retained.
                        states[kind]={'status':'failed','error':str(error) or 'IMAGE_MODEL_CONFIGURATION_INVALID',
                                      'binding':binding.public()}
                info={'url':asset.path,'original_url':asset.path,'start_pos':0,'end_pos':0,
                    'page_number':asset.page_number,'source_type':asset.source_type,
                    'caption':results.get('caption',''),'ocr_text':results.get('ocr_text',''),
                    'ocr_layout':layout,'ocr_status':states.get('ocr_text',{}),
                    'caption_status':states.get('caption',{})}
                if on_result is not None:
                    await on_result(index,info)
                for field,chunk_type in [('ocr_text','image_ocr'),('caption','image_caption')]:
                    if not info[field]:continue
                    identifier=str(uuid.uuid4())
                    chunk=StoredChunk(identifier,scope,info[field],0,0,0,chunk_type,'',parent_chunk_id=parent.id,
                        metadata={'source_version':scope.source_version,'image_source_type':asset.source_type,
                                  'page_number':asset.page_number,'recommended':True},
                        image_info=json.dumps([info],ensure_ascii=False))
                    created.append(chunk);indexed.append(IndexInfo(identifier,0,identifier,scope.knowledge_id,kb['id'],info[field]))
            return ChunkPlan((*plan.persisted,*created),(*plan.indexed,*indexed))
        finally:await registry.close()
