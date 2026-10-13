"""Native fixed-source wire/usage/stream/replay fixtures, exclusively offline."""
import asyncio
import json
import math
from dataclasses import replace
import httpx
import pytest
from backend.weknora_port.models import (provider_config,ProviderModel,AsyncHTTPTransport,Message,
    ChatOptions,ProviderError,Tool,ToolCall,FunctionCall)
from backend.weknora_port.models.provider import normalize_rerank_response
from backend.weknora_port.models.native_response import parse_native_response
from backend.weknora_port.models.transport import endpoint_urls,configured_endpoint
from backend.weknora_port.models.types import RerankResponse,RerankResult
from tests.weknora_port.test_provider_transport import Gate,context

TOOLS=(Tool("lookup","Search",{"type":"object","properties":{"title":{"type":"string","default":"x"}},"additionalProperties":False}),)
MESSAGES=(Message("system","Use evidence."),Message("user","Question"))


def config(api):
    return provider_config("generic","m",base_url="https://fixture.example/v1",extra_config={"api":api},
        spec_override={"reasoning":True})


@pytest.mark.parametrize("api",["openai-responses","anthropic-messages","google-generative-ai"])
def test_native_budget_tools_headers_and_endpoint_exact(api):
    async def run():
        cfg=config(api);sent=[];gate=Gate()
        payload={"openai-responses":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}],"usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7}},
                 "anthropic-messages":{"content":[{"type":"text","text":"OK"}],"stop_reason":"end_turn","usage":{"input_tokens":5,"output_tokens":2}},
                 "google-generative-ai":{"candidates":[{"content":{"parts":[{"text":"OK"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":2,"totalTokenCount":7}}}[api]
        transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg),gate=gate,credential=lambda _:"FIXTURE",http_transport=httpx.MockTransport(lambda req:sent.append(req) or httpx.Response(200,json=payload)))
        model=ProviderModel(cfg,transport)
        try:
            result=await model.chat(MESSAGES,ChatOptions(max_tokens=256,tools=TOOLS,tool_choice="required",reasoning_effort="high"),context())
            assert result.content=="OK" and result.usage.total_tokens==7 and result.usage.status=="REPORTED"
            req=sent[0];body=json.loads(req.content)
            assert str(req.url)==configured_endpoint(cfg)
            assert "messages" not in body if api!="anthropic-messages" else True
            if api=="openai-responses":
                assert body["input"]==[{"type":"message","role":"user","content":"Question"}]
                assert body["instructions"]=="Use evidence." and body["reasoning"]=={"effort":"high","summary":"auto"}
                assert body["max_output_tokens"]==256 and body["tools"][0]["name"]=="lookup"
            elif api=="anthropic-messages":
                assert body["thinking"]=={"type":"enabled","budget_tokens":16384}
                assert body["max_tokens"]==17408 and req.headers["anthropic-version"]=="2023-06-01"
                assert "interleaved-thinking-2025-05-14" in req.headers["anthropic-beta"]
                assert body["tool_choice"]["type"]=="any"
            else:
                assert body["generationConfig"]["maxOutputTokens"]==256
                schema=body["tools"][0]["functionDeclarations"][0]["parameters"]
                assert "additionalProperties" not in schema and "title" in schema["properties"] and "default" not in schema["properties"]["title"]
            assert gate.calls[0].output_budget==body.get("max_tokens",body.get("max_output_tokens",body.get("generationConfig",{}).get("maxOutputTokens")))
        finally: await transport.aclose()
    asyncio.run(run())


def test_native_auth_styles_and_no_bearer_fallback_for_native_header():
    async def run():
        cases=[("azure_openai","gpt-4o","api-key"),("anthropic","claude-sonnet-4-6","x-api-key"),("gemini","gemini-2.5-flash","x-goog-api-key")]
        for provider,modelname,header in cases:
            cfg=provider_config(provider,modelname,base_url="https://fixture.example/v1")
            sent=[];transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg),gate=Gate(),credential=lambda _:"FIXTURE",http_transport=httpx.MockTransport(lambda req:sent.append(req) or httpx.Response(401,json={})))
            try:
                with pytest.raises(ProviderError): await ProviderModel(cfg,transport).chat(MESSAGES,None,context())
                assert sent[0].headers[header]=="FIXTURE" and "Authorization" not in sent[0].headers
                assert "FIXTURE" not in str(sent[0].url)
            finally: await transport.aclose()
    asyncio.run(run())


def test_opaque_signed_thinking_and_responses_reasoning_roundtrip():
    anth=config("anthropic-messages")
    blocks=[{"type":"thinking","thinking":"opaque <| text","signature":"SIG1"},{"type":"redacted_thinking","data":"OPAQUE"},{"type":"thinking","thinking":"unfinished"}]
    msg=Message("assistant","Answer",reasoning_content="rewritten",reasoning_metadata={"anthropic_thinking_blocks":blocks})
    body=ProviderModel(anth,None).build_request_body((msg,),ChatOptions(cache_retention="none"))
    assert body["messages"][0]["content"][:2]==blocks[:2]
    responses=config("openai-responses")
    item={"type":"reasoning","id":"r1","encrypted_content":"OPAQUE","summary":[]}
    call=ToolCall("call1",function=FunctionCall("lookup","{}"),provider_metadata={"openai_responses_item_id":"item1"})
    body=ProviderModel(responses,None).build_request_body((Message("assistant",tool_calls=(call,),reasoning_metadata={"openai_responses_reasoning":[item]}),))
    assert body["input"]==[item,{"type":"function_call","id":"item1","call_id":"call1","name":"lookup","arguments":"{}"}]


def test_gemini_function_signature_and_tool_result_batch_replay():
    cfg=config("google-generative-ai")
    call=ToolCall("c1",function=FunctionCall("lookup","{}"),provider_metadata={"thought_signature":"SIG"})
    messages=(Message("assistant",tool_calls=(call,)),Message("tool",'{"x":1}',tool_call_id="c1"),Message("tool","plain",tool_call_id="c1"))
    body=ProviderModel(cfg,None).build_request_body(messages)
    assert body["contents"][0]["parts"][0]["thoughtSignature"]=="SIG"
    assert len(body["contents"])==2 and len(body["contents"][1]["parts"])==2
    assert body["contents"][1]["parts"][0]["functionResponse"]["response"]=={"result":{"x":1}}


def _sse(events):
    return "".join("data: "+json.dumps(e)+"\n\n" for e in events).encode()


@pytest.mark.parametrize("api,events,finish,answer",[
    ("openai-responses",[{"type":"response.reasoning_summary_text.delta","delta":"think"},{"type":"response.output_text.delta","delta":"OK"},{"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7},"output":[{"type":"reasoning","encrypted_content":"opaque"}]}}],"stop","OK"),
    ("anthropic-messages",[{"type":"message_start","message":{"usage":{"input_tokens":5,"output_tokens":0}}},{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"think"}},{"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"SIG"}},{"type":"content_block_start","index":1,"content_block":{"type":"text","text":"OK"}},{"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":2}},{"type":"message_stop"}],"length","OK"),
    ("google-generative-ai",[{"candidates":[{"content":{"parts":[{"text":"think","thought":True},{"text":"OK","thoughtSignature":"SIG"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":1,"thoughtsTokenCount":1,"totalTokenCount":7}}],"stop","OK")])
def test_native_stream_state_usage_finish_metadata_and_single_final(api,events,finish,answer):
    async def run():
        cfg=config(api);gate=Gate();sent=[]
        transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg),gate=gate,http_transport=httpx.MockTransport(lambda req:sent.append(req) or httpx.Response(200,content=_sse(events),headers={"content-type":"text/event-stream"})))
        try:
            out=[e async for e in ProviderModel(cfg,transport).chat_stream(MESSAGES,ChatOptions(max_tokens=256),context())]
            assert len(sent)==1 and sum(e.is_final for e in out)==1
            assert "".join(e.content for e in out if e.response_type=="answer")==answer
            assert out[-1].finish_reason==finish and out[-1].usage.total_tokens==7
            assert gate.finishes[0][2].total_tokens==7
            if api=="openai-responses": assert out[-1].data["reasoning_metadata"]["openai_responses_reasoning"][0]["encrypted_content"]=="opaque"
            else: assert out[-1].data["reasoning_metadata"]["reasoning_signature"]==api+":SIG"
        finally: await transport.aclose()
    asyncio.run(run())


def test_anthropic_open_tool_is_incomplete_and_never_replayed_as_complete():
    async def run():
        cfg=config("anthropic-messages")
        events=[{"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"c1","name":"lookup","input":{}}},{"type":"message_delta","delta":{"stop_reason":"tool_use"}},{"type":"message_stop"}]
        transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg),gate=Gate(),http_transport=httpx.MockTransport(lambda req:httpx.Response(200,content=_sse(events))))
        try:
            out=[e async for e in ProviderModel(cfg,transport).chat_stream(MESSAGES,None,context())]
            assert out[-1].finish_reason=="incomplete" and not out[-1].tool_calls
        finally: await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("api,env",[("openai-responses",{"output":[None]}),("anthropic-messages",{"content":[False]}),("google-generative-ai",{"candidates":[{"content":{"parts":[False]}}]})])
def test_native_malformed_response_safe_failure(api,env):
    with pytest.raises(ProviderError,match="PROVIDER_RESPONSE_INVALID"):
        parse_native_response(env,config(api))


def test_logit_normalize_text_fill_stable_sort_and_invalid_index_source_behavior():
    response=RerankResponse((RerankResult(0,0),RerankResult(1,0,"own"),RerankResult(2,1000),RerankResult(3,-1000)),score_scale="logit")
    out=normalize_rerank_response(response,["a","b","c","d"])
    assert [(r.index,r.score,r.text) for r in out.results]==[(2,1,"c"),(0,.5,"a"),(1,.5,"own"),(3,0,"d")]
    assert out.score_scale=="probability" and response.score_scale=="logit"
    with pytest.raises(ProviderError,match="PROVIDER_RERANK_INVALID"):
        normalize_rerank_response(RerankResponse((RerankResult(-1,.8),)),["a"])


@pytest.mark.parametrize("provider,model,response,body_key",[
    ("volcengine","doubao-embedding-vision-250615",{"data":{"embedding":[.1,.2]}},"input"),
    ("gemini","gemini-embedding-001",{"embeddings":[{"values":[.1,.2]}]},"requests"),
    ("aliyun","tongyi-embedding-vision-plus",{"output":{"embeddings":[{"index":0,"embedding":[.1,.2]}]}},"input")])
def test_native_embedding_wire_shape_vector_count_and_exact_endpoint(provider,model,response,body_key):
    async def run():
        cfg=provider_config(provider,model,model_type="Embedding",base_url="https://fixture.example/v1");sent=[]
        transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg),gate=Gate(),credential=lambda _:"FIXTURE",http_transport=httpx.MockTransport(lambda req:sent.append(req) or httpx.Response(200,json=response)))
        try:
            out=await ProviderModel(cfg,transport).embed(["text"],context())
            assert out.vectors==((.1,.2),) and out.usage.status=="UNKNOWN"
            body=json.loads(sent[0].content)
            assert body_key in body and str(sent[0].url)==configured_endpoint(cfg,"embedding")
            if provider=="volcengine": assert body["input"]==[{"type":"text","text":"text"}]
            if provider=="aliyun": assert body["input"]=={"contents":[{"text":"text"}]}
        finally: await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("provider,model,response,want",[
    ("aliyun","gte-rerank-v2",{"output":{"results":[{"index":1,"relevance_score":.8}]}},.8),
    ("nvidia","nvidia/nv-rerankqa-mistral-4b-v3",{"rankings":[{"index":1,"logit":-2}]},-2),
    ("lkeap","lke-reranker-base",{"Response":{"ScoreList":[.3,.8]}},.3),
    ("volcengine","doubao-seed-rerank",{"code":0,"data":{"scores":[.3,.8]}},.3)])
def test_native_rerank_wire_and_signed_protocols(provider,model,response,want):
    async def run():
        cfg=provider_config(provider,model,model_type="Rerank",base_url="https://fixture.example/v1",extra_config={"secret_key":"FIXTURESECRET"});sent=[]
        transport=AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg),gate=Gate(),credential=lambda _:"FIXTUREID",http_transport=httpx.MockTransport(lambda req:sent.append(req) or httpx.Response(200,json=response)))
        try:
            out=await ProviderModel(cfg,transport).rerank("q",["a","b"],context())
            assert out.results[0].score==want
            req=sent[0];body=json.loads(req.content)
            if provider=="aliyun": assert body["parameters"]["top_n"]==2
            if provider=="nvidia": assert body["passages"]==[{"text":"a"},{"text":"b"}]
            if provider=="lkeap": assert body=={"Docs":["a","b"],"Model":model,"Query":"q"} and req.headers["X-TC-Action"]=="RunRerank" and req.headers["Authorization"].startswith("TC3-HMAC-SHA256")
            if provider=="volcengine": assert body["datas"]==[{"query":"q","content":"a"},{"query":"q","content":"b"}] and req.headers["Authorization"].startswith("HMAC-SHA256")
            assert "FIXTURESECRET" not in str(req.url)
        finally: await transport.aclose()
    asyncio.run(run())
