"""SIMULATED counters/evidence/provider; real normal bootstrap and local SQLite/CAS.

No production DB/API/ledger. Historical clocks are fixtures, never runtime policy.
"""
import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import Engine

from backend.app.adapters.models.cloud import XingChatGrant, XingAuthorizedTransport, SimulatedXingSend
from backend.app.application.rule_router_v1 import V6GenerationBinding, V6XingGenerationBinding
from backend.app.application.validation_usd_budget import OWNER_V6_TOKEN_GRANT_R3, V6OwnerActivityGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.config import Settings
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.run_lifecycle import current_execution
from backend.app.ports.model_access import model_access
from backend.app.ports.xing_zero_reservation import FreeModelPlannedCall, SiliconFlowFreePermit, SiliconFlowZeroPriceContract
from eval_center.paired_preparation import prepare_normal_paired_runtime, PairedRetrievalPreparation
from eval_center.public_runner import PairedDatasetComposition, PairedRuntimeComposition, PairedProviderCallBudget, _paired_runtime_admission
from eval_center.paired_state import CurrentProviderSendClaims, register_paired_run_link
from eval_center.paired_simulation import build_simulated_container
from eval_center.public_paired_reporting import time_profile
from eval_center.tests.test_paired_setup_retrieval_contract import query_setup, register_pair, FIXED


class Clock(datetime):
    value = FIXED
    @classmethod
    def now(cls, tz=None): return cls.value


