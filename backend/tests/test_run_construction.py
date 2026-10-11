"""SIMULATED PG/Redis operations on normal construction objects; no connections."""
from contextlib import contextmanager
from dataclasses import replace
import uuid
import pytest
from sqlalchemy import create_engine

from backend.app.application.run_construction import (
    PostgresRunTarget, ApprovedRunStreamEndpoint, ProductRunRequest, build_normal_run_construction,
)
from backend.app.adapters.postgres.run_lifecycle import PostgresRunLifecycle
from backend.app.adapters.redis_stream import RedisStreamManager
from backend.app.application.run_lifecycle import RunLifecycle
from backend.app.ports.run_lifecycle import RunClaim, LifecycleDenied, current_execution, current_attempt
from backend.app.domain.scope import Scope
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
from backend.app.ports.v6_token_budget import V6TokenBudgetReceipt

RUN = '00000000-0000-0000-0000-000000000011'
SESSION = '00000000-0000-0000-0000-000000000012'
SCOPE = Scope(frozenset({'SIMULATED-kb'}), frozenset({'SIMULATED-doc'}))


def engine(database='SIMULATED-rag'):
    def forbidden(): pytest.fail('REAL DB CONNECTION FORBIDDEN')
    return create_engine('postgresql+psycopg://SIMULATED-C4@127.0.0.1:5432/'+database, creator=forbidden)


def build():
    return build_normal_run_construction(target=PostgresRunTarget('127.0.0.1',5432,'SIMULATED-rag'),
        write_engine=engine(), retrieval_engine=engine(),
        stream_endpoint=ApprovedRunStreamEndpoint.unix_socket(namespace='rag:v6:SIMULATED-C4',
            owner_approval_reference='SIMULATED-ENDPOINT-INPUT-NO-ACTUAL-SERVICE',db=15))


def request():
    return ProductRunRequest(SESSION, SCOPE, 'SIMULATED public question', 'quick', RUN)


def simulate(bundle, monkeypatch, *, duplicate=False, status='running', stream_denied=False):
    """Keep production class identities; explicitly substitute offline SQL/Redis operations."""
    repo, streams = bundle.lifecycle.repository, bundle.lifecycle.streams
    observations = {'status':status,'claims':[],'attempts':[],'finished_attempts':[],'finished_run':[],
                    'failed':[],'cleared':[], 'marker':None}
    claim = RunClaim(RUN,'SIMULATED-owner',not duplicate,'COMPLETED' if duplicate else 'IN_PROGRESS')
    def claimed(*args): observations['claims'].append(args); return claim
    def read(*args): return {'status':observations['status']}
    def fail(*args): observations['failed'].append(args); observations['status']='failed'
    def marker(session, run, owner):
        if stream_denied: raise OSError('SIMULATED_STREAM_UNAVAILABLE')
        observations['marker']={'run_id':run,'owner':owner}; return True
    monkeypatch.setattr(repo,'claim_run',claimed)
    monkeypatch.setattr(repo,'read_run',read)
    monkeypatch.setattr(repo,'fail_run',fail)
    monkeypatch.setattr(repo,'finish_run',lambda *a,**kw: observations['finished_run'].append((a,kw)))
    monkeypatch.setattr(repo,'cleanup_identity',lambda *a:None)
    monkeypatch.setattr(repo,'cleanup_done',lambda *a:None)
    monkeypatch.setattr(repo,'claim_attempt',lambda **kw: observations['attempts'].append(kw))
    monkeypatch.setattr(repo,'finish_attempt',lambda *a: observations['finished_attempts'].append(a))
    monkeypatch.setattr(streams,'set_live_run',marker)
    monkeypatch.setattr(streams,'clear_live_run',lambda *a: observations['cleared'].append(a) or True)
    return observations, claim


