"""SIMULATED SQL/provider, real offline parser/CAS/native claim contracts.

No live DB, service, network/model or project ledger. All artifacts stay in the
dedicated report test directory supplied with pytest --basetemp.
"""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from io import BytesIO
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from scripts import v6_batch_operator as op
from backend.tests.test_prepared_ingestion import fixture, mutate
from backend.app.adapters.models.cloud import EmbeddingAdmission, SimulatedEmbeddingCounter, SimulatedXingSend
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.application.validation_usd_budget import V6ExecutionLease
from backend.app.domain.version_source import VersionSource
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.prepared_ingestion import validate_prepared_input
from eval_center.paired_state import initialize_state, provider_send_claim_record, attempts_for_qid


class Result:
    rowcount = 1
    def __init__(self, value=None): self.value = value
    def mappings(self): return self
    def first(self): return self.value
    def one(self): return self.value
    def scalar(self): return self.value
    def __iter__(self): return iter(self.value or [])


class SimulatedSQL:
    """Records normal repository writes, without a database connection."""
    def __init__(self, source, profile):
        self.source, self.profile, self.calls = source, profile, []
        self.kb = str(uuid.UUID(int=3))
        self.version = dict(id=source.version_id, document_id=source.document_id,
            version_no=1, source_sha256=source.source_sha256, storage_key=source.storage_key,
            file_name=source.file_name, media_type=source.media_type, original_size=source.original_size,
            knowledge_base_id=self.kb)
        self.job_status, self.children = 'queued', []
    def connect(self): return nullcontext(self)
    def begin(self): return nullcontext(self)
    def execute(self, statement, params=None):
        sql = ' '.join(str(statement).split()); p = params or {}
        self.calls.append((sql, p))
        if sql.startswith('SELECT id,name,description,graph_enabled,cloud_allowed'):
            assert p['id'] == self.kb
            return Result(dict(id=self.kb, cloud_allowed=True, name='SIMULATED-public-KB'))
        if sql.startswith('SELECT v.*,d.active_version'):
            return Result(dict(self.version, active_version_id=self.source.version_id, job_status=self.job_status))
        if sql.startswith('SELECT v.*') or sql.startswith('SELECT dv.*'):
            return Result(dict(self.version))
        if sql.startswith('SELECT id::text,version_id::text'):
            return Result(dict(id='job', version_id=self.source.version_id, status=self.job_status, attempts=0))
        if sql.startswith('SELECT * FROM ingestion_jobs'):
            return Result(dict(version_id=self.source.version_id))
        if sql.startswith('SELECT id,version_id,status'):
            return Result(dict(id='job', version_id=self.source.version_id, status=self.job_status))
        if sql.startswith('SELECT 1 FROM ingestion_jobs'): return Result((1,))
        if sql.startswith('SELECT active_version_id'): return Result(None)
        if sql.startswith('SELECT c.id::text'): return Result(self.children)
        if sql.startswith('INSERT INTO chunks') and p['chunk_role'] == 'child':
            self.children.append(dict(id=str(p['id']), content=p['content'], content_sha256=p['sha256'],
                context_header=p['context_header'], source_locator=json.loads(p['locator']),
                dims=1024, fingerprint=self.profile, vector='[1,0]', terms=1))
        if sql.startswith("UPDATE document_versions SET index_status='ready'"):
            self.version.update(index_status='ready', parser_version=p['parser_version'], chunker_version=p['chunker_version'],
                index_identity=p['index_identity'], normalized_content_key=p['normalized_key'],
                normalized_content_sha256=p['normalized_sha'], processing_manifest_key=p['manifest_key'],
                processing_manifest_sha256=p['manifest_sha'])
        if sql.startswith('UPDATE ingestion_jobs') and "status='succeeded'" in sql:
            self.job_status = 'succeeded'
        return Result()