@pytest.fixture
def setup(monkeypatch, tmp_path):
    Clock.value = FIXED
    for module in ('eval_center.public_runner', 'eval_center.paired_preparation', 'eval_center.paired_state'):
        monkeypatch.setattr(module + '.datetime', Clock)
    monkeypatch.setattr(Engine, 'connect', lambda *a, **k: pytest.fail('LIVE DB forbidden'))
    monkeypatch.setattr('backend.app.adapters.models.cloud._send', lambda *a, **k: pytest.fail('LIVE SF forbidden'))
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener', lambda *a, **k: pytest.fail('LIVE DS forbidden'))
    row, contract, binding, free, adapter, budget, sent = query_setup(monkeypatch, tmp_path)
    source, cohort, index = (tmp_path / n for n in ('source.json', 'cohort.jsonl', 'index.json'))
    manifest = json.loads(source.read_text())
    manifest['datasets'][0].update(corpus={'sha256': 'a'*64}, development_cases={'sha256': 'b'*64},
        dataset_version=row['dataset_version'])
    source.write_text(json.dumps(manifest), encoding='utf8')
    from eval_center.public_runner import _canonical_hash
    selection = replace(contract.selections[0], index_identity_sha256=_canonical_hash(
        dict(execution_kind='SIMULATED', corpus_sha256='a'*64, index='NO_REAL_INDEX')))
    contract = replace(contract, source_manifest_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        selections=(selection,))
    free_permit = replace(free.permit, source_manifest_sha256=contract.source_manifest_sha256)
    free_permit = replace(free_permit, frozen_plan_sha256=free_permit.fingerprint)
    # Query plans also include the permit dispatch identity, so rebuild the
    # fixture with the final source pin before doing any native claim.
    from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport
    from backend.app.ports.xing_zero_reservation import free_dispatch_identity_sha256
    from backend.app.adapters.models.factory import ProviderFactory
    transport = SiliconFlowAuthorizedTransport(free_permit, send_claim_callback=CurrentProviderSendClaims(),
        sender=adapter.transport.sender, clock=lambda: FIXED)
    free = replace(free, permit=free_permit, authorized_transport=transport)
    old_plan = binding.plans[0][3] if hasattr(binding, 'plans') else binding.query_plans[0][3]
    plan = replace(old_plan, calls=(replace(old_plan.calls[0],
        request_identity_sha256=free_dispatch_identity_sha256(free_permit, free_permit.calls[0])),))
    binding = type(binding)(contract, (('miracl-zh', row['qid'], free_permit.calls[0].batch_id, plan),),
                            execution_kind='SIMULATED')
    guard = BudgetUsageGuard(budget, adapter.spec, 0, zero_price_permit=free_permit)
    adapter = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('embedding',),
        free_model_bindings={'embedding': free}, usage_guards={'embedding': guard}).build('embedding')
    sim = build_simulated_container(ledger_dir=tmp_path/'SIMULATED-legacy')
    ledger = tmp_path/'SIMULATED-r3-ledger.json'
    old = json.loads(sim.simulated_gate.path.read_text())
    ledger.write_text(json.dumps({k:v for k,v in old.items() if k != 'v6_owner_activity'}))
    grant = replace(OWNER_V6_TOKEN_GRANT_R3, execution_kind='SIMULATED')
    gate = V6OwnerActivityGate(ledger, grant=grant, clock=lambda: FIXED, execution_lease=contract.execution_lease)
    gate.initialize_grant()
    scope = contract.selections[0].scope
    v6 = V6GenerationBinding(grant, gate, sim.simulated_counter, scope, CurrentProviderSendClaims())
    from backend.app.adapters.models.siliconflow_xing_capacity import SiliconFlowXingCapacityCounter
    import os
    counter = SiliconFlowXingCapacityCounter.from_directory(os.environ['XING_TEST_ASSETS'])
    xing_grant = XingChatGrant(OWNER_V6_TOKEN_GRANT_R3.grant_id+':miracl-zh:chat_cheap',
        'SIMULATED-scope', scope, None, ('answer',), 'SIMULATED')
    xing = V6XingGenerationBinding(xing_grant, counter, None, None)
    @contextmanager
    def execution(c, rid, scope):
        token = current_execution.set((SimpleNamespace(run_id=rid), None, Event(), 'SIMULATED-session'))
        try: yield
        finally: current_execution.reset(token)
    settings = Settings(cloud_enabled=True, chat_egress_enabled=True, rule_router_enabled=True,
        embedding_egress_enabled=True, cloud_cost_estimate_microunits=0, local_answer_enabled=False,
        local_query_enabled=False, rule_router_quality_escalation=False, rule_router_abstention_escalation=False,
        rule_router_provider_fallback=False,
        database_url='sqlite:///'+(tmp_path/'SIMULATED-unopened.sqlite3').as_posix(), storage_root=tmp_path/'storage')
    calls = tuple(PairedProviderCallBudget(__import__('backend.app.ports.session_attempts', fromlist=['request_identity']).request_identity(
        str(__import__('uuid').uuid5(__import__('uuid').NAMESPACE_URL, f'{contract.cohort_run_id}:{row["qid"]}:{arm}')),
        'quick.router.chat_expensive', 1), row['qid'], arm, 'answer', 2048, 512)
        for arm in ('EXPENSIVE_ONLY', 'DYNAMIC'))
    item = PairedDatasetComposition(settings, scope, v6, xing, execution,
        {'embedding': guard, 'chat_expensive': sim.quick_chain.generation_roles['chat_expensive'].usage_guard},
        None, free.admission, calls, {'embedding': free})
    composition = PairedRuntimeComposition({'miracl-zh': item}, {kb:True for kb in scope.knowledge_base_ids},
        lambda: None, 'SIMULATED', execution_contract=contract, shared_retrieval_binding=binding)
    paths = dict(source_manifest_path=source, index_manifest_path=index, cohort_path=cohort,
        run_id=contract.cohort_run_id, time_profile=time_profile('owner-lease-small', native_execution_lease=contract.execution_lease),
        execution_contract=contract)
    captures = []
    def bind(wires):
        captures.append(wires)
        cheap = [w for w in wires if w.role == 'chat_cheap']
        calls = tuple(FreeModelPlannedCall(w.request_id, w.qid, w.arm, 'answer', 'SIMULATED-answer',
            w.input_profile_sha256, w.wire_sha256, w.input_token_bound, 512, 1, 30) for w in cheap)
        permit = SiliconFlowFreePermit(xing_grant.grant_id, contract.cohort_run_id, scope, None,
            contract.source_manifest_sha256, free_permit.authority_manifest_sha256,
            ModelRegistry.frozen_defaults().select('chat_cheap').model_id, 'chat_cheap',
            SiliconFlowZeroPriceContract(ModelRegistry.frozen_defaults().select('chat_cheap').model_id), calls,
            sum(c.input_token_bound+c.output_token_bound for c in calls), '0'*64, 'SIMULATED',
            owner_activity_id=OWNER_V6_TOKEN_GRANT_R3.grant_id, execution_lease=contract.execution_lease)
        permit = replace(permit, frozen_plan_sha256=permit.fingerprint)
        tr = XingAuthorizedTransport(xing_grant, free_model_permit=permit, send_claim_callback=CurrentProviderSendClaims(),
            sender=SimulatedXingSend(lambda *a: pytest.fail('chat send forbidden')), clock=lambda: FIXED)
        guards = {**item.model_usage_guards, 'chat_cheap': BudgetUsageGuard(InMemoryBudgetGate(0),
            ModelRegistry.frozen_defaults().select('chat_cheap'), 0, zero_price_permit=permit)}
        return replace(composition, datasets={'miracl-zh': replace(item,
            xing_binding=replace(xing, authorized_transport=tr, request_window=permit), model_usage_guards=guards)})
    # Normal bootstrap with explicit offline DeepSeek transport substitution;
    # keep Factory/role/permit/admission checks intact, and never open a DB.
    from eval_center import public_runner
    from backend.app.adapters.models.deepseek import SimulatedDeepSeekTransport
    from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
    real_build = public_runner.build_container
    def offline_build(settings, **kwargs):
        container = real_build(settings, **kwargs)
        container.quick_chain.context_manager = None
        if kwargs.get('v6_generation_binding') is not None:
            role = container.quick_chain.generation_roles['chat_expensive']
            gateway = container.provider_factory.build_v6_gateway(activity_gate=gate,
                gate_types=DEEPSEEK_GATE_TYPES, api_key='SIMULATED-NOT-A-KEY',
                transport=SimulatedDeepSeekTransport(lambda *a: pytest.fail('chat forbidden')),
                send_claim_callback=v6.send_claim_callback)
            container.quick_chain.generation_roles['chat_expensive'] = replace(role, gateway=gateway)
        return container
    monkeypatch.setattr(public_runner, 'build_container', offline_build)
    prep = prepare_normal_paired_runtime(composition, bind_answers=bind, **paths)
    chain = prep.runtime.containers['miracl-zh'].quick_chain
    gateway = sim.quick_chain.knowledge_gateway
    original = gateway.retrieve_question
    def retrieve(scope, question, **kwargs):
        with model_access('embedding', allowed=True, scope=scope): adapter.embed([question], 10)
        plan, result = original(scope, question, **kwargs)
        for entry in result.items:
            entry.chunk.knowledge_base_id = next(iter(scope.knowledge_base_ids))
            entry.chunk.document_id = next(iter(scope.document_ids))
        return plan, result
    gateway.retrieve_question = retrieve
    original_bundle = gateway.evidence.bundle
    def bundle(plan, result):
        value = original_bundle(plan, result)
        for entry in value.selected:
            entry.chunk.knowledge_base_id = next(iter(scope.knowledge_base_ids))
            entry.chunk.document_id = next(iter(scope.document_ids))
        return value
    gateway.evidence.bundle = bundle
    chain.knowledge_gateway = gateway
    chain.context_manager = None  # SIMULATED fixture has no history/DB reads
    state = tmp_path/'SIMULATED-state.sqlite3'
    register_pair(state, contract)
    register_paired_run_link(state, contract, retrieval_binding_sha256=binding.fingerprint)
    return prep, state, row, manifest, paths, sent, captures


def bind_case(case):
    prep, state, row, manifest, paths, _, _ = case
    return prep.bind_after_retrieval(state_path=state, cohort=[row], manifest=manifest,
        index_manifest_path=paths['index_manifest_path'], top_k=5)


def test_stage_one_has_no_chat_capability(setup):
    prep = setup[0]
    c = prep.runtime.containers['miracl-zh']
    assert not c.quick_chain.generation_roles
    assert c.quick_chain.answer_gateway is c.quick_chain.cloud_answer_gateway is None
    assert not {'chat_cheap','chat_expensive','vision'} & c.provider_factory.enabled_roles
    assert _paired_runtime_admission(prep, manifest=setup[3], index_manifest_path=setup[4]['index_manifest_path'])[0]
    assert setup[5] == []


