"""Offline golden semantic fields transcribed from pinned Go golden tests.

These are source-derived Python fixtures, not a claim that Go was executed.
"""
from dataclasses import replace
import pytest
from backend.weknora_port.models import (ChatOptions, Message, Tool, ToolCall, FunctionCall,
    ChatSettings, EmbeddingSettings, RerankSettings, ProviderError, provider_config,
    build_chat_body, build_embedding_body, build_rerank_body, ProviderModel)
from backend.weknora_port.models.request import split_batches, neutralize


def config(provider="openai-compatible", model="m", **kw):
    return provider_config(provider, model, base_url="https://fixture.example/v1", **kw)


CONVERSATION = (
    Message("system", "You are helpful."), Message("user", "hi"),
    Message("assistant", reasoning_content="thinking...", tool_calls=(
        ToolCall("call_1", function=FunctionCall("search", '{"q":"x"}')),)),
    Message("tool", "result", tool_call_id="call_1", name="search"))
OPTIONS = ChatOptions(temperature=.7, max_tokens=1000, tools=(Tool("search", "d", {"type":"object","properties":{}}),),
                      tool_choice="required", parallel_tool_calls=True)


def test_openai_baseline_golden_matches_source_semantic_fields():
    c = config(chat=replace(ChatSettings(), supports_store=True, supports_developer_role=True,
                            thinking_format="openai", supports_reasoning_effort=True))
    actual = build_chat_body(c, CONVERSATION, OPTIONS, stream=True)
    assert actual == {
        "model":"m", "messages":[
            {"role":"system","content":"You are helpful."}, {"role":"user","content":"hi"},
            {"role":"assistant","tool_calls":[{"id":"call_1","type":"function","function":{"name":"search","arguments":'{"q":"x"}'}}],
                "reasoning_content":"thinking..."},
            {"role":"tool","content":"result","tool_call_id":"call_1","name":"search"}],
        "stream":True, "stream_options":{"include_usage":True}, "store":False,
        "temperature":.7, "max_completion_tokens":1000, "tools":[{
            "type":"function", "function":{"name":"search","description":"d","parameters":{"type":"object","properties":{}}}}],
        "tool_choice":"required", "parallel_tool_calls":True}


@pytest.mark.parametrize("provider,thinking", [("siliconflow",{"enable_thinking":False}),
                                              ("deepseek",{"thinking":{"type":"disabled"}})])
def test_vendor_configuration_not_role_fork(provider, thinking):
    c = config(provider)
    body = build_chat_body(c, (Message("user","hi"),), ChatOptions(max_tokens=1024, thinking=False), stream=True)
    assert body["max_tokens"] == 1024 and "max_completion_tokens" not in body
    assert body | thinking == body
    assert body["stream_options"] == {"include_usage":True}


def test_deepseek_golden_effort_clamp_and_unsupported_tool_choice():
    body = build_chat_body(config("deepseek"), CONVERSATION, replace(OPTIONS, reasoning_effort="medium"))
    assert body["thinking"] == {"type":"enabled"}
    assert body["reasoning_effort"] == "high" and "tool_choice" not in body
    assert body["parallel_tool_calls"] is True
    assert "stream" not in body  # upstream omits false on non-streaming requests


def test_catalog_capacity_is_separate_and_xing_capacity_is_not_invented():
    deep = provider_config("deepseek", "deepseek-flash")
    xing = provider_config("siliconflow", "XingChenAGI/Xing4.0-29B")
    assert (deep.context_window, deep.max_output_tokens) == (1_000_000,384_000)
    assert deep.supports_vision and deep.reasoning
    assert xing.context_window == xing.max_output_tokens == 0
    assert build_chat_body(xing, (Message("user","hi"),), ChatOptions(max_tokens=4096))["max_tokens"] == 4096


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
def test_single_completion_budget_caller_precedence_extra_body_and_no_512_gate(field):
    c = config(chat=replace(ChatSettings(), max_tokens_field=field,
        extra_body={"max_tokens":40,"max_completion_tokens":50,"temperature":.2}))
    body = build_chat_body(c, (Message("user","hi"),), ChatOptions(max_tokens=1000,max_completion_tokens=4096))
    assert body[field] == 4096
    assert ("max_tokens" in body) != ("max_completion_tokens" in body)