def test_normal_construction_is_lazy_and_binds_distinct_same_target_engines():
    bundle = build()
    assert type(bundle.lifecycle) is RunLifecycle
    assert type(bundle.lifecycle.repository) is PostgresRunLifecycle
    assert type(bundle.lifecycle.streams) is RedisStreamManager
    assert bundle.lifecycle.repository.engine is bundle.write_engine
    assert bundle.write_engine is not bundle.retrieval_engine
    assert bundle.write_engine.pool is not bundle.retrieval_engine.pool
    assert bundle.lifecycle.streams.namespace == 'rag:v6:SIMULATED-C4'
    from redis.connection import UnixDomainSocketConnection
    pool=bundle.lifecycle.streams.client.connection_pool
    assert pool.connection_class is UnixDomainSocketConnection
    assert pool.connection_kwargs['path']=='/opt/rag-p7-r1/state/streams-r1/redis.sock'
    assert pool.connection_kwargs['db']==15
    assert 'host' not in pool.connection_kwargs and 'port' not in pool.connection_kwargs


@pytest.mark.parametrize('kind',['shared_engine','foreign_database','no_stream','default_namespace'])
def test_construction_cannot_infer_real_services_or_mix_databases(kind):
    write, read = engine(), engine()
    endpoint = ApprovedRunStreamEndpoint.unix_socket(namespace='rag:v6:SIMULATED-C4',owner_approval_reference='SIMULATED')
    if kind=='shared_engine': read=write
    if kind=='foreign_database': read=engine('foreign')
    if kind=='no_stream': endpoint=None
    if kind=='default_namespace': endpoint=replace(endpoint,namespace='rag:stream:v1')
    with pytest.raises(LifecycleDenied):
        build_normal_run_construction(target=PostgresRunTarget('127.0.0.1',5432,'SIMULATED-rag'),
            write_engine=write,retrieval_engine=read,stream_endpoint=endpoint)


def test_run_session_owner_attempt_and_terminal_are_complete(monkeypatch):
    bundle=build(); obs,claim=simulate(bundle,monkeypatch)
    with bundle.executing(request()) as actual:
        assert actual is claim
        context=RunLifecycle.assert_current()
        assert context[0] is claim and context[1] is bundle.lifecycle and context[3]==SESSION
        with bundle.attempt(claim,role='chat_expensive',ordinal=1,envelope_sha256='a'*64,
                            provider_request_id='SIMULATED-native-request') as invocation:
            assert current_attempt.get()==invocation.product_attempt_id
            assert invocation.run_id==RUN and invocation.owner==claim.owner
            assert obs['attempts'][0]['envelope_hash']==invocation.envelope_sha256
        assert current_attempt.get() is None
        obs['status']='completed' # SIMULATED existing normal terminal commit, not performed by construction.
    assert current_execution.get() is None
    assert obs['claims'][0]==(SESSION,['SIMULATED-kb'],['SIMULATED-doc'],'SIMULATED public question','quick',RUN)
    assert obs['finished_attempts'][0][3]=='COMPLETED' and len(obs['cleared'])==1


def test_missing_normal_terminal_commit_fails_run_instead_of_inventing_success(monkeypatch):
    bundle=build();obs,_=simulate(bundle,monkeypatch)
    with pytest.raises(LifecycleDenied,match='NORMAL_TERMINAL_COMMIT_REQUIRED'):
        with bundle.executing(request()): pass
    assert obs['failed'] and obs['status']=='failed'


def test_duplicate_or_completed_product_run_is_never_reexecuted(monkeypatch):
    bundle=build();obs,_=simulate(bundle,monkeypatch,duplicate=True)
    with pytest.raises(LifecycleDenied,match='NOT_REEXECUTABLE'):
        with bundle.executing(request()): pytest.fail('BODY MUST NOT EXECUTE')
    assert obs['attempts']==[] and obs['marker'] is None


def test_stream_unavailable_retains_normal_admission_failure(monkeypatch):
    bundle=build();obs,_=simulate(bundle,monkeypatch,stream_denied=True)
    with pytest.raises(LifecycleDenied,match='STREAM_ADMISSION_DENIED'):
        with bundle.executing(request()): pytest.fail('BODY MUST NOT EXECUTE')
    assert obs['finished_run'][0][1]=={'not_sent':True} and obs['attempts']==[]


