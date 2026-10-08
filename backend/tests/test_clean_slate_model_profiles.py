"""REAL isolated PostgreSQL / pgvector, SIMULATED model.

Normal fixtures rollback. Explicit Owner-authorized concurrency cases alone
commit uniquely marked synthetic profiles and precisely clean those rows.
Never connect to the Settings old DB.
"""
from contextlib import contextmanager, nullcontext
from io import BytesIO
import json
import os
from pathlib import Path
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, text

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.domain.scope import Scope
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.embedding_identity import effective_identity
from backend.tests.test_model_provider_contracts import embedding, SimulatedGuard
from scripts.with_clean_slate import resolve_environment, verify_database


@pytest.fixture
def isolated_database(monkeypatch):
    if os.getenv("RAG_R3_CLEAN_SLATE_TEST") != "1":
        pytest.skip("Explicit Clean-slate opt-in absent; real DB NOT RUN")
    profile = json.loads(Path("deploy/clean-slate/profile.json").read_text(encoding="utf-8"))
    assert profile["database"]["name"] == "rag_clean_dev_20261008t072656z_352f705b"
    env = resolve_environment(profile, os.environ.copy())
    assert verify_database(profile, env)["status"] == "PASS"
    engine = create_engine(env["RAG_DATABASE_URL"], hide_parameters=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'")).scalars().all()
        before = {table: connection.execute(text('SELECT count(*) FROM "'+table+'"')).scalar_one() for table in tables}
        class TransactionEngine:
            @contextmanager
            def connect(self):
                yield connection

            @contextmanager
            def begin(self):
                with connection.begin_nested():
                    yield connection

        monkeypatch.setattr(PostgresKnowledgeRepository, "_lease_heartbeat", lambda *a, **k: nullcontext())
        try:
            yield TransactionEngine(), connection
        finally:
            transaction.rollback()
        after = {table: connection.execute(text('SELECT count(*) FROM "'+table+'"')).scalar_one() for table in tables}
        assert after == before
    engine.dispose()


def test_existing_postgres_parser_strategy_in_rollback(isolated_database, monkeypatch, tmp_path):
    from backend.tests import test_postgres_chunk_strategy as old
    monkeypatch.setattr(old, "create_engine", lambda *a, **k: isolated_database[0])
    old.test_postgres_records_actual_parser_chunker_and_strategy(tmp_path)


def test_existing_exact_profile_contract_in_rollback(isolated_database, monkeypatch, tmp_path):
    from backend.tests import test_postgres_scope_boundaries as old
    monkeypatch.setattr(old, "create_engine", lambda *a, **k: isolated_database[0])
    old.test_postgres_vector_candidates_require_the_exact_profile(tmp_path)


@pytest.mark.parametrize("allowed", [True, False])
def test_siliconflow_identity_full_input_child_only_pgvector(isolated_database, monkeypatch, tmp_path, allowed):
    engine, connection = isolated_database
    registry, guard = ModelRegistry.frozen_defaults(), SimulatedGuard()
    adapter, calls = embedding(registry, guard)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "SIMULATED-NOT-A-KEY")
    def simulated(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload)
        return dict(model="BAAI/bge-m3", data=[dict(index=i, embedding=[1.0]+[0.0]*1023)
                    for i in range(len(payload["input"]))])
    adapter.transport = simulated
    repository = PostgresKnowledgeRepository(engine, ContentAddressedStorage(tmp_path), embedding_provider=adapter)
    kb = repository.create_knowledge_base("r3-synthetic-"+uuid.uuid4().hex, cloud_allowed=allowed)
    source = ("# Root\n# A\n" + "synthetic number -7.25. "*300 + "\n# B\nend").encode()
    stored = repository.storage.put_stream(BytesIO(source))
    receipt = repository.create_upload(kb["id"], "synthetic.md", "text/markdown", stored)
    job = repository.process_job(receipt["job_id"])
    if not allowed:
        assert job["status"] == "failed" and calls == []
        assert repository.get_document(receipt["document_id"])["active_version_id"] is None
        return
    assert job["status"] == "succeeded"
    rows = connection.execute(text("SELECT c.id,c.content,c.context_header,c.chunk_role,c.locator,c.parent_id,ce.profile_id FROM chunks c LEFT JOIN chunk_embeddings ce ON ce.chunk_id=c.id WHERE c.version_id=:v ORDER BY c.chunk_index"), {"v": receipt["version_id"]}).mappings().all()
    parents = [r for r in rows if r["chunk_role"] == "parent"]
    children = [r for r in rows if r["chunk_role"] == "child"]
    assert parents and children and all(r["profile_id"] is None for r in parents)
    assert all(r["profile_id"] is not None for r in children)
    # Frozen P3 emits a parent only when subdivision needs one; a complete
    # standalone child retains NULL parent_id. Every actual parent link is local.
    parent_ids = {r["id"] for r in parents}
    assert any(r["parent_id"] in parent_ids for r in children)
    assert all(r["parent_id"] is None or r["parent_id"] in parent_ids for r in children)
    assert calls[0]["input"] == [r["context_header"]+"\n\n"+r["content"].strip() for r in children]
    assert all(r["locator"]["quote"] == r["content"] for r in children)
    profile_id = repository.get_embedding_profile_id("BAAI/bge-m3", 1024)
    profile = connection.execute(text("SELECT * FROM embedding_profiles WHERE id=:id"), {"id": profile_id}).mappings().one()
    assert profile["provider"] == "siliconflow" and profile["model_revision"] == "UNKNOWN"
    assert profile["fingerprint"] == repository.embedding_identity().fingerprint
    hits = repository.vector_candidates(Scope.from_ids([kb["id"]]), [1.0]+[0.0]*1023, 100, profile_id=profile_id)
    assert {h.chunk_id for h in hits} == {str(r["id"]) for r in children}
    assert str(repository.get_document(receipt["document_id"])["active_version_id"]) == receipt["version_id"]
    assert not repository.vector_candidates(Scope.from_ids([str(uuid.uuid4())]), [1.0]+[0.0]*1023, 100, profile_id=profile_id)


