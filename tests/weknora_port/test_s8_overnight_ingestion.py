"""Offline synthetic runner contracts; no service, Provider or database access."""
import asyncio,copy,json
from types import SimpleNamespace
import pytest
from scripts.s8_overnight_ingestion import (SavedMinerUReceiptParser,NoRetryEmbedder,BoundedEmbeddingCatalogue,
    MODEL_ID,sha,source_key,parent_state,write,canonical,verify_execution,single_instance,is_current_projection)
from backend.weknora_port.ingestion.core import DocumentRequest,ParserRegistry,ingest_document
from backend.weknora_port.ingestion.mineru import public_chunk_locators
from backend.weknora_port.storage.indexing import batch_embed_with_backoff,IndexBoundaryDenied
from backend.weknora_port.storage.configuration import ConfiguredRole
from backend.weknora_port.model_registry import SourceCatalogue


def saved():
    raw=b'synthetic original image; not a real OCR execution'
    text='Matrix\n1 -2 3\n4 5 -6'
    digest=sha(raw);middle={'logical_path':'mineru/middle_json.json','sha256':'a'*64}
    identity={'source_sha256':digest,'source_version':'historical-version','document_id':'historical-doc',
        'parser_engine':'mineru-basic','parser_version':'4.0.12','schema':'docvortex.middle','schema_version':'2.0',
        'offset_space':'derived_unicode_codepoints','middle':middle,'page_indices':[0],
        'derived_text_sha256':sha(text.encode())}
    locator={'kind':'image','page':1,'start':0,'end':len(text),'quote':text,'raw_text':text,'bbox':[0,0,1,1],
        'raw_evidence':{'page_idx':0,'block_index':0,'block_path':'/pages/0/blocks/0','block_type':'text',
            'source_sha256':digest,'middle':middle,'coordinate_space':'normalized_0_1','offset_space':'derived_unicode_codepoints'}}
    row={'image_sha256':digest,'status':'COMPLETE','parser_engine':'mineru-basic','parser_version':'4.0.12',
        'gold_was_sent_to_parser':False,'raw_ocr_text':text,'derived_text_sha256':sha(text.encode()),
        'mineru_source':identity,'source_locators':[locator],'mineru_blocks':[],
        'document_id':'historical-doc','source_version':'historical-version'}
    return raw,row


def test_saved_projection_is_exact_rebound_and_source_readable_without_fake_middle():
    raw,old=saved();request=DocumentRequest('source.png',raw,'owner','kb','new-doc','new-version',parser_engine='mineru-basic')
    parser=SavedMinerUReceiptParser([old]);parsed=parser(request)
    assert parsed.content==old['raw_ocr_text'] and parsed.metadata['mineru_source']['raw_sdk_sidecars_available'] is False
    assert parsed.metadata['mineru_source']['historical_middle']==old['mineru_source']['middle']
    assert not any(a.path=='mineru/middle_json.json' for a in parsed.assets)
    registry=ParserRegistry();registry.register('mineru-basic',('.png',),parser)
    ingested=ingest_document(request,registry)
    metadata={'sha256':sha(raw),'parser_metadata':parsed.metadata,
        'assets':[{'logical_path':a.path,'sha256':sha(a.data)} for a in parsed.assets]}
    for chunk in ingested.chunks:
        proof=public_chunk_locators(chunk.metadata,metadata,'new-doc','new-version',chunk.start,chunk.end,chunk.content)
        assert proof['source_locators'] and proof['parser_evidence']['middle']['logical_path']=='mineru/saved_receipt_projection.json'
    assert old['mineru_source']['document_id']=='historical-doc'


@pytest.mark.parametrize('mutation',['hash','offset','gold','wrong-image'])
def test_saved_ocr_tampering_never_becomes_completed_projection(mutation):
    raw,row=saved()
    if mutation=='hash':row['raw_ocr_text']+=' changed'
    elif mutation=='offset':row['source_locators'][0]['start']=2
    elif mutation=='gold':row['gold_was_sent_to_parser']=True
    else:raw=b'other source'
    with pytest.raises(ValueError):SavedMinerUReceiptParser([row])(DocumentRequest('s.png',raw))


