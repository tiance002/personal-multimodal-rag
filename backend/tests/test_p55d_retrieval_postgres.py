"""REAL isolated PostgreSQL, rolled-back synthetic data; SIMULATED models."""
import json,hashlib,uuid,os,time
from pathlib import Path
from contextlib import contextmanager
from dataclasses import replace
import pytest
from sqlalchemy import create_engine,text
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query,term_frequencies
from backend.app.domain.bm25 import score_bm25,bm25_score
from backend.app.application.retrieval import HybridRetriever
from backend.app.application.knowledge_tools import KnowledgeToolGateway,ToolDenied
from backend.tests.test_p55d_retrieval import corpus,FROZEN,legacy_term_frequencies,legacy_query

@pytest.fixture
def pg(tmp_path,request):
    assert os.environ.get('P55B21_ISOLATED_AUTHORIZED')=='p55b21-global-scoped-r1','GUARDED_ISOLATED_RUNNER_REQUIRED'
    engine=create_engine('postgresql+psycopg://p55a:SIMULATED_TEST_ONLY@127.0.0.1:55729/p55a_lifecycle_test',connect_args={'connect_timeout':5})
    with engine.connect() as c:
        assert c.execute(text('SELECT current_database(),oid,(SELECT system_identifier FROM pg_control_system()) FROM pg_database WHERE datname=current_database()')).one()==('p55a_lifecycle_test',16384,7694635764170571814)
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0021_global_scoped_memory'
        # Actual trusted public schema, including authoritative citation tables.
        tables=c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars().all()
        quote=engine.dialect.identifier_preparer.quote
        before={table:c.execute(text(f'SELECT count(*) FROM public.{quote(table)}')).scalar_one() for table in tables}
        c.rollback();tx=c.begin()
        class TransactionEngine:
            @contextmanager
            def connect(self):yield c
            @contextmanager
            def begin(self):
                with c.begin_nested():yield c
        repo=PostgresKnowledgeRepository(TransactionEngine(),ContentAddressedStorage(tmp_path/'SYNTHETIC-STORAGE'))
        try:yield repo,c
        finally:tx.rollback()
        after={table:c.execute(text(f'SELECT count(*) FROM public.{quote(table)}')).scalar_one() for table in before}
        assert after==before
        out=Path(request.config.option.xmlpath).parent if request.config.option.xmlpath else tmp_path
        name=hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
        (out/f'rollback-counts-{name}.json').write_text(json.dumps(dict(test=request.node.nodeid,before=before,after=after,rolled_back=True,identity=['p55a_lifecycle_test',16384,7694635764170571814]),indent=2),encoding='utf8')
    engine.dispose()

def seed(repo,c,records,*,kb=None,legacy=True):
    kb=kb or repo.create_knowledge_base('P55D-SYNTHETIC-'+uuid.uuid4().hex,cloud_allowed=False)['id']
    mapping={};drafts=[]
    for n,row in enumerate(records):
        doc,version,id=map(str,(uuid.uuid4(),uuid.uuid4(),uuid.uuid4()))
        mapping[row.chunk_id]=id
        params=dict(id=id,doc=doc,filename=f'SYNTHETIC-{doc}.txt',version=version,kb=kb,body=row.content,sha=hashlib.sha256(row.content.encode()).hexdigest(),length=len(row.content),identity=repo.chunking_config.identity,kind=row.chunk_type,
                    locator=json.dumps(row.locator),role=row.chunk_role)
        c.execute(text("INSERT INTO documents(id,knowledge_base_id,file_name,media_type,original_size) VALUES (:doc,:kb,:filename,'text/plain',:length)"),params)
        c.execute(text("INSERT INTO document_versions(id,document_id,version_no,source_sha256,storage_key,parser_version,index_status,index_identity) VALUES (:version,:doc,1,:sha,'SYNTHETIC','SIMULATED','ready',:identity)"),params)
        c.execute(text("UPDATE documents SET active_version_id=:version WHERE id=:doc"),params)
        c.execute(text("""INSERT INTO chunks(id,knowledge_base_id,document_id,version_id,chunk_index,content,content_sha256,start_pos,end_pos,locator,chunk_role,index_identity,chunk_type)
            VALUES (:id,:kb,:doc,:version,0,:body,:sha,0,:length,CAST(:locator AS jsonb),:role,:identity,:kind)"""),params)
        for term,tf in (legacy_term_frequencies(row.content) if legacy else term_frequencies(row.content)).items():
            c.execute(text('INSERT INTO chunk_terms(chunk_id,term,term_frequency) VALUES (:id,:term,:tf)'),dict(id=id,term=term,tf=tf))
        drafts.append(replace(row,chunk_id=id,knowledge_base_id=kb,document_id=doc,version_id=version,index_identity=repo.chunking_config.identity,content_sha256=params['sha']))
    return Scope.from_ids([kb]),mapping,drafts

