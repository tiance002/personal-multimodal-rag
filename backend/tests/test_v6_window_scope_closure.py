"""SIMULATED provider/lease/budget; REAL LOCAL TEMP SQLite, never canonical."""
from dataclasses import replace
import sqlite3
import pytest
from backend.tests.test_v6_free_native_binding import native_index_setup
from backend.tests.test_v6_free_model_mainpath import context_for,result_store,REGISTRY
from backend.tests.test_v6_xing_generation_binding import no_live_sender
from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport,SimulatedXingSend
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.ports.xing_zero_reservation import free_model_requests
from backend.app.ports.model_access import model_access,request_scope,scope_allows
from backend.app.ports.providers import ProviderRequestNotSent,ProviderUnavailable,EmbeddingResult
from backend.app.application.retrieval import HybridRetriever,InMemoryRetrievalRepository
from backend.app.domain.scope import Scope
from backend.app.domain.models import ChunkRecord
from eval_center.paired_state import provider_send_claim_record,check_provider_send_before_send,PairedContractError


def batch(adapter,permit,plan,chunks,number):
    call=permit.calls[number]
    with free_model_requests((replace(context_for(permit,call),batch_plan=plan),)),model_access('embedding',allowed=True,scope=permit.scope):
        return adapter.embed([c.embedding_content for c in chunks[number*32:number*32+call.item_count]],60,
            purpose='index_embedding',batch_id=call.batch_id,lease_check=lambda:True,
            document_version_ids=call.document_version_ids,chunk_ids=call.chunk_ids)


def test_real_native_overrun_survives_rebuilt_transport_and_callback(monkeypatch,tmp_path):
    repo,chunks,permit,plan,ids,callback,path,wires=native_index_setup(monkeypatch,tmp_path)
    original=repo.embedding_provider;sender=original.transport.sender
    def overrun(request,timeout):
        response=sender.send(request,timeout)
        response['usage']['prompt_tokens']+=1;response['usage']['total_tokens']+=1
        return response
    original.transport.sender=SimulatedXingSend(overrun)
    with pytest.raises(ProviderUnavailable,match='TOKEN_BOUND_EXCEEDED'):batch(original,permit,plan,chunks,0)
    old=provider_send_claim_record(path,request_id=permit.calls[0].request_id)
    assert old['status']=='unknown' and old['error_code']=='SF_FREE_TOKEN_BOUND_EXCEEDED' and old['result'] is None
    # Fresh objects over the exact same SQLite identity; no reset of durable state.
    fresh_callback=replace(callback)
    fresh_transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=fresh_callback,sender=sender,clock=original.transport.clock)
    binding=replace(original.free_binding,authorized_transport=fresh_transport)
    fresh=ProviderFactory(REGISTRY,enabled_roles=['embedding'],usage_guards={'embedding':original.usage_guard},free_model_bindings={'embedding':binding}).build('embedding')
    assert fresh.transport.blocked is False
    with pytest.raises(ProviderRequestNotSent):batch(fresh,permit,plan,chunks,1)
    assert len(wires)==1
    current=provider_send_claim_record(path,request_id=permit.calls[1].request_id)
    assert current is None or current['status']=='blocked'
    assert provider_send_claim_record(path,request_id=permit.calls[0].request_id)==old
    assert any(r['state']=='unknown' and r['settled_microunits'] is None for r in original.usage_guard.budget_gate.reservations.values())


@pytest.mark.parametrize('blocked',[False,True])
def test_normal_retrieval_forwards_existing_server_scope_and_keeps_child_filters(blocked):
    scope=Scope.from_ids(['kb-a'],['doc-a']);seen=[]
    class Repository(InMemoryRetrievalRepository):
        def embedding_scope_allowed(self,actual):
            assert actual is scope
            return not blocked
    class Embedding:
        provider_kind='cloud'
        def embed(self,texts,timeout_seconds):
            seen.append(('embedding',request_scope('embedding'),scope_allows('embedding')))
            if not scope_allows('embedding'):raise ProviderRequestNotSent('SIMULATED_EGRESS_DENIED')
            return EmbeddingResult([[1.0,0.0]],'SIMULATED',2,1.0,'profile')
    class Ranker:
        provider_kind='cloud'
        def rank(self,question,hits,chunks):
            seen.append(('rerank',request_scope('rerank'),scope_allows('rerank')))
            assert set(chunks)=={'in'}
            if not scope_allows('rerank'):raise ProviderRequestNotSent('SIMULATED_EGRESS_DENIED')
            return hits
    repo=Repository()
    for chunk in [ChunkRecord('in','kb-a','doc-a','v1','public evidence',embedding=(1.0,0.0),embedding_profile_id='profile'),
        ChunkRecord('other-kb','kb-b','doc-a','v1','public evidence',embedding=(1.0,0.0),embedding_profile_id='profile'),
        ChunkRecord('other-doc','kb-a','doc-b','v1','public evidence',embedding=(1.0,0.0),embedding_profile_id='profile'),
        ChunkRecord('old-version','kb-a','doc-a','v0','public evidence',is_current=False,embedding=(1.0,0.0),embedding_profile_id='profile'),
        ChunkRecord('parent','kb-a','doc-a','v1','public evidence',chunk_role='parent',embedding=(1.0,0.0),embedding_profile_id='profile')]:
        repo.add(chunk)
    result=HybridRetriever(repo,Embedding(),ranker=Ranker(),mode='hybrid').retrieve(scope,'public evidence')
    assert [item.chunk.chunk_id for item in result.items]==['in']
    assert [r[0] for r in seen]==['embedding','rerank']
    assert all(r[1]==(None if blocked else scope) and r[2] is (not blocked) for r in seen)
    assert request_scope('embedding') is None and request_scope('rerank') is None


@pytest.mark.parametrize('kind',['unknown_timeout','token_overrun','legacy_xing_overrun'])
def test_native_window_check_uses_persistent_state_without_reset_or_new_ledger(monkeypatch,tmp_path,kind):
    repo,chunks,permit,plan,ids,callback,path,wires=native_index_setup(monkeypatch,tmp_path)
    first=permit.calls[0];second=permit.calls[1]
    for c in (first,second):
        callback(request_id=c.request_id,purpose=c.purpose,provider='siliconflow',model=permit.model,role='embedding',
            request_identity_sha256=plan.calls[c.call_ordinal-1].request_identity_sha256,call_ordinal=c.call_ordinal)
    if kind=='legacy_xing_overrun':
        from eval_center.paired_state import finish_provider_send
        finish_provider_send(path,request_id=first.request_id,status='unknown',result={'provider_dispatch':{'request_window_blocked':True}})
    else:callback.settle(request_id=first.request_id,status='unknown',error_code='SF_FREE_TOKEN_BOUND_EXCEEDED' if kind=='token_overrun' else 'REQUEST_TIMEOUT')
    before=provider_send_claim_record(path,request_id=first.request_id)
    if kind=='unknown_timeout':callback.check_before_send(request_id=second.request_id)
    else:
        with pytest.raises(PairedContractError,match='window_blocked'):callback.check_before_send(request_id=second.request_id)
    assert provider_send_claim_record(path,request_id=first.request_id)==before
    assert not wires
    with sqlite3.connect(path) as db:assert db.execute('SELECT count(*) FROM paired_provider_calls').fetchone()[0]==2