def test_normal_registry_batch_config_preserves_embedding_identity():
    role=ConfiguredRole('embedding','siliconflow','BAAI/bge-m3','https://api.siliconflow.cn/v1','synthetic',{},source='remote')
    base=SourceCatalogue();old=base.resolve(role,MODEL_ID);new=BoundedEmbeddingCatalogue(base).resolve(role,MODEL_ID)
    assert new.model_id==old.model_id and new.model==old.model and new.base_url==old.base_url and new.dimensions==1024
    assert new.embedding.max_batch_size==16 and old.embedding.max_batch_size==32


def test_native_backoff_does_not_resend_uncertain_embedding():
    class RealBoundaryShape:
        calls=0
        async def batch_embed_with_pool(self,texts,*,attempt):
            self.calls+=1;raise TimeoutError('synthetic unknown')
    base=RealBoundaryShape()
    with pytest.raises(IndexBoundaryDenied):asyncio.run(batch_embed_with_backoff(NoRetryEmbedder(base),['body']))
    assert base.calls==1


def ledger_fixture(tmp_path):
    row={'dataset':'hotpotqa','knowledge_base_id':'kb','source_sha256':'a'*64,'status':'NEW_SOURCE',
        'embedding_calls_upper':1,'token_reservation_upper':5000}
    plan={'subject_sha256':sha(b'synthetic-owner'),'sources':[row]}
    filename=tmp_path/'preexisting-execution.json'
    model={'provider':'siliconflow','model':'BAAI/bge-m3','model_id':MODEL_ID,'capability':'embedding'}
    ledger={'status':'OPEN','tenant_id':332,'subject_id':'synthetic-owner','max_calls':1,'max_tokens':5000,
        'allowed_kbs':['kb'],'allowed_models':[model],'allowed_request_prefixes':['*'],'entries':[]}
    write(filename,ledger)
    parent={'schema':'s8-overnight-owner-authorization/v1','status':'OPEN','owner_goal_sha256':'synthetic-only',
        'plan_sha256':sha(canonical(plan).encode()),'tenant_id':332,'subject_sha256':plan['subject_sha256'],
        'executions':{source_key(row):str(filename)},'max_calls':1,'max_tokens':5000}
    auth=tmp_path/'preexisting-parent.json';write(auth,parent)
    return plan,row,filename,ledger,auth,parent


def test_frozen_jobset_and_scope_not_renewable(tmp_path):
    plan,row,filename,ledger,auth,parent=ledger_fixture(tmp_path)
    p,c,t=parent_state(auth,plan)
    assert verify_execution(p,plan,row,SimpleNamespace(subject_id='synthetic-owner'),c,t)==filename
    parent['executions']['unexpected']='not-a-new-grant';write(auth,parent)
    with pytest.raises(ValueError,match='EXACT_FROZEN_SOURCE'):parent_state(auth,plan)
    parent['executions'].pop('unexpected');parent['status']='CLOSED';write(auth,parent)
    with pytest.raises(ValueError,match='AUTHORIZATION'):parent_state(auth,plan)


def test_unknown_occupancy_is_untouched_and_refuses_more_sends(tmp_path):
    plan,row,filename,ledger,auth,parent=ledger_fixture(tmp_path)
    model=ledger['allowed_models'][0];request_id='unknown-operation'
    ledger.update(status='STOP',entries=[{**model,'id':sha(json.dumps([request_id,'embedding']).encode()),
        'request_id':request_id,'wire_sha256':'b'*64,'reserved_tokens':1024,'held_tokens':1024,'status':'UNKNOWN'}])
    write(filename,ledger);before=filename.read_bytes()
    with pytest.raises(ValueError,match='UNKNOWN_OR_INFLIGHT'):parent_state(auth,plan)
    assert filename.read_bytes()==before


def test_single_instance_lock_and_old_ocr_not_reused(tmp_path):
    with single_instance(tmp_path/'run.lock'):
        with pytest.raises((OSError,BlockingIOError)):
            with single_instance(tmp_path/'run.lock'):pass
    assert not is_current_projection({'parse_status':'completed','enable_status':'enabled','metadata':{}},
        {'dataset':'omnidocbench','parsed_sha256':'x'})
