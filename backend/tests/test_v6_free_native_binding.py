"""REAL LOCAL SQLite/CAS; provider/counter/lease SIMULATED; never live ledgers."""
from dataclasses import replace
import hashlib,json,sqlite3
import pytest
from backend.tests.test_v6_free_model_mainpath import (
    embedding_setup,embed_one,permit_for,context_for,result_store,SCOPE,REGISTRY,
)
from backend.tests.test_v6_xing_generation_binding import no_live_sender
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.ports.xing_zero_reservation import (
    SiliconFlowFreeBinding,free_dispatch_identity_sha256,free_dispatch_contract,
    free_model_requests,
)
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderRequestNotSent,ProviderUnavailable
from backend.tests.test_v6_embedding_batches import setup as indexing_setup
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.adapters.models.cloud import RerankAdmission,SimulatedRerankCounter,SimulatedXingSend
from types import SimpleNamespace
import uuid,os
from eval_center.paired_state import (
    ProviderBatchCall,ProviderBatchPlan,SQLiteProviderBatchSendClaims,
    register_run,begin_provider_batch_scope,provider_send_claim_record,stable_provider_request_id,
)


def native_setup(monkeypatch,tmp_path,*,failure=None):
    adapter,old_permit,_,gate,wires,payloads,_=embedding_setup(monkeypatch,count=2,failure=failure)
    calls=tuple(replace(c,qid='shared-Q',arm='SHARED_RETRIEVAL',call_ordinal=i+1,
        request_id=stable_provider_request_id(run_id=old_permit.run_id,qid='shared-Q',arm='SHARED_RETRIEVAL',purpose='query_embedding',call_ordinal=i+1),
        input_sha256=(hashlib.sha256(payloads[i]['input'][0].encode()).hexdigest(),),
        query_sha256=hashlib.sha256(payloads[i]['input'][0].encode()).hexdigest()) for i,c in enumerate(old_permit.calls))
    # Each distinct query has its own native parent/plan; use first here. A
    # second frozen slot is retained only to exercise the finite-window shape.
    permit=permit_for('embedding',calls)
    call=calls[0]
    native_call=ProviderBatchCall(1,(),(),call.input_sha256,call.wire_sha256,free_dispatch_identity_sha256(permit,call))
    plan=ProviderBatchPlan('query_embedding','siliconflow',permit.model,adapter.identity.fingerprint,
        hashlib.sha256(adapter.identity.chunking_index_identity.encode()).hexdigest(),
        free_dispatch_contract(permit,call)['scope_sha256'],(native_call,),call.query_sha256)
    path=tmp_path/'SIMULATED-center.sqlite3'
    register_run(path,run_id=permit.run_id,cohort_sha256='1'*64,source_manifest_sha256='2'*64,
        code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',deadline_utc=permit.deadline_utc,expected_qids=['shared-Q'],execution_purpose='RETRIEVAL')
    arm=begin_provider_batch_scope(path,run_id=permit.run_id,qid='shared-Q',input_identity_sha256='3'*64)
    callback=SQLiteProviderBatchSendClaims(path,permit.run_id,'shared-Q',arm,'3'*64,plan,'4'*64,lambda lease:None)
    original=adapter.transport
    transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=callback,sender=original.sender,clock=original.clock)
    binding=SiliconFlowFreeBinding(permit,transport,adapter.admission,adapter.free_binding.result_storage)
    guard=BudgetUsageGuard(gate,adapter.spec,0,zero_price_permit=permit)
    gateway=ProviderFactory(REGISTRY,
        enabled_roles=['embedding'],usage_guards={'embedding':guard},free_model_bindings={'embedding':binding}).build('embedding')
    context=replace(context_for(permit,call),batch_plan=plan)
    return gateway,permit,call,context,callback,path,gate,wires,payloads[0]


def invoke(setup):
    adapter,permit,call,context,callback,path,gate,wires,payload=setup
    with free_model_requests((context,)),model_access('embedding',allowed=True,scope=SCOPE):
        return adapter.embed(payload['input'],10)


