"""Synthetic selected-caption source association; no Provider or private data.

The separate immutable real-source replay is retained only in private reports.
This fixture preserves the source-locator shape with synthetic text and IDs.
"""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port import documents
from backend.weknora_port.documents import DocumentLifecycleError

FROZEN = json.loads('{"chunk":{"chunk_id":"synthetic-parent","document_id":"synthetic-document","knowledge_base_id":"synthetic-kb","text":"10\\n20\\n\\nA synthetic code excerpt for an image.","chunk_index":7,"start":100,"end":145,"source_version":"synthetic-version","page_number":2,"source_locators":[{"kind":"pdf","page":2,"start":100,"end":105,"quote":"10\\n20","raw_text":"10\\n20","bbox":[0.1,0.2,0.8,0.9],"raw_evidence":{"page_idx":1,"block_index":0,"block_path":"/pages/1/blocks/0/content/0","block_type":"image_body","bbox":[0.1,0.2,0.8,0.9],"coordinate_space":"normalized_0_1","page_dimensions":{"width_pt":600.0,"height_pt":400.0},"offset_space":"derived_unicode_codepoints","source_sha256":"6bd40a8516177ca8dab7a2eef9489acd311bc4a3be08f321b1faba3ab40229d3","middle":{"logical_path":"mineru/rag_middle_json.json","sha256":"f1c62af13e21b2bd6b01de6258498b9a18b9f9810e0c88b8f68c30eea8fe16db"},"asset":{"logical_path":"mineru/images/page_1_image_0.jpg","sha256":"5df73e7cb1cc711c22fe5b8e97f35e368254c74efab976466e7e61c3c51b4ff6"},"derived_block_start":100,"derived_block_end":105,"image_ocr":{"sidecar":{"logical_path":"mineru/image_ocr.json","sha256":"d72729fc255482590bba07f3ffc78afce46e42da7a5e69f34cd73669f4f9720f"},"asset_sha256":"5df73e7cb1cc711c22fe5b8e97f35e368254c74efab976466e7e61c3c51b4ff6","width":100,"height":50,"lines":[],"coordinate_space":"asset_pixels","origin":"local_mineru_image_text","text_offset_space":"image_ocr_body_unicode"}}}],"metadata":{"source_version":"synthetic-version","mineru_source":{"middle":{"logical_path":"mineru/rag_middle_json.json","sha256":"f1c62af13e21b2bd6b01de6258498b9a18b9f9810e0c88b8f68c30eea8fe16db"},"schema":"docvortex.middle","document_id":"synthetic-document","offset_space":"derived_unicode_codepoints","page_indices":[0,1,2],"parser_engine":"mineru-basic","source_sha256":"6bd40a8516177ca8dab7a2eef9489acd311bc4a3be08f321b1faba3ab40229d3","parser_version":"4.0.12","schema_version":"2.0","source_version":"synthetic-version","is_full_document":true,"source_page_count":3,"derived_text_sha256":"792e2bd1cfd42b01f977a89ad1fcfc7761f1765c9436774b541b3c7dbad0fc5a"},"source_locators":[{"kind":"pdf","page":2,"start":100,"end":105,"quote":"10\\n20","raw_text":"10\\n20","bbox":[0.1,0.2,0.8,0.9],"raw_evidence":{"page_idx":1,"block_index":0,"block_path":"/pages/1/blocks/0/content/0","block_type":"image_body","bbox":[0.1,0.2,0.8,0.9],"coordinate_space":"normalized_0_1","page_dimensions":{"width_pt":600.0,"height_pt":400.0},"offset_space":"derived_unicode_codepoints","source_sha256":"6bd40a8516177ca8dab7a2eef9489acd311bc4a3be08f321b1faba3ab40229d3","middle":{"logical_path":"mineru/rag_middle_json.json","sha256":"f1c62af13e21b2bd6b01de6258498b9a18b9f9810e0c88b8f68c30eea8fe16db"},"asset":{"logical_path":"mineru/images/page_1_image_0.jpg","sha256":"5df73e7cb1cc711c22fe5b8e97f35e368254c74efab976466e7e61c3c51b4ff6"},"derived_block_start":100,"derived_block_end":105,"image_ocr":{"sidecar":{"logical_path":"mineru/image_ocr.json","sha256":"d72729fc255482590bba07f3ffc78afce46e42da7a5e69f34cd73669f4f9720f"},"asset_sha256":"5df73e7cb1cc711c22fe5b8e97f35e368254c74efab976466e7e61c3c51b4ff6","width":100,"height":50,"lines":[],"coordinate_space":"asset_pixels","origin":"local_mineru_image_text","text_offset_space":"image_ocr_body_unicode"}}}]}},"identity":{"middle":{"logical_path":"mineru/rag_middle_json.json","sha256":"f1c62af13e21b2bd6b01de6258498b9a18b9f9810e0c88b8f68c30eea8fe16db"},"schema":"docvortex.middle","document_id":"synthetic-document","offset_space":"derived_unicode_codepoints","page_indices":[0,1,2],"parser_engine":"mineru-basic","source_sha256":"6bd40a8516177ca8dab7a2eef9489acd311bc4a3be08f321b1faba3ab40229d3","parser_version":"4.0.12","schema_version":"2.0","source_version":"synthetic-version","is_full_document":true,"source_page_count":3,"derived_text_sha256":"792e2bd1cfd42b01f977a89ad1fcfc7761f1765c9436774b541b3c7dbad0fc5a"},"assets":[{"path":"synthetic-storage/mineru/images/page_1_image_0.jpg","size":123,"sha256":"5df73e7cb1cc711c22fe5b8e97f35e368254c74efab976466e7e61c3c51b4ff6","metadata":{"sha256":"5df73e7cb1cc711c22fe5b8e97f35e368254c74efab976466e7e61c3c51b4ff6","processing_role":"parser_evidence","source_sha256":"6bd40a8516177ca8dab7a2eef9489acd311bc4a3be08f321b1faba3ab40229d3","locations":[{"bbox":[0.1,0.2,0.8,0.9],"page_idx":1,"block_path":"/pages/1/blocks/0/content/0","block_index":0}]},"media_type":"image/jpeg","page_number":2,"source_type":"mineru_evidence","logical_path":"mineru/images/page_1_image_0.jpg"},{"path":"synthetic-storage/mineru/rag_middle_json.json","size":123,"sha256":"f1c62af13e21b2bd6b01de6258498b9a18b9f9810e0c88b8f68c30eea8fe16db","metadata":{"sha256":"f1c62af13e21b2bd6b01de6258498b9a18b9f9810e0c88b8f68c30eea8fe16db","processing_role":"parser_evidence","source_sha256":"6bd40a8516177ca8dab7a2eef9489acd311bc4a3be08f321b1faba3ab40229d3"},"media_type":"application/json","page_number":null,"source_type":"mineru_evidence","logical_path":"mineru/rag_middle_json.json"},{"path":"synthetic-storage/mineru/image_ocr.json","size":123,"sha256":"d72729fc255482590bba07f3ffc78afce46e42da7a5e69f34cd73669f4f9720f","metadata":{"sha256":"d72729fc255482590bba07f3ffc78afce46e42da7a5e69f34cd73669f4f9720f","processing_role":"parser_evidence","source_sha256":"6bd40a8516177ca8dab7a2eef9489acd311bc4a3be08f321b1faba3ab40229d3"},"media_type":"application/json","page_number":null,"source_type":"mineru_evidence","logical_path":"mineru/image_ocr.json"}]}')


