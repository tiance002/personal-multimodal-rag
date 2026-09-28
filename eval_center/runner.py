"""Version-bound local A/B runner using production ingestion and Quick RAG.

No cloud calls, normal database access, index replacement or remote upload.
Outputs retain full local evidence; only strict v2 projection is exportable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from backend.app.bootstrap import build_container
from backend.app.config import Settings
from backend.app.workers.ingestion import run_once
from backend.app.application.retrieval import HybridRetriever
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.knowledge_gateway import EvidenceService, KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.adapters.models.usage import capture_usage
from backend.app.domain.evidence import EvidenceResolver
from backend.app.domain.scope import Scope

from eval_center.dataset import load_reviewed_dataset
from eval_center.gold import evidence_from_dict
from eval_center.isolated_index import create_isolated_database, read_index_snapshot
from eval_center.runtime import committed_code_sha, effective_configuration, model_identities
from eval_center.source_metrics import build_source_statistics
from eval_center.quality import answer_statistics, duplicate_statistics
from eval_center.telemetry import project_calls
from eval_center.metrics import aggregate_metrics
from eval_center.verification import compute_case_metrics, ExperimentInvalidError
from eval_center.export_v2 import build_v2_bundle
from eval_center.contracts_v2 import _V2_ERROR_CODES


def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


class RecordingRetriever(HybridRetriever):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.records=[]

    def retrieve(self,*args,**kwargs):
        result=super().retrieve(*args,**kwargs)
        self.records.append(result)
        return result


class RecordingContextBuilder(ContextBuilder):
    def __init__(self,max_chars):
        super().__init__(max_chars)
        self.elapsed_ms=0

    def select(self,*args,**kwargs):
        started=time.perf_counter()
        try: return super().select(*args,**kwargs)
        finally: self.elapsed_ms+=(time.perf_counter()-started)*1000

    def build(self,*args,**kwargs):
        started=time.perf_counter()
        before=self.elapsed_ms
        try: return super().build(*args,**kwargs)
        finally: self.elapsed_ms+=max(0,(time.perf_counter()-started)*1000-(self.elapsed_ms-before))


class RecordingEvidenceService(EvidenceService):
    def __init__(self,context_builder):
        super().__init__(context_builder=context_builder)
        self.context_bundle=None

    def with_context(self,*args,**kwargs):
        bundle=super().with_context(*args,**kwargs)
        self.context_bundle=bundle
        return bundle


def _ranks(result):
    return {'keyword':[hit.chunk_id for hit in result.candidate_rankings.get('keyword',())],
            'vector':[hit.chunk_id for hit in result.candidate_rankings.get('vector',())],
            'fused':[item.chunk.chunk_id for item in result.items]}


def _raw_attempt(result):
    return {'query_plan':result.query_plan.model_dump(),
        'candidate_rankings':{stage:[hit.model_dump() for hit in ranking] for stage,ranking in result.candidate_rankings.items()},
        'full_fused_ranking':[hit.model_dump() for hit in result.fused_ranking],
        'returned_chunk_ids':[item.chunk.chunk_id for item in result.items],
        'effective_config':result.effective_config,'stage_latency_ms':result.stage_latency_ms,
        'latency_ms':result.latency_ms,'degradation_flags':list(result.degradation_flags)}


def evaluate_case(container,kb_id,index,case,config,*,generate,tokenizer):
    retriever=RecordingRetriever(container.store,embedding_provider=container.ollama,
        top_k=config['top_k'],candidate_k=config['candidate_k'],rrf_k=config['rrf_k'])
    context_builder=RecordingContextBuilder(config['context_budget_chars'])
    evidence=RecordingEvidenceService(context_builder)
    gateway=KnowledgeGateway(retriever,evidence_service=evidence)
    actual=effective_configuration(retriever,container.store,context_builder,declared=config)
    scope=Scope.from_ids([kb_id])
    started=time.perf_counter()
    answer_result=None
    with capture_usage() as usage:
        if generate:
            answer_result=LangChainQuickChain(gateway,answer_gateway=container.ollama).invoke(
                case['question'],scope,settings=QuickSettings(cloud_enabled=False,local_query_enabled=False,
                    answer_timeout_seconds=120),cloud_allowed_by_kb={kb_id:False})
        else:
            plan,retrieval=gateway.retrieve_question(scope,case['question'])
            bundle=evidence.bundle(plan,retrieval)
            evidence.with_context(bundle,str(uuid.uuid4()),CitationService(InMemoryCitationStore()))
    elapsed=(time.perf_counter()-started)*1000
    if not retriever.records: raise ExperimentInvalidError('no_actual_retrieval_record')
    for record in retriever.records:
        if record.effective_config!={key:actual[key] for key in ('top_k','candidate_k','rrf_k')}:
            raise ExperimentInvalidError('effective_config_changed_during_run')
    calls=project_calls(usage)
    answer_calls=[call for call in calls if call['stage']=='answer']
    prepared=evidence.context_bundle
    mode='prepared' if not generate else 'sent' if any(call['status']=='ok' for call in answer_calls) else 'unconfirmed' if answer_calls else 'not_run'
    selected=list(prepared.selected) if prepared is not None and mode!='not_run' else []
    context=prepared.context if selected else ''
    ranks={**_ranks(retriever.records[0]),'context':[item.chunk.chunk_id for item in selected]}
    stats=build_source_statistics([evidence_from_dict(item) for item in case['gold_evidence']],index['spans'],ranks)
    stats.update(context_mode=mode,retrieval_attempts=[_ranks(result) for result in retriever.records])
    count=(lambda text:len(tokenizer.encode(text))) if tokenizer is not None else None
    def estimated(text):
        return {'availability':'estimated','count':count(text)} if count is not None else {'availability':'unavailable','count':None}
    def sum_timing(key):
        values=[record.stage_latency_ms.get(key) for record in retriever.records]
        return sum(values) if values and all(value is not None for value in values) else None
    chat_latency=[call['latency_ms'] for call in answer_calls]
    query_latency=[call['latency_ms'] for call in calls if call['stage']=='query']
    stats['telemetry']={'calls':calls,'timings':{
        'query_processing_ms':(sum_timing('query_processing_ms') or 0)+sum(query_latency),
        'keyword_retrieval_ms':sum_timing('keyword_retrieval_ms'),'vector_retrieval_ms':sum_timing('vector_retrieval_ms'),
        'fusion_ms':sum_timing('fusion_ms'),'embedding_ms':sum(call['latency_ms'] for call in calls if call['stage']=='embedding') or None,
        'context_building_ms':context_builder.elapsed_ms,'retrieval_ms':sum(record.latency_ms for record in retriever.records),
        'generation_ms':sum(chat_latency) if chat_latency else None,'end_to_end_ms':elapsed},
        'context_tokens':estimated(context),'evidence_tokens':estimated('\n\n'.join(item.chunk.content for item in selected))}
    duplicate=duplicate_statistics([{'text':item.chunk.content,'document_id':index['spans'][item.chunk.chunk_id].document_id,
        'source_version':index['spans'][item.chunk.chunk_id].source_version} for item in selected],token_counter=count)
    savings=duplicate['context_token_savings']
    after_context=context
    if duplicate['removed_indices']:
        retained=[item for number,item in enumerate(selected) if number not in duplicate['removed_indices']]
        after_context,_=ContextBuilder(config['context_budget_chars']).build(str(uuid.uuid4()),retained,CitationService(InMemoryCitationStore()))
    stats['dedup']={'total_units':len(selected),'duplicate_units':len(duplicate['removed_indices']),
        'tokens_before':count(context) if count is not None else None,'tokens_after':count(after_context) if count is not None else None,
        'reviewed_merges':0,'false_merges':0,'tokenizer':'cl100k_base' if tokenizer is not None else 'unavailable'}
    readbacks={}
    def read_quote(version,chunk_id):
        chunk=container.store.get_chunk(chunk_id)
        if chunk is None or chunk.version_id!=version: raise ValueError('unreadable frozen version')
        scope.assert_contains(chunk.knowledge_base_id,chunk.document_id)
        return chunk.content
    if answer_result is not None:
        resolver=EvidenceResolver({snapshot.label:snapshot for snapshot in answer_result.evidence},read_quote)
        for label in answer_result.citations:
            try: resolver.resolve(label); readbacks[label]=True
            except Exception: readbacks[label]=False
    refusal_codes={'NO_CANDIDATES','LOW_COVERAGE','NO_EVIDENCE_AFTER_RETRY','SEMANTIC_MISMATCH','SECTION_TRUNCATED'}
    stats['quality']=answer_statistics(answer=answer_result.answer if answer_result is not None else None,
        answer_points=case['required_answer_points'],citation_readbacks=readbacks,answerable=case['answerable'],
        refused=answer_result.error_code in refusal_codes if answer_result is not None else None)
    metrics=compute_case_metrics(stats,actual)
    status=('passed' if metrics['context_recall']==1 else 'failed') if case['answerable'] else (
        'passed' if metrics['refusal_accuracy']==1 else 'failed' if generate else 'not_evaluated')
    errors=[]
    codes={flag for record in retriever.records for flag in record.degradation_flags}
    if answer_result is not None:
        if answer_result.error_code: codes.add(answer_result.error_code)
        if answer_result.trace.degradation_code: codes.update(answer_result.trace.degradation_code.split(';'))
    for code in sorted(codes):
        errors.append({'case_id':case['case_id'],'stage':'retrieve' if code in refusal_codes or code=='VECTOR_UNAVAILABLE' else 'generate',
                       'error_code':code if code in _V2_ERROR_CODES else 'UNKNOWN'})
    row={'case_id':case['case_id'],'status':status,'statistics':stats,'metrics':metrics,
         'question':case['question'],'gold_evidence':case['gold_evidence'],'context':context,
         'context_mode':mode,'tokenizer':'cl100k_base (estimated for Qwen)' if tokenizer is not None else 'unavailable',
         'retrieval_attempts':[_raw_attempt(record) for record in retriever.records],
         'answer':answer_result.answer if answer_result is not None else None,
         'answer_trace':asdict(answer_result.trace) if answer_result is not None else None,
         'answer_error':answer_result.error_code if answer_result is not None else None,
         'final_citations':[snapshot.model_dump() for snapshot in answer_result.evidence] if answer_result is not None else [],
         'citation_readbacks':readbacks,'model_usage':usage.to_dict(),'duplicate_analysis':duplicate}
    return row,errors


def run_arm(root,dataset,admin_url,run_token,arm,chunk_size,overlap,generation_ids,output,code_sha):
    dataset_manifest,cases,sources=load_reviewed_dataset(dataset)
    database=create_isolated_database(admin_url,f'rag_eval_trust_{run_token}_{arm.lower()}')
    env={**os.environ,'RAG_DATABASE_URL':database,'RAG_CLOUD_ENABLED':'false','RAG_LANGFUSE_ENABLED':'false'}
    subprocess.run([sys.executable,'-m','alembic','-c',str(root/'alembic.ini'),'upgrade','head'],cwd=root,env=env,check=True)
    settings=Settings(database_url=database,storage_root=output/arm/'storage',cloud_enabled=False,langfuse_enabled=False,
                      inline_ingestion_enabled=False,max_chunk_chars=chunk_size,chunk_overlap=overlap)
    container=build_container(settings,agent_model=None)
    try:
        identities=model_identities(container.ollama)
        started=datetime.now(timezone.utc).isoformat()
        kb=container.store.create_knowledge_base('trust-'+arm,cloud_allowed=False)['id']
        bindings={}
        ingestion_started=time.perf_counter()
        with capture_usage() as ingestion_usage:
            for identity,(source,content) in sources.items():
                with (dataset/source['path']).open('rb') as file: stored=container.storage.put_stream(file)
                receipt=container.store.create_upload(kb,identity+'.md','text/markdown',stored)
                job=run_once(container.store,worker_id='trust-'+run_token+'-'+arm)
                if job is None or job['status']!='succeeded': raise ExperimentInvalidError('real_ingestion_failed')
                bindings[receipt['document_id']]={'document_id':identity,'source_version':source['source_version'],'text':content}
        ingestion_ms=(time.perf_counter()-ingestion_started)*1000
        index=read_index_snapshot(container.store,kb,bindings,container.ollama.embedding_model)
        config=effective_configuration(HybridRetriever(container.store,embedding_provider=container.ollama,top_k=5,candidate_k=32,rrf_k=60),
                                       container.store,ContextBuilder(8000))
        try:
            import tiktoken
            tokenizer=tiktoken.get_encoding('cl100k_base')
        except (ImportError,OSError): tokenizer=None
        rows=[]; errors=[]
        with capture_usage() as warmup_usage:
            container.ollama.answer('Reply READY.',120)
        for case in cases:
            row,case_errors=evaluate_case(container,kb,index,case,config,generate=case['case_id'] in generation_ids,tokenizer=tokenizer)
            rows.append(row); errors.extend(case_errors)
            print(json.dumps({'arm':arm,'case_id':case['case_id'],'status':row['status'],
                              'context_mode':row['context_mode'],'source_recall':row['metrics']['context_recall']}),flush=True)
        if model_identities(container.ollama)!=identities: raise ExperimentInvalidError('model_identity_changed_during_run')
        if committed_code_sha(root)!=code_sha: raise ExperimentInvalidError('code_changed_during_run')
        if read_index_snapshot(container.store,kb,bindings,container.ollama.embedding_model)['index_version']!=index['index_version']:
            raise ExperimentInvalidError('index_changed_during_run')
        corpus_hash=hashlib.sha256(json.dumps({identity:source['source_version'] for identity,(source,_) in sources.items()},sort_keys=True).encode()).hexdigest()
        runtime={'effective_config':config,'git_sha':code_sha,'corpus_hash':corpus_hash,'gold_set_hash':dataset_manifest['locked_sha256'],
                 'index_version':index['index_version'],'models':identities,'index_counts':index['index_counts']}
        summary=aggregate_metrics([row['metrics'] for row in rows])
        manifest={'experiment_id':str(uuid.uuid4()),'git_sha':code_sha,'dataset_version':dataset_manifest['dataset_version'],
                  'corpus_hash':corpus_hash,'gold_set_hash':runtime['gold_set_hash'],'index_version':runtime['index_version'],
                  'model_profile':'local-qwen3.5-4b','embedding_profile':'local-bge-m3',
                  'config_hash':hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                  'started_at':started,'ended_at':datetime.now(timezone.utc).isoformat(),
                  'environment':{'os_family':platform.system().lower(),'python_version':platform.python_version(),'architecture':'x86_64'},
                  'evaluation_mode':'real_postgres_local','status':'partial' if any(row['status']=='failed' for row in rows) else 'pass',
                  'sample_count':len(rows)}
        report={'schema_version':2,'dataset_version':manifest['dataset_version'],'runtime':runtime,
                'metrics':summary['metrics'],'metric_counts':summary['counts'],'cases':rows,'errors':errors,
                'ingestion_ms':ingestion_ms,'ingestion_usage':ingestion_usage.to_dict(),
                'warmup_usage':warmup_usage.to_dict(),'knowledge_base_id':kb,
                'database_name':f'rag_eval_trust_{run_token}_{arm.lower()}','query_mode':'q0',
                'warmup':'BGE ingested the corpus and Qwen performed a separately recorded READY call before cases; warmup usage not folded into case means'}
        directory=output/arm
        for name,value in [('manifest',manifest),('config',config),('metrics',summary),('report',report)]: _write(directory/(name+'.json'),value)
        for name,value in [('cases',rows),('errors',errors)]:
            (directory/(name+'.jsonl')).write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in value),encoding='utf-8')
        bundle=build_v2_bundle(report,manifest,config)
        _write(directory/'bundle.json',bundle)
        (directory/'summary.md').write_text(f'# Arm {arm}\n\nCode: {code_sha}\nSamples: {len(rows)}\nSource context recall: {summary["metrics"]["context_recall"]}\n\nJudge NOT_EVALUATED; context token estimates use cl100k_base.\n',encoding='utf-8')
        return {'arm':arm,'experiment_id':manifest['experiment_id'],'bundle':str(directory/'bundle.json'),
                'context_recall':summary['metrics']['context_recall'],'index_counts':index['index_counts']}
    finally: container.engine.dispose()


def main():
    parser=argparse.ArgumentParser(description='Run committed-code isolated real33-case RAG A/B; no remote upload.')
    parser.add_argument('--connection-file',type=Path,required=True,help='External local JSON with database_url for local postgres admin; never committed')
    parser.add_argument('--dataset',type=Path,default=Path('evaluations/trust_v1'))
    parser.add_argument('--output',type=Path,default=Path('var/trust-acceptance'))
    parser.add_argument('--generation-cases',default='trust-001,trust-010,trust-026,trust-029,trust-030,trust-031,trust-032,trust-033')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    code_sha=committed_code_sha(root)  # reject before any write/DB connection
    manifest,cases,_=load_reviewed_dataset(args.dataset)
    if manifest['sample_count']<30: raise ExperimentInvalidError('insufficient_official_sample_count')
    generation_ids=set(args.generation_cases.split(','))
    if not generation_ids<={case['case_id'] for case in cases}: raise ExperimentInvalidError('unknown_generation_case')
    admin=json.loads(args.connection_file.read_text(encoding='utf-8-sig'))['database_url']
    token=uuid.uuid4().hex[:12]
    output=args.output/token
    results=[]
    for arm,size,overlap in [('A',1200,120),('B',700,70)]:
        results.append(run_arm(root,args.dataset,admin,token,arm,size,overlap,generation_ids,output,code_sha))
    _write(output/'ab.json',{'git_sha':code_sha,'runs':results})
    print(json.dumps({'status':'REAL_AB_EXECUTED','git_sha':code_sha,'runs':results}))


if __name__=='__main__':
    try: main()
    except Exception as exc:
        # No connection URLs, credentials or raw exception strings on stdout.
        print(json.dumps({'status':'FAIL','error_code':getattr(exc,'code',type(exc).__name__)}))
        raise SystemExit(1)
