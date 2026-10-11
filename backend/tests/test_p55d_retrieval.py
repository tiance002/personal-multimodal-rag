"""SIMULATED providers, frozen labelled SYNTHETIC corpus; zero real requests."""
import json,math,hashlib,re,unicodedata
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.app.domain.bm25 import score_bm25,bm25_score
from backend.app.domain.models import ChunkRecord,RankedHit
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import normalize_query,term_frequencies
from backend.app.application.retrieval import HybridRetriever,InMemoryRetrievalRepository,score_keyword_hits
from backend.app.application.query_expansion import variants,risk_signals
from backend.app.application.knowledge_tools import KnowledgeToolGateway,ToolDenied
from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.ports.query_understand import QueryUnderstanding,QueryTask

FIXTURE=Path(__file__).parent/'fixtures/p55d_retrieval/corpus.json'
FROZEN='dc61ab5c276785c10893ae12942d7d91e926315396d01100e3ebc737cf18ade0'
SCOPE=Scope.from_ids(['SYNTHETIC-KB'])

def legacy_term_frequencies(text):
    """Frozen pre-D lexical rule, used only for old-index/baseline evidence."""
    counts=Counter()
    for match in re.finditer(r'[\w]+|>=|<=|==|!=|[><=]',unicodedata.normalize('NFKC',text).strip().lower()):
        token=match.group();counts[token]+=1
        if re.fullmatch(r'[\u3400-\u9fff]+',token) and len(token)>2:
            counts.update(token[i:i+2] for i in range(len(token)-1))
    return dict(counts)

def legacy_query(question):
    from backend.app.domain.text_normalization import NormalizedQuery
    return NormalizedQuery(q0=question,normalized=unicodedata.normalize('NFKC',question).strip().lower(),terms=tuple(legacy_term_frequencies(question)))

def corpus():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest()==FROZEN
    data=json.loads(FIXTURE.read_text(encoding='utf8'))
    chunks=[ChunkRecord(r['id'],'SYNTHETIC-KB',r['document'],'v1',r['content'],{},chunk_type=r['chunk_type']) for r in data['chunks']]
    return data,chunks

def repository(chunks):
    r=InMemoryRetrievalRepository()
    for c in chunks:r.add(c)
    return r

@pytest.mark.parametrize('tf,df,n,dl,avg',[(1,1,4,3,7),(20,2,5,40,15),(2,3,3,9,9)])
def test_formula_exact(tf,df,n,dl,avg):
    expected=math.log(1+(n-df+.5)/(df+.5))*tf*2.2/(tf+1.2*(.25+.75*dl/avg))
    assert bm25_score(tf,df,n,dl,avg)==pytest.approx(expected)
    assert bm25_score(0,df,n,dl,avg)==0

def test_idf_length_saturation_multiterm_and_stable_tie():
    chunks=[ChunkRecord('a','kb','d','v','rare common'),ChunkRecord('b','kb','e','v','common '*100),ChunkRecord('c','kb','f','v','common'),ChunkRecord('z','kb','f','v','common')]
    hits=score_bm25(chunks,normalize_query('rare common'),10)
    assert hits[0].chunk_id=='a'
    assert next(i for i,h in enumerate(hits) if h.chunk_id=='c')<next(i for i,h in enumerate(hits) if h.chunk_id=='z')
    assert bm25_score(20,1,3,200,20)<bm25_score(20,1,3,20,20)
    assert bm25_score(1,1,3,10,10)>bm25_score(1,3,3,10,10)

