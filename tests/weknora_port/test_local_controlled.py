"""Controlled Local transport: no actual Ollama/model requests."""
import asyncio,json
from dataclasses import replace
import httpx,pytest
from backend.weknora_port.models.types import ModelConfig,CallContext,TrustedScope,Message,ChatOptions,CallCancelled,ProviderError
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.local_ollama import HTTPLocalService,local_connection_config
from backend.weknora_port.models.transport import AsyncHTTPTransport,endpoint_urls
from tests.weknora_port.test_provider_transport import Gate

BASE='http://127.0.0.1:12499'
CFG=ModelConfig('ollama','tiny:1b','local-safe','https://inline.invalid/never',source='local',requires_auth=True,custom_headers={'X-PRIVATE':'synthetic-secret'},extra_config={'app_secret':'synthetic-secret'})
CTX=CallContext('local',TrustedScope('owner',('kb',)))
MS=(Message('user','synthetic'),)
OPT=ChatOptions(max_tokens=8)

def make(handler,allowed=None):
    service=HTTPLocalService(BASE,allow_pull=False);gate=Gate();effective=local_connection_config(CFG,BASE)
    transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(effective) if allowed is None else allowed,gate=gate,credential=lambda _:(_ for _ in ()).throw(AssertionError('local credential lookup')),http_transport=httpx.MockTransport(handler))
    return ProviderModel(CFG,transport,local_service=service),transport,gate

def tags():return httpx.Response(200,json={'models':[{'name':CFG.model}]})

@pytest.mark.parametrize('stream',[False,True])
@pytest.mark.parametrize('state',['cancelled','expired'])
def test_pre_cancel_deadline_ensure_and_chat_zero(stream,state):
    async def run():
        requests=[]
        model,t,g=make(lambda r:requests.append(r))
        cancel=asyncio.Event();cancel.set()
        context=replace(CTX,cancelled=cancel) if state=='cancelled' else replace(CTX,deadline=asyncio.get_running_loop().time()-1)
        try:
            if stream:
                if state=='cancelled':
                    with pytest.raises(CallCancelled):[e async for e in model.chat_stream(MS,OPT,context)]
                else:assert any(e.response_type=='error' for e in [e async for e in model.chat_stream(MS,OPT,context)])
            else:
                with pytest.raises(ProviderError):await model.chat(MS,OPT,context)
            assert not requests and not g.calls
        finally:await t.aclose()
    asyncio.run(run())

@pytest.mark.parametrize('payload',[{'error':'private synthetic failure'},{'models':[]}])
def test_ensure_error_has_no_pull_or_chat(payload):
    async def run():
        requests=[]
        def handler(r):requests.append(r.url.path);return httpx.Response(200,json=payload)
        model,t,g=make(handler)
        try:
            events=[e async for e in model.chat_stream(MS,OPT,CTX)]
            assert requests==['/api/tags'] and not any(e.response_type=='answer' and e.done for e in events)
            assert len(g.finishes)==1 and g.finishes[0][1]=='UNKNOWN'
        finally:await t.aclose()
    asyncio.run(run())

@pytest.mark.parametrize('env,expected',[
 ({'done':True,'message':{'content':'ok'}},(None,None,'UNKNOWN')),
 ({'done':True,'message':{'content':'ok'},'prompt_eval_count':3},(3,None,'UNKNOWN')),
 ({'done':True,'message':{'content':'ok'},'prompt_eval_count':0,'eval_count':0},(0,0,'REPORTED'))])
def test_chat_usage_does_not_fill_missing_zero(env,expected):
    async def run():
        requests=[]
        def handler(r):
            requests.append(r)
            return tags() if r.url.path=='/api/tags' else httpx.Response(200,json=env)
        model,t,g=make(handler)
        try:
            out=await model.chat(MS,OPT,CTX)
            assert (out.usage.prompt_tokens,out.usage.completion_tokens,out.usage.status)==expected
            assert [r.method for r in requests]==['GET','POST']
            assert all('authorization' not in r.headers and 'x-private' not in r.headers for r in requests)
            assert len(g.calls)==len(g.finishes)==2 and g.calls[-1].output_budget==8
        finally:await t.aclose()
    asyncio.run(run())

class LiveBytes(httpx.AsyncByteStream):
    def __init__(self):self.eof=asyncio.Event();self.release=asyncio.Event();self.closed=False
    async def __aiter__(self):
        yield b'{"message":{"con';yield b'tent":"first"}}\n'
        await self.release.wait();yield b'{"done":true,"prompt_eval_count":0,"eval_count":0}\n';self.eof.set()
    async def aclose(self):self.closed=True

def test_true_stream_first_event_before_eof_cancel_closes_unknown():
    async def run():
        body=LiveBytes();model,t,g=make(lambda r:tags() if r.url.path=='/api/tags' else httpx.Response(200,stream=body))
        cancel=asyncio.Event();it=model.chat_stream(MS,OPT,replace(CTX,cancelled=cancel))
        try:
            first=await asyncio.wait_for(anext(it),1)
            assert first.content=='first' and not body.eof.is_set()
            task=asyncio.create_task(anext(it));await asyncio.sleep(0);cancel.set()
            with pytest.raises(CallCancelled):await task
            assert body.closed and g.finishes[-1][1]=='UNKNOWN'
        finally:await it.aclose();await t.aclose()
    asyncio.run(run())