def fixture():
    f=copy.deepcopy(FROZEN); c=f['chunk']; identity=f['identity']
    task=SimpleNamespace(tenant_id=7,subject_id='owner',knowledge_base_id=c['knowledge_base_id'],
        knowledge_id=c['document_id'],source_version=c['source_version'],retry={
            'kind':'caption','asset_path':'mineru/images/page_1_image_0.jpg',
            'asset_sha256':f['assets'][0]['sha256']})
    metadata={'source_version':task.source_version,'sha256':identity['source_sha256'],
        'assets':f['assets'],'parser_engine':'mineru-basic','parser_metadata':{
            'mineru_source':identity,'source_locators':c['metadata']['source_locators']}}
    row={'id':c['chunk_id'],'tenant_id':7,'knowledge_base_id':task.knowledge_base_id,
        'knowledge_id':task.knowledge_id,'content':c['text'],'chunk_type':'text',
        'start_at':c['start'],'end_at':c['end'],'chunk_index':c['chunk_index'],
        'content_revision':0,'is_enabled':True,'metadata':{'source_version':task.source_version,
            'mineru_source':identity,'source_locators':c['metadata']['source_locators']}}
    manifest=next(a for a in f['assets'] if a['logical_path']==task.retry['asset_path'])
    return task,metadata,manifest,row