def setup(tmp_path, monkeypatch, *, prepared=True, large=False):
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-NOT-A-REAL-KEY')
    monkeypatch.setattr('backend.app.adapters.models.cloud._send', lambda *a, **k: pytest.fail('LIVE SEND FORBIDDEN'))
    storage, source, document, chunks, bundle = fixture(tmp_path)
    if large:
        original = ('source evidence ' * 5000).encode()
        stored = storage.put_stream(BytesIO(original))
        source = replace(source, source_sha256=stored.sha256, storage_key=stored.storage_key,
            original_size=stored.size)
        prepared = False
    original = storage.path_for(source.storage_key).read_bytes()
    descriptor = dict(unit_id='unit', input_kind='prepared_input' if prepared else 'original_sources',
        source=dict(source_document_id='public-source', file_name=source.file_name, media_type=source.media_type,
            artifact=dict(bytes=len(original), sha256=source.source_sha256)))
    loader = SimpleNamespace(load_original_source=lambda *a, **k: (descriptor, original),
        load_prepared_input=lambda *a, **k: (descriptor, original, bundle))
    admission = EmbeddingAdmission(SimulatedEmbeddingCounter(lambda text: 10),
        tokenizer_identity='SIMULATED-BGE-OPERATOR', non_truncating_verified=True)
    prototype = ProviderFactory(ModelRegistry.frozen_defaults(), embedding_admission=admission).build('embedding')
    engine = SimulatedSQL(source, prototype.identity.fingerprint)
    repo = PostgresKnowledgeRepository(engine, storage)
    events = []
    def upload(*args, **kwargs):
        events.append('create_upload')
        assert kwargs == {'duplicate_policy': 'skip'}
        assert args[:3] == (engine.kb, source.file_name, source.media_type)
        return dict(job_id='job', document_id=source.document_id, version_id=source.version_id)
    repo.create_upload = Mock(side_effect=upload)
    def claim(self, **kwargs):
        events.append('claim_job'); engine.job_status = 'running'
        return dict(id='job', claim_token='SIMULATED-FENCE')
    def renew(self, *args, **kwargs):
        events.append('renew_job'); return True
    monkeypatch.setattr(PostgresKnowledgeRepository, 'claim_job', claim)
    monkeypatch.setattr(PostgresKnowledgeRepository, 'renew_job', renew)
    monkeypatch.setattr(PostgresKnowledgeRepository, '_lease_heartbeat', lambda *a, **k: nullcontext())
    monkeypatch.setattr(PostgresKnowledgeRepository, '_get_or_create_embedding_profile', lambda *a, **k: 'SIMULATED-profile')
    bound = op.bind_package_job(store=repo, loader=loader, dataset_root=tmp_path, unit_id='unit',
        package_manifest_sha256='a'*64, kb_id=engine.kb, guard=lambda: events.append('guard'))
    now = datetime.now(timezone.utc)
    lease = V6ExecutionLease('SIMULATED-LEASE', (now-timedelta(seconds=1)).isoformat(),
        (now+timedelta(minutes=30)).isoformat(), None)
    plan = op.plan_bound_index(store=repo, bound=bound, prototype=prototype,
        run_id='SIMULATED-RUN', grant_id='owner-v6-public-rag-r3:SIMULATED-GRANT',
        source_manifest_sha256='b'*64, authority_manifest_sha256='c'*64, lease=lease,
        guard=lambda: None, execution_kind='SIMULATED')
    checkpoint = op.OperatorCheckpoint(tmp_path/'checkpoint.json', identity=dict(run_id='SIMULATED-RUN'), execution_kind='SIMULATED')
    checkpoint.begin_allocation('unit', dict(source_sha256=source.source_sha256))
    checkpoint.bind('unit', dict(kb_id=engine.kb, document_id=source.document_id, version_id=source.version_id,
        job_id='job', source_sha256=source.source_sha256, source_document_id='public-source'))
    native = tmp_path/'SIMULATED-native.sqlite'
    initialize_state(native)
    op.register_index_run(native_state=native, plans=[plan], cohort_sha256='d'*64,
        source_manifest_sha256='b'*64, code_sha='e'*40, guard=lambda: None)
    wires = []
    def send(request, timeout):
        payload = json.loads(request.data); wires.append(payload); events.append('SIMULATED-send')
        return dict(model=prototype.spec.model_id,
            data=[dict(index=i, embedding=[1.0]+[0.0]*1023) for i in range(len(payload['input']))],
            usage=dict(prompt_tokens=10*len(payload['input']), total_tokens=10*len(payload['input'])))
    return SimpleNamespace(repo=repo, source=source, engine=engine, bound=bound, plan=plan, checkpoint=checkpoint,
        native=native, prototype=prototype, events=events, wires=wires, gate=InMemoryBudgetGate(10),
        result_storage=storage, sender=SimulatedXingSend(send), bundle=bundle, loader=loader)


def execute(s):
    return op.execute_planned_index(checkpoint=s.checkpoint, key='unit', store=s.repo, plan=s.plan,
        native_state=s.native, budget_gate=s.gate, result_storage=s.result_storage,
        admission=s.prototype.admission, guard=lambda: None, worker_id='SIMULATED-worker', simulation_sender=s.sender)


