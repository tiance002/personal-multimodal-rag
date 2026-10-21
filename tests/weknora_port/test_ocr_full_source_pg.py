"""Exact fixed Go full OCR/table fixtures versus Python rules and native upload."""
import json,io,hashlib
from pathlib import Path
from PIL import Image
from backend.weknora_port.image_multimodal import sanitize_ocr
from backend.weknora_port.ingestion.html_conversion import normalize_html_tables
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from scripts.weknora_port_six_mock import prepare_public_fixture


def test_fixed_go_full_ocr_same_fixture_and_native_image_entry():
    value=json.loads((Path(__file__).parent/'fixtures/ocr_full_go_oracle.json').read_text(encoding='utf-8'))
    assert value['exit_code']==0 and len(value['cases'])==16
    for row in value['cases']:assert sanitize_ocr(row['input'])==row['output'],row
    async def scenario():
        receipts=[]
        for index in (5,9,11):
            row=value['cases'][index]
            async with isolated_mock_application(image_ocr_text=row['input']) as (app,wire,root):
                kb=(await app.create_knowledge_base(app.principal,'Full OCR source fixture'))['id'];await prepare_public_fixture(app,kb,'ocr')
                image=io.BytesIO();Image.new('RGB',(60,60),'white').save(image,format='PNG');raw=image.getvalue()
                document=await app.upload_document(app.principal,kb,'html-source.png','image/png',raw);assert document['status']=='completed',document
                assert (await app.read_document(app.principal,kb,document['id'])).content==raw
                async with app.leases.lease() as guard:
                    chunks=await (await guard.connection.execute("SELECT id,content,parent_chunk_id,is_enabled FROM chunks WHERE tenant_id=%s AND knowledge_id=%s AND chunk_type='image_ocr'",(app.principal.tenant_id,document['id']))).fetchall()
                    assert len(chunks)==1 and chunks[0]['content']==row['output'] and chunks[0]['parent_chunk_id'] and chunks[0]['is_enabled']
                    indexes=(await (await guard.connection.execute('SELECT count(*) AS n FROM embeddings WHERE chunk_id=%s AND is_enabled=true',(chunks[0]['id'],))).fetchone())['n'];assert indexes==1
                receipts.append({'oracle_case':index,'document':document['id'],'ocr_sha256':hashlib.sha256(row['output'].encode()).hexdigest(),'active_indices':1,'original_readback_equal':True,'closed_mock_calls':len(wire),'real_provider_calls':0})
        Path('docs/weknora-port/evidence/P2_OCR_FULL_PY_ENTRY_20261014.json').write_text(json.dumps({'kind':'FIXED_GO_OCR_TABLE_SOURCE_VS_PYTHON_RULES_NATIVE_UPLOAD','oracle_cases':16,'native_cases':receipts,'renderer':'pinned hash-bound third-party library primitive; not source Go application/oracle','real_provider_calls':0,'full_image_worker_parity':'NOT_CLAIMED'},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