@pytest.mark.parametrize('kind',['cancelled','lost','foreign_claim'])
def test_cancellation_loss_and_wrong_claim_cannot_admit_an_attempt(monkeypatch,kind):
    bundle=build();obs,claim=simulate(bundle,monkeypatch)
    with bundle.executing(request()):
        if kind=='lost': current_execution.get()[2].set()
        if kind=='cancelled': obs['status']='cancelled';current_execution.get()[2].set()
        target=replace(claim,owner='other') if kind=='foreign_claim' else claim
        with pytest.raises(LifecycleDenied):
            with bundle.attempt(target,role='chat_expensive',ordinal=1,envelope_sha256='a'*64,
                                provider_request_id='SIMULATED-request'):pytest.fail('BODY MUST NOT EXECUTE')
        if kind!='cancelled':obs['status']='completed'
    assert obs['attempts']==[]


@pytest.mark.parametrize('error,state',[(ProviderRequestNotSent('SIMULATED'),'NOT_SENT'),
                                       (ProviderUnavailable('SIMULATED'),'UNKNOWN')])
def test_attempt_finalization_preserves_send_uncertainty(monkeypatch,error,state):
    bundle=build();obs,claim=simulate(bundle,monkeypatch)
    with pytest.raises(type(error)):
        with bundle.executing(request()):
            with bundle.attempt(claim,role='chat_expensive',ordinal=1,envelope_sha256='a'*64,
                                provider_request_id='SIMULATED-native-request'):raise error
    assert obs['finished_attempts'][0][3]==state and obs['failed']


class SimulatedSQLResult:
    def __init__(self, lease, valid): self.lease, self.valid, self.rowcount = lease, valid, 1
    def one(self): return (RUN,)
    def mappings(self): return self
    def first(self): return self.lease
    def scalar(self): return self.valid


class SimulatedSQLEngine:
    def __init__(self, lease, valid=1): self.lease,self.valid,self.calls=lease,valid,[]
    @contextmanager
    def begin(self): yield self
    def execute(self, statement, values):
        self.calls.append((str(statement),values))
        return SimulatedSQLResult(self.lease,self.valid)


def receipt():
    return V6TokenBudgetReceipt('SIMULATED-request','SIMULATED-token','SIMULATED-attempt',RUN,'SIMULATED-owner',SESSION,
        ('SIMULATED-kb',),('SIMULATED-doc',),'a'*64,'SIMULATED-grant','SIMULATED-activity',None,None,
        'SIMULATED-lease','b'*64,'c'*64,100,512,'reserved','reserved')


def sql_statement_type(query):
    return query.lstrip().split(None, 1)[0].upper()


def test_sql_classifier_distinguishes_locked_select_from_real_update():
    locked_select = 'SELECT id FROM rag_runs WHERE id=:id FOR UPDATE'
    actual_update = ' \nupdate rag_model_attempts SET state=:state WHERE id=:id'
    assert sql_statement_type(locked_select) == 'SELECT'
    assert locked_select.endswith('FOR UPDATE')
    assert sql_statement_type(actual_update) == 'UPDATE'


@pytest.mark.parametrize('lease', [None, {'owner':'foreign','valid':True,'state':'IN_PROGRESS'},
    {'owner':'SIMULATED-owner','valid':False,'state':'IN_PROGRESS'},
    {'owner':'SIMULATED-owner','valid':True,'state':'CANCELLED'}])
def test_normal_pg_token_link_and_final_fence_reject_bad_lease(lease):
    for method in ('link_token_budget','assert_token_budget_current'):
        sql=SimulatedSQLEngine(lease)
        repository=PostgresRunLifecycle(sql)
        with pytest.raises(LifecycleDenied): getattr(repository,method)(receipt())
        assert len(sql.calls)==2 and not any(sql_statement_type(query)=='UPDATE' for query,_ in sql.calls)
        assert all(sql_statement_type(query)=='SELECT' and query.rstrip().upper().endswith('FOR UPDATE')
                   for query,_ in sql.calls)


