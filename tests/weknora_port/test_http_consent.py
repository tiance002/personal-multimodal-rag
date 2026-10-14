"""Consent PATCH contract: in-process ASGI/fake runtime, no database/network."""
import asyncio
import httpx
import pytest
from backend.weknora_port.http_api import create_app

PRINCIPAL = object()
class Runtime:
    def __init__(self): self.calls=[]
    async def update_knowledge_base_consent(self, principal, kb_id, cloud_allowed):
        assert principal is PRINCIPAL
        self.calls.append((kb_id,cloud_allowed))
        return dict(id=kb_id,name='Fixture',cloud_allowed=cloud_allowed,document_count=0)

def call(body, origin='http://test', *, missing_origin=False):
    async def run():
        runtime=Runtime()
        app=create_app(runtime,resolve_principal=lambda request:PRINCIPAL)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
            response=await client.patch('/api/v1/knowledge-bases/kb-1/external-processing',
                json=body,headers={} if missing_origin else {'Origin':origin})
        return runtime,response
    return asyncio.run(run())

@pytest.mark.parametrize('allowed',[True,False])
def test_patch_passes_exact_boolean_and_trusted_principal(allowed):
    runtime,response=call({'cloud_allowed':allowed})
    assert response.status_code==200 and response.json()['data']['cloud_allowed'] is allowed
    assert runtime.calls==[('kb-1',allowed)]

@pytest.mark.parametrize('body',[{'cloud_allowed':'true'},{'cloud_allowed':1},{'cloud_allowed':0},{'cloud_allowed':None},{},{'cloud_allowed':True,'owner_id':'caller'}])
def test_strict_boolean_and_extra_field_rejected_without_mutation(body):
    runtime,response=call(body)
    assert response.status_code==422 and not runtime.calls

@pytest.mark.parametrize('origin',['https://evil.example','null',''])
def test_patch_cross_origin_denied_before_runtime(origin):
    runtime,response=call({'cloud_allowed':True},origin)
    assert response.status_code==403 and not runtime.calls

def test_patch_missing_origin_denied_before_runtime():
    runtime,response=call({'cloud_allowed':True},missing_origin=True)
    assert response.status_code==403 and not runtime.calls