def test_sampling_matches_actual_greater_than_zero_behavior():
    c = config(chat=replace(ChatSettings(), supports_developer_role=True), reasoning=True)
    body = build_chat_body(c, (Message("system","s"),), ChatOptions(temperature=0,top_p=.8,seed=42,
        frequency_penalty=-.2,presence_penalty=.4))
    assert body == {"model":"m","messages":[{"role":"developer","content":"s"}],
                    "top_p":.8,"seed":42,"presence_penalty":.4}
    fixed = build_chat_body(replace(c, chat=replace(c.chat,fixed_temperature=1)), CONVERSATION, OPTIONS)
    assert fixed["temperature"] == 1 and "top_p" not in fixed
    disabled = build_chat_body(replace(c,chat=replace(c.chat,supports_temperature=False)),CONVERSATION,OPTIONS)
    assert "temperature" not in disabled and "seed" not in disabled


@pytest.mark.parametrize("format,want", [
    ("openai",{"reasoning_effort":"high"}), ("thinking-type",{"thinking":{"type":"enabled"},"reasoning_effort":"high"}),
    ("enable-thinking",{"enable_thinking":True,"reasoning_effort":"high"}),
    ("chat-template-kwargs",{"chat_template_kwargs":{"enable_thinking":True}}),
    ("openrouter",{"reasoning":{"effort":"high"}})])
def test_thinking_dialects_share_renderer(format,want):
    c = config(chat=replace(ChatSettings(), thinking_format=format,supports_reasoning_effort=True))
    body = build_chat_body(c,(Message("user","q"),),ChatOptions(reasoning_effort="high"))
    for key,value in want.items():
        assert body[key] == value


def test_always_on_and_nonstream_disable_budget_exclusion_and_invalid_reasoning():
    c = config(chat=replace(ChatSettings(), thinking_format="enable-thinking",thinking_always_send=True,
        thinking_budget_field="thinking_budget",thinking_disable_on_non_stream=True,supports_reasoning_effort=True,
        thinking_budget_excludes_effort=True))
    assert build_chat_body(c,CONVERSATION)["enable_thinking"] is False
    opts = ChatOptions(thinking=True,thinking_budget_tokens=4096)
    assert "thinking_budget" not in build_chat_body(c,CONVERSATION,opts)
    assert build_chat_body(c,CONVERSATION,opts,stream=True)["thinking_budget"] == 4096
    graded = build_chat_body(c,CONVERSATION,replace(opts,reasoning_effort="high"),stream=True)
    assert "thinking_budget" not in graded and graded["reasoning_effort"] == "high"
    always_on = replace(c,thinking_levels={"off":None})
    assert "enable_thinking" not in build_chat_body(always_on,CONVERSATION,ChatOptions(thinking=False))
    assert build_chat_body(c,CONVERSATION,ChatOptions(reasoning_effort="hgih",thinking=False))["enable_thinking"] is False


