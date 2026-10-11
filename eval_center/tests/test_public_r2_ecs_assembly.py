"""Normal bootstrap/CLI, SIMULATED permits/IDs; never live DB/model/ledger."""
from dataclasses import replace
from datetime import datetime, timezone
from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
import hashlib,json
import pytest
from sqlalchemy.engine import Engine, make_url

from backend.app.bootstrap import Container
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.adapters.models.cloud import (
    XingAuthorizedTransport, SimulatedXingSend, SiliconFlowAuthorizedTransport,
    EmbeddingAdmission, SimulatedEmbeddingCounter, V6XingRequestWindow,
)
from backend.app.application.rule_router_v1 import V6XingGenerationBinding
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.ports.xing_zero_reservation import (
    SiliconFlowFreePermit, SiliconFlowFreeBinding, SiliconFlowZeroPriceContract,
    FreeModelPlannedCall, FREE_OWNER_DEADLINE,
)
from backend.tests.test_v6_xing_generation_binding import counter, no_live_sender
from eval_center.tests.test_public_paired_meet import native_fixture, composition_for, _answer_request, SOURCE, COHORT
from eval_center.tests.test_verified_index import identity as legacy_identity
from eval_center.paired_state import CurrentProviderSendClaims, load_frozen_cohort, sha256_file, stable_provider_request_id
from eval_center.public_runner import (
    PairedProviderCallBudget, PairedRuntimeAssemblyBlocked, build_normal_paired_runtime,
    _validate_paired_query_embedding,
)
from eval_center.public_paired_reporting import time_profile
from eval_center.verified_index import (
    ECSReadOnlyDatabaseProfile, IndexReuseRejected, validate_index_identity,
    read_only_database_url, require_read_only_connection,
)

RUN='SIMULATED-R2-INDEPENDENT-ASSEMBLY'
REGISTRY=ModelRegistry.frozen_defaults()


@pytest.fixture(autouse=True)
def forbid_real_io(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-ONLY')
    monkeypatch.setenv('DEEPSEEK_API_KEY','SIMULATED-ONLY')
    monkeypatch.setattr(Engine,'connect',lambda *a,**k:pytest.fail('REAL DB connection forbidden'))
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener',
        lambda *a,**k:pytest.fail('REAL DeepSeek forbidden'))
    # R2 is a historical offline fixture, independent of today's wall clock.
    # Individual expiry tests override this lawful instant explicitly below.
    class HistoricalR2Clock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,10,3,tzinfo=timezone.utc)
    for target in ('eval_center.public_runner.datetime','eval_center.public_benchmark_cli.datetime',
                   'eval_center.paired_state.datetime'):
        monkeypatch.setattr(target,HistoricalR2Clock)


def freeze(permit):
    return replace(permit,frozen_plan_sha256=permit.fingerprint)


def make_permit(scope,role,calls):
    model=REGISTRY.select(role).model_id
    return freeze(SiliconFlowFreePermit('SIMULATED-R2-'+role,RUN,scope,FREE_OWNER_DEADLINE,
        sha256_file(SOURCE),'a'*64,model,role,SiliconFlowZeroPriceContract(model),tuple(calls),
        sum(c.input_token_bound+c.output_token_bound for c in calls),'0'*64,'SIMULATED'))


