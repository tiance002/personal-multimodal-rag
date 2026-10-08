"""REAL authorized PostgreSQL, SIMULATED provider errors, rollback only."""
from io import BytesIO
import json
import uuid

import pytest
from sqlalchemy import text
from urllib.error import HTTPError

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.domain.model_registry import ModelRegistry
from backend.tests.test_clean_slate_model_profiles import isolated_database
from backend.tests.test_model_provider_contracts import SimulatedGuard, embedding


@pytest.mark.parametrize('error', ['401','403','429','count','dimension','nan','inf'])
def test_failed_embedding_does_not_activate_or_persist_vectors(isolated_database, monkeypatch, tmp_path, error):
    engine, c = isolated_database
    adapter, calls = embedding(ModelRegistry.frozen_defaults(), SimulatedGuard())
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-R4-ONLY')
    def fail(request, timeout):
        calls.append('SIMULATED_REQUEST')
        if error.isdigit():
            raise HTTPError('https://api.siliconflow.cn/v1/embeddings', int(error), 'SIMULATED', {}, None)
        inputs = json.loads(request.data)['input']
        rows = [dict(index=i, embedding=[1.0]*1024) for i in range(len(inputs))]
        if error == 'count': rows.pop()
        if error == 'dimension': rows[0]['embedding'] = [1.0]
        if error in {'nan','inf'}: rows[0]['embedding'][0] = float(error)
        return dict(model='BAAI/bge-m3', data=rows)
    adapter.transport = fail
    repo = PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path), embedding_provider=adapter)
    kb = repo.create_knowledge_base('r4-error-'+uuid.uuid4().hex, cloud_allowed=True)
    source = repo.storage.put_stream(BytesIO('# 合成标题\n费用 42.75，完整内容。'.encode()))
    receipt = repo.create_upload(kb['id'], 'synthetic.md', 'text/markdown', source)
    result = repo.process_job(receipt['job_id'])
    assert result['status'] == 'failed'
    assert repo.get_document(receipt['document_id'])['active_version_id'] is None
    assert c.execute(text('SELECT index_status FROM document_versions WHERE id=:v'),
                     {'v':receipt['version_id']}).scalar_one() == 'failed'
    assert c.execute(text('SELECT count(*) FROM chunk_embeddings')).scalar_one() == 0
    assert c.execute(text('SELECT count(*) FROM chunks')).scalar_one() == 0
    assert len(calls) == 1 and adapter.requests == 1  # no retry or local fallback


def test_live_entry_rehearsal_real_sql_simulated_http(isolated_database, monkeypatch, tmp_path):
    """Exercise the same runner before a live send, with explicit SIMULATED HTTP.

    All SQL, cleanup ownership checks and budget accounting are real; outer
    rollback restores even the simulated call audit. Real-run heartbeat is not
    disabled by the runner; this shared-transaction fixture alone isolates it.
    """
    import scripts.verify_p4_cloud_embedding as runner
    from backend.app.adapters.models.factory import ProviderFactory
    engine, c = isolated_database
    class SharedTransaction:
        connect = engine.connect
        begin = engine.begin
        def dispose(self): pass
    class SimulatedFactory(ProviderFactory):
        def build(self, role, key=None):
            adapter = super().build(role, key)
            if role == 'embedding':
                def transport(request, timeout):
                    inputs=json.loads(request.data)['input']
                    return dict(model='BAAI/bge-m3',data=[dict(index=i,embedding=[1.0]+[0.0]*1023)
                                                        for i in range(len(inputs))])
                adapter.transport=transport
            return adapter
    original_resolve=runner.resolve_environment
    monkeypatch.setattr(runner,'resolve_environment',lambda *a,**kw:{
        **original_resolve(*a,**kw),'SILICONFLOW_API_KEY':'SIMULATED-R4-REHEARSAL'})
    monkeypatch.setattr(runner,'create_engine',lambda *a,**kw:SharedTransaction())
    monkeypatch.setattr(runner,'ProviderFactory',SimulatedFactory)
    monkeypatch.setattr(runner,'OUT',tmp_path/'rehearsal')
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-R4-REHEARSAL')
    from backend.tests.test_p4_bge_admission import ASSET
    assert runner.run(ASSET)==0
    receipt=json.loads((runner.OUT/'live-receipt.json').read_text(encoding='utf-8'))
    assert receipt['cleanup']=='PASS' and receipt['actual_request_count']==3
    assert receipt['parent_count']>0 and receipt['child_count']>1
    assert receipt['counts_after']['model_calls']==3
    assert all(v==0 for k,v in receipt['counts_after'].items() if k!='model_calls')
    # SIMULATED model audit stays inside the fixture's outer rollback.
    assert c.execute(text('SELECT count(*) FROM chunks')).scalar_one()==0
    assert 'SIMULATED-R4-REHEARSAL' not in (runner.OUT/'live-receipt.json').read_text(encoding='utf-8')
