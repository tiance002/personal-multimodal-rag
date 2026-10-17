"""Synthetic secrets only; real config HTTP contract with an offline row fixture."""
import asyncio
import json
import pytest
import httpx
from backend.weknora_port.crypto import SecretCipher
from backend.weknora_port.storage.configuration import PostgresModelRoleStore
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.model_registry import role_snapshot
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.http_api import create_app
from tests.weknora_port.test_history_storage import adapter
from tests.weknora_port.test_model_configuration import ConfigurationConnection,Catalogue,values
from tests.weknora_port.test_http_api import FakeRuntime,PRINCIPAL


def test_normal_http_replaces_only_bad_role_preserves_ciphertext_and_isolates_failure():
    async def run():
        conn=ConfigurationConnection()
        store=PostgresModelRoleStore(adapter(conn).repository,LocalPrincipal(10000,'local'),
                                    Catalogue(),cipher=SecretCipher(b'a'*32))
        await store.update(0,values())
        # Isolated in-memory database row, never a production corrupt-secret write.
        conn.saved['roles']['expensive']['api_key']=SecretCipher(b'b'*32).encrypt('old-synthetic')
        before=json.loads(json.dumps(conn.saved['roles']))
        with pytest.raises(ValueError,match='STORED_SECRET_DECRYPT_FAILED'):
            await store.read()
        class Models:
            async def persist(self,role):
                assert role.api_key=='synthetic-secret'
                return 'id-'+role.role
        snapshot=await role_snapshot(store,Models())
        assert snapshot.model_id('cheap')=='id-cheap'
        assert len(snapshot.model_ids)==4
        with pytest.raises(ProviderError,match='STORED_SECRET_DECRYPT_FAILED'):
            snapshot.model_id('expensive')
        runtime=FakeRuntime()
        async def read(principal):
            assert principal is PRINCIPAL
            return await store.read_public()
        async def write(principal,revision,roles):
            assert principal is PRINCIPAL
            return await store.update(revision,roles)
        runtime.read_model_configuration=read
        runtime.update_model_configuration=write
        api=create_app(runtime,resolve_principal=lambda request:PRINCIPAL,
                       allowed_origins=('http://fixture',))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://fixture',
                headers={'Origin':'http://fixture'}) as client:
            public=(await client.get('/api/v1/settings/model-configuration')).json()['data']
            bad=next(r for r in public['roles'] if r['role']=='expensive')
            assert bad['key_configured'] and bad['credential_status']=='UNAVAILABLE'
            assert bad['credential_error']=='STORED_SECRET_DECRYPT_FAILED'
            update=[{k:r[k] for k in ('role','provider','model','base_url','source')} for r in public['roles']]
            # First save another role while the bad one remains untouched.
            response=await client.put('/api/v1/settings/model-configuration',json={'revision':'1','roles':update})
            assert response.status_code==200
            assert conn.saved['roles']==before
            next(r for r in update if r['role']=='expensive')['api_key']='replacement-synthetic'
            response=await client.put('/api/v1/settings/model-configuration',json={'revision':'2','roles':update})
            assert response.status_code==200
            assert 'replacement-synthetic' not in response.text and 'enc:v1:' not in response.text
            assert all(r['credential_status']=='CONFIGURED' for r in response.json()['data']['roles'])
        for name in ('cheap','embedding','rerank','vlm'):
            assert conn.saved['roles'][name]==before[name]
        _,roles=await store.read()
        assert next(r for r in roles if r.role=='expensive').api_key=='replacement-synthetic'
        assert all(r.api_key=='synthetic-secret' for r in roles if r.role!='expensive')
    asyncio.run(run())