@pytest.mark.parametrize('prepared,large', [(True, False), (False, True)])
def test_normal_cas_upload_claim_renew_process_and_exact_child_batches(tmp_path, monkeypatch, prepared, large):
    s = setup(tmp_path, monkeypatch, prepared=prepared, large=large)
    result = execute(s)
    assert result['status'] == 'READY'
    assert s.checkpoint.snapshot()['units']['unit']['status'] == 'READY'
    assert s.events.index('create_upload') < s.events.index('claim_job') < s.events.index('renew_job') < s.events.index('SIMULATED-send')
    assert [len(w['input']) for w in s.wires] == [c.item_count for c in s.plan.permit.calls]
    assert all(c.item_count <= 32 and c.document_version_ids == (s.source.version_id,)*c.item_count for c in s.plan.permit.calls)
    if large: assert len(s.wires) > 1
    assert [c.batch_id for c in s.plan.permit.calls] == [f'{s.source.version_id}:job:{i}:{len(s.wires)}' for i in range(len(s.wires))]
    assert {r['id'] for r in s.engine.children} == {i for c in s.plan.permit.calls for i in c.chunk_ids}
    assert s.engine.job_status == 'succeeded'
    assert attempts_for_qid(s.native, run_id=s.plan.permit.run_id, qid=s.plan.qid)['INDEXING']['status'] == 'simulated'
    assert all(provider_send_claim_record(s.native,request_id=c.request_id)['status']=='simulated' for c in s.plan.permit.calls)
    # Repeat is no-send, no extra reservation, with stable original IDs.
    count = len(s.wires); occupancy = dict(s.gate.reservations)
    assert execute(s)['status'] == 'REUSE_READY_NO_SEND'
    assert len(s.wires) == count and s.gate.reservations == occupancy


def test_prepared_formal_rebinding_and_original_quote(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch)
    normalized, chunks = validate_prepared_input(s.bundle, s.repo.storage, s.source, s.repo.chunking_config)
    assert normalized.document_id == s.bound.document_id and normalized.version_id == s.bound.version_id
    assert all(c.content == normalized.markdown_content[c.start:c.end] == c.source_locator.quote for c in chunks.children)
    broken = mutate(s.bundle, 'chunks', lambda v: v['children'][0]['source_locator'].update(quote='Gold answer'))
    from backend.app.domain.parsers import ParserError
    with pytest.raises(ParserError, match='QUOTE_INVALID'):
        validate_prepared_input(broken, s.repo.storage, s.source, s.repo.chunking_config)
    assert not s.wires


def test_unknown_is_durable_and_never_replayed_or_refunded(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch, large=True)
    sends = []
    def fail(*a): sends.append('send'); raise TimeoutError('SIMULATED-timeout')
    s.sender = SimulatedXingSend(fail)
    with pytest.raises(op.CheckpointBlocked): execute(s)
    claim = provider_send_claim_record(s.native, request_id=s.plan.permit.calls[0].request_id)
    assert claim['status'] == 'unknown' and len(sends) == 1
    assert [r['state'] for r in s.gate.reservations.values()] == ['unknown']
    restarted = op.OperatorCheckpoint(s.checkpoint.path, identity=s.checkpoint.identity, execution_kind='SIMULATED')
    s.checkpoint = restarted
    before = dict(s.gate.reservations)
    with pytest.raises(op.CheckpointBlocked, match='NO_REPLAY'): execute(s)
    assert len(sends) == 1 and s.gate.reservations == before
    assert restarted.snapshot()['units']['unit']['status'] == 'DISPATCHING'


def test_recover_after_ready_response_before_checkpoint_no_new_send(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch)
    real_ready = s.checkpoint.ready
    s.checkpoint.ready = Mock(side_effect=RuntimeError('SIMULATED-crash-after-native-success'))
    with pytest.raises(RuntimeError, match='crash'): execute(s)
    assert s.checkpoint.snapshot()['units']['unit']['status'] == 'DISPATCHING'
    row = s.checkpoint.snapshot()['units']['unit']
    reader = object.__new__(op.BatchOperator)
    reader.prototype, reader.result_storage,reader.checkpoint = s.prototype, s.result_storage,s.checkpoint
    binding = reader._recovery_binding(row)
    assert binding.authorized_transport is None
    claims = [provider_send_claim_record(s.native, request_id=c.request_id) for c in s.plan.permit.calls]
    def validate():
        for call, claim in zip(s.plan.permit.calls, claims):
            op.read_bound_native_result(binding,claim,call)
    receipt = op.observe_ready_index(store=s.repo, bound=s.bound,
        expected_child_ids=tuple(i for c in s.plan.permit.calls for i in c.chunk_ids),
        expected_profile=s.prototype.identity.fingerprint, expected_manifest=s.plan.expected_index,execution_kind='SIMULATED')
    s.checkpoint.ready = real_ready
    s.checkpoint.recover_succeeded('unit', native_claims=claims, receipt=receipt, validate_saved_results=validate)
    assert s.checkpoint.snapshot()['units']['unit']['status'] == 'READY'
    assert execute(s)['status'] == 'REUSE_READY_NO_SEND' and len(s.wires) == 1


