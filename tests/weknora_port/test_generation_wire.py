"""Full P1 HTTP/SSE path, injected MockTransport + synthetic one-send gate."""
import asyncio,json
from dataclasses import replace
import httpx
import pytest
from backend.weknora_port.models import AsyncHTTPTransport,ProviderModel,provider_config
from backend.weknora_port.models.transport import resolve_endpoint
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.generation import GenerationCoordinator,RoleBinding
from test_generation import request,CTX,Store,Committer,Lifecycle

class Gate:
    def __init__(self):self.calls=[];self.finishes=[]
    async def authorize(self,call):
        assert all(c.context.request_id!=call.context.request_id for c in self.calls)
        self.calls.append(call)
    async def finish(self,call,*,outcome,usage):self.finishes.append((call,outcome,usage))
class Fragmented(httpx.AsyncByteStream):
    def __init__(self,data):self.data=data
    async def __aiter__(self):
        for i in range(0,len(self.data),3):yield self.data[i:i+3]

def frames(model,text,finish='stop',*,reported=True,usage=True):
    value={'choices':[{'delta':{'content':text},'finish_reason':finish}]}
    if reported:value['model']=model
    final={'choices':[]}
    if usage:final['usage']={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}
    data='data: '+json.dumps(value,ensure_ascii=False)+'\r\n\r\n'+'data: '+json.dumps(final)+'\r\n\r\n'+'data: [DONE]\r\n\r\n'
    return httpx.Response(200,stream=Fragmented(data.encode()))
async def execute(handler,*,reported=True,usage=True):
    configs={role:provider_config(provider,model) for role,provider,model in [('cheap','siliconflow','XingChenAGI/Xing4.0-29B'),('expensive','deepseek','deepseek-flash')]}
    gate=Gate();transport=AsyncHTTPTransport(allowed_endpoints=[resolve_endpoint(c,'/chat/completions') for c in configs.values()],gate=gate,credential=lambda _: 'SIMULATED-NOT-A-KEY',http_transport=httpx.MockTransport(handler))
    models={role:ProviderModel(c,transport) for role,c in configs.items()}
    bindings={role:RoleBinding(role,c.provider,c.model,c.base_url,models[role]) for role,c in configs.items()}
    bindings['baseline']=replace(bindings['expensive'],role='baseline')
    store=Store();committer=Committer(store);lifecycle=Lifecycle()
    coordinator=GenerationCoordinator(bindings,store,committer,lifecycle=lifecycle)
    try:return await coordinator.run(request(),CTX),gate,lifecycle,store
    finally:await transport.aclose()

def test_real_transport_read_timeout_upgrades_once_and_unknown_cost_is_retained():
    async def exercise():
        bodies=[]
        def handler(req):
            body=json.loads(req.content);bodies.append(body)
            if body['model']=='XingChenAGI/Xing4.0-29B':raise httpx.ReadTimeout('synthetic private message',request=req)
            return frames(body['model'],'selected answer <ref id="c1"/>')
        result,gate,lifecycle,store=await execute(handler)
        assert len(bodies)==len(gate.calls)==len(gate.finishes)==2
        assert result.upgraded and result.attempts[0].error_code=='MODEL_CALL_TIMEOUT'
        assert result.attempts[0].usage.status=='UNKNOWN' and result.attempts[0].cost_status=='UNKNOWN'
        assert result.attempts[1].model_reported=='deepseek-flash'
        assert result.usage.status=='UNKNOWN' and result.usage.total_tokens is None
        assert bodies[0]['messages']==bodies[1]['messages']
        assert all(sum(key in body for key in ('max_tokens','max_completion_tokens'))==1 for body in bodies)
        assert all(body.get('max_tokens',body.get('max_completion_tokens'))==1024 for body in bodies)
        assert [c.context.request_id for c in gate.calls]==['run/answer/1/cheap','run/answer/2/expensive']
        assert gate.finishes[0][1]!='COMPLETED' and gate.finishes[0][2].status=='UNKNOWN'
        assert 'synthetic private message' not in repr(result)
    asyncio.run(exercise())

@pytest.mark.parametrize('reported,usage',[(False,False),(True,True)])
def test_actual_reported_model_and_absent_identity_usage_are_not_inferred(reported,usage):
    async def exercise():
        def handler(req):
            body=json.loads(req.content);return frames(body['model'],'plain answer',reported=reported,usage=usage)
        result,gate,life,store=await execute(handler)
        assert result.attempts[0].model_reported==('XingChenAGI/Xing4.0-29B' if reported else None)
        assert result.attempts[0].usage.status==('REPORTED' if usage else 'UNKNOWN')
        assert result.selected_role=='cheap' and not result.upgraded and len(gate.calls)==1
    asyncio.run(exercise())

@pytest.mark.parametrize('kind',['http500','invalidjson','wrongmodel','generic_timeout_code'])
def test_full_transport_errors_do_not_upgrade(kind):
    async def exercise():
        configs=provider_config('siliconflow','XingChenAGI/Xing4.0-29B');gate=Gate()
        def handler(req):
            if kind=='http500':return httpx.Response(500,json={'error':{'message':'synthetic private provider error'}})
            if kind=='invalidjson':return httpx.Response(200,content=b'data: {broken}\n\n')
            if kind=='wrongmodel':return frames('wrong-model','must never publish')
            return httpx.Response(200,content=b'data: {"error":{"message":"MODEL_CALL_TIMEOUT"}}\n\n')
        transport=AsyncHTTPTransport(allowed_endpoints=[resolve_endpoint(configs,'/chat/completions')],gate=gate,credential=lambda _: 'SIMULATED-NOT-A-KEY',http_transport=httpx.MockTransport(handler))
        model=ProviderModel(configs,transport);binding=RoleBinding('cheap',configs.provider,configs.model,configs.base_url,model)
        store=Store();life=Lifecycle();co=GenerationCoordinator({'cheap':binding},store,Committer(store),lifecycle=life)
        try:
            with pytest.raises(ProviderError):await co.run(request(),CTX)
            assert len(gate.calls)==1 and len(life.finished)==1 and not store.events
            if kind=='wrongmodel':assert life.finished[0][0].model_reported=='wrong-model'
        finally:await transport.aclose()
    asyncio.run(exercise())