@pytest.mark.parametrize('wire',[b'{"message":{"content":"partial"}}\n',b'{"error":"private synthetic failure"}\n',b'{"done":tru'])
def test_stream_error_or_truncation_never_success(wire):
    async def run():
        model,t,g=make(lambda r:tags() if r.url.path=='/api/tags' else httpx.Response(200,content=wire))
        try:
            events=[e async for e in model.chat_stream(MS,OPT,CTX)]
            assert any(e.response_type=='error' for e in events)
            assert not any(e.response_type=='answer' and e.done for e in events)
            assert g.finishes[-1][1]=='UNKNOWN'
        finally:await t.aclose()
    asyncio.run(run())

def test_closed_endpoint_set_cannot_be_expanded_by_service():
    async def run():
        requests=[];model,t,g=make(lambda r:requests.append(r),allowed=())
        try:
            with pytest.raises(ProviderError,match='ENDPOINT_NOT_ALLOWED'):await model.chat(MS,OPT,CTX)
            assert not requests and not g.calls
        finally:await t.aclose()
    asyncio.run(run())

@pytest.mark.parametrize('stream',[False,True])
def test_run_deadline_preexpired_zero_requests(stream):
    from backend.weknora_port.models.types import RunDeadlineExceeded
    async def run():
        seen=[];model,t,g=make(lambda r:seen.append(r))
        try:
            c=replace(CTX,deadline=asyncio.get_running_loop().time()-1,deadline_kind='run')
            with pytest.raises(RunDeadlineExceeded):
                if stream:[e async for e in model.chat_stream(MS,OPT,c)]
                else:await model.chat(MS,OPT,c)
            assert not seen and not g.calls
        finally:await t.aclose()
    asyncio.run(run())


def test_ensure_timeout_after_send_is_unknown_and_never_chat():
    async def run():
        seen=[]
        async def handler(r):seen.append(r.url.path);await asyncio.Event().wait()
        model,t,g=make(handler)
        try:
            with pytest.raises(ProviderError):await model.chat(MS,OPT,replace(CTX,deadline=asyncio.get_running_loop().time()+.03))
            assert seen==['/api/tags'] and len(g.finishes)==1 and g.finishes[0][1]=='UNKNOWN'
        finally:await t.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('stream',[False,True])
def test_explicit_wrong_model_is_rejected_without_fabricating_identity(stream):
    async def run():
        def handler(r):
            if r.url.path=='/api/tags':return tags()
            env={'model':'wrong:1b','message':{'content':'bad'},'done':True}
            return httpx.Response(200,content=json.dumps(env).encode()+b'\n')
        model,t,g=make(handler)
        try:
            if stream:
                events=[e async for e in model.chat_stream(MS,OPT,CTX)]
                assert any(e.response_type=='error' for e in events) and not any(e.response_type=='answer' and e.done for e in events)
            else:
                with pytest.raises(ProviderError,match='MODEL_IDENTITY_MISMATCH'):await model.chat(MS,OPT,CTX)
            assert g.finishes[-1][1]=='UNKNOWN'
        finally:await t.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('pull_wire',[b'{"error":"synthetic refused pull"}\n',b'{"status":"pulling"}\n'])
def test_explicitly_authorized_pull_failure_never_chats(pull_wire):
    async def run():
        service=HTTPLocalService(BASE,allow_pull=True);seen=[];gate=Gate()
        effective=local_connection_config(CFG,BASE,allow_pull=True)
        def handler(r):
            seen.append(r.url.path)
            return httpx.Response(200,json={'models':[]}) if r.url.path=='/api/tags' else httpx.Response(200,content=pull_wire)
        t=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(effective),gate=gate,http_transport=httpx.MockTransport(handler))
        model=ProviderModel(CFG,t,local_service=service)
        try:
            events=[e async for e in model.chat_stream(MS,OPT,CTX)]
            assert seen==['/api/tags','/api/pull']
            assert not any(e.response_type=='answer' and e.done for e in events)
            assert len(gate.calls)==len(gate.finishes)==2 and gate.finishes[-1][1]=='UNKNOWN'
        finally:await t.aclose()
    asyncio.run(run())


def test_explicit_authorized_pull_success_precedes_chat_on_same_gate():
    async def run():
        service=HTTPLocalService(BASE,allow_pull=True);seen=[];gate=Gate()
        config=local_connection_config(CFG,BASE,allow_pull=True)
        def handler(r):
            seen.append(r.url.path)
            if r.url.path=='/api/tags':return httpx.Response(200,json={'models':[]})
            if r.url.path=='/api/pull':return httpx.Response(200,content=b'{"status":"pulling"}\n{"status":"success"}\n')
            return httpx.Response(200,json={'done':True,'message':{'content':'ok'}})
        t=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(config),gate=gate,http_transport=httpx.MockTransport(handler))
        try:
            answer=await ProviderModel(CFG,t,local_service=service).chat(MS,OPT,CTX)
            assert answer.content=='ok' and seen==['/api/tags','/api/pull','/api/chat']
            assert [c.capability for c in gate.calls]==['local_model_check','local_model_pull','chat']
            assert len(gate.finishes)==3
        finally:await t.aclose()
    asyncio.run(run())