def test_actual_context_normal_renderer_and_full_admission(setup):
    runtime = bind_case(setup)
    prep, _, row, _, _, sent, captures = setup
    assert len(sent) == len(captures) == 1
    assert len(prep.wires) == 4 and len({w.envelope.identity for w in prep.wires}) == 1
    assert all(w.envelope.q0 == row['question'] for w in prep.wires)
    assert 'SIMULATED public evaluation evidence' in prep.wires[0].envelope.prompt
    cheap = runtime.containers['miracl-zh'].quick_chain.generation_roles['chat_cheap'].xing_binding
    for w in prep.wires:
        if w.role != 'chat_cheap': continue
        policy = SimpleNamespace(transport=cheap.authorized_transport, request_id=w.request_id,
            purpose='answer', scope=cheap.grant.scope, input_profile_sha256=w.input_profile_sha256,
            wire_bytes=w.wire_bytes, item_count=1, input_tokens=w.input_token_bound, output_tokens=512)
        assert cheap.request_window.planned_call(ModelRegistry.frozen_defaults().select('chat_cheap'), policy, 30)
        policy.wire_bytes += b' '
        with pytest.raises(ValueError, match='SF_FREE_REQUEST_NOT_PLANNED'):
            cheap.request_window.planned_call(ModelRegistry.frozen_defaults().select('chat_cheap'), policy, 30)
    with pytest.raises(Exception, match='already_bound_no_retry'): bind_case(setup)
    assert len(sent) == len(captures) == 1


@pytest.mark.parametrize('change', ['index','lease','cancel'])
def test_preparation_denies_drift_before_any_retrieval(setup, change):
    prep = setup[0]
    if change == 'index': setup[4]['index_manifest_path'].write_text('tampered')
    elif change == 'lease': Clock.value = prep.runtime.execution_contract.execution_lease.deadline
    else: prep._is_cancelled = lambda: True
    with pytest.raises(Exception, match='changed|expired|cancelled'): bind_case(setup)
    assert setup[5] == setup[6] == []


def test_reconstructed_preparation_does_not_repeat_retrieval(setup):
    bind_case(setup)
    old = setup[0]
    restored = PairedRetrievalPreparation(old.composition, old.runtime, old.paths, old._bind_answers, lambda: False)
    with pytest.raises(Exception, match='existing_receipt_no_retry'): bind_case((restored, *setup[1:]))
    assert len(setup[5]) == 1


@pytest.mark.parametrize('change', ['context_hash', 'prompt'])
def test_final_boundary_denies_context_or_payload_drift(setup, change):
    from eval_center.paired_preparation import assert_prepared_answer_envelope
    runtime = bind_case(setup)
    wire = setup[0].wires[0]
    expected = runtime.containers['miracl-zh'].prepared_answer_envelopes
    assert_prepared_answer_envelope(expected, wire.qid, wire.arm, wire.envelope)
    altered = replace(wire.envelope, **{change: 'tampered'})
    with pytest.raises(Exception, match='context_or_payload_changed'):
        assert_prepared_answer_envelope(expected, wire.qid, wire.arm, altered)
    assert len(setup[5]) == 1


def test_zero_hash_cannot_be_bound_after_context(setup):
    prep = setup[0]
    original = prep._bind_answers
    def bind_bad(wires):
        final = original(wires)
        item = final.datasets['miracl-zh']
        old = item.xing_binding
        calls = tuple(replace(c, wire_sha256='0'*64) for c in old.request_window.calls)
        permit = replace(old.request_window, calls=calls)
        permit = replace(permit, frozen_plan_sha256=permit.fingerprint)
        tr = XingAuthorizedTransport(old.grant, free_model_permit=permit,
            send_claim_callback=CurrentProviderSendClaims(), clock=lambda: FIXED,
            sender=SimulatedXingSend(lambda *a: pytest.fail('chat forbidden')))
        guards = {**item.model_usage_guards, 'chat_cheap': BudgetUsageGuard(InMemoryBudgetGate(0),
            ModelRegistry.frozen_defaults().select('chat_cheap'), 0, zero_price_permit=permit)}
        return replace(final, datasets={'miracl-zh': replace(item,
            xing_binding=replace(old, authorized_transport=tr, request_window=permit), model_usage_guards=guards)})
    prep._bind_answers = bind_bad
    with pytest.raises(Exception, match='exact_answer_wire_required'): bind_case(setup)
    with pytest.raises(Exception, match='already_bound_no_retry'): bind_case(setup)
    assert len(setup[5]) == len(setup[6]) == 1



