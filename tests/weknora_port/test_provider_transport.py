"""All HTTP calls use httpx.MockTransport; no socket, DB or real credentials."""
import asyncio
import json
from dataclasses import replace
import httpx
import pytest
from backend.weknora_port.models import (AsyncHTTPTransport, ProviderModel, provider_config, Message,
    ChatOptions, CallContext, TrustedScope, ProviderError, ModelCallTimeout, CallCancelled, RunDeadlineExceeded)
from backend.weknora_port.models.transport import resolve_endpoint, call_lifetime


class Gate:
    def __init__(self, deny=False):
        self.calls, self.finishes, self.deny = [], [], deny
    async def authorize(self, call):
        if self.deny:
            raise ProviderError("MODEL_EGRESS_DENIED")
        if any(c.context.request_id == call.context.request_id for c in self.calls):
            raise ProviderError("MODEL_REQUEST_ALREADY_CLAIMED")
        self.calls.append(call)
    async def finish(self, call, *, outcome, usage):
        self.finishes.append((call, outcome, usage))


def context(identity="request"):
    return CallContext(identity,TrustedScope("owner",("kb",)),session_id="session")


def response(config,content="answer",**extra):
    return {"model":config.model,"choices":[{"message":{"content":content},"finish_reason":"stop"}],
            "usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15},**extra}


def setup(handler, *, config=None, gate=None, endpoints=None):
    config = config or provider_config("openai-compatible","m",base_url="https://fixture.example/v1")
    gate = gate or Gate()
    allowed = endpoints if endpoints is not None else [resolve_endpoint(config,path) for path in
        ("/chat/completions","/embeddings","/rerank")]
    transport = AsyncHTTPTransport(allowed_endpoints=allowed,gate=gate,
        credential=lambda provider:"SIMULATED-NOT-A-KEY",http_transport=httpx.MockTransport(handler))
    return ProviderModel(config,transport),transport,gate


def test_all_capabilities_share_transport_and_basic_response_mapping():
    async def run():
        seen = []
        cfg = provider_config("siliconflow","fixture",supports_vision=True)
        def handler(req):
            seen.append((str(req.url),json.loads(req.content),req.headers))
            if req.url.path.endswith("/embeddings"):
                return httpx.Response(200,json={"data":[{"index":1,"embedding":[2.]},{"index":0,"embedding":[1.]}]})
            if req.url.path.endswith("/rerank"):
                return httpx.Response(200,json={"results":[{"index":1,"score":.8,"document":"two"},{"index":0,"relevance_score":.2}]})
            return httpx.Response(200,json=response(cfg))
        model,transport,gate = setup(handler,config=cfg)
        try:
            assert (await model.chat((Message("user","q"),),ChatOptions(max_tokens=4096),context("chat"))).content == "answer"
            assert (await model.embed(("one","two"),context("embed"))).vectors == ((1.,),(2.,))
            assert [r.index for r in (await model.rerank("q",("one","two"),context("rank"))).results] == [1,0]
            await model.vision((Message("user","q",images=("data:image/png;base64,AA==",)),),ChatOptions(),context("vision"))
            assert [c.capability for c in gate.calls] == ["chat","embedding","rerank","vision"]
            assert len(gate.finishes) == 4
            assert gate.finishes[1][2].status == "UNKNOWN"
            assert all(headers["Authorization"] == "Bearer SIMULATED-NOT-A-KEY" for _,_,headers in seen)
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_missing_usage_unknown_and_wrong_model_identity_safe_failure():
    async def run():
        cfg = provider_config("deepseek","deepseek-flash")
        model,transport,gate = setup(lambda req:httpx.Response(200,json=response(cfg,usage=None)),config=cfg)
        try:
            out = await model.chat((Message("user","PRIVATE"),),ChatOptions(max_tokens=2048),context())
            assert out.usage.status == "UNKNOWN" and out.usage.prompt_tokens is None
        finally:
            await transport.aclose()
        model,transport,gate = setup(lambda req:httpx.Response(200,json=response(cfg,model="wrong-model")),config=cfg)
        try:
            with pytest.raises(ProviderError,match="PROVIDER_MODEL_IDENTITY_MISMATCH"):
                await model.chat((Message("user","PRIVATE"),),None,context())
            assert gate.finishes[0][2].total_tokens == 15
        finally:
            await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("failure",["egress","endpoint","missing_gate","scope","already_claimed"])
def test_pre_send_gate_endpoint_scope_and_identity(failure):
    async def run():
        sent = []
        model,transport,gate = setup(lambda req:sent.append(req) or httpx.Response(200,json={}),
            gate=Gate(deny=failure=="egress"),endpoints=[] if failure=="endpoint" else None)
        if failure=="missing_gate":
            from backend.weknora_port.models.transport import DenySendGate
            transport.gate = DenySendGate()
        ctx = context()
        if failure=="scope":
            ctx = replace(ctx,scope=TrustedScope("",()))
        if failure=="already_claimed":
            gate.calls.append(type("Claim",(),{"context":ctx})())
        try:
            with pytest.raises(ProviderError):
                await model.chat((Message("user","PRIVATE"),),None,ctx)
            assert sent == []
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_private_provider_errors_are_redacted_and_no_hidden_retry(caplog):
    async def run():
        requests=[]
        model,transport,gate = setup(lambda req:requests.append(req) or httpx.Response(503,text="SIMULATED-NOT-A-KEY PRIVATE"))
        try:
            with pytest.raises(ProviderError) as error:
                await model.chat((Message("user","PRIVATE"),),None,context())
            assert str(error.value) == "PROVIDER_HTTP_ERROR"
            assert error.value.status_code == 503 and error.value.sent
            assert len(requests) == len(gate.calls) == 1
            assert gate.finishes[0][2].status == "UNKNOWN"
            assert "PRIVATE" not in caplog.text and "SIMULATED-NOT-A-KEY" not in caplog.text
        finally:
            await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("kind,exception",[("model",ModelCallTimeout),("run",RunDeadlineExceeded)])
def test_explicit_deadline_timeout_distinguishes_model_from_run(kind,exception):
    async def run():
        requests=[]
        async def slow(req):
            requests.append(req)
            await asyncio.sleep(.2)
            return httpx.Response(200,json={})
        model,transport,gate = setup(slow)
        ctx = replace(context(),deadline=asyncio.get_running_loop().time()+.02,deadline_kind=kind)
        try:
            with pytest.raises(exception):
                await model.chat((Message("user","q"),),None,ctx)
            assert len(requests)==1 and len(gate.calls)==1
            assert gate.finishes[0][2].status == "UNKNOWN"
        finally:
            await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("error_type",[httpx.ConnectTimeout,httpx.ReadTimeout])
def test_connection_and_read_timeout_are_typed_without_cheap_retry(error_type):
    async def run():
        count=0
        def broken(req):
            nonlocal count
            count+=1
            raise error_type("PRIVATE",request=req)
        model,transport,gate=setup(broken)
        try:
            with pytest.raises(ModelCallTimeout):
                await model.chat((Message("user","q"),),None,context())
            assert count==1 and gate.finishes[0][2].status=="UNKNOWN"
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_user_cancellation_is_not_model_timeout_and_task_cancellation_propagates():
    async def run():
        entered=asyncio.Event()
        async def slow(req):
            entered.set()
            await asyncio.sleep(100)
        model,transport,gate=setup(slow)
        cancelled=asyncio.Event()
        try:
            task=asyncio.create_task(model.chat((Message("user","q"),),None,replace(context(),cancelled=cancelled)))
            await entered.wait();cancelled.set()
            with pytest.raises(CallCancelled):
                await task
            entered.clear()
            task=asyncio.create_task(model.chat((Message("user","q"),),None,context("task-cancel")))
            await entered.wait();task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert all(u.status=="UNKNOWN" for _,_,u in gate.finishes)
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_deadline_longer_than_fallback_is_respected_and_defaults_are_source_values():
    async def run():
        from backend.weknora_port.models.transport import DEFAULT_CHAT_TIMEOUT,DEFAULT_STREAM_TIMEOUT
        assert (DEFAULT_CHAT_TIMEOUT,DEFAULT_STREAM_TIMEOUT)==(300,600)
        ctx=replace(context(),deadline=asyncio.get_running_loop().time()+.2)
        async with call_lifetime(ctx,.005):
            await asyncio.sleep(.02)
    asyncio.run(run())


def test_headers_cache_affinity_and_endpoint_full_path():
    async def run():
        from backend.weknora_port.models import ChatSettings
        captured=[]
        cfg=provider_config("openai-compatible","m",base_url="https://fixture.example/v1",
            chat=replace(ChatSettings(),prompt_cache_key=True),
            custom_headers={"Authorization":"BAD","Content-Type":"BAD","Accept":"BAD","X-Trace":"ok"})
        model,transport,_=setup(lambda req:captured.append(req) or httpx.Response(200,json=response(cfg)),config=cfg)
        try:
            await model.chat((Message("user","q"),),None,context())
            request=captured[0]
            assert request.headers["Authorization"]=="Bearer SIMULATED-NOT-A-KEY"
            assert request.headers["Content-Type"]=="application/json" and request.headers["X-Trace"]=="ok"
            assert request.headers["session_id"]=="session"
            assert json.loads(request.content)["prompt_cache_key"]=="session"
            complete=replace(cfg,base_url="https://fixture.example/v1/embeddings")
            assert resolve_endpoint(complete,"/embeddings")==complete.base_url
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_upstream_multimodal_rejection_fallback_reauthorizes_new_identity_only():
    async def run():
        cfg=provider_config("siliconflow","m",supports_vision=True)
        sent=[]
        def handler(req):
            sent.append(json.loads(req.content))
            return httpx.Response(400,text="image unsupported") if len(sent)==1 else httpx.Response(200,json=response(cfg))
        model,transport,gate=setup(handler,config=cfg)
        try:
            out=await model.vision((Message("user","text",images=("data:image/png;base64,AA==",)),),None,context())
            assert out.content=="answer" and len(sent)==2
            assert sent[1]["messages"][0]["content"]=="text"
            assert gate.calls[1].context.request_id=="request/image-fallback"
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_admission_deadline_cannot_be_misclassified_as_a_model_timeout():
    async def run():
        class SlowGate(Gate):
            async def authorize(self,call):
                await asyncio.sleep(.1)
        sent=[]
        model,transport,_=setup(lambda req:sent.append(req) or httpx.Response(200,json={}),gate=SlowGate())
        try:
            with pytest.raises(ProviderError) as error:
                await model.chat((Message("user","q"),),None,
                    replace(context(),deadline=asyncio.get_running_loop().time()+.01))
            assert error.value.code=="MODEL_ADMISSION_TIMEOUT" and not isinstance(error.value,ModelCallTimeout)
            assert not error.value.sent and sent==[]
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_missing_or_failed_credentials_are_safe_not_sent_failures():
    async def run():
        for credential in (None,lambda provider:(_ for _ in ()).throw(RuntimeError("PRIVATE_SECRET"))):
            sent=[]
            model,transport,gate=setup(lambda req:sent.append(req) or httpx.Response(200,json={}))
            transport.credential=credential
            try:
                with pytest.raises(ProviderError) as error:
                    await model.chat((Message("user","q"),),None,context())
                assert not error.value.sent and "PRIVATE_SECRET" not in str(error.value) and sent==[]
                assert gate.finishes[0][1]=="NOT_SENT"
            finally:
                await transport.aclose()
    asyncio.run(run())


def test_gate_settlement_failure_prevents_a_successful_return():
    async def run():
        class BrokenFinish(Gate):
            async def finish(self,call,**kwargs):
                raise RuntimeError("PRIVATE_LEDGER")
        cfg=provider_config("openai-compatible","m",base_url="https://fixture.example/v1")
        model,transport,_=setup(lambda req:httpx.Response(200,json=response(cfg)),gate=BrokenFinish())
        try:
            with pytest.raises(ProviderError,match="MODEL_USAGE_SETTLEMENT_FAILED"):
                await model.chat((Message("user","q"),),None,context())
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_native_http_backend_default_gate_stays_closed_without_a_network_request():
    async def run():
        cfg=provider_config("deepseek","deepseek-flash")
        transport=AsyncHTTPTransport(allowed_endpoints=[resolve_endpoint(cfg,"/chat/completions")],
            credential=lambda provider:"SIMULATED-NOT-A-KEY")
        try:
            with pytest.raises(ProviderError,match="MODEL_SEND_AUTHORIZATION_REQUIRED"):
                await ProviderModel(cfg,transport).chat((Message("user","q"),),None,context())
            assert transport.live_ready is True
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_settlement_deadline_is_never_an_upgradeable_provider_timeout():
    async def run():
        class SlowFinish(Gate):
            async def finish(self,call,**kwargs):
                await asyncio.sleep(.1)
        cfg=provider_config("openai-compatible","m",base_url="https://fixture.example/v1")
        model,transport,_=setup(lambda req:httpx.Response(200,json=response(cfg)),gate=SlowFinish())
        try:
            with pytest.raises(ProviderError) as error:
                await model.chat((Message("user","q"),),None,
                    replace(context(),deadline=asyncio.get_running_loop().time()+.01))
            assert error.value.code=="MODEL_USAGE_SETTLEMENT_FAILED"
            assert not isinstance(error.value,ModelCallTimeout)
        finally:
            await transport.aclose()
    asyncio.run(run())
