"""Five configurable model roles; credentials never appear in public DTOs.

The model catalogue remains the single source for provider compatibility and
capacities. This store binds roles, not fixed Xing/DeepSeek implementation paths.
"""
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .history import LocalPrincipal
from .postgres import PostgresRetriever
from ..crypto import SecretCipher, SecretCipherError

ROLES = ('embedding', 'rerank', 'cheap', 'expensive', 'vlm')


class ModelConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ConfiguredRole:
    role: str
    provider: str
    model: str
    base_url: str
    api_key: str = field(default='', repr=False)
    parameters: Mapping[str, Any] = field(default_factory=dict, repr=False)

    source: str = ''  # empty is the explicit legacy-vendor/v1 representation

    def public(self):
        return {'role':self.role, 'provider':self.provider, 'model':self.model,
                'base_url':self.base_url, 'key_configured':bool(self.api_key), 'source':self.source}


class ModelCatalogue(Protocol):
    def validate(self, role: ConfiguredRole) -> None:
        """Validate configured identity/URL against supported protocol metadata."""


class PostgresModelRoleStore:
    def __init__(self, guard: PostgresRetriever, principal: LocalPrincipal, catalogue: ModelCatalogue, *, cipher: SecretCipher | None = None):
        self.guard, self.principal, self.catalogue = guard, principal, catalogue
        self.cipher = cipher or SecretCipher(None)

    async def _rows(self, query, args=()):
        self.guard.require_verified()
        return await (await self.guard.connection.execute(query,args)).fetchall()

    async def _stored(self):
        rows = await self._rows('SELECT revision,roles FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s',
                               (self.principal.tenant_id,self.principal.subject_id))
        if not rows:
            return 0, {role:{'role':role,'provider':'','model':'','base_url':'','api_key':''} for role in ROLES}
        return rows[0]['revision'], rows[0]['roles']

    async def read(self, *, strict_secrets=True) -> tuple[int, tuple[ConfiguredRole, ...]]:
        revision, values = await self._stored()
        return revision, tuple(ConfiguredRole(**{**values[role],
            'api_key':self.cipher.decrypt(values[role]['api_key'],strict=strict_secrets)}) for role in ROLES)

    async def read_available(self):
        revision, values = await self._stored()
        available, errors = [], []
        for name in ROLES:
            try:
                available.append(ConfiguredRole(**{**values[name],
                    'api_key':self.cipher.decrypt(values[name]['api_key'])}))
            except SecretCipherError as error:
                errors.append((name,error.code))
        return revision, tuple(available), tuple(errors)

    def _public(self, revision, values):
        roles=[]
        for name in ROLES:
            value=values[name]
            error=''
            try:
                self.cipher.decrypt(value['api_key'])
            except SecretCipherError as exc:
                error=exc.code
            public=ConfiguredRole(**{**value,'api_key':''}).public()
            public.update(key_configured=bool(value['api_key']),
                credential_status='UNAVAILABLE' if error else 'CONFIGURED' if value['api_key'] else 'NOT_CONFIGURED',
                credential_error=error)
            roles.append(public)
        return {'revision':revision,'roles':roles}

    async def read_public(self):
        revision,values = await self._stored()
        return self._public(revision,values)

    async def update(self, revision: int, roles: list[dict[str, Any]]):
        import json
        if type(revision) is not int or revision < 0 or not isinstance(roles,list) or len(roles) != len(ROLES):
            raise ModelConfigurationError('MODEL_CONFIGURATION_INVALID')
        if any(not isinstance(role,dict) or any(not isinstance(value,str) for value in role.values()) for role in roles):
            raise ModelConfigurationError('MODEL_CONFIGURATION_FIELD_INVALID')
        if {role.get('role') for role in roles} != set(ROLES):
            raise ModelConfigurationError('MODEL_ROLES_REQUIRED')
        async with self.guard.connection.transaction():
            self.guard.require_verified()
            # This immutable owner lock also serializes initial configuration.
            await self.guard.connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
                (f'port-model-config:{self.principal.tenant_id}:{self.principal.subject_id}',))
            current_revision, current = await self._stored()
            if revision != current_revision:
                raise ModelConfigurationError('MODEL_CONFIGURATION_REVISION_CONFLICT')
            previous = {name:ConfiguredRole(**value) for name,value in current.items()}
            prepared = {}
            for value in roles:
                if set(value) - {'role','provider','model','base_url','api_key','source'}:
                    raise ModelConfigurationError('MODEL_CONFIGURATION_FIELD_INVALID')
                old = previous[value['role']]
                provider = value.get('provider','').strip()
                endpoint = value.get('base_url','').strip()
                source = value.get('source',old.source)
                if source not in ('','local','remote'):raise ModelConfigurationError('MODEL_SOURCE_INVALID')
                if (provider or value.get('model') or endpoint) and not source and (not old.provider or (provider,value.get('model','').strip(),endpoint)!=(old.provider,old.model,old.base_url)):
                    raise ModelConfigurationError('MODEL_SOURCE_REQUIRED')
                if old.api_key and (provider,endpoint,source) != (old.provider,old.base_url,old.source) and not value.get('api_key'):
                    raise ModelConfigurationError('MODEL_CREDENTIAL_REBINDING_REQUIRED')
                # Blank key preserves its prior credential, never echoes it.
                new_key = value.get('api_key','')
                key = self.cipher.encrypt(new_key) if new_key else old.api_key
                name = value.get('model','').strip()
                parameters = old.parameters if (provider,name) == (old.provider,old.model) else {}
                # Catalogue validates identity/capability. Preserved ciphertext
                # is never passed to a model or interpreted as an API key.
                role = ConfiguredRole(value['role'], provider,name,endpoint,'',parameters,source)
                if role.provider or role.model or role.base_url or key:
                    self.catalogue.validate(role)
                prepared[role.role] = {'role':role.role,'provider':role.provider,'model':role.model,
                    'base_url':role.base_url,'api_key':key,'parameters':dict(role.parameters),'source':role.source}
            stored = prepared
            await self.guard.connection.execute('INSERT INTO port_model_configuration(tenant_id,subject_id,revision,roles) '
                'VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(tenant_id,subject_id) DO UPDATE '
                'SET revision=EXCLUDED.revision,roles=EXCLUDED.roles,updated_at=now()',
                (self.principal.tenant_id,self.principal.subject_id,current_revision+1,
                 json.dumps(stored,ensure_ascii=False,allow_nan=False)))
        return self._public(current_revision+1,stored)
