"""Fixed source configuration resolution; no Agent/tool execution or model sends."""
from functools import lru_cache
from pathlib import Path
import hashlib,json
from collections.abc import Mapping
from .document_profile import language_name
from .models.types import ChatOptions,ProviderError
from .retrieval.query import go_strip


@lru_cache(maxsize=1)
def source_prompt_templates():
    import yaml
    root=Path(__file__).with_name('conversation_assets')
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    result={}
    for name,sha in manifest['files'].items():
        raw=(root/name).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sha:raise ProviderError('CONVERSATION_TEMPLATE_ASSET_MISMATCH')
        result[name.removesuffix('.yaml')]=yaml.safe_load(raw)['templates']
    return result


def first_language_tag(header):
    return go_strip(go_strip(header.split(',',1)[0]).split(';',1)[0])


def resolve_language(startup_locale,accept_language):
    # Equivalent injected startup WEKNORA_LANGUAGE; never implicitly read process env.
    return language_name(go_strip(startup_locale) or first_language_tag(accept_language) or 'zh-CN')


def reasoning_override(value):
    aliases={'none':'off','false':'off','disabled':'off','true':'auto','enabled':'auto','default':'auto','on':'auto'}
    value=aliases.get(value,value)
    return value if value in ('off','auto','minimal','low','medium','high','xhigh','max') else ''


def resolve_startup_templates(row,templates=None):
    # Source config.resolvePromptTemplates: found startup IDs replace text;
    # missing IDs retain current fields. Template collection order is explicit.
    templates=source_prompt_templates() if templates is None else templates
    lists=[templates.get(name,[]) for name in ('system_prompt','context_template','rewrite','fallback','generate_session_title','generate_summary','generate_kb_description','keywords_extraction','agent_system_prompt','graph_extraction','generate_questions','intent_prompts')]
    def find(identifier):
        if not identifier:return None
        return next((item for rows in lists for item in rows if item.get('id')==identifier),None)
    result=dict(row)
    if row.get('rewrite_prompt_id'):
        result.setdefault('rewrite_prompt_system','');result.setdefault('rewrite_prompt_user','')
    rewrite=find(row.get('rewrite_prompt_id',''))
    if rewrite is not None:
        result['rewrite_prompt_system']=rewrite.get('content','');result['rewrite_prompt_user']=rewrite.get('user','')
    for identifier,target in [('fallback_prompt_id','fallback_prompt'),('generate_summary_prompt_id','generate_summary_prompt')]:
        found=find(row.get(identifier,''))
        if found is not None:result[target]=found.get('content','')
    summary=dict(row.get('summary') or {})
    for identifier,target in [('prompt_id','prompt'),('context_template_id','context_template')]:
        if summary.get(identifier):summary.setdefault(target,'')
        found=find(summary.get(identifier,''))
        if found is not None:summary[target]=found.get('content','')
    result['summary']=summary
    intents=templates.get('intent_prompts',[])
    if intents:result['intent_system_prompts']={t['id']:t['content'] for t in intents if t.get('id') and t.get('content')}
    return result


def resolve_saved_conversation(source,session):
    """Explicit local saved-conversation adapter, not current Go Session fields.

    Source's current ordinary Session comments the legacy columns out. Server
    context_config.conversation carries only ordinary settings; no Agent runtime.
    Legacy non-null columns retain existing compatibility. Positive-only source
    retrieval overrides do not turn zero into a different global default.
    """
    context=session.get('context_config') or {}
    if isinstance(context,str):context=json.loads(context)
    saved=context.get('conversation') or {}
    if not isinstance(saved,Mapping):raise ProviderError('CONVERSATION_SETTINGS_INVALID')
    values={name:getattr(source,name) for name in ('max_rounds','embedding_top_k','vector_threshold','keyword_threshold','rerank_top_k','rerank_threshold','enable_rewrite','enable_query_expansion','enable_rerank','rewrite_prompt_system','rewrite_prompt_user','query_understand_model_id')}
    for name in ('max_rounds','enable_rewrite','embedding_top_k','vector_threshold','keyword_threshold','rerank_top_k','rerank_threshold'):
        if session.get(name) is not None:values[name]=session[name]
    # Saved profile's nil values mean absent. Zero is tested per source field.
    for name,value in saved.items():
        if name in values and value is not None:
            if name in ('embedding_top_k','vector_threshold','keyword_threshold','rerank_top_k') and value<=0:continue
            if name in ('rewrite_prompt_system','rewrite_prompt_user','query_understand_model_id') and value=='':continue
            values[name]=value
    for name in ('embedding_top_k','vector_threshold','keyword_threshold','rerank_top_k'):
        if values[name] is None or values[name]<=0:values[name]=getattr(source,name)
    if saved.get('multi_turn_enabled') is False:values['max_rounds']=0
    summary=dict(source.summary)
    for overlay in (session.get('summary_parameters') or {},saved.get('summary') or {}):
        if not isinstance(overlay,Mapping):raise ProviderError('CONVERSATION_SUMMARY_INVALID')
        for name,value in overlay.items():
            if value is None:continue
            if name in ('max_completion_tokens','max_tokens') and value<=0:continue
            if name=='temperature' and value<0:continue
            if name in ('prompt','context_template') and value=='':continue
            summary[name]=value
    values['summary']=summary
    values['intent_system_prompts']=dict(saved.get('intent_prompts') or {})
    values['global_intent_system_prompts']=dict(source.intent_system_prompts)
    values['summary_model_id']=saved.get('model_id') or session.get('summary_model_id') or ''
    values['rerank_model_id']=saved.get('rerank_model_id') or session.get('rerank_model_id') or ''
    return values


def resolved_chat_options(summary,request_effort):
    options={k:v for k,v in summary.items() if k in ChatOptions.__dataclass_fields__ and v is not None}
    effort=reasoning_override(request_effort)
    if effort:options.update(reasoning_effort=effort,thinking=effort!='off')
    return ChatOptions(**options)