def resolve(task,metadata,manifest,rows):
    return documents._selected_image_retry_parent(rows,task,metadata,manifest)


def test_synthetic_locator_selects_original_parent_without_rewriting_content():
    task,metadata,manifest,row=fixture()
    assert task.retry['asset_path'] not in row['content']
    before=copy.deepcopy(row)
    parent,explicit=resolve(task,metadata,manifest,[row])
    assert parent is row and explicit is True and row==before
    assert parent['id']=='synthetic-parent'
    assert hashlib.sha256(parent['content'].encode()).hexdigest()==hashlib.sha256(FROZEN['chunk']['text'].encode()).hexdigest()


def test_existing_markdown_parent_path_is_preserved():
    task,metadata,manifest,row=fixture(); row['content']='![image]('+task.retry['asset_path']+')'
    row['metadata']={'source_version':task.source_version}
    parent,explicit=resolve(task,metadata,manifest,[row])
    assert parent is row and explicit is False


@pytest.mark.parametrize('field,value',[
    ('tenant_id',8),('knowledge_base_id','other-kb'),('knowledge_id','other-doc'),
    ('content_revision',1),('is_enabled',False),('start_at',101),('end_at',999),
])
def test_invalid_current_parent_identity_is_denied(field,value):
    task,metadata,manifest,row=fixture();row[field]=value
    with pytest.raises((DocumentLifecycleError,PermissionError)):
        resolve(task,metadata,manifest,[row])


@pytest.mark.parametrize('target,field,value',[
    ('metadata','source_version','old-version'),
    ('identity','source_version','old-version'),('identity','document_id','other-doc'),
    ('identity','source_sha256','0'*64),('proof','page_idx',0),
    ('proof','block_path','/pages/0/blocks/0/content/0'),('proof','block_index',7),
    ('proof','derived_block_start',101),('proof','source_sha256','0'*64),
    ('asset','sha256','0'*64),('locator','start',101),
])
def test_invalid_or_conflicting_locator_proof_is_denied(target,field,value):
    task,metadata,manifest,row=fixture()
    # Keep document originals independent, as a corrupt current chunk must not
    # silently redefine its source proof.
    row=copy.deepcopy(row)
    locations={'metadata':row['metadata'],'identity':row['metadata']['mineru_source'],
        'locator':row['metadata']['source_locators'][0],
        'proof':row['metadata']['source_locators'][0]['raw_evidence'],
        'asset':row['metadata']['source_locators'][0]['raw_evidence']['asset']}
    locations[target][field]=value
    with pytest.raises((DocumentLifecycleError,PermissionError)):
        resolve(task,metadata,manifest,[row])


@pytest.mark.parametrize('reverse',[False,True])
def test_multiple_matching_parents_are_ambiguous_regardless_of_order(reverse):
    task,metadata,manifest,row=fixture();other=copy.deepcopy(row);other['id']='other-parent'
    rows=[row,other]
    if reverse:rows.reverse()
    with pytest.raises(DocumentLifecycleError,match='IMAGE_PARENT_CHUNK_AMBIGUOUS'):
        resolve(task,metadata,manifest,rows)


def test_missing_locator_and_unselected_ocr_do_not_use_caption_association():
    task,metadata,manifest,row=fixture();row['metadata']['source_locators']=[]
    with pytest.raises(DocumentLifecycleError,match='IMAGE_PARENT_CHUNK_NOT_FOUND'):
        resolve(task,metadata,manifest,[row])


