"""Offline frozen-receipt replay with READ ONLY current source loading.

No Provider/transport is created. Evaluation targets are used only after
production selection and rendering; they are not supplied to selectors.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import fields
import hashlib
import html
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResult, RerankResponse
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing
from backend.weknora_port.retrieval.rerank import rerank, enriched_passage
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.source_evidence import expand_source_evidence, MODE
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy


def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def receipt_case(case):
    folder = Path(case['evidence_dir'])
    stage_file = next(folder.glob('*.stages.json'))
    capture = load(stage_file)
    states = {r['stage']:r['state'] for r in capture['stages']
              if r['status']=='completed' and 'state' in r}
    request_file, request = next((p, load(p)) for p in folder.glob('*.request.json')
                                if load(p)['capability']=='rerank')
    response_file = request_file.with_name(request_file.name.replace('.request.json','.response.body'))
    response_meta = load(request_file.with_name(request_file.name.replace('.request.json','.response.json')))
    if response_meta != {'complete': True, 'status_code': 200, 'headers_saved': False}:
        assert response_meta['complete'] and response_meta['status_code']==200
    raw = load(response_file)
    names={f.name for f in fields(SearchResult)}
    candidates=[SearchResult(**{k:v for k,v in r.items() if k in names}) for r in states['retrieval']['search_results'] if r]
    actual=[enriched_passage(r) for r in candidates]
    assert actual==request['body']['documents'], 'FROZEN_RERANK_INPUT_NOT_REPRODUCIBLE'
    return {'query':states['understand']['query'], 'rewrite':request['body']['query'],
            'candidates':candidates, 'request':request, 'raw':raw,
            'files':[stage_file,request_file,response_file],
            'historical_final_ids':(capture.get('finalized_context') or {}).get('result_ids')}


async def evaluate(case, frozen, conn, mode):
    tenant=case['tenant_id']; request=frozen['request']
    scope=request['scope']
    context=CallContext('OFFLINE_REPLAY',TrustedScope(scope['owner_id'],tuple(scope['knowledge_base_ids']),tuple(scope['document_ids'])))
    async def resolve(owner):
        if owner!=scope['owner_id']: raise PermissionError('OWNER_MISMATCH')
        rows=await (await conn.execute('SELECT DISTINCT tenant_id FROM knowledge_bases WHERE id=ANY(%s)',
                                      (list(scope['knowledge_base_ids']),))).fetchall()
        assert rows and {r['tenant_id'] for r in rows}=={tenant}
        return tenant
    retriever=PostgresRetriever(conn,IsolationPolicy('7696088403504697382',frozenset({'OLD_BUSINESS_DENIED'})))
    await retriever.verify_identity()
    repo=ScopedKnowledgeRepository(retriever,resolve)
    from copy import deepcopy
    state=PipelineState(query=case.get('question') or frozen['query'], rewrite_query=frozen['rewrite'],
        targets=tuple(SearchTarget(kb) for kb in scope['knowledge_base_ids']),
        search_results=deepcopy(frozen['candidates']),rerank_model_id='SAVED_RESPONSE_ONLY', source_evidence_mode=mode)
    synthetic=case['kind']=='SYNTHETIC_SUMMARY_ONLY_BOUNDARY'
    class SavedRegistry:
        async def rerank(self,*args): return self
        async def rerank_call(self,query,passages,ctx):
            assert passages==request['body']['documents'] and query==request['body']['query']
            return RerankResponse(tuple(RerankResult(r['index'],r['relevance_score']) for r in frozen['raw']['results']))
    registry=SimpleNamespace(rerank=_async_registry(SavedRegistry()))
    segmenter=default_segmenter()
    try:
        if synthetic:
            # Known historical failure shape; score is NOT a reconstructed receipt.
            state.rerank_results=[deepcopy(r) for r in state.search_results if r.chunk_type=='summary']
        else:
            await rerank(state,context,registry,segmenter)
        await merge(state,context,repo,segmenter)
        filter_top_k(state)
        base_ids=[r.id for r in state.merge_results]
        if mode!= 'off':await expand_source_evidence(state,context,repo)
        final=await into_chat_message(state)
        prepared=prepare_generation(GenerationRequest('offline','offline','offline',state.query,final,
            tuple(Evidence.from_result(r) for r in state.merge_results)))
        message=html.unescape(prepared.messages[-1].content)
        required={i for group in case['required_sets'] for i in group}
        sources=await repo.get_chunks(tuple(required),context)
        present={i for i,r in sources.items() if r.content and r.content in message}
        coverage=any(set(group)<=present for group in case['required_sets'])
        return {'status':'OFFLINE_REPLAY' if not synthetic else 'SYNTHETIC_BOUNDARY',
            'mode':mode,'selected_before_expansion':base_ids,'final_ids':list(final.result_ids),
            'required_full_quotes_present':sorted(present),'required_evidence_satisfied':coverage,
            'context_chars':len(final.rendered_contexts),'generation_user_chars':len(prepared.messages[-1].content),
            'prepared_user_sha256':hashlib.sha256(prepared.messages[-1].content.encode()).hexdigest(),
            'trace':state.evidence_trace,'provider_sends':0,'actual_delivery':'NOT_RUN',
            'generation_quality':'NOT_RUN','historical_ids_match':None if synthetic else base_ids==frozen['historical_final_ids']}
    except SearchNothing:
        return {'status':'SEARCH_NOTHING','mode':mode,'required_evidence_satisfied':False,
                'trace':state.evidence_trace,'actual_delivery':'NOT_RUN','provider_sends':0}


def _async_registry(saved):
    # Registry and model deliberately have different rerank signatures.
    async def resolve(*args):return SimpleNamespace(rerank=saved.rerank_call)
    return resolve


async def main(args):
    import psycopg
    from psycopg.rows import dict_row
    from scripts.local_runtime import environment
    env=environment(load(args.config))
    plan=load(args.plan); assert plan['cases'], 'EMPTY_CASES_NOT_A_PASS'
    frozen={c['case_id']:receipt_case(c) for c in plan['cases'] if c['kind']=='SAVED_PROVIDER_RERANK'}
    result=[]
    conn=await psycopg.AsyncConnection.connect(env['WEKNORA_PORT_DATABASE_URL'],autocommit=True,row_factory=dict_row)
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        for case in plan['cases']:
            data=frozen.get(case['case_id']) or frozen[case['basis_case']]
            modes=[await evaluate(case,data,conn,m) for m in ('off',MODE)]
            result.append({'case_id':case['case_id'],'kind':case['kind'],'source_mapping':case.get('source_mapping'),
                'inputs':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in data['files']],
                'results':modes})
        await conn.execute('ROLLBACK')
    finally:await conn.close()
    output={'execution_kind':'OFFLINE_REPLAY_WITH_CURRENT_READ_ONLY_SOURCES','provider_sends':0,'cases':result}
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([{'case':c['case_id'],'covered':[r['required_evidence_satisfied'] for r in c['results']],
                      'chars':[r.get('context_chars') for r in c['results']]} for c in result]))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',required=True);parser.add_argument('--config',required=True);parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None)
