"""Local Ollama chat branch: mock transport + mock local service, no network.

Source-derived Python fixtures for Tencent/WeKnora@3e8b0bfc
internal/models/chat/ollama.go and internal/models/utils/ollama/ollama.go.
No real Ollama, model download, cloud or provider request is made.
"""
import asyncio
import json

import httpx
from dataclasses import replace
import pytest

from backend.weknora_port.models import (ChatOptions, Message, ModelConfig, Tool, ToolCall,
    FunctionCall, ProviderError, StreamResponse)
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.local_ollama import (build_ollama_body, parse_ollama_response,
    convert_ollama_messages, OllamaStreamState, ndjson_events, HTTPLocalService)
from backend.weknora_port.models.types import CallContext, TrustedScope


class RecordingTransport:
    """Stands in for AsyncHTTPTransport; any use is a hard failure for local."""

    def __init__(self):
        self.gate = object()
        self.opens = 0

    def open(self, *args, **kwargs):
        self.opens += 1
        raise AssertionError("local chat must never touch the remote transport")


class FakeLocalService(HTTPLocalService):
    def __init__(self,responses=(),*,ensure_error=None,chat_error=None):
        super().__init__('http://127.0.0.1:12499',allow_pull=False)
        self.responses,self.ensure_error,self.chat_error=list(responses),ensure_error,chat_error
        self.ensured,self.bodies=[],[]
    async def ensure_model_available(self,model_name,**controlled):
        self.ensured.append(model_name)
        if self.ensure_error is not None:raise self.ensure_error
        await super().ensure_model_available(model_name,**controlled)


def make_local(service):
    from backend.weknora_port.models.transport import AsyncHTTPTransport,endpoint_urls
    from backend.weknora_port.models.local_ollama import local_connection_config
    from tests.weknora_port.test_provider_transport import Gate
    def handler(request):
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'qwen3:8b'}]})
        body=json.loads(request.content);service.bodies.append((body['model'],body))
        if service.chat_error is not None:raise service.chat_error
        if body['stream']:return httpx.Response(200,content=b''.join(json.dumps(v).encode()+b'\n' for v in service.responses))
        return httpx.Response(200,json=service.responses[0])
    config=local_config()
    transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(local_connection_config(config,service.base_url)),gate=Gate(),http_transport=httpx.MockTransport(handler))
    return ProviderModel(config,transport,local_service=service)


def local_config(**kw):
    base = dict(provider="ollama", model="qwen3:8b", model_id="local-1",
                base_url="http://inline.invalid/never-used", api="ollama",
                source="local", requires_auth=False)
    base.update(kw)
    return ModelConfig(**base)


def context():
    return CallContext("run", TrustedScope("owner", ("kb",)))


# ------------------------------------------------------------------- request

def test_local_request_body_is_exact_ollama_wire():
    tool = Tool("search", "d", {"type": "object", "properties": {}})
    messages = (
        Message("system", "sys"), Message("user", "hi"),
        Message("assistant", tool_calls=(ToolCall("1", function=FunctionCall("search", '{"q":"x"}')),)),
        Message("tool", "result", name="search"))
    options = ChatOptions(temperature=.7, top_p=.8, max_tokens=1000, reasoning_effort="medium",
                          tools=(tool,))
    body = build_ollama_body(local_config(), messages, options, stream=True)
    assert body == {
        "model": "qwen3:8b", "stream": True,
        "messages": [
            {"role": "system", "content": "sys"}, {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"index": 1, "name": "search", "arguments": {"q": "x"}}}]},
            {"role": "tool", "content": "result", "tool_name": "search"}],
        "options": {"temperature": .7, "top_p": .8, "num_predict": 1000},
        "think": True,
        "tools": [{"type": "function", "function": {"name": "search", "description": "d",
                                                   "parameters": {"type": "object", "properties": {}}}}]}
    assert ProviderModel(local_config(), RecordingTransport()).build_request_body(messages, options) == {
        **body, "stream": False}