def offline_composition(root,counter):
    runtime,receipts=native_fixture(root/'SIMULATED-bindings',counter)
    composition=composition_for(runtime);items={}
    rows=load_frozen_cohort(COHORT)
    # Independent fixture IDs. Original public cohort/Gold stays byte-identical;
    # MIRACL '#' rejection is retained separately as a backend-author blocker.
    rows=[{**r,'qid':'SIMULATED-Q-'+hashlib.sha256(r['qid'].encode()).hexdigest()[:32]} for r in rows]
    cohort=root/'SIMULATED-independent-cohort.jsonl'
    cohort.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    for name,item in composition.datasets.items():
        selected=[r for r in rows if r['dataset']==name]
        calls=tuple(FreeModelPlannedCall(_answer_request(RUN,r['qid'],arm,'chat_cheap'),r['qid'],arm,
            'answer','SIMULATED-answer-batch','b'*64,'c'*64,2048,512,1,30)
            for r in selected for arm in ('CHEAP_ONLY','DYNAMIC'))
        permit=make_permit(item.scope,'chat_cheap',calls)
        grant=replace(item.xing_binding.grant,grant_id=permit.grant_id,deadline_utc=permit.deadline_utc)
        transport=XingAuthorizedTransport(grant,send_claim_callback=CurrentProviderSendClaims(),
            free_model_permit=permit,sender=SimulatedXingSend(lambda *a:pytest.fail('Assembly must not send')))
        xing=V6XingGenerationBinding(grant,counter,transport,permit)
        embedding_calls=[]
        for r in selected:
            query_sha=hashlib.sha256(r['question'].encode()).hexdigest()
            embedding_calls.append(FreeModelPlannedCall(stable_provider_request_id(run_id=RUN,qid=r['qid'],
                arm='SHARED_RETRIEVAL',purpose='query_embedding'),r['qid'],'SHARED_RETRIEVAL','query_embedding',
                'SIMULATED-query-'+r['qid'],'d'*64,'e'*64,256,0,1,10,
                input_sha256=(query_sha,),query_sha256=query_sha))
        ep=make_permit(item.scope,'embedding',embedding_calls)
        admission=EmbeddingAdmission(SimulatedEmbeddingCounter(lambda t:32),
            tokenizer_identity='SIMULATED-BGE-PROFILE',non_truncating_verified=True)
        et=SiliconFlowAuthorizedTransport(ep,send_claim_callback=CurrentProviderSendClaims(),
            sender=SimulatedXingSend(lambda *a:pytest.fail('Assembly must not send')))
        eb=SiliconFlowFreeBinding(ep,et,admission)
        guards=dict(item.model_usage_guards)
        guards['chat_cheap']=BudgetUsageGuard(InMemoryBudgetGate(0),REGISTRY.select('chat_cheap'),0,zero_price_permit=permit)
        guards['embedding']=BudgetUsageGuard(InMemoryBudgetGate(0),REGISTRY.select('embedding'),0,zero_price_permit=ep)
        ds=tuple(PairedProviderCallBudget(_answer_request(RUN,r['qid'],arm,'chat_expensive'),r['qid'],arm,
            'answer',2048,512) for r in selected for arm in ('EXPENSIVE_ONLY','DYNAMIC'))
        settings=replace(item.settings,storage_root=root/name/'SIMULATED-storage',
            database_url='sqlite:///'+(root/name/'SIMULATED-unopened.sqlite3').as_posix(),
            embedding_egress_enabled=True,cloud_cost_estimate_microunits=0)
        items[name]=replace(item,settings=settings,xing_binding=xing,deepseek_calls=ds,
            model_usage_guards=guards,embedding_provider=None,embedding_admission=admission,
            free_model_bindings={'embedding':eb},execution_context=lambda *a:nullcontext())
    return replace(composition,datasets=items),receipts,cohort


@pytest.fixture(scope='module')
def r2(counter,tmp_path_factory):
    return offline_composition(tmp_path_factory.mktemp('SIMULATED-r2-normal'),counter)


def assemble(composition,cohort):
    return build_normal_paired_runtime(composition,source_manifest_path=SOURCE,index_manifest_path=None,
        cohort_path=cohort,run_id=RUN,time_profile=time_profile('owner-small'))


def test_r2_permit_actual_normal_bootstrap_zero_io(r2):
    composition,receipts,cohort=r2
    runtime=assemble(composition,cohort)
    for name,c in runtime.containers.items():
        assert type(c) is Container and type(c.quick_chain) is LangChainQuickChain
        item=composition.datasets[name];role=c.quick_chain.generation_roles['chat_cheap']
        assert role.xing_binding is item.xing_binding
        assert role.gateway.transport.free_model_permit is item.xing_binding.request_window
        ep=c.store.embedding_provider
        assert ep.free_binding.permit is item.free_model_bindings['embedding'].permit
        assert c.knowledge_gateway.retriever.embedding_provider is ep
        assert ep.free_binding.authorized_transport is item.free_model_bindings['embedding'].authorized_transport
        assert c.memory_service is None
        with c.execution_context('SIMULATED-arm'):pass
        assert not receipts[name]['sf_wires'] and not receipts[name]['ds_wires']
        c.engine.dispose()


