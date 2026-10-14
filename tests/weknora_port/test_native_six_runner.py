"""Actual dedicated PG/Redis native six-runner integration, synthetic cohort."""
import asyncio,io,json,hashlib
from types import SimpleNamespace
from PIL import Image
from dataclasses import replace
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from eval_center.public_data import PublicDocument,PublicCase
from eval_center.weknora_frozen_gold import NativeDataset,FrozenGold
from eval_center.weknora_six_runner import NativeSixRunner
from scripts.weknora_port_six_mock import prepare_public_fixture


def test_actual_six_adapter_native_ingestion_pairing_and_gold_exclusion(tmp_path):
    async def scenario():
        import fitz
        pdf=fitz.open();page=pdf.new_page();page.insert_text((50,50),'Native PDF public source evidence.');raw_pdf=pdf.tobytes();pdf.close()
        pdfpath=tmp_path/'original.pdf';pdfpath.write_bytes(raw_pdf)
        cases=lambda name:(PublicCase(name+'-q','extract source',{name+'-source':1},'synthetic',('DO_NOT_SEND_GOLD_ANSWER',)),)
        datasets=tuple(NativeDataset(name,'SYNTHETIC_NOT_FROZEN_ACCEPTANCE',cases(name),
            (PublicDocument(name+'-source','public source','Blue widgets use protocol W-17.'),))
            for name in ('scifact','miracl-zh','longbench-zh','hotpotqa'))
        datasets+= (NativeDataset('mmlongbench-doc','SYNTHETIC_NOT_FROZEN_ACCEPTANCE',(replace(cases('mmlongbench-doc')[0],qrels={'mmlongbench-doc-source.pdf':1}),),files=({'doc_id':'mmlongbench-doc-source.pdf','path':str(pdfpath),'sha256':hashlib.sha256(raw_pdf).hexdigest(),'bytes':len(raw_pdf)},)),)
        imagepath=tmp_path/'page.png';Image.new('RGB',(100,100),'white').save(imagepath)
        page={'page_id':'synthetic-omni-page','document_id':'page.png','image_path':str(imagepath),'image_sha256':hashlib.sha256(imagepath.read_bytes()).hexdigest()}
        frozen=FrozenGold('synthetic',datasets,(page,),{page['page_id']:{'layout_dets':[{'text':'DO_NOT_SEND_OMNI_GOLD'}]}},())
        async with isolated_mock_application() as (application,wire,root):
            runner=NativeSixRunner(application,execution_kind='ISOLATED_MOCK',prepare_kb=prepare_public_fixture,ingest_workers=4)
            result=await runner.run(frozen)
            cases=[r for r in result['records'] if r['event']=='native_case'];omni=[r for r in result['records'] if r['event']=='native_omni_page']
            assert len(cases)==5 and len(omni)==1
            assert 0<application.fixture_connection_pool.count<=32
            assert all(len(r['modes'])==4 and r['model_quality_status']=='NOT_RUN_SIMULATED_MODELS' for r in cases)
            assert all(m['status']=='COMPLETED' and m['quality']['quality_metrics'] is None for r in cases for m in r['modes'])
            assert all(m['citations'] and all(c['readable'] for c in m['citations']) for r in cases for m in r['modes'] if m['mode'] in ('EXPENSIVE_ONLY','DYNAMIC'))
            assert omni[0]['parsing']['status']=='NOT_RUN'
            assert 'DO_NOT_SEND_GOLD_ANSWER' not in json.dumps(wire) and 'DO_NOT_SEND_OMNI_GOLD' not in json.dumps(wire)
            assert all(r['gold_indexed'] is False for r in cases+omni)
            from pathlib import Path
            Path('docs/weknora-port/evidence/P7_SIX_NATIVE_SYNTHETIC_20261014.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2),encoding='utf-8')
    actual_pg_run(scenario())