@pytest.mark.parametrize('invalid',[None,'upload','wrong-id','wrong-scope','wrong-version','two-assets'])
def test_processor_explicit_parent_is_server_selected_retry_only(monkeypatch,invalid):
    from backend.weknora_port import image_multimodal as module
    from backend.weknora_port.ingestion.core import ParsedAsset
    from backend.weknora_port.storage.chunks import ChunkPlan,DocumentScope,StoredChunk
    from backend.weknora_port.image_configuration import ImageBinding,ImageSelection
    async def scenario():
        task,_,_,row=fixture()
        scope=DocumentScope(task.tenant_id,task.subject_id,task.knowledge_base_id,task.knowledge_id,task.source_version)
        parent=StoredChunk(row['id'],scope,row['content'],row['chunk_index'],row['start_at'],row['end_at'],
            'text','',metadata={'source_version':task.source_version})
        original=parent.content
        if invalid=='wrong-scope':parent.scope=DocumentScope(8,'other','kb','doc','v')
        if invalid=='wrong-version':parent.metadata['source_version']='stale'
        asset=ParsedAsset(task.retry['asset_path'],b'synthetic asset bytes','image/jpeg','mineru_evidence',2)
        assets=(asset,asset) if invalid=='two-assets' else (asset,)
        async def selected(*args):return ImageSelection(ImageBinding('mineru'),None)
        monkeypatch.setattr(module,'validate_pinned_selection',selected)
        class Registry:
            async def get(self,*args):pytest.fail('zero Provider/model access required')
            async def close(self):pass
        class Documents:
            async def _rows(self,*args):return [{'parse_status':'processing' if invalid=='upload' else 'completed',
                'metadata':{'source_version':task.source_version}}]
        app=SimpleNamespace(_owner=lambda p:None,documents=Documents(),models=None,catalogue=None,
            registry_for_context=lambda:Registry(),conversation=SimpleNamespace(language_locale='en-US'))
        processor=module.SourceImageProcessor(app)
        kw={'selection_snapshot':{},'source_states':('completed',),'request_identity':'selected-attempt',
            'source_parent_id':'missing' if invalid=='wrong-id' else row['id'],
            'prior':{'0':{'ocr_text':{'status':'unavailable'},'caption':{'status':'unavailable'}}}}
        if invalid=='upload':kw['source_states']=('processing',)
        principal=SimpleNamespace(tenant_id=task.tenant_id,subject_id=task.subject_id)
        call=processor.process_plan(principal,{'id':task.knowledge_base_id},scope,assets,ChunkPlan((parent,),()),**kw)
        if invalid:
            with pytest.raises(DocumentLifecycleError,match='IMAGE_SOURCE_ASSOCIATION_INVALID'):await call
        else:
            plan=await call
            assert plan.persisted==(parent,) and parent.content==original
    asyncio.run(scenario())


def test_retry_entry_passes_validated_original_parent_and_offsets_without_provider():
    """Synthetic asset bytes exercise the real caller and locator contract."""
    async def scenario():
        task,metadata,manifest,row=fixture()
        raw=b'synthetic image bytes for no-network caller check'
        digest=hashlib.sha256(raw).hexdigest()
        # Bind the synthetic transport bytes and all synthetic proof references.
        task.retry['asset_sha256']=digest;manifest['sha256']=digest
        manifest['metadata']['sha256']=digest
        for locator in row['metadata']['source_locators']:
            proof=locator['raw_evidence']
            if proof.get('asset',{}).get('logical_path')==manifest['logical_path']:
                proof['asset']['sha256']=digest;proof['image_ocr']['asset_sha256']=digest
        task.config={};task.selection={};task.id='fresh-attempt';task.outcomes={}
        called=[]
        class Processor:
            async def process_plan(self,principal,kb,scope,assets,plan,**kwargs):
                called.append(kwargs)
                assert kwargs['source_parent_id']==row['id']
                assert kwargs['request_identity']=='fresh-attempt'
                assert plan.persisted[0].content==FROZEN['chunk']['text']
                assert (plan.persisted[0].start_at,plan.persisted[0].end_at)==(FROZEN['chunk']['start'],FROZEN['chunk']['end'])
                raise RuntimeError('VALIDATED_PARENT_REACHED_ZERO_SEND')
        service=documents.DocumentLifecycleService.__new__(documents.DocumentLifecycleService)
        async def kb(*args):return {'id':task.knowledge_base_id,'indexing_strategy':{'keyword_enabled':True}}
        async def source(*args,**kwargs):return documents.DocumentBytes('asset.jpg','image/jpeg',raw)
        async def rows(sql,args):
            if 'FROM knowledges' in sql:
                return [{'metadata':metadata,'file_hash':metadata['sha256'],'parse_status':'completed'}]
            return [row]
        service._kb=kb;service.read_document_asset=source;service._rows=rows;service.asset_processor=Processor()
        principal=SimpleNamespace(tenant_id=task.tenant_id,subject_id=task.subject_id)
        with pytest.raises(RuntimeError,match='VALIDATED_PARENT_REACHED_ZERO_SEND'):
            await service._process_selected_image_retry(principal,task,SimpleNamespace())
        assert len(called)==1
    asyncio.run(scenario())
    task,metadata,manifest,row=fixture();task.retry['kind']='ocr_text'
    with pytest.raises(DocumentLifecycleError,match='IMAGE_PARENT_CHUNK_NOT_FOUND'):
        resolve(task,metadata,manifest,[row])
