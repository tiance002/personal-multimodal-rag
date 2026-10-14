"""Opt-in full frozen six-dataset native execution; CLOSED mock transport only."""
import argparse,asyncio,json,sys,hashlib,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests/weknora_port'))
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from eval_center.weknora_frozen_gold import load_frozen_gold
from eval_center.weknora_six_runner import NativeSixRunner


async def prepare_public_fixture(application,kb,dataset):
    await application.update_knowledge_base_consent(application.principal,kb,True)
    async with application.leases.lease() as guard:
        snapshot=await application._snapshot(guard)
        await guard.connection.execute('UPDATE knowledge_bases SET vlm_config=%s::jsonb,image_processing_config=%s::jsonb WHERE tenant_id=%s AND id=%s',
            (json.dumps({'enabled':True,'model_id':snapshot.model_id('vlm'),'description_language':'Chinese (Simplified)','custom_instructions':'BUSINESS_ONLY_CAPTION'}),
            json.dumps({'enable_multimodal':True}),application.principal.tenant_id,kb))


async def main(args):
    frozen=load_frozen_gold(args.manifest,args.manifest_sha256)
    output=Path(args.output)
    if output.exists():raise ValueError('NEW_RESULT_PATH_REQUIRED_HISTORY_PRESERVED')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as handle:
        async def record(row):
            handle.write(json.dumps(row,ensure_ascii=False,default=str)+'\n');handle.flush()
            if row['event'] in ('dataset_ingested','native_case','native_omni_page') or (row['event']=='source_ingested' and len(runner.records)%100==0):
                print(json.dumps({'event':row['event'],'dataset':row.get('dataset'),'qid':row.get('qid'),'page_id':row.get('page_id')},ensure_ascii=False),flush=True)
        paths=sorted((ROOT/'backend/weknora_port').rglob('*.py'))+[ROOT/'eval_center'/name for name in ('weknora_frozen_gold.py','weknora_six_runner.py','weknora_paired.py','weknora_scoring.py')]+[Path(__file__),ROOT/'tests/weknora_port/native_fixture.py',ROOT/'tests/weknora_port/test_runtime_pg.py']
        await record({'event':'execution_start','manifest_sha256':frozen.manifest_sha256,
            'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            'model_quality':'NOT_RUN_SIMULATED_MODELS','independent_omni_qa':'NOT_RUN_OVERLAY_UNAVAILABLE'})
        async with isolated_mock_application(synthetic_monthly_limit=10000000) as (application,wire,root):
            await record({'event':'fixture_capacity','monthly_limit_synthetic_microunits':10000000,'price_source':'SYNTHETIC-NOT-BILLING','real_spending_authorized':False})
            runner=NativeSixRunner(application,execution_kind='ISOLATED_MOCK',prepare_kb=prepare_public_fixture,on_record=record,ingest_workers=args.ingest_workers)
            await runner.run(frozen)
            await record({'event':'execution_complete','transport':'httpx.MockTransport fixture.invalid only',
                'supplier_requests_real':0,'wire_calls_simulated':len(wire),'storage_root':str(root),
                'model_quality':'NOT_RUN_SIMULATED_MODELS','stage_acceptance':'NOT_CLAIMED'})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--manifest-sha256',required=True);parser.add_argument('--output',required=True);parser.add_argument('--ingest-workers',type=int,default=4)
    actual_pg_run(main(parser.parse_args()))