def test_pg_bm25_matches_hand_formula_and_python_all_frozen_queries(pg,request,tmp_path):
    repo,c=pg;data,records=corpus();scope,mapping,drafts=seed(repo,c,records)
    output=[]
    for q in data['queries']:
        query=normalize_query(q['question']);actual=repo.keyword_candidates(scope,query,32);expected=score_bm25(drafts,query,32)
        assert {h.chunk_id:h.raw_score for h in actual}==pytest.approx({h.chunk_id:h.raw_score for h in expected})
        legacy={}
        old_query=legacy_query(q['question'])
        for draft in drafts:
            freq=legacy_term_frequencies(draft.content);score=sum(freq.get(t,0) for t in old_query.terms)+(2 if old_query.normalized and old_query.normalized in draft.content.lower() else 0)
            if score:legacy[draft.chunk_id]=score
        # Execute the pre-D persisted TF formula in the same real PG snapshot.
        params={'normalized':old_query.normalized,'limit':32,'index_identity':repo.chunking_config.identity}
        clause=repo._scope_filter(scope,params);terms=','.join(f':t_{i}' for i in range(len(old_query.terms)))
        params.update({f't_{i}':term for i,term in enumerate(old_query.terms)})
        started=time.perf_counter()
        old_rows=c.execute(text(f'''SELECT c.id AS chunk_id,SUM(ct.term_frequency)+CASE
            WHEN :normalized<>'' AND POSITION(:normalized IN lower(c.content))>0 THEN 2 ELSE 0 END AS score
            FROM chunks c {repo._ACTIVE_VERSION_JOINS}
            JOIN chunk_terms ct ON ct.chunk_id=c.id AND ct.term IN ({terms})
            WHERE {clause} AND d.deleted_at IS NULL AND dv.index_status='ready'
            AND c.chunk_role='child' AND c.index_identity=:index_identity AND dv.index_identity=:index_identity
            GROUP BY c.id ORDER BY score DESC,c.id LIMIT :limit'''),params).mappings().all()
        old_latency_ms=(time.perf_counter()-started)*1000
        assert {str(r['chunk_id']):float(r['score']) for r in old_rows}==legacy
        old=[str(r['chunk_id']) for r in old_rows];new=[h.chunk_id for h in actual];gold={mapping[id] for id in q['expected_chunk_ids']}
        metrics=lambda ids,k:dict(recall=len(set(ids[:k])&gold)/len(gold),mrr=next((1/i for i,id in enumerate(ids[:k],1) if id in gold),0))
        expanded=HybridRetriever(repo,mode='keyword',top_k=3,candidate_k=8).retrieve(scope,q['question'])
        ranks={'tf':old,'bm25':new,'bm25_expansion':[h.chunk_id for h in expanded.fused_ranking]}
        output.append(dict(id=q['id'],query=q['question'],gold=list(gold),rankings=ranks,metrics={m:{str(k):metrics(ids,k) for k in (1,3)} for m,ids in ranks.items()},scores=[h.model_dump(mode='json') for h in actual],expansion=expanded.retrieval_stats['query_expansion'],latency=expanded.stage_latency_ms,old_tf_sql_ms=old_latency_ms))
    assert any(r['rankings']['tf']!=r['rankings']['bm25'] for r in output)
    out=Path(request.config.option.xmlpath).parent if request.config.option.xmlpath else tmp_path
    (out/'pg-ranking-comparison.json').write_text(json.dumps(dict(kind='REAL_PG_BM25_FROZEN_SYNTHETIC_NO_MODEL',fixture_sha256=FROZEN,rows=output,
        aggregate={mode:{str(k):{name:sum(r['metrics'][mode][str(k)][name] for r in output)/len(output) for name in ('recall','mrr')} for k in (1,3)} for mode in ranks}),ensure_ascii=False,indent=2),encoding='utf8')

