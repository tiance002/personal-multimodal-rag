"""Configured, archived model identities behind one neutral Provider interface."""
from dataclasses import dataclass,field
import inspect
from typing import Any,Callable

from .models.types import CallContext,ModelConfig,ProviderError
from .storage.configuration import ConfiguredRole,ROLES
from .generation.types import RoleBinding

MODEL_TYPES = {'embedding':'Embedding','rerank':'Rerank','cheap':'KnowledgeQA',
               'expensive':'KnowledgeQA','vlm':'VLLM'}


@dataclass(frozen=True)
class RoleSnapshot:
    revision: int
    model_ids: tuple[tuple[str,str], ...]
    configurations: tuple[ConfiguredRole, ...] = field(repr=False)

    def model_id(self, role: str) -> str:
        value = dict(self.model_ids).get(role,'')
        if not value:
            raise ProviderError('MODEL_ROLE_NOT_CONFIGURED')
        return value


async def role_snapshot(configuration_store,model_store) -> RoleSnapshot:
    revision,roles = await configuration_store.read()
    ids = []
    for role in roles:
        if role.provider or role.model or role.base_url:
            ids.append((role.role,await model_store.persist(role)))
    return RoleSnapshot(revision,tuple(ids),roles)


class SourceCatalogue:
    """Resolve source protocol/capacity metadata, with no role-specific HTTP path."""
    def resolve(self, role: ConfiguredRole, model_id: str) -> ModelConfig:
        from .models.catalog import find_model, resolve_model_config
        parameters = dict(role.parameters)
        declared = parameters.pop('vision_user_declared', False)
        if type(declared) is not bool:
            raise ProviderError('MODEL_VISION_DECLARATION_INVALID')
        if declared:
            known = find_model(role.provider, role.model, 'VLLM')
            if known is not None and 'image' not in known.get('input', ()):
                raise ProviderError('MODEL_VISION_DECLARATION_CONFLICT')
            if known is None:
                parameters['spec_override'] = {'input': ['image']}
        # Stored source ModelParameters includes identity and encrypted-secret
        # slots. They are supplied separately by the trusted immutable role.
        for name, expected in (('provider', role.provider), ('base_url', role.base_url),
                               ('api_key', role.api_key)):
            if name in parameters and parameters[name] != expected:
                raise ProviderError('MODEL_PARAMETER_IDENTITY_CONFLICT')
            parameters.pop(name, None)
        parameters.pop('app_secret', None)
        return resolve_model_config(role.provider,role.model,base_url=role.base_url,
            model_id=model_id,model_type=MODEL_TYPES[role.role],internal_overrides=parameters)

    def validate(self, role: ConfiguredRole) -> None:
        if role.role not in ROLES or not all((role.provider,role.model,role.base_url)):
            raise ProviderError('MODEL_CONFIGURATION_INVALID')
        self.resolve(role,'configuration-validation')


class NeutralModelRegistry:
    def __init__(self,model_store,model_factory:Callable[[ModelConfig,str],Any],*,
                 owner_id:str,catalogue=None):
        self.store,self.model_factory,self.owner_id = model_store,model_factory,owner_id
        self.catalogue = catalogue or SourceCatalogue()
        self._models = {}

    async def get(self,model_id:str,context:CallContext):
        if context.scope.owner_id != self.owner_id:
            raise ProviderError('MODEL_OWNER_MISMATCH')
        if model_id not in self._models:
            role = await self.store.resolve(model_id)
            config = self.catalogue.resolve(role,model_id)
            model = self.model_factory(config,role.api_key)
            if inspect.isawaitable(model):
                model = await model
            if getattr(model,'config',None) != config:
                raise ProviderError('MODEL_FACTORY_IDENTITY_MISMATCH')
            self._models[model_id] = model
        return self._models[model_id]

    async def chat(self,model_id,context): return await self.get(model_id,context)
    async def rerank(self,model_id,context): return await self.get(model_id,context)
    async def embedding(self,model_id,context): return await self.get(model_id,context)

    async def physical_key(self,model_id,context):
        if context.scope.owner_id != self.owner_id:
            raise ProviderError('MODEL_OWNER_MISMATCH')
        return await self.store.physical_key(model_id)

    async def generation_bindings(self,snapshot:RoleSnapshot,context:CallContext):
        bindings = {}
        for role,configured_role in (('baseline','cheap'),('cheap','cheap'),('expensive','expensive')):
            model = await self.get(snapshot.model_id(configured_role),context)
            config = model.config
            bindings[role] = RoleBinding(role,config.provider,config.model,
                                        config.endpoint_url or config.base_url,model)
        return bindings

    async def close(self):
        closed = set()
        for model in self._models.values():
            transport = getattr(model,'transport',None)
            if transport is not None and id(transport) not in closed:
                closed.add(id(transport))
                await transport.aclose()
