"""Explicit isolated application assembly; imports never start infrastructure.

Tencent/WeKnora3e8b0bfc stage mechanisms are reused rather than copied. The
application supplies verified leases, principal, source conversation settings
and optional permission/cost authorities. Missing authorities deny sends.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Any, Mapping
import inspect, json, uuid, hashlib

from .crypto import SecretCipher
from .models.types import ProviderError, ChatOptions, CallContext, TrustedScope
from .model_registry import SourceCatalogue, NeutralModelRegistry, role_snapshot
from .generation import GenerationCoordinator, RouterPolicy
from .generation.types import RoleBinding
from .retrieval.pipeline import PipelineState, RetrievalPipeline
from .retrieval.search import SearchTarget
from .service import RuntimeService, TurnConfiguration, OrdinaryMemoryLoader
from .conversation import OrdinaryHistoryService, MemoryService
from .storage.configuration import PostgresModelRoleStore
from .storage.models import PostgresModelStore
from .storage.turns import PostgresTurnRepository
from .storage.generation import PostgresGenerationLifecycle
from .storage.memory import PostgresMemoryRepository
from .storage.hybrid import PostgresKnowledgeRepository
from .storage.send_gate import P1SendGate


@dataclass(frozen=True)
class ConversationSettings:
    """Values resolved from fixed source config/config.yaml conversation.

    Session non-null persisted fields override startup settings. Model identities
    and the router mode are server configuration, never message JSON.
    """
    max_rounds: int = 5
    embedding_top_k: int = 30
    vector_threshold: float = .2
    keyword_threshold: float = .3
    rerank_top_k: int = 30
    rerank_threshold: float = .3
    enable_rewrite: bool = True
    enable_query_expansion: bool = True
    enable_rerank: bool = True
    summary: Mapping[str, Any] = field(default_factory=lambda: {
        "temperature": .3, "max_completion_tokens": 1024})

    @classmethod
    def from_mapping(cls, configuration):
        row = configuration["conversation"]
        names = cls.__dataclass_fields__.keys()
        return cls(**{k: row[k] for k in names if k in row})


class LeasedMemoryRepository:
    """Each source operation owns its verified PG connection/transaction."""
    def __init__(self, leases, principal):
        self.leases, self.principal = leases, principal

    def __getattr__(self, name):
        if name.startswith("_") or not hasattr(PostgresMemoryRepository, name):
            raise AttributeError(name)

        async def invoke(*args, **kwargs):
            if args:
                scope = args[0]
                if hasattr(scope, "tenant_id"):
                    if (scope.tenant_id, scope.subject_id) != (
                            self.principal.tenant_id, self.principal.subject_id):
                        raise ProviderError("MEMORY_OWNER_MISMATCH")
                elif name == "workspace_config" and scope != self.principal.tenant_id:
                    raise ProviderError("MEMORY_OWNER_MISMATCH")
            async with self.leases.lease() as guard:
                repo = PostgresMemoryRepository(
                    guard.connection, guard.isolation, identity_guard=guard)
                return await getattr(repo, name)(*args, **kwargs)
        return invoke


class LeasedSendGate:
    def __init__(self, application):
        self.application = application

    async def _call(self, name, call, **kwargs):
        app = self.application
        async with app.leases.lease() as guard:
            gate = P1SendGate(guard, tenant_id=app.principal.tenant_id,
                              permissions=app.permissions, costs=app.costs)
            return await getattr(gate, name)(call, **kwargs)

    async def authorize(self, call):
        return await self._call("authorize", call)

    async def finish(self, call, *, outcome, usage):
        return await self._call("finish", call, outcome=outcome, usage=usage)


class LeasedLifecycle:
    def __init__(self, application, turn):
        self.application, self.turn = application, turn

    def __getattr__(self, name):
        if name not in ("claim_run", "begin_attempt", "finish_attempt"):
            raise AttributeError(name)

        async def invoke(*args):
            app, turn = self.application, self.turn
            async with app.leases.lease() as guard:
                adapter = PostgresGenerationLifecycle(
                    guard, tenant_id=app.principal.tenant_id,
                    session_id=turn.session_id, message_id=turn.assistant_message_id)
                return await getattr(adapter, name)(*args)
        return invoke


class LeasedKnowledgeRepository:
    def __init__(self, application, registry):
        self.app, self.registry = application, registry

    async def _tenant(self, owner):
        if owner != self.app.principal.subject_id:
            raise ProviderError("KNOWLEDGE_OWNER_MISMATCH")
        return self.app.principal.tenant_id

    async def _embed(self, knowledge_base_id, query, context):
        if knowledge_base_id not in context.scope.knowledge_base_ids:
            raise ProviderError("KNOWLEDGE_BASE_SCOPE_MISMATCH")
        rows = await self.app._rows(
            "SELECT embedding_model_id FROM knowledge_bases WHERE tenant_id=%s "
            "AND id=%s AND deleted_at IS NULL",
            (await self._tenant(context.scope.owner_id), knowledge_base_id))
        if len(rows) != 1 or not rows[0]["embedding_model_id"]:
            raise ProviderError("EMBEDDING_MODEL_CONFIGURATION_REQUIRED")
        model = await self.registry.embedding(rows[0]["embedding_model_id"], context)
        suffix = knowledge_base_id + "-" + hashlib.sha256(query.encode()).hexdigest()[:16]
        result = await model.embed((query,), context.child("query-embedding-" + suffix), kind="query")
        if len(result.vectors) != 1:
            raise ProviderError("QUERY_EMBEDDING_RESULT_INVALID")
        return result.vectors[0]

    def __getattr__(self, name):
        if name not in ("hybrid_search", "get_chunks", "get_chunk_children", "get_faq_answers"):
            raise AttributeError(name)

        async def invoke(*args, **kwargs):
            async with self.app.leases.lease() as guard:
                repo = PostgresKnowledgeRepository(
                    guard, self._tenant, embed_query=self._embed,
                    model_identity=self.registry.physical_key)
                return await getattr(repo, name)(*args, **kwargs)
        return invoke


class BoundOrdinaryService(RuntimeService):
    def __init__(self, application, *args, **kwargs):
        self.application = application
        super().__init__(*args, **kwargs)

    async def run_turn(self, turn):
        try:
            return await super().run_turn(turn)
        finally:
            state = self.application._runs.pop(turn.run_id, None)
            if state is not None:
                await state["registry"].close()


class ApplicationRuntime:
    """Concrete scoped SQL facade + one assembled ordinary RAG chain.

    model_factory(config, key, durable_gate) must return the neutral ProviderModel.
    Its actual transport always uses durable_gate. No real credential or network
    default is inferred. documents is the source DocumentLifecycleService with
    explicit parser/embed/postprocessing dependencies.
    """
    def __init__(self, leases, principal, store, documents, *, model_factory,
                 cipher: SecretCipher, conversation=None, policy=None,
                 estimator=None, permissions=None, costs=None, catalogue=None):
        self.leases, self.principal = leases, principal
        self.store, self.documents = store, documents
        self.cipher, self.catalogue = cipher, catalogue or SourceCatalogue()
        self.model_factory, self.permissions, self.costs = model_factory, permissions, costs
        self.conversation = conversation or ConversationSettings()
        self.policy, self.estimator = policy or RouterPolicy(), estimator
        self.models = PostgresModelStore(leases, principal, self.catalogue, cipher=cipher)
        self.turns = PostgresTurnRepository(leases, principal)
        self.memory_repository = LeasedMemoryRepository(leases, principal)
        self.memory = MemoryService(self.memory_repository)
        self.history = OrdinaryHistoryService(self.memory_repository)
        self._runs = {}
        self.ordinary = BoundOrdinaryService(
            self, self.turns, store, self.history, OrdinaryMemoryLoader(self.memory),
            configuration=self._configuration, pipeline_factory=self._pipeline,
            generation_factory=self._generation)

    def _owner(self, principal):
        if principal != self.principal:
            raise ProviderError("TRUSTED_PRINCIPAL_REQUIRED")

    async def _rows(self, sql, params=()):
        async with self.leases.lease() as guard:
            return await (await guard.connection.execute(sql, params)).fetchall()

    async def runtime_identity(self, principal):
        self._owner(principal)
        async with self.leases.lease() as guard:
            identity = await guard.verify_identity()
        return {"implementation": "weknora-python-port", "backend": "weknora_python_port", "upstream_commit":
                "3e8b0bfc80b845b2d4b2ed683994748741450a97",
                "database": identity.database, "system_identifier": identity.system_identifier,
                "real_provider_status": "NOT_RUN", "cutover_status": "NOT_RUN"}

    async def read_model_configuration(self, principal):
        self._owner(principal)
        async with self.leases.lease() as guard:
            return await PostgresModelRoleStore(
                guard, principal, self.catalogue, cipher=self.cipher).read_public()

    async def update_model_configuration(self, principal, revision, roles):
        self._owner(principal)
        async with self.leases.lease() as guard:
            return await PostgresModelRoleStore(
                guard, principal, self.catalogue, cipher=self.cipher).update(revision, roles)

    async def create_session(self, principal, request):
        self._owner(principal)
        session = str(uuid.uuid4())
        kbs = tuple(sorted(set(request.knowledge_base_scope)))
        docs = tuple(sorted(set(request.document_scope)))
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                await self.turns._resources(guard, kbs, docs)
                defaults = self.conversation
                roles = await self._snapshot(guard)
                await guard.connection.execute(
                    "INSERT INTO sessions(id,tenant_id,subject_id,title,knowledge_base_scope,"
                    "document_scope,max_rounds,enable_rewrite,embedding_top_k,vector_threshold,"
                    "keyword_threshold,rerank_top_k,rerank_threshold,summary_model_id,rerank_model_id,"
                    "summary_parameters) VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (session, principal.tenant_id, principal.subject_id, request.title,
                     json.dumps(kbs), json.dumps(docs), defaults.max_rounds, defaults.enable_rewrite,
                     defaults.embedding_top_k, defaults.vector_threshold, defaults.keyword_threshold,
                     defaults.rerank_top_k, defaults.rerank_threshold, roles.model_id("cheap"),
                     dict(roles.model_ids).get("rerank", ""), json.dumps(dict(defaults.summary))))
        return {"id": session, "title": request.title, "knowledge_base_scope": list(kbs),
                "document_scope": list(docs)}

    async def list_sessions(self, principal):
        self._owner(principal)
        return await self._rows(
            "SELECT id,title,knowledge_base_scope,document_scope FROM sessions WHERE "
            "tenant_id=%s AND subject_id=%s AND deleted_at IS NULL ORDER BY created_at,id",
            (principal.tenant_id, principal.subject_id))

    async def list_messages(self, principal, session_id):
        self._owner(principal)
        async with self.leases.lease() as guard:
            await self.turns._session(guard, session_id, principal)
            return await (await guard.connection.execute(
                "SELECT id,request_id,role,content,knowledge_references,is_completed,turn_status,"
                "turn_error_code,knowledge_base_scope,document_scope FROM messages WHERE "
                "tenant_id=%s AND subject_id=%s AND session_id=%s AND deleted_at IS NULL "
                "ORDER BY created_at,id", (principal.tenant_id, principal.subject_id, session_id))).fetchall()

    async def _snapshot(self, guard):
        configuration = PostgresModelRoleStore(
            guard, self.principal, self.catalogue, cipher=self.cipher)
        return await role_snapshot(configuration, self.models)

    async def _configuration(self, turn):
        async with self.leases.lease() as guard:
            session = await self.turns._session(guard, turn.session_id, self.principal)
            snapshot = await self._snapshot(guard)
        gate = LeasedSendGate(self)

        async def factory(config, key):
            model = self.model_factory(config, key, gate)
            model = await model if inspect.isawaitable(model) else model
            if getattr(getattr(model, "transport", None), "gate", None) is not gate:
                raise ProviderError("RUNTIME_DURABLE_GATE_REQUIRED")
            return model
        registry = NeutralModelRegistry(
            self.models, factory, owner_id=self.principal.subject_id, catalogue=self.catalogue)
        source = self.conversation
        chat_id = session.get("summary_model_id") or snapshot.model_id("cheap")
        state = PipelineState(
            targets=tuple(SearchTarget(kb, "knowledge" if turn.context.scope.document_ids else "knowledge_base",
                turn.context.scope.document_ids) for kb in turn.context.scope.knowledge_base_ids),
            chat_model_id=chat_id, query_understand_model_id=chat_id,
            vlm_model_id=dict(snapshot.model_ids).get("vlm", ""),
            rerank_model_id=(session.get("rerank_model_id") or dict(snapshot.model_ids).get("rerank", ""))
                if source.enable_rerank else "",
            max_rounds=session["max_rounds"], enable_rewrite=session["enable_rewrite"],
            embedding_top_k=session["embedding_top_k"], vector_threshold=session["vector_threshold"],
            keyword_threshold=session["keyword_threshold"], rerank_top_k=session["rerank_top_k"],
            rerank_threshold=session["rerank_threshold"], enable_expansion=source.enable_query_expansion)
        options = dict(source.summary)
        options.update(session.get("summary_parameters") or {})
        allowed = ChatOptions.__dataclass_fields__.keys()
        self._runs[turn.run_id] = {"registry": registry, "snapshot": snapshot, "chat_id": chat_id}
        return TurnConfiguration(state, {"policy": self.policy,
            "options": ChatOptions(**{k: v for k, v in options.items() if k in allowed})})

    async def _persist_context(self, state, context):
        async with self.leases.lease() as guard:
            await guard.connection.execute(
                "UPDATE messages SET rendered_content=%s,updated_at=now() "
                "WHERE id=%s AND request_id=%s AND session_id=%s AND tenant_id=%s "
                "AND subject_id=%s AND role='user' AND deleted_at IS NULL",
                (state.user_content, state.user_message_id, context.request_id, context.session_id,
                 self.principal.tenant_id, self.principal.subject_id))

    async def _pipeline(self, turn, progress):
        registry = self._runs[turn.run_id]["registry"]
        return RetrievalPipeline(
            LeasedKnowledgeRepository(self, registry), registry, on_progress=progress,
            persist_sink=self._persist_context)

    async def _generation(self, turn, progress):
        data = self._runs[turn.run_id]
        registry = data["registry"]
        bindings = {}
        roles = ("cheap", "expensive") if self.policy.mode == "DYNAMIC" else (
            ("cheap",) if self.policy.mode == "CHEAP_ONLY" else
            ("expensive",) if self.policy.mode == "EXPENSIVE_ONLY" else ())
        ids = dict(data["snapshot"].model_ids)
        for role in roles:
            if ids.get(role):
                model = await registry.get(ids[role], turn.context)
                config = model.config
                bindings[role] = RoleBinding(role, config.provider, config.model,
                    config.endpoint_url or config.base_url, model)
        if self.policy.mode == "OFF":
            baseline = await registry.chat(data["chat_id"], turn.context)
            config = baseline.config
            bindings["baseline"] = RoleBinding(
                "baseline", config.provider, config.model, config.endpoint_url or config.base_url, baseline)
        return GenerationCoordinator(
            bindings, self.store, self.turns, lifecycle=LeasedLifecycle(self, turn),
            estimator=self.estimator, on_progress=progress)

    async def accept_turn(self, request, trusted_principal):
        return await self.ordinary.accept_turn(request, trusted_principal)

    async def validate_run(self, session_id, assistant_message_id, trusted_principal):
        return await self.ordinary.validate_run(session_id, assistant_message_id, trusted_principal)

    def replay(self, accepted, principal):
        self._owner(principal)
        return self.ordinary.replay(accepted, principal)

    async def drain_background(self):
        await self.ordinary.drain_background()
        await self.memory.drain_background()

    async def list_knowledge_bases(self, principal):
        self._owner(principal)
        return await self.documents.list_knowledge_bases(principal)

    async def create_knowledge_base(self, principal, name):
        self._owner(principal)
        return await self.documents.create_knowledge_base(principal, name)

    async def list_documents(self, principal, knowledge_base_id):
        self._owner(principal)
        return await self.documents.list_documents(principal, knowledge_base_id)

    async def upload_document(self, principal, knowledge_base_id, filename, content_type, source):
        self._owner(principal)
        return await self.documents.upload_document(
            principal, knowledge_base_id, filename, content_type, source)

    async def read_source_chunk(self, principal, chunk_id):
        self._owner(principal)
        return await self.documents.read_source_chunk(principal, chunk_id)

    async def update_knowledge_base_consent(self, principal, knowledge_base_id, cloud_allowed):
        self._owner(principal)
        async with self.leases.lease() as guard:
            await self.turns._resources(guard, (knowledge_base_id,), ())
            await guard.connection.execute(
                "UPDATE knowledge_bases SET cloud_allowed=%s,updated_at=now() "
                "WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL",
                (cloud_allowed, principal.tenant_id, knowledge_base_id))
        return {"id": knowledge_base_id, "cloud_allowed": cloud_allowed}