def shared_rerank_case(setup, monkeypatch, tmp_path):
    from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport, RerankAdmission, SimulatedRerankCounter
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.adapters.storage import ContentAddressedStorage
    from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding, free_dispatch_contract, free_dispatch_identity_sha256
    from eval_center.paired_state import ProviderBatchPlan, ProviderBatchCall, stable_provider_request_id
    from eval_center.public_runner import PairedSharedRetrievalBinding, _validate_paired_rerank
    prep, _, row, _, _, _, _ = setup
    item = prep.composition.datasets['miracl-zh']
    contract = prep.runtime.execution_contract
    admission = RerankAdmission(SimulatedRerankCounter(lambda q,d:7),
        tokenizer_identity='SIMULATED-RERANK', execution_kind='SIMULATED')
    model = ModelRegistry.frozen_defaults().select('rerank').model_id
    payload = dict(model=model, query=row['question'], documents=['a','b'], top_n=2, return_documents=True)
    call = FreeModelPlannedCall(stable_provider_request_id(run_id=contract.retrieval_run_id, qid=row['qid'],
        arm='SHARED_RETRIEVAL', purpose='rerank'), row['qid'], 'SHARED_RETRIEVAL', 'rerank',
        'SIMULATED-rerank-batch', admission.input_profile_sha256,
        hashlib.sha256(json.dumps(payload).encode()).hexdigest(), 14, 0, 2, 30,
        document_version_ids=('va','vb'), chunk_ids=('a','b'), candidate_order=('a','b'),
        input_sha256=tuple(hashlib.sha256(t.encode()).hexdigest() for t in ['a','b']),
        query_sha256=hashlib.sha256(row['question'].encode()).hexdigest())
    ep = item.free_model_bindings['embedding'].permit
    permit = SiliconFlowFreePermit(OWNER_V6_TOKEN_GRANT_R3.grant_id+':miracl-zh:rerank',
        contract.retrieval_run_id, item.scope, None, ep.source_manifest_sha256, ep.authority_manifest_sha256,
        model, 'rerank', SiliconFlowZeroPriceContract(model), (call,), 14, '0'*64, 'SIMULATED',
        owner_activity_id=ep.owner_activity_id, execution_lease=contract.execution_lease)
    permit = replace(permit, frozen_plan_sha256=permit.fingerprint)
    sent = []
    def sender(request, timeout):
        sent.append(request.data)
        return dict(model=model, results=[dict(index=i,relevance_score=1.0,document={'text':['a','b'][i]})
            for i in [1,0]], usage=dict(prompt_tokens=14,completion_tokens=0,total_tokens=14))
    transport = SiliconFlowAuthorizedTransport(permit, send_claim_callback=CurrentProviderSendClaims(),
        sender=SimulatedXingSend(sender), clock=lambda: Clock.value)
    free = SiliconFlowFreeBinding(permit, transport, admission, ContentAddressedStorage(tmp_path/'SIMULATED-rerank-results'))
    native = ProviderBatchCall(1, call.document_version_ids, call.chunk_ids, call.input_sha256,
        call.wire_sha256, free_dispatch_identity_sha256(permit,call), call.candidate_order)
    plan = ProviderBatchPlan('rerank','siliconflow',model,'a'*64,'b'*64,
        free_dispatch_contract(permit,call)['scope_sha256'],(native,),call.query_sha256)
    binding = PairedSharedRetrievalBinding(contract, prep.runtime.shared_retrieval_binding.plans,
        (('miracl-zh',row['qid'],call.batch_id,plan),), execution_kind='SIMULATED')
    guard = BudgetUsageGuard(InMemoryBudgetGate(0),ModelRegistry.frozen_defaults().select('rerank'),0,zero_price_permit=permit)
    item = replace(item, settings=replace(item.settings,rerank_enabled=True,rerank_egress_enabled=True),
        free_model_bindings={**item.free_model_bindings,'rerank':free}, model_usage_guards={**item.model_usage_guards,'rerank':guard})
    _validate_paired_rerank(item,qids={row['qid']},cohort=[row],run_id=contract.cohort_run_id,
        source_sha256=contract.source_manifest_sha256,execution_kind='SIMULATED',execution_lease=contract.execution_lease,
        retrieval_binding=binding,dataset='miracl-zh')
    composition = replace(prep.composition,datasets={'miracl-zh':item},shared_retrieval_binding=binding)
    staged = prepare_normal_paired_runtime(composition, bind_answers=lambda _: pytest.fail('no chat'), **prep.paths)
    ranker = staged.runtime.containers['miracl-zh'].quick_chain.knowledge_gateway.retriever.ranker
    embedding = ProviderFactory(ModelRegistry.frozen_defaults(),enabled_roles=('embedding',),
        free_model_bindings={'embedding':item.free_model_bindings['embedding']},
        usage_guards={'embedding':item.model_usage_guards['embedding']}).build('embedding')
    state = tmp_path/'SIMULATED-query-rerank-state.sqlite3'
    register_pair(state,contract)
    register_paired_run_link(state,contract,retrieval_binding_sha256=binding.fingerprint)
    return staged,binding,ranker,embedding,state,row,sent,[SimpleNamespace(chunk_id='a'),SimpleNamespace(chunk_id='b')],{
        'a':SimpleNamespace(content='a',version_id='va'),'b':SimpleNamespace(content='b',version_id='vb')}


def test_shared_normal_rerank_separate_exact_native_receipts(setup,monkeypatch,tmp_path):
    from eval_center.public_runner import _shared_retrieval_context
    from eval_center.paired_state import attempts_for_qid
    staged,binding,ranker,embedding,state,row,sent,hits,chunks=shared_rerank_case(setup,monkeypatch,tmp_path)
    scope=staged.runtime.scopes['miracl-zh']
    container=staged.runtime.containers['miracl-zh']
    with _shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',qid=row['qid'],scope=scope,
            question=row['question'],execution_context=container.execution_context):
        with model_access('embedding',allowed=True,scope=scope): embedding.embed([row['question']],10)
        with model_access('rerank',allowed=True,scope=scope): assert ranker.rank(row['question'],hits,chunks)==(hits[1],hits[0])
    children=attempts_for_qid(state,run_id=binding.execution_contract.retrieval_run_id,qid=row['qid'])['SHARED_RETRIEVAL']['provider_calls']
    assert len(children)==2 and {c['purpose'] for c in children}=={'query_embedding','rerank'}
    assert all(c['status']=='simulated' for c in children) and len(sent)==1
    with pytest.raises(Exception,match='no_retry'):
        with _shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',qid=row['qid'],scope=scope,
            question=row['question'],execution_context=container.execution_context): pass
    assert len(sent)==1


@pytest.mark.parametrize('tamper',['order','content','version','query','lease','cancel'])
def test_shared_rerank_input_or_lease_tamper_zero_rank_send(setup,monkeypatch,tmp_path,tamper):
    from eval_center.public_runner import _shared_retrieval_context
    staged,binding,ranker,embedding,state,row,sent,hits,chunks=shared_rerank_case(setup,monkeypatch,tmp_path)
    scope=staged.runtime.scopes['miracl-zh'];container=staged.runtime.containers['miracl-zh']
    with pytest.raises(Exception):
        with _shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',qid=row['qid'],scope=scope,
                question=row['question'],execution_context=container.execution_context):
            with model_access('embedding',allowed=True,scope=scope): embedding.embed([row['question']],10)
            question=row['question']
            if tamper=='order': hits.reverse()
            elif tamper=='content': chunks['a'].content='tampered'
            elif tamper=='version': chunks['a'].version_id='tampered'
            elif tamper=='query': question='tampered'
            elif tamper=='lease': Clock.value=binding.execution_contract.execution_lease.deadline
            else: current_execution.get()[2].set()
            with model_access('rerank',allowed=True,scope=scope): ranker.rank(question,hits,chunks)
    assert sent==[]


def test_enabling_rerank_without_its_native_plan_remains_blocked(setup):
    from eval_center.public_runner import _validate_paired_rerank
    prep=setup[0];item=prep.composition.datasets['miracl-zh'];contract=prep.runtime.execution_contract
    item=replace(item,settings=replace(item.settings,rerank_enabled=True))
    with pytest.raises(ValueError,match='requires_separate_plan'):
        _validate_paired_rerank(item,qids={setup[2]['qid']},cohort=[setup[2]],run_id=contract.cohort_run_id,
            source_sha256=contract.source_manifest_sha256,execution_kind='SIMULATED',
            execution_lease=contract.execution_lease,retrieval_binding=prep.runtime.shared_retrieval_binding,dataset='miracl-zh')
    assert setup[5]==[]



