"""Read-only verify partial+remaining native cohort; never reuse or invoke a model."""
import argparse,asyncio,hashlib,json,statistics,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests/weknora_port'))
from tests.weknora_port.test_memory_storage_pg import approved_dsn,connection,actual_pg_run,EXPECTED
from eval_center.weknora_frozen_gold import load_frozen_gold
from eval_center.weknora_source_packages import checked_bytes
from eval_center.weknora_paired import MODES


def percentile(values,p):
    values=sorted(values)
    if not values:return None
    index=(len(values)-1)*p;lo=int(index);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(index-lo)


async def verify(args):
    frozen=load_frozen_gold(args.manifest,args.manifest_sha256)
    completed={};cases={};sources={};pages=[];receipts=[]
    for file,digest in zip(args.results,args.result_sha256):
        raw=checked_bytes(Path(file),digest);rows=[json.loads(line) for line in raw.decode().splitlines()]
        if rows[0]['event']!='execution_start' or rows[0]['manifest_sha256']!=frozen.manifest_sha256:
            raise ValueError('COHORT_FROZEN_IDENTITY_MISMATCH')
        receipts.append({'path':file,'sha256':digest,'bytes':len(raw),'git_head':rows[0]['git_head'],
            'command_complete':rows[-1].get('event')=='execution_complete'})
        for row in rows:
            if row['event']=='source_ingested':
                if row['native_document_id'] in sources:raise ValueError('COHORT_SOURCE_DUPLICATE')
                sources[row['native_document_id']]=row
            elif row['event']=='dataset_ingested':
                if row['dataset'] in completed:raise ValueError('COHORT_DATASET_DUPLICATE')
                completed[row['dataset']]=row
            elif row['event']=='native_case':
                key=(row['dataset'],row['qid'])
                if key in cases:raise ValueError('COHORT_QID_DUPLICATE')
                cases[key]=row
            elif row['event']=='native_omni_page':pages.append(row)
    if set(completed)!={d.name for d in frozen.datasets}:raise ValueError('COHORT_DATASET_MISSING')
    for dataset in frozen.datasets:
        stage=completed[dataset.name]
        if stage['source_documents']!=len(dataset.documents)+len(dataset.files) or stage['version']!=dataset.version:
            raise ValueError('COHORT_FULL_SOURCE_COUNT_VERSION_MISMATCH')
        if {qid for name,qid in cases if name==dataset.name}!={c.qid for c in dataset.cases}:
            raise ValueError('COHORT_FIXED_QIDS_MISMATCH')
    for dataset in frozen.datasets:
        kb=completed[dataset.name]['knowledge_base_id']
        expected={d.doc_id:(hashlib.sha256((d.title+'\n'+d.text).encode('utf-8')).hexdigest(),len((d.title+'\n'+d.text).encode('utf-8'))) for d in dataset.documents}
        expected.update({r['doc_id']:(r['sha256'],r['bytes']) for r in dataset.files})
        observed=[r for r in sources.values() if r['knowledge_base_id']==kb]
        if {r['source_id'] for r in observed}!=set(expected) or len(observed)!=len(expected):raise ValueError('COHORT_SOURCE_IDENTITIES_MISMATCH')
        for row in observed:
            if (row['sha256'],row['bytes'])!=expected[row['source_id']]:raise ValueError('COHORT_SOURCE_BYTES_CHANGED')
    if len(pages)!=24 or {r['page_id'] for r in pages}!={p['page_id'] for p in frozen.omni_pages}:
        raise ValueError('COHORT_OMNI_FIXED_PAGES_MISSING')
    for page in pages:
        if page['source']['sha256']!=next(p['image_sha256'] for p in frozen.omni_pages if p['page_id']==page['page_id']):raise ValueError('COHORT_OMNI_ORIGINAL_CHANGED')
    dsn=approved_dsn();conn,guard=await connection(dsn)
    citations=0;mode_rows={mode:[] for mode in MODES};model_calls=[];aux=[]
    try:
        for native,source in sources.items():
            row=await (await conn.execute('SELECT tenant_id,knowledge_base_id,parse_status,file_hash,file_size,metadata FROM knowledges WHERE id=%s AND deleted_at IS NULL',(native,))).fetchone()
            if not row or row['parse_status']!='completed' or row['knowledge_base_id']!=source['knowledge_base_id'] or row['file_hash']!=source['sha256'] or row['file_size']!=source['bytes']:
                raise ValueError('COHORT_SOURCE_PERSISTENCE_MISMATCH')
        for key,case in cases.items():
            if case['execution_kind']!='ISOLATED_MOCK' or case['model_quality_status']!='NOT_RUN_SIMULATED_MODELS' or case['gold_indexed'] is not False:
                raise ValueError('COHORT_EXECUTION_KIND_NOT_VERIFIED_MOCK')
            if [r['mode'] for r in case['modes']]!=list(MODES):raise ValueError('COHORT_FOUR_MODES_REQUIRED')
            if len({r['run_id'] for r in case['modes']})!=4:raise ValueError('COHORT_RUN_ID_REUSE')
            for mode in case['modes']:
                result=mode.get('result')
                if mode['status']!='COMPLETED' or not result or result['prompt_sha256']!=case['prompt_sha256'] or result['context_sha256']!=case['context_sha256']:
                    raise ValueError('COHORT_PAIRED_INPUT_OR_RESULT_MISMATCH')
                if mode['quality']['quality_metrics'] is not None:raise ValueError('COHORT_SIMULATED_QUALITY_CLAIM')
                ownership=await (await conn.execute('SELECT tenant_id,subject_id,knowledge_base_scope FROM port_turns WHERE run_id=%s',(mode['run_id'],))).fetchone()
                if not ownership or not ownership['subject_id'].startswith('fixture-owner:') or ownership['knowledge_base_scope']!=[completed[case['dataset']]['knowledge_base_id']]:
                    raise ValueError('COHORT_SERVER_ADMISSION_SCOPE_MISMATCH')
                message=await (await conn.execute("SELECT content,is_completed,turn_status FROM messages WHERE id=%s AND request_id=%s AND role='assistant' AND tenant_id=%s AND subject_id=%s AND deleted_at IS NULL",(mode['assistant_message_id'],mode['run_id'],ownership['tenant_id'],ownership['subject_id']))).fetchone()
                if not message or not message['is_completed'] or message['content']!=result['answer']:
                    raise ValueError('COHORT_ASSISTANT_PERSISTENCE_MISMATCH')
                for citation in mode['citations']:
                    if not citation['readable']:raise ValueError('COHORT_CITATION_UNREADABLE')
                    row=await (await conn.execute('SELECT c.knowledge_id,k.file_hash,k.parse_status FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id WHERE c.id=%s AND c.tenant_id=%s AND k.tenant_id=%s AND c.knowledge_base_id=%s AND c.deleted_at IS NULL AND k.deleted_at IS NULL',(citation['chunk_id'],ownership['tenant_id'],ownership['tenant_id'],completed[case['dataset']]['knowledge_base_id']))).fetchone()
                    if not row or row['parse_status']!='completed' or row['file_hash']!=citation['source_sha256'] or row['knowledge_id'] not in sources:
                        raise ValueError('COHORT_CITATION_BINDING_MISMATCH')
                    citations+=1
                mode_rows[mode['mode']].append({'generation':mode['generation_seconds'],'shared_preparation_plus_generation':case['preparation_seconds']+mode['generation_seconds']})
                model_calls.extend(mode['model_calls'])
            aux.extend(case['auxiliary_calls'])
        for call in model_calls+aux:
            row=await (await conn.execute('SELECT state,reserved_microunits,actual_microunits,price_source FROM send_calls WHERE request_id=%s',(call['request_id'],))).fetchone()
            if not row or any(row[field]!=call[field] for field in ('state','reserved_microunits','actual_microunits','price_source')):
                raise ValueError('COHORT_LEDGER_PERSISTENCE_MISMATCH')
            if row['price_source']!='SYNTHETIC-NOT-BILLING':raise ValueError('COHORT_REAL_SPENDING_DATA_FORBIDDEN')
    finally:await conn.close()
    report={'schema':'native-six-cohort-readonly-verification-v1','kind':'ACTUAL_NATIVE_ISOLATED_COHORT_WITH_MOCK_MODELS','system_identifier':EXPECTED,
        'manifest_sha256':frozen.manifest_sha256,'input_receipts':receipts,
        'sources':{d.name:completed[d.name]['source_documents'] for d in frozen.datasets},'omni_original_images':len(pages),
        'qa_cases':len(cases),'persisted_assistants':sum(len(r['modes']) for r in cases.values()),'paired_capture_verified':True,
        'citation_readbacks_verified':citations,'mock_answer_model_calls':len(model_calls),'mock_auxiliary_calls':len(aux),
        'known_mock_answer_cost_microunits':sum(r['actual_microunits'] or 0 for r in model_calls),
        'unknown_mock_aux_occupied_microunits':sum(r['reserved_microunits'] for r in aux if r['state']=='unknown'),
        'latency':{mode:{field:{'p50_seconds':percentile([r[field] for r in rows],.5),'p95_seconds':percentile([r[field] for r in rows],.95)} for field in ('generation','shared_preparation_plus_generation')} for mode,rows in mode_rows.items()},
        'latency_kind':'MEASURED_MOCK_GENERATION_AND_RECONSTRUCTED_COMMON_PREPARATION_NOT_REAL_PROVIDER_LATENCY',
        'omni_parsing':'NOT_RUN_NO_NATIVE_GEOMETRY_PREDICTION','omni_independent_qa':'NOT_RUN_OVERLAY_UNAVAILABLE','mml_page_base':'UNKNOWN',
        'model_quality':'NOT_RUN_SIMULATED_MODELS','real_provider_calls':0,'full_source_parity':'NOT_CLAIMED','whole_original_command_pass':False,'cutover':'NOT_RUN'}
    target=Path(args.output)
    if target.exists():raise ValueError('COHORT_REPORT_PATH_ALREADY_EXISTS')
    target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--manifest-sha256',required=True);parser.add_argument('--results',nargs='+',required=True);parser.add_argument('--result-sha256',nargs='+',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if len(args.results)!=len(args.result_sha256):raise ValueError('RESULT_HASH_COUNT_MISMATCH')
    actual_pg_run(verify(args))
