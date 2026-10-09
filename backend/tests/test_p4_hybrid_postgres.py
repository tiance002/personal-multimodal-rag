"""REAL registered PG/pgvector; SIMULATED model HTTP and budget, rollback."""
from contextlib import contextmanager
from dataclasses import replace
import json,os
import pytest
from sqlalchemy import text
from scripts import verify_p4_hybrid_once as hybrid
from scripts import verify_p4_rerank_once as once
from scripts import with_clean_slate as clean
from backend.app.adapters.models import cloud
from backend.app.application import budget
from backend.tests.test_p4_hybrid_once import approval

@pytest.mark.parametrize('outcome',['success','embedding401','rerank403','document_null','dense_empty','keyword_empty','insufficient_budget'])
def test_actual_hybrid_entry_pg_simulated_models_and_persistent_contract(monkeypatch,tmp_path,outcome):
    if os.getenv('RAG_R3_CLEAN_SLATE_TEST')!='1':pytest.skip('Explicit Clean-slate opt-in absent')
    # The project loader is replaced BEFORE it can read .env.local.
    monkeypatch.setattr(clean,'_read_project_secrets',lambda path:{'SILICONFLOW_API_KEY':'SIMULATED_HYBRID_KEY'})
    gate=budget.InMemoryBudgetGate(10)
    for i in range(8):
        old=gate.reserve(run_id=None,provider='siliconflow',model_name='historical',capability='rerank',estimate_microunits=1).reservation_id
        gate.mark_unknown(old)
    old_rows={k:dict(v) for k,v in gate.reservations.items()}
    monkeypatch.setattr(budget,'PostgresBudgetGate',lambda *a,**k:gate)
    admission=cloud.EmbeddingAdmission.from_bge_m3_file(hybrid.TOKENIZER)
    sends=[]
    def send(request,timeout):
        payload=json.loads(request.data);role='embedding' if request.full_url.endswith('/embeddings') else 'rerank'
        sends.append(role);assert timeout==30
        from urllib.error import HTTPError
        if outcome==role+'401' or outcome==role+'403':raise HTTPError(request.full_url,401 if role=='embedding' else 403,'SIMULATED',{},None)
        if role=='embedding':
            rows=[dict(index=i,embedding=[float(i+1),1.]+[0.]*1022) for i in range(len(payload['input']))]
            data=dict(model='BAAI/bge-m3',data=rows,usage={'prompt_tokens':admission.count(payload['input']),'total_tokens':admission.count(payload['input'])})
        else:
            assert payload['return_documents'] is True
            data=dict(results=[dict(index=i,relevance_score=.95-n*.1,document={'text':payload['documents'][i]}) for n,i in enumerate(reversed(range(len(payload['documents']))))])
            if outcome=='document_null':data['results'][0]['document']=None
        return cloud._ReceivedResponse(data,200,len(json.dumps(data).encode()),'application/json')
    monkeypatch.setattr(cloud,'_send',send)
    output=tmp_path/'SIMULATED-hybrid'
    before={}
    @contextmanager
    def factory():
        with once.live_container(hybrid.REQUEST_ID,output,retrieval_mode='hybrid',embedding_admission=admission) as (container,identity):
            with container.engine.connect() as c:before.update(hybrid.table_counts(c))
            if outcome=='dense_empty':monkeypatch.setattr(container.store,'vector_candidates',lambda *a,**k:[])
            if outcome=='keyword_empty':monkeypatch.setattr(container.store,'keyword_candidates',lambda *a,**k:[])
            if outcome=='insufficient_budget':
                # Real SQL used=8, but normal per-call gate now has only one unit.
                gate.monthly_budget_microunits=9
            try:yield container,identity
            finally:
                with container.engine.connect() as c:assert hybrid.table_counts(c)==before
    result=hybrid.execute_simulated(approval(hybrid.design(admission)[0]),admission,output,factory)
    receipt=json.loads((output/'live-receipt.json').read_text())
    assert receipt['evidence_kind']=='SIMULATED' and all(gate.reservations[k]==v for k,v in old_rows.items())
    assert sends.count('embedding')<=1 and sends.count('rerank')<=1
    assert 'SIMULATED_HYBRID_KEY' not in json.dumps(receipt)
    if outcome=='success':
        assert result==0 and receipt['status']=='HYBRID_JOINT_PASS' and sends==['embedding','rerank']
        assert receipt['pipeline']['retrieval_mode']=='hybrid' and set(receipt['channel_rankings'])=={'keyword','vector'}
        assert receipt['pipeline']['query_cache_hits']==1 and receipt['pipeline']['stats']['parent_count']==1
        assert all(set(v)=={'keyword','vector'} for v in receipt['rrf_contributions'].values())
        assert len(gate.reservations)==10 and all(r['state']=='unknown' for r in gate.reservations.values())
    else:
        assert result==1 and receipt['status']=='BLOCKED'
        assert sends==(['embedding','rerank'] if outcome in {'rerank403','document_null'} else ['embedding'])
    saved=(output/'live-receipt.json').read_bytes()
    with pytest.raises(FileExistsError):hybrid.execute_simulated(approval(hybrid.design(admission)[0]),admission,output,lambda:pytest.fail('REENTERED'))
    assert (output/'live-receipt.json').read_bytes()==saved