def test_declared_rerank_cannot_silently_fall_back_to_embedding_only(setup,monkeypatch,tmp_path):
    from eval_center.public_runner import _shared_retrieval_context
    staged,binding,ranker,embedding,state,row,sent,hits,chunks=shared_rerank_case(setup,monkeypatch,tmp_path)
    scope=staged.runtime.scopes['miracl-zh'];container=staged.runtime.containers['miracl-zh']
    with pytest.raises(Exception,match='retrieval_receipt_required'):
        with _shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',qid=row['qid'],scope=scope,
                question=row['question'],execution_context=container.execution_context):
            with model_access('embedding',allowed=True,scope=scope): embedding.embed([row['question']],10)
    assert sent==[]


def test_rerank_unknown_is_consumed_and_prevents_retrieval_replay(setup,monkeypatch,tmp_path):
    from eval_center.public_runner import _shared_retrieval_context
    from eval_center.paired_state import attempts_for_qid
    staged,binding,ranker,embedding,state,row,sent,hits,chunks=shared_rerank_case(setup,monkeypatch,tmp_path)
    count=[]
    def timeout(*args): count.append(1);raise TimeoutError('SIMULATED unknown rerank')
    ranker.original.transport.sender=SimulatedXingSend(timeout)
    scope=staged.runtime.scopes['miracl-zh'];container=staged.runtime.containers['miracl-zh']
    with pytest.raises(Exception):
        with _shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',qid=row['qid'],scope=scope,
                question=row['question'],execution_context=container.execution_context):
            with model_access('embedding',allowed=True,scope=scope): embedding.embed([row['question']],10)
            with model_access('rerank',allowed=True,scope=scope): ranker.rank(row['question'],hits,chunks)
    children=attempts_for_qid(state,run_id=binding.execution_contract.retrieval_run_id,qid=row['qid'])['SHARED_RETRIEVAL']['provider_calls']
    assert next(c for c in children if c['purpose']=='rerank')['status']=='unknown'
    assert all(r['state']=='unknown' for r in ranker.original.usage_guard.budget_gate.reservations.values())
    with pytest.raises(Exception,match='no_retry'):
        with _shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',qid=row['qid'],scope=scope,
                question=row['question'],execution_context=container.execution_context): pass
    assert count==[1] and sent==[]


def late_rerank_case(setup, monkeypatch, tmp_path, mutate=None):
    from eval_center.paired_preparation import LateRerankPreparation
    from eval_center.public_runner import PairedSharedRetrievalBinding
    from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding
    from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport
    staged, _, ranker, embedding, _, row, _, hits, chunks = shared_rerank_case(setup, monkeypatch, tmp_path)
    item = staged.composition.datasets['miracl-zh']
    admission = ranker.original.free_binding.admission
    result_storage = ranker.original.free_binding.result_storage
    ep = item.free_model_bindings['embedding'].permit
    sent, sealed = [], []
    def bind(prepared):
        # Candidates were unknown before this callback; use only frozen actual input.
        assert sent == [] and len(setup[5]) == 1
        sealed.append(prepared)
        call = FreeModelPlannedCall(prepared.request_id, prepared.qid, 'SHARED_RETRIEVAL', 'rerank',
            'SIMULATED-late-batch', prepared.input_profile_sha256, prepared.wire_sha256,
            prepared.input_token_bound, 0, len(prepared.chunk_ids), 30,
            document_version_ids=prepared.document_version_ids, chunk_ids=prepared.chunk_ids,
            candidate_order=prepared.chunk_ids,
            input_sha256=tuple(hashlib.sha256(t.encode()).hexdigest() for t in prepared.documents),
            query_sha256=hashlib.sha256(prepared.question.encode()).hexdigest())
        if mutate: call = mutate(call, prepared)
        permit = SiliconFlowFreePermit(ep.owner_activity_id+':late-rerank', ep.run_id, ep.scope,
            ep.deadline_utc, ep.source_manifest_sha256, ep.authority_manifest_sha256,
            ModelRegistry.frozen_defaults().select('rerank').model_id, 'rerank',
            SiliconFlowZeroPriceContract(ModelRegistry.frozen_defaults().select('rerank').model_id),
            (call,), call.input_token_bound, '0'*64, 'SIMULATED',
            owner_activity_id=ep.owner_activity_id, execution_lease=ep.execution_lease)
        permit = replace(permit, frozen_plan_sha256=permit.fingerprint)
        def sender(request, timeout):
            sent.append(request.data)
            return dict(model=permit.model, results=[dict(index=i,relevance_score=1.0,
                document={'text':prepared.documents[i]}) for i in reversed(range(len(prepared.documents)))],
                usage=dict(prompt_tokens=prepared.input_token_bound,completion_tokens=0,
                           total_tokens=prepared.input_token_bound))
        transport = SiliconFlowAuthorizedTransport(permit, send_claim_callback=CurrentProviderSendClaims(),
            sender=SimulatedXingSend(sender), clock=lambda:Clock.value)
        free = SiliconFlowFreeBinding(permit,transport,admission,result_storage)
        guard = BudgetUsageGuard(InMemoryBudgetGate(0),ModelRegistry.frozen_defaults().select('rerank'),0,
            zero_price_permit=permit)
        return free,guard
    binding = PairedSharedRetrievalBinding(staged.runtime.execution_contract,
        staged.runtime.shared_retrieval_binding.plans,
        late_rerank=(('miracl-zh',LateRerankPreparation(admission,bind)),), execution_kind='SIMULATED')
    item = replace(item,free_model_bindings={k:v for k,v in item.free_model_bindings.items() if k!='rerank'},
        model_usage_guards={k:v for k,v in item.model_usage_guards.items() if k!='rerank'})
    composition = replace(staged.composition,datasets={'miracl-zh':item},shared_retrieval_binding=binding)
    prep = prepare_normal_paired_runtime(composition,bind_answers=lambda _:pytest.fail('no answer binding'),**setup[0].paths)
    ranker = prep.runtime.containers['miracl-zh'].quick_chain.knowledge_gateway.retriever.ranker
    state=tmp_path/'SIMULATED-late-state.sqlite3';register_pair(state,binding.execution_contract)
    register_paired_run_link(state,binding.execution_contract,retrieval_binding_sha256=binding.fingerprint)
    for key,chunk in chunks.items():
        chunk.chunk_id=key;chunk.knowledge_base_id=next(iter(item.scope.knowledge_base_ids))
        chunk.document_id=next(iter(item.scope.document_ids));chunk.chunk_role='child';chunk.is_current=True
    # This content differs from the old fixed-plan fixture and is learned now.
    chunks['a'].content='actual newly recalled A';chunks['b'].content='actual newly recalled B'
    return prep,binding,ranker,embedding,state,row,sent,hits,chunks,sealed


