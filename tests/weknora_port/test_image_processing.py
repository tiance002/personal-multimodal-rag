"""MinerU OCR source maps stay separate from optional visual captions."""
import asyncio
import json
from types import SimpleNamespace
import pytest

from backend.weknora_port.image_multimodal import SourceImageProcessor
from backend.weknora_port.ingestion.core import ParsedAsset,ParsedDocument,ParseFailure
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.storage.chunks import ChunkPlan,DocumentScope,StoredChunk
from backend.weknora_port.storage.configuration import ConfiguredRole
from backend.weknora_port.storage.configuration import ModelConfigurationError


MODEL='Qwen/Qwen3-VL-32B-Instruct'
OCR_TEXT='alpha\n\n\n-7'
OCR_METADATA={
    'mineru_source':{'parser_engine':'mineru-basic','parser_version':'4.0.12','source_sha256':'synthetic-digest'},
    'source_locators':[{'start':0,'end':len(OCR_TEXT),'quote':OCR_TEXT,'page':1,
                        'bbox':[0.1,0.2,0.8,0.9]}],
    'mineru_blocks':[{'block_type':'text','bbox':[0.1,0.2,0.8,0.9],
                      'offset_space':'derived_unicode_codepoints'}],
}


class FakeStore:
    def __init__(self,mode=''):self.calls=[];self.mode=mode
    async def resolve(self,model_id):
        self.calls.append(model_id)
        if self.mode=='caption-revoked' and len(self.calls)==2:
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
        return SimpleNamespace(content='Caption text')


def process(mode):
    async def scenario():
        scope=DocumentScope(7,'owner','kb','doc','version')
        parent=StoredChunk('parent',scope,'![image](images/a.png)',0,0,24,'text','')
        asset=ParsedAsset('images/a.png',b'synthetic-image-bytes','image/png','standalone_image',3)
        model=FakeModel(mode)
        store=FakeStore(mode)

        class Registry:
            async def get(self,model_id,context):return model
            async def close(self):pass

        class ParserRegistry:
            def __init__(self):self.requests=[]
            def resolve(self,engine,filename):
                if mode=='mineru-unavailable':raise ValueError('MINERU_EXPLICIT_PARSER_UNAVAILABLE')
                assert engine=='mineru-basic' and filename=='a.png'
                return engine
            def parse(self,request):
                self.requests.append(request)
                if mode=='mineru-failed':
                    return 'mineru-basic',ParsedDocument('',metadata={},failures=(
                        ParseFailure('parse','MINERU_SYNTHETIC_FAILURE','SyntheticError','fixture'),))
                return 'mineru-basic',ParsedDocument(OCR_TEXT,metadata=OCR_METADATA)

        parsers=ParserRegistry()
        class Documents:
            parser_registry=parsers
            async def _rows(self,*args,**kwargs):
                return [{'metadata':{'source_version':'version'},'parse_status':'processing'}]

        app=SimpleNamespace(documents=Documents(),models=store,catalogue=SourceCatalogue(),
            conversation=ConversationSettings(),registry_for_context=lambda:Registry(),
            _owner=lambda principal:None)
        kb={'id':'kb','image_processing_config':{'ocr_adapter':'mineru',
             'caption_mode':'explicit','caption_model_id':'caption'},'vlm_config':{}}
        principal=SimpleNamespace(tenant_id=7,subject_id='owner')
        plan=await SourceImageProcessor(app).process_plan(principal,kb,scope,(asset,),ChunkPlan((parent,),()))
        return plan,model.calls,store.calls,parsers.requests
    return asyncio.run(scenario())


def test_mineru_ocr_success_caption_failure_preserves_text_and_source_map():
    plan,calls,models,requests=process('caption-failed')
    assert calls==['caption'] and models==['caption','caption']
    assert len(requests)==1
    request=requests[0]
    assert (request.owner_id,request.knowledge_base_id,request.document_id,request.source_version)==(
        'owner','kb','doc','version')
    assert request.parser_engine=='mineru-basic' and request.filename=='a.png'
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert len(children)==1 and children[0].chunk_type=='image_ocr'
    assert children[0].content==OCR_TEXT
    info=json.loads(children[0].image_info)[0]
    assert info['ocr_status']['status']=='completed'
    assert info['caption_status']['status']=='failed'
    assert info['page_number']==3 and info['source_type']=='standalone_image'
    assert info['ocr_layout']['mineru_source']==OCR_METADATA['mineru_source']
    assert info['ocr_layout']['source_locators']==OCR_METADATA['source_locators']
    assert info['ocr_layout']['mineru_blocks']==OCR_METADATA['mineru_blocks']


def test_mineru_failure_does_not_mislabel_caption_as_ocr():
    plan,calls,_,requests=process('mineru-failed')
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert calls==['caption'] and len(requests)==1
    assert len(children)==1 and children[0].chunk_type=='image_caption'
    assert children[0].content=='Caption text'
    info=json.loads(children[0].image_info)[0]
    assert info['ocr_status']['status']=='failed'
    assert info['ocr_status']['error']=='MINERU_SYNTHETIC_FAILURE'
    assert info['caption_status']['status']=='completed'


def test_missing_mineru_registry_fails_ocr_closed_without_vlm_ocr_fallback():
    plan,calls,_,requests=process('mineru-unavailable')
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert calls==['caption'] and requests==[]
    assert len(children)==1 and children[0].chunk_type=='image_caption'
    info=json.loads(children[0].image_info)[0]
    assert info['ocr_status']['status']=='failed'
    assert info['ocr_status']['error']=='IMAGE_MINERU_PARSER_REQUIRED'
    assert info['caption_status']['status']=='completed'


def test_empty_caption_is_not_success_or_asset():
    plan,_,_,_=process('caption-empty')
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert len(children)==1 and children[0].chunk_type=='image_ocr'
    assert json.loads(children[0].image_info)[0]['caption_status']['error']=='IMAGE_CAPTION_EMPTY'


def test_caption_revocation_keeps_local_ocr_and_sends_no_caption():
    plan,calls,models,_=process('caption-revoked')
    children=[row for row in plan.persisted if row.chunk_type.startswith('image_')]
    assert calls==[] and models==['caption','caption'] and len(children)==1
    assert children[0].chunk_type=='image_ocr'
    info=json.loads(children[0].image_info)[0]
    assert info['ocr_status']['status']=='completed'
    assert info['caption_status']['error']=='MODEL_DISABLED'


def test_uncertain_sent_caption_is_not_downgraded_to_partial_success():
    with pytest.raises(ProviderError,match='PROVIDER_TRANSPORT_ERROR') as error:
        process('caption-uncertain')
    assert error.value.sent
