"""SIMULATED providers/evidence; real temporary SQLite; no live DB/API/SSH."""
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from backend.tests.test_v6_xing_generation_binding import counter, no_live_sender

from backend.app.domain.scope import Scope
from backend.app.application.validation_usd_budget import V6ExecutionLease, OWNER_V6_TOKEN_GRANT_R3
from eval_center.paired_state import (
    PairedCohortContract, PairedDatasetSelection, PairedContractError, load_frozen_cohort,
    register_run, register_paired_run_link, validate_paired_run_link, begin_provider_batch_scope,
    ProviderBatchPlan, ProviderBatchCall, CurrentProviderSendClaims, stable_provider_request_id,
    provider_send_claim_record, attempts_for_qid, run_record,
)
from eval_center.public_runner import (
    PairedSharedRetrievalBinding, _validate_execution_contract, _validate_paired_query_embedding,
    _shared_retrieval_context, _canonical_hash,
)

FIXED = datetime(2026, 10, 10, 3, 50, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline_clock_and_transport(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED if tz else FIXED.replace(tzinfo=None)
    monkeypatch.setattr('eval_center.paired_state.datetime', Clock)
    monkeypatch.setattr('eval_center.public_runner.datetime', Clock)
    monkeypatch.setattr('backend.app.adapters.models.deepseek.build_opener',
        lambda *a, **k: pytest.fail('LIVE transport forbidden'))
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-NOT-A-KEY')


def files(tmp_path):
    row = dict(dataset='miracl-zh', dataset_version='SIMULATED-public-v1', qid='277616#0',
               question='public-0-0', qrels={}, split='development')
    cohort, source, index = (tmp_path / n for n in ('cohort.jsonl', 'source.json', 'index.json'))
    cohort.write_text(json.dumps(row)+'\n', encoding='utf-8')
    manifest = dict(status='FROZEN_FOR_PREPARATION', datasets=[dict(dataset='miracl-zh')])
    source.write_text(json.dumps(manifest), encoding='utf-8')
    index.write_text('{"execution":"SIMULATED"}', encoding='utf-8')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    scope = Scope.from_ids(['public-kb'], ['public-doc'])
    lease = V6ExecutionLease('SIMULATED-new-lease', '2026-10-10T03:45:00+00:00',
                             '2026-10-10T04:25:00+00:00', None)
    contract = PairedCohortContract(1, 'SETUP', 'SIMULATED-cohort', 'SIMULATED-retrieval',
        sha(cohort), sha(source), sha(index), (PairedDatasetSelection('miracl-zh', ('277616#0',), scope, 'd'*64),), lease)
    return row, cohort, source, index, manifest, contract


def register_pair(state, contract):
    for rid, purpose in ((contract.cohort_run_id, 'COHORT'), (contract.retrieval_run_id, 'RETRIEVAL')):
        register_run(state, run_id=rid, cohort_sha256=contract.cohort_sha256,
            source_manifest_sha256=contract.source_manifest_sha256, code_sha='c'*64,
            deadline_utc=contract.execution_lease.deadline_utc,
            dispatch_deadline_utc='2026-10-10T04:20:00+00:00',
            round_deadline_utc=contract.execution_lease.deadline_utc,
            expected_qids=[q for s in contract.selections for q in s.qids], execution_purpose=purpose)


def test_setup_is_explicit_and_batch_default_remains_exact(tmp_path):
    row, cohort, _, _, _, contract = files(tmp_path)
    with pytest.raises(PairedContractError, match='cohort_sample_counts_invalid'):
        load_frozen_cohort(cohort)
    assert load_frozen_cohort(cohort, execution_contract=contract) == [row]
    with pytest.raises(PairedContractError, match='cohort_sample_counts_invalid'):
        load_frozen_cohort(cohort, execution_contract=replace(contract, phase='BATCH'))


@pytest.mark.parametrize('change', ['version', 'cohort_hash', 'qids', 'duplicate', 'bounds'])
def test_setup_loader_rejects_drift(tmp_path, change):
    _, cohort, _, _, _, contract = files(tmp_path)
    selection = contract.selections[0]
    changes = dict(version=dict(schema_version=True), cohort_hash=dict(cohort_sha256='f'*64),
        qids=dict(selections=(replace(selection, qids=('wrong',)),)),
        duplicate=dict(selections=(selection, selection)),
        bounds=dict(selections=(replace(selection, scope=Scope.from_ids(['public-kb'], [str(i) for i in range(6)])),)))
    with pytest.raises((PairedContractError, ValueError)):
        load_frozen_cohort(cohort, execution_contract=replace(contract, **changes[change]))


@pytest.mark.parametrize('change', ['source', 'index', 'run', 'scope', 'index_identity', 'dataset'])
def test_setup_runtime_identity_rejects_drift(tmp_path, change):
    row, _, _, _, manifest, contract = files(tmp_path)
    kw = dict(cohort=[row], manifest=manifest, cohort_hash=contract.cohort_sha256,
        source_hash=contract.source_manifest_sha256, index_hash=contract.index_manifest_sha256,
        run_id=contract.cohort_run_id, scopes={'miracl-zh': contract.selections[0].scope},
        identities={'miracl-zh': 'd'*64})
    if change in ('source', 'index'): kw[change+'_hash'] = 'f'*64
    if change == 'run': kw['run_id'] = 'wrong'
    if change == 'scope': kw['scopes'] = {'miracl-zh': Scope.from_ids(['other'], ['other'])}
    if change == 'index_identity': kw['identities'] = {'miracl-zh': 'f'*64}
    if change == 'dataset': kw['manifest'] = {'datasets': [{'dataset': 'scifact'}]}
    with pytest.raises(PairedContractError): _validate_execution_contract(contract, **kw)


def query_setup(monkeypatch, tmp_path):
    from backend.tests.test_v6_free_model_mainpath import embedding_setup, REGISTRY
    from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.application.provider_usage import BudgetUsageGuard
    from backend.app.ports.xing_zero_reservation import (
        SiliconFlowFreeBinding, free_dispatch_contract, free_dispatch_identity_sha256)
    monkeypatch.setenv('V6_SIMULATED_RESULT_ROOT', str(tmp_path/'SIMULATED-results'))
    row, cohort, source, index, manifest, contract = files(tmp_path)
    adapter, permit, _, budget, wires, _, _ = embedding_setup(monkeypatch)
    query_sha = hashlib.sha256(row['question'].encode()).hexdigest()
    call = replace(permit.calls[0], qid=row['qid'], arm='SHARED_RETRIEVAL',
        request_id=stable_provider_request_id(run_id=contract.retrieval_run_id, qid=row['qid'],
            arm='SHARED_RETRIEVAL', purpose='query_embedding', call_ordinal=1),
        query_sha256=query_sha, input_sha256=(query_sha,))
    permit = replace(permit, grant_id=OWNER_V6_TOKEN_GRANT_R3.grant_id + ':SIMULATED-query',
        run_id=contract.retrieval_run_id,
        source_manifest_sha256=contract.source_manifest_sha256, deadline_utc=None, calls=(call,),
        owner_activity_id=OWNER_V6_TOKEN_GRANT_R3.grant_id, execution_lease=contract.execution_lease)
    permit = replace(permit, frozen_plan_sha256=permit.fingerprint)
    transport = SiliconFlowAuthorizedTransport(permit, send_claim_callback=CurrentProviderSendClaims(),
        sender=adapter.transport.sender, clock=lambda: FIXED)
    free = SiliconFlowFreeBinding(permit, transport, adapter.admission, adapter.free_binding.result_storage)
    guard = BudgetUsageGuard(budget, adapter.spec, 0, zero_price_permit=permit)
    adapter = ProviderFactory(REGISTRY, enabled_roles=['embedding'], usage_guards={'embedding': guard},
        free_model_bindings={'embedding': free}).build('embedding')
    plan = ProviderBatchPlan('query_embedding', 'siliconflow', permit.model, adapter.identity.fingerprint,
        hashlib.sha256(adapter.identity.chunking_index_identity.encode()).hexdigest(),
        free_dispatch_contract(permit, call)['scope_sha256'],
        (ProviderBatchCall(1, (), (), (query_sha,), call.wire_sha256,
            free_dispatch_identity_sha256(permit, call)),), query_sha)
    binding = PairedSharedRetrievalBinding(contract, (('miracl-zh', row['qid'], call.batch_id, plan),), execution_kind='SIMULATED')
    return row, contract, binding, free, adapter, budget, wires


def test_typed_query_permit_uses_retrieval_not_cohort(monkeypatch, tmp_path):
    row, contract, binding, free, _, _, _ = query_setup(monkeypatch, tmp_path)
    item = SimpleNamespace(free_model_bindings={'embedding': free}, scope=contract.selections[0].scope)
    kw = dict(qids={row['qid']}, cohort=[row], run_id=contract.cohort_run_id,
        source_sha256=contract.source_manifest_sha256, execution_kind='SIMULATED', deadline_utc=None,
        required=True, owner_activity_id=OWNER_V6_TOKEN_GRANT_R3.grant_id,
        execution_lease=contract.execution_lease, dataset='miracl-zh')
    with pytest.raises(ValueError, match='independent_shared_retrieval_binding_required'):
        _validate_paired_query_embedding(item, **kw)
    _validate_paired_query_embedding(item, retrieval_binding=binding, **kw)
    with pytest.raises(ValueError, match='paired_shared_retrieval_contract_mismatch'):
        _validate_paired_query_embedding(item, retrieval_binding=binding,
            **{**kw, 'execution_lease': replace(contract.execution_lease)})


def test_link_is_atomic_immutable_and_old_runs_untouched(tmp_path):
    *_, contract = files(tmp_path)
    state = tmp_path/'SIMULATED.sqlite3'; register_pair(state, contract)
    before = [run_record(state, r) for r in (contract.cohort_run_id, contract.retrieval_run_id)]
    def bind(): register_paired_run_link(state, contract, retrieval_binding_sha256='e'*64)
    with ThreadPoolExecutor(2) as pool: list(pool.map(lambda _: bind(), range(2)))
    assert validate_paired_run_link(state, contract, retrieval_binding_sha256='e'*64, for_dispatch=True)
    assert before == [run_record(state, r) for r in (contract.cohort_run_id, contract.retrieval_run_id)]
    with pytest.raises(PairedContractError, match='paired_run_link_conflict'):
        register_paired_run_link(state, contract, retrieval_binding_sha256='f'*64)


def test_normal_query_transport_nested_claim_and_unknown_no_replay(monkeypatch, tmp_path):
    from backend.app.ports.model_access import model_access
    from backend.app.ports.run_lifecycle import current_execution
    from threading import Event
    row, contract, binding, free, adapter, budget, wires = query_setup(monkeypatch, tmp_path)
    state = tmp_path/'SIMULATED.sqlite3'; register_pair(state, contract)
    register_paired_run_link(state, contract, retrieval_binding_sha256=binding.fingerprint)
    # Explicit simulated product lifecycle, real temporary SQLite native claims.
    token = current_execution.set((SimpleNamespace(run_id=contract.retrieval_run_id), None, Event(), 'SIMULATED-session'))
    try:
        with _shared_retrieval_context(binding, state_path=state, dataset='miracl-zh', qid=row['qid'],
                scope=free.permit.scope, question=row['question']), model_access('embedding', allowed=True, scope=free.permit.scope):
            adapter.embed([row['question']], 10)
        receipt = provider_send_claim_record(state, request_id=free.permit.calls[0].request_id)
        assert receipt['run_id'] == contract.retrieval_run_id and receipt['status'] == 'simulated'
        assert attempts_for_qid(state, run_id=contract.retrieval_run_id, qid=row['qid'])['SHARED_RETRIEVAL']['status'] == 'simulated'
        with pytest.raises(PairedContractError, match='attempt_already_exists_no_retry'):
            with _shared_retrieval_context(binding, state_path=state, dataset='miracl-zh', qid=row['qid'],
                    scope=free.permit.scope, question=row['question']): pass
        assert len(wires) == 1
        assert all(r['state'] == 'unknown' for r in budget.reservations.values())
    finally: current_execution.reset(token)


def test_single_miracl_normal_assembly_and_common_scheduler(tmp_path, counter, monkeypatch):
    from eval_center.tests.test_public_paired_meet import planned_native_fixture, composition_for, SOURCE, COHORT
    from eval_center.public_runner import build_normal_paired_runtime, run_public_paired_cohort
    from eval_center.paired_state import SQLiteProviderBatchSendClaims
    from eval_center.public_paired_reporting import time_profile
    from eval_center.public_benchmark_cli import _parser
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.domain.model_registry import ModelRegistry

    # Existing native provider/normal QuickChain fixture: providers and evidence
    # are explicitly SIMULATED; no live bootstrap DB/credentials are consulted.
    rid = 'SIMULATED-setup-normal'
    runtime, _ = planned_native_fixture(tmp_path, counter, rid)
    original = runtime.containers['miracl-zh']
    row = next(r for r in load_frozen_cohort(COHORT) if r['dataset'] == 'miracl-zh')
    cohort = tmp_path/'setup.jsonl'; cohort.write_text(json.dumps(row, ensure_ascii=False)+'\n', encoding='utf-8')
    index = tmp_path/'index.json'; index.write_text('{"execution":"SIMULATED"}', encoding='utf-8')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    entry = next(e for e in json.loads(SOURCE.read_text())['datasets'] if e['dataset'] == 'miracl-zh')
    index_identity = _canonical_hash(dict(execution_kind='SIMULATED', corpus_sha256=entry['corpus']['sha256'], index='NO_REAL_INDEX'))
    scope = runtime.scopes['miracl-zh']
    lease = V6ExecutionLease('SIMULATED-small', '2026-10-10T03:45:00+00:00', '2026-10-10T04:25:44Z', None)
    contract = PairedCohortContract(1, 'SETUP', rid, rid+'-retrieval', sha(cohort), sha(SOURCE), sha(index),
        (PairedDatasetSelection('miracl-zh', (row['qid'],), scope, index_identity),), lease)
    query_sha = hashlib.sha256(row['question'].encode()).hexdigest()
    plan = ProviderBatchPlan('query_embedding','siliconflow','BAAI/bge-m3','a'*64,'b'*64,'c'*64,
        (ProviderBatchCall(1,(),(),(query_sha,),'d'*64,'e'*64),),query_sha)
    shared = PairedSharedRetrievalBinding(contract, (('miracl-zh', row['qid'], 'SIMULATED-query', plan),), execution_kind='SIMULATED')
    old_composition = composition_for(runtime)
    item = old_composition.datasets['miracl-zh']
    window = replace(item.xing_binding.request_window, cohort_sha256=sha(cohort),
        calls=tuple(c for c in item.xing_binding.request_window.calls if c.qid == row['qid']))
    window = replace(window, frozen_plan_sha256=window.fingerprint)
    item.v6_binding.activity_gate.execution_lease = lease
    item.v6_binding.activity_gate.clock = lambda: FIXED
    item = replace(item, settings=replace(item.settings, rerank_enabled=False),
        xing_binding=replace(item.xing_binding, request_window=window),
        deepseek_calls=tuple(c for c in item.deepseek_calls if c.qid == row['qid']))
    composition = replace(old_composition, datasets={'miracl-zh': item},
        execution_contract=contract, shared_retrieval_binding=shared, index_manifest_sha256=sha(index))
    construction, queries = [], []
    original._fixture_execution_context = original.execution_context
    def build(settings, **kwargs):
        construction.append(kwargs['v6_generation_binding'].server_scope)
        cheap = original.quick_chain.generation_roles['chat_cheap']
        gateway = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('chat_cheap',),
            usage_guards=kwargs['model_usage_guards']).build('chat_cheap', xing_binding=kwargs['v6_xing_binding'])
        original.quick_chain.generation_roles['chat_cheap'] = replace(cheap, gateway=gateway,
            xing_binding=kwargs['v6_xing_binding'])
        return original
    monkeypatch.setattr('eval_center.public_runner.build_container', build)
    retrieve = original.quick_chain.knowledge_gateway.retrieve_question
    def simulated_query(*args, **kwargs):
        claims = CurrentProviderSendClaims._bound()
        assert type(claims) is SQLiteProviderBatchSendClaims and claims.run_id == contract.retrieval_run_id
        queries.append(claims.run_id)
        request_id = claims.request_id(purpose='query_embedding')
        claims(request_id=request_id, purpose='query_embedding', provider='siliconflow', model='BAAI/bge-m3',
            role='embedding', request_identity_sha256='e'*64)
        claims.settle(request_id=request_id, status='simulated', result=dict(
            result_sha256='f'*64, result_ref='sha256:'+'f'*64, item_count=1))
        return retrieve(*args, **kwargs)
    monkeypatch.setattr(original.quick_chain.knowledge_gateway, 'retrieve_question', simulated_query)
    profile = time_profile('owner-lease-small', native_execution_lease=lease)
    built = build_normal_paired_runtime(composition, source_manifest_path=SOURCE, index_manifest_path=index,
        cohort_path=cohort, run_id=rid, time_profile=profile, execution_contract=contract)
    assert construction == [scope]
    state = tmp_path/'setup-center.sqlite3'
    report = run_public_paired_cohort(cohort_path=cohort, source_path=SOURCE,
        data_root=Path(_parser().parse_args(['--phase', 'paired-run']).data_root), state_path=state, run_id=rid,
        runtime_factory=lambda _: built, index_manifest_path=index,
        time_profile=profile, execution_contract=contract)
    assert report['status'] == 'SIMULATED_PAIRED_COHORT_COMPLETED', report.get('failures')
    assert report['qid_count'] == 1 and report['quickchain_invocations'] == 3
    assert queries == [contract.retrieval_run_id]
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT COUNT(*) FROM paired_provider_calls WHERE run_id=? AND purpose='query_embedding'", (contract.retrieval_run_id,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM paired_provider_calls WHERE run_id=? AND purpose='answer'", (contract.cohort_run_id,)).fetchone()[0] == 3
    result = __import__('eval_center.paired_state', fromlist=['load_case_results']).load_case_results(state, rid)[0]
    assert result['paired_input_hashes'] and result['observed_retrieval_calls'] == 1