@pytest.mark.parametrize('drift', ['quote', 'child', 'profile', 'normalized'])
def test_ready_readback_rejects_expected_actual_drift(tmp_path, monkeypatch, drift):
    s = setup(tmp_path, monkeypatch); execute(s)
    if drift == 'quote': s.engine.children[0]['source_locator']['quote'] = 'unfounded'
    if drift == 'child': s.engine.children.pop()
    if drift == 'profile': s.engine.children[0]['fingerprint'] = 'other-profile'
    if drift == 'normalized': s.engine.version['normalized_content_sha256'] = '0'*64
    with pytest.raises(op.CheckpointBlocked):
        op.observe_ready_index(store=s.repo, bound=s.bound,
            expected_child_ids=tuple(i for c in s.plan.permit.calls for i in c.chunk_ids),
            expected_profile=s.prototype.identity.fingerprint, expected_manifest=s.plan.expected_index,execution_kind='SIMULATED')


def test_atomic_t0_setup_resume_deadline_and_no_reset(tmp_path):
    cp = op.OperatorCheckpoint(tmp_path/'cp.json', identity=dict(run_id='SIMULATED'), execution_kind='SIMULATED')
    now = datetime.now(timezone.utc)
    assert cp.start_batch(phase='SETUP', now=now, approved_batch_id=None, guard=lambda: None) is None
    assert cp.snapshot()['batch'] is None
    batch = cp.start_batch(phase='BATCH', now=now, approved_batch_id='owner-review-1', guard=lambda: None)
    original = cp.path.read_bytes()
    assert op.utc(batch['deadline_utc'])-op.utc(batch['T0_utc']) == timedelta(hours=5)
    assert cp.start_batch(phase='BATCH', now=now+timedelta(hours=1), approved_batch_id='owner-review-1', guard=lambda: None) == batch
    for at, identity in [(now+timedelta(hours=5), 'owner-review-1'), (now, 'different')]:
        with pytest.raises(op.CheckpointBlocked): cp.start_batch(phase='BATCH', now=at, approved_batch_id=identity, guard=lambda: None)
    assert cp.snapshot()['batch'] == batch
    cp.begin_allocation('uncertain', dict(source_sha256='a'*64))
    with pytest.raises(op.CheckpointBlocked, match='ALLOCATION_UNKNOWN'):
        cp.begin_allocation('uncertain', dict(source_sha256='a'*64))
    assert not list(tmp_path.glob('*.pending-*')) and json.loads(original)['batch'] == batch


def test_full_execution_writer_lock(tmp_path):
    path = tmp_path/'writer.lock'
    with op.execution_lock(path):
        with pytest.raises(op.CheckpointBlocked, match='SINGLE_WRITER_BUSY'):
            with op.execution_lock(path): pytest.fail('second writer entered')
    with op.execution_lock(path): pass


def test_local_real_execution_is_fenced(tmp_path):
    if op.os.name != 'nt': pytest.skip('Windows local fence')
    instance = object.__new__(op.BatchOperator)
    instance.external_guard = Mock(side_effect=AssertionError('must not run'))
    with pytest.raises(op.CheckpointBlocked, match='LOCAL_FENCED'): instance.guard()
    instance.external_guard.assert_not_called()


