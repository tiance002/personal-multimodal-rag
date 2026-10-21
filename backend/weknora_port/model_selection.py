"""Fixed normal QA model priority; source enums, not URL/cloud heuristics."""
from .models.types import ProviderError
from .storage.configuration import ModelConfigurationError
from .retrieval.query import go_strip


async def _model_or_none(get_model,id):
    try:return await get_model(id)
    except (ProviderError,ModelConfigurationError) as error:
        if getattr(error,'code',str(error)) not in ('MODEL_NOT_FOUND','MODEL_ID_INVALID','MODEL_UNAVAILABLE'):
            raise  # Scope/identity/cipher safety cannot be softened by selection.
        return None


async def resolve_chat_model_id(request_id,profile_id,kb_ids,get_kb,get_model,list_models):
    # Source resolveChatModelID validates saved profile before request override.
    profile_id=go_strip(profile_id or '')
    if profile_id:
        model=await _model_or_none(get_model,profile_id)
        if model is None or model['type']!='KnowledgeQA':raise ProviderError('CONFIGURED_CHAT_MODEL_UNAVAILABLE')
    request_id=go_strip(request_id or '')
    if request_id:
        model=await _model_or_none(get_model,request_id)
        if model is not None and model['type']=='KnowledgeQA':return request_id
    if profile_id:return profile_id
    # Source selectChatModelID: literal remote first, then first KB, then ListModels.
    for id in kb_ids:
        kb=await get_kb(id)
        if kb and kb.get('summary_model_id'):
            model=await _model_or_none(get_model,kb['summary_model_id'])
            if model and model['source']=='remote':return kb['summary_model_id']
    if kb_ids:
        kb=await get_kb(kb_ids[0])
        if kb is None:raise ProviderError('KNOWLEDGE_BASE_MODEL_SELECTION_FAILED')
        if kb.get('summary_model_id'):return kb['summary_model_id']
    for model in await list_models():
        if model is not None and model['type']=='KnowledgeQA':return model['id']
    raise ProviderError('CHAT_MODEL_CONFIGURATION_REQUIRED')
