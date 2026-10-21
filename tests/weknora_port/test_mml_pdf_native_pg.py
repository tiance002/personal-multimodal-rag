"""Full frozen MML PDF actual native parser/assets/index/profile; closed mock VLM."""
import json,hashlib
from pathlib import Path
from eval_center.weknora_frozen_gold import load_frozen_gold
from eval_center.weknora_source_packages import checked_bytes
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from scripts.weknora_port_six_mock import prepare_public_fixture
from backend.weknora_port.documents import _native_io_path


def test_actual_full_mml_pdf_long_assets_readback_and_index():
    async def scenario():
        frozen=load_frozen_gold('E:/RAG quention/var/reports/v6-full40-evaluation-entry-r4-r17/frozen-local/source_manifest_40.json','05a11e2d0e36be4d04bbb8472f10135ea703b464013e5ecdfd0297e69e28a05d')
        source=next(r for r in next(d for d in frozen.datasets if d.name=='mmlongbench-doc').files if r['doc_id'].startswith('catvsdogdlpycon15se-'))
        raw=checked_bytes(Path(source['path']),source['sha256'],source['bytes'])
        async with isolated_mock_application(synthetic_monthly_limit=10000000) as (runtime,wire,root):
            kb=(await runtime.create_knowledge_base(runtime.principal,'MML full original PDF assets'))['id'];await prepare_public_fixture(runtime,kb,'mmlongbench-doc')
            document=await runtime.upload_document(runtime.principal,kb,source['doc_id'],'application/pdf',raw)
            assert document['status']=='completed',document
            async with runtime.leases.lease() as guard:
                metadata=(await (await guard.connection.execute('SELECT metadata FROM knowledges WHERE tenant_id=%s AND id=%s',(runtime.principal.tenant_id,document['id']))).fetchone())['metadata']
            assert metadata['assets']
            for asset in metadata['assets']:
                data=_native_io_path(root/asset['path']).read_bytes();assert hashlib.sha256(data).hexdigest()==asset['sha256'] and len(data)==asset['size']
            original=await runtime.read_document(runtime.principal,kb,document['id']);assert original.content==raw
            Path('docs/weknora-port/evidence/P2_MML_FULL_PDF_ASSETS_20261014.json').write_text(json.dumps({'kind':'ACTUAL_FULL_FROZEN_MML_PDF_NATIVE_ENTRY','source_id':source['doc_id'],'source_sha256':source['sha256'],'native_document':document['id'],'knowledge_base':kb,'assets':len(metadata['assets']),'asset_hashes_verified':True,'original_readback':True,'indexed':metadata['indexed_count'],'windows_extended_length_io':True,'model_transport':'CLOSED_MOCK','real_provider_calls':0},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