def test_deadline_stops_before_operation(tmp_path, monkeypatch):
    instance = object.__new__(op.BatchOperator)
    now = datetime.now(timezone.utc)
    instance.checkpoint = op.OperatorCheckpoint(tmp_path/'cp.json', identity=dict(run_id='SIMULATED'), execution_kind='SIMULATED')
    instance.checkpoint.start_batch(phase='BATCH', now=now-timedelta(hours=5), approved_batch_id='owner', guard=lambda: None)
    instance.clock = lambda: now
    instance.external_guard = lambda: None
    instance.lease = V6ExecutionLease('SIMULATED', (now-timedelta(seconds=1)).isoformat(), (now+timedelta(minutes=10)).isoformat(), None)
    # Execute only the portable deadline portion; local fence remains mandatory.
    batch = instance.checkpoint.snapshot()['batch']
    assert now >= op.utc(batch['deadline_utc'])
    with pytest.raises(op.CheckpointBlocked, match='DEADLINE'):
        instance.checkpoint.start_batch(phase='BATCH', now=now, approved_batch_id='owner', guard=lambda: None)
    # Test production guard without changing any filesystem path. A mocked
    # already-read snapshot lets us exercise its portable clock branch on Windows.
    instance.checkpoint = SimpleNamespace(snapshot=lambda: dict(batch=batch))
    monkeypatch.setattr(op, 'os', SimpleNamespace(name='posix'))
    with pytest.raises(op.CheckpointBlocked, match='DEADLINE'): instance.guard()


def test_recovery_unknown_and_failed_cas_validation_preserve_dispatch(tmp_path):
    cp = op.OperatorCheckpoint(tmp_path/'cp.json', identity=dict(run_id='SIMULATED'), execution_kind='SIMULATED')
    pins = dict(source_sha256='a'*64)
    formal = dict(kb_id='kb', document_id='doc', version_id='v', job_id='job',
        source_sha256='a'*64, source_document_id='source')
    cp.begin_allocation('unit', pins); cp.bind('unit', formal)
    cp.freeze_plan('unit', dict(request_id='original-request'))
    cp.begin_dispatch('unit', native_claims=[None])
    verify = Mock(side_effect=ValueError('SIMULATED-CAS-corruption'))
    receipt = dict(status='READY',execution_kind='SIMULATED',identity_sha256='b'*64)
    with pytest.raises(op.CheckpointBlocked, match='NO_REPLAY'):
        cp.recover_succeeded('unit', native_claims=[dict(status='unknown')], receipt=receipt, validate_saved_results=verify)
    verify.assert_not_called()
    with pytest.raises(ValueError, match='corruption'):
        cp.recover_succeeded('unit', native_claims=[dict(status='simulated')], receipt=receipt, validate_saved_results=verify)
    assert cp.snapshot()['units']['unit']['status'] == 'DISPATCHING'
    with pytest.raises(op.CheckpointBlocked, match='FROZEN_PLAN_DRIFT'):
        cp.freeze_plan('unit', dict(request_id='replacement-request'))
    cp.recover_succeeded('unit', native_claims=[dict(status='simulated')], receipt=receipt, validate_saved_results=lambda: None)
    assert cp.begin_dispatch('unit', native_claims=[dict(status='simulated')]) is False


def test_simulated_execution_cannot_fall_through_to_real_sender(tmp_path, monkeypatch):
    s = setup(tmp_path, monkeypatch)
    s.sender = None
    with pytest.raises(op.CheckpointBlocked, match='SIMULATED_SENDER_REQUIRED'): execute(s)
    assert not s.wires and not s.gate.reservations


def test_full_source_inventory_is_frozen_and_partial_refused(tmp_path):
    cp = op.OperatorCheckpoint(tmp_path/'cp.json', identity=dict(run_id='SIMULATED'), execution_kind='SIMULATED')
    unit = op.SourceUnit('SIMULATED-dataset', str(tmp_path), 'unit', 'a'*64, 'source', 'b'*64,
        'original_sources', 'kb', 'original.txt', 'text/plain')
    formal = dict(kb_id='kb', document_id='doc', version_id='v', job_id='job', source_sha256='b'*64, source_document_id='source')
    package_identity=op.FrozenPackageIdentity('a'*64,'b'*64,tuple((d,'c'*64) for d in op.DATASETS),'SIMULATED-review','d'*64)
    cp.begin_allocation(unit.key, unit.pins()); cp.bind(unit.key, formal)
    with pytest.raises(op.CheckpointBlocked, match='PARTIAL'):
        op.freeze_ready_inventory(checkpoint=cp, units=[unit], path=tmp_path/'inventory.json',package_identity=package_identity,guard=lambda: None)
    cp.freeze_plan(unit.key, dict(request_id='original'))
    cp.ready(unit.key, dict(status='READY',execution_kind='SIMULATED',identity_sha256='c'*64))
    result = op.freeze_ready_inventory(checkpoint=cp, units=[unit], path=tmp_path/'inventory.json',package_identity=package_identity,guard=lambda: None)
    assert result['execution_kind'] == 'SIMULATED' and result['entries'][0]['formal'] == formal
    assert op.freeze_ready_inventory(checkpoint=cp, units=[unit], path=tmp_path/'inventory.json',package_identity=package_identity,guard=lambda: None) == result
    with pytest.raises(op.CheckpointBlocked, match='UNIT_SET'):
        op.freeze_ready_inventory(checkpoint=cp, units=[], path=tmp_path/'inventory.json',package_identity=package_identity,guard=lambda: None)


