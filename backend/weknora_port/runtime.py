"""Explicit isolated application assembly; imports never start infrastructure.

Tencent/WeKnora3e8b0bfc stage mechanisms are reused rather than copied. The
application supplies verified leases, principal, source conversation settings
and ordinary provider configuration. Historical budget authorities are not used.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from typing import Any, Mapping
import inspect, json, uuid, hashlib, asyncio

from .crypto import SecretCipher
from .models.types import ProviderError, ChatOptions, CallContext, TrustedScope
from .model_registry import SourceCatalogue, NeutralModelRegistry, role_snapshot
from .generation import GenerationCoordinator, RouterPolicy
from .generation.types import RoleBinding
from .generation.estimator import LocalTextTokenEstimator
from .retrieval.pipeline import PipelineState, RetrievalPipeline
from .retrieval.search import SearchTarget
from .service import RuntimeService, TurnConfiguration, OrdinaryMemoryLoader
from .conversation import OrdinaryHistoryService, MemoryService
from .storage.configuration import PostgresModelRoleStore, ModelConfigurationError
from .storage.models import PostgresModelStore
from .storage.turns import PostgresTurnRepository
from .storage.generation import PostgresGenerationLifecycle
from .storage.memory import PostgresMemoryRepository
from .storage.hybrid import PostgresKnowledgeRepository


@dataclass(frozen=True)
class ConversationSettings:
    """Values resolved from fixed source config/config.yaml conversation.

    Saved ordinary settings are an explicit local compatibility adapter, resolved
    per source field (nil/zero are not interchangeable). Request model IDs are
    owner/type validated; P5 role bindings and router mode remain server-owned.
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
    source_evidence_mode: str = 'off'
    rerank_context_enabled: bool = False
    summary: Mapping[str, Any] = field(default_factory=lambda: {
        "temperature": .3, "max_completion_tokens": 1024})

    fallback_strategy: str = 'model'
    fallback_response: str = 'Sorry, I am unable to answer this question.'
    fallback_prompt_id: str = 'default_fallback_prompt'
    fallback_prompt: str | None = None

    generate_summary_prompt_id: str = 'default_summary'
    generate_summary_prompt: str | None = None  # already-resolved server text, not YAML
    language_locale: str = ''  # injected startup override; empty permits HTTP locale
    rewrite_prompt_system: str | None = None
    rewrite_prompt_user: str | None = None
    query_understand_model_id: str = ''
    intent_system_prompts: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, configuration, *, prompt_templates=None):
        from .conversation_configuration import resolve_startup_templates
        row = resolve_startup_templates(configuration['conversation'], prompt_templates)
        names = cls.__dataclass_fields__.keys()
        values = {k: row[k] for k in names if k in row and row[k] is not None}
        if 'language_locale' in configuration:
            values['language_locale'] = configuration['language_locale'] or ''
        return cls(**values)


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

    async def authorize(self, call):
        app = self.application
        scope = call.context.scope
        if scope.owner_id != app.principal.subject_id:
            raise ProviderError('MODEL_ORIGIN_KB_ACCESS_DENIED')
        if call.context.cancelled is not None and call.context.cancelled.is_set():
            raise ProviderError('MODEL_CALL_CANCELLED')
        if call.capability in ('chat', 'vision') and (type(call.output_budget) is not int or call.output_budget <= 0):
            raise ProviderError('MODEL_OUTPUT_HARD_LIMIT_REQUIRED')
        from .source_facts import operation_slot
        call_id = hashlib.sha256(json.dumps([app.principal.tenant_id, scope.owner_id,
            operation_slot(call.context.request_id, call.capability).identity()],
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        kbs = set(scope.knowledge_base_ids) | set(call.context.required_origin_kbs)
        async with app.leases.lease() as guard:
            async with guard.connection.transaction():
                rows = await (await guard.connection.execute(
                    'SELECT id,cloud_allowed FROM knowledge_bases WHERE tenant_id=%s AND id=ANY(%s) AND deleted_at IS NULL',
                    (app.principal.tenant_id, list(kbs)))).fetchall()
                if not kbs or {r['id'] for r in rows} != kbs:
                    raise ProviderError('MODEL_ORIGIN_KB_ACCESS_DENIED')
                models = await (await guard.connection.execute(
                    "SELECT source FROM models WHERE tenant_id=%s AND subject_id=%s AND id=%s AND status='active' AND deleted_at IS NULL",
                    (app.principal.tenant_id, app.principal.subject_id, call.model_id))).fetchall()
                if len(models) != 1:
                    raise ProviderError('MODEL_NOT_FOUND')
                if models[0]['source'] != 'local':
                    if not app.cloud_allowed:
                        raise ProviderError('MODEL_CLOUD_DISABLED')
                    if not all(r['cloud_allowed'] for r in rows):
                        raise ProviderError('MODEL_ORIGIN_KB_EGRESS_DENIED')
                if scope.document_ids:
                    docs = await (await guard.connection.execute(
                        'SELECT id FROM knowledges WHERE tenant_id=%s AND id=ANY(%s) AND knowledge_base_id=ANY(%s) AND deleted_at IS NULL',
                        (app.principal.tenant_id, list(scope.document_ids), list(scope.knowledge_base_ids)))).fetchall()
                    if {r['id'] for r in docs} != set(scope.document_ids):
                        raise ProviderError('MODEL_DOCUMENT_ACCESS_DENIED')
                # Reuse only the old operation identity, never its account balance.
                await guard.connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                                               ('weknora-call:'+call_id,))
                tables = await (await guard.connection.execute(
                    "SELECT to_regclass('port_call_slots') AS slots")).fetchall()
                if tables and tables[0]['slots']:
                    old = await (await guard.connection.execute(
                        'SELECT call_id FROM port_call_slots WHERE call_id=%s', (call_id,))).fetchall()
                    if old:
                        raise ProviderError('MODEL_CALL_ID_REUSE_DENIED')
                # A durable claim, not a Token reservation. An interrupted call
                # remains UNKNOWN and the same operation is never resent.
                claimed = await (await guard.connection.execute(
                    'INSERT INTO port_provider_calls(tenant_id,subject_id,call_id,request_id,capability,session_id,provider,model,model_id,wire_sha256) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING request_id',
                    (app.principal.tenant_id, scope.owner_id, call_id, call.context.request_id, call.capability,
                     call.context.session_id, call.provider, call.model, call.model_id, call.wire_sha256))).fetchall()
                if not claimed:
                    raise ProviderError('MODEL_CALL_ID_REUSE_DENIED')

    async def finish(self, call, *, outcome, usage):
        from dataclasses import asdict
        app = self.application
        try:
            async with asyncio.timeout(10):
                async with app.leases.lease() as guard:
                    rows = await (await guard.connection.execute(
                        'UPDATE port_provider_calls SET outcome=%s,usage=%s::jsonb,finished_at=now() '
                        'WHERE tenant_id=%s AND subject_id=%s AND request_id=%s AND capability=%s AND wire_sha256=%s AND finished_at IS NULL RETURNING request_id',
                        (outcome, json.dumps(asdict(usage)), app.principal.tenant_id,
                         call.context.scope.owner_id, call.context.request_id, call.capability, call.wire_sha256))).fetchall()
                    return len(rows) == 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Preserve the durable UNKNOWN claim and the successful answer.
            # Ordinary mode preserves the answer; bounded mode requires True.
            import logging
            logging.getLogger(__name__).error('PROVIDER_USAGE_RECORD_FAILED request_id=%s error=%s',
                                             call.context.request_id, type(exc).__name__)
            return False


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
        if name not in ("hybrid_search", "get_chunks", "get_chunk_children", "get_faq_answers", "get_source_unit"):
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
            result = await super().run_turn(turn)
            succeeded=False
            if result is not None:
                rows=await self.application._rows("SELECT status,error_code FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND run_id=%s",
                    (self.application.principal.tenant_id,self.application.principal.subject_id,turn.session_id,turn.run_id))
                succeeded=len(rows)==1 and rows[0]['status']=='completed' and not rows[0]['error_code']
            if succeeded:
                try:
                    await self.application.record_turn_memory(turn,result)
                except Exception as exc:
                    self.application.memory_hook_errors.append(type(exc).__name__)
            return result
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
                 estimator=None, tokenizer_asset=None, memory_enqueuer=None, permissions=None, costs=None, catalogue=None, local_service=None,token_authority=None,cloud_allowed=False):
        self.leases, self.principal = leases, principal
        self.store, self.documents = store, documents
        self.cipher, self.catalogue = cipher, catalogue or SourceCatalogue()
        self.model_factory, self.permissions, self.costs = model_factory, permissions, costs
        self.token_authority=None  # P31: legacy argument never controls production sends
        self.cloud_allowed=cloud_allowed
        self.local_service=local_service
        self.conversation = conversation or ConversationSettings()
        self.policy = policy or RouterPolicy()
        self.estimator = estimator if estimator is not None else LocalTextTokenEstimator(tokenizer_asset)
        self.models = PostgresModelStore(leases, principal, self.catalogue, cipher=cipher)
        self.turns = PostgresTurnRepository(leases, principal)
        self.memory_repository = LeasedMemoryRepository(leases, principal)
        from .memory_runtime import RuntimeMemoryModels
        from .conversation.management import MemoryManager
        from .conversation.maintenance import MemoryMaintenance, embeddable_text
        from .conversation.extraction import ExtractionWorker
        from .conversation.topics import TopicService
        from .storage.memory_queue import RedisMemoryExtractionQueue
        self.memory_models = RuntimeMemoryModels(self)
        self.memory = MemoryService(self.memory_repository, self.memory_models)
        async def after_memory_write(scope, cfg, item):
            if cfg.embedding_model_id:
                vector=tuple(await asyncio.wait_for(self.memory_models.embed_query(
                    scope,cfg.embedding_model_id,embeddable_text(item),operation_id='memory-item/'+item.id+'/embedding'),10))
                if vector:
                    await self.memory_repository.upsert_item_embedding(scope,item,cfg.embedding_model_id,vector)
        self.memory_manager = MemoryManager(self.memory_repository,self.memory,after_write=after_memory_write)
        self.memory_maintenance = MemoryMaintenance(self.memory_repository,self.memory_manager,self.memory_models)
        self.memory_topics = TopicService(self.memory_repository,self.memory_manager,self.memory_models)
        client=getattr(store,'client',None)
        if memory_enqueuer is None and callable(getattr(client,'eval',None)) and callable(getattr(client,'pipeline',None)):
            memory_enqueuer=RedisMemoryExtractionQueue(client,store.prefix+':memory',principal)
        self.memory_enqueuer=memory_enqueuer
        self.memory_worker=ExtractionWorker(self.memory_repository,self.memory_manager,self.memory_models,
            memory_enqueuer,self.memory_topics,self.memory_maintenance)
        if isinstance(memory_enqueuer,RedisMemoryExtractionQueue):memory_enqueuer.worker=self.memory_worker
        self.memory_hook_errors=[]
        self.history = OrdinaryHistoryService(self.memory_repository)
        self._runs = {}
        if documents.asset_processor is None:
            from .image_multimodal import SourceImageProcessor
            documents.asset_processor=SourceImageProcessor(self)
        if documents.post_processor is None:
            from .document_profile import SourceDocumentPostprocessor
            documents.post_processor = SourceDocumentPostprocessor(self)
        from .image_tasks import ImageTaskQueue
        self.image_tasks=ImageTaskQueue(leases,principal,documents,self.models,self.catalogue)
        documents.image_tasks=self.image_tasks
        self.ordinary = BoundOrdinaryService(
            self, self.turns, store, self.history, OrdinaryMemoryLoader(self.memory, self.memory_models.origin_knowledge_bases),
            configuration=self._configuration, pipeline_factory=self._pipeline,
            generation_factory=self._generation)

    async def _make_model(self,config,key,gate):
        from .models.local_ollama import local_connection_config
        from .models.transport import validate_endpoint,endpoint_urls,AsyncHTTPTransport
        from .models.provider import ProviderModel
        effective=config
        execution = getattr(self, 'execution', None)
        if execution is not None:
            execution.install_gate(gate)
        if config.source=='local':
            if self.local_service is None:raise ProviderError('MODEL_LOCAL_SERVICE_UNAVAILABLE')
            effective=local_connection_config(config,self.local_service.base_url,allow_pull=self.local_service.allow_pull)
        model=self.model_factory(effective,key,gate)
        model=await model if inspect.isawaitable(model) else model
        if getattr(getattr(model,'transport',None),'gate',None) is not gate:
            raise ProviderError('RUNTIME_DURABLE_GATE_REQUIRED')
        if getattr(model,'config',None)!=effective:raise ProviderError('MODEL_FACTORY_IDENTITY_MISMATCH')
        if execution is not None:
            execution.observe_model(model)
        capture = getattr(self, 'evidence_capture', None)
        if capture is not None:
            capture.observe_model(model)
        if config.source=='local':
            if not isinstance(model,ProviderModel) or not isinstance(model.transport,AsyncHTTPTransport):
                raise ProviderError('RUNTIME_LOCAL_PROVIDER_REQUIRED')
            try:
                for endpoint in endpoint_urls(effective):validate_endpoint(endpoint,model.transport.allowed_endpoints)
            except BaseException:
                await model.transport.aclose()
                raise
            model.config=config
            model.local_service=self.local_service
        return model

    def registry_for_context(self):
        gate = LeasedSendGate(self)
        async def factory(config, key):
            return await self._make_model(config,key,gate)
        return NeutralModelRegistry(self.models, factory, owner_id=self.principal.subject_id,
                                    catalogue=self.catalogue)

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
                "SELECT m.id,m.request_id,m.role,m.content,m.created_at,m.knowledge_references,m.agent_steps,m.is_completed,m.turn_status,"
                "m.turn_error_code,m.knowledge_base_scope,m.document_scope,"
                "g.finish_reason,COALESCE(g.objective,'[]'::jsonb) AS generation_issues FROM messages m "
                "LEFT JOIN LATERAL (SELECT a.finish_reason,a.objective FROM port_turns t "
                "JOIN generation_runs r ON r.run_id=t.run_id AND r.tenant_id=t.tenant_id "
                "AND r.subject_id=t.subject_id AND r.session_id=t.session_id AND r.message_id=t.assistant_message_id "
                "JOIN generation_attempts a ON a.run_id=r.run_id AND a.tenant_id=r.tenant_id AND a.subject_id=r.subject_id "
                "WHERE t.tenant_id=m.tenant_id AND t.subject_id=m.subject_id AND t.session_id=m.session_id "
                "AND t.run_id=m.request_id AND t.assistant_message_id=m.id AND t.status='completed' "
                "AND m.role='assistant' AND m.is_completed=true AND m.turn_status NOT IN ('error','cancelled') "
                "AND a.status IN ('COMPLETED','TRUNCATED') ORDER BY a.ordinal DESC LIMIT 1) g ON true "
                "WHERE m.tenant_id=%s AND m.subject_id=%s AND m.session_id=%s AND m.deleted_at IS NULL "
                "ORDER BY m.created_at,m.id", (principal.tenant_id, principal.subject_id, session_id))).fetchall()

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
            return await self._make_model(config,key,gate)
        registry = NeutralModelRegistry(
            self.models, factory, owner_id=self.principal.subject_id, catalogue=self.catalogue)
        from .conversation_configuration import resolve_saved_conversation, resolve_language, resolved_chat_options
        from .retrieval.pipeline import DEFAULT_REWRITE_SYSTEM, DEFAULT_REWRITE_USER, DEFAULT_CONTEXT_TEMPLATE
        from .generation.context import DEFAULT_SYSTEM_PROMPT
        source = self.conversation
        effective = resolve_saved_conversation(source, session)
        from .model_selection import resolve_chat_model_id
        context_configuration=session.get('context_config') or {}
        if isinstance(context_configuration,str):context_configuration=json.loads(context_configuration)
        profile_id=(context_configuration.get('conversation') or {}).get('model_id','')
        allowed=set(turn.context.scope.knowledge_base_ids)
        ordered=tuple(dict.fromkeys(turn.request.knowledge_base_scope or turn.context.scope.knowledge_base_ids))
        if set(ordered)!=allowed:raise ProviderError('TURN_KB_SCOPE_DENIED')
        async def get_kb(id):
            if id not in allowed:raise ProviderError('TURN_KB_SCOPE_DENIED')
            rows=await self._rows('SELECT id,summary_model_id FROM knowledge_bases WHERE tenant_id=%s AND id=ANY(%s) AND deleted_at IS NULL',
                (self.principal.tenant_id,[id]))
            return rows[0] if rows else None
        async def get_model(id):
            row,_,_=await self.models._load(id)
            return {name:row[name] for name in ('id','type','source')}
        async def list_models():
            # Source repository List has no ORDER BY; do not invent role priority.
            rows=await self._rows("SELECT id FROM models WHERE tenant_id=%s AND subject_id=%s AND status='active' AND deleted_at IS NULL",
                (self.principal.tenant_id,self.principal.subject_id))
            return [await get_model(row['id']) for row in rows]
        chat_id=await resolve_chat_model_id(turn.request.summary_model_id,profile_id,ordered,get_kb,get_model,list_models)
        self._runs[turn.run_id] = {"registry": registry, "snapshot": snapshot, "chat_id": chat_id}
        baseline = await registry.chat(chat_id, turn.context)
        summary = effective['summary']
        state = PipelineState(
            targets=tuple(SearchTarget(kb, "knowledge" if turn.context.scope.document_ids else "knowledge_base",
                turn.context.scope.document_ids) for kb in turn.context.scope.knowledge_base_ids),
            chat_model_id=chat_id, query_understand_model_id=effective['query_understand_model_id'] or chat_id,
            chat_model_supports_vision=baseline.config.supports_vision,
            vlm_model_id=dict(snapshot.model_ids).get('vlm', ''),
            rerank_model_id=(effective['rerank_model_id'] or dict(snapshot.model_ids).get('rerank', ''))
                if effective['enable_rerank'] else '',
            max_rounds=effective['max_rounds'], enable_rewrite=effective['enable_rewrite'],
            embedding_top_k=effective['embedding_top_k'], vector_threshold=effective['vector_threshold'],
            keyword_threshold=effective['keyword_threshold'], rerank_top_k=effective['rerank_top_k'],
            rerank_threshold=effective['rerank_threshold'], enable_expansion=effective['enable_query_expansion'],
            source_evidence_mode=effective['source_evidence_mode'],
            rerank_context_enabled=effective['rerank_context_enabled'],
            language=resolve_language(source.language_locale, turn.request.accept_language),
            rewrite_system_prompt=DEFAULT_REWRITE_SYSTEM if effective['rewrite_prompt_system'] is None else effective['rewrite_prompt_system'],
            rewrite_user_prompt=DEFAULT_REWRITE_USER if effective['rewrite_prompt_user'] is None else effective['rewrite_prompt_user'],
            context_template=summary.get('context_template', DEFAULT_CONTEXT_TEMPLATE),
            intent_system_prompts=effective['intent_system_prompts'],
            global_intent_system_prompts=effective['global_intent_system_prompts'])
        return TurnConfiguration(state, {"policy": self.policy,
            "system_prompt": summary.get('prompt', DEFAULT_SYSTEM_PROMPT),
            "options": resolved_chat_options(summary, turn.request.reasoning_effort)},
            fallback={"strategy":source.fallback_strategy,"response":source.fallback_response,
                "prompt":self._fallback_prompt(),"documents":lambda:self._fallback_documents(turn),
                "options":ChatOptions(temperature=summary.get('temperature',0),
                    max_completion_tokens=summary.get('max_completion_tokens',0),thinking=False)})

    def _fallback_prompt(self):
        if self.conversation.fallback_prompt is not None:return self.conversation.fallback_prompt
        from .conversation_configuration import source_prompt_templates
        return next((row.get('content','') for row in source_prompt_templates()['fallback']
            if row.get('id')==self.conversation.fallback_prompt_id),'')

    async def _fallback_documents(self,turn):
        # Source listing capped at 50 enabled documents. Safety wrapper also
        # restricts it to admission's document scope, never broader than retrieval.
        async with self.leases.lease() as guard:
            await self.turns._session(guard,turn.session_id,self.principal)
            rows=await (await guard.connection.execute(
                "SELECT title,file_name,file_type,description FROM knowledges WHERE tenant_id=%s "
                "AND knowledge_base_id=ANY(%s) AND deleted_at IS NULL AND enable_status='enabled' "
                "AND (COALESCE(title,'')<>'' OR COALESCE(file_name,'')<>'') "
                "AND (%s::boolean OR id=ANY(%s)) LIMIT 50",
                (self.principal.tenant_id,list(turn.context.scope.knowledge_base_ids),
                 not bool(turn.context.scope.document_ids),list(turn.context.scope.document_ids)))).fetchall()
        lines=[]
        for row in rows:
            title=row['title'] or row['file_name']
            if not title:continue
            line='- '+title
            if row['file_type']:line+=' ('+row['file_type']+')'
            description=row['description'] or ''
            if description:line+=': '+description[:100]+('...' if len(description)>100 else '')
            lines.append(line+'\n')
        if len(lines)>=50:lines.append('... (showing first 50 documents)\n')
        return ''.join(lines)

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
        repository = LeasedKnowledgeRepository(self, registry)
        async def affinity(ids):
            return await self.memory.document_affinity(turn.memory_context,ids)
        pipeline = RetrievalPipeline(
            repository, registry, on_progress=progress,
            persist_sink=self._persist_context, affinity_resolver=affinity)
        execution = getattr(self, 'execution', None)
        if execution is not None: return execution.observe_pipeline(pipeline, turn)
        capture = getattr(self, 'evidence_capture', None)
        return capture.observe_pipeline(pipeline, turn) if capture is not None else pipeline

    async def _generation(self, turn, progress, *, policy=None):
        policy=policy or self.policy
        data = self._runs[turn.run_id]
        registry = data["registry"]
        bindings = {}
        roles = ("cheap", "expensive") if policy.mode == "DYNAMIC" else (
            ("cheap",) if policy.mode == "CHEAP_ONLY" else
            ("expensive",) if policy.mode == "EXPENSIVE_ONLY" else ())
        ids = dict(data["snapshot"].model_ids)
        for role in roles:
            if ids.get(role):
                model = await registry.get(ids[role], turn.context)
                config = model.config
                bindings[role] = RoleBinding(role, config.provider, config.model,
                    config.endpoint_url or config.base_url, model)
        if policy.mode == "OFF":
            baseline = await registry.chat(data["chat_id"], turn.context)
            config = baseline.config
            bindings["baseline"] = RoleBinding(
                "baseline", config.provider, config.model, config.endpoint_url or config.base_url, baseline)
        return GenerationCoordinator(
            bindings, self.store, self.turns, lifecycle=LeasedLifecycle(self, turn),
            estimator=self.estimator, on_progress=progress)

    async def stop_session(self,principal,session_id,message_id):
        self._owner(principal)
        turn=await self.ordinary.validate_run(session_id,message_id,principal)
        rows=await self.list_messages(principal,session_id)
        message=next((row for row in rows if row['id']==turn.assistant_message_id),None)
        if message and message['is_completed']:
            return {'success':True,'message':'Message already completed'}
        return await self.ordinary.request_stop(session_id,message_id,principal)

    async def accept_turn(self, request, trusted_principal):
        return await self.ordinary.accept_turn(request, trusted_principal)

    async def validate_run(self, session_id, assistant_message_id, trusted_principal):
        return await self.ordinary.validate_run(session_id, assistant_message_id, trusted_principal)

    def replay(self, accepted, principal):
        self._owner(principal)
        return self.ordinary.replay(accepted, principal)

    async def record_turn_memory(self,turn,result):
        from .conversation.turns import record_turn_memory
        from .conversation.types import DocumentAffinity
        data=self._runs[turn.run_id]
        references=tuple(DocumentAffinity(r['knowledge_id'],r.get('knowledge_title',''),1,r['knowledge_base_id'])
            for r in result.references if r.get('knowledge_id') and
            r.get('knowledge_base_id') in turn.context.scope.knowledge_base_ids)
        await record_turn_memory(self.memory_worker,turn.memory_context,turn.session_id,
            turn.assistant_message_id,turn.request.content,turn.user_message_id,data['chat_id'],references)

    async def memory_settings(self,principal):
        self._owner(principal)
        from .conversation.types import MemoryContext
        return await self.memory_manager.settings(MemoryContext(principal.tenant_id,principal.subject_id))

    async def update_memory_settings(self,principal,enabled):
        self._owner(principal)
        if type(enabled) is not bool:raise ProviderError('MEMORY_ENABLED_REQUIRED')
        from .conversation.types import MemoryContext
        context=MemoryContext(principal.tenant_id,principal.subject_id)
        await self.memory_manager.set_enabled(context,enabled)
        return await self.memory_manager.settings(context)

    async def confirm_memory(self,principal,item_id):
        self._owner(principal)
        from .conversation.types import MemoryContext
        return await self.memory_manager.confirm(MemoryContext(principal.tenant_id,principal.subject_id),item_id)

    async def reject_memory(self,principal,item_id):
        self._owner(principal)
        from .conversation.types import MemoryContext
        await self.memory_manager.reject(MemoryContext(principal.tenant_id,principal.subject_id),item_id)

    async def create_memory(self,principal,kind,content,importance):
        self._owner(principal)
        from .conversation.types import MemoryContext
        return await self.memory_manager.create(MemoryContext(principal.tenant_id,principal.subject_id),kind,content,importance)

    async def update_memory(self,principal,item_id,content,importance):
        self._owner(principal)
        from .conversation.types import MemoryContext
        return await self.memory_manager.update(MemoryContext(principal.tenant_id,principal.subject_id),item_id,content,importance)

    async def clear_memory(self,principal):
        self._owner(principal)
        from .conversation.types import MemoryContext
        return await self.memory_manager.clear(MemoryContext(principal.tenant_id,principal.subject_id))

    async def list_memory_documents(self,principal,limit=50,offset=0):
        self._owner(principal)
        where='tenant_id=%s AND subject_id=%s AND hits>=2'
        rows=await self._rows('SELECT id,knowledge_id,knowledge_base_id,title,hits,last_used_at FROM memory_doc_affinity WHERE '+where+' ORDER BY hits DESC,last_used_at DESC LIMIT %s OFFSET %s',
            (principal.tenant_id,principal.subject_id,limit,offset))
        total=await self._rows('SELECT count(*) AS n FROM memory_doc_affinity WHERE '+where,(principal.tenant_id,principal.subject_id))
        return rows,total[0]['n']

    async def delete_memory_document(self,principal,affinity_id):
        self._owner(principal)
        from .conversation.management import MemoryWriteError
        async with self.leases.lease() as guard:
            rows=await (await guard.connection.execute('DELETE FROM memory_doc_affinity WHERE tenant_id=%s AND subject_id=%s AND id=%s RETURNING id',
                (principal.tenant_id,principal.subject_id,affinity_id))).fetchall()
            if not rows:raise MemoryWriteError('MEMORY_ITEM_NOT_FOUND')

    async def list_memory(self,principal,status='',limit=50,offset=0):
        self._owner(principal)
        from .conversation.types import MemoryScope
        scope=MemoryScope(principal.tenant_id,principal.subject_id)
        items=await self.memory_repository.list_items(scope,status,limit,offset)
        rows=await self._rows("SELECT count(*) AS n FROM memory_items WHERE tenant_id=%s AND subject_id=%s "
            "AND (%s='' OR status=%s)",(principal.tenant_id,principal.subject_id,status,status))
        return items,rows[0]['n']

    async def list_memory_topics(self,principal,limit=50,offset=0):
        self._owner(principal)
        from .conversation.types import MemoryScope,MemoryConfig
        scope=MemoryScope(principal.tenant_id,principal.subject_id)
        cfg=await self.memory_repository.workspace_config(principal.tenant_id) or MemoryConfig()
        threshold=min(20,cfg.interest_threshold if cfg.interest_threshold>0 else 3)
        rows=await self._rows('SELECT id,topic,aliases,hits,last_seen_at FROM memory_topic_stats WHERE tenant_id=%s AND subject_id=%s AND promoted_at IS NULL ORDER BY hits DESC,last_seen_at DESC LIMIT %s OFFSET %s',
            (scope.tenant_id,scope.subject_id,limit,offset))
        total=await self._rows('SELECT count(*) AS n FROM memory_topic_stats WHERE tenant_id=%s AND subject_id=%s AND promoted_at IS NULL',(scope.tenant_id,scope.subject_id))
        return [{**row,'aliases':row['aliases'] or [],'threshold':threshold} for row in rows],total[0]['n']

    async def promote_memory_topic(self,principal,topic_id):
        self._owner(principal)
        from .conversation.types import MemoryContext
        return await self.memory_topics.promote(MemoryContext(principal.tenant_id,principal.subject_id),topic_id)

    async def delete_memory_topic(self,principal,topic_id):
        self._owner(principal)
        from .conversation.types import MemoryContext
        await self.memory_topics.delete(MemoryContext(principal.tenant_id,principal.subject_id),topic_id)

    async def consolidate_memory(self,principal):
        self._owner(principal)
        from .conversation.types import MemoryContext
        return await self.memory_maintenance.review(MemoryContext(principal.tenant_id,principal.subject_id),force=True)

    async def forget_memory(self,principal,item_id):
        self._owner(principal)
        from .conversation.types import MemoryContext
        await self.memory_manager.delete(MemoryContext(principal.tenant_id,principal.subject_id),item_id)

    async def start_background_workers(self):
        await self.image_tasks.start()
        start=getattr(self.memory_enqueuer,'start',None)
        if callable(start):await start()

    async def stop_background_workers(self):
        await self.image_tasks.stop()
        stop=getattr(self.memory_enqueuer,'stop',None)
        if callable(stop):await stop()

    async def drain_background(self):
        await self.ordinary.drain_background()
        drain=getattr(self.memory_enqueuer,'drain',None)
        if callable(drain):await drain()
        await self.memory.drain_background()

    async def list_knowledge_bases(self, principal):
        self._owner(principal)
        return await self.documents.list_knowledge_bases(principal)

    async def create_knowledge_base(self, principal, name):
        self._owner(principal)
        return await self.documents.create_knowledge_base(principal, name)

    async def register_vision_model(self,principal,provider,model,base_url,api_key,vision_user_declared=False,source=''):
        self._owner(principal)
        from .storage.configuration import ConfiguredRole
        from .image_configuration import _image_wire_supported
        role=ConfiguredRole('vlm',provider.strip(),model.strip(),base_url.strip(),api_key,
                            {'vision_user_declared':bool(vision_user_declared)},source=source)
        config=self.catalogue.resolve(role,'vision-registration-validation')
        if not config.supports_vision or not _image_wire_supported(config):
            raise ProviderError('MODEL_VISION_UNSUPPORTED')
        model_id=await self.models.persist(role)
        row=await self.models.public(model_id)
        row['vision_capability']='USER_DECLARED_UNTESTED' if vision_user_declared and not config.cataloged else 'CATALOG_DECLARED'
        return row

    async def update_image_processing(self,principal,knowledge_base_id,value):
        self._owner(principal)
        import json
        from .image_configuration import effective_image_config,resolve_image_selection
        from .models.types import CallContext,TrustedScope
        kb=await self.documents._kb(principal,knowledge_base_id)
        allowed={'enable_multimodal','ocr_adapter','ocr_model_id','caption_mode',
                 'caption_model_id','description_language','custom_instructions'}
        if set(value)-allowed or ('enable_multimodal' in value and type(value['enable_multimodal']) is not bool):
            raise ValueError('IMAGE_CONFIG_INVALID')
        if value.get('ocr_adapter') in {'tesseract','vlm'}:
            raise ValueError('IMAGE_OCR_ADAPTER_RETIRED')
        if value.get('ocr_model_id'):
            raise ValueError('IMAGE_OCR_MODEL_RETIRED')
        cfg=effective_image_config(kb,value)
        if cfg.get('ocr_adapter') == 'mineru':
            cfg.pop('ocr_model_id',None)
        elif not cfg.get('ocr_adapter'):
            cfg['ocr_adapter']='mineru'
        cfg['enable_multimodal']=value.get('enable_multimodal',bool(cfg.get('enable_multimodal',False)))
        if cfg['enable_multimodal']:
            await resolve_image_selection(kb,value,self.models,self.catalogue,
                CallContext('image-config-validation',TrustedScope(principal.subject_id,(knowledge_base_id,),())))
        async with self.leases.lease(writable_knowledge_base_ids=frozenset({knowledge_base_id})) as guard:
            guard.require_verified()
            rows=await (await guard.connection.execute(
                'UPDATE knowledge_bases SET image_processing_config=%s::jsonb,updated_at=now() '
                'WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL RETURNING id',
                (json.dumps(cfg,ensure_ascii=False),knowledge_base_id,principal.tenant_id))).fetchall()
        if len(rows)!=1:raise LookupError('KNOWLEDGE_BASE_NOT_FOUND')
        return {'id':knowledge_base_id,'image_processing_config':cfg}

    async def list_documents(self, principal, knowledge_base_id):
        self._owner(principal)
        return await self.documents.list_documents(principal, knowledge_base_id)

    async def image_task_status(self,principal,knowledge_base_id,document_id):
        self._owner(principal)
        await self.documents._kb(principal,knowledge_base_id)
        rows=await self._rows(
            'SELECT id,state,attempt,max_retry,error_code,outcomes,selected_config '
            'FROM port_image_tasks WHERE tenant_id=%s AND subject_id=%s '
            'AND knowledge_base_id=%s AND knowledge_id=%s ORDER BY created_at DESC LIMIT 1',
            (principal.tenant_id,principal.subject_id,knowledge_base_id,document_id))
        if not rows:raise LookupError('IMAGE_TASK_NOT_FOUND')
        row=rows[0]
        return {'id':row['id'],'state':row['state'],'attempt':row['attempt'],
            'max_retry':row['max_retry'],'error_code':row['error_code'],
            'selection':row['selected_config']['selection'],'outcomes':row['outcomes']}

    async def cancel_image_task(self,principal,knowledge_base_id,document_id):
        self._owner(principal)
        return await self.image_tasks.cancel(principal,knowledge_base_id,document_id)

    async def upload_document(self, principal, knowledge_base_id, filename, content_type, source,process_overrides=None):
        self._owner(principal)
        return await self.documents.upload_document(
            principal, knowledge_base_id, filename, content_type, source,process_overrides)

    async def read_document(self,principal,knowledge_base_id,document_id):
        self._owner(principal)
        return await self.documents.read_document(principal,knowledge_base_id,document_id)

    async def read_document_asset(self,principal,knowledge_base_id,document_id,*,asset_path,source_version):
        self._owner(principal)
        return await self.documents.read_document_asset(principal,knowledge_base_id,document_id,
            asset_path=asset_path,source_version=source_version)

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
        rows=await self.documents.list_knowledge_bases(principal)
        row=next((row for row in rows if row['id']==knowledge_base_id),None)
        if row is None:raise ProviderError('KNOWLEDGE_BASE_NOT_FOUND')
        return row