def test_local_options_think_and_format_guards():
    # Only temperature is unconditional; top_p/num_predict/think/format are optional.
    bare = build_ollama_body(local_config(), (Message("user", "hi"),), None)
    assert bare == {"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    off = build_ollama_body(local_config(), (Message("user", "hi"),),
                            ChatOptions(temperature=0, reasoning_effort="off"))
    assert off["options"] == {"temperature": 0} and off["think"] is False
    thinking = build_ollama_body(local_config(), (Message("user", "hi"),), ChatOptions(thinking=True))
    assert thinking["think"] is True
    schema = build_ollama_body(local_config(), (Message("user", "hi"),),
                               ChatOptions(format='{"type":"object"}'))
    assert schema["format"] == {"type": "object"}


def test_local_images_only_inlined_for_user_role_and_multicontent_merge():
    messages = (
        Message("user", "", multi_content=(
            {"type": "text", "text": "look"}, {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,AA=="}})),
        Message("assistant", images=("data:image/png;base64,AA==",)))
    converted = convert_ollama_messages(local_config(), messages)
    assert converted[0]["content"] == "look" and converted[0]["images"] == ["AA=="]
    assert "images" not in converted[1]  # non-user images are never inlined


# ------------------------------------------------------------------ response

def test_local_non_stream_thinking_fallback_tools_and_usage():
    env = {"message": {"content": "", "thinking": "reasoned"}, "done": True,
           "prompt_eval_count": 11, "eval_count": 22}
    response = parse_ollama_response(env, local_config())
    assert response.content == "reasoned" and response.tool_calls == ()
    assert (response.usage.prompt_tokens, response.usage.completion_tokens,
            response.usage.total_tokens, response.usage.status,
            response.usage.cache_status) == (11, 22, 33, "REPORTED", "unsupported")

    tools = {"message": {"content": "go", "tool_calls": [
        {"function": {"index": 0, "name": "search", "arguments": {"q": "x"}}}]},
        "prompt_eval_count": 5, "eval_count": 7}
    response = parse_ollama_response(tools, local_config())
    assert response.content == "go"
    assert response.tool_calls == (ToolCall("0", "function", FunctionCall("search", '{"q":"x"}')),)


def test_local_chat_uses_injected_service_and_records_exact_body():
    async def run():
        service = FakeLocalService([{"message": {"content": "hi there"}, "prompt_eval_count": 4,
                                     "eval_count": 6, "done": True}])
        model = make_local(service)
        response = await model.chat((Message("user", "hi"),),
                                    ChatOptions(temperature=.2, max_tokens=64), context())
        assert response.content == "hi there" and response.usage.total_tokens == 10
        assert service.ensured == ["qwen3:8b"]
        assert service.bodies == [("qwen3:8b", {
            "model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}],
            "stream": False, "options": {"temperature": .2, "num_predict": 64}})]
    asyncio.run(run())


# --------------------------------------------------------------- streaming

def test_local_stream_order_thinking_answer_done_usage():
    async def run():
        service = FakeLocalService([
            {"message": {"thinking": "t1"}},
            {"message": {"content": "a1"}},
            {"done": True, "prompt_eval_count": 3, "eval_count": 4}])
        model = make_local(service)
        events = [event async for event in model.chat_stream((Message("user", "hi"),), None, context())]
        assert [(event.response_type, event.content, event.done, event.is_final) for event in events] == [
            ("thinking", "t1", False, False),
            ("thinking", "", True, False),
            ("answer", "a1", False, False),
            ("answer", "", True, True)]
        assert events[-1].usage.total_tokens == 7
        assert all(event.data["model_requested"] == "qwen3:8b" for event in events)
        assert service.bodies[0][1]["stream"] is True
    asyncio.run(run())


def test_local_stream_tool_call_and_thinking_tool_event():
    async def run():
        service = FakeLocalService([
            {"message": {"tool_calls": [
                {"function": {"index": 0, "name": "search", "arguments": {"q": "x"}}},
                {"function": {"index": 1, "name": "thinking", "arguments": {"thought": "hmm"}}}]}},
            {"done": True}])
        model = make_local(service)
        events = [event async for event in model.chat_stream((Message("user", "hi"),), None, context())]
        tool = next(event for event in events if event.response_type == "tool_call")
        assert tuple(call.id for call in tool.tool_calls) == ("0", "1")
        hint = next(event for event in events if event.response_type == "thinking" and event.content == "hmm")
        assert hint.data["source"] == "thinking_tool"
    asyncio.run(run())


def test_local_stream_and_chat_fail_loudly_never_fall_back_to_remote():
    async def run():
        transport = RecordingTransport()
        missing = ProviderModel(local_config(), transport)
        with pytest.raises(ProviderError, match="MODEL_LOCAL_SERVICE_UNAVAILABLE"):
            await missing.chat((Message("user", "hi"),), None, context())
        error_events = [event async for event in missing.chat_stream((Message("user", "hi"),), None, context())]
        assert [event.response_type for event in error_events] == ["error"]
        assert error_events[0].data["error_code"] == "MODEL_LOCAL_SERVICE_UNAVAILABLE"
        service = FakeLocalService(ensure_error=ProviderError("MODEL_LOCAL_MODEL_UNAVAILABLE"))
        unavailable = ProviderModel(local_config(), transport, local_service=service)
        with pytest.raises(ProviderError, match="MODEL_LOCAL_MODEL_UNAVAILABLE"):
            await unavailable.chat((Message("user", "hi"),), None, context())
        events = [event async for event in unavailable.chat_stream((Message("user", "hi"),), None, context())]
        assert [event.response_type for event in events] == ["error"]
        assert transport.opens == 0 and service.bodies == []
    asyncio.run(run())


# ------------------------------------------------------------- NDJSON decode

class FakeStreamResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


def test_ndjson_decodes_one_object_per_line_across_chunks():
    async def run():
        return [item async for item in ndjson_events(
            FakeStreamResponse([b'{"a":1}\n{"b', b'":2}\n\n{"c":3}']))]
    assert asyncio.run(run()) == [{"a": 1}, {"b": 2}, {"c": 3}]


def test_ndjson_rejects_invalid_line():
    async def run():
        return [item async for item in ndjson_events(FakeStreamResponse([b"not json\n"]))]
    with pytest.raises(ProviderError, match="PROVIDER_RESPONSE_INVALID"):
        asyncio.run(run())


# ------------------------------------------------- default HTTP local service

def test_http_local_service_over_mock_transport():
    async def run():
        service=FakeLocalService([{'message':{'content':'one'},'done':True,'prompt_eval_count':1,'eval_count':1}])
        model=make_local(service)
        try:
            answer=await model.chat((Message('user','hi'),),None,context())
            events=[e async for e in model.chat_stream((Message('user','hi'),),None,replace(context(),request_id='second'))]
            assert answer.content=='one' and any(e.done and e.response_type=='answer' for e in events)
            assert len(model.transport.gate.calls)==4
        finally:await model.transport.aclose()
    asyncio.run(run())


def test_http_local_service_missing_model_is_not_pulled():
    from tests.weknora_port.test_local_controlled import make,MS,OPT,CTX
    async def run():
        seen=[]
        def handler(r):seen.append(r.url.path);return httpx.Response(200,json={'models':[]})
        model,transport,gate=make(handler)
        try:
            with pytest.raises(ProviderError,match='MODEL_LOCAL_MODEL_UNAVAILABLE'):await model.chat(MS,OPT,CTX)
            assert seen==['/api/tags']
        finally:await transport.aclose()
    asyncio.run(run())


def test_ollama_stream_state_tool_only_then_done_keeps_explicit_done():
    state = OllamaStreamState(local_config())
    calls = state.feed({"message": {"tool_calls": [{"function": {"index": 0, "name": "fn"}}]}})
    assert [event.response_type for event in calls] == ["tool_call"]
    done = state.feed({"done": True})
    assert done[-1].response_type == "answer" and done[-1].done and done[-1].is_final