def test_bm25_vs_tf_frozen_quality_report(request,tmp_path):
    data,chunks=corpus();repo=repository(chunks);rows=[]
    def metric(ids,gold,k):
        ranks=[i for i,id in enumerate(ids[:k],1) if id in gold]
        return {'recall':len(set(ids[:k])&set(gold))/len(gold),'mrr':1/min(ranks) if ranks else 0}
    for q in data['queries']:
        plan=normalize_query(q['question']);old=score_keyword_hits(chunks,legacy_query(q['question']),32);new=repo.keyword_candidates(SCOPE,plan,32)
        expanded=HybridRetriever(repo,mode='keyword',top_k=3,candidate_k=8).retrieve(SCOPE,q['question'])
        rankings={'old_tf':[h.chunk_id for h in old],'bm25':[h.chunk_id for h in new],
                  'bm25_expansion':[h.chunk_id for h in expanded.fused_ranking]}
        rows.append({'query':q,'rankings':rankings,'scores':{'old_tf':[h.model_dump(mode='json') for h in old],'bm25':[h.model_dump(mode='json') for h in new]},
                     'metrics':{mode:{str(k):metric(ids,q['expected_chunk_ids'],k) for k in (1,3)} for mode,ids in rankings.items()},
                     'expansion':expanded.retrieval_stats['query_expansion'],'stage_latency_ms':expanded.stage_latency_ms})
    aggregate={mode:{str(k):{name:sum(r['metrics'][mode][str(k)][name] for r in rows)/len(rows) for name in ('recall','mrr')} for k in (1,3)} for mode in rankings}
    report={'kind':'LABELLED_SYNTHETIC_IN_PROCESS_NOT_REAL_DENSE','fixture_sha256':FROZEN,'parameters':data['parameters'],
            'rows':rows,'aggregate':aggregate,'trigger_rate':sum(r['expansion']['triggered'] for r in rows)/len(rows),'real_model_calls':0}
    out=Path(request.config.option.xmlpath).parent if request.config.option.xmlpath else tmp_path
    (out/'ranking-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    assert any(r['rankings']['old_tf']!=r['rankings']['bm25'] for r in rows)
    assert all(math.isfinite(h['raw_score']) for r in rows for h in r['scores']['bm25'])

def test_scope_and_old_parent_not_in_memory_corpus():
    c=ChunkRecord('good','kb','doc','v','word')
    repo=repository([c,replace(c,chunk_id='other',knowledge_base_id='other'),replace(c,chunk_id='old',is_current=False),replace(c,chunk_id='parent',chunk_role='parent')])
    assert [h.chunk_id for h in repo.keyword_candidates(Scope.from_ids(['kb'],['doc']),normalize_query('word'),9)]==['good']

def test_nonempty_low_quality_and_missing_target_trigger():
    repo=repository([ChunkRecord('irrelevant','SYNTHETIC-KB','d','v','holiday gardening')])
    class Dense:
        def __init__(self):self.calls=0
        def embed(self,*args,**kwargs):self.calls+=1;return SimpleNamespace(vectors=[[1]],profile_id=None)
    repo.vector_candidates=lambda *a,**k:[RankedHit(chunk_id='irrelevant',rank=1,raw_score=.1)]
    dense=Dense();rank_calls=[]
    ranker=SimpleNamespace(rank=lambda q,h,c:(rank_calls.append(q) or h))
    result=HybridRetriever(repo,dense,ranker=ranker).retrieve(SCOPE,'A 的成本是多少？')
    e=result.retrieval_stats['query_expansion']
    assert e['triggered'] and 'KEYWORD_CHANNEL_MISSING' in e['before']['reasons']
    assert 'EVIDENCE_TARGET_UNCOVERED' in e['before']['reasons']
    assert result.items and len(rank_calls)==1 and dense.calls==1
    assert len(e['queries'])<=2 and e['model_calls']==0 and e['cost_microunits']==0

@pytest.mark.parametrize('question',['请问 ZX-102 在 2026-10-01 的成本不能超过 300 元；分别说明。','What is alpha cost in 2026/10/01; excluding beta?'])
def test_projections_never_invent_fact_or_scope_and_keep_hard_anchors(question):
    from backend.app.application.query_expansion import STOP
    original=normalize_query(question)
    anchors={t for t in original.terms if any(c.isascii() and c.isalnum() for c in t) and t not in STOP}
    for source,plan in variants(question):
        assert plan.q0==question and plan.normalized==original.normalized
        assert anchors<=set(plan.terms)
        assert all(t in question.lower() for t in plan.terms)
        assert source.source!='MEMORY'

def test_validated_tasks_only_and_no_unverified_hidden_entity():
    q='比较 A 和 B 的日期、价格。';part='价格';start=q.index(part)
    u=QueryUnderstanding(q,False,q,'comparison',(QueryTask(part,start,start+len(part),'lookup'),QueryTask('UNAUTHORISED 987654',0,2,'lookup')))
    for source,plan in variants(q,u):assert 'unauthorised' not in plan.terms and '987654' not in plan.terms
    assert u.q0==q and u.intent=='comparison'
    assert risk_signals(q,[],[],u)['missing_task_count']==1

def test_expansion_failure_preserves_initial_without_retry():
    c=ChunkRecord('c','SYNTHETIC-KB','d','v','合同')
    repo=repository([c]);original=repo.keyword_candidates;calls=[]
    def candidate(scope,plan,k):
        calls.append(plan.terms)
        if len(calls)>1:raise RuntimeError('PRIVATE EXCEPTION MUST NOT LEAK')
        return original(scope,plan,k)
    repo.keyword_candidates=candidate
    result=HybridRetriever(repo,mode='keyword').retrieve(SCOPE,'什么是合同；解除条件')
    e=result.retrieval_stats['query_expansion']
    assert e['status']=='DEGRADED_KEEP_INITIAL' and [i.chunk.chunk_id for i in result.items]==['c']
    assert len(calls)<=3 and all(r['status']=='FAILED' for r in e['queries'])
    assert 'PRIVATE' not in str(e)

def test_expansion_readback_failure_preserves_initial_without_retry():
    repo=repository([ChunkRecord('c','SYNTHETIC-KB','d','v','合同')])
    original=repo.get_retrieval_chunks;reads=[]
    def readback(scope,ids):
        reads.append(tuple(ids))
        if len(reads)==2:raise RuntimeError('PRIVATE READBACK')
        return original(scope,ids)
    repo.get_retrieval_chunks=readback
    result=HybridRetriever(repo,mode='keyword').retrieve(SCOPE,'什么是合同；解除条件')
    e=result.retrieval_stats['query_expansion']
    assert e['status']=='DEGRADED_KEEP_INITIAL' and e['validation_failure']=='EXPANSION_READBACK_UNAVAILABLE'
    assert [i.chunk.chunk_id for i in result.items]==['c'] and len(reads)==3
    assert 'PRIVATE' not in str(e)

def test_nonempty_dense_recovers_keyword_once_and_revalidates_original_child():
    repo=repository([ChunkRecord('noise','SYNTHETIC-KB','d','v','gardening'),ChunkRecord('contract','SYNTHETIC-KB','e','v','合同解除条件')])
    original=repo.keyword_candidates;keywords=[];ranks=[];embeddings=[]
    def keywords_once(scope,plan,limit):
        keywords.append(plan.terms)
        return [] if len(keywords)==1 else original(scope,plan,limit)
    repo.keyword_candidates=keywords_once
    repo.vector_candidates=lambda *a,**k:[RankedHit(chunk_id='noise',rank=1,raw_score=.1)]
    dense=SimpleNamespace(embed=lambda *a,**k:(embeddings.append(a) or SimpleNamespace(vectors=[[1]],profile_id=None)))
    ranker=SimpleNamespace(rank=lambda q,h,c:(ranks.append(tuple(c)) or h))
    result=HybridRetriever(repo,dense,ranker=ranker).retrieve(SCOPE,'什么是合同；解除条件')
    e=result.retrieval_stats['query_expansion']
    assert e['triggered'] and 1<=len(e['queries'])<=2 and len(keywords)==1+len(e['queries'])
    assert len(embeddings)==len(ranks)==1 and 'contract' in ranks[0]
    assert any(i.chunk.chunk_id=='contract' and i.chunk.content=='合同解除条件' for i in result.items)
    assert e['initial_candidate_rankings']['keyword']==[] and result.candidate_rankings['keyword']
    assert e['before']['missing_term_count']>e['after']['missing_term_count'] and e['model_calls']==0

def test_uppercase_entity_a_is_not_english_stopword():
    q='请问 A 的成本；B 的日期。'
    plans=variants(q)
    assert plans and all({'a','b'}<=set(plan.terms) for source,plan in plans)

def test_mixed_tokens_keep_identifiers_numbers_cjk_tf_and_exact_quote():
    body='A方案成本1000元 A方案成本1000元 API_v2 ZX-102 é合同'
    f=term_frequencies(body)
    assert f['a']==f['成本']==f['1000']==f['元']==2
    assert f['api_v2']==f['zx']==f['102']==f['é']==f['合同']==1
    assert 'a方案成本1000元' not in f
    c=ChunkRecord('mixed','SYNTHETIC-KB','d','v',body)
    result=HybridRetriever(repository([c]),mode='keyword').retrieve(SCOPE,'A 的成本是多少？')
    assert result.items[0].chunk.content==body and result.items[0].chunk.chunk_id=='mixed'

def test_adaptive_semantic_route_does_not_treat_unselected_keyword_as_missing():
    c=ChunkRecord('semantic','SYNTHETIC-KB','d','v','注意力能关联远处的内容',embedding=(1.,),embedding_profile_id='p')
    repo=repository([c]);calls=[]
    original=repo.keyword_candidates
    repo.keyword_candidates=lambda *a,**k:(calls.append(a) or original(*a,**k))
    dense=SimpleNamespace(embed=lambda *a,**k:SimpleNamespace(vectors=[[1.]],profile_id='p'))
    result=HybridRetriever(repo,dense,mode='adaptive').retrieve(SCOPE,'为什么注意力机制有帮助？')
    e=result.retrieval_stats['query_expansion']
    assert result.retrieval_mode=='vector' and result.sources==('vector',) and not calls
    assert not e['triggered'] and e['before']['keyword_channel_state']=='NOT_SELECTED'
    assert 'KEYWORD_CHANNEL_MISSING' not in e['before']['reasons'] and e['trigger_reasons']==[]

def test_explicit_vector_evidence_gap_can_recover_once():
    repo=repository([ChunkRecord('noise','SYNTHETIC-KB','d','v','holiday'),ChunkRecord('fact','SYNTHETIC-KB','e','v','A方案成本1000元')])
    repo.vector_candidates=lambda *a,**k:[RankedHit(chunk_id='noise',rank=1,raw_score=.1)]
    embeddings=[];ranks=[]
    dense=SimpleNamespace(embed=lambda *a,**k:(embeddings.append(a) or SimpleNamespace(vectors=[[1]],profile_id=None)))
    ranker=SimpleNamespace(rank=lambda q,h,c:(ranks.append(tuple(c)) or h))
    result=HybridRetriever(repo,dense,mode='vector',ranker=ranker).retrieve(SCOPE,'A 的成本是多少？')
    e=result.retrieval_stats['query_expansion']
    assert e['triggered'] and e['queries'] and 'EVIDENCE_TARGET_UNCOVERED' in e['trigger_reasons']
    assert result.retrieval_mode=='hybrid' and 'fact' in ranks[0] and len(ranks)==len(embeddings)==1
    assert len(e['queries'])<=2 and e['model_calls']==0

@pytest.mark.parametrize('enabled,question,fail,expected_calls',[(False,'原始问题',False,0),(True,'原始问题',False,1),
    (True,'什么是合同；解除条件',False,0),(True,'什么是合同；解除条件',True,0)])
def test_no_safe_variant_local_continuation_is_explicit_and_not_a_second_loop(enabled,question,fail,expected_calls):
    from backend.app.application.knowledge_gateway import KnowledgeGateway
    repo=repository([ChunkRecord('expanded','SYNTHETIC-KB','d','v','扩展词证据')])
    requests=[];original=repo.keyword_candidates
    def candidates(scope,plan,limit):
        requests.append(plan.terms)
        if fail and len(requests)>1:raise RuntimeError('PRIVATE')
        return original(scope,plan,limit)
    repo.keyword_candidates=candidates
    calls=[]
    model=SimpleNamespace(query_expand=lambda *a,**k:(calls.append(a) or {'expansions':['扩展词']}))
    plan,result=KnowledgeGateway(HybridRetriever(repo,mode='keyword')).retrieve_question(SCOPE,question,
        local_query_enabled=enabled,local_query_gateway=model)
    assert len(calls)==expected_calls and plan.model_calls==expected_calls and plan.question==question
    if expected_calls:
        assert [i.chunk.chunk_id for i in result.items]==['expanded'] and len(requests)==2
        assert result.retrieval_stats['query_expansion']['enabled'] is False
    else:assert not result.items and len(requests)<=3

def reading(text,**limits):
    d=dict(knowledge_base_id='kb',active_version_id='v',index_status='ready',deleted_at=None)
    evidence=EvidenceAccumulator()
    gateway=KnowledgeToolGateway(content_reader=lambda doc:text,document_resolver=lambda doc:d,evidence_accumulator=evidence,**limits)
    gateway.bind_read_token_counter(lambda row:len(row['content'].split())) # SIMULATED exact fixture tokenizer
    return gateway,d,evidence

def read(g,offset=0,length=8000,scope=None,**kwargs):
    return g.invoke('read_document',dict(document_id='d',offset=offset,length=length,**kwargs),scope or Scope.from_ids(['kb'])).data

def test_read_first_middle_tail_no_overlap_or_gap_or_evidence():
    text=''.join(f'Paragraph {n}: '+'word '*20+'\n' for n in range(60))
    g,_,evidence=reading(text,max_document_chars=3000)
    pages=[];offset=0
    while True:
        page=read(g,offset,3000);pages.append(page)
        assert page['start_offset']==offset and page['version_id']=='v'
        if not page['has_more']:break
        assert page['next_offset']>offset;offset=page['next_offset']
    assert len(pages)>=3 and ''.join(p['content'] for p in pages)==text
    assert not evidence.chunks
    with pytest.raises(ToolDenied,match='ALREADY_READ'):read(g,0,3000)

@pytest.mark.parametrize('change',['version','scope','delete','content'])
def test_read_binding_change_denied(change):
    text='a '*9000;g,d,_=reading(text);read(g,0,100)
    if change=='version':d['active_version_id']='new'
    elif change=='delete':d['deleted_at']='now'
    elif change=='content':g.content_reader=lambda doc:text+'changed'
    with pytest.raises(ToolDenied):read(g,100,100,scope=Scope.from_ids(['kb'],['d']) if change=='scope' else None)

@pytest.mark.parametrize('offset,length',[(-1,4),(True,4),(1.0,4),(0,False),(0,8001),(0,0)])
def test_read_invalid_position(offset,length):
    g,_,_=reading('word '*5000)
    with pytest.raises(ToolDenied,match='POSITION_INVALID'):read(g,offset,length)

@pytest.mark.parametrize('limit,kwargs,code',[('calls',{'max_read_calls':1},'RUN_READ_LIMIT'),('chars',{'max_read_chars':5},'RUN_READ_LIMIT'),('tokens',{'max_read_tokens':1},'RUN_TOKEN_LIMIT')])
def test_read_run_quotas(limit,kwargs,code):
    g,_,_=reading('one two three '*10,**kwargs)
    if limit=='calls':read(g,0,4)
    with pytest.raises(ToolDenied,match=code):read(g,4 if limit=='calls' else 0,8)

def test_read_atomic_table_caption_ocr_and_unknown_counter():
    g,_,_=reading('HEADER\nA 100\nB 200\nTAIL\n')
    g.document_view_reader=lambda *a:dict(version_id='v',content='HEADER\nA 100\nB 200\nTAIL\n',protected_spans=[(0,19)])
    with pytest.raises(ToolDenied,match='ATOMIC_PAGE_TOO_LARGE'):read(g,0,10)
    with pytest.raises(ToolDenied,match='ATOMIC_OFFSET_INVALID'):read(g,8,20)
    page=read(g,0,20);assert page['content']=='HEADER\nA 100\nB 200\n'
    g,_,_=reading('a '*200);g.bind_read_token_counter(None);read(g,0,10)
    with pytest.raises(ToolDenied,match='TOKEN_CAPACITY_UNKNOWN'):read(g,10,10)

def test_scope_or_during_read_switch_cannot_release_content():
    g,d,_=reading('secret')
    with pytest.raises(ToolDenied,match='SCOPE_VIOLATION'):read(g,scope=Scope.from_ids(['other']))
    def race(doc):d['active_version_id']='new';return 'old'
    g.content_reader=race
    with pytest.raises(ToolDenied,match='IDENTITY_CHANGED'):read(g)
