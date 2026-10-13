"""Five configurable model roles; credentials never appear in public DTOs.

The model catalogue remains the single source for provider compatibility and
capacities. This store binds roles, not fixed Xing/DeepSeek implementation paths.
"""
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .history import LocalPrincipal
from .postgres import PostgresRetriever
from ..crypto import SecretCipher

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

    def public(self):
        return {'role':self.role, 'provider':self.provider, 'model':self.model,
                'base_url':self.base_url, 'key_configured':bool(self.api_key)}


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

    async def read(self, *, strict_secrets=True) -> tuple[int, tuple[ConfiguredRole, ...]]:
        rows = await self._rows('SELECT revision,roles FROM port_model_configuration WHERE tenant_id=%s AND subject_id=%s',
                               (self.principal.tenant_id,self.principal.subject_id))
        if not rows:
            return 0, tuple(ConfiguredRole(role,'','','') for role in ROLES)
        values = rows[0]['roles']
        return rows[0]['revision'], tuple(ConfiguredRole(**{**values[role],
            'api_key':self.cipher.decrypt(values[role]['api_key'],strict=strict_secrets)}) for role in ROLES)

    async def read_public(self):
        revision,roles = await self.read(strict_secrets=False)
        return {'revision':revision,'roles':[role.public() for role in roles]}

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
            current_revision, current = await self.read()
            if revision != current_revision:
                raise ModelConfigurationError('MODEL_CONFIGURATION_REVISION_CONFLICT')
            previous = {role.role:role for role in current}
            prepared = {}
            for value in roles:
                if set(value) - {'role','provider','model','base_url','api_key'}:
                    raise ModelConfigurationError('MODEL_CONFIGURATION_FIELD_INVALID')
                old = previous[value['role']]
                provider = value.get('provider','').strip()
                endpoint = value.get('base_url','').strip()
                if old.api_key and (provider,endpoint) != (old.provider,old.base_url) and not value.get('api_key'):
                    raise ModelConfigurationError('MODEL_CREDENTIAL_REBINDING_REQUIRED')
                # Blank key preserves its prior credential, never echoes it.
                key = value.get('api_key') or old.api_key
                name = value.get('model','').strip()
                parameters = old.parameters if (provider,name) == (old.provider,old.model) else {}
                role = ConfiguredRole(value['role'], provider,name,endpoint,key,parameters)
                if role.provider or role.model or role.base_url or role.api_key:
                    self.catalogue.validate(role)
                prepared[role.role] = {'role':role.role,'provider':role.provider,'model':role.model,
                    'base_url':role.base_url,'api_key':role.api_key,'parameters':dict(role.parameters)}
            stored = {name:{**value,'api_key':self.cipher.encrypt(value['api_key'])} for name,value in prepared.items()}
            await self.guard.connection.execute('INSERT INTO port_model_configuration(tenant_id,subject_id,revision,roles) '
                'VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT(tenant_id,subject_id) DO UPDATE '
                'SET revision=EXCLUDED.revision,roles=EXCLUDED.roles,updated_at=now()',
                (self.principal.tenant_id,self.principal.subject_id,current_revision+1,
                 json.dumps(stored,ensure_ascii=False,allow_nan=False)))
        return {'revision':current_revision+1,'roles':[ConfiguredRole(**prepared[role]).public() for role in ROLES]}
