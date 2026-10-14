"""Immutable physical model catalog; role changes never replace pinned rows."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from ..crypto import SecretCipher
from .configuration import ConfiguredRole, ModelConfigurationError, ROLES
from .history import LocalPrincipal

MODEL_TYPES = {'embedding': 'Embedding', 'rerank': 'Rerank',
               'cheap': 'KnowledgeQA', 'expensive': 'KnowledgeQA', 'vlm': 'VLLM'}
SECRET_FIELDS = frozenset({'api_key', 'app_secret'})


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, UnicodeError):
        raise ModelConfigurationError('MODEL_PARAMETERS_INVALID') from None


def _parameters(role):
    if not isinstance(role.parameters, Mapping):
        raise ModelConfigurationError('MODEL_PARAMETERS_INVALID')
    parameters = json.loads(_json(dict(role.parameters)))
    # Source ModelParameters stores these fields; the trusted role is authoritative.
    for name, value in (('base_url', role.base_url), ('provider', role.provider), ('api_key', role.api_key)):
        if name in parameters and parameters[name] != value:
            raise ModelConfigurationError('MODEL_PARAMETER_IDENTITY_CONFLICT')
        parameters[name] = value
    if not all(isinstance(parameters.get(name, ''), str) for name in SECRET_FIELDS):
        raise ModelConfigurationError('MODEL_SECRET_INVALID')
    return parameters


def _without_secrets(parameters):
    return {key: value for key, value in parameters.items() if key not in SECRET_FIELDS}


class PostgresModelStore:
    def __init__(self, lease_factory, principal: LocalPrincipal, catalogue, *, cipher: SecretCipher | None = None):
        if not isinstance(principal, LocalPrincipal):
            raise ModelConfigurationError('LOCAL_PRINCIPAL_REQUIRED')
        self.leases, self.principal, self.catalogue = lease_factory, principal, catalogue
        # Matches source's unset AES key plaintext behavior. Runtime injects a key.
        self.cipher = cipher if cipher is not None else SecretCipher(None)

    def _identity(self, role):
        if not isinstance(role, ConfiguredRole) or role.role not in ROLES:
            raise ModelConfigurationError('MODEL_ROLE_INVALID')
        if not all(isinstance(value, str) and value for value in (role.provider, role.model, role.base_url)) or not isinstance(role.api_key, str):
            raise ModelConfigurationError('MODEL_IDENTITY_REQUIRED')
        self.catalogue.validate(role)
        parameters = _parameters(role)
        physical = {'version': 'model-physical/v1', 'provider': role.provider,
                    'model': role.model, 'base_url': role.base_url,
                    'type': MODEL_TYPES[role.role], 'parameters': _without_secrets(parameters)}
        physical_hash = hashlib.sha256(_json(physical).encode('utf-8')).hexdigest()
        identity = {'version': 'model-catalog/v1', 'tenant_id': self.principal.tenant_id,
                    'subject_id': self.principal.subject_id, 'role': role.role, 'physical': physical}
        identity_hash = hashlib.sha256(_json(identity).encode('utf-8')).hexdigest()
        return 'mdl' + identity_hash[:61], identity_hash, physical_hash, parameters

    def _check(self, row, role, identity_hash, physical_hash, parameters):
        if (row['tenant_id'], row['subject_id']) != (self.principal.tenant_id, self.principal.subject_id):
            raise ModelConfigurationError('MODEL_OWNER_MISMATCH')
        stored = row['parameters']
        if isinstance(stored, str):
            stored = json.loads(stored)
        if (row['role'], row['name'], row['type'], row['source'], row['identity_sha256'], row['physical_sha256']) != (
            role.role, role.model, MODEL_TYPES[role.role], role.provider, identity_hash, physical_hash
        ) or _json(_without_secrets(stored)) != _json(_without_secrets(parameters)):
            raise ModelConfigurationError('MODEL_IDENTITY_COLLISION')
        if row['deleted_at'] is not None or row['status'] != 'active':
            raise ModelConfigurationError('MODEL_UNAVAILABLE')

    async def _rows(self, guard, sql, args=()):
        guard.require_verified()
        return await (await guard.connection.execute(sql, args)).fetchall()

    async def persist(self, role: ConfiguredRole) -> str:
        model_id, identity_hash, physical_hash, parameters = self._identity(role)
        stored = {key: self.cipher.encrypt(value) if key in SECRET_FIELDS else value for key, value in parameters.items()}
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                await guard.connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('port-model:' + model_id,))
                owners = await self._rows(guard, 'SELECT id FROM tenants WHERE id=%s AND deleted_at IS NULL FOR SHARE', (self.principal.tenant_id,))
                if len(owners) != 1:
                    raise ModelConfigurationError('MODEL_TENANT_UNAVAILABLE')
                rows = await self._rows(guard, 'SELECT * FROM models WHERE id=%s AND tenant_id=%s AND subject_id=%s FOR UPDATE',
                                        (model_id, self.principal.tenant_id, self.principal.subject_id))
                if rows:
                    self._check(rows[0], role, identity_hash, physical_hash, parameters)
                    # Preserve source parameters/identity; only known credential fields change.
                    await guard.connection.execute('UPDATE models SET parameters=%s::jsonb,updated_at=now() WHERE id=%s AND tenant_id=%s AND subject_id=%s',
                        (_json(stored), model_id, self.principal.tenant_id, self.principal.subject_id))
                else:
                    inserted = await self._rows(guard, 'INSERT INTO models(id,tenant_id,subject_id,name,type,source,parameters,role,identity_sha256,physical_sha256) '
                        'VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id',
                        (model_id, self.principal.tenant_id, self.principal.subject_id, role.model, MODEL_TYPES[role.role], role.provider,
                         _json(stored), role.role, identity_hash, physical_hash))
                    if len(inserted) != 1:
                        raise ModelConfigurationError('MODEL_IDENTITY_COLLISION')
        return model_id

    async def _load(self, model_id):
        if not isinstance(model_id, str) or not model_id or len(model_id) > 64:
            raise ModelConfigurationError('MODEL_ID_INVALID')
        async with self.leases.lease() as guard:
            rows = await self._rows(guard, 'SELECT m.* FROM models m JOIN tenants t ON t.id=m.tenant_id '
                'WHERE m.id=%s AND m.tenant_id=%s AND m.subject_id=%s AND m.deleted_at IS NULL '
                "AND m.status='active' AND t.deleted_at IS NULL", (model_id, self.principal.tenant_id, self.principal.subject_id))
            if len(rows) != 1:
                raise ModelConfigurationError('MODEL_NOT_FOUND')
            row = rows[0]
        parameters = row['parameters']
        if isinstance(parameters, str):
            parameters = json.loads(parameters)
        # Validate the full non-secret identity before returning even a public projection.
        role = ConfiguredRole(row['role'], row['source'], row['name'], parameters['base_url'], '',
                              {key: value for key, value in parameters.items() if key not in SECRET_FIELDS})
        expected_id, identity_hash, physical_hash, expected_params = self._identity(role)
        self._check(row, role, identity_hash, physical_hash, expected_params)
        if expected_id != model_id:
            raise ModelConfigurationError('MODEL_IDENTITY_COLLISION')
        return row, parameters, role

    async def resolve(self, model_id: str) -> ConfiguredRole:
        row, parameters, role = await self._load(model_id)
        decrypted = {key: self.cipher.decrypt(value, strict=True) if key in SECRET_FIELDS else value for key, value in parameters.items()}
        return ConfiguredRole(role.role, role.provider, role.model, role.base_url, decrypted.pop('api_key', ''),
                              {key: value for key, value in decrypted.items() if key not in {'base_url', 'provider'}})

    async def public(self, model_id: str):
        row, parameters, role = await self._load(model_id)
        result={'id': model_id, 'role': role.role, 'provider': role.provider, 'model': role.model,
                'base_url': role.base_url, 'key_configured': bool(parameters.get('api_key'))}
        if role.role=='vlm':
            from ..models.catalog import find_model
            known=find_model(role.provider,role.model,'VLLM')
            result['vision_capability']=('USER_DECLARED_UNTESTED' if parameters.get('vision_user_declared')
                and known is None else 'CATALOG_DECLARED' if known and 'image' in known.get('input',())
                else 'UNKNOWN')
        return result

    async def physical_key(self, model_id: str) -> str:
        row, _, _ = await self._load(model_id)
        return row['physical_sha256']
