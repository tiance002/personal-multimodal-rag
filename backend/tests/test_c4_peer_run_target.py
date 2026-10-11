"""Actual SQLAlchemy Engines, SIMULATED connection/Redis fences; no live services."""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from redis import Redis

from backend.app.application.run_construction import (
    PostgresRunTarget, ApprovedRunStreamEndpoint, build_normal_run_construction,
)
from backend.app.ports.run_lifecycle import LifecycleDenied

SOCKET = '/var/run/postgresql'
DATABASE = 'rag_p7_r1'


def peer_url(**changes):
    values = dict(drivername='postgresql+psycopg', username='rag-eval', port=5432,
                  database=DATABASE, query={'host': SOCKET})
    values.update(changes)
    return URL.create(**values)


@pytest.fixture
def no_io(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('service I/O')
        pytest.fail('REAL DB/REDIS CONNECTION FORBIDDEN')
    monkeypatch.setattr(Redis, 'execute_command', forbidden)
    def engine(url):
        result = create_engine(url, creator=forbidden)
        event.listen(result, 'do_connect', forbidden)
        return result
    yield engine
    assert calls == []


def build(writer, reader, target=None):
    return build_normal_run_construction(
        target=target or PostgresRunTarget(SOCKET, 5432, DATABASE),
        write_engine=writer, retrieval_engine=reader,
        stream_endpoint=ApprovedRunStreamEndpoint.unix_socket(
            namespace='rag:v6:SIMULATED-C4-peer', owner_approval_reference='SIMULATED-no-live-service'),
    )


def test_canonical_peer_constructor_uses_query_host_without_connecting(no_io):
    writer_url = peer_url()
    reader_url = peer_url(query={'host': SOCKET, 'options': '-c default_transaction_read_only=on',
                                'application_name': 'rag-public-bench-readonly'})
    writer, reader = no_io(writer_url), no_io(reader_url)
    assert writer is not reader and writer.pool is not reader.pool
    assert writer.url.host is reader.url.host is None
    for engine in (writer, reader):
        args, params = engine.dialect.create_connect_args(engine.url)
        assert args == []
        assert (params['host'], params['port'], params['dbname'], params['user']) == (SOCKET, 5432, DATABASE, 'rag-eval')
    bundle = build(writer, reader)
    assert bundle.write_engine is writer and bundle.retrieval_engine is reader
    assert bundle.lifecycle.repository.engine is writer
    assert reader.url.query['options'] == '-c default_transaction_read_only=on'


@pytest.mark.parametrize('representation', ['host_field', 'matching_both', 'query_port', 'query_user_db'])
def test_single_explicit_peer_identity_representations_are_equivalent(no_io, representation):
    if representation == 'host_field': url = peer_url(host=SOCKET, query={})
    elif representation == 'matching_both': url = peer_url(host=SOCKET)
    elif representation == 'query_port': url = peer_url(port=None, query={'host': SOCKET, 'port': '5432'})
    else: url = peer_url(username=None, database=None, query={'host': SOCKET, 'user': 'rag-eval', 'dbname': DATABASE})
    bundle = build(no_io(url), no_io(url))
    assert bundle.target.host == SOCKET and bundle.write_engine.pool is not bundle.retrieval_engine.pool


@pytest.mark.parametrize('case', [
    'host_conflict', 'other_socket', 'host_list', 'host_tuple', 'port_conflict',
    'missing_host', 'missing_port', 'missing_user', 'missing_database', 'empty_host',
    'other_database', 'other_user', 'query_database_conflict', 'query_user_conflict',
    'zero_port', 'hostaddr', 'service', 'database_alias', 'dsn', 'different_driver',
])
def test_peer_identity_conflicts_multihost_missing_and_foreign_targets_are_denied(no_io, case):
    changes = {}
    if case == 'host_conflict': changes = {'host': '127.0.0.1'}
    elif case == 'other_socket': changes = {'query': {'host': '/tmp/other-postgres'}}
    elif case == 'host_list': changes = {'query': {'host': SOCKET + ',/tmp/other-postgres'}}
    elif case == 'host_tuple': changes = {'query': {'host': (SOCKET, '/tmp/other-postgres')}}
    elif case == 'port_conflict': changes = {'query': {'host': SOCKET, 'port': '5433'}}
    elif case == 'missing_host': changes = {'query': {}}
    elif case == 'missing_port': changes = {'port': None}
    elif case == 'missing_user': changes = {'username': None}
    elif case == 'missing_database': changes = {'database': None}
    elif case == 'empty_host': changes = {'query': {'host': ''}}
    elif case == 'other_database': changes = {'database': 'other_database'}
    elif case == 'other_user': changes = {'username': 'other_user'}
    elif case == 'query_database_conflict': changes = {'query': {'host': SOCKET, 'dbname': 'other_database'}}
    elif case == 'query_user_conflict': changes = {'query': {'host': SOCKET, 'user': 'other_user'}}
    elif case == 'zero_port': changes = {'port': 0}
    elif case == 'different_driver': changes = {'drivername': 'postgresql+psycopg_async'}
    elif case == 'database_alias': changes = {'query': {'host': SOCKET, 'database': 'other_database'}}
    else: changes = {'query': {'host': SOCKET, case: 'SIMULATED-override'}}
    writer, reader = no_io(peer_url()), no_io(peer_url(**changes))
    with pytest.raises(LifecycleDenied, match='SEPARATE_SAME_TARGET_PG_ENGINES_REQUIRED'):
        build(writer, reader)


@pytest.mark.parametrize('field', ['username', 'drivername'])
def test_tcp_pair_cannot_use_different_users_or_drivers(no_io, field):
    base = peer_url(host='127.0.0.1', query={})
    foreign = base.set(**{field: 'other_user' if field == 'username' else 'postgresql+psycopg_async'})
    with pytest.raises(LifecycleDenied, match='SEPARATE_SAME_TARGET_PG_ENGINES_REQUIRED'):
        build(no_io(base), no_io(foreign), PostgresRunTarget('127.0.0.1', 5432, DATABASE))


def test_peer_same_engine_or_shared_pool_is_still_denied(no_io):
    writer = no_io(peer_url())
    with pytest.raises(LifecycleDenied): build(writer, writer)
    reader = no_io(peer_url())
    reader.pool = writer.pool
    with pytest.raises(LifecycleDenied): build(writer, reader)


def test_arbitrary_declared_socket_is_not_an_approved_peer_target(no_io):
    with pytest.raises(LifecycleDenied):
        build(no_io(peer_url(query={'host': '/tmp/other-postgres'})),
              no_io(peer_url(query={'host': '/tmp/other-postgres'})),
              PostgresRunTarget('/tmp/other-postgres', 5432, DATABASE))
