"""S8.8 deterministic reliability regressions; zero network and database writes."""
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from backend.weknora_port.documents import DocumentBytes,DocumentLifecycleError,DocumentLifecycleService
from backend.weknora_port.image_tasks import ImageTask,ImageTaskQueue
from backend.weknora_port.image_configuration import image_generation_settings
from backend.weknora_port.image_multimodal import caption_completion
from backend.weknora_port.models.types import ChatResponse,CallContext,TrustedScope,CallCancelled,RunDeadlineExceeded
from backend.weknora_port.models.usage import normalize_usage
from backend.weknora_port.retrieval.query import format_document_clarification
from backend.weknora_port.service import RuntimeService
from backend.weknora_port.storage.chunks import ChunkPlan,StoredChunk
from backend.weknora_port.storage.indexing import IndexInfo
from backend.weknora_port.streaming.events import StreamEvent
from scripts.s8_6_1_api_acceptance import completed_answer_contract


def test_caption_truncation_usage_is_factual_not_estimated():
    selection=SimpleNamespace(max_output_tokens=5000,reasoning_mode='smart',generation_config_version=3)
    response=ChatResponse(content='',finish_reason='length',usage=normalize_usage({'prompt_tokens':12,
        'completion_tokens':5000,'total_tokens':5012,'completion_tokens_details':{'reasoning_tokens':5000}}))
    state=caption_completion(response,selection)
    assert state['completion_kind']=='truncated'
    assert state['usage']=={'prompt_tokens':12,'completion_tokens':5000,'total_tokens':5012,'reasoning_tokens':5000}
    assert state['error']=='IMAGE_CAPTION_TRUNCATED'


@pytest.mark.parametrize('value',[True,-1,1.5,'5000'])
def test_image_token_settings_reject_noninteger_or_negative(value):
    with pytest.raises(DocumentLifecycleError):image_generation_settings({'max_output_tokens':value})


def test_image_default_and_mode_are_independent_settings():
    assert image_generation_settings({'max_output_tokens':0,'reasoning_mode':'quick','config_version':4})=={
        'max_output_tokens':5000,'reasoning_mode':'quick','generation_config_version':4}


def test_fixed_completion_is_separate_and_does_not_weaken_provider_checks():
    fixed={'completion_source':'server_fixed','provider_completion_required':False,'lifecycle_completed':True,
           'attempts':[],'is_fallback':True}
    def frames(data):return [{'response_type':'answer','content':'Choose a document.'},{'response_type':'complete','data':data}]
    assert completed_answer_contract(frames(fixed))
    assert not completed_answer_contract(frames({'is_fallback':True,'attempts':[]}))
    assert not completed_answer_contract(frames({**fixed,'attempts':[{'finish_reason':'length'}]}))
    assert not completed_answer_contract(frames({'finish_reason':'length','truncated':True}))
    assert not completed_answer_contract(frames({'finish_reason':'stop'}))
    assert completed_answer_contract(frames({'finish_reason':'stop','truncated':False}))


def test_clarification_catalog_is_bounded_without_changing_scope():
    rows=[{'id':str(i),'title':'Long title '*100} for i in range(700)]
    text=format_document_clarification(rows,'English')
    assert len(text)<1000 and text.count('\n- ')==5
    assert '695 more documents' in text and len(rows)==700


def test_cancellation_keeps_unknown_send_and_settlement_facts():
    accepted=SimpleNamespace(context=CallContext('run',TrustedScope('owner',('kb',)),deadline_kind='run'))
    events=[StreamEvent('r','progress',data={'stage':'rerank','status':'completed'}),
            StreamEvent('s','stop',data={'reason':'user_requested'})]
    diagnostic=RuntimeService.cancellation_diagnostics(accepted,events,CallCancelled())
    assert diagnostic['origin']=='user' and diagnostic['stage']=='rerank'
    assert diagnostic['generation_request_sent'] is None and diagnostic['settled_provider_calls'] is None
    assert RuntimeService.cancellation_diagnostics(accepted,[],RunDeadlineExceeded())['deadline_kind']=='run'


