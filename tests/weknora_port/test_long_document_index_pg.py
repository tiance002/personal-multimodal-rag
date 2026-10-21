"""Actual large frozen public source native indexing, providers explicitly mocked."""
import hashlib,json
from pathlib import Path
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from scripts.weknora_port_six_mock import prepare_public_fixture
from eval_center.weknora_frozen_gold import load_frozen_gold


def test_actual_longbench_source_crosses_parallel_batch_boundary():
    async def scenario():
        frozen=load_frozen_gold('E:/RAG quention/var/reports/v6-full40-evaluation-entry-r4-r17/frozen-local/source_manifest_40.json','05a11e2d0e36be4d04bbb8472f10135ea703b464013e5ecdfd0297e69e28a05d')
        dataset=next(d for d in frozen.datasets if d.name=='longbench-zh');source=dataset.documents[0]
        raw=(source.title+'\n'+source.text).encode('utf-8')
        async with isolated_mock_application() as (runtime,wire,root):
            kb=(await runtime.create_knowledge_base(runtime.principal,'LongBench source index boundary'))['id']
            await prepare_public_fixture(runtime,kb,'longbench-zh')
            document=await runtime.upload_document(runtime.principal,kb,'longbench-source.txt','text/plain',raw)
            assert document['status']=='completed',document
            async with runtime.leases.lease() as guard:
                chunks=(await (await guard.connection.execute('SELECT count(*) AS n FROM chunks WHERE tenant_id=%s AND knowledge_id=%s AND deleted_at IS NULL',(runtime.principal.tenant_id,document['id']))).fetchone())['n']
                indices=(await (await guard.connection.execute('SELECT count(*) AS n FROM embeddings WHERE knowledge_id=%s AND is_enabled=true',(document['id'],))).fetchone())['n']
            assert chunks>40 and indices==chunks
            original=await runtime.read_document(runtime.principal,kb,document['id']);assert original.content==raw
            Path('docs/weknora-port/evidence/P2_LONG_DOCUMENT_INDEX_20261014.json').write_text(json.dumps({'kind':'ACTUAL_FROZEN_LONG_SOURCE_INDEX_BOUNDARY','source_id':source.doc_id,'source_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'native_document':document['id'],'knowledge_base':kb,'chunks':chunks,'active_indices':indices,'original_hash_verified':True,'model_transport':'CLOSED_MOCK','real_provider_calls':0},indent=2),encoding='utf-8')
    actual_pg_run(scenario())
