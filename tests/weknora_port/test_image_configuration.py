"""Image roles use local MinerU OCR and a separate optional visual caption."""
import asyncio

import pytest

from backend.weknora_port.documents import DocumentLifecycleError
from backend.weknora_port.image_configuration import resolve_image_selection, validate_pinned_selection
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.models.types import CallContext,ProviderError,TrustedScope
from backend.weknora_port.runtime import ApplicationRuntime
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


def test_mineru_is_default_ocr_and_caption_is_an_independent_explicit_role():
    async def scenario():
        store=Store(caption=role(KNOWN),legacy_ocr=role(KNOWN))
        kb={'image_processing_config':{},'vlm_config':{'enabled':True,'model_id':'legacy_ocr'}}
        default=await resolve_image_selection(kb,None,store,CATALOG,CONTEXT)
        assert default.ocr.adapter=='mineru' and default.ocr.capability=='LOCAL_ENGINE'
        assert default.caption is None
        assert default.caption_error=='IMAGE_CAPTION_VISION_MODEL_REQUIRED'
        assert store.calls==[]

        explicit=await resolve_image_selection(kb,{'ocr_adapter':'mineru','caption_mode':'explicit',
            'caption_model_id':'caption'},store,CATALOG,CONTEXT)
        assert explicit.ocr.adapter=='mineru'
        assert explicit.caption.model_id=='caption' and explicit.caption.source=='upload'
        assert store.calls==['caption']
    asyncio.run(scenario())


@pytest.mark.parametrize('kb,override,code',[
    ({'image_processing_config':{'ocr_adapter':'tesseract'}},None,'IMAGE_OCR_ADAPTER_RETIRED'),
    ({'image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'old'}},None,'IMAGE_OCR_ADAPTER_RETIRED'),
    ({'image_processing_config':{}},{'ocr_model_id':'old'},'IMAGE_OCR_MODEL_RETIRED'),
])
def test_new_and_legacy_tesseract_or_vlm_ocr_selection_is_rejected(kb,override,code):
    async def scenario():
        with pytest.raises(DocumentLifecycleError,match=code):
            await resolve_image_selection(kb,override,Store(),CATALOG,CONTEXT)
    asyncio.run(scenario())


def test_explicit_caption_binding_is_not_inherited_as_ocr():
    async def scenario():
        store=Store(caption=role(KNOWN))
        kb={'image_processing_config':{'ocr_adapter':'mineru','caption_mode':'explicit',
             'caption_model_id':'caption'},'vlm_config':{}}
        result=await resolve_image_selection(kb,None,store,CATALOG,CONTEXT)
        assert result.ocr.adapter=='mineru'
        assert result.caption.adapter=='vlm' and result.caption.model_id=='caption'
        assert result.caption.source=='knowledge_base'
    asyncio.run(scenario())


def test_pinned_mineru_and_caption_are_validated_independently():
    async def scenario():
        store=Store(caption=role(KNOWN))
        snapshot={'config_version':1,'ocr':{'adapter':'mineru','source':'local_parser'},
            'caption':{'adapter':'vlm','model_id':'caption','source':'knowledge_base'},
            'caption_error':''}
        result=await validate_pinned_selection(snapshot,store,CATALOG,CONTEXT)
        assert result.ocr.adapter=='mineru'
        assert result.caption.model_id=='caption'
        assert result.caption.source=='knowledge_base'
        assert store.calls==['caption']
    asyncio.run(scenario())


@pytest.mark.parametrize('adapter',['tesseract','vlm'])
def test_pinned_legacy_ocr_task_is_rejected_without_resolving_it(adapter):
    async def scenario():
        store=Store(ocr=role(KNOWN))
        snapshot={'config_version':1,'ocr':{'adapter':adapter,'model_id':'ocr','source':'knowledge_base'},
                  'caption':None}
        with pytest.raises(DocumentLifecycleError,match='IMAGE_OCR_ADAPTER_RETIRED'):
            await validate_pinned_selection(snapshot,store,CATALOG,CONTEXT)
        assert store.calls==[]
    asyncio.run(scenario())


@pytest.mark.parametrize('patch,code',[
    ({'ocr_adapter':'tesseract'},'IMAGE_OCR_ADAPTER_RETIRED'),
    ({'ocr_adapter':'vlm'},'IMAGE_OCR_ADAPTER_RETIRED'),
    ({'ocr_model_id':'old-model'},'IMAGE_OCR_MODEL_RETIRED'),
])
def test_runtime_config_write_rejects_retired_ocr_inputs_before_persistence(patch,code):
    async def scenario():
        app=ApplicationRuntime.__new__(ApplicationRuntime)
        app._owner=lambda principal:None
        class Documents:
            async def _kb(self,principal,knowledge_base_id):return {'id':knowledge_base_id,
                'image_processing_config':{'enable_multimodal':False}}
        app.documents=Documents()
        with pytest.raises(ValueError,match=code):
            await app.update_image_processing(object(),'kb',patch)
    asyncio.run(scenario())


def test_known_text_model_cannot_be_promoted_by_user_checkbox():
    with pytest.raises(ProviderError,match='MODEL_VISION_DECLARATION_CONFLICT'):
        CATALOG.resolve(role('deepseek-ai/DeepSeek-V4-Pro',declared=True),'fake')


def test_explicit_caption_permission_failure_fails_without_ocr_model_fallback():
    class ScopedStore(Store):
        async def resolve(self,model_id):
            self.calls.append(model_id)
            if model_id=='foreign':raise PermissionError('MODEL_TENANT_SCOPE_DENIED')
            return self.roles[model_id]
    async def scenario():
        store=ScopedStore(legacy_ocr=role(KNOWN))
        kb={'image_processing_config':{'ocr_adapter':'mineru'},'vlm_config':{'enabled':True,'model_id':'legacy_ocr'}}
        with pytest.raises(PermissionError,match='MODEL_TENANT_SCOPE_DENIED'):
            await resolve_image_selection(kb,{'caption_model_id':'foreign'},store,CATALOG,CONTEXT)
        assert store.calls==['foreign']
    asyncio.run(scenario())
