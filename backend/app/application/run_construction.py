"""Normal PG/Redis construction ABI, with no connection, deployment or fake fallback."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit, parse_qsl, unquote
import re
import uuid

from sqlalchemy.engine import Engine
from backend.app.adapters.postgres.run_lifecycle import PostgresRunLifecycle
from backend.app.adapters.redis_stream import RedisStreamManager
from backend.app.application.run_lifecycle import RunLifecycle
from backend.app.domain.scope import Scope
from backend.app.ports.run_lifecycle import (
    LifecycleDenied, current_attempt, execution_owner, RunClaim, checked_retrieval_completion,
)
from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.ports.session_attempts import request_identity


@dataclass(frozen=True)
class PostgresRunTarget:
    host: str
    port: int
    database: str

    def __post_init__(self):
        if (type(self.host) is not str or not self.host or type(self.database) is not str or not self.database
                or type(self.port) is not int or not 0 < self.port <= 65535
                or self.host.startswith('/') and self.host != '/var/run/postgresql'):
            raise LifecycleDenied('PG_RUN_TARGET_INVALID')

    @staticmethod
    def _engine_identity(engine):
        """Resolve one explicit psycopg URL target; reject ambiguous overrides."""
        if (not isinstance(engine, Engine) or engine.url.drivername != 'postgresql+psycopg'
                or engine.dialect.name != 'postgresql' or engine.dialect.driver != 'psycopg'):
            return None
        url, query = engine.url, engine.url.query
        if (any(type(value) is not str for value in query.values())
                or set(query) & {'hostaddr', 'service', 'servicefile', 'dsn', 'database', 'username', 'drivername'}):
            return None
        host, user, database, port = url.host, url.username, url.database, url.port
        for key, original in (('host', host), ('user', user), ('dbname', database)):
            if key in query and original is not None and query[key] != original:
                return None
        host = query.get('host', host)
        user = query.get('user', user)
        database = query.get('dbname', database)
        if 'port' in query:
            if not re.fullmatch(r'[0-9]+', query['port']): return None
            query_port = int(query['port'])
            if port is not None and port != query_port: return None
            port = query_port
        if (type(host) is not str or not host or type(user) is not str
                or not re.fullmatch(r'[A-Za-z0-9_.-]+', user)
                or type(database) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]+', database)
                or type(port) is not int or not 0 < port <= 65535):
            return None
        if host.startswith('/'):
            if host != '/var/run/postgresql' or user != 'rag-eval': return None
        elif not re.fullmatch(r'[A-Za-z0-9_.-]+', host):
            return None
        return (host, port, database, user, url.drivername)

    def matches(self, engine):
        identity = self._engine_identity(engine)
        return identity is not None and identity[:3] == (self.host, self.port, self.database)


@dataclass(frozen=True)
class ApprovedRunStreamEndpoint:
    """Concrete separately approved endpoint input; never inferred from settings."""
    url: str = field(repr=False)
    namespace: str
    owner_approval_reference: str

    @classmethod
    def unix_socket(cls, *, namespace, owner_approval_reference, db=0,
                    socket_path='/opt/rag-p7-r1/state/streams-r1/redis.sock'):
        if type(db) is not int or not 0 <= db <= 15:
            raise LifecycleDenied('APPROVED_ISOLATED_STREAM_ENDPOINT_REQUIRED')
        endpoint = cls(f'unix://{socket_path}?db={db}', namespace, owner_approval_reference)
        endpoint.validate()
        return endpoint

    def validate(self):
        parsed = urlsplit(self.url)
        query = parse_qsl(parsed.query, strict_parsing=True)
        if (parsed.scheme != 'unix' or parsed.netloc or parsed.fragment
                or unquote(parsed.path) != '/opt/rag-p7-r1/state/streams-r1/redis.sock'
                or len(query) != 1 or query[0][0] != 'db' or not query[0][1].isdigit()
                or not 0 <= int(query[0][1]) <= 15
                or not re.fullmatch(r'rag:v6:[A-Za-z0-9_.:-]{1,128}', self.namespace)
                or type(self.owner_approval_reference) is not str or not self.owner_approval_reference.strip()):
            raise LifecycleDenied('APPROVED_ISOLATED_STREAM_ENDPOINT_REQUIRED')


@dataclass(frozen=True)
class ProductRunRequest:
    session_id: str
    server_scope: Scope
    content: str = field(repr=False)
    mode: str
    request_id: str

    def validate(self):
        try:
            if (type(self) is not ProductRunRequest or str(uuid.UUID(self.request_id)) != self.request_id
                    or str(uuid.UUID(self.session_id)) != self.session_id
                    or type(self.server_scope) is not Scope
                    or type(self.server_scope.knowledge_base_ids) is not frozenset
                    or type(self.server_scope.document_ids) is not frozenset
                    or not self.server_scope.knowledge_base_ids
                    or any(type(x) is not str or not x for x in
                           self.server_scope.knowledge_base_ids | self.server_scope.document_ids)
                    or self.mode not in ('quick', 'smart') or type(self.content) is not str or not self.content.strip()):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise LifecycleDenied('PRODUCT_RUN_CONSTRUCTION_INPUT_INVALID') from None

    @property
    def kbs(self): return sorted(self.server_scope.knowledge_base_ids)

    @property
    def docs(self): return sorted(self.server_scope.document_ids)


@dataclass(frozen=True)
class ProductAttemptInvocation:
    run_id: str
    owner: str
    product_attempt_id: str
    provider_request_id: str
    envelope_sha256: str


@dataclass(frozen=True)
class NormalRunConstruction:
    target: PostgresRunTarget
    write_engine: Engine = field(repr=False)
    retrieval_engine: Engine = field(repr=False)
    lifecycle: RunLifecycle = field(repr=False)
    stream_endpoint: ApprovedRunStreamEndpoint = field(repr=False)

    def finalize_retrieval(self, request: ProductRunRequest, *, completed_qids: tuple[str, ...],
                           native_receipts_sha256: str) -> bool:
        """Commit retrieval only; caller first verifies all selected native receipts succeeded."""
        if type(request) is not ProductRunRequest:
            raise LifecycleDenied('PRODUCT_RUN_CONSTRUCTION_INPUT_INVALID')
        request.validate()
        checked_retrieval_completion(completed_qids, native_receipts_sha256)
        claim, lifecycle, _, session = RunLifecycle.assert_current()
        if (type(claim) is not RunClaim or lifecycle is not self.lifecycle
                or type(lifecycle) is not RunLifecycle or type(lifecycle.repository) is not PostgresRunLifecycle
                or lifecycle.repository.engine is not self.write_engine
                or not claim.created or claim.state != 'IN_PROGRESS' or claim.run_id != request.request_id
                or session != request.session_id or execution_owner.get() != (claim.run_id, claim.owner)
                or current_attempt.get() is not None):
            raise LifecycleDenied('RETRIEVAL_COMPLETION_CONTEXT_INVALID')
        return lifecycle.repository.finalize_retrieval(run_id=claim.run_id, owner=claim.owner,
            session=session, kbs=request.kbs, docs=request.docs, content=request.content, mode=request.mode,
            completed_qids=completed_qids, native_receipts_sha256=native_receipts_sha256)

    @contextmanager
    def executing(self, request):
        if type(request) is not ProductRunRequest:
            raise LifecycleDenied('PRODUCT_RUN_CONSTRUCTION_INPUT_INVALID')
        request.validate()
        repository = self.lifecycle.repository
        claim = repository.claim_run(request.session_id, request.kbs, request.docs,
            request.content, request.mode, request.request_id)
        if not claim.created or claim.state != 'IN_PROGRESS' or claim.run_id != request.request_id:
            raise LifecycleDenied('PRODUCT_RUN_NOT_REEXECUTABLE')
        try:
            saved = repository.read_run(claim.run_id, request.session_id, request.kbs, request.docs)
            if saved['status'] not in ('created', 'running'):
                raise LifecycleDenied('PRODUCT_RUN_STATE_INVALID')
        except Exception:
            repository.fail_run(claim.run_id, claim.owner)
            repository.finish_run(claim.run_id, claim.owner)
            raise
        with self.lifecycle.executing(claim, request.session_id):
            yield claim
            # Existing normal AnswerService/event-store commit remains authority.
            # A generator/diagnostic call cannot invent a completed product Run.
            saved = repository.read_run(claim.run_id, request.session_id, request.kbs, request.docs)
            if saved['status'] not in ('completed', 'failed', 'cancelled'):
                raise LifecycleDenied('NORMAL_TERMINAL_COMMIT_REQUIRED')

    @contextmanager
    def attempt(self, claim, *, role, ordinal, envelope_sha256, provider_request_id):
        context = RunLifecycle.assert_current()
        if (context[0] is not claim or context[1] is not self.lifecycle
                or role not in ('chat_cheap', 'chat_expensive')
                or type(ordinal) is not int or ordinal not in (1, 2) or ordinal == 2 and role != 'chat_expensive'
                or type(envelope_sha256) is not str
                or not re.fullmatch(r'[0-9a-f]{64}', envelope_sha256)
                or type(provider_request_id) is not str
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', provider_request_id)
                or current_attempt.get() is not None):
            raise LifecycleDenied('PRODUCT_ATTEMPT_CONSTRUCTION_INVALID')
        identity = request_identity(claim.run_id, 'quick.router.' + role, ordinal)
        provider, model = ('deepseek', 'deepseek-flash') if role == 'chat_expensive' else ('siliconflow', 'XingChenAGI/Xing4.0-29B')
        self.lifecycle.repository.claim_attempt(run_id=claim.run_id, owner=claim.owner, attempt_id=identity,
            role=role, ordinal=ordinal, envelope_hash=envelope_sha256, provider=provider, model=model)
        token = current_attempt.set(identity)
        state = 'COMPLETED'
        try:
            yield ProductAttemptInvocation(claim.run_id, claim.owner, identity, provider_request_id, envelope_sha256)
        except ProviderRequestNotSent:
            state = 'NOT_SENT'
            raise
        except Exception:
            state = 'UNKNOWN'
            raise
        finally:
            current_attempt.reset(token)
            self.lifecycle.repository.finish_attempt(claim.run_id, claim.owner, identity, state,
                {'provider_request_id': provider_request_id, 'envelope_sha256': envelope_sha256})


def build_normal_run_construction(*, target, write_engine, retrieval_engine,
                                  stream_endpoint, lease_seconds=60, event_ttl_seconds=86400,
                                  event_store=None):
    """Construct only. Actual same-DB identity/readonly checks run on live connections.

    Endpoint approval and DB privileges are caller/deployment prerequisites. No
    Redis service, DB row, lease, connection or provider request is created here.
    """
    if (type(target) is not PostgresRunTarget or not target.matches(write_engine)
            or not target.matches(retrieval_engine) or write_engine is retrieval_engine
            or write_engine.pool is retrieval_engine.pool
            or target._engine_identity(write_engine) != target._engine_identity(retrieval_engine)):
        raise LifecycleDenied('SEPARATE_SAME_TARGET_PG_ENGINES_REQUIRED')
    if type(stream_endpoint) is not ApprovedRunStreamEndpoint:
        raise LifecycleDenied('APPROVED_ISOLATED_STREAM_ENDPOINT_REQUIRED')
    stream_endpoint.validate()
    streams = RedisStreamManager.from_url(stream_endpoint.url, namespace=stream_endpoint.namespace,
        live_ttl=lease_seconds, event_ttl=event_ttl_seconds)
    lifecycle = RunLifecycle(PostgresRunLifecycle(write_engine, lease_seconds=lease_seconds), streams, event_store)
    return NormalRunConstruction(target, write_engine, retrieval_engine, lifecycle, stream_endpoint)
