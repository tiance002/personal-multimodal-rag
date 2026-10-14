"""Image results stay separate, including partial failures and empty output."""
import asyncio
import json
from types import SimpleNamespace
import pytest

from backend.weknora_port.image_multimodal import SourceImageProcessor
from backend.weknora_port.ingestion.core import ParsedAsset
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.storage.chunks import ChunkPlan,DocumentScope,StoredChunk
from backend.weknora_port.storage.configuration import ConfiguredRole
from backend.weknora_port.storage.configuration import ModelConfigurationError


MODEL='Qwen/Qwen3-VL-32B-Instruct'


class FakeStore:
    def __init__(self,mode=''):self.calls=[];self.mode=mode
    async def resolve(self,model_id):
        self.calls.append(model_id)
        if self.mode=='caption-revoked' and len(self.calls)==4:
            raise ModelConfigurationError('MODEL_DISABLED')
        return ConfiguredRole('vlm','siliconflow',MODEL,'https://fixture.invalid/v1')


class FakeModel:
    def __init__(self,mode):self.mode=mode;self.calls=[]
    async def vision(self,messages,options,context):
        kind=context.request_id.rsplit('/',1)[-1]
        self.calls.append(kind)
        if self.mode==kind+'-uncertain':raise ProviderError('PROVIDER_TRANSPORT_ERROR',sent=True)
        if self.mode==kind+'-failed':raise ProviderError('SYNTHETIC_MODEL_FAILURE')
        if self.mode==kind+'-empty':return SimpleNamespace(content='  ')
        return SimpleNamespace(content='OCR text' if kind=='ocr_text' else 'Caption text')


def process(mode):
    async def scenario():
        scope=DocumentScope(7,'owner','kb','doc','version')
        parent=StoredChunk('parent',scope,'![image](images/a.png)',0,0,24,'text','')
        asset=ParsedAsset('images/a.png',b'fixture','image/png','standalone_image',3)
        model=FakeModel(mode)
        store=FakeStore(mode)
        class Registry:
            async def get(self,model_id,context):return model
            async def close(self):pass
        class Documents:
            async def _rows(self,*args,**kwargs):
                return [{'metadata':{'source_version':'version'},'parse_status':'processing'}]
        app=SimpleNamespace(documents=Documents(),models=store,catalogue=SourceCatalogue(),
            conversation=ConversationSettings(),registry_for_context=lambda:Registry(),
            _owner=lambda principal:None)
        kb={'id':'kb','image_processing_config':{'ocr_adapter':'vlm','ocr_model_id':'ocr',
             'caption_mode':'explicit','caption_model_id':'caption'},'vlm_config':{}}
        principal=SimpleNamespace(tenant_id=7,subject_id='owner')
        plan=await SourceImageProcessor(app).process_plan(principal,kb,scope,(asset,),ChunkPlan((parent,),()))
        return plan,model.calls,store.calls
    return asyncio.run(scenario())


def test_ocr_success_caption_failure_preserves_ocr_metadata():
    plan,calls,models=process('caption-failed')
    assert calls==['ocr_text','caption'] and models==['ocr','caption','ocr','caption']
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert len(children)==1 and children[0].chunk_type=='image_ocr'
    info=json.loads(children[0].image_info)[0]
    assert info['ocr_status']['status']=='completed'
    assert info['caption_status']['status']=='failed'
    assert info['page_number']==3 and info['source_type']=='standalone_image'


def test_caption_success_ocr_failure_never_mislabels_caption():
    plan,calls,_=process('ocr_text-failed')
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert calls==['ocr_text','caption'] and len(children)==1
    assert children[0].chunk_type=='image_caption' and children[0].content=='Caption text'
    info=json.loads(children[0].image_info)[0]
    assert info['ocr_status']['status']=='failed' and info['caption_status']['status']=='completed'


def test_empty_output_is_not_success_or_asset():
    plan,_,_=process('caption-empty')
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert len(children)==1 and children[0].chunk_type=='image_ocr'
    assert json.loads(children[0].image_info)[0]['caption_status']['error']=='IMAGE_CAPTION_EMPTY'


def test_caption_revoked_during_processing_keeps_ocr_and_never_sends_caption():
    plan,calls,_=process('caption-revoked')
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert calls==['ocr_text'] and len(children)==1
    assert children[0].chunk_type=='image_ocr'
    info=json.loads(children[0].image_info)[0]
    assert info['ocr_status']['status']=='completed'
    assert info['caption_status']['error']=='MODEL_DISABLED'


def test_uncertain_sent_caption_is_not_downgraded_to_ordinary_partial_failure():
    with pytest.raises(ProviderError,match='PROVIDER_TRANSPORT_ERROR') as error:
        process('caption-uncertain')
    assert error.value.sent