def run_late(case, inside=None):
    from eval_center.public_runner import _shared_retrieval_context
    prep,binding,ranker,embedding,state,row,_,hits,chunks,_=case
    scope=prep.runtime.scopes['miracl-zh'];container=prep.runtime.containers['miracl-zh']
    with _shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',qid=row['qid'],scope=scope,
            question=row['question'],execution_context=container.execution_context):
        with model_access('embedding',allowed=True,scope=scope): embedding.embed([row['question']],10)
        if inside: inside(case)
        with model_access('rerank',allowed=True,scope=scope): return ranker.rank(row['question'],hits,chunks)


def test_late_seal_actual_new_candidates_then_exact_native_send(setup,monkeypatch,tmp_path):
    from eval_center.paired_state import attempts_for_qid
    case=late_rerank_case(setup,monkeypatch,tmp_path)
    assert case[6]==case[9]==[] and case[2].seals==()
    assert not case[0].runtime.containers['miracl-zh'].quick_chain.generation_roles
    result=run_late(case)
    assert [h.chunk_id for h in result]==['b','a']
    prepared=case[9][0]
    assert prepared.documents==('actual newly recalled A','actual newly recalled B')
    assert case[6]==[prepared.wire_bytes]
    children=attempts_for_qid(case[4],run_id=case[1].execution_contract.retrieval_run_id,
        qid=case[5]['qid'])['SHARED_RETRIEVAL']['provider_calls']
    assert len(children)==2 and all(c['status']=='simulated' for c in children)
    with pytest.raises(Exception,match='no_retry'): run_late(case)
    assert len(case[6])==len(case[9])==len(setup[5])==1


@pytest.mark.parametrize('tamper',['wire','order','version','content','query','tokens'])
def test_late_seal_rejects_callback_plan_tamper_zero_send(setup,monkeypatch,tmp_path,tamper):
    def mutate(c,p):
        return replace(c,**{'wire':dict(wire_sha256='0'*64),
            'order':dict(chunk_ids=tuple(reversed(c.chunk_ids)),candidate_order=tuple(reversed(c.chunk_ids))),
            'version':dict(document_version_ids=('changed','vb')),
            'content':dict(input_sha256=('0'*64,'0'*64)),
            'query':dict(query_sha256='0'*64),'tokens':dict(input_token_bound=c.input_token_bound+1)}[tamper])
    case=late_rerank_case(setup,monkeypatch,tmp_path,mutate)
    with pytest.raises(Exception,match='exact_actual_candidates'):run_late(case)
    assert case[6]==[] and len(case[9])==1
    with pytest.raises(Exception,match='no_retry'):run_late(case)
    assert len(setup[5])==1


@pytest.mark.parametrize('tamper',['scope','index','lease','cancel'])
def test_late_seal_refuses_drift_before_binding(setup,monkeypatch,tmp_path,tamper):
    case=late_rerank_case(setup,monkeypatch,tmp_path)
    def inside(c):
        if tamper=='scope':c[8]['a'].knowledge_base_id='another-kb'
        elif tamper=='index':c[0].paths['index_manifest_path'].write_text('tampered')
        elif tamper=='lease':Clock.value=c[1].execution_contract.execution_lease.deadline
        else:current_execution.get()[2].set()
    with pytest.raises(Exception):run_late(case,inside)
    assert case[6]==case[9]==[]


@pytest.mark.parametrize('tamper',['index','lease','cancel'])
def test_late_seal_rechecks_after_callback_before_any_send(setup,monkeypatch,tmp_path,tamper):
    case=late_rerank_case(setup,monkeypatch,tmp_path)
    ranker=case[2];original=ranker.policy.bind
    def bind(prepared):
        value=original(prepared)
        if tamper=='index':case[0].paths['index_manifest_path'].write_text('tampered')
        elif tamper=='lease':Clock.value=case[1].execution_contract.execution_lease.deadline
        else:current_execution.get()[2].set()
        return value
    ranker.policy=replace(ranker.policy,bind=bind)
    with pytest.raises(Exception):run_late(case)
    assert case[6]==[] and len(case[9])==1


def test_late_rerank_unknown_retains_occupancy_no_replay(setup,monkeypatch,tmp_path):
    from eval_center.paired_state import attempts_for_qid
    case=late_rerank_case(setup,monkeypatch,tmp_path)
    ranker=case[2];original=ranker.policy.bind;count=[]
    def bind(prepared):
        free,guard=original(prepared)
        def timeout(*args):count.append(1);raise TimeoutError('SIMULATED late UNKNOWN')
        free.authorized_transport.sender=SimulatedXingSend(timeout)
        return free,guard
    ranker.policy=replace(ranker.policy,bind=bind)
    with pytest.raises(Exception):run_late(case)
    receipt=attempts_for_qid(case[4],run_id=case[1].execution_contract.retrieval_run_id,
        qid=case[5]['qid'])['SHARED_RETRIEVAL']
    assert receipt['status']=='unknown'
    assert next(c for c in receipt['provider_calls'] if c['purpose']=='rerank')['status']=='unknown'
    assert all(r['state']=='unknown' for r in ranker.seals[0][2].budget_gate.reservations.values())
    with pytest.raises(Exception,match='no_retry'):run_late(case)
    assert count==[1] and len(setup[5])==1