def test_shared_query_timeout_keeps_native_unknown_and_never_replays(monkeypatch, tmp_path):
    from backend.app.adapters.models.cloud import SimulatedXingSend
    from backend.app.ports.model_access import model_access
    from backend.app.ports.run_lifecycle import current_execution
    from backend.app.ports.providers import ProviderUnavailable
    from threading import Event
    row, contract, binding, free, adapter, budget, _ = query_setup(monkeypatch, tmp_path)
    state = tmp_path/'SIMULATED.sqlite3'; register_pair(state, contract)
    register_paired_run_link(state, contract, retrieval_binding_sha256=binding.fingerprint)
    calls = []
    def timeout(*args):
        calls.append(1); raise TimeoutError('SIMULATED uncertain provider')
    adapter.transport.sender = SimulatedXingSend(timeout)
    token = current_execution.set((SimpleNamespace(run_id=contract.retrieval_run_id), None, Event(), 'SIMULATED-session'))
    try:
        with pytest.raises(ProviderUnavailable):
            with _shared_retrieval_context(binding, state_path=state, dataset='miracl-zh', qid=row['qid'],
                    scope=free.permit.scope, question=row['question']), model_access('embedding', allowed=True, scope=free.permit.scope):
                adapter.embed([row['question']], 10)
        assert provider_send_claim_record(state, request_id=free.permit.calls[0].request_id)['status'] == 'unknown'
        with pytest.raises(PairedContractError, match='attempt_already_exists_no_retry'):
            with _shared_retrieval_context(binding, state_path=state, dataset='miracl-zh', qid=row['qid'],
                    scope=free.permit.scope, question=row['question']): pass
        assert calls == [1] and all(r['state']=='unknown' for r in budget.reservations.values())
    finally: current_execution.reset(token)


