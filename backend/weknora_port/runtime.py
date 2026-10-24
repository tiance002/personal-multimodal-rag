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
from .storage.configuration import ConfiguredRole, PostgresModelRoleStore, ModelConfigurationError
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
    retrieval_strategy_mode: str = field(default='off', kw_only=True)
    source_evidence_mode: str = 'off'
    rerank_context_enabled: bool = False
    rerank_top1_floorless: bool = True  # Owner-approved server default; explicit false remains available.
    bridge_rescue_mode: str = 'off'  # Server-only experiment, no saved-session override.
    shadow_routing_mode: str = field(default='off', kw_only=True)
    summary: Mapping[str, Any] = field(default_factory=lambda: {
        "temperature": .7, "max_completion_tokens": 0})

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

    def __post_init__(self):
        from .retrieval.strategy import validate_strategy_mode
        validate_strategy_mode(self.retrieval_strategy_mode, self.source_evidence_mode)
        if type(self.bridge_rescue_mode) is not str or self.bridge_rescue_mode not in ('off', 'cheap_bridge_v1'):
            raise ValueError('BRIDGE_RESCUE_MODE_INVALID')
        if self.shadow_routing_mode not in ('off','shadow_structure_v1'):
            raise ValueError('SHADOW_ROUTING_MODE_INVALID')

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

    def observe_error(self, call, error):
        observer = getattr(self.application, 'observability', None)
        if observer is not None:
            observer._safe('PROVIDER_ERROR', lambda: observer.provider_error(call, error))

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
        observer = getattr(app, 'observability', None)
        if observer is not None:
            observer._safe('PROVIDER_START', lambda: observer.provider_started(call,
                cloud_consented=bool(rows) and all(row['cloud_allowed'] for row in rows)))

    async def finish(self, call, *, outcome, usage):
        from dataclasses import asdict
        app = self.application
        settled, settlement_error = False, None
        try:
            async with asyncio.timeout(10):
                async with app.leases.lease() as guard:
                    rows = await (await guard.connection.execute(
                        'UPDATE port_provider_calls SET outcome=%s,usage=%s::jsonb,finished_at=now() '
                        'WHERE tenant_id=%s AND subject_id=%s AND request_id=%s AND capability=%s AND wire_sha256=%s AND finished_at IS NULL RETURNING request_id',
                        (outcome, json.dumps(asdict(usage)), app.principal.tenant_id,
                         call.context.scope.owner_id, call.context.request_id, call.capability, call.wire_sha256))).fetchall()
                    settled = len(rows) == 1
                    return settled
        except asyncio.CancelledError:
            settlement_error = 'CANCELLED'
            raise
        except Exception as exc:
            settlement_error = 'PERSISTENCE_FAILED'
            # Preserve the durable UNKNOWN claim and the successful answer.
            # Ordinary mode preserves the answer; bounded mode requires True.
            import logging
            logging.getLogger(__name__).error('PROVIDER_USAGE_RECORD_FAILED request_id=%s error=%s',
                                             call.context.request_id, type(exc).__name__)
            return False
        finally:
            observer = getattr(app, 'observability', None)
            if observer is not None:
                observer._safe('PROVIDER_FINISH', lambda: observer.provider_finished(call, outcome=outcome,
                    usage=usage, settlement_success=settled, settlement_error=settlement_error))


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
        self.tenant_id = application.principal.tenant_id

    async def _tenant(self, owner):
        if owner != self.app.principal.subject_id:
            raise ProviderError("KNOWLEDGE_OWNER_MISMATCH")
        return self.app.principal.tenant_id

    async def get_graph_batches(self, context):
        from .storage.graph import PostgresGraphRepository
        async with self.app.leases.lease() as guard:
            repo = PostgresGraphRepository(guard, self._tenant)
            return await repo.load_graph(context)

    async def get_graph_chunks(self, ids, context):
        async with self.app.leases.lease() as guard:
            repo = PostgresKnowledgeRepository(guard, self._tenant)
            return await repo._read_chunks(ids, context, current=True)

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
        if name not in ("hybrid_search", "get_chunks", "get_current_chunks", "get_first_source_chunks", "get_chunk_children", "get_faq_answers"):
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
        self._bound_active = set()

    async def run_turn(self, turn):
        self._validate(turn)
        key = self._key(turn)
        if turn.replay_only or key in self._active or key in self._bound_active:
            return None
        self._bound_active.add(key)
        trace, result, terminal = None, None, None
        try:
            trace = await self.application._start_turn_trace(turn)
            result = await super().run_turn(turn)
            succeeded=False
            if result is not None:
                rows=await self.application._rows("SELECT status,error_code FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND run_id=%s",
                    (self.application.principal.tenant_id,self.application.principal.subject_id,turn.session_id,turn.run_id))
                terminal = rows[0] if len(rows) == 1 else None
                succeeded=terminal is not None and terminal['status']=='completed' and not terminal['error_code']
            if succeeded:
                try:
                    await self.application.record_turn_memory(turn,result)
                except Exception as exc:
                    self.application.memory_hook_errors.append(type(exc).__name__)
            return result
        finally:
            try:
                observer = getattr(self.application, 'observability', None)
                if trace is not None and observer is not None:
                    if terminal is None:
                        try:
                            rows = await self.application._rows('SELECT status,error_code FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND run_id=%s',
                                (self.application.principal.tenant_id, self.application.principal.subject_id, turn.session_id, turn.run_id))
                            terminal = rows[0] if len(rows) == 1 else None
                        except asyncio.CancelledError:
                            observer._safe('TURN_FINISH', lambda: observer.finish_turn(trace,
                                status='UNKNOWN', error_code='CANCELLED'))
                            raise
                        except Exception:
                            observer._warn('TURN_STATUS_UNAVAILABLE')
                    observer._safe('TURN_FINISH', lambda: observer.finish_turn(trace, result=result,
                        status=terminal['status'] if terminal else 'UNKNOWN', error_code=terminal.get('error_code') if terminal else None))
            finally:
                try:
                    state = self.application._runs.pop(turn.run_id, None)
                    if state is not None:
                        await state["registry"].close()
                finally:
                    self._bound_active.discard(key)