@pytest.mark.parametrize('mismatch',['run','source','scope','grant','deadline','expired','hard_ceiling','permit_copy','legacy_expired'])
def test_window_mismatch_or_expired_zero_bootstrap(r2,monkeypatch,mismatch):
    composition,_,cohort=r2;name=next(iter(composition.datasets));item=composition.datasets[name]
    xing=item.xing_binding;window=xing.request_window
    changes={'run':{'run_id':'SIMULATED-other'},'source':{'source_manifest_sha256':'f'*64},
        'scope':{'scope':Scope.from_ids(['SIMULATED-other'])},'grant':{'grant_id':'SIMULATED-other'},
        'deadline':{'deadline_utc':'2026-10-10T07:43:00Z'},'expired':{'deadline_utc':'2026-10-10T04:25:44Z'},
        'hard_ceiling':{'deadline_utc':'2026-10-10T07:44:03Z'}}
    if mismatch=='legacy_expired':
        window=V6XingRequestWindow(RUN,xing.grant.grant_id,item.scope,'2026-10-10T04:25:44Z',
            sha256_file(COHORT),sha256_file(SOURCE),(),'0'*64)
        window=replace(window,frozen_plan_sha256=window.fingerprint)
    else:window=freeze(replace(window,**changes.get(mismatch,{})))
    bad=replace(composition,datasets={**composition.datasets,name:replace(item,xing_binding=replace(xing,request_window=window))})
    monkeypatch.setattr('eval_center.public_runner.build_container',lambda *a,**k:pytest.fail('Denied before bootstrap'))
    with pytest.raises(PairedRuntimeAssemblyBlocked) as caught:assemble(bad,cohort)
    assert 'frozen_xing_request_window:'+name in caught.value.missing


@pytest.mark.parametrize('mismatch',['indexing_only','wrong_arm','question_hash','run','scope','deadline','request_id','missing'])
def test_query_embedding_independent_plan_fail_closed(r2,mismatch):
    composition,_,cohort=r2;item=next(iter(composition.datasets.values()));binding=item.free_model_bindings['embedding']
    p=binding.permit;c=p.calls[0]
    changes={'indexing_only':{'purpose':'index_embedding','arm':'INDEXING'},'wrong_arm':{'arm':'CHEAP_ONLY'},
        'question_hash':{'query_sha256':'f'*64},'request_id':{'request_id':'SIMULATED-other'}}
    p=freeze(replace(p,calls=(replace(c,**changes.get(mismatch,{})),)+p.calls[1:]))
    if mismatch in ('run','scope','deadline'):
        p=freeze(replace(p,**{'run':{'run_id':'SIMULATED-other'},'scope':{'scope':Scope.from_ids(['other'])},
            'deadline':{'deadline_utc':'2026-10-10T04:25:44Z'}}[mismatch]))
    et=SiliconFlowAuthorizedTransport(p,send_claim_callback=CurrentProviderSendClaims(),sender=binding.authorized_transport.sender)
    item=replace(item,free_model_bindings={} if mismatch=='missing' else {'embedding':replace(binding,permit=p,authorized_transport=et)})
    rows=load_frozen_cohort(cohort);qids={r['qid'] for r in rows if r['dataset']==next(iter(composition.datasets))}
    with pytest.raises(ValueError):
        _validate_paired_query_embedding(item,qids=qids,cohort=rows,run_id=RUN,source_sha256=sha256_file(SOURCE),
            execution_kind='SIMULATED',deadline_utc=FREE_OWNER_DEADLINE,required=True)


def ecs_identity():
    # These values are SIMULATED identities, not observations of ECS.
    scope=Scope.from_ids(['SIMULATED-kb'],['SIMULATED-doc'])
    profile=ECSReadOnlyDatabaseProfile(15432,'1234567890123456789',17001,'0022_memory_vector_index',
        '7'*64,'SIMULATED-ecs-volume','8'*64)
    expected=legacy_identity();expected.update(port=profile.port,database=profile.database,
        container_name=profile.container_name,container_id=profile.container_id,volume_name=profile.volume_name,
        system_identifier=profile.system_identifier,database_oid=profile.database_oid,schema_revision=profile.schema_revision,
        scope={'kb':sorted(scope.knowledge_base_ids),'documents':sorted(scope.document_ids)})
    return profile,scope,expected