def test_failed_shared_receipt_never_populates_frozen_context():
    from contextlib import contextmanager
    from eval_center.public_runner import _FrozenCaseGateway
    gateway = SimpleNamespace(evidence=SimpleNamespace(), retrieve_question=lambda *a, **k: 'SIMULATED-UNVERIFIED')
    @contextmanager
    def failed(*args):
        yield
        raise ValueError('SIMULATED missing durable receipt')
    frozen = _FrozenCaseGateway(gateway, retrieval_context=failed)
    with pytest.raises(ValueError): frozen.retrieve_question(None, 'SIMULATED public question')
    assert frozen.retrieved is None


def test_two_qids_normal_assembly_share_one_retrieval_lifecycle_and_reject_replay(tmp_path, counter, monkeypatch):
    """SIMULATED repositories/providers; real RunLifecycle/QuickChain/native SQLite."""
    from contextlib import contextmanager
    from eval_center.tests.test_public_paired_meet import planned_native_fixture, composition_for, SOURCE, COHORT
    from eval_center.public_runner import build_normal_paired_runtime, run_public_paired_cohort
    from eval_center.paired_state import SQLiteProviderBatchSendClaims, SQLiteProviderSendClaims, load_case_results
    from eval_center.public_paired_reporting import time_profile
    from eval_center.public_benchmark_cli import _parser
    from backend.app.adapters.models.factory import ProviderFactory
    from backend.app.domain.model_registry import ModelRegistry
    from backend.app.application.rule_router_v1 import GenerationRole
    from backend.app.application.run_lifecycle import RunLifecycle
    from backend.app.ports.run_lifecycle import RunClaim, current_execution, execution_owner
    from backend.app.ports.xing_zero_reservation import current_free_request

    rid = 'SIMULATED-two-qids-normal'
    runtime, _ = planned_native_fixture(tmp_path, counter, rid)
    original = runtime.containers['miracl-zh']
    rows = [r for r in load_frozen_cohort(COHORT) if r['dataset'] == 'miracl-zh'][:2]
    qids = tuple(r['qid'] for r in rows)
    assert len(set(qids)) == 2
    cohort = tmp_path/'setup-two.jsonl'
    cohort.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    index = tmp_path/'index.json'; index.write_text('{"execution":"SIMULATED"}', encoding='utf-8')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    entry = next(e for e in json.loads(SOURCE.read_text())['datasets'] if e['dataset'] == 'miracl-zh')
    index_identity = _canonical_hash(dict(execution_kind='SIMULATED', corpus_sha256=entry['corpus']['sha256'], index='NO_REAL_INDEX'))
    scope = runtime.scopes['miracl-zh']
    lease = V6ExecutionLease('SIMULATED-two-qids-lease', '2026-10-10T03:45:00+00:00', '2026-10-10T04:25:44Z', None)
    contract = PairedCohortContract(1, 'SETUP', rid, rid+'-retrieval', sha(cohort), sha(SOURCE), sha(index),
        (PairedDatasetSelection('miracl-zh', qids, scope, index_identity),), lease)
    plans = []
    for row in rows:
        query_sha = hashlib.sha256(row['question'].encode()).hexdigest()
        plan = ProviderBatchPlan('query_embedding', 'siliconflow', 'BAAI/bge-m3', 'a'*64, 'b'*64, 'c'*64,
            (ProviderBatchCall(1, (), (), (query_sha,), 'd'*64, 'e'*64),), query_sha)
        plans.append(('miracl-zh', row['qid'], 'SIMULATED-query-'+row['qid'], plan))
    shared = PairedSharedRetrievalBinding(contract, tuple(plans), execution_kind='SIMULATED')
    old_composition = composition_for(runtime)
    item = old_composition.datasets['miracl-zh']
    window = replace(item.xing_binding.request_window, cohort_sha256=sha(cohort),
        calls=tuple(c for c in item.xing_binding.request_window.calls if c.qid in qids))
    window = replace(window, frozen_plan_sha256=window.fingerprint)
    item.v6_binding.activity_gate.execution_lease = lease
    item.v6_binding.activity_gate.clock = lambda: FIXED
    item = replace(item, settings=replace(item.settings, rerank_enabled=False),
        xing_binding=replace(item.xing_binding, request_window=window),
        deepseek_calls=tuple(c for c in item.deepseek_calls if c.qid in qids))
    composition = replace(old_composition, datasets={'miracl-zh': item}, execution_contract=contract,
        shared_retrieval_binding=shared, index_manifest_sha256=sha(index))

    # One production lifecycle execution owns the RETRIEVAL Run throughout both
    # questions. Each query hook borrows its live context; it never completes or
    # recreates that Run. Repository/stream effects are explicitly SIMULATED.
    events, borrowed, chats, queries, rejects, construction = [], [], [], [], [], []
    repository = SimpleNamespace(lease_seconds=60,
        renew=lambda *a: events.append('renew'), finish_run=lambda *a, **k: events.append('finish'),
        fail_run=lambda *a: events.append('fail'), cleanup_done=lambda *a: events.append('cleanup'))
    streams = SimpleNamespace(live_ttl=60,
        set_live_run=lambda *a: events.append('start') or True,
        renew_live_run=lambda *a: True, clear_live_run=lambda *a: events.append('clear') or True)
    lifecycle = RunLifecycle(repository, streams)
    claim = RunClaim(contract.retrieval_run_id, 'SIMULATED-two-qid-owner', True, 'running')
    persistent = []
    chat_context = original.execution_context
    @contextmanager
    def execution_context(product_run_id):
        if product_run_id == contract.retrieval_run_id:
            assert persistent and events.count('start') == 1 and 'finish' not in events
            token = current_execution.set(persistent[0])
            owner_token = execution_owner.set((claim.run_id, claim.owner))
            try:
                assert RunLifecycle.assert_current()[0] is claim
                borrowed.append(claim.run_id)
                yield
            finally:
                current_execution.reset(token); execution_owner.reset(owner_token)
        else:
            with chat_context(product_run_id):
                current_claim = RunLifecycle.assert_current()[0]
                owner_token = execution_owner.set((current_claim.run_id, current_claim.owner))
                try: yield
                finally: execution_owner.reset(owner_token)
    original._fixture_execution_context = execution_context
    def build(settings, **kwargs):
        construction.append(kwargs['v6_generation_binding'].server_scope)
        cheap = original.quick_chain.generation_roles['chat_cheap']
        gateway = ProviderFactory(ModelRegistry.frozen_defaults(), enabled_roles=('chat_cheap',),
            usage_guards=kwargs['model_usage_guards']).build('chat_cheap', xing_binding=kwargs['v6_xing_binding'])
        original.quick_chain.generation_roles['chat_cheap'] = replace(cheap, gateway=gateway,
            xing_binding=kwargs['v6_xing_binding'])
        return original
    monkeypatch.setattr('eval_center.public_runner.build_container', build)
    retrieve = original.quick_chain.knowledge_gateway.retrieve_question
    def simulated_query(*args, **kwargs):
        claims = CurrentProviderSendClaims._bound()
        assert type(claims) is SQLiteProviderBatchSendClaims
        assert claims.run_id == contract.retrieval_run_id and claims.qid in qids
        assert RunLifecycle.assert_current()[:2] == (claim, lifecycle)
        request = current_free_request('query_embedding')
        assert request.run_id == claims.run_id and request.scope == scope
        assert request.batch_plan is shared.plan_for('miracl-zh', claims.qid)
        assert request.batch_id == shared.batch_id_for('miracl-zh', claims.qid)
        assert request.request_id == claims.request_id(purpose='query_embedding')
        other_qid = next(q for q in qids if q != claims.qid)
        wrong_id = stable_provider_request_id(run_id=claims.run_id, qid=other_qid,
            arm='SHARED_RETRIEVAL', purpose='query_embedding', call_ordinal=1)
        call_kwargs = dict(purpose='query_embedding', provider='siliconflow', model='BAAI/bge-m3',
            role='embedding', request_identity_sha256='e'*64)
        with pytest.raises(PairedContractError): claims(request_id=wrong_id, **call_kwargs)
        rejects.append('cross-qid')
        claims(request_id=request.request_id, **call_kwargs)
        claims.check_before_send(request_id=request.request_id)
        queries.append((claims.qid, request.request_id, claims.run_id))
        claims.settle(request_id=request.request_id, status='simulated', result=dict(
            result_sha256='f'*64, result_ref='sha256:'+'f'*64, item_count=1))
        with pytest.raises(PairedContractError): claims(request_id=request.request_id, **call_kwargs)
        rejects.append('duplicate')
        return retrieve(*args, **kwargs)
    monkeypatch.setattr(original.quick_chain.knowledge_gateway, 'retrieve_question', simulated_query)
    invoke = GenerationRole.invoke
    def observe_chat(self, env, **kwargs):
        claims = CurrentProviderSendClaims._bound()
        assert type(claims) is SQLiteProviderSendClaims and claims.run_id == contract.cohort_run_id
        assert claims.qid in qids and RunLifecycle.assert_current()[0].run_id == kwargs['run_id']
        with pytest.raises(ValueError): current_free_request('query_embedding')
        chats.append((claims.qid, claims.arm, claims.run_id))
        return invoke(self, env, **kwargs)
    monkeypatch.setattr(GenerationRole, 'invoke', observe_chat)
    profile = time_profile('owner-lease-small', native_execution_lease=lease)
    built = build_normal_paired_runtime(composition, source_manifest_path=SOURCE, index_manifest_path=index,
        cohort_path=cohort, run_id=rid, time_profile=profile, execution_contract=contract)
    assert construction == [scope]
    state = tmp_path/'setup-two-center.sqlite3'
    call = dict(cohort_path=cohort, source_path=SOURCE,
        data_root=Path(_parser().parse_args(['--phase', 'paired-run']).data_root), state_path=state, run_id=rid,
        runtime_factory=lambda _: built, index_manifest_path=index, time_profile=profile, execution_contract=contract)
    with lifecycle.executing(claim, 'SIMULATED-retrieval-session'):
        persistent.append(RunLifecycle.assert_current())
        report = run_public_paired_cohort(**call)
        assert report['status'] == 'SIMULATED_PAIRED_COHORT_COMPLETED', report.get('failures')
        assert report['qid_count'] == 2 and report['quickchain_invocations'] == 6
        assert borrowed == [claim.run_id]*2 and len(queries) == 2 and len(chats) == 6
        assert tuple(q[0] for q in queries) == qids and len({q[1] for q in queries}) == 2
        assert rejects == ['cross-qid', 'duplicate']*2
        saved_counts = (len(queries), len(chats), len(borrowed))
        resumed = run_public_paired_cohort(**call)
        assert resumed['reused_qids'] == 2 and resumed['new_quickchain_invocations'] == 0
        assert (len(queries), len(chats), len(borrowed)) == saved_counts
        assert events.count('start') == 1 and 'finish' not in events
    assert events.count('finish') == events.count('clear') == events.count('cleanup') == 1
    assert 'fail' not in events and current_execution.get() is None
    with sqlite3.connect(state) as db:
        native_queries = db.execute("SELECT qid,request_id,status FROM paired_provider_calls WHERE run_id=? AND purpose='query_embedding' ORDER BY qid", (claim.run_id,)).fetchall()
        assert len(native_queries) == 2 and {r[0] for r in native_queries} == set(qids)
        assert all(r[2] == 'simulated' for r in native_queries)
        assert db.execute("SELECT COUNT(*) FROM paired_provider_calls WHERE run_id=? AND purpose='answer'", (rid,)).fetchone()[0] == 6
        assert db.execute("SELECT COUNT(*) FROM paired_provider_calls").fetchone()[0] == 8
        assert db.execute("SELECT cohort_run_id,retrieval_run_id FROM paired_execution_contracts").fetchall() == [(rid, claim.run_id)]
    results = load_case_results(state, rid)
    assert len(results) == 2 and all(r['paired_input_hashes'] and r['observed_retrieval_calls'] == 1 for r in results)
    (tmp_path/'two-qid-evidence.json').write_text(json.dumps(dict(execution='SIMULATED',
        cohort_run_id=rid, retrieval_run_id=claim.run_id, qids=qids, queries=queries, chats=chats,
        rejection_checks=rejects, lifecycle_events=events, lifecycle_creations=1,
        query_claim_count=2, answer_claim_count=6, resume_new_calls=0), indent=2)+'\n', encoding='utf-8')