def test_multicontent_flattening_special_tokens_and_opaque_metadata_copy():
    meta = {"extra_content":{"signature":"opaque"}}
    messages = (Message("user","[INST]",images=("data:image/png;base64,AA==",)),
        Message("assistant",reasoning_content="<|reason",reasoning_metadata={"reasoning_details":[{"x":1}]},
            tool_calls=(ToolCall("a",function=FunctionCall("fn",'{"x":"[INST]"}'),provider_metadata=meta),)))
    c = config(chat=replace(ChatSettings(),thinking_format="openrouter",tool_call_extra_fields=("extra_content",)))
    body = build_chat_body(c,messages)
    assert body["messages"][0]["content"][0]["image_url"]["detail"] == "auto"
    assert body["messages"][0]["content"][1]["text"] == "[\u200bINST]"
    assistant = body["messages"][1]
    assert assistant["reasoning_content"] == "<\u200b|reason"
    assert assistant["reasoning_details"] == [{"x":1}]
    assert assistant["tool_calls"][0]["extra_content"] == meta["extra_content"]
    assistant["tool_calls"][0]["extra_content"]["signature"] = "changed"
    assert meta["extra_content"]["signature"] == "opaque"
    flat = build_chat_body(replace(c,chat=replace(c.chat,supports_multi_content=False)),messages)
    assert flat["messages"][0]["content"] == "[\u200bINST]"
    assert messages[0].content == "[INST]"


def test_response_format_cache_key_retention_and_anthropic_breakpoints():
    c = config(chat=replace(ChatSettings(),prompt_cache_key=True,cache_control_format="anthropic"))
    opts = replace(OPTIONS,format='{"type":"object"}',prompt_cache_key="x"*80,cache_retention="long")
    body = build_chat_body(c,CONVERSATION,opts)
    assert body["prompt_cache_key"] == "x"*64 and body["prompt_cache_retention"] == "24h"
    assert body["response_format"] == {"type":"json_object"}
    assert body["messages"][-1]["content"][0]["text"].endswith('Use this JSON schema: {"type":"object"}')
    assert body["messages"][0]["content"][0]["cache_control"] == {"type":"ephemeral","ttl":"1h"}
    assert body["tools"][-1]["cache_control"]["ttl"] == "1h"
    no = build_chat_body(c,CONVERSATION,replace(opts,cache_retention="none"))
    assert "prompt_cache_key" not in no and "cache_control" not in no["tools"][-1]


def test_embedding_and_rerank_golden_optional_fields():
    bge = provider_config("siliconflow","BAAI/bge-m3",dimensions=1024)
    assert build_embedding_body(bge,["a","b"],"document") == {
        "model":"BAAI/bge-m3","input":["a","b"],"encoding_format":"float"}
    c = config(embedding=EmbeddingSettings(dimensions_field="dimensions",input_type_field="task",
        input_type_values={"query":"retrieval.query"},truncate_field="truncate",truncate_value="false"),dimensions=32)
    assert build_embedding_body(c,["q"],"query") == {
        "model":"m","input":["q"],"dimensions":32,"task":"retrieval.query","truncate":False}
    assert build_rerank_body(config(),"q",["d"]) == {"model":"m","query":"q","documents":["d"]}
    c = config(reranking=RerankSettings(send_top_n=True,send_return_documents=True,
        accepts_truncate_prompt_tokens=True,truncate_prompt_tokens=20))
    assert build_rerank_body(c,"q",["d"])["top_n"] == 1
    assert build_rerank_body(c,"q",["d"])["truncate_prompt_tokens"] == 20


def test_batch_limits_reject_oversize_instead_of_truncating():
    assert split_batches(["ab","cd","ef"],max_items=2) == [(0,["ab","cd"]),(2,["ef"])]
    assert split_batches(["ab","cd","ef"],max_total_chars=5,fixed_chars=1) == [(0,["ab","cd"]),(2,["ef"])]
    with pytest.raises(ProviderError,match="MODEL_INPUT_LIMIT_EXCEEDED"):
        split_batches(["long"],max_item_chars=3)


def test_model_capacity_validation_has_no_output_probe_dependency():
    c = config(max_output_tokens=8192)
    model = ProviderModel(c,None)
    assert model.build_request_body(CONVERSATION,ChatOptions(max_tokens=4096))["max_completion_tokens"] == 4096
    with pytest.raises(ProviderError,match="MODEL_OUTPUT_LIMIT_EXCEEDED"):
        model.build_request_body(CONVERSATION,ChatOptions(max_tokens=8193))