def typed_checkpoint_fixture(prep):
    from backend.app.application.knowledge_gateway import EvidenceBundle
    from backend.app.application.query_router import EvidencePlan
    from backend.app.application.quality import QualityDecision
    from backend.app.application.retrieval import RetrievalItem
    from backend.app.domain.models import ChunkRecord, RankedHit
    gateway=prep.runtime.containers['miracl-zh'].quick_chain.knowledge_gateway
    original=gateway.retrieve_question;scope=prep.runtime.scopes['miracl-zh']
    def retrieve(scope,question,**kwargs):
        plan,result=original(scope,question,**kwargs)
        plan=replace(plan,evidence_plan=EvidencePlan(question,kind='simple'))
        chunk=ChunkRecord('SIMULATED-public-fixture',next(iter(scope.knowledge_base_ids)),
            next(iter(scope.document_ids)),'SIMULATED-v1','SIMULATED public evaluation evidence')
        return plan,replace(result,items=[RetrievalItem(chunk,RankedHit(chunk_id=chunk.chunk_id,rank=1))])
    gateway.retrieve_question=retrieve
    gateway.evidence.bundle=lambda plan,result:EvidenceBundle(plan,result,tuple(result.items),QualityDecision(True),
        context='SIMULATED public evaluation evidence.')
    original_context=gateway.evidence.with_context
    def context(bundle,run,citations):
        value=original_context(bundle,run,citations)
        return replace(bundle,context=value.context,labels=value.labels,snapshots=value.snapshots)
    gateway.evidence.with_context=context


def test_checkpoint_recovery_before_answer_sends_reuses_identical_context(setup,tmp_path):
    typed_checkpoint_fixture(setup[0])
    prep=setup[0];prep.checkpoint_path=tmp_path/'SIMULATED-context.json'
    first=bind_case(setup)
    digest=prep.checkpoint_sha256
    assert digest==hashlib.sha256(prep.checkpoint_path.read_bytes()).hexdigest()
    restored=PairedRetrievalPreparation(prep.composition,prep.runtime,prep.paths,prep._bind_answers,
        lambda:False,checkpoint_path=prep.checkpoint_path,checkpoint_sha256=digest)
    second=bind_case((restored,*setup[1:]))
    assert [w.wire_bytes for w in restored.wires]==[w.wire_bytes for w in prep.wires]
    assert first.containers['miracl-zh'].prepared_answer_envelopes==second.containers['miracl-zh'].prepared_answer_envelopes
    assert len(setup[5])==1


@pytest.mark.parametrize('tamper',['bytes','hash','index','lease','cancel','unknown'])
def test_checkpoint_recovery_rejects_tamper_or_unknown_zero_sends(setup,tmp_path,tamper):
    typed_checkpoint_fixture(setup[0])
    from eval_center.paired_state import finish_attempt
    prep=setup[0];prep.checkpoint_path=tmp_path/'SIMULATED-context.json';bind_case(setup)
    digest=prep.checkpoint_sha256
    restored=PairedRetrievalPreparation(prep.composition,prep.runtime,prep.paths,prep._bind_answers,
        lambda:False,checkpoint_path=prep.checkpoint_path,checkpoint_sha256=digest)
    if tamper=='bytes':prep.checkpoint_path.write_bytes(prep.checkpoint_path.read_bytes()+b' ')
    elif tamper=='hash':restored.checkpoint_sha256='0'*64
    elif tamper=='index':prep.paths['index_manifest_path'].write_text('changed')
    elif tamper=='lease':Clock.value=prep.runtime.execution_contract.execution_lease.deadline
    elif tamper=='cancel':restored._is_cancelled=lambda:True
    else:
        # SIMULATED recovery-corruption fixture only; terminal production APIs
        # correctly forbid rewriting succeeded receipts.
        import sqlite3
        with sqlite3.connect(setup[1]) as connection:
            connection.execute("UPDATE paired_attempts SET status='unknown' WHERE run_id=? AND qid=?",
                (prep.runtime.execution_contract.retrieval_run_id,setup[2]['qid']))
    with pytest.raises(Exception):bind_case((restored,*setup[1:]))
    assert len(setup[5])==1


def test_late_rerank_then_full_answer_admission_and_checkpoint_recovery(setup,monkeypatch,tmp_path):
    from backend.app.application.retrieval import RetrievalResult
    case=late_rerank_case(setup,monkeypatch,tmp_path)
    prep,binding,ranker,_,state,row,sent,_,_,sealed=case
    base=setup[0]
    typed_checkpoint_fixture(base)
    # SIMULATED retrieval fixture, real stage/ranker/permit/claim/admission wiring.
    gateway=base.runtime.containers['miracl-zh'].quick_chain.knowledge_gateway
    original=gateway.retrieve_question
    def retrieve(scope,question,**kwargs):
        plan,result=original(scope,question,**kwargs)
        hits=tuple(entry.hit for entry in result.items)
        chunks={entry.chunk.chunk_id:entry.chunk for entry in result.items}
        with model_access('rerank',allowed=True,scope=scope):ranked=ranker.rank(question,hits,chunks)
        items={entry.chunk.chunk_id:entry for entry in result.items}
        return plan,replace(result,items=[items[h.chunk_id] for h in ranked])
    gateway.retrieve_question=retrieve
    prep.runtime.containers['miracl-zh'].quick_chain.knowledge_gateway=gateway
    prep.runtime.containers['miracl-zh'].quick_chain.context_manager=None
    def answers(wires):
        bound=base._bind_answers(wires).datasets['miracl-zh']
        old=prep.composition.datasets['miracl-zh']
        return replace(prep.composition,datasets={'miracl-zh':replace(old,xing_binding=bound.xing_binding,
            model_usage_guards={**old.model_usage_guards,'chat_cheap':bound.model_usage_guards['chat_cheap']})})
    prep._bind_answers=answers;prep.checkpoint_path=tmp_path/'SIMULATED-late-context.json'
    runtime=bind_case((prep,state,row,*setup[3:5],setup[5],setup[6]))
    assert len(sent)==len(sealed)==len(setup[5])==1 and len(prep.wires)==4
    # No predeclared candidate plan was used or retroactively added to the link.
    assert binding.rerank_plans==() and runtime.shared_retrieval_binding is binding
    restored=PairedRetrievalPreparation(prep.composition,prep.runtime,prep.paths,answers,lambda:False,
        checkpoint_path=prep.checkpoint_path,checkpoint_sha256=prep.checkpoint_sha256)
    bind_case((restored,state,row,*setup[3:5],setup[5],setup[6]))
    assert [w.wire_bytes for w in restored.wires]==[w.wire_bytes for w in prep.wires]
    assert len(sent)==len(sealed)==len(setup[5])==1