@pytest.mark.parametrize('kind,statuses', [('REAL',['simulated']),('REAL',['succeeded','simulated']),
    ('SIMULATED',['succeeded']),('SIMULATED',['simulated','unknown']),('REAL',['unknown'])])
def test_native_execution_kinds_mixed_unknown_and_real_pseudo_simulated_rejected(tmp_path,kind,statuses):
    cp=op.OperatorCheckpoint(tmp_path/'cp.json',identity=dict(run_id='SIMULATED-no-provider'),execution_kind=kind)
    cp.begin_allocation('unit',dict(source_sha256='a'*64))
    cp.bind('unit',dict(kb_id='kb',document_id='doc',version_id='v',job_id='j',source_document_id='s',source_sha256='a'*64))
    cp.freeze_plan('unit',dict(request_id='original'))
    cp.begin_dispatch('unit',native_claims=[None])
    verify=Mock(side_effect=AssertionError('must not read/send before exact receipt kind'))
    with pytest.raises(op.CheckpointBlocked,match='NO_REPLAY'):
        cp.recover_succeeded('unit',native_claims=[dict(status=s) for s in statuses],
            receipt=dict(status='READY',execution_kind=kind,identity_sha256='b'*64),validate_saved_results=verify)
    verify.assert_not_called()
    assert cp.snapshot()['units']['unit']['status']=='DISPATCHING'


def test_checkpoint_permit_kind_mismatch_stops_before_native_dispatch(tmp_path,monkeypatch):
    s=setup(tmp_path,monkeypatch)
    s.checkpoint.kind='REAL'
    with pytest.raises(op.CheckpointBlocked,match='EXECUTION_KIND_MISMATCH'):execute(s)
    assert not s.wires and not s.gate.reservations


def test_reviewed_manifest_pin_is_independent_and_matches_actual_package(tmp_path):
    package=tmp_path/'SIMULATED-package';package.mkdir()
    loader=b'# SIMULATED loader not imported\n'
    (package/'package_loader.py').write_bytes(loader)
    pins={d:hashlib.sha256(d.encode()).hexdigest() for d in op.DATASETS}
    index=dict(schema='rag-v6-final-six-pack-index/v1',packages=[dict(dataset=d,units=count,input_kind=kind,
        manifest=dict(sha256=pins[d])) for d,(count,kind) in op.DATASETS.items()])
    raw=op.canonical(index);(package/'six-package-manifest.json').write_bytes(raw)
    review=dict(schema='v6-reviewed-package-identity/v1',review_id='SIMULATED-independent-owner-review',
        manifest_sha256=hashlib.sha256(raw).hexdigest(),loader_sha256=hashlib.sha256(loader).hexdigest(),dataset_manifest_sha256=pins)
    review_raw=op.canonical(review);review_path=tmp_path/'review.json';review_path.write_bytes(review_raw)
    identity=op.load_reviewed_package_identity(review_path=review_path,expected_review_sha256=hashlib.sha256(review_raw).hexdigest())
    assert op.validate_frozen_package(package_root=package,package_identity=identity)==index
    with pytest.raises(op.CheckpointBlocked,match='REVIEW_RECORD_DRIFT'):
        op.load_reviewed_package_identity(review_path=review_path,expected_review_sha256='0'*64)
    # A new internally consistent package cannot replace independently frozen pin.
    index['packages'][0]['manifest']['sha256']='0'*64
    new_raw=op.canonical(index);(package/'six-package-manifest.json').write_bytes(new_raw)
    with pytest.raises(op.CheckpointBlocked,match='MANIFEST_DRIFT'):
        op.validate_frozen_package(package_root=package,package_identity=identity)
    with pytest.raises(op.CheckpointBlocked,match='DATASET_PIN_DRIFT'):
        op.validate_frozen_package(package_root=package,package_identity=replace(identity,manifest_sha256=hashlib.sha256(new_raw).hexdigest()))