@pytest.mark.parametrize('invalid',['old','deleted','other_kb','other_document','parent','wrong_identity','version_identity','kb_deleted','not_ready','document_kb_mismatch'])
def test_pg_stats_and_candidates_exclude_invalid_data(pg,invalid):
    repo,c=pg
    scope,_,drafts=seed(repo,c,[ChunkRecord('a','kb','d','v','rare common'),ChunkRecord('b','kb','e','v','common')])
    query=normalize_query('rare common');before=repo.keyword_candidates(scope,query,20)
    bad_scope,_,bad=seed(repo,c,[ChunkRecord('bad','kb','bad','v','rare '*400)],kb=next(iter(scope.knowledge_base_ids)) if invalid!='other_kb' else None)
    row=bad[0]
    if invalid=='old':c.execute(text('UPDATE documents SET active_version_id=NULL WHERE id=:id'),dict(id=row.document_id))
    elif invalid=='deleted':c.execute(text('UPDATE documents SET deleted_at=now() WHERE id=:id'),dict(id=row.document_id))
    elif invalid=='parent':c.execute(text("UPDATE chunks SET chunk_role='parent' WHERE id=:id"),dict(id=row.chunk_id))
    elif invalid=='wrong_identity':c.execute(text("UPDATE chunks SET index_identity='UNAUTHORISED' WHERE id=:id"),dict(id=row.chunk_id))
    elif invalid=='version_identity':c.execute(text("UPDATE document_versions SET index_identity='UNAUTHORISED' WHERE id=:id"),dict(id=row.version_id))
    elif invalid=='not_ready':c.execute(text("UPDATE document_versions SET index_status='failed' WHERE id=:id"),dict(id=row.version_id))
    elif invalid=='document_kb_mismatch':
        kb=repo.create_knowledge_base('P55D-Other')['id']
        c.execute(text('UPDATE documents SET knowledge_base_id=:kb WHERE id=:id'),dict(kb=kb,id=row.document_id))
    elif invalid=='kb_deleted':
        # Move bad document and chunk consistently into a separate deleted KB.
        kb=repo.create_knowledge_base('P55D-Deleted')['id']
        c.execute(text('UPDATE documents SET knowledge_base_id=:kb WHERE id=:id'),dict(kb=kb,id=row.document_id))
        c.execute(text('UPDATE chunks SET knowledge_base_id=:kb WHERE id=:id'),dict(kb=kb,id=row.chunk_id))
        c.execute(text('UPDATE knowledge_bases SET deleted_at=now() WHERE id=:kb'),dict(kb=kb))
        scope=Scope.from_ids([*scope.knowledge_base_ids,kb])
    elif invalid=='other_document':scope=Scope.from_ids(scope.knowledge_base_ids,[d.document_id for d in drafts])
    after=repo.keyword_candidates(scope,query,20)
    assert after==before

def test_pg_legacy_and_new_mixed_terms_same_bm25_without_rewrite(pg):
    repo,c=pg;body='合同A合同A方案成本1000元 API_v2 ZX-102 é方案'
    scope,_,old=seed(repo,c,[ChunkRecord('old','kb','d','v',body)],legacy=True)
    scope,_,new=seed(repo,c,[ChunkRecord('new','kb','e','v',body)],kb=next(iter(scope.knowledge_base_ids)),legacy=False)
    records=old+new
    physical=list(c.execute(text('SELECT chunk_id,term,term_frequency FROM chunk_terms WHERE chunk_id=ANY(CAST(:ids AS uuid[])) ORDER BY chunk_id,term'),{'ids':[r.chunk_id for r in records]}))
    for question in ['A','成本','合同','1000','API_v2','ZX-102','é方案']:
        query=normalize_query(question);actual=repo.keyword_candidates(scope,query,20);expected=score_bm25(records,query,20)
        assert {h.chunk_id:h.raw_score for h in actual}==pytest.approx({h.chunk_id:h.raw_score for h in expected})
        assert len(actual)==2 and actual[0].raw_score==pytest.approx(actual[1].raw_score)
    assert physical==list(c.execute(text('SELECT chunk_id,term,term_frequency FROM chunk_terms WHERE chunk_id=ANY(CAST(:ids AS uuid[])) ORDER BY chunk_id,term'),{'ids':[r.chunk_id for r in records]}))