@pytest.mark.parametrize('failure',[None,'caption','embedding','index'])
def test_selected_retry_keeps_old_evidence_until_atomic_success(monkeypatch,failure):
    import backend.weknora_port.documents as module
    async def scenario():
        principal=SimpleNamespace(tenant_id=7,subject_id='owner')
        raw=b'private synthetic image';digest=hashlib.sha256(raw).hexdigest()
        task=ImageTask('retry','7','owner','kb','doc','v',{}, {},1,'claim',{},retry={
            'asset_path':'image.png','asset_sha256':digest,'kind':'caption'})
        state={'old_enabled':True,'new_written':False,'complete':False}
        calls=[]
        class Conn:
            @asynccontextmanager
            async def transaction(self):
                before=dict(state)
                try:yield
                except BaseException:state.clear();state.update(before);raise
            async def execute(self,sql,args=()):
                calls.append(sql)
                if sql.startswith('UPDATE embeddings') or sql.startswith('UPDATE chunks'):state['old_enabled']=False
                if "state='completed'" in sql:
                    assert "jsonb_set(outcomes,'{0,embedding,status}'" in sql
                    state['complete']=True
        class Guard:
            connection=Conn()
            async def batch_save(self,indexes,vectors):
                assert state['old_enabled']
                if failure=='index':raise RuntimeError('synthetic index failure')
                state['new_written']=True
        class Leases:
            @asynccontextmanager
            async def lease(self,**kwargs):yield Guard()
        class Repository:
            def __init__(self,*args,**kwargs):pass
            async def create_chunks(self,*args):assert state['old_enabled']
        monkeypatch.setattr(module,'ScopedKnowledgeRepository',Repository)
        class Processor:
            async def process_plan(self,principal,kb,scope,assets,plan,**kw):
                assert len(assets)==1 and kw['prior']['0']['ocr_text']['status']=='unavailable'
                assert kw['request_identity']=='retry' and kw['source_states']==('completed',)
                await kw['on_result'](0,{'caption_status':{'status':'failed' if failure=='caption' else 'completed','error':'IMAGE_CAPTION_TRUNCATED'}})
                if failure=='caption':return plan
                chunk=StoredChunk('new',scope,'complete caption',0,0,0,'image_caption','')
                return ChunkPlan((*plan.persisted,chunk),(IndexInfo('new',0,'new','doc','kb','complete caption'),))
        class Embedder:
            async def batch_embed_with_pool(self,texts,*,attempt):
                assert texts==['complete caption'] and attempt==0 and state['old_enabled']
                if failure=='embedding':raise RuntimeError('synthetic embedding failure')
                return [[.1,.2]]
        service=DocumentLifecycleService.__new__(DocumentLifecycleService)
        service.asset_processor=Processor();service.connection_factory=Leases()
        async def kb(*args):return {'id':'kb','indexing_strategy':{'vector_enabled':True}}
        async def source(*args,**kwargs):return DocumentBytes('image.png','image/png',raw)
        async def rows(sql,args):
            if 'FROM knowledges' in sql:return [{'metadata':{'assets':[{'logical_path':'image.png','source_type':'standalone_image'}]}}]
            return [{'id':'parent','content':'![x](image.png)','chunk_type':'text','metadata':{},'image_info':'[]'},
                    {'id':'old','content':'old complete caption','chunk_type':'image_caption','metadata':{},'image_info':json.dumps([{'url':'image.png'}])}]
        async def locked(*args,**kwargs):assert kwargs['states']==('completed',)
        async def embedder(*args):return Embedder()
        service._kb=kb;service.read_document_asset=source;service._rows=rows
        service._lock_current_image_document=locked;service.embedder_for_kb=embedder
        class Queue:
            async def before(self,task,index,kind):calls.append('sending:'+kind)
            async def result(self,task,index,info):task.outcomes['0']={'caption':info['caption_status']}
        if failure in ('embedding','index'):
            with pytest.raises(RuntimeError):await service._process_selected_image_retry(principal,task,Queue())
        else:
            result=await service._process_selected_image_retry(principal,task,Queue())
            assert result['status']==('failed' if failure else 'completed')
        assert state==({'old_enabled':False,'new_written':True,'complete':True} if failure is None else
                       {'old_enabled':True,'new_written':False,'complete':False})
        assert not any('DELETE' in sql for sql in calls)
    asyncio.run(scenario())



