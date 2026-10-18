"""Offline native-stage analysis of already saved S1 A/B receipts; zero sends.

The saved response is a model-contract fixture, not a new model evaluation.
PostgreSQL is accessed only inside a READ ONLY transaction. No Provider exists.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import fields
import hashlib
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResult, RerankResponse
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.rerank import rerank, enriched_passage, model_passage, mmr
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest

TARGETS = dict(zip(('mml-0959','mml-0434','hotpot-frost','hotpot-romeo','omni-page007'),
    ('df6098cb-68e6-4546-b9e0-8682b4bf19e9','085643dc-5ebb-4b8a-b7f1-75427da40015',
     '31417c7c-7abf-4723-956a-74bf7ee14683','fb294950-cd77-4da1-ada6-b2c76aaa07da',
     '51316a20-67fd-4732-b520-c049999933d0')))

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def norm(value):
    return ''.join(html.unescape(value).split())

def snapshot(rows):
    return [dict(id=r.id, kind=r.chunk_type, score=r.score, chars=len(r.content),
        sub_chunk_ids=r.sub_chunk_ids.copy(), document_id=r.knowledge_id,
        source_version=r.metadata.get('source_version'), content_sha256=hashlib.sha256(r.content.encode()).hexdigest()) for r in rows]

def quotes_for(case, data, quote_plan):
    qid=case.get('source_input_identity',{}).get('original_qid','')
    quoted=next((c.get('quotes',[]) for c in quote_plan['cases'] if qid and qid in c['case_id']),[])
    target=next((r for r in data['candidates'] if r['id']==TARGETS.get(case['case_id'])),None)
    if target:
        quoted=quoted+[dict(id='target_original_body',text=target['content'],fact_scope='TARGET_BODY_NOT_ANSWER_JUDGE')]
    if case['case_id']=='mml-0434':
        quoted=quoted+[dict(id='dataset_fact',text="Kaggle's Cat vs Dog dataset (25k dog/cat pictures)",fact_scope='REVIEWED_ORIGINAL_FACT')]
    return quoted

async def arm(case, data, rows, label, response, repo, context, args, quotes):
    state=PipelineState(query=case['question'],rewrite_query=data['query'],
        targets=(SearchTarget(case['kb_id']),),search_results=deepcopy(rows),
        rerank_model_id='SAVED_ONLY',rerank_context_enabled=label=='B',
        rerank_threshold=args.threshold,rerank_top_k=args.topk,source_evidence_mode='off')
    calls=0
    class SavedModel:
        async def rerank(self, query, passages, ctx):
            nonlocal calls
            calls+=1
            assert query==data['query'] and passages==data[label], 'FROZEN_ARM_INPUT_MISMATCH'
            return RerankResponse(tuple(RerankResult(r['index'],r['score']) for r in response['results']))
    class Registry:
        async def rerank(self,*unused):
            return SavedModel()
    raw=[dict(rank=i+1,index=r['index'],id=rows[r['index']].id,score=r['score'],
        passes_threshold=r['score']>=args.threshold) for i,r in enumerate(response['results'])]
    result=dict(status='OFFLINE_NATIVE_REPLAY',raw=raw,source_mode='off',saved_response_invocations=0,
        threshold_ids=[r['id'] for r in raw if r['passes_threshold']],accepted_pre_mmr='UNVERIFIED',
        mmr=[],merge=[],top_k=[],final_ids=[],context_chars=0,generation_chars=0)
    # Observe the real mmr call arguments without replacing any production function.
    def observe(frame,event,arg):
        if event=='call' and frame.f_code is mmr.__code__:
            result['accepted_pre_mmr']=snapshot(frame.f_locals['candidates'])
    segmenter=default_segmenter()
    try:
        previous=sys.getprofile()
        try:
            sys.setprofile(observe)
            await rerank(state,context,Registry(),segmenter)
        finally:
            sys.setprofile(previous)
        result['mmr']=snapshot(state.rerank_results)
        await merge(state,context,repo,segmenter)
        result['merge']=snapshot(state.merge_results)
        filter_top_k(state)
        result['top_k']=snapshot(state.merge_results)
        final=await into_chat_message(state)
        prepared=prepare_generation(GenerationRequest('offline','offline','offline',case['question'],
            final,tuple(Evidence.from_result(r) for r in state.merge_results)))
        user=html.unescape(prepared.messages[-1].content)
        result.update(final_ids=list(final.result_ids),context_chars=len(final.rendered_contexts),
            generation_chars=len(user),context_sha256=hashlib.sha256(user.encode()).hexdigest(),
            final_kind_counts=dict(Counter(r.chunk_type for r in state.merge_results)),
            final_original_content_chars=sum(len(r.content) for r in state.merge_results))
        final_rows=state.merge_results
    except SearchNothing:
        result.update(status='SEARCH_NOTHING',accepted_pre_mmr=[],final_kind_counts={},final_original_content_chars=0)
        user=''
        final_rows=[]
    result['saved_response_invocations']=calls
    target=TARGETS.get(case['case_id'])
    result['target']={'id':target,'raw':next((r for r in raw if r['id']==target),None),
        'in_accepted':any(r['id']==target for r in result['accepted_pre_mmr']) if isinstance(result['accepted_pre_mmr'],list) else 'UNVERIFIED',
        'in_mmr':any(r['id']==target for r in result['mmr']),
        'in_merge_or_subchunks':any(r['id']==target or target in r['sub_chunk_ids'] for r in result['merge']),
        'in_topk_or_subchunks':any(r['id']==target or target in r['sub_chunk_ids'] for r in result['top_k']),
        'in_final_ids':target in result['final_ids'] if target else None}
    result['quotes']=[dict(q,normalized_present_in_generation=norm(q['text']) in norm(user),
        present_in_final_source_body=any(norm(q['text']) in norm(r.content) for r in final_rows if r.chunk_type!='summary'),
        present_in_final_summary=any(norm(q['text']) in norm(r.content) for r in final_rows if r.chunk_type=='summary')) for q in quotes]
    return result

async def run(args):
    import psycopg
    from psycopg.rows import dict_row
    from scripts.local_runtime import environment,load_config
    plan=read(args.input/'plan.json')
    quote_plan=read(args.quote_plan)
    env=environment(load_config(args.config))  # Never print config, env or DSN.
    conn=await psycopg.AsyncConnection.connect(env['WEKNORA_PORT_DATABASE_URL'],autocommit=True,row_factory=dict_row)
    output=[]
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        db=await (await conn.execute('SELECT current_database() AS name, current_setting(\'transaction_read_only\') AS ro')).fetchone()
        assert db==dict(name='weknora_python_port_20261013',ro='on')
        retriever=PostgresRetriever(conn,IsolationPolicy('7696088403504697382',frozenset({'OLD_BUSINESS_DENIED'})))
        await retriever.verify_identity()
        for case in plan['cases']:
            folder=args.input/('live-'+case['case_id'])
            data=read(folder/'inputs.json')
            request=read(case['source_input_identity']['request_path'])
            scope=request.get('trusted_scope')
            if scope is None:
                grant=read(folder/'execution.json')
                assert grant['tenant_id']==332 and grant['allowed_kbs']==[case['kb_id']]
                scope=dict(owner_id=grant['subject_id'],knowledge_base_ids=grant['allowed_kbs'],document_ids=[])
            assert scope['knowledge_base_ids']==[case['kb_id']]
            context=CallContext('OFFLINE_S1_AB',TrustedScope(scope['owner_id'],tuple(scope['knowledge_base_ids']),tuple(scope['document_ids'])))
            async def tenant(owner):
                assert owner==scope['owner_id']
                found=await (await conn.execute('SELECT DISTINCT tenant_id FROM knowledge_bases WHERE id=ANY(%s)',([case['kb_id']],))).fetchall()
                assert found and {r['tenant_id'] for r in found}=={332}
                return 332
            repo=ScopedKnowledgeRepository(retriever,tenant)
            sources=await repo.get_chunks(tuple(r['id'] for r in data['candidates']),context)
            names={f.name for f in fields(SearchResult)}
            rows=[]
            for frozen in data['candidates']:
                source=sources[frozen['id']]
                assert source.knowledge_id==frozen['knowledge_id'] and source.knowledge_base_id==case['kb_id']
                assert source.content==frozen['content'] and source.metadata.get('source_version')==frozen['metadata'].get('source_version')
                row=SearchResult(**{k:v for k,v in frozen.items() if k in names})
                row.knowledge_title=source.knowledge_title
                row.context_header=source.context_header
                rows.append(row)
            assert [enriched_passage(r) for r in rows]==data['A']
            assert [model_passage(r) for r in rows]==data['B']
            quotes=quotes_for(case,data,quote_plan)
            arms={}
            for label in ('A','B'):
                response=read(folder/(label+'.response.json'))
                assert all(0<=r['index']<len(rows) for r in response['results'])
                assert len({r['index'] for r in response['results']})==len(response['results'])
                arms[label]=await arm(case,data,rows,label,response,repo,context,args,quotes)
            output.append(dict(case_id=case['case_id'],question=case['question'],kb_id=case['kb_id'],
                construction=case['construction'],input_sha256=sha(folder/'inputs.json'),
                response_sha256={label:sha(folder/(label+'.response.json')) for label in ('A','B')},
                hydration_and_inputs='VERIFIED',candidate_count=len(rows),
                input_chars={label:sum(map(len,data[label])) for label in ('A','B')},arms=arms))
        await conn.execute('ROLLBACK')
    finally:
        await conn.close()
    result=dict(schema='s1-rerank-native-ab-analysis/v1',provider_sends=0,provider_constructions=0,database_writes=0,
        identity=dict(database=db['name'],system_identifier='7696088403504697382',tenant=332,transaction_read_only=True),
        settings=dict(rerank_threshold=args.threshold,rerank_top_k=args.topk,top1_floor=0.15,source_mode='off',
            origin='Owner-specified native settings; historical state snapshots do not capture threshold/topk: UNVERIFIED'),
        production_source_sha256={str(p.relative_to(ROOT)):sha(p) for p in [ROOT/'backend/weknora_port'/s for s in
            ('retrieval/rerank.py','retrieval/merge.py','retrieval/filter_top_k.py','retrieval/context.py','generation/context.py')]},
        cases=output,limitations=['No new model/generation/API request; answer quality NOT RUN.',
            'One saved receipt per arm; stochastic rerank reproducibility UNVERIFIED.',
            'No bridge/entity matching/cross-chunk LLM validation/second retrieval.',
            'Quote presence measures delivery only, not full answer sufficiency.',
            'Omni raw OCR is noisy; coefficient/constant matrix correctness UNVERIFIED.',
            'Negative is constructed reviewed candidate subset, not official gold.',
            'Native existing merge parent/neighbor reads remain enabled; no source_evidence expansion.'])
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS_OFFLINE_EXECUTION',cases=len(output),provider_sends=0,out=str(args.out))))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--quote-plan',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--threshold',type=float,default=0.3)
    p.add_argument('--topk',type=int,default=5)
    asyncio.run(run(p.parse_args()),loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None)
