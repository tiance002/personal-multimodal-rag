"""Browser DELETE preflight must remain confined to explicit UI origins."""
import asyncio
import httpx
from backend.weknora_port.http_api import create_app
from tests.weknora_port.test_http_api import FakeRuntime, PRINCIPAL

def test_memory_delete_preflight_requires_explicit_allowed_origin():
 async def scenario():
  runtime=FakeRuntime()
  app=create_app(runtime,resolve_principal=lambda request:PRINCIPAL,allowed_origins=('http://127.0.0.1:56426',))
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://port.test') as client:
   headers={'Origin':'http://127.0.0.1:56426','Access-Control-Request-Method':'DELETE','Access-Control-Request-Headers':'content-type'}
   allowed=await client.options('/api/v1/memory/items',headers=headers)
   assert allowed.status_code==200
   assert allowed.headers['access-control-allow-origin']=='http://127.0.0.1:56426'
   assert 'DELETE' in allowed.headers['access-control-allow-methods']
   denied=await client.options('/api/v1/memory/items',headers=dict(headers,Origin='http://untrusted.test'))
   assert denied.status_code==400 and 'access-control-allow-origin' not in denied.headers
  closed=create_app(runtime,resolve_principal=lambda request:PRINCIPAL)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=closed),base_url='http://port.test') as client:
   absent=await client.options('/api/v1/memory/items',headers=headers)
   assert 'access-control-allow-origin' not in absent.headers
  assert runtime.principals==[] and runtime.accept_calls==[] and runtime.run_calls==[]
 asyncio.run(scenario())