def test_native_batch_sender_exact_identity_and_digest_only_recovery(monkeypatch,tmp_path):
    case=native_setup(monkeypatch,tmp_path);invoke(case)
    adapter,permit,call,context,callback,path,gate,wires,payload=case
    row=provider_send_claim_record(path,request_id=call.request_id)
    assert row['status']=='simulated' and row['purpose']=='query_embedding' and row['call_ordinal']==1
    assert row['batch_plan_sha256']==context.batch_plan.fingerprint
    assert set(row['result'])=={'result_sha256','result_ref','item_count'}
    recovered=adapter.free_binding.read_result(callback.reusable_result(request_id=call.request_id),request_id=call.request_id)
    assert recovered['budget_reservation_id'] in gate.reservations and len(recovered['value'])==1
    with pytest.raises(ProviderRequestNotSent):invoke(case)
    assert len(wires)==1


def test_native_unknown_consumed_no_output_or_replay(monkeypatch,tmp_path):
    case=native_setup(monkeypatch,tmp_path,failure=TimeoutError('SIMULATED'))
    with pytest.raises(ProviderUnavailable):invoke(case)
    adapter,permit,call,context,callback,path,gate,wires,payload=case
    row=provider_send_claim_record(path,request_id=call.request_id)
    assert row['status']=='unknown' and row['result'] is None
    assert callback.reusable_result(request_id=call.request_id) is None
    with pytest.raises(ProviderRequestNotSent):invoke(case)
    assert len(wires)==1 and list(gate.reservations.values())[-1]['state']=='unknown'


@pytest.mark.parametrize('field',['wire','profile','input_hash'])
def test_native_plan_mismatch_zero_send(monkeypatch,tmp_path,field):
    case=list(native_setup(monkeypatch,tmp_path));plan=case[3].batch_plan
    if field=='profile':plan=replace(plan,embedding_identity_sha256='f'*64)
    if field=='wire':plan=replace(plan,calls=(replace(plan.calls[0],wire_request_sha256='f'*64),))
    if field=='input_hash':plan=replace(plan,query_sha256='f'*64,calls=(replace(plan.calls[0],input_sha256=('f'*64,)),))
    case[3]=replace(case[3],batch_plan=plan)
    with pytest.raises((ProviderRequestNotSent,ValueError)):invoke(case)
    assert not case[7]
    with sqlite3.connect(case[5]) as db:assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0]==0


def test_tampered_saved_result_fails_hash_before_reuse(monkeypatch,tmp_path):
    case=native_setup(monkeypatch,tmp_path);invoke(case)
    adapter,permit,call,context,callback,path,gate,wires,payload=case
    summary=callback.reusable_result(request_id=call.request_id);digest=summary['result_sha256']
    artifact=adapter.free_binding.result_storage.path_for(f'objects/{digest[:2]}/{digest}')
    artifact.write_bytes(b'SIMULATED TAMPER')
    with pytest.raises(ProviderUnavailable,match='ARTIFACT_UNVERIFIED'):
        adapter.free_binding.read_result(summary,request_id=call.request_id)
    assert len(wires)==1


def native_index_setup(monkeypatch,tmp_path):
    repo,chunks,old,_,gate,wires,_=indexing_setup(monkeypatch)
    ids=tuple(str(uuid.uuid5(uuid.NAMESPACE_URL,f'SIMULATED-index-{i}')) for i in range(len(chunks)))
    calls=tuple(replace(c,request_id=stable_provider_request_id(run_id=old.run_id,qid=c.qid,arm='INDEXING',purpose=c.purpose,call_ordinal=c.call_ordinal),
        document_version_ids=('version',)*c.item_count,chunk_ids=ids[i*32:i*32+c.item_count],
        input_sha256=tuple(hashlib.sha256(x.embedding_content.encode()).hexdigest() for x in chunks[i*32:i*32+c.item_count]))
        for i,c in enumerate(old.calls))
    permit=permit_for('embedding',calls);adapter=repo.embedding_provider
    plan=ProviderBatchPlan('index_embedding','siliconflow',permit.model,adapter.identity.fingerprint,
        hashlib.sha256(adapter.identity.chunking_index_identity.encode()).hexdigest(),free_dispatch_contract(permit,calls[0])['scope_sha256'],
        tuple(ProviderBatchCall(c.call_ordinal,c.document_version_ids,c.chunk_ids,c.input_sha256,c.wire_sha256,
            free_dispatch_identity_sha256(permit,c)) for c in calls))
    path=tmp_path/'SIMULATED-index.sqlite3'
    register_run(path,run_id=permit.run_id,cohort_sha256='1'*64,source_manifest_sha256='2'*64,
        code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',deadline_utc=permit.deadline_utc,expected_qids=['INDEX-DOC'],execution_purpose='INDEXING')
    arm=begin_provider_batch_scope(path,run_id=permit.run_id,qid='INDEX-DOC',input_identity_sha256='3'*64)
    callback=SQLiteProviderBatchSendClaims(path,permit.run_id,'INDEX-DOC',arm,'3'*64,plan,'4'*64,lambda lease:None)
    transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=callback,sender=adapter.transport.sender,clock=adapter.transport.clock)
    binding=SiliconFlowFreeBinding(permit,transport,adapter.admission,adapter.free_binding.result_storage)
    guard=BudgetUsageGuard(gate,adapter.spec,0,zero_price_permit=permit)
    repo.embedding_provider=ProviderFactory(REGISTRY,enabled_roles=['embedding'],usage_guards={'embedding':guard},free_model_bindings={'embedding':binding}).build('embedding')
    return repo,chunks,permit,plan,ids,callback,path,wires


