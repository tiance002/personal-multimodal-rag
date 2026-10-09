"""SIMULATED transport/cache; normal Hybrid and safety contracts, zero live I/O."""
from dataclasses import replace
from datetime import datetime,timezone
import hashlib,json
from types import SimpleNamespace
from urllib.request import Request
import pytest
from scripts import verify_p4_hybrid_once as hybrid
from scripts import verify_p4_rerank_once as once
from backend.app.adapters.models.cloud import EmbeddingAdmission
from backend.app.domain.embedding_identity import EmbeddingIdentity
from backend.app.domain.adaptive_chunking import ChunkingConfig
from backend.app.domain.fusion import rrf_fuse
from backend.app.domain.models import RankedHit
from backend.app.ports.providers import EmbeddingResult,ProviderRequestNotSent
from backend.app.ports.model_access import model_access

@pytest.fixture
def admission():
    return EmbeddingAdmission(lambda text: max(1,len(text)//4),tokenizer_identity='SIMULATED',non_truncating_verified=True)

def approval(plan):
    return dict(approved=True,request_id=hybrid.REQUEST_ID,provider='siliconflow',embedding_model='BAAI/bge-m3',
        rerank_model=once.MODEL,embedding_body_sha256=plan['embedding_body_sha256'],embedding_body_bytes=plan['embedding_body_bytes'],
        synthetic_corpus_sha256=once.PG_CORPUS_SHA256,max_embedding_requests=1,max_rerank_requests=1,timeout_seconds=30,retries=0,
        return_documents=True,currency='CNY',max_cost='0',free_embedding_price_verified=True,free_rerank_price_verified=True,
        owner_call_authorized=True,account_initial_state='UNKNOWN',pricing_source=once.PRICING_SOURCE,
        owner_reference='SIMULATED_ONLY',pricing_verified_at=datetime.now(timezone.utc).isoformat())

def test_frozen_batch_has_all_exact_child_headers_and_query(admission):
    plan,inputs,body=hybrid.design(admission)
    assert inputs==[c.embedding_content for c in hybrid.prepared_fixture().children]+[once.PG_QUERY]
    assert plan['child_count']==5 and plan['parent_count']==1
    assert plan['embedding_input_sha256']==[hybrid.text_hash(t) for t in inputs]
    assert hashlib.sha256(body).hexdigest()==plan['embedding_body_sha256']
    assert json.loads(body)==dict(model='BAAI/bge-m3',input=inputs,encoding_format='float')
    assert plan['return_documents'] is True and plan['reservation_each_microunits']==1
    assert hybrid.history()['attempts']==5 and hybrid.history()['body_bytes']==2641

@pytest.mark.parametrize('field,value', [('approved',False),('owner_call_authorized',False),('free_embedding_price_verified',False),
    ('free_rerank_price_verified',False),('return_documents',False),('embedding_model','Pro/BAAI/bge-m3'),
    ('max_embedding_requests',True),('max_rerank_requests',2),('max_cost','1'),('embedding_body_sha256','0'*64),
    ('account_initial_state',True),('timeout_seconds',31),('retries',1)])
def test_approval_denies_before_factory(admission,tmp_path,field,value):
    grant=approval(hybrid.design(admission)[0]);grant[field]=value
    with pytest.raises(ValueError):hybrid.execute_simulated(grant,admission,tmp_path/'out',lambda:pytest.fail('FACTORY_ENTERED'))
    assert not (tmp_path/'out').exists()

@pytest.mark.parametrize('field',['owner_reference','pricing_verified_at','free_embedding_price_verified','free_rerank_price_verified'])
def test_missing_approval_zero_send(admission,tmp_path,field):
    grant=approval(hybrid.design(admission)[0]);del grant[field]
    with pytest.raises(ValueError):hybrid.execute_simulated(grant,admission,tmp_path/'out',lambda:pytest.fail('FACTORY_ENTERED'))
    assert not (tmp_path/'out').exists()

@pytest.mark.parametrize('kind',['success','http','timeout','persist'])
def test_exact_batch_transport_consumes_at_most_once(admission,kind):
    record,sends,saves={'attempts':[]},[],[];body=hybrid.design(admission)[2]
    def save(stage):
        saves.append(stage)
        if kind=='persist':raise OSError('SIMULATED_PERSISTENCE')
    def send(*args):
        sends.append(True)
        if kind=='http':raise RuntimeError('SIMULATED_HTTP_FAILURE')
        if kind=='timeout':raise TimeoutError('SIMULATED_TIMEOUT')
        return SimpleNamespace(http_status_code=200,response_bytes=1)
    transport=hybrid.ExactBatchTransport(record,save,send,body)
    request=Request('https://api.siliconflow.cn/v1/embeddings',data=body)
    if kind=='success':transport(request,30)
    else:
        with pytest.raises((OSError,RuntimeError,TimeoutError)):transport(request,30)
    with pytest.raises(ProviderRequestNotSent):transport(request,30)
    assert len(record['attempts'])==len(saves)==1 and len(sends)==(0 if kind=='persist' else 1)
    assert record['attempts'][0]['request_body_sha256']==hybrid.sha(body)

@pytest.mark.parametrize('kind',['body','endpoint','timeout'])
def test_batch_mismatch_is_zero_send(admission,kind):
    body=hybrid.design(admission)[2];record={'attempts':[]};calls=[]
    transport=hybrid.ExactBatchTransport(record,lambda *a:calls.append('save'),lambda *a:calls.append('send'),body)
    request=Request('https://api.siliconflow.cn/v1/embeddings',data=body)
    if kind=='body':request.data+=b' '
    if kind=='endpoint':request.full_url='https://invalid.test/embeddings'
    with pytest.raises(ProviderRequestNotSent):transport(request,29 if kind=='timeout' else 30)
    assert calls==record['attempts']==[]

def cache_fixture():
    identity=EmbeddingIdentity('siliconflow','BAAI/bge-m3','UNKNOWN',1024,'cosine',ChunkingConfig().identity)
    adapter=SimpleNamespace(identity=identity,embedding_model=identity.model_id,embedding_dimension=1024)
    result=EmbeddingResult([[1.]+[0.]*1023],identity.model_id,1024,1.,identity_fingerprint=identity.fingerprint)
    return adapter,result

def test_real_response_query_reuse_keeps_identity_and_scope():
    adapter,result=cache_fixture();cache=hybrid.VerifiedQueryCache(adapter,result,[once.PG_QUERY],'profile','SIMULATED')
    with model_access('embedding',allowed=False):
        with pytest.raises(ProviderRequestNotSent):cache.embed([once.PG_QUERY],10)
    with model_access('embedding',allowed=True):
        with pytest.raises(ProviderRequestNotSent):cache.embed(['other'],10)
        actual=cache.embed([once.PG_QUERY],10)
    assert actual.vectors==result.vectors and actual.profile_id=='profile'
    assert actual.identity_fingerprint==adapter.identity.fingerprint and actual.usage_actual is None
    assert cache.hits==1 and cache.last_cache_hit

@pytest.mark.parametrize('kind',['model','dimension','fingerprint','count','nan','boolean','zero'])
def test_bad_cached_identity_or_vector_rejected(kind):
    adapter,result=cache_fixture()
    if kind=='model':result=replace(result,model='other')
    if kind=='dimension':result=replace(result,dimensions=1)
    if kind=='fingerprint':result=replace(result,identity_fingerprint='other')
    if kind=='count':result=replace(result,vectors=[])
    if kind=='nan':result.vectors[0][0]=float('nan')
    if kind=='boolean':result.vectors[0][0]=True
    if kind=='zero':result.vectors[0][0]=0.
    with pytest.raises(ValueError):hybrid.VerifiedQueryCache(adapter,result,[once.PG_QUERY],'p','SIMULATED')

@pytest.mark.parametrize('reverse',[False,True])
def test_each_channel_contributes_to_actual_weighted_rrf(reverse):
    keyword=[RankedHit(chunk_id='a',rank=1),RankedHit(chunk_id='b',rank=2)]
    vector=[RankedHit(chunk_id=id,rank=n) for n,id in enumerate(['b','a'] if reverse else ['a','b'],1)]
    channels=dict(keyword=keyword,vector=vector);config=dict(rrf_k=60,source_weights=dict(vector=.7,keyword=.3))
    hits=rrf_fuse(channels,60,source_weights=config['source_weights'])
    contribution=hybrid.verify_fusion(channels,hits,config)
    assert all(set(v)=={'keyword','vector'} and all(n>0 for n in v.values()) for v in contribution.values())
    assert all(sum(contribution[h.chunk_id].values())==h.fused_score for h in hits)
    with pytest.raises(ValueError):hybrid.verify_fusion({'keyword':keyword},hits,config)

@pytest.mark.parametrize('mode,flags,sources,cache,status',[
    ('keyword',(),('keyword',),True,'RERANKED'),('hybrid',('VECTOR_UNAVAILABLE',),('keyword','vector'),True,'RERANKED'),
    ('hybrid',(),('keyword',),True,'RERANKED'),('hybrid',(),('keyword','vector'),False,'RERANKED'),
    ('hybrid',(),('keyword','vector'),True,'FUSION_FALLBACK')])
def test_silent_fallback_never_counts_as_joint_pass(mode,flags,sources,cache,status):
    with pytest.raises(ValueError):hybrid.check_joint(SimpleNamespace(retrieval_mode=mode,degradation_flags=flags,sources=sources,
        embedding_cache_hit=cache,effective_config={'rerank_status':status}))

def test_no_probe_default_and_no_real_factory_seam(monkeypatch,capsys,admission,tmp_path):
    monkeypatch.setattr(hybrid,'execute',lambda *a:pytest.fail('LIVE_ENTERED'))
    assert hybrid.main([])==0 and json.loads(capsys.readouterr().out)['max_embedding_requests']==1
    with pytest.raises(ValueError):hybrid.execute_simulated(approval(hybrid.design(admission)[0]),admission,hybrid.LIVE,lambda:None)
