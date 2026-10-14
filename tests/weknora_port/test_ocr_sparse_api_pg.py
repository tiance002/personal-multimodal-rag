"""Sparse OCR passes actual original upload/index and persisted readback."""
import io,json,hashlib
from pathlib import Path
from PIL import Image
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from scripts.weknora_port_six_mock import prepare_public_fixture


def test_actual_standalone_image_sparse_ocr_preserves_source_tags():
    async def scenario():
        text='This is mostly text with a <b>bold</b> word.'
        async with isolated_mock_application(image_ocr_text=text) as (app,wire,root):
            kb=(await app.create_knowledge_base(app.principal,'Synthetic sparse OCR'))['id']
            await prepare_public_fixture(app,kb,'synthetic-ocr')
            output=io.BytesIO();Image.new('RGB',(60,60),'white').save(output,format='PNG');raw=output.getvalue()
            document=await app.upload_document(app.principal,kb,'sparse.png','image/png',raw)
            assert document['status']=='completed',document
            original=await app.read_document(app.principal,kb,document['id'])
            assert hashlib.sha256(original.content).hexdigest()==hashlib.sha256(raw).hexdigest()
            async with app.leases.lease() as guard:
                rows=await (await guard.connection.execute("SELECT id,content,is_enabled,parent_chunk_id FROM chunks WHERE tenant_id=%s AND knowledge_id=%s AND chunk_type='image_ocr'",
                    (app.principal.tenant_id,document['id']))).fetchall()
                assert len(rows)==1 and rows[0]['content']==text and rows[0]['is_enabled'] and rows[0]['parent_chunk_id']
                indexed=await (await guard.connection.execute('SELECT count(*) AS n FROM embeddings WHERE chunk_id=%s AND is_enabled=true',(rows[0]['id'],))).fetchone()
                assert indexed['n']==1
            Path('docs/weknora-port/evidence/P2_OCR_SPARSE_ENTRY_20261014.json').write_text(json.dumps({'kind':'ACTUAL_NATIVE_UPLOAD_PG_IMAGE_OCR_INDEX_READBACK','storage_root':str(root),
                'document':document['id'],'original_sha256':hashlib.sha256(raw).hexdigest(),'ocr_chunk':rows[0]['id'],'sparse_tags_retained':True,'active_index_count':1,'real_provider_calls':0,'closed_mock_calls':len(wire)},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