def test_actual_index_batches_bind_uuid_version_inputs_and_native_ordinals(monkeypatch,tmp_path):
    repo,chunks,permit,plan,ids,callback,path,wires=native_index_setup(monkeypatch,tmp_path)
    contexts=tuple(replace(context_for(permit,c),batch_plan=plan) for c in permit.calls)
    with free_model_requests(contexts),model_access('embedding',allowed=True,scope=SCOPE):
        assert repo._planned_index_chunk_ids(chunks,job_id='job',version_id='version')==ids
        vectors=repo._embed_index_chunks(chunks,job_id='job',worker_id='worker',claim_token='SIMULATED',lease_seconds=60,version_id='version')
    assert len(vectors)==65 and [len(w['input']) for w in wires]==[32,32,1]
    for c in permit.calls:
        row=provider_send_claim_record(path,request_id=c.request_id)
        assert row['call_ordinal']==c.call_ordinal and row['batch_plan_sha256']==plan.fingerprint
        recovered=repo.embedding_provider.free_binding.read_result(callback.reusable_result(request_id=c.request_id),request_id=c.request_id)
        assert len(recovered['value'])==c.item_count


@pytest.mark.parametrize('tamper',['version','chunk','input'])
def test_actual_index_metadata_mismatch_stops_before_first_model_and_claim(monkeypatch,tmp_path,tamper):
    repo,chunks,permit,plan,ids,callback,path,wires=native_index_setup(monkeypatch,tmp_path)
    context=replace(context_for(permit,permit.calls[0]),batch_plan=plan)
    versions=('wrong' if tamper=='version' else 'version',)*32
    chunk_ids=(str(uuid.uuid4()),)+ids[1:32] if tamper=='chunk' else ids[:32]
    inputs=[c.embedding_content for c in chunks[:32]]
    if tamper=='input':inputs[0]='SIMULATED ALTERED INPUT'
    with free_model_requests((context,)),model_access('embedding',allowed=True,scope=SCOPE):
        with pytest.raises((ProviderRequestNotSent,ValueError)):
            repo.embedding_provider.embed(inputs,60,purpose='index_embedding',batch_id=permit.calls[0].batch_id,
                lease_check=lambda:True,document_version_ids=versions,chunk_ids=chunk_ids)
    assert not wires
    with sqlite3.connect(path) as db:assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0]==0