@pytest.mark.parametrize('protected_state,kind',[('unknown','caption'),('cancelled','caption'),('failed','embedding'),('orphaned','embedding')])
def test_selected_retry_binds_config_source_sha_and_idempotency_without_reopening_unknown(protected_state,kind):
    from backend.weknora_port.model_registry import SourceCatalogue
    from backend.weknora_port.storage.configuration import ConfiguredRole
    async def scenario():
        principal=SimpleNamespace(tenant_id=7,subject_id='owner')
        raw=b'synthetic bytes';digest=hashlib.sha256(raw).hexdigest()
        records=[]
        class Cursor:
            def __init__(self,rows):self.rows=rows
            async def fetchall(self):return self.rows
        class Conn:
            @asynccontextmanager
            async def transaction(self):yield
            async def execute(self,sql,args):
                if sql.startswith('SELECT metadata'):return Cursor([{'metadata':{'source_version':'v'},'parse_status':'completed'}])
                if sql.startswith('SELECT id,state'):return Cursor(records)
                if sql.startswith('INSERT'):
                    records.append({'id':args[0],'state':'pending','retry_key':args[6],'selected_config':json.loads(args[7])})
                    return Cursor([])
                raise AssertionError(sql)
        class Guard:
            connection=Conn()
            def require_verified(self):pass
        class Leases:
            @asynccontextmanager
            async def lease(self,**kwargs):yield Guard()
        class Documents:
            async def read_document_asset(self,*args,**kwargs):return DocumentBytes('a.png','image/png',raw)
        class Models:
            async def resolve(self,*args):return ConfiguredRole('vlm','siliconflow','Qwen/Qwen3-VL-32B-Instruct','https://fixture.invalid')
        queue=ImageTaskQueue(Leases(),principal,Documents(),Models(),SourceCatalogue())
        kb={'id':'kb','image_processing_config':{'caption_model_id':'caption','reasoning_mode':'quick','max_output_tokens':5000,'config_version':2}}
        args=dict(source_version='v',asset_path='a.png',asset_sha256=digest,kind='caption',idempotency_key='request_a')
        created=await queue.create_selected_retry(principal,kb,'doc',**args)
        assert created['replay'] is False and len(records)==1
        assert records[0]['selected_config']['selection']['generation_config_version']==2
        assert records[0]['selected_config']['retry']['asset_sha256']==digest
        assert (await queue.create_selected_retry(principal,kb,'doc',**args))['replay'] is True
        with pytest.raises(DocumentLifecycleError,match='IDEMPOTENCY_CONFLICT'):
            await queue.create_selected_retry(principal,kb,'doc',**args,override={'max_output_tokens':6000})
        with pytest.raises(DocumentLifecycleError,match='REPLAY_MISMATCH'):
            await queue.create_selected_retry(principal,kb,'doc',**{**args,'asset_sha256':'f'*64})
        records[0]['state']=protected_state
        records[0]['outcomes']={'0':{kind:{'status':'sending'}}}
        with pytest.raises(DocumentLifecycleError,match='ACTIVE_OR_UNKNOWN'):
            await queue.create_selected_retry(principal,kb,'doc',**{**args,'idempotency_key':'request_b'})
        assert len(records)==1 and records[0]['state']==protected_state
    asyncio.run(scenario())


def test_incomplete_caption_never_enters_summary_enrichment():
    from backend.weknora_port.image_multimodal import enrich_summary_images
    infos=[{'url':'a.png','caption':'TRUNCATED_PRIVATE_CONTENT','caption_status':{'status':'failed'},
            'ocr_text':'valid OCR','ocr_status':{'status':'completed'}}]
    result=enrich_summary_images('![a](a.png)',infos,True)
    assert 'TRUNCATED_PRIVATE_CONTENT' not in result and 'valid OCR' in result


@pytest.mark.parametrize('kind',['caption','embedding'])
def test_cancel_sending_image_task_preserves_unknown_barrier(kind):
    async def scenario():
        principal=SimpleNamespace(tenant_id=7,subject_id='owner')
        row={'id':'task','state':'processing','source_version':'v','outcomes':{'0':{kind:{'status':'sending'}}}}
        commands=[]
        class Cursor:
            async def fetchall(self):return [row]
        class Conn:
            @asynccontextmanager
            async def transaction(self):yield
            async def execute(self,sql,args):
                commands.append((sql,args))
                return Cursor()
        class Guard:
            connection=Conn()
            def require_verified(self):pass
        class Leases:
            @asynccontextmanager
            async def lease(self,**kwargs):yield Guard()
        queue=ImageTaskQueue(Leases(),principal,None,None,None)
        result=await queue.cancel(principal,'kb','doc')
        assert result['state']=='unknown'
        update=next((sql,args) for sql,args in commands if sql.startswith('UPDATE port_image_tasks'))
        assert ('unknown' in update[0] or 'unknown' in update[1])
        assert row['outcomes']['0'][kind]['status']=='sending'
    asyncio.run(scenario())
