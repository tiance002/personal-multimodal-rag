import asyncio
import json
import pytest
from backend.weknora_port.storage.configuration import ConfiguredRole,PostgresModelRoleStore,ModelConfigurationError,ROLES
from backend.weknora_port.storage.history import LocalPrincipal
from tests.weknora_port.test_history_storage import adapter,HistoryConnection
from tests.weknora_port.test_storage import FakeCursor


class ConfigurationConnection(HistoryConnection):
    def __init__(self):
        super().__init__()
        self.saved = None
    async def execute(self,query,params=()):
        self.calls.append((query,params))
        if query.startswith('SELECT revision,roles'):
            return FakeCursor([self.saved] if self.saved else [])
        if query.startswith('INSERT INTO port_model_configuration'):
            self.saved = {'revision':params[2],'roles':json.loads(params[3])}
        return FakeCursor([])


class Catalogue:
    def validate(self,role):
        if not role.provider or not role.model or not role.base_url.startswith('https://'):
            raise ModelConfigurationError('MODEL_CONFIGURATION_INVALID')


def store(conn):
    return PostgresModelRoleStore(adapter(conn).repository,LocalPrincipal(10000,'local'),Catalogue())


def values():
    return [{'role':role,'provider':'custom','model':'model-'+role,'base_url':'https://configured.example/v1','api_key':'synthetic-secret','source':'remote'} for role in ROLES]


def test_configured_roles_are_generic_and_credentials_do_not_enter_public_or_repr():
    async def run():
        conn = ConfigurationConnection()
        public = await store(conn).update(0,values())
        assert public['revision'] == 1 and {v['role'] for v in public['roles']} == set(ROLES)
        assert 'synthetic-secret' not in json.dumps(public)
        assert 'synthetic-secret' not in repr(ConfiguredRole('cheap','custom','arbitrary','https://x', 'synthetic-secret'))
        assert (await store(conn).read_public()) == public
    asyncio.run(run())


def test_blank_key_preserves_key_and_stale_revision_never_writes():
    async def run():
        conn = ConfigurationConnection()
        roles = values()
        await store(conn).update(0,roles)
        for role in roles: role['api_key'] = ''
        await store(conn).update(1,roles)
        assert all(role['api_key'] == 'synthetic-secret' for role in conn.saved['roles'].values())
        writes = sum(sql.startswith('INSERT') for sql,_ in conn.calls)
        with pytest.raises(ModelConfigurationError,match='REVISION_CONFLICT'):
            await store(conn).update(1,roles)
        assert writes == sum(sql.startswith('INSERT') for sql,_ in conn.calls)
    asyncio.run(run())


def test_changed_endpoint_cannot_inherit_another_endpoint_credential():
    async def run():
        conn = ConfigurationConnection()
        roles = values()
        await store(conn).update(0,roles)
        roles[0].update(base_url='https://other.example/v1',api_key='')
        with pytest.raises(ModelConfigurationError,match='CREDENTIAL_REBINDING_REQUIRED'):
            await store(conn).update(1,roles)
        assert conn.saved['revision'] == 1
    asyncio.run(run())


def test_internal_fields_and_duplicate_roles_cannot_be_configured():
    async def run():
        conn = ConfigurationConnection()
        roles = values()
        roles[0]['tokenizer_path'] = '/internal'
        with pytest.raises(ModelConfigurationError,match='FIELD_INVALID'):
            await store(conn).update(0,roles)
        assert conn.saved is None
        roles = values()
        roles[0]['role'] = 'cheap'
        with pytest.raises(ModelConfigurationError,match='ROLES_REQUIRED'):
            await store(conn).update(0,roles)
    asyncio.run(run())


def test_legacy_role_update_preserves_source_interpretation_requires_explicit_rebind():
    async def run():
        conn=ConfigurationConnection()
        legacy={v['role']:{k:value for k,value in v.items() if k!='source'} for v in values()}
        conn.saved={'revision':7,'roles':legacy}
        old=await store(conn).read()
        assert all(role.source=='' for role in old[1])
        update=[{k:value for k,value in v.items() if k!='source'} for v in values()]
        result=await store(conn).update(7,update)
        assert all(v['source']=='' for v in result['roles'])
        update[0]['model']='new-model-without-source'
        with pytest.raises(ModelConfigurationError,match='MODEL_SOURCE_REQUIRED'):await store(conn).update(8,update)
        update[0]['source']='remote';update[0]['api_key']=''
        with pytest.raises(ModelConfigurationError,match='CREDENTIAL_REBINDING_REQUIRED'):await store(conn).update(8,update)
    asyncio.run(run())