def test_ecs_explicit_identity_and_legacy_branches():
    p,s,e=ecs_identity()
    proof=validate_index_identity(e,deepcopy(e),database_profile=p,scope=s)
    assert set(('system_identifier','database_oid','scope'))<=set(proof['verified_fields'])
    assert validate_index_identity(legacy_identity(),legacy_identity())['status']=='VERIFIED_READ_ONLY_REUSE'
    with pytest.raises(IndexReuseRejected,match='port'):validate_index_identity(e,deepcopy(e))
    url=make_url(read_only_database_url('postgresql+psycopg://SIMULATED:x@127.0.0.1:15432/postgres?port=5432&dbname=other',
        p.database,database_profile=p))
    assert url.port==p.port and url.database==p.database and set(url.query)=={'options','application_name'}


@pytest.mark.parametrize('field',['port','database','system_identifier','database_oid','schema_revision',
    'container_id','container_name','volume_name','scope','embedding_digest','index_fingerprint','database_read_only'])
def test_ecs_identity_mismatch_or_missing_rejected(field):
    p,s,e=ecs_identity();actual=deepcopy(e);actual.pop(field)
    with pytest.raises(IndexReuseRejected):validate_index_identity(e,actual,database_profile=p,scope=s)
    actual=deepcopy(e);actual[field]='SIMULATED-wrong'
    with pytest.raises(IndexReuseRejected):validate_index_identity(e,actual,database_profile=p,scope=s)
    if field in ('port','database','system_identifier','database_oid','schema_revision','container_id','container_name','volume_name'):
        e[field]=actual[field]
        with pytest.raises(IndexReuseRejected):validate_index_identity(e,actual,database_profile=p,scope=s)


@pytest.mark.parametrize('field,value',[('port',None),('port',True),('system_identifier','UNKNOWN'),
    ('database_oid',None),('schema_revision',''),('evidence_sha256','UNKNOWN'),('database','rag')])
def test_ecs_profile_unknown_or_wrong_target_rejected(field,value):
    p,_,_=ecs_identity()
    with pytest.raises(IndexReuseRejected):replace(p,**{field:value}).validate()


def test_ecs_readonly_connection_requires_observed_system_oid_migration():
    p,_,_=ecs_identity()
    class Result:
        def __init__(self,row):self.row=row
        def one(self):return self.row
    class Connection:
        def __init__(self,identity):self.identity=identity;self.calls=0
        def execute(self,sql):
            self.calls+=1
            return Result((p.database,'on') if self.calls==1 else self.identity)
    observed=(p.system_identifier,p.database_oid,p.schema_revision)
    c=Connection(observed);require_read_only_connection(c,p.database,database_profile=p);assert c.calls==2
    for i in range(3):
        wrong=list(observed);wrong[i]='SIMULATED-other'
        with pytest.raises(IndexReuseRejected):require_read_only_connection(Connection(tuple(wrong)),p.database,database_profile=p)


def test_reviewed_cli_entry_actual_loader_to_normal_bootstrap(r2,monkeypatch,tmp_path):
    from eval_center.public_benchmark_cli import _parser,_load_runtime_composition
    composition,_,cohort=r2
    # Independent offline entry fixture. This is not the as-yet unprovided ECS entry.
    import eval_center.tests.test_public_r2_ecs_assembly as module
    monkeypatch.setattr(module,'_OFFLINE_COMPOSITION',composition,raising=False)
    entry=tmp_path/'SIMULATED-compose.py'
    entry.write_text('from eval_center.tests.test_public_r2_ecs_assembly import _OFFLINE_COMPOSITION\n'
        'def compose_public_paired_runtime(*, state_path, index_manifest_path):\n'
        '    assert state_path is not None and index_manifest_path is None\n'
        '    return _OFFLINE_COMPOSITION\n',encoding='utf-8')
    args=_parser().parse_args(['--phase','paired-run','--run-id',RUN,'--paired-time-profile','owner-small',
        '--runtime-entry',str(entry),'--runtime-entry-sha256',sha256_file(entry)])
    loaded=_load_runtime_composition(args,tmp_path/'SIMULATED-center.sqlite3')
    runtime=assemble(loaded,cohort)
    assert loaded.entry_sha256==sha256_file(entry)
    assert all(type(c) is Container for c in runtime.containers.values())
    for c in runtime.containers.values():c.engine.dispose()