def native_rerank_setup(monkeypatch,tmp_path):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-NOT-A-REAL-KEY')
    admission=RerankAdmission(SimulatedRerankCounter(lambda q,d:7),tokenizer_identity='SIMULATED-RERANK',execution_kind='SIMULATED')
    spec=REGISTRY.select('rerank');question='public';documents=['a','b']
    payload=dict(model=spec.model_id,query=question,documents=documents,top_n=2,return_documents=True)
    from backend.tests.test_v6_free_model_mainpath import call_for
    call=replace(call_for(payload,admission.input_profile_sha256,'rerank',count=2,input_bound=14,timeout=30),
        qid='rerank-Q',arm='SHARED_RETRIEVAL',request_id=stable_provider_request_id(run_id='SIMULATED-RUN',qid='rerank-Q',arm='SHARED_RETRIEVAL',purpose='rerank'),
        document_version_ids=('va','vb'),chunk_ids=('a','b'),candidate_order=('a','b'),
        input_sha256=tuple(hashlib.sha256(t.encode()).hexdigest() for t in documents),query_sha256=hashlib.sha256(question.encode()).hexdigest())
    permit=permit_for('rerank',(call,))
    plan=ProviderBatchPlan('rerank','siliconflow',permit.model,'a'*64,'b'*64,free_dispatch_contract(permit,call)['scope_sha256'],
        (ProviderBatchCall(1,call.document_version_ids,call.chunk_ids,call.input_sha256,call.wire_sha256,
            free_dispatch_identity_sha256(permit,call),call.candidate_order),),call.query_sha256)
    path=tmp_path/'SIMULATED-rerank.sqlite3'
    register_run(path,run_id=permit.run_id,cohort_sha256='1'*64,source_manifest_sha256='2'*64,
        code_sha='befd5478a680ffa779a1e8ede736260d9bac1060',deadline_utc=permit.deadline_utc,expected_qids=['rerank-Q'],execution_purpose='RETRIEVAL')
    arm=begin_provider_batch_scope(path,run_id=permit.run_id,qid='rerank-Q',input_identity_sha256='3'*64)
    callback=SQLiteProviderBatchSendClaims(path,permit.run_id,'rerank-Q',arm,'3'*64,plan,'4'*64,lambda lease:None)
    wires=[]
    def sender(request,timeout):
        wires.append(request.data)
        return dict(model=spec.model_id,results=[dict(index=i,relevance_score=1.0,document={'text':documents[i]}) for i in (1,0)],
            usage=dict(prompt_tokens=14,completion_tokens=0,total_tokens=14))
    from backend.tests.test_v6_free_model_mainpath import NOW
    transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=callback,sender=SimulatedXingSend(sender),clock=lambda:NOW)
    binding=SiliconFlowFreeBinding(permit,transport,admission,ContentAddressedStorage(os.environ['V6_SIMULATED_RESULT_ROOT']))
    from backend.app.application.budget import InMemoryBudgetGate
    guard=BudgetUsageGuard(InMemoryBudgetGate(0),spec,0,zero_price_permit=permit)
    ranker=ProviderFactory(REGISTRY,enabled_roles=['rerank'],usage_guards={'rerank':guard},free_model_bindings={'rerank':binding}).build('rerank')
    hits=[SimpleNamespace(chunk_id='a'),SimpleNamespace(chunk_id='b')]
    chunks={'a':SimpleNamespace(content='a',version_id='va'),'b':SimpleNamespace(content='b',version_id='vb')}
    return ranker,permit,call,replace(context_for(permit,call),batch_plan=plan),callback,path,wires,hits,chunks


@pytest.mark.parametrize('tamper',[None,'version','order','content','query'])
def test_native_rerank_uses_actual_candidates_and_digest_only_result(monkeypatch,tmp_path,tamper):
    ranker,permit,call,ctx,callback,path,wires,hits,chunks=native_rerank_setup(monkeypatch,tmp_path)
    question='public'
    if tamper=='version':chunks['a'].version_id='wrong'
    if tamper=='order':hits.reverse()
    if tamper=='content':chunks['a'].content='altered'
    if tamper=='query':question='altered'
    with free_model_requests((ctx,)),model_access('rerank',allowed=True,scope=SCOPE):
        if tamper is not None:
            with pytest.raises((ProviderRequestNotSent,ValueError)):ranker.rank(question,hits,chunks)
        else:
            assert ranker.rank(question,hits,chunks)==(hits[1],hits[0])
    if tamper is not None:
        assert not wires
        with sqlite3.connect(path) as db:assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0]==0
    else:
        summary=callback.reusable_result(request_id=call.request_id)
        assert summary['item_count']==2 and ranker.free_binding.read_result(summary,request_id=call.request_id)['value']['ranked_chunk_ids']==['b','a']
