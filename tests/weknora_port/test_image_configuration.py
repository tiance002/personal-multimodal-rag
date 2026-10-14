"""No provider traffic: image choice and capability are catalog facts or user claims."""
import asyncio

import pytest

from backend.weknora_port.documents import DocumentLifecycleError
from backend.weknora_port.image_configuration import resolve_image_selection
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.models.types import CallContext,ProviderError,TrustedScope
from backend.weknora_port.storage.configuration import ConfiguredRole


class Store:
    def __init__(self,**roles):self.roles=roles;self.calls=[]
    async def resolve(self,model_id):
        self.calls.append(model_id)
        if model_id not in self.roles:raise DocumentLifecycleError('MODEL_NOT_FOUND')
        return self.roles[model_id]


def role(name,*,declared=False,provider='siliconflow'):
    return ConfiguredRole('vlm',provider,name,'https://fixture.invalid','',
                          {'vision_user_declared':True} if declared else {})


CONTEXT=CallContext('fixture',TrustedScope('owner',('kb',),('doc',)))
CATALOG=SourceCatalogue()
KNOWN='Qwen/Qwen3-VL-32B-Instruct'


def test_dedicated_ocr_explicit_caption_and_upload_precedence():
    async def scenario():
        store=Store(caption=role(KNOWN),ocr=role(KNOWN))
        kb={'image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'ocr',
             'caption_mode':'follow_ocr'},'vlm_config':{}}
        result=await resolve_image_selection(kb,{'ocr_adapter':'tesseract',
            'caption_mode':'explicit','caption_model_id':'caption'},store,CATALOG,CONTEXT)
        assert result.ocr.adapter=='tesseract'
        assert result.caption.model_id=='caption' and result.caption.source=='upload'
        assert store.calls==['caption']
    asyncio.run(scenario())


def test_upload_caption_model_alone_selects_explicit_over_kb_follow():
    async def scenario():
        store=Store(ocr=role(KNOWN),caption=role(KNOWN))
        kb={'image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'ocr',
             'caption_mode':'follow_ocr'},'vlm_config':{}}
        result=await resolve_image_selection(kb,{'caption_model_id':'caption'},
                                             store,CATALOG,CONTEXT)
        assert result.ocr.model_id=='ocr'
        assert result.caption.model_id=='caption'
        assert result.caption.source=='upload'
    asyncio.run(scenario())


def test_vision_ocr_inherits_only_after_capability_check():
    async def scenario():
        store=Store(ocr=role(KNOWN))
        kb={'image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'ocr'},'vlm_config':{}}
        result=await resolve_image_selection(kb,{'ocr_model_id':''},store,CATALOG,CONTEXT)
        assert result.caption.model_id=='ocr' and result.caption.source=='ocr_inherited'
        assert store.calls==['ocr']
    asyncio.run(scenario())


def test_dedicated_ocr_never_becomes_caption_and_unknown_model_fails():
    async def scenario():
        kb={'image_processing_config':{'ocr_adapter':'tesseract'},'vlm_config':{}}
        result=await resolve_image_selection(kb,None,Store(),CATALOG,CONTEXT)
        assert result.caption is None and result.caption_error=='IMAGE_CAPTION_VISION_MODEL_REQUIRED'
        unknown={'image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'x'},'vlm_config':{}}
        with pytest.raises(DocumentLifecycleError,match='IMAGE_VISION_CAPABILITY_UNKNOWN'):
            await resolve_image_selection(unknown,None,Store(x=role('unknown-model')),CATALOG,CONTEXT)
    asyncio.run(scenario())


def test_custom_user_declaration_is_unverified_and_explicit_invalid_does_not_inherit():
    async def scenario():
        kb={'image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'custom'},'vlm_config':{}}
        store=Store(custom=role('custom-image',declared=True,provider='generic'))
        result=await resolve_image_selection(kb,None,store,CATALOG,CONTEXT)
        assert result.caption.capability=='USER_DECLARED_UNTESTED'
        with pytest.raises(DocumentLifecycleError,match='MODEL_NOT_FOUND'):
            await resolve_image_selection(kb,{'caption_mode':'explicit','caption_model_id':'missing'},
                                          store,CATALOG,CONTEXT)
        assert store.calls[-1]=='missing'
    asyncio.run(scenario())


def test_known_text_model_cannot_be_promoted_by_user_checkbox():
    with pytest.raises(ProviderError,match='MODEL_VISION_DECLARATION_CONFLICT'):
        CATALOG.resolve(role('deepseek-ai/DeepSeek-V4-Pro',declared=True),'fake')


def test_explicit_caption_permission_failure_never_resolves_or_inherits_ocr():
    class ScopedStore(Store):
        async def resolve(self,model_id):
            self.calls.append(model_id)
            if model_id=='foreign':raise PermissionError('MODEL_TENANT_SCOPE_DENIED')
            return self.roles[model_id]
    async def scenario():
        store=ScopedStore(ocr=role(KNOWN))
        kb={'image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'ocr'},'vlm_config':{}}
        with pytest.raises(PermissionError,match='MODEL_TENANT_SCOPE_DENIED'):
            await resolve_image_selection(kb,{'caption_model_id':'foreign'},store,CATALOG,CONTEXT)
        assert store.calls==['ocr','foreign']
    asyncio.run(scenario())