def test_normal_pg_receipt_link_uses_scope_attempt_and_native_identity_without_currency_row():
    sql=SimulatedSQLEngine({'owner':'SIMULATED-owner','valid':True,'state':'IN_PROGRESS'})
    repository=PostgresRunLifecycle(sql)
    repository.link_token_budget(receipt())
    repository.assert_token_budget_current(receipt())
    query,values=sql.calls[2]
    assert sql_statement_type(query)=='UPDATE'
    assert 'envelope_hash=:envelope' in query and "role='chat_expensive'" in query
    assert values['attempt']=='SIMULATED-attempt' and values['owner']=='SIMULATED-owner'
    assert json_payload(values['receipt'])['native_token_attempt_id']=='SIMULATED-token'
    assert sql.calls[0][1]['session']==SESSION and 'knowledge_base_scope' in sql.calls[0][0]
    assert sql.calls[-1][1]['native']=='SIMULATED-token'
    assert not any('model_calls' in query or 'budget_reservations' in query for query,_ in sql.calls)


def json_payload(value):
    import json
    return json.loads(value)


def test_normal_heartbeat_sets_loss_and_denies_later_attempt(monkeypatch):
    from threading import Event
    bundle=build();obs,claim=simulate(bundle,monkeypatch)
    bundle.lifecycle.repository.lease_seconds=3
    bundle.lifecycle.streams.live_ttl=3
    renewed=Event()
    monkeypatch.setattr(bundle.lifecycle.streams,'renew_live_run',lambda *a:True)
    def failed(*args):
        renewed.set()
        raise LifecycleDenied('SIMULATED_RENEW_LEASE_LOST')
    monkeypatch.setattr(bundle.lifecycle.repository,'renew',failed)
    with bundle.executing(request()):
        assert renewed.wait(2), 'Real local heartbeat thread did not run'
        assert current_execution.get()[2].wait(1)
        with pytest.raises(LifecycleDenied,match='RUN_LIFECYCLE_UNAVAILABLE'):
            with bundle.attempt(claim,role='chat_expensive',ordinal=1,envelope_sha256='a'*64,
                                provider_request_id='SIMULATED-request'):pytest.fail('NO DISPATCH AFTER LOSS')
        obs['status']='failed'
    assert obs['attempts']==[]


@pytest.mark.parametrize('url', ['redis://127.0.0.1:6379/0',
    'unix:///tmp/other.sock?db=0',
    'unix:///opt/rag-p7-r1/state/streams-r1/redis.sock?db=0&socket_timeout=999',
    'unix://foreign/opt/rag-p7-r1/state/streams-r1/redis.sock?db=0'])
def test_only_approved_unix_socket_and_closed_query_parameters_are_admitted(url):
    with pytest.raises(LifecycleDenied):
        ApprovedRunStreamEndpoint(url,'rag:v6:SIMULATED-C4','SIMULATED-OWNER').validate()


def test_official_unix_client_connection_error_stays_normal_admission_denial(monkeypatch):
    from redis.exceptions import ConnectionError
    bundle=build();obs,_=simulate(bundle,monkeypatch)
    # Restore the normal StreamManager method; inject an error, never success,
    # at the real official client boundary. No actual socket connect is made.
    monkeypatch.setattr(bundle.lifecycle.streams,'set_live_run',
        RedisStreamManager.set_live_run.__get__(bundle.lifecycle.streams))
    def failed(*a,**k):raise ConnectionError('SIMULATED UNIX SOCKET NOT READY')
    monkeypatch.setattr(bundle.lifecycle.streams.client,'set',failed)
    with pytest.raises(LifecycleDenied,match='STREAM_ADMISSION_DENIED'):
        with bundle.executing(request()):pytest.fail('NO EXECUTION ON UNREADY SOCKET')
    assert obs['finished_run'][0][1]=={'not_sent':True} and obs['attempts']==[]
