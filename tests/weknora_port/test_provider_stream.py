"""Source-derived protocol stream fixture tests, httpx mock only."""
import asyncio
import json
from dataclasses import replace
import httpx
import pytest
from backend.weknora_port.models import (Message, ChatOptions, ProviderError, provider_config, CallCancelled)
from tests.weknora_port.test_provider_transport import setup, context


class Packets(httpx.AsyncByteStream):
    def __init__(self,packets,*,error=None,delay=0):
        self.packets,self.error,self.delay,self.closed=packets,error,delay,False
    async def __aiter__(self):
        for packet in self.packets:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield packet
        if self.error:
            raise self.error
    async def aclose(self):
        self.closed=True


def packet(delta=None,*,finish=None,usage=None,model=None):
    data={"choices":[] if delta is None else [{"delta":delta,"finish_reason":finish}]}
    if usage is not None:
        data["usage"]=usage
    if model is not None:
        data["model"]=model
    return b"data: "+json.dumps(data,ensure_ascii=False).encode()+b"\n\n"


def collect(body,**kwargs):
    async def run():
        model,transport,gate=setup(lambda req:httpx.Response(200,stream=body),**kwargs)
        try:
            events=[event async for event in model.chat_stream((Message("user","q"),),ChatOptions(max_tokens=4096),context())]
            return events,gate
        finally:
            await transport.aclose()
    return asyncio.run(run())


def test_long_stream_reasoning_answer_handoff_usage_unknown_and_single_transport():
    text="长正文😀"*3000
    body=Packets([b":comment\nid: ignored\n",packet({"reasoning_content":"r"}),
        packet({"content":text}),packet({},finish="stop"),b"data:[DONE]  \n"])
    events,gate=collect(body)
    assert [(e.response_type,e.done) for e in events] == [
        ("thinking",False),("thinking",True),("answer",False),("answer",True),("answer",True)]
    assert events[2].content==text and events[-1].is_final and events[-1].finish_reason=="stop"
    assert events[-1].usage is None
    assert gate.finishes[0][2].status=="UNKNOWN" and gate.finishes[0][2].prompt_tokens is None
    assert len(gate.calls)==1 and body.closed


def test_final_usage_only_chunk_and_cache_precedence():
    cfg=provider_config("deepseek","deepseek-flash")
    body=Packets([packet({"content":"answer"},finish="length"),packet(usage={
        "prompt_tokens":20,"completion_tokens":10,"total_tokens":30,
        "prompt_cache_hit_tokens":12,"prompt_cache_miss_tokens":8,
        "prompt_tokens_details":{"cached_tokens":19}}),b"data: [DONE]\n"])
    events,gate=collect(body,config=cfg)
    assert events[0].truncated and events[-1].truncated
    usage=events[-1].usage
    assert usage.cache_read_tokens==12 and usage.cache_miss_tokens==8 and usage.cache_status=="hit"
    assert usage.total_tokens==30 and gate.finishes[0][2]==usage


def test_tool_delta_order_gaps_repeated_name_and_early_notification():
    cfg=provider_config("openai-compatible","m",base_url="https://fixture.example/v1")
    cfg=replace(cfg,chat=replace(cfg.chat,tool_call_extra_fields=("extra_content",)))
    body=Packets([
        packet({"tool_calls":[{"index":3,"id":"b","function":{"name":"search","arguments":"{"},"extra_content":{"sig":"s"}},
                              {"index":1,"id":"a","function":{"name":"fetch","arguments":"{}"}}]}),
        packet({"tool_calls":[{"index":3,"function":{"name":"search","arguments":'"q":"x"}'}}]}),
        packet({},finish="tool_calls"),b"data:[DONE]\n"])
    events,_=collect(body,config=cfg)
    calls=events[-1].tool_calls
    assert [c.id for c in calls]==["a","b"] and calls[1].function.name=="search"
    assert calls[1].function.arguments=='{"q":"x"}' and calls[1].provider_metadata=={"extra_content":{"sig":"s"}}
    assert any(e.response_type=="tool_call" and e.data=={"tool_name":"search","tool_call_id":"b"} for e in events)
    assert events[-1].finish_reason=="tool_calls"


