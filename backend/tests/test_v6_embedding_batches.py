"""SIMULATED ingestion batch/fencing contracts; no real DB or writes."""
from types import SimpleNamespace
from dataclasses import replace
import pytest,os
from backend.tests.test_v6_free_model_mainpath import (
    Claims,SCOPE,NOW,REGISTRY,permit_for,call_for,context_for,result_store,
)
from backend.tests.test_v6_xing_generation_binding import no_live_sender
from backend.app.adapters.models.cloud import (
    EmbeddingAdmission,SimulatedEmbeddingCounter,SimulatedXingSend,SiliconFlowAuthorizedTransport,
)
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.domain.adaptive_chunking import ChunkingConfig
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderRequestNotSent,ProviderUnavailable
from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding,free_model_requests
from backend.app.adapters.storage import ContentAddressedStorage


def setup(monkeypatch,size=65,*,failure=None):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-NOT-A-REAL-KEY')
    admission=EmbeddingAdmission(SimulatedEmbeddingCounter(lambda t:10),
        tokenizer_identity='SIMULATED-BGE-BATCH-PROFILE',non_truncating_verified=True)
    prototype=ProviderFactory(REGISTRY,embedding_admission=admission).build('embedding')
    chunks=[SimpleNamespace(embedding_content=f'public-{i}') for i in range(size)]
    n=(size+31)//32
    payloads=[dict(model=prototype.spec.model_id,input=[c.embedding_content for c in chunks[start:start+32]],encoding_format='float') for start in range(0,size,32)]
    calls=[replace(call_for(payload,prototype.input_profile_sha256,number=i,purpose='index_embedding',
        batch_id=f'version:job:{i}:{n}',count=len(payload['input']),input_bound=len(payload['input'])*10,timeout=60),
        qid='INDEX-DOC',call_ordinal=i+1) for i,payload in enumerate(payloads)]
    permit=permit_for('embedding',calls);claims=Claims();claims.permit=permit;gate=InMemoryBudgetGate(10);wires=[]
    def sender(request,timeout):
        import json
        wire=json.loads(request.data);wires.append(wire)
        if failure=='timeout':raise TimeoutError('SIMULATED')
        result=dict(model=prototype.spec.model_id,data=[dict(index=i,embedding=[1.0]+[0.0]*1023) for i in range(len(wire['input']))],
            usage=dict(prompt_tokens=len(wire['input'])*10,total_tokens=len(wire['input'])*10))
        if failure=='vector_count':result['data'].pop()
        return result
    guard=BudgetUsageGuard(gate,prototype.spec,0,zero_price_permit=permit)
    def build():
        transport=SiliconFlowAuthorizedTransport(permit,send_claim_callback=claims,sender=SimulatedXingSend(sender),clock=lambda:NOW)
        binding=SiliconFlowFreeBinding(permit,transport,admission,ContentAddressedStorage(os.environ['V6_SIMULATED_RESULT_ROOT']))
        return ProviderFactory(REGISTRY,enabled_roles=['embedding'],usage_guards={'embedding':guard},
            free_model_bindings={'embedding':binding}).build('embedding')
    repo=object.__new__(PostgresKnowledgeRepository)
    repo.embedding_provider=build();repo.chunking_config=ChunkingConfig();repo.renew_job=lambda *a,**k:True
    return repo,chunks,permit,claims,gate,wires,build


def invoke(repo,chunks,permit):
    with free_model_requests(tuple(context_for(permit,c) for c in permit.calls)),model_access('embedding',allowed=True,scope=SCOPE):
        return repo._embed_index_chunks(chunks,job_id='job',worker_id='worker',claim_token='SIMULATED-FENCE',lease_seconds=60,version_id='version')


def test_ingest_more_than_32_uses_one_instance_and_exact_stable_batches(monkeypatch):
    repo,chunks,permit,claims,gate,wires,_=setup(monkeypatch)
    original=repo.embedding_provider
    vectors=invoke(repo,chunks,permit)
    assert repo.embedding_provider is original and original.requests==3
    assert [len(w['input']) for w in wires]==[32,32,1] and len(vectors)==65
    assert [c.batch_id for c in permit.calls]==['version:job:0:3','version:job:1:3','version:job:2:3']
    assert all(row['claim'].purpose=='index_embedding' for row in claims.rows.values())
    assert all(r['state']=='unknown' and r['settled_microunits'] is None for r in gate.reservations.values())


@pytest.mark.parametrize('failure',['timeout','vector_count'])
def test_unknown_or_invalid_vectors_stop_before_next_batch_and_not_replayed(monkeypatch,failure):
    repo,chunks,permit,claims,gate,wires,build=setup(monkeypatch,failure=failure)
    with pytest.raises(ProviderUnavailable):invoke(repo,chunks,permit)
    assert len(wires)==len(claims.rows)==1 and list(gate.reservations.values())[0]['state']=='unknown'
    repo.embedding_provider=build() # Fresh transport still faces durable claim.
    with pytest.raises(ProviderRequestNotSent):invoke(repo,chunks,permit)
    assert len(wires)==len(claims.rows)==1
    assert [r['state'] for r in gate.reservations.values()]==['unknown','released']


def test_lost_lease_after_first_response_prevents_following_batch(monkeypatch):
    repo,chunks,permit,claims,gate,wires,_=setup(monkeypatch)
    repo.renew_job=lambda *a,**k:len(wires)==0
    with pytest.raises(RuntimeError,match='INGESTION_LEASE_LOST'):invoke(repo,chunks,permit)
    assert len(wires)==len(claims.rows)==1


def test_wrong_version_batch_identity_zero_send(monkeypatch):
    repo,chunks,permit,claims,gate,wires,_=setup(monkeypatch)
    with free_model_requests(tuple(context_for(permit,c) for c in permit.calls)),model_access('embedding',allowed=True,scope=SCOPE):
        with pytest.raises(ProviderRequestNotSent):
            repo._embed_index_chunks(chunks,job_id='job',worker_id='worker',claim_token='SIMULATED-FENCE',lease_seconds=60,version_id='other')
    assert not wires and not claims.rows and not gate.reservations


def test_index_batch_cannot_be_presented_as_query_embedding(monkeypatch):
    repo,chunks,permit,claims,gate,wires,_=setup(monkeypatch,size=1)
    with free_model_requests(tuple(context_for(permit,c) for c in permit.calls)),model_access('embedding',allowed=True,scope=SCOPE):
        with pytest.raises(ProviderRequestNotSent):repo.embedding_provider.embed([chunks[0].embedding_content],10)
    assert not wires and not claims.rows and not gate.reservations
