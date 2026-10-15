"""Explicit development assembly. No old database, credentials, migrations or grants inferred."""
from __future__ import annotations
from contextlib import asynccontextmanager
from dataclasses import dataclass,field
from pathlib import Path
from urllib.parse import urlsplit
import os,re,json,uuid

PROHIBITED=frozenset({'7691227493754040358','7689510505384681511','7696038373595934758'})
class DevelopmentConfigurationError(RuntimeError):pass

@dataclass(frozen=True)
class DevelopmentSettings:
    database_url:str=field(repr=False)
    system_id:str
    database:str
    redis_url:str=field(repr=False)
    redis_run_id:str
    redis_prefix:str
    storage:Path
    secret_key_file:Path=field(repr=False)
    tokenizer:Path|None=None
    router_mode:str='DYNAMIC'
    local_url:str=''
    origins:tuple[str,...]=('http://127.0.0.1:5173','http://localhost:5173')

    def __post_init__(self):
        db=urlsplit(self.database_url);redis=urlsplit(self.redis_url)
        if db.scheme not in ('postgres','postgresql') or not db.hostname or db.path!='/'+self.database or not self.database.startswith('weknora_'):
            raise DevelopmentConfigurationError('EXPLICIT_NEW_POSTGRES_TARGET_REQUIRED')
        if not self.system_id.isdigit() or self.system_id in PROHIBITED:raise DevelopmentConfigurationError('EXPLICIT_NEW_POSTGRES_ID_REQUIRED')
        if redis.scheme not in ('redis','rediss') or not redis.hostname or redis.path not in tuple('/'+str(i) for i in range(1,16)):
            raise DevelopmentConfigurationError('EXPLICIT_NONDEFAULT_REDIS_DB_REQUIRED')
        if not re.fullmatch('[0-9a-f]{40}',self.redis_run_id) or not self.redis_prefix.startswith('weknora-python-'):
            raise DevelopmentConfigurationError('EXPLICIT_REDIS_IDENTITY_PREFIX_REQUIRED')
        if not self.storage.is_absolute() or not self.secret_key_file.is_absolute():raise DevelopmentConfigurationError('ABSOLUTE_STORAGE_AND_KEY_PATHS_REQUIRED')
        if self.router_mode not in ('OFF','DYNAMIC','CHEAP_ONLY','EXPENSIVE_ONLY'):raise DevelopmentConfigurationError('ROUTER_MODE_INVALID')

    @classmethod
    def from_environment(cls,environment=None):
        env=os.environ if environment is None else environment
        def required(name):
            value=env.get('WEKNORA_PORT_'+name,'')
            if not value:raise DevelopmentConfigurationError('WEKNORA_PORT_'+name+'_REQUIRED')
            return value
        return cls(required('DATABASE_URL'),required('DATABASE_SYSTEM_ID'),required('DATABASE_NAME'),
            required('REDIS_URL'),required('REDIS_RUN_ID'),required('REDIS_PREFIX'),Path(required('STORAGE_ROOT')),
            Path(required('SECRET_KEY_FILE')),Path(env['WEKNORA_PORT_TOKENIZER_ASSET']) if env.get('WEKNORA_PORT_TOKENIZER_ASSET') else None,
            env.get('WEKNORA_PORT_ROUTER_MODE','DYNAMIC'),env.get('WEKNORA_PORT_OLLAMA_URL',''),
            tuple(v.strip() for v in env.get('WEKNORA_PORT_ORIGINS','http://127.0.0.1:5173,http://localhost:5173').split(',') if v.strip()))


def own_storage(settings):
    root=settings.storage;marker=root/'.weknora-python-storage.json'
    expected={'database':settings.database,'system_id':settings.system_id}
    root.mkdir(parents=True,exist_ok=True)
    if not marker.exists():
        if any(root.iterdir()):raise DevelopmentConfigurationError('UNOWNED_STORAGE_NOT_EMPTY')
        try:
            with marker.open('x',encoding='utf-8') as stream:json.dump(expected,stream)
        except FileExistsError:pass
    if json.loads(marker.read_text(encoding='utf-8'))!=expected:raise DevelopmentConfigurationError('STORAGE_DATABASE_IDENTITY_MISMATCH')