def test_distinct_input_semantics_profiles_can_coexist(isolated_database):
    """Original RED regression: full effective identities coexist after 0017.

    Same truthful UNKNOWN model revision, distinct full effective identities.
    Do not disguise input semantics/index generation as a provider revision.
    """
    _, connection = isolated_database
    from backend.app.domain.adaptive_chunking import ChunkingConfig
    original = effective_identity(None, ChunkingConfig().identity)
    identities = [original, replace(original, embedding_input_semantics_version="context-header-child/v2")]
    assert identities[0].fingerprint != identities[1].fingerprint
    for identity in identities:
        connection.execute(text("""INSERT INTO embedding_profiles
            (id,provider,model_name,model_revision,dimension,distance,fingerprint)
            VALUES (:id,:provider,:model,:revision,:dimension,:distance,:fingerprint)"""), dict(
                id=uuid.uuid4(), provider=identity.provider, model=identity.model_id,
                revision=identity.resolved_revision_or_unknown, dimension=identity.dimension,
                distance=identity.distance_metric, fingerprint=identity.fingerprint))
    assert connection.execute(text("SELECT count(*) FROM embedding_profiles WHERE fingerprint IN (:a,:b)"),
        dict(a=identities[0].fingerprint,b=identities[1].fingerprint)).scalar_one() == 2


def _identity():
    from backend.app.domain.adaptive_chunking import ChunkingConfig
    return effective_identity(None, ChunkingConfig().identity)


@pytest.mark.parametrize("change", [
    {}, {"embedding_input_semantics_version":"context-header-child/v2"},
    {"chunking_index_identity":"p3:synthetic-other-identity"},
    {"provider":"siliconflow"}, {"model_id":"synthetic-other-model"},
])
def test_atomic_full_identity_reuse_and_isolation(isolated_database, change):
    _, c = isolated_database
    original = _identity()
    get = PostgresKnowledgeRepository._get_or_create_embedding_profile
    first = get(c,original)
    assert get(c,original) == first
    changed = replace(original,**change)
    second = get(c,changed)
    assert (first == second) == (not change)
    assert get(c,changed) == second
    rows = c.execute(text('SELECT id,model_revision,fingerprint FROM embedding_profiles')).mappings().all()
    assert len(rows) == (2 if change else 1)
    assert all(r['model_revision']=='UNKNOWN' for r in rows)
    assert {r['fingerprint'] for r in rows} == {original.fingerprint,changed.fingerprint}