@pytest.mark.parametrize('kind,statuses,accepted',[
    ('REAL',('succeeded','succeeded'),True),
    ('SIMULATED',('simulated','simulated'),True),
    ('REAL',('simulated','simulated'),False),
    ('SIMULATED',('succeeded','succeeded'),False),
    ('REAL',('succeeded','simulated'),False),
    ('SIMULATED',('simulated','succeeded'),False),
    ('REAL',('succeeded','unknown'),False),
    ('SIMULATED',('simulated','unknown'),False),
])
def test_receipt_kind_contract_without_provider_transport(kind,statuses,accepted):
    from eval_center.paired_preparation import require_retrieval_receipt_status
    children=[dict(purpose=p,status=s) for p,s in zip(('query_embedding','rerank'),statuses)]
    if accepted:
        assert require_retrieval_receipt_status(children,execution_kind=kind,
            required_purposes={'query_embedding','rerank'})==statuses[0]
    else:
        with pytest.raises(Exception,match='execution_kind_mismatch'):
            require_retrieval_receipt_status(children,execution_kind=kind,
                required_purposes={'query_embedding','rerank'})


@pytest.mark.parametrize('kind,parent,children,accepted',[
    ('REAL','succeeded',('succeeded','succeeded'),True),
    ('SIMULATED','simulated',('simulated','simulated'),True),
    ('REAL','simulated',('simulated','simulated'),False),
    ('SIMULATED','succeeded',('succeeded','succeeded'),False),
    ('REAL','succeeded',('succeeded','simulated'),False),
    ('SIMULATED','simulated',('simulated','succeeded'),False),
    ('REAL','succeeded',('succeeded','unknown'),False),
    ('SIMULATED','unknown',('simulated','unknown'),False),
])
def test_checkpoint_read_kind_contract_no_real_transport(setup,tmp_path,monkeypatch,kind,parent,children,accepted):
    # State-read contract test; no pretend REAL model/transport/DB invocation.
    prep=setup[0];row=setup[2]
    # Required rerank presence here uses a read-contract stub, not a fake production plan.
    runtime=SimpleNamespace(execution_contract=prep.runtime.execution_contract,
        shared_retrieval_binding=SimpleNamespace(execution_kind=kind,fingerprint='a'*64,rerank_plans=True,late_rerank=()))
    restored=PairedRetrievalPreparation(SimpleNamespace(execution_kind=kind),runtime,prep.paths,
        lambda _:pytest.fail('no bind'),lambda:False,checkpoint_path=tmp_path/'SIMULATED-read-contract.json')
    record={'SHARED_RETRIEVAL':dict(status=parent,provider_calls=[dict(purpose=p,status=s)
        for p,s in zip(('query_embedding','rerank'),children)])}
    monkeypatch.setattr('eval_center.paired_state.attempts_for_qid',lambda *a,**k:record)
    cases={row['dataset']+'\0'+row['qid']:dict(bundle='not used at read boundary',receipt=record)}
    raw=json.dumps(dict(identity=restored._checkpoint_identity(),cases=cases)).encode()
    restored.checkpoint_path.write_bytes(raw);restored.checkpoint_sha256=hashlib.sha256(raw).hexdigest()
    if accepted:assert restored._read_checkpoint(setup[1],[row])==cases
    else:
        with pytest.raises(Exception,match='receipt_changed_or_unknown|execution_kind_mismatch'):
            restored._read_checkpoint(setup[1],[row])
    assert setup[5]==[]


def test_real_late_rerank_refuses_simulated_query_receipt_before_seal(setup,monkeypatch,tmp_path):
    case=late_rerank_case(setup,monkeypatch,tmp_path)
    # Contract-only injection at the read boundary, never a fake REAL transport.
    case[2].binding=replace(case[1],execution_kind='REAL')
    monkeypatch.setattr(case[2],'_guard',lambda claims:None)
    with pytest.raises(Exception,match='execution_kind_mismatch'):run_late(case)
    assert case[6]==case[9]==[] and len(setup[5])==1


@pytest.mark.parametrize('kind,statuses,accepted',[
    ('REAL',('succeeded','succeeded'),True),
    ('SIMULATED',('simulated','simulated'),True),
    ('REAL',('simulated','simulated'),False),
    ('SIMULATED',('succeeded','succeeded'),False),
    ('REAL',('succeeded','simulated'),False),
    ('SIMULATED',('simulated','succeeded'),False),
    ('REAL',('succeeded','unknown'),False),
])
def test_shared_context_terminal_kind_contract(setup,monkeypatch,tmp_path,kind,statuses,accepted):
    from eval_center import public_runner, paired_state
    prep=setup[0];row=setup[2]
    binding=replace(prep.runtime.shared_retrieval_binding,execution_kind=kind)
    # Declare the rank stage with an existing valid fixed plan; no send occurs.
    staged,fixed,_,_,_,_,_,_,_=shared_rerank_case(setup,monkeypatch,tmp_path)
    binding=replace(binding,rerank_plans=fixed.rerank_plans)
    state=tmp_path/'SIMULATED-contract-read-state.sqlite3';register_pair(state,binding.execution_contract)
    register_paired_run_link(state,binding.execution_contract,retrieval_binding_sha256=binding.fingerprint)
    real_read=paired_state.attempts_for_qid
    # Explicit injected status-reader contract fixture; no pretend REAL provider.
    children=[dict(purpose=p,status=s) for p,s in zip(('query_embedding','rerank'),statuses)]
    monkeypatch.setattr(paired_state,'attempts_for_qid',lambda *a,**k:{'SHARED_RETRIEVAL':dict(provider_calls=children)})
    container=prep.runtime.containers['miracl-zh']
    def scope():return public_runner._shared_retrieval_context(binding,state_path=state,dataset='miracl-zh',
        qid=row['qid'],scope=prep.runtime.scopes['miracl-zh'],question=row['question'],
        execution_context=container.execution_context)
    if accepted:
        with scope():pass
    else:
        with pytest.raises(Exception,match='execution_kind_mismatch'):
            with scope():pass
    receipt=real_read(state,run_id=binding.execution_contract.retrieval_run_id,qid=row['qid'])['SHARED_RETRIEVAL']
    assert receipt['status']==(statuses[0] if accepted else 'unknown')
    assert setup[5]==[]