@asynccontextmanager
async def assemble(settings,*,model_factory=None,permissions=None,costs=None,principal=None):
    # Optional dependencies are test/server injections, never request JSON or env module imports.
    import psycopg
    from psycopg.rows import dict_row
    from redis.asyncio import Redis
    from .storage.connections import IsolatedConnectionFactory
    from .storage.postgres import IsolationPolicy
    from .storage.local_principal import LocalPrincipalStore
    from .streaming.manager import RedisListStreamStore
    from .crypto import SecretCipher
    from .runtime import ApplicationRuntime,ConversationSettings
    from .documents import DocumentLifecycleService,KnowledgeBaseSettings
    from .models.provider import ProviderModel
    from .models.transport import AsyncHTTPTransport,endpoint_urls
    from .models.types import CallContext,TrustedScope
    from .generation import RouterPolicy
    from .models.local_ollama import HTTPLocalService
    key=settings.secret_key_file.read_bytes()
    if len(key)!=32:raise DevelopmentConfigurationError('SECRET_KEY_FILE_MUST_CONTAIN_32_BYTES')
    async def acquire():return await psycopg.AsyncConnection.connect(settings.database_url,autocommit=True,row_factory=dict_row,connect_timeout=5)
    async def release(connection):await connection.close()
    leases=IsolatedConnectionFactory(acquire,release,IsolationPolicy(settings.system_id,PROHIBITED,settings.database))
    redis=Redis.from_url(settings.redis_url,decode_responses=True)
    runtime=None
    try:
        async with leases.lease() as guard:
            row=await (await guard.connection.execute("SELECT to_regclass('port_local_principal') AS principal,to_regclass('port_image_tasks') AS image")).fetchone()
            if not row['principal'] or not row['image']:raise DevelopmentConfigurationError('EXPLICIT_SCHEMA_INITIALIZATION_REQUIRED')
        if (await redis.info('server'))['run_id']!=settings.redis_run_id:raise DevelopmentConfigurationError('REDIS_IDENTITY_MISMATCH')
        own_storage(settings)
        principal=principal or await LocalPrincipalStore(leases).bootstrap()
        store=RedisListStreamStore(redis,prefix=settings.redis_prefix)
        box={}
        async def kb_settings(who):
            app=box['runtime']
            async with leases.lease() as guard:snapshot=await app._snapshot(guard)
            roles=dict(snapshot.model_ids)
            return KnowledgeBaseSettings(roles.get('embedding',''),roles.get('cheap',''),roles.get('rerank',''),{},
                {'vector_enabled':True,'keyword_enabled':True,'wiki_enabled':False,'graph_enabled':False},{'enable_multimodal':False})
        async def embedder(who,kb):
            app=box['runtime'];registry=app.registry_for_context()
            context=CallContext(str(uuid.uuid4()),TrustedScope(who.subject_id,(kb['id'],)))
            class Embedder:
                async def batch_embed_with_pool(self,texts,*,attempt):
                    try:
                        model=await registry.embedding(kb['embedding_model_id'],context)
                        return (await model.embed(texts,context.child('ingest-'+str(attempt)))).vectors
                    finally:await registry.close()
            return Embedder()
        if model_factory is None:
            def model_factory(config,key,gate):
                transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(config),gate=gate,credential=lambda _:key)
                return ProviderModel(config,transport)
        documents=DocumentLifecycleService(leases,settings.storage,principal=principal,resolve_kb_settings=kb_settings,
            embedder_for_kb=embedder,start_image_worker_on_upload=False)
        runtime=ApplicationRuntime(leases,principal,store,documents,model_factory=model_factory,cipher=SecretCipher(key),
            conversation=ConversationSettings(),policy=RouterPolicy(mode=settings.router_mode),tokenizer_asset=settings.tokenizer,
            permissions=permissions,costs=costs,local_service=HTTPLocalService(settings.local_url,allow_pull=False) if settings.local_url else None)
        box['runtime']=runtime
        yield runtime
    finally:
        if runtime is not None:await runtime.stop_background_workers()
        await redis.aclose()