@pytest.mark.parametrize('change', [
    {'provider':''}, {'model_id':'  '}, {'resolved_revision_or_unknown':''},
    {'dimension':0}, {'dimension':True}, {'distance_metric':'unsupported'},
    {'chunking_index_identity':''}, {'embedding_input_semantics_version':''},
])
def test_incomplete_identity_rejected_without_insert(isolated_database,change):
    _, c = isolated_database
    with pytest.raises(ValueError,match='EMBEDDING_PROFILE_IDENTITY_INVALID'):
        PostgresKnowledgeRepository._get_or_create_embedding_profile(c,replace(_identity(),**change))
    assert c.execute(text('SELECT count(*) FROM embedding_profiles')).scalar_one() == 0


def test_non_read_committed_isolation_rejected(isolated_database):
    _, c = isolated_database
    # A separate real connection with its own transaction, never the proxy.
    with c.engine.connect().execution_options(isolation_level='REPEATABLE READ') as other:
        with other.begin():
            with pytest.raises(RuntimeError,match='REQUIRES_READ_COMMITTED'):
                PostgresKnowledgeRepository._get_or_create_embedding_profile(other,_identity())
            assert other.execute(text('SELECT count(*) FROM embedding_profiles')).scalar_one() == 0


def test_unexpected_primary_key_conflict_fails_without_retry_or_mutation(isolated_database,monkeypatch):
    from sqlalchemy.exc import IntegrityError
    _, c = isolated_database
    original = _identity()
    get = PostgresKnowledgeRepository._get_or_create_embedding_profile
    first = get(c,original)
    monkeypatch.setattr(uuid,'uuid4',lambda:first)
    with pytest.raises(IntegrityError,match='embedding_profiles_pkey'):
        with c.begin_nested():
            get(c,replace(original,embedding_input_semantics_version='synthetic-other'))
    assert c.execute(text('SELECT id,fingerprint FROM embedding_profiles')).one() == (first,original.fingerprint)