def test_pg_hybrid_rrf_rerank_parent_context_original_citation(pg,monkeypatch):
    from backend.tests.test_p4_rag_postgres import seed_p3_fixture
    from backend.tests.test_model_provider_contracts import embedding,SimulatedGuard
    from backend.app.domain.model_registry import ModelRegistry
    from backend.app.adapters.models.cloud import SiliconFlowRerank
    from backend.app.application.citations import CitationService,InMemoryCitationStore
    from backend.app.application.context_builder import ContextBuilder
    repo,c=pg;adapter,_=embedding(ModelRegistry.frozen_defaults(),SimulatedGuard())
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-P55D-NOT-A-KEY');repo.embedding_provider=adapter
    kb,receipt,body,children,parents=seed_p3_fixture(repo,c)
    profile=repo._get_or_create_embedding_profile(c,repo.embedding_identity())
    for id in children:
        c.execute(text('INSERT INTO chunk_embeddings(chunk_id,profile_id,embedding) VALUES (:id,:p,CAST(:v AS vector))'),dict(id=id,p=profile,v='['+','.join(['1']*1024)+']'))
    sends=[]
    def transport(request,timeout):
        payload=json.loads(request.data);sends.append(payload)
        assert payload['return_documents'] is True
        return dict(results=[dict(index=i,relevance_score=1-i/100,document={'text':t}) for i,t in enumerate(payload['documents'])])
    ranker=SiliconFlowRerank(ModelRegistry.frozen_defaults().select('rerank'),enabled=True,usage_guard=SimulatedGuard(),transport=transport)
    result=HybridRetriever(repo,adapter,mode='hybrid',ranker=ranker,top_k=1,source_weights={'keyword':.25,'vector':1}).retrieve(Scope.from_ids([kb['id']]),'Synthetic cost')
    assert result.items and set(result.candidate_rankings)=={'keyword','vector'} and len(sends)==1
    assert result.items[0].context_passage.parent_ids
    assert result.retrieval_stats['fused_before_rank'] and result.effective_config['rerank_status']=='RERANKED'
    assert result.retrieval_stats['keyword_scoring'].startswith('BM25')
    service=CitationService(InMemoryCitationStore())
    context,labels=ContextBuilder().build('SIMULATED-P55D',result.items,service);assert body[:100] in context
    for item in result.items:
        snapshot=service.freeze('SIMULATED-P55D',item.chunk)
        assert snapshot.quote==item.chunk.content and snapshot.chunk_id in children and snapshot.version_id==receipt['version_id']

def test_pg_versioned_pages_do_not_create_evidence_and_deny_change(pg):
    repo,c=pg;body=''.join(f'Paragraph {n}: '+'read only '*10+'\n' for n in range(40))
    scope,_,drafts=seed(repo,c,[ChunkRecord('reading','kb','d','v',body)])
    doc=drafts[0].document_id
    gateway=KnowledgeToolGateway(content_reader=repo.get_document_content,document_resolver=repo.get_document_access,document_view_reader=repo.read_document_view,max_document_chars=2000)
    gateway.bind_read_token_counter(lambda data:len(data['content'].split())) # SIMULATED fixture tokenizer
    pages=[];offset=0
    while True:
        data=gateway.invoke('read_document',dict(document_id=doc,offset=offset,length=2000),scope).data
        pages.append(data)
        if data['next_offset'] is None:break
        offset=data['next_offset']
    assert ''.join(d['content'] for d in pages)==body and len(pages)>=3
    assert all(d['version_id']==drafts[0].version_id and d['evidence_status'].startswith('READING_CONTEXT_ONLY') for d in pages)
    c.execute(text('UPDATE documents SET active_version_id=NULL WHERE id=:id'),dict(id=doc))
    with pytest.raises(ToolDenied):gateway.invoke('read_document',dict(document_id=doc,offset=1,length=10),scope)
