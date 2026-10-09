"""Owner-bound P4 Hybrid closure. Default plans only; no probe, retries or answer.

Live uses normal providers/budget, one exact embedding batch and one rerank.
Real vectors stay bound to input hashes and the full profile; SQL rolls back.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import verify_p4_rerank_once as once
from scripts.verify_p4_rag import table_counts
from backend.app.ports.providers import EmbeddingResult, ProviderRequestNotSent
from backend.app.ports.model_access import model_access, scope_allows
from backend.app.domain.models import NormalizedDocument
from backend.app.domain.adaptive_chunking import ChunkingConfig, prepare_document
from backend.app.domain.fusion import rrf_fuse

REQUEST_ID='p4-hybrid-joint-r1'
PREP=ROOT/'var/reports/p4-hybrid-closure-r1'
LIVE=PREP/'live'
TOKENIZER=Path(r'D:\RAG-ModelAssets\bge-m3\5617a9f61b028005a4858fdac845db406aefb181\tokenizer.json')
ECHO=once.OUT/'live-receipt.json'
ECHO_SHA='4ec0df561959fd597f57d6feb33f37b8361cfe32ea809bd3e7ad68c97053d326'
ECHO_PG=once.PG_OUT/'live-receipt.json'
# Fixed at plan time from the existing successful real PG receipt.
ECHO_PG_SHA='d2c085ef19d0fd0d1247772d45706ce955a5ae86e8ac27e8a8debeaef7c03b2b'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def text_hash(value):return sha(value.encode('utf-8'))

def prepared_fixture():
    document=NormalizedDocument(document_id='synthetic-doc',version_id='synthetic-version',
        title='SYNTHETIC P4',media_type='text/plain',markdown_content=once.PG_CORPUS,
        content_sha256=text_hash(once.PG_CORPUS),parser_version='SIMULATED-NORMALIZATION/p4')
    return prepare_document(document,ChunkingConfig())

def design(admission):
    prepared=prepared_fixture()
    inputs=[c.embedding_content for c in prepared.children]+[once.PG_QUERY]
    tokens=admission.count(inputs)
    if tokens+256>20000:raise ValueError('BATCH_TOKEN_AUTHORIZATION_EXCEEDED')
    payload=json.dumps(dict(model='BAAI/bge-m3',input=inputs,encoding_format='float')).encode('utf-8')
    return dict(request_id=REQUEST_ID,synthetic_corpus_sha256=once.PG_CORPUS_SHA256,
        query_sha256=text_hash(once.PG_QUERY),child_count=len(prepared.children),parent_count=len(prepared.parents),
        embedding_input_sha256=[text_hash(t) for t in inputs],embedding_body_sha256=sha(payload),
        embedding_body_bytes=len(payload),embedding_tokens=tokens,tokenizer_identity=admission.tokenizer_identity,
        chunking_identity=ChunkingConfig().identity,max_embedding_requests=1,max_rerank_requests=1,
        request_timeout_seconds=30,retries=0,monthly_cap_microunits=10,reservation_each_microunits=1,
        weights=dict(vector=.7,keyword=.3),rrf_k=60,return_documents=True,query_cache='same-real-response-only',
        total_body_limit=20000), inputs, payload


def history():
    old=once.read_history(once.HISTORY)
    for path,digest,status in [(ECHO,ECHO_SHA,'PROBE_PASS'),(ECHO_PG,ECHO_PG_SHA,'PG_ONLY_PASS')]:
        raw=path.read_bytes();v=json.loads(raw)
        if sha(raw)!=digest or v.get('status')!=status or v.get('evidence_kind')!='REAL':
            raise ValueError('FIVE_RERANK_HISTORY_CHANGED')
    return dict(attempts=5,body_bytes=2641,settlement='UNKNOWN',old=old,
                receipts={str(p.relative_to(ROOT)):h for p,h in [(ECHO,ECHO_SHA),(ECHO_PG,ECHO_PG_SHA)]})


def validate_approval(value, plan):
    expected=dict(approved=True,request_id=REQUEST_ID,provider='siliconflow',embedding_model='BAAI/bge-m3',
        rerank_model=once.MODEL,embedding_body_sha256=plan['embedding_body_sha256'],
        embedding_body_bytes=plan['embedding_body_bytes'],synthetic_corpus_sha256=once.PG_CORPUS_SHA256,
        max_embedding_requests=1,max_rerank_requests=1,timeout_seconds=30,retries=0,return_documents=True,
        currency='CNY',max_cost='0',free_embedding_price_verified=True,free_rerank_price_verified=True,
        owner_call_authorized=True,account_initial_state='UNKNOWN',pricing_source=once.PRICING_SOURCE)
    if not isinstance(value,dict) or set(value)!=set(expected)|{'owner_reference','pricing_verified_at'}:
        raise ValueError('EXACT_HYBRID_APPROVAL_REQUIRED')
    if any(type(value[k]) is not type(v) or value[k]!=v for k,v in expected.items()):
        raise ValueError('EXACT_HYBRID_APPROVAL_REQUIRED')
    if not isinstance(value['owner_reference'],str) or not 1<=len(value['owner_reference'])<=200:
        raise ValueError('OWNER_REFERENCE_REQUIRED')
    try:age=datetime.now(timezone.utc)-datetime.fromisoformat(value['pricing_verified_at'].replace('Z','+00:00'))
    except (ValueError,TypeError,AttributeError):raise ValueError('CURRENT_FREE_PRICE_REQUIRED') from None
    if not 0<=age.total_seconds()<=86400:raise ValueError('CURRENT_FREE_PRICE_REQUIRED')


class ExactBatchTransport:
    def __init__(self,record,save,send,body):
        self.record,self.save,self.send,self.body=record,save,send,body
        self.lock=threading.Lock();self.consumed=False
    def __call__(self,request,timeout):
        with self.lock:
            if self.consumed or self.record['attempts']:raise ProviderRequestNotSent('HYBRID_EMBEDDING_ALREADY_CONSUMED')
            if request.full_url!='https://api.siliconflow.cn/v1/embeddings' or request.data!=self.body or timeout!=30:
                raise ProviderRequestNotSent('HYBRID_EXACT_EMBEDDING_REQUIRED')
            self.consumed=True
            self.record['attempts'].append(dict(state='consumed_before_transport',request_body_sha256=sha(request.data),request_body_bytes=len(request.data)))
            self.save('before_embedding_transport')
        try:
            result=self.send(request,timeout)
            self.record['http_status_code']=getattr(result,'http_status_code',None)
            self.record['response_bytes']=getattr(result,'response_bytes',None)
            return result
        except Exception as exc:
            from urllib.error import HTTPError
            if isinstance(exc,HTTPError):self.record['http_status_code']=exc.code
            raise


class VerifiedQueryCache:
    """Exact in-process reuse of the validated batch response, never fabricated."""
    def __init__(self,adapter,result,inputs,profile_id,provenance):
        if (result.model!=adapter.embedding_model or result.dimensions!=adapter.embedding_dimension
                or result.identity_fingerprint!=adapter.identity.fingerprint or len(result.vectors)!=len(inputs)
                or inputs[-1]!=once.PG_QUERY or provenance not in {'REAL','SIMULATED'}):
            raise ValueError('VERIFIED_VECTOR_IDENTITY_INVALID')
        if any(len(v)!=result.dimensions or not any(v) or any(type(n) not in (int,float) or not math.isfinite(n) for n in v) for v in result.vectors):
            raise ValueError('VERIFIED_VECTOR_VALUE_INVALID')
        self.adapter,self.result,self.profile_id=adapter,result,str(profile_id)
        self.query_hash=text_hash(inputs[-1]);self.last_cache_hit=False;self.hits=0
        self.fingerprint=result.identity_fingerprint;self.provenance=provenance
    def __getattr__(self,name):return getattr(self.adapter,name)
    def embed(self,texts,timeout_seconds):
        if not scope_allows('embedding'):raise ProviderRequestNotSent('CACHED_EMBEDDING_SCOPE_DENIED')
        if list(texts)!=[once.PG_QUERY] or text_hash(once.PG_QUERY)!=self.query_hash or self.adapter.identity.fingerprint!=self.fingerprint:
            raise ProviderRequestNotSent('CACHED_EMBEDDING_IDENTITY_DENIED')
        self.hits+=1;self.last_cache_hit=True
        return EmbeddingResult([list(self.result.vectors[-1])],self.result.model,self.result.dimensions,0.,
            self.profile_id,self.fingerprint,None)


class ObservedRepository:
    def __init__(self,repo,channels):self.repo,self.channels=repo,channels
    def __getattr__(self,name):return getattr(self.repo,name)
    def keyword_candidates(self,*args,**kwargs):
        values=self.repo.keyword_candidates(*args,**kwargs);self.channels['keyword']=values;return values
    def vector_candidates(self,*args,**kwargs):
        values=self.repo.vector_candidates(*args,**kwargs);self.channels['vector']=values;return values


def verify_fusion(channels,hits,config):
    if set(channels)!={'keyword','vector'} or not all(channels.values()):raise ValueError('BOTH_REAL_CHANNELS_REQUIRED')
    if config.get('source_weights')!={'vector':.7,'keyword':.3} or config.get('rrf_k')!=60:
        raise ValueError('FROZEN_WEIGHTED_RRF_REQUIRED')
    expected=rrf_fuse(channels,k=config['rrf_k'],source_weights=config['source_weights'])
    if list(hits)!=expected:raise ValueError('ACTUAL_RRF_ORDER_MISMATCH')
    contributions={hit.chunk_id:{} for hit in hits}
    for name,rows in channels.items():
        for hit in rows:contributions[hit.chunk_id][name]=config['source_weights'][name]/(config['rrf_k']+hit.rank)
    for hit in hits:
        if not math.isclose(sum(contributions[hit.chunk_id].values()),hit.fused_score,rel_tol=1e-12):
            raise ValueError('RRF_CONTRIBUTION_MISMATCH')
    if not any(set(v)=={'vector','keyword'} for v in contributions.values()):raise ValueError('SHARED_CONTRIBUTION_REQUIRED')
    return contributions


class ObservedRanker:
    def __init__(self,ranker,channels,config,record):self.ranker,self.channels,self.config,self.record=ranker,channels,config,record
    def __getattr__(self,name):return getattr(self.ranker,name)
    def rank(self,question,hits,chunks):
        self.record['channel_rankings']={n:[h.model_dump(mode='json') for h in rows] for n,rows in self.channels.items()}
        self.record['rrf_contributions']=verify_fusion(self.channels,hits,self.config)
        self.record['rrf_before_rerank']=[h.model_dump(mode='json') for h in hits]
        return self.ranker.rank(question,hits,chunks)


def check_joint(result):
    if result.retrieval_mode!='hybrid' or result.degradation_flags or set(result.sources)!={'vector','keyword'}:
        raise ValueError('SILENT_KEYWORD_FALLBACK_REJECTED')
    if not result.embedding_cache_hit or result.effective_config.get('rerank_status')!='RERANKED':
        raise ValueError('REAL_JOINT_STAGE_MISSING')


def execute(approval,admission):
    plan,inputs,body=design(admission);validate_approval(approval,plan)
    return _execute(approval,admission,LIVE,'REAL',lambda:once.live_container(REQUEST_ID,LIVE,retrieval_mode='hybrid',embedding_admission=admission))


def execute_simulated(approval,admission,output,factory):
    if factory is None or Path(output).resolve()==LIVE.resolve():raise ValueError('SIMULATED_FACTORY_OUTPUT_REQUIRED')
    return _execute(approval,admission,Path(output),'SIMULATED',factory)


def _execute(approval,admission,output,evidence_kind,factory):
    plan,inputs,body=design(admission);validate_approval(approval,plan)
    previous=history()
    # Exclusive identity, before credentials/DB/network. Never relocate/retry.
    output.mkdir(exist_ok=False)
    record=dict(task=REQUEST_ID,status='STARTED',evidence_kind=evidence_kind,plan=plan,history=previous,
        approval_sha256=sha(json.dumps(approval,sort_keys=True).encode()),embedding=dict(attempts=[]),
        rerank=dict(attempts=[],history={'body_bytes':previous['body_bytes']+len(body)}),
        actual_cost='UNKNOWN',settlement='UNKNOWN',other_model_requests=0)
    receipt=output/'live-receipt.json'
    with receipt.open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)
    persistence=None
    def save(stage):
        nonlocal persistence
        temporary=receipt.with_suffix('.tmp');operation='write'
        try:
            with temporary.open('x',encoding='utf-8') as f:
                json.dump(record,f,indent=2);f.flush();operation='fsync';os.fsync(f.fileno())
            operation='replace';os.replace(temporary,receipt)
        except Exception as exc:
            persistence=once.ReceiptPersistenceFailure(stage,operation,type(exc).__name__)
            raise persistence from exc
    embedding=ranker=None
    try:
        from sqlalchemy import text
        from backend.tests.test_p4_rag_postgres import seed_p3_fixture
        from backend.app.domain.scope import Scope
        from backend.app.application.citations import CitationService,InMemoryCitationStore
        with factory() as (container,identity):
            record['database_identity']=identity
            engine,repo=container.engine,container.store
            embedding,ranker=repo.embedding_provider,container.knowledge_gateway.retriever.ranker
            with engine.connect() as c:
                before=table_counts(c)
                if any(v for k,v in before.items() if k!='model_calls'):raise ValueError('CLEAN_SLATE_UNEXPECTED_BUSINESS_DATA')
                used=int(c.execute(text("SELECT COALESCE(SUM(CASE WHEN reservation_state IN ('reserved','unknown') THEN reserved_cost_microunits WHEN reservation_state='settled' THEN COALESCE(settled_cost_microunits,0) ELSE 0 END),0) FROM model_calls WHERE budget_month=date_trunc('month',CURRENT_DATE)::date")).scalar_one())
                if 10-used<2:raise ValueError('TWO_NORMAL_RESERVATIONS_REQUIRED')
                record['budget_before']=dict(cap_microunits=10,used_microunits=used,required_microunits=2)
            embedding.max_requests=1
            embedding.transport=ExactBatchTransport(record['embedding'],save,embedding.transport,body)
            original=repo.engine
            with engine.connect() as c:
                tx=c.begin()
                class TransactionEngine:
                    @contextmanager
                    def connect(self):yield c
                    @contextmanager
                    def begin(self):
                        with c.begin_nested():yield c
                try:
                    repo.engine=TransactionEngine()
                    kb,upload,source,children,parents=seed_p3_fixture(repo,c)
                    scope=Scope.from_ids([kb['id']])
                    seeds=repo.get_retrieval_chunks(scope,children)
                    if source!=once.PG_CORPUS or set(seeds)!=set(children):raise ValueError('FROZEN_SOURCE_SCOPE_MISMATCH')
                    headers={str(r['id']):r['context_header'] for r in c.execute(text('SELECT id,context_header FROM chunks WHERE id=ANY(CAST(:ids AS uuid[])) AND version_id=:version'),dict(ids=children,version=upload['version_id'])).mappings()}
                    actual_inputs=[(headers[key]+'\n\n'+seeds[key].content.strip()) if headers[key] else seeds[key].content.strip() for key in children]+[once.PG_QUERY]
                    if actual_inputs!=inputs:raise ValueError('CHILD_HEADER_BATCH_MISMATCH')
                    if any(seed.content!=source[seed.locator['start']:seed.locator['end']] for seed in seeds.values()):raise ValueError('ORIGINAL_CHILD_LOCATOR_MISMATCH')
                    with model_access('embedding',allowed=repo.embedding_scope_allowed(scope)):
                        vectors=embedding.embed(inputs,30)
                    repo.validate_embedding_result(vectors)
                    profile=repo._get_or_create_embedding_profile(c,repo.embedding_identity())
                    for key,vector in zip(children,vectors.vectors[:-1],strict=True):
                        c.execute(text('INSERT INTO chunk_embeddings(chunk_id,profile_id,embedding) VALUES (:id,:profile,CAST(:v AS vector))'),dict(id=key,profile=profile,v=json.dumps(vector)))
                    cache=VerifiedQueryCache(embedding,vectors,inputs,profile,evidence_kind)
                    vector_evidence=dict(evidence_kind=evidence_kind,request_body_sha256=plan['embedding_body_sha256'],input_sha256=plan['embedding_input_sha256'],identity=asdict(embedding.identity),fingerprint=embedding.identity.fingerprint,profile_id=str(profile),vectors=vectors.vectors,usage_actual=vectors.usage_actual)
                    cache_path=output/('real-vectors.json' if evidence_kind=='REAL' else 'SIMULATED-vectors.json')
                    with cache_path.open('x',encoding='utf-8') as f:json.dump(vector_evidence,f);f.flush();os.fsync(f.fileno())
                    record['vector_cache']=dict(path=str(cache_path),sha256=sha(cache_path.read_bytes()),profile_id=str(profile),identity=asdict(embedding.identity),fingerprint=embedding.identity.fingerprint,input_sha256=plan['embedding_input_sha256'])
                    if c.execute(text("SELECT count(*) FROM chunk_embeddings ce JOIN chunks c ON c.id=ce.chunk_id WHERE c.chunk_role='parent'")).scalar_one()!=0:raise ValueError('PARENT_INDEXED')
                    if repo.vector_candidates(Scope.from_ids([str(uuid.uuid4())]),vectors.vectors[-1],32,profile_id=str(profile)):raise ValueError('CROSS_SCOPE_VECTOR')
                    if repo.vector_candidates(scope,vectors.vectors[-1],32,profile_id=str(uuid.uuid4())):raise ValueError('CROSS_PROFILE_VECTOR')
                    channels={};retriever=container.knowledge_gateway.retriever
                    config=retriever.effective_config();record['effective_config']=config
                    retriever.repository=ObservedRepository(repo,channels);retriever.embedding_provider=cache
                    ranker.transport=once.OnceTransport(record['rerank'],lambda:save('before_rerank_transport'),ranker.transport,frozenset(s.content for s in seeds.values()),pg_only=True)
                    retriever.ranker=ObservedRanker(ranker,channels,config,record)
                    result=retriever.retrieve(scope,once.PG_QUERY);check_joint(result)
                    if len(ranker.receipts)!=1 or ranker.last_result is None:raise ValueError('EXACTLY_ONE_REAL_RERANK_REQUIRED')
                    citations=CitationService(InMemoryCitationStore());service=container.knowledge_gateway.evidence
                    bundle=service.with_context(service.bundle(service.plan(once.PG_QUERY),result),REQUEST_ID,citations)
                    if not bundle.snapshots or result.retrieval_stats['parent_count']<=0:raise ValueError('PARENT_CONTEXT_OR_CITATION_MISSING')
                    for snap in bundle.snapshots:
                        if snap.chunk_id not in children or snap.quote!=source[snap.locator['start']:snap.locator['end']] or snap.quote_sha256!=text_hash(snap.quote):raise ValueError('CITATION_ORIGINAL_SOURCE_MISMATCH')
                    record['pipeline']=dict(retrieval_mode=result.retrieval_mode,sources=result.sources,degradation_flags=result.degradation_flags,reranked=[h.model_dump(mode='json') for h in result.fused_ranking],relevance_scores=ranker.last_result.relevance_scores,scope=dict(knowledge_base_ids=sorted(scope.knowledge_base_ids),document_ids=sorted(scope.document_ids)),upload=upload,children=[dict(id=k,parent_id=seeds[k].parent_id,version_id=seeds[k].version_id,index_identity=seeds[k].index_identity,content_sha256=seeds[k].content_sha256,locator=seeds[k].locator) for k in children],parents=parents,stats=result.retrieval_stats,context=bundle.context,labels=bundle.labels,snapshots=[dict(chunk_id=s.chunk_id,version_id=s.version_id,locator=s.locator,quote_sha256=s.quote_sha256) for s in bundle.snapshots],evidence_stats=bundle.evidence_stats,query_cache_hits=cache.hits)
                finally:tx.rollback();repo.engine=original
            with engine.connect() as c:after=table_counts(c)
            record['counts_before']=before;record['counts_after']=after
            if any(v!=after[k] for k,v in before.items() if k!='model_calls') or after['model_calls']!=before['model_calls']+(2 if evidence_kind=='REAL' else 0):raise ValueError('ROLLBACK_OR_NORMAL_LEDGER_MISMATCH')
            record['status']='HYBRID_JOINT_PASS'
        if history()!=previous:raise ValueError('HISTORY_DRIFT')
    except Exception as exc:
        record.update(status='BLOCKED',error_type=type(exc).__name__)
        # Only explicitly fixed local errors may be exported.
        if type(exc) is ValueError and str(exc) in {'TWO_NORMAL_RESERVATIONS_REQUIRED','SILENT_KEYWORD_FALLBACK_REJECTED','CHILD_HEADER_BATCH_MISMATCH','ROLLBACK_OR_NORMAL_LEDGER_MISMATCH'}:record['error_code']=str(exc)
    finally:
        record['embedding']['receipts']=embedding.receipts if embedding is not None else []
        record['rerank']['receipts']=ranker.receipts if ranker is not None else []
        if persistence is None:
            try:save('final_receipt')
            except once.ReceiptPersistenceFailure:pass
        if persistence is not None:raise persistence
    return 0 if record['status']=='HYBRID_JOINT_PASS' else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-owner-authorized-once',action='store_true')
    parser.add_argument('--authorization-file',type=Path)
    args=parser.parse_args(argv)
    if not args.execute_owner_authorized_once:
        print(json.dumps(dict(status='NOT_RUN',request_id=REQUEST_ID,max_embedding_requests=1,max_rerank_requests=1,probe_requests=0)))
        return 0
    try:
        if args.authorization_file is None:raise ValueError('EXACT_HYBRID_APPROVAL_REQUIRED')
        from backend.app.adapters.models.cloud import EmbeddingAdmission
        code=execute(json.loads(args.authorization_file.read_text(encoding='utf-8-sig')),EmbeddingAdmission.from_bge_m3_file(TOKENIZER))
    except Exception as exc:
        print(json.dumps(dict(status='BLOCKED',error_type=type(exc).__name__)))
        return 1
    print(json.dumps(dict(exit_code=code,receipt=str(LIVE/'live-receipt.json'))));return code

if __name__=='__main__':raise SystemExit(main())