def test_legacy_window_keeps_original_deadline_and_historical_branch(r2,monkeypatch):
    from backend.app.adapters.models.cloud import V6XingPlannedCall
    composition,_,cohort=r2;items={}
    for name,item in composition.datasets.items():
        old=item.xing_binding
        grant=replace(old.grant,deadline_utc='2026-10-10T04:25:44Z')
        calls=tuple(V6XingPlannedCall(c.request_id,c.qid,c.arm,c.purpose,c.input_token_bound,c.output_token_bound)
            for c in old.request_window.calls)
        window=V6XingRequestWindow(RUN,grant.grant_id,item.scope,grant.deadline_utc,
            sha256_file(cohort),sha256_file(SOURCE),calls,'0'*64)
        window=replace(window,frozen_plan_sha256=window.fingerprint)
        transport=XingAuthorizedTransport(grant,send_claim_callback=CurrentProviderSendClaims(),
            sender=old.authorized_transport.sender)
        guard=BudgetUsageGuard(InMemoryBudgetGate(100),REGISTRY.select('chat_cheap'),3)
        items[name]=replace(item,xing_binding=V6XingGenerationBinding(grant,old.capacity_counter,transport,window),
            free_model_bindings={},embedding_provider=None,embedding_admission=None,
            model_usage_guards={'chat_cheap':guard,'chat_expensive':item.model_usage_guards['chat_expensive']})
    old_composition=replace(composition,datasets=items)
    class HistoricalClock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,9,tzinfo=timezone.utc)
    monkeypatch.setattr('eval_center.public_runner.datetime',HistoricalClock)
    runtime=build_normal_paired_runtime(old_composition,source_manifest_path=SOURCE,index_manifest_path=None,
        cohort_path=cohort,run_id=RUN,time_profile=time_profile('legacy-dev24'))
    assert all(c.quick_chain.generation_roles['chat_cheap'].xing_binding.request_window.deadline_utc
        =='2026-10-10T04:25:44Z' for c in runtime.containers.values())
    for c in runtime.containers.values():c.engine.dispose()
    class ExpiredClock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,10,5,tzinfo=timezone.utc)
    monkeypatch.setattr('eval_center.public_runner.datetime',ExpiredClock)
    with pytest.raises(PairedRuntimeAssemblyBlocked,match='paired_runtime_assembly_blocked') as caught:
        build_normal_paired_runtime(old_composition,source_manifest_path=SOURCE,index_manifest_path=None,
            cohort_path=cohort,run_id=RUN,time_profile=time_profile('legacy-dev24'))
    assert caught.value.missing==['absolute_deadline_expired']


def test_r2_native_final_send_rechecks_deadline_after_claim(monkeypatch,tmp_path):
    from backend.tests.test_v6_free_native_binding import native_setup,invoke
    from backend.app.ports.providers import ProviderRequestNotSent
    from eval_center.paired_state import provider_send_claim_record
    # This test imports a helper; that helper's module-local autouse fixture
    # does not run here. Supply our own independent E-drive SIMULATED CAS root.
    assert tmp_path.drive.upper() == 'E:'
    monkeypatch.setenv('V6_SIMULATED_RESULT_ROOT',str(tmp_path/'SIMULATED-results'))
    # Imported native fixture keeps its original R2 grant. Freeze this test's
    # Evaluation Center clock at the same lawful historical instant; final
    # transport cutoff below still exercises expiry after the durable claim.
    class HistoricalClaimClock(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,10,3,tzinfo=timezone.utc)
    monkeypatch.setattr('eval_center.paired_state.datetime',HistoricalClaimClock)
    case=native_setup(monkeypatch,tmp_path)
    adapter,permit,call,context,callback,path,gate,wires,payload=case
    original=callback.check_before_send
    def expire_after_durable_check(*,request_id):
        original(request_id=request_id)
        adapter.transport.clock=lambda:datetime.fromisoformat(permit.deadline_utc.replace('Z','+00:00'))
    monkeypatch.setattr(type(callback),'check_before_send',lambda self,**kwargs:expire_after_durable_check(**kwargs))
    with pytest.raises(ProviderRequestNotSent):invoke(case)
    assert not wires
    assert provider_send_claim_record(path,request_id=call.request_id) is not None
    with pytest.raises(ProviderRequestNotSent):invoke(case)
    assert not wires