class ApplicationRuntime:
    """Concrete scoped SQL facade + one assembled ordinary RAG chain.

    model_factory(config, key, durable_gate) must return the neutral ProviderModel.
    Its actual transport always uses durable_gate. No real credential or network
    default is inferred. documents is the source DocumentLifecycleService with
    explicit parser/embed/postprocessing dependencies.
    """
    def __init__(self, leases, principal, store, documents, *, model_factory,
                 cipher: SecretCipher, conversation=None, policy=None,
                 estimator=None, tokenizer_asset=None, memory_enqueuer=None, permissions=None, costs=None, catalogue=None, local_service=None,token_authority=None,cloud_allowed=False,observability=None):
        self.leases, self.principal = leases, principal
        self.store, self.documents = store, documents
        self.cipher, self.catalogue = cipher, catalogue or SourceCatalogue()
        self.model_factory, self.permissions, self.costs = model_factory, permissions, costs
        self.token_authority=None  # P31: legacy argument never controls production sends
        self.cloud_allowed=cloud_allowed
        from .observability import LangfuseObservability
        self.observability = observability if observability is not None else LangfuseObservability()
        self.graph_enabled=False  # Explicit development setting only; never inferred from old graph placeholders.
        self.local_service=local_service
        self.conversation = conversation or ConversationSettings()
        self.policy = policy or RouterPolicy()
        self.estimator = estimator if estimator is not None else LocalTextTokenEstimator(tokenizer_asset)
        self.models = PostgresModelStore(leases, principal, self.catalogue, cipher=cipher)
        self.turns = PostgresTurnRepository(leases, principal, generation_defaults=self.conversation.summary)
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
            generation_factory=self._generation, document_clarifier=self._document_scope_clarification)

    async def _start_turn_trace(self, turn):
        observer = getattr(self, 'observability', None)
        if observer is None or not observer.enabled: return None
        try:
            async with self.leases.lease() as guard:
                await self.turns._session(guard, turn.session_id, self.principal)
                await self.turns._resources(guard, turn.context.scope.knowledge_base_ids, turn.context.scope.document_ids)
                rows = await (await guard.connection.execute(
                    'SELECT id,cloud_allowed FROM knowledge_bases WHERE tenant_id=%s AND id=ANY(%s) AND deleted_at IS NULL',
                    (self.principal.tenant_id, list(turn.context.scope.knowledge_base_ids)))).fetchall()
            consented = bool(rows) and {r['id'] for r in rows} == set(turn.context.scope.knowledge_base_ids) and all(r['cloud_allowed'] for r in rows)
            if not consented: observer._warn('TURN_SCOPE_EGRESS_DENIED')
            return observer.begin_turn(turn, cloud_consented=consented)
        except Exception:
            observer._warn('TURN_PERMISSION_UNAVAILABLE')
            return None

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
        from .retrieval.strategy import validate_strategy_mode
        mode = getattr(request, 'retrieval_strategy_mode', None)
        source_mode = request.source_evidence_mode
        # Public candidate selection is explicit; never silently enable its
        # required source profile or accept a partial candidate request.
        if mode == 'query_structure_v1' and source_mode != 'bounded_source_v1':
            raise ValueError('RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE')
        validate_strategy_mode(mode if mode is not None else self.conversation.retrieval_strategy_mode,
            source_mode if source_mode is not None else self.conversation.source_evidence_mode)
        session = str(uuid.uuid4())
        kbs = tuple(sorted(set(request.knowledge_base_scope)))
        docs = tuple(sorted(set(request.document_scope)))
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                await self.turns._resources(guard, kbs, docs)
                defaults = self.conversation
                roles = await self._snapshot(guard)
                saved = {}
                if source_mode is not None:
                    saved["source_evidence_mode"] = source_mode
                if mode is not None:
                    saved["retrieval_strategy_mode"] = mode
                context_config = {"conversation": saved} if saved else {}
                generation = {name: getattr(request,name) for name in ('reasoning_mode','max_output_tokens') if getattr(request,name,None) is not None}
                if generation:
                    from .generation_settings import validate_settings
                    validate_settings(generation.get('reasoning_mode'),generation.get('max_output_tokens'))
                    context_config['generation'] = {**generation,'revision':1}
                await guard.connection.execute(
                    "INSERT INTO sessions(id,tenant_id,subject_id,title,knowledge_base_scope,"
                    "document_scope,max_rounds,enable_rewrite,embedding_top_k,vector_threshold,"
                    "keyword_threshold,rerank_top_k,rerank_threshold,summary_model_id,rerank_model_id,"
                    "context_config,summary_parameters) VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                    (session, principal.tenant_id, principal.subject_id, request.title,
                     json.dumps(kbs), json.dumps(docs), defaults.max_rounds, defaults.enable_rewrite,
                     defaults.embedding_top_k, defaults.vector_threshold, defaults.keyword_threshold,
                     defaults.rerank_top_k, defaults.rerank_threshold, roles.model_id("cheap"),
                     dict(roles.model_ids).get("rerank", ""), json.dumps(context_config), json.dumps(dict(defaults.summary))))
        return {"id": session, "title": request.title, "knowledge_base_scope": list(kbs),
                "document_scope": list(docs)}

    async def list_sessions(self, principal):
        self._owner(principal)
        return await self._rows(
            "SELECT id,title,knowledge_base_scope,document_scope FROM sessions WHERE "
            "tenant_id=%s AND subject_id=%s AND deleted_at IS NULL ORDER BY created_at,id",
            (principal.tenant_id, principal.subject_id))

    async def update_session_document_scope(self, principal, session_id, document_ids):
        self._owner(principal)
        return await self.turns.update_document_scope(session_id,document_ids,principal)

    async def read_generation_settings(self, principal, session_id):
        self._owner(principal)
        from .generation_settings import resolve_settings
        from .conversation_configuration import resolve_saved_conversation
        async with self.leases.lease() as guard:
            session = await self.turns._session(guard, session_id, principal)
        summary = resolve_saved_conversation(self.conversation, session)['summary']
        result = resolve_settings(summary, session)
        result['capabilities'] = await self._answer_generation_capabilities(
            session, result['effective']['max_output_tokens'])
        return result

    async def _answer_generation_capabilities(self, session, maximum):
        from .models.generation_capabilities import describe_generation_capabilities
        from .storage.configuration import ModelConfigurationError
        mode = self.policy.mode
        if mode == 'OFF':
            try:
                scope = session.get('knowledge_base_scope') or ()
                if isinstance(scope, str): scope = json.loads(scope)
                scope = tuple(scope) or ((session['knowledge_base_id'],) if session.get('knowledge_base_id') else ())
                model_id = await self._normal_chat_model_id(session, '', scope)
                _, _, role = await self.models._load(model_id)
                config = self.catalogue.resolve(role, model_id)
                result = describe_generation_capabilities(config, maximum, role=role.role)
            except (ProviderError, ModelConfigurationError) as error:
                code = getattr(error, 'code', str(error))
                if code not in {'CHAT_MODEL_CONFIGURATION_REQUIRED', 'CONFIGURED_CHAT_MODEL_UNAVAILABLE',
                                'MODEL_NOT_FOUND', 'MODEL_UNAVAILABLE', 'MODEL_ID_INVALID'}:
                    raise
                result = describe_generation_capabilities(None, maximum, error_code=code)
            return {**result, 'routing_mode': mode, 'selection_scope': 'selected-model',
                    'candidate_models': [result['selected_model']] if result['selected_model'] else []}
        names = ('cheap', 'expensive') if mode == 'DYNAMIC' else (
            ('cheap',) if mode == 'CHEAP_ONLY' else ('expensive',))
        # This read does not persist model snapshots, decrypt credentials or
        # instantiate a provider. Role identities are prospective until a turn.
        async with self.leases.lease() as guard:
            revision, values = await PostgresModelRoleStore(
                guard, self.principal, self.catalogue, cipher=self.cipher)._stored()
        candidates = []
        for name in names:
            value = values[name]
            if not all(value.get(key) for key in ('provider', 'model', 'base_url')):
                candidates.append(describe_generation_capabilities(None, maximum, error_code='MODEL_ROLE_NOT_CONFIGURED'))
                continue
            parameters = {key: item for key, item in (value.get('parameters') or {}).items()
                          if key not in {'api_key', 'app_secret'}}
            role = ConfiguredRole(**{**value, 'api_key': '', 'parameters': parameters})
            config = self.catalogue.resolve(role, 'configured-role-' + name)
            item = describe_generation_capabilities(config, maximum, role=name)
            item['selected_model']['model_id'] = None
            candidates.append(item)
        result = describe_generation_capabilities(None, maximum)
        states = [item['thinking'] for item in candidates]
        result['thinking'] = 'unsupported' if 'unsupported' in states else (
            'unknown' if 'unknown' in states else 'supported')
        limits = [item['declared_max_output_tokens'] for item in candidates]
        if all(limit is not None for limit in limits):
            result['declared_max_output_tokens'] = min(limits)
            result['capacity_source'] = 'fixed-source-catalog'
        result['error_code'] = next((item['error_code'] for item in candidates if item['error_code']), '')
        return {**result, 'routing_mode': mode, 'selection_scope': 'configured-routing-roles',
                'configuration_revision': revision,
                'candidate_models': [item['selected_model'] for item in candidates if item['selected_model']],
                'candidate_capabilities': candidates}

    async def update_generation_settings(self, principal, session_id, value):
        self._owner(principal)
        from .generation_settings import validate_settings, session_settings
        validate_settings(value.get('reasoning_mode'), value.get('max_output_tokens'))
        if set(value) - {'reasoning_mode', 'max_output_tokens', 'reset'}:
            raise ProviderError('GENERATION_SETTINGS_INVALID')
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                session = await self.turns._session(guard, session_id, principal)
                context = session.get('context_config') or {}
                if isinstance(context, str): context = json.loads(context)
                context = dict(context)
                revision = int(session_settings(session).get('revision', 0)) + 1
                saved = {} if value.get('reset') else {k: value[k] for k in ('reasoning_mode', 'max_output_tokens') if k in value}
                context['generation'] = {**saved, 'revision': revision}
                await guard.connection.execute('UPDATE sessions SET context_config=%s::jsonb,updated_at=now() WHERE id=%s AND tenant_id=%s AND subject_id=%s AND deleted_at IS NULL',
                    (json.dumps(context), session_id, principal.tenant_id, principal.subject_id))
        return await self.read_generation_settings(principal, session_id)

    async def _document_scope_clarification(self, turn, language):
        import re
        query=turn.request.content
        # Only singular document references require an identity. Ordinary
        # questions may legitimately retrieve evidence from several documents.
        reference=re.search(r'(?:这|那)(?:一)?(?:份|个|篇|本)\s*(?:技术)?(?:文档|文件|规范|标准|报告|课件|PPT)|(?:上述|该)\s*(?:技术)?(?:文档|文件|规范|标准|报告|课件|PPT)|\b(?:this|that|above)\s+(?:(?:technical|current)\s+)?(?:document|file|specification|standard|report|presentation|PPT)\b',query,re.I)
        documents=turn.context.scope.document_ids
        if not reference or len(documents)==1:return None
        if len(documents)>1 and re.search(r'比较|对比|差异|\bcompar(?:e|ing|ison)\b|\bdifferences?\b',query,re.I):
            return None
        async with self.leases.lease() as guard:
            await self.turns._session(guard,turn.session_id,self.principal)
            await self.turns._resources(guard,turn.context.scope.knowledge_base_ids,documents)
            rows=await (await guard.connection.execute(
                "SELECT id,knowledge_base_id,title,file_name FROM knowledges WHERE tenant_id=%s "
                "AND knowledge_base_id=ANY(%s) AND deleted_at IS NULL AND enable_status='enabled' "
                "AND (%s::boolean OR id=ANY(%s)) ORDER BY knowledge_base_id,title,id",
                (self.principal.tenant_id,list(turn.context.scope.knowledge_base_ids),not bool(documents),list(documents)))).fetchall()
        if len(rows)<=1:return None
        # A distinctive literal or an explicitly named title establishes
        # identity. Short labels and ordinary words require a naming marker;
        # incidental question text must not silently resolve ambiguity.
        def title_matches(value):
            if not value or not value.strip():return False
            name=value.strip()
            bounded=r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])'
            if not re.search(bounded,query,re.I):return False
            for left,right in (('《','》'),('「','」'),('“','”'),('"','"'),("'","'"),('‘','’')):
                if re.search(re.escape(left)+r'\s*'+re.escape(name)+r'\s*'+re.escape(right),query,re.I):return True
            kind=r'(?:文档|文件|规范|标准|报告|课件|PPT|\b(?:document|file|specification|standard|report|presentation)\b)'
            if re.search(kind+r'\s*(?:名为|名称为|叫做|named\b|called\b|[:：])\s*'+bounded,query,re.I):return True
            # Direct labels such as "document B" are explicit identities too.
            # Case matters here: a lower-case ordinary word after "document"
            # (for example "document date") is not a naming declaration.
            if name[0].isupper() and re.search(r'(?i:'+kind+r')\s*'+bounded,query):return True
            if re.fullmatch(r'[\u3400-\u9fff]+',name) and re.search(kind+r'\s+'+bounded,query,re.I):return True
            # Names with numbers/punctuation and multiword Latin titles can
            # stand alone. Plain single words/CJK phrases need explicit naming.
            return bool(re.search(r'[0-9._—-]',name) or
                        len(re.findall(r'[A-Za-z]+',name))>=2)
        named={row['id'] for row in rows if any(title_matches(value)
            for value in (row.get('title'),row.get('file_name')))}
        if len(named)==1:return None
        from .retrieval.query import format_document_clarification
        return format_document_clarification(rows,language)

    async def list_messages(self, principal, session_id):
        self._owner(principal)
        async with self.leases.lease() as guard:
            await self.turns._session(guard, session_id, principal)
            rows = await (await guard.connection.execute(
                "SELECT m.id,m.request_id,m.role,m.content,m.created_at,m.knowledge_references,m.agent_steps,m.is_completed,m.turn_status,"
                "m.turn_error_code,m.knowledge_base_scope,m.document_scope,"
                "g.finish_reason,COALESCE(g.objective,'[]'::jsonb) AS generation_issues,"
                "(SELECT t.request_configuration->'generation_settings' FROM port_turns t WHERE t.tenant_id=m.tenant_id AND t.subject_id=m.subject_id AND t.session_id=m.session_id AND t.run_id=m.request_id LIMIT 1) AS generation_settings FROM messages m "
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
        from .streaming.replay import project_final_quality
        return [project_final_quality(row) for row in rows]

    async def _snapshot(self, guard):
        configuration = PostgresModelRoleStore(
            guard, self.principal, self.catalogue, cipher=self.cipher)
        return await role_snapshot(configuration, self.models)

    async def _normal_chat_model_id(self, session, requested_model_id, ordered):
        """Share normal scoped selection with read-only capability guidance."""
        from .model_selection import resolve_chat_model_id
        context_configuration = session.get('context_config') or {}
        if isinstance(context_configuration, str): context_configuration = json.loads(context_configuration)
        profile_id = (context_configuration.get('conversation') or {}).get('model_id', '')
        allowed = set(ordered)
        async def get_kb(id):
            if id not in allowed: raise ProviderError('TURN_KB_SCOPE_DENIED')
            rows = await self._rows('SELECT id,summary_model_id FROM knowledge_bases WHERE tenant_id=%s AND id=ANY(%s) AND deleted_at IS NULL',
                (self.principal.tenant_id, [id]))
            return rows[0] if rows else None
        async def get_model(id):
            row, _, _ = await self.models._load(id)
            return {name: row[name] for name in ('id', 'type', 'source')}
        async def list_models():
            # Source repository List has no ORDER BY; do not invent role priority.
            rows = await self._rows("SELECT id FROM models WHERE tenant_id=%s AND subject_id=%s AND status='active' AND deleted_at IS NULL",
                (self.principal.tenant_id, self.principal.subject_id))
            return [await get_model(row['id']) for row in rows]
        return await resolve_chat_model_id(requested_model_id, profile_id, ordered, get_kb, get_model, list_models)

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
        from .generation_settings import resolve_settings, apply_settings
        from .retrieval.pipeline import DEFAULT_REWRITE_SYSTEM, DEFAULT_REWRITE_USER, DEFAULT_CONTEXT_TEMPLATE
        from .generation.context import EN_SYSTEM_PROMPT
        source = self.conversation
        effective = resolve_saved_conversation(source, session)
        allowed=set(turn.context.scope.knowledge_base_ids)
        ordered=tuple(dict.fromkeys(turn.request.knowledge_base_scope or turn.context.scope.knowledge_base_ids))
        if set(ordered)!=allowed:raise ProviderError('TURN_KB_SCOPE_DENIED')
        chat_id=await self._normal_chat_model_id(session, turn.request.summary_model_id, ordered)
        self._runs[turn.run_id] = {"registry": registry, "snapshot": snapshot, "chat_id": chat_id}
        baseline = await registry.chat(chat_id, turn.context)
        summary = effective['summary']
        answer_settings = getattr(turn.request,'generation_settings',{}) or resolve_settings(summary, session,
            reasoning_mode=getattr(turn.request,'reasoning_mode',None), max_output_tokens=getattr(turn.request,'max_output_tokens',None),
            reasoning_effort=turn.request.reasoning_effort)
        answer_options = apply_settings(resolved_chat_options(summary, ''), answer_settings)
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
            retrieval_strategy_mode=effective['retrieval_strategy_mode'],
            rerank_context_enabled=effective['rerank_context_enabled'],
            rerank_top1_floorless=source.rerank_top1_floorless,
            bridge_rescue_mode=source.bridge_rescue_mode,
            bridge_cheap_model_id=snapshot.model_id('cheap') if (source.bridge_rescue_mode != 'off'
                or effective['retrieval_strategy_mode'] == 'query_structure_v1') else '',
            graph_enabled=self.graph_enabled,
            language=resolve_language(source.language_locale, turn.request.accept_language),
            rewrite_system_prompt=DEFAULT_REWRITE_SYSTEM if effective['rewrite_prompt_system'] is None else effective['rewrite_prompt_system'],
            rewrite_user_prompt=DEFAULT_REWRITE_USER if effective['rewrite_prompt_user'] is None else effective['rewrite_prompt_user'],
            context_template=summary.get('context_template', DEFAULT_CONTEXT_TEMPLATE),
            intent_system_prompts=effective['intent_system_prompts'],
            global_intent_system_prompts=effective['global_intent_system_prompts'])
        observer = getattr(self, 'observability', None)
        if observer is not None:
            observer._safe('CONFIGURATION', lambda: observer.annotate(router_mode=self.policy.mode,
                **{name: getattr(state, name) for name in ('retrieval_strategy_mode', 'source_evidence_mode',
                    'enable_rewrite', 'enable_expansion', 'rerank_top1_floorless', 'rerank_context_enabled',
                    'bridge_rescue_mode', 'chat_model_id', 'query_understand_model_id', 'rerank_model_id', 'vlm_model_id')}))
        return TurnConfiguration(state, {"policy": self.policy,
            "system_prompt": summary.get('prompt', EN_SYSTEM_PROMPT),
            "options": answer_options, "generation_settings": answer_settings,
            "shadow_routing_mode":source.shadow_routing_mode},
            fallback={"strategy":source.fallback_strategy,"response":source.fallback_response,
                "prompt":self._fallback_prompt(),"documents":lambda:self._fallback_documents(turn),
                "options":answer_options})

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
                "SELECT title,file_name,file_type FROM knowledges WHERE tenant_id=%s "
                "AND knowledge_base_id=ANY(%s) AND deleted_at IS NULL AND enable_status='enabled' "
                "AND (COALESCE(title,'')<>'' OR COALESCE(file_name,'')<>'') "
                "AND (%s::boolean OR id=ANY(%s)) LIMIT 50",
                (self.principal.tenant_id,list(turn.context.scope.knowledge_base_ids),
                 not bool(turn.context.scope.document_ids),list(turn.context.scope.document_ids)))).fetchall()
        lines=[]
        for row in rows:
            title=row['title'] or row['file_name']
            if not title:continue
            # Titles and file types are catalog data, never read source facts.
            # JSON quoting keeps embedded newlines from becoming evidence lines.
            lines.append('- '+json.dumps({'title':title,'file_type':row['file_type'] or ''},
                ensure_ascii=False)+'\n')
        if not lines:return ''
        if len(lines)>=50:lines.append('... (showing first 50 documents)\n')
        return 'Document catalog metadata only (titles and file types; contents not read):\n'+''.join(lines)

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
        try:
            await self.image_tasks.stop()
            stop=getattr(self.memory_enqueuer,'stop',None)
            if callable(stop):await stop()
        finally:
            observer = getattr(self, 'observability', None)
            if observer is not None: await observer.shutdown()

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
        allowed={'enable_multimodal','ocr_adapter','ocr_model_id','caption_mode','reasoning_mode','max_output_tokens',
                 'caption_model_id','description_language','custom_instructions'}
        if set(value)-allowed or ('enable_multimodal' in value and type(value['enable_multimodal']) is not bool):
            raise ValueError('IMAGE_CONFIG_INVALID')
        if value.get('ocr_adapter') in {'tesseract','vlm'}:
            raise ValueError('IMAGE_OCR_ADAPTER_RETIRED')
        if value.get('ocr_model_id'):
            raise ValueError('IMAGE_OCR_MODEL_RETIRED')
        from .generation_settings import validate_settings
        validate_settings(value.get('reasoning_mode'),value.get('max_output_tokens'))
        cfg=effective_image_config(kb,value)
        previous=kb.get('image_processing_config') or {}
        if isinstance(previous,str):previous=json.loads(previous)
        cfg['config_version']=int(previous.get('config_version',1))+1
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
                'WHERE id=%s AND tenant_id=%s AND deleted_at IS NULL AND COALESCE(image_processing_config,\'{}\'::jsonb)=%s::jsonb RETURNING id',
                (json.dumps(cfg,ensure_ascii=False),knowledge_base_id,principal.tenant_id,json.dumps(previous)))).fetchall()
        if len(rows)!=1:raise ProviderError('IMAGE_CONFIG_CONFLICT')
        return {'id':knowledge_base_id,'image_processing_config':cfg}

    async def read_image_processing(self,principal,knowledge_base_id):
        self._owner(principal)
        from .image_configuration import effective_image_config
        from .models.generation_capabilities import describe_generation_capabilities
        kb=await self.documents._kb(principal,knowledge_base_id)
        cfg=effective_image_config(kb)
        maximum=cfg.get('max_output_tokens',0) or 5000
        model_id=cfg.get('caption_model_id') or ''
        config=None
        role=None
        error_code='IMAGE_CAPTION_VISION_MODEL_REQUIRED'
        if model_id:
            try:
                _,_,role=await self.models._load(model_id)
                config=self.catalogue.resolve(role,model_id)
                error_code=''
            except (ProviderError,ModelConfigurationError) as error:
                error_code=getattr(error,'code',str(error))
                if error_code not in {'MODEL_NOT_FOUND','MODEL_UNAVAILABLE','MODEL_ID_INVALID'}:
                    raise
        capabilities=describe_generation_capabilities(config,maximum,role=role.role if role else '',
            error_code=error_code)
        return {'id':knowledge_base_id,'image_processing_config':cfg,
                'capabilities':{**capabilities,'selection_scope':'explicit-caption-model',
                                'routing_mode':'OFF'},
                'effective_generation':{'reasoning_mode':cfg.get('reasoning_mode','quick'),
                                        'max_output_tokens':maximum}}

    async def retry_image_item(self,principal,knowledge_base_id,document_id,value):
        self._owner(principal)
        kb=await self.documents._kb(principal,knowledge_base_id)
        return await self.image_tasks.create_selected_retry(principal,kb,document_id,**value)

    async def list_documents(self, principal, knowledge_base_id):
        self._owner(principal)
        return await self.documents.list_documents(principal, knowledge_base_id)

    async def image_task_status(self,principal,knowledge_base_id,document_id):
        self._owner(principal)
        await self.documents._kb(principal,knowledge_base_id)
        rows=await self._rows(
            'SELECT id,state,attempt,max_retry,error_code,outcomes,selected_config,source_version '
            'FROM port_image_tasks WHERE tenant_id=%s AND subject_id=%s '
            'AND knowledge_base_id=%s AND knowledge_id=%s ORDER BY created_at DESC LIMIT 1',
            (principal.tenant_id,principal.subject_id,knowledge_base_id,document_id))
        if not rows:raise LookupError('IMAGE_TASK_NOT_FOUND')
        row=rows[0]
        return {'id':row['id'],'state':row['state'],'attempt':row['attempt'],
            'max_retry':row['max_retry'],'error_code':row['error_code'],
            'selection':row['selected_config']['selection'],'outcomes':row['outcomes'],
            'source_version':row['source_version']}

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