def test_migration_preserves_existing_profile_id_embedding_fk_and_vectors(isolated_database,monkeypatch,tmp_path):
    """REAL SQL fixture populated in outer rollback; exercise 0017 with existing FK."""
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    _, c = isolated_database
    test_siliconflow_identity_full_input_child_only_pgvector(isolated_database,monkeypatch,tmp_path,True)
    profiles_before = c.execute(text('SELECT * FROM embedding_profiles ORDER BY id')).mappings().all()
    embeddings_before = c.execute(text('SELECT chunk_id,profile_id,embedding::text FROM chunk_embeddings ORDER BY chunk_id,profile_id')).all()
    operations = Operations(MigrationContext.configure(c))
    model_columns = ['provider','model_name','model_revision','dimension','distance']
    operations.drop_constraint('embedding_profiles_identity_ux','embedding_profiles',type_='unique')
    operations.create_unique_constraint('embedding_profiles_identity_ux','embedding_profiles',model_columns)
    spec = importlib.util.spec_from_file_location('migration0017',Path('alembic/versions/0017_embedding_profiles_fingerprint_identity.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration,'op',operations)
    migration.upgrade()
    assert c.execute(text('SELECT * FROM embedding_profiles ORDER BY id')).mappings().all() == profiles_before
    assert c.execute(text('SELECT chunk_id,profile_id,embedding::text FROM chunk_embeddings ORDER BY chunk_id,profile_id')).all() == embeddings_before
    assert c.execute(text("SELECT convalidated FROM pg_constraint WHERE conname='chunk_embeddings_profile_fk'")).scalar_one()
    with pytest.raises(RuntimeError,match='UNEXPECTED_UNIQUE_CONSTRAINT'):
        migration.upgrade()
    assert c.execute(text('SELECT * FROM embedding_profiles ORDER BY id')).mappings().all() == profiles_before


def test_wrong_provider_model_or_chunk_identity_cannot_read_other_vectors(isolated_database,monkeypatch,tmp_path):
    engine, c = isolated_database
    test_siliconflow_identity_full_input_child_only_pgvector(isolated_database,monkeypatch,tmp_path,True)
    registry, guard = ModelRegistry.frozen_defaults(), SimulatedGuard()
    adapter, _ = embedding(registry,guard)
    repository = PostgresKnowledgeRepository(engine,ContentAddressedStorage(tmp_path),embedding_provider=adapter)
    identity = repository.embedding_identity()
    profile_id = repository.get_embedding_profile_id(identity.model_id,1024)
    kb_id = str(c.execute(text('SELECT id FROM knowledge_bases')).scalar_one())
    scope = Scope.from_ids([kb_id])
    vector = [1.0]+[0.0]*1023
    assert repository.vector_candidates(scope,vector,100,profile_id=profile_id)
    for changed in [replace(identity,provider='ollama'), replace(identity,model_id='other-model'),
                    replace(identity,chunking_index_identity='p3:other'),
                    replace(identity,embedding_input_semantics_version='context-header-child/v2')]:
        wrong_id = repository._get_or_create_embedding_profile(c,changed)
        assert not repository.vector_candidates(scope,vector,100,profile_id=str(wrong_id))
        monkeypatch.setattr(repository,'embedding_identity',lambda identity=changed:identity)
        assert not repository.vector_candidates(scope,vector,100,profile_id=profile_id)
        monkeypatch.setattr(repository,'embedding_identity',lambda:identity)
    assert repository.get_embedding_profile_id('other-model',1024) is None


@pytest.fixture
def independent_engine():
    """Each thread owns a real connection; no shared-connection/savepoint proxy."""
    if os.getenv('RAG_R3_CLEAN_SLATE_TEST') != '1':
        pytest.skip('Explicit Clean-slate opt-in absent; real DB NOT RUN')
    profile = json.loads(Path('deploy/clean-slate/profile.json').read_text())
    env = resolve_environment(profile,os.environ.copy())
    assert verify_database(profile,env)['status']=='PASS'
    engine = create_engine(env['RAG_DATABASE_URL'],hide_parameters=True,connect_args={
        'application_name':'r3-fix-concurrency','options':'-c statement_timeout=10000 -c lock_timeout=5000'})
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM embedding_profiles')).scalar_one()==0
    try:
        yield engine
    finally:
        with engine.connect() as c:
            assert c.execute(text('SELECT count(*) FROM embedding_profiles')).scalar_one()==0
        engine.dispose()


def test_same_identity_real_concurrent_conflict_wait_and_rollback_handoff(independent_engine):
    """Aborted winner is not reusable. Committed-winner reuse needs Owner decision."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    get = PostgresKnowledgeRepository._get_or_create_embedding_profile
    started = Event()
    pids = []
    def contender():
        with independent_engine.connect() as c:
            transaction = c.begin()
            try:
                pids.append(c.execute(text('SELECT pg_backend_pid()')).scalar_one())
                started.set()
                result = get(c,_identity())
                assert get(c,_identity()) == result
                assert c.execute(text('SELECT count(*) FROM embedding_profiles')).scalar_one()==1
                return result
            finally:
                transaction.rollback()
    with independent_engine.connect() as leader, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = leader.begin()
        try:
            leader_pid = leader.execute(text('SELECT pg_backend_pid()')).scalar_one()
            first = get(leader,_identity())
            pending = pool.submit(contender)
            assert started.wait(3)
            deadline = time.monotonic()+3
            blocked = False
            while time.monotonic()<deadline:
                blocked = leader_pid in leader.execute(text('SELECT pg_blocking_pids(:pid)'),{'pid':pids[0]}).scalar_one()
                if blocked:
                    break
                time.sleep(.01)
            assert blocked and not pending.done() and pids[0]!=leader_pid
            assert leader.execute(text('SELECT count(*) FROM embedding_profiles')).scalar_one()==1
        finally:
            transaction.rollback()
        assert pending.result(timeout=5) != first


def test_different_identities_real_concurrent_independent_transactions(independent_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2,timeout=5)
    def create(identity):
        with independent_engine.connect() as c:
            transaction = c.begin()
            try:
                pid = c.execute(text('SELECT pg_backend_pid()')).scalar_one()
                barrier.wait()
                profile_id = PostgresKnowledgeRepository._get_or_create_embedding_profile(c,identity)
                # Both INSERTs must complete while both transactions remain open.
                barrier.wait()
                assert PostgresKnowledgeRepository._get_or_create_embedding_profile(c,identity)==profile_id
                return pid,profile_id
            finally:
                transaction.rollback()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(create,_identity())
        b = pool.submit(create,replace(_identity(),embedding_input_semantics_version='context-header-child/v2'))
        left,right = a.result(timeout=10),b.result(timeout=10)
    assert left[0]!=right[0] and left[1]!=right[1]


@pytest.mark.parametrize('different',[False,True])
def test_owner_authorized_committed_concurrent_profiles_and_exact_cleanup(independent_engine,different):
    """REAL committed-winner visibility; narrowly authorized synthetic rows only."""
    if os.getenv('RAG_R3_SYNTHETIC_COMMIT_CLEANUP') != '1':
        pytest.skip('Owner-authorized synthetic commit/cleanup opt-in absent')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event
    import time
    identities = [replace(_identity(),model_id='SIMULATED-R3-FIX-'+uuid.uuid4().hex)]
    identities.append(replace(identities[0],embedding_input_semantics_version='context-header-child/v2') if different else identities[0])
    model = identities[0].model_id
    get = PostgresKnowledgeRepository._get_or_create_embedding_profile
    record = dict(model=model,different=different,identities=[i.fingerprint for i in identities],
                  evidence='REAL_POSTGRESQL_INDEPENDENT_CONNECTIONS',cleanup_rows=[])
    try:
        if different:
            barrier = Barrier(2,timeout=5)
            def create(identity):
                with independent_engine.connect() as c:
                    with c.begin():
                        pid = c.execute(text('SELECT pg_backend_pid()')).scalar_one()
                        barrier.wait()
                        result = get(c,identity)
                        barrier.wait()  # both rows inserted before either commits
                    return pid,result
            with ThreadPoolExecutor(max_workers=2) as pool:
                a,b = [pool.submit(create,i) for i in identities]
                left,right = a.result(timeout=10),b.result(timeout=10)
            assert left[0]!=right[0] and left[1]!=right[1]
        else:
            started = Event()
            contender_pid = []
            def contend():
                with independent_engine.connect() as c:
                    with c.begin():
                        pid = c.execute(text('SELECT pg_backend_pid()')).scalar_one()
                        contender_pid.append(pid)
                        started.set()
                        result = get(c,identities[1])
                    return pid,result
            # Executor exits only after the leader transaction has finished,
            # including assertion failure, so a failed assertion cannot deadlock.
            with ThreadPoolExecutor(max_workers=1) as pool:
                with independent_engine.connect() as leader:
                    with leader.begin():
                        left = leader.execute(text('SELECT pg_backend_pid()')).scalar_one(),get(leader,identities[0])
                        pending = pool.submit(contend)
                        assert started.wait(3)
                        deadline = time.monotonic()+3
                        blocked = False
                        while time.monotonic()<deadline:
                            blocked = left[0] in leader.execute(text('SELECT pg_blocking_pids(:pid)'),{'pid':contender_pid[0]}).scalar_one()
                            if blocked:
                                break
                            time.sleep(.01)
                        assert blocked and not pending.done()
                        record['conflict_wait_observed']=True
                    # leader commits here; contender SELECT gets a new snapshot.
                right = pending.result(timeout=5)
            assert left[0]!=right[0] and left[1]==right[1]
        with independent_engine.connect() as c:
            rows = c.execute(text('SELECT id,fingerprint FROM embedding_profiles WHERE model_name=:model'),dict(model=model)).mappings().all()
            assert len(rows)==(2 if different else 1)
            assert {r['id'] for r in rows} == {left[1],right[1]}
            record.update(backend_pids=[left[0],right[0]],profile_ids=[str(left[1]),str(right[1])],
                          committed_count=len(rows),result='PASS')
    finally:
        with independent_engine.begin() as c:
            # SELECT exact test model + validate every column before deletion.
            rows = c.execute(text('SELECT * FROM embedding_profiles WHERE model_name=:model'),dict(model=model)).mappings().all()
            for row in rows:
                match = next((i for i in identities if i.fingerprint==row['fingerprint']),None)
                assert match is not None
                params = dict(id=row['id'],model=model,provider=match.provider,
                    revision=match.resolved_revision_or_unknown,dimension=match.dimension,
                    distance=match.distance_metric,fingerprint=match.fingerprint)
                removed = c.execute(text('''DELETE FROM embedding_profiles WHERE id=:id AND model_name=:model
                    AND provider=:provider AND model_revision=:revision AND dimension=:dimension
                    AND distance=:distance AND fingerprint=:fingerprint RETURNING id'''),params).scalar_one()
                record['cleanup_rows'].append(str(removed))
        with independent_engine.connect() as c:
            assert c.execute(text('SELECT count(*) FROM embedding_profiles WHERE model_name=:model'),dict(model=model)).scalar_one()==0
        record['after_count']=0
        evidence = Path('var/reports/p4-pre-r3-fix')
        evidence.mkdir(parents=True,exist_ok=True)
        (evidence/('committed-concurrency-'+('distinct' if different else 'same')+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
