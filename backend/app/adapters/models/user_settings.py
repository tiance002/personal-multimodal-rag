"""Local role configuration. Secrets stay in the shared private runtime volume.

Saving configuration never probes a provider or grants quota/egress permission.
Unknown capabilities remain unavailable until the system has an adapter profile.
"""
import json
import hashlib
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from backend.app.domain.model_registry import ENDPOINTS, KEY_ENVS, ModelRegistry, ModelSpec

ROLES = ('embedding', 'rerank', 'chat_cheap', 'chat_expensive', 'vision')
LOCK = RLock()


def config_path(settings):
    return settings.storage_root / 'private' / 'model-settings.json'


def read_config(settings):
    path = config_path(settings)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def public_config(settings):
    saved = read_config(settings)
    registry = settings.model_registry()
    rows = []
    for role in ROLES:
        spec = registry.select(role)
        row = saved['roles'][role] if saved else dict(provider=spec.provider, model=spec.model_id, base_url=spec.base_url)
        # Never borrow a provider's environment secret for an alternate endpoint.
        key = row.get('api_key')
        inherited = (row['base_url'] == ENDPOINTS.get(row['provider'])
                     and bool(os.environ.get(KEY_ENVS.get(row['provider'], ''), '').strip()))
        rows.append(dict(role=role, provider=row['provider'], model=row['model'],
                         base_url=row['base_url'], key_configured=bool(key) or inherited))
    return dict(revision=saved['revision'] if saved else 'environment', roles=rows)


def prepare_config(settings, payload):
    import uuid
    if type(payload) is not dict or set(payload) != {'revision', 'roles'} or type(payload['roles']) is not list:
        raise ValueError('MODEL_SETTINGS_INVALID')
    previous = read_config(settings)
    revision = previous['revision'] if previous else 'environment'
    if payload['revision'] != revision:
        raise ValueError('MODEL_SETTINGS_CONFLICT')
    rows = {}
    for row in payload['roles']:
        if (type(row) is not dict or set(row) - {'role', 'provider', 'model', 'base_url', 'api_key'}
                or row.get('role') not in ROLES or row['role'] in rows):
            raise ValueError('MODEL_SETTINGS_INVALID')
        role = row['role']
        if any(type(row.get(field)) is not str or not row[field].strip() or len(row[field]) > 500
               for field in ('provider', 'model', 'base_url')):
            raise ValueError('MODEL_SETTINGS_INVALID')
        value = {field: row[field].strip() for field in ('provider', 'model', 'base_url')}
        value['base_url'] = value['base_url'].rstrip('/')
        url = urlsplit(value['base_url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('MODEL_SETTINGS_URL_INVALID')
        if value['provider'] == 'siliconflow' and value['model'].lower().startswith('pro/'):
            raise ValueError('MODEL_SETTINGS_PRO_DISABLED')
        supplied = row.get('api_key', '')
        if type(supplied) is not str or len(supplied) > 4096 or any(c in supplied for c in '\r\n'):
            raise ValueError('MODEL_SETTINGS_INVALID')
        old = previous['roles'][role] if previous else {}
        # Blank means retain only when the destination identity is unchanged.
        same = all(old.get(field) == value[field] for field in ('provider', 'model', 'base_url'))
        value['api_key'] = supplied.strip() or (old.get('api_key', '') if same else '')
        rows[role] = value
    if set(rows) != set(ROLES):
        raise ValueError('MODEL_SETTINGS_INVALID')
    return dict(revision=str(uuid.uuid4()), roles=rows)


def apply_config(settings, saved=None):
    saved = read_config(settings) if saved is None else saved
    if saved is None:
        return settings
    models, keys = [], {}
    for role in ROLES:
        row = saved['roles'][role]
        models.append(ModelSpec(role+'_user', role, row['provider'], row['model'],
            KEY_ENVS.get(row['provider'], 'UNCONFIGURED_MODEL_KEY'), row['base_url'],
            1024 if role == 'embedding' else None, user_configured=True,
            resolved_revision=('UNKNOWN' if row['base_url'] == ENDPOINTS.get(row['provider']) else
                               'endpoint/'+hashlib.sha256(row['base_url'].encode()).hexdigest())))
        if row.get('api_key'):
            keys[role] = row['api_key']
        elif row['base_url'] != ENDPOINTS.get(row['provider']):
            keys[role] = ''
    registry = ModelRegistry(models, {role: role+'_user' for role in ROLES})
    # Preserve existing authorization identities/ledgers. Changing models does
    # not transplant their free quote, token quota or verified protocol profile.
    policy = json.loads(settings.service_model_policy_json or '{"schema_version":1,"policies":[]}')
    policy['policies'] = [p for p in policy['policies'] if all(
        (registry.select(role).provider, registry.select(role).model_id, registry.select(role).base_url)
        == (p['provider'], p['model'], ENDPOINTS.get(p['provider'])) for role in p['roles'])]
    return replace(settings, model_configuration_revision=saved['revision'], model_registry_json=json.dumps(dict(models=[m.__dict__ for m in models],
        defaults=registry.defaults)), model_api_keys=keys, service_model_policy_json=json.dumps(policy))


def write_config(settings, saved):
    path = config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.model-settings-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(saved, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