def test_thinking_tool_incremental_string_and_reasoning_metadata():
    body=Packets([
        packet({"tool_calls":[{"index":1,"id":"t","function":{"name":"thinking","arguments":'{"thought":"hel'}}]}),
        packet({"tool_calls":[{"index":1,"function":{"arguments":'lo\\nworld"}'}}],"reasoning_details":[{"opaque":1}]}),
        packet({},finish="tool_calls"),b"data:[DONE]\n"])
    events,_=collect(body)
    assert "".join(e.content for e in events if e.response_type=="thinking")=="hello\nworld"
    assert events[-1].data["reasoning_metadata"] == {"reasoning_details":[{"opaque":1}]}
    assert events[-1].data["model_requested"] == "m"
    assert events[-1].data["model_reported"] is None


def test_clean_eof_is_upstream_end_not_an_invented_failure_or_stop():
    events,gate=collect(Packets([packet({"content":"partial"})]))
    assert events[-1].done and events[-1].is_final and events[-1].finish_reason==""
    assert events[-1].response_type=="answer" and gate.finishes[0][2].status=="UNKNOWN"


@pytest.mark.parametrize("kind,expected",[("socket","PROVIDER_TRANSPORT_ERROR"),
    ("json","PROVIDER_RESPONSE_INVALID"),("wrong_model","PROVIDER_MODEL_IDENTITY_MISMATCH"),
    ("tool","PROVIDER_TOOL_CALL_INVALID"),("timeout","MODEL_CALL_TIMEOUT")])
def test_broken_streams_emit_error_terminal_never_success(kind,expected):
    packets=[packet({"reasoning_content":"private thought"})]
    error=None
    if kind=="socket":
        error=httpx.ReadError("PRIVATE_SOCKET")
    elif kind=="json":
        packets.append(b'data:{"bad":PRIVATE}\n')
    elif kind=="wrong_model":
        packets.append(packet({"content":"bad"},model="wrong"))
    elif kind=="tool":
        packets.append(packet({"tool_calls":[{"function":{"arguments":{"invalid":1}}}]}))
    else:
        error=httpx.ReadTimeout("PRIVATE_TIMEOUT")
    events,gate=collect(Packets(packets,error=error))
    assert events[-1].response_type=="error" and events[-1].is_final
    assert events[-1].finish_reason=="incomplete" and events[-1].content==expected
    assert "PRIVATE" not in events[-1].content
    assert gate.finishes[0][2].status=="UNKNOWN"


def test_cancelled_stream_closes_body_and_propagates_cancel_without_upgrade_signal():
    async def run():
        body=Packets([packet({"content":"one"}),packet({"content":"two"})],delay=.01)
        model,transport,gate=setup(lambda req:httpx.Response(200,stream=body))
        cancelled=asyncio.Event()
        try:
            stream=model.chat_stream((Message("user","q"),),None,replace(context(),cancelled=cancelled))
            first=await anext(stream)
            assert first.content=="one"
            cancelled.set()
            with pytest.raises(CallCancelled):
                await anext(stream)
            assert body.closed and gate.finishes[0][2].status=="UNKNOWN"
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_consumer_close_stops_reading_and_closes_stream_without_new_request():
    async def run():
        body=Packets([packet({"content":"one"}),packet({"content":"two"})])
        model,transport,gate=setup(lambda req:httpx.Response(200,stream=body))
        try:
            stream=model.chat_stream((Message("user","q"),),None,context())
            await anext(stream)
            await stream.aclose()
            assert body.closed and len(gate.calls)==1 and gate.finishes[0][2].status=="UNKNOWN"
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_sse_one_mib_bound_and_chunk_split_utf8():
    long=b"data:"+b"x"*(1<<20)+b"\n"
    events,_=collect(Packets([long]))
    assert events[-1].content=="PROVIDER_SSE_LINE_TOO_LONG"
    raw=packet({"content":"😀"})
    events,_=collect(Packets([raw[:20],raw[20:25],raw[25:],b"data:[DONE]\n"]))
    assert events[0].content=="😀"


def test_stream_consumer_close_from_cleanup_task_has_no_context_token_error():
    async def run():
        body=Packets([packet({"content":"one"}),packet({"content":"two"})])
        model,transport,gate=setup(lambda req:httpx.Response(200,stream=body))
        try:
            stream=model.chat_stream((Message("user","q"),),None,context())
            await anext(stream)
            await asyncio.create_task(stream.aclose())
            assert body.closed and len(gate.finishes)==1
        finally:
            await transport.aclose()
    asyncio.run(run())
