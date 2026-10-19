"""Semantic request renderer: pinned openaicompletions/request.go.

Vendor-neutral; compatibility settings alone select every optional wire field.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Callable, Sequence
from .types import ChatOptions, Message, ModelConfig, ProviderError

LADDER = ("minimal", "low", "medium", "high", "xhigh", "max")


def neutralize(text: str) -> str:
    for literal, replacement in (("<|", "<\u200b|"), ("<｜", "<\u200b｜"),
        ("<start_of_turn>", "<\u200bstart_of_turn>"), ("<end_of_turn>", "<\u200bend_of_turn>"),
        ("[INST]", "[\u200bINST]"), ("[/INST]", "[\u200b/INST]"),
        ("<<SYS>>", "<\u200b<SYS>>"), ("<</SYS>>", "<\u200b</SYS>>")):
        text = text.replace(literal, replacement)
    return text


def strip_images(messages: Sequence[Message]) -> tuple[Message, ...]:
    return tuple(replace(m, images=(), multi_content=tuple(
        p for p in m.multi_content if p.get("type") != "image_url" and not p.get("image_url"))) for m in messages)


def convert_messages(config: ModelConfig, messages: Sequence[Message],
                     image_resolver: Callable[[str], str] | None = None) -> list[dict]:
    s = config.chat
    resolve = image_resolver or (lambda url: url)
    out = []
    for msg in messages:
        role = "developer" if msg.role == "system" and config.reasoning and s.supports_developer_role else msg.role
        wm = {"role": role}
        parts = []
        if msg.multi_content:
            for part in msg.multi_content:
                if part.get("type") == "text":
                    wire = {"type": "text"}
                    if part.get("text"):
                        wire["text"] = neutralize(part["text"])
                    parts.append(wire)
                elif part.get("type") == "image_url" and part.get("image_url"):
                    image = part["image_url"]
                    wire = {"url": resolve(image["url"])}
                    if image.get("detail"):
                        wire["detail"] = image["detail"]
                    parts.append({"type": "image_url", "image_url": wire})
        elif msg.images and msg.role == "user":
            parts = [{"type": "image_url", "image_url": {"url": resolve(image), "detail": "auto"}} for image in msg.images]
            part = {"type": "text"}
            if msg.content:
                part["text"] = neutralize(msg.content)
            parts.append(part)
        if msg.multi_content or (msg.images and msg.role == "user"):
            wm["content"] = parts if s.supports_multi_content else "\n".join(p["text"] for p in parts if p.get("text"))
        elif msg.content or not msg.tool_calls:
            wm["content"] = neutralize(msg.content)
        if msg.tool_calls:
            calls = []
            for tc in msg.tool_calls:
                wire = {"type": tc.type or "function", "function": {
                    "name": tc.function.name, "arguments": neutralize(tc.function.arguments)}}
                if tc.id:
                    wire["id"] = tc.id
                for key in s.tool_call_extra_fields:
                    if key in tc.provider_metadata:
                        wire[key] = deepcopy(tc.provider_metadata[key])
                calls.append(wire)
            wm["tool_calls"] = calls
        if msg.role == "tool":
            if msg.tool_call_id:
                wm["tool_call_id"] = msg.tool_call_id
            if msg.name:
                wm["name"] = msg.name
        if msg.role == "assistant":
            if s.replay_reasoning_content and msg.reasoning_content:
                wm["reasoning_content"] = neutralize(msg.reasoning_content)
            if "reasoning_details" in msg.reasoning_metadata and s.thinking_format == "openrouter":
                wm["reasoning_details"] = deepcopy(msg.reasoning_metadata["reasoning_details"])
        out.append(wm)
    return out


def supports_level(levels, level):
    return levels[level] is not None if level in levels else level not in ("xhigh", "max")


def clamp_level(levels, level):
    if level not in LADDER or supports_level(levels, level):
        return level
    index = LADDER.index(level)
    for candidate in (*LADDER[index+1:], *reversed(LADDER[:index])):
        if supports_level(levels, candidate):
            return candidate
    return "auto"


def apply_thinking(body, config, opts, stream):
    s, levels = config.chat, config.thinking_levels
    if s.thinking_format in ("", "none"):
        return
    level, requested = opts.reasoning() if opts is not None else ("", False)
    if not requested:
        if not s.thinking_always_send:
            return
        level = "off"
    if level in LADDER:
        level = clamp_level(levels, level)
    enabled = level not in ("", "off")
    if enabled and s.thinking_disable_on_non_stream and not stream:
        enabled = False
    if not enabled and not supports_level(levels, "off"):
        return
    effort = (levels.get(level, level) or "") if enabled and level in LADDER and s.supports_reasoning_effort else ""
    budget = opts.thinking_budget_tokens if enabled and opts is not None and s.thinking_budget_field else 0
    if (enabled and config.provider == "siliconflow" and s.thinking_budget_field == "thinking_budget"
            and (type(budget) is not int or (budget != 0 and not 128 <= budget <= 32768))):
        raise ProviderError("MODEL_THINKING_BUDGET_INVALID")
    if effort and s.thinking_budget_excludes_effort:
        budget = 0
    if budget > 0:
        body[s.thinking_budget_field] = budget
    effort_field = s.reasoning_effort_field or "reasoning_effort"
    if s.thinking_format == "openai":
        if enabled and effort:
            body[effort_field] = effort
        elif not enabled and levels.get("off"):
            body[effort_field] = levels["off"]
    elif s.thinking_format == "thinking-type":
        body["thinking"] = {"type": (s.thinking_enabled_value or "enabled") if enabled else "disabled"}
        if enabled and effort:
            body[effort_field] = effort
    elif s.thinking_format == "enable-thinking":
        body["enable_thinking"] = enabled
        if effort:
            body[effort_field] = effort
    elif s.thinking_format == "chat-template-kwargs":
        body["chat_template_kwargs"] = {"enable_thinking": enabled}
    elif s.thinking_format == "openrouter":
        body["reasoning"] = {"effort": effort} if enabled and effort else {"enabled": enabled}
    else:
        raise ProviderError("MODEL_THINKING_CONFIGURATION_INVALID")


def _cache_message(message, marker):
    content = message.get("content")
    if isinstance(content, str) and content:
        message["content"] = [{"type": "text", "text": content, "cache_control": dict(marker)}]
        return True
    if isinstance(content, list):
        for part in reversed(content):
            if part.get("type") in ("text", "tool_result"):
                part["cache_control"] = dict(marker)
                return True
    return False


def build_chat_body(config: ModelConfig, messages: Sequence[Message], options: ChatOptions | None = None,
                    *, stream: bool = False, image_resolver=None) -> dict:
    s, opts = config.chat, options
    body = {"model": config.model, "messages": convert_messages(config, messages, image_resolver)}
    if stream:
        body["stream"] = True
        if s.supports_usage_in_streaming:
            body["stream_options"] = {"include_usage": True}
    if s.supports_store:
        body["store"] = False
    if opts is not None:
        if s.supports_temperature:
            if s.fixed_temperature is not None:
                body["temperature"] = s.fixed_temperature
            else:
                for name in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
                    value = getattr(opts, name)
                    if value > 0:
                        body[name] = value
                if opts.seed > 0 and s.supports_seed:
                    body["seed"] = opts.seed
        budget = opts.completion_budget()
        if budget > 0:
            body[s.max_tokens_field or "max_completion_tokens"] = budget
        if opts.tools:
            body["tools"] = []
            for tool in opts.tools:
                function = {"name": tool.name, "description": tool.description}
                if tool.parameters:
                    function["parameters"] = deepcopy(tool.parameters)
                body["tools"].append({"type": tool.type or "function", "function": function})
            if opts.parallel_tool_calls is not None and s.supports_parallel_tool_calls:
                body["parallel_tool_calls"] = opts.parallel_tool_calls
            if opts.tool_choice in ("none", "required", "auto"):
                if opts.tool_choice in s.tool_choice_modes:
                    body["tool_choice"] = opts.tool_choice
            elif opts.tool_choice and "function" in s.tool_choice_modes:
                body["tool_choice"] = {"type": "function", "function": {"name": opts.tool_choice}}
        if opts.format and s.supports_response_format:
            body["response_format"] = {"type": "json_object"}
            if body["messages"] and isinstance(body["messages"][-1].get("content"), str):
                body["messages"][-1]["content"] += "\nUse this JSON schema: " + opts.format
        if s.prompt_cache_key and opts.cache_retention != "none":
            key = opts.prompt_cache_key[:64]
            if key:
                body["prompt_cache_key"] = key
                if opts.cache_retention == "long":
                    body["prompt_cache_retention"] = "24h"
    apply_thinking(body, config, opts, stream)
    for key, value in s.extra_body.items():
        body.setdefault(key, deepcopy(value))
    if "max_tokens" in body and "max_completion_tokens" in body:
        del body["max_completion_tokens" if s.max_tokens_field == "max_tokens" else "max_tokens"]
    if s.cache_control_format == "anthropic" and (opts is None or opts.cache_retention != "none"):
        marker = {"type": "ephemeral"}
        if opts is not None and opts.cache_retention == "long":
            marker["ttl"] = "1h"
        for message in body["messages"]:
            if message["role"] in ("system", "developer"):
                _cache_message(message, marker)
                break
        if body.get("tools"):
            body["tools"][-1]["cache_control"] = dict(marker)
        for message in reversed(body["messages"]):
            if message["role"] in ("user", "assistant", "tool") and _cache_message(message, marker):
                break
    return body


def build_embedding_body(config, texts, kind):
    s = config.embedding
    dimensions=config.dimensions if config.supports_dimension_override else 0
    if config.embedding_api == "dashscope-embeddings":
        body={"model":config.model,"input":{"contents":[{"text":text} for text in texts]}}
        if s.dimensions_field and dimensions>0: body["parameters"]={s.dimensions_field:dimensions}
        for key,value in s.extra_body.items(): body.setdefault(key,deepcopy(value))
        return body
    if config.embedding_api == "ark-embeddings":
        body={"model":config.model,"input":[{"type":"text","text":text} for text in texts]}
        if s.send_encoding_format: body["encoding_format"]="float"
        if s.dimensions_field and dimensions>0: body[s.dimensions_field]=dimensions
        for key,value in s.extra_body.items(): body.setdefault(key,deepcopy(value))
        return body
    if config.embedding_api == "google-embeddings":
        requests=[]
        for text in texts:
            request={"model":"models/"+config.model.removeprefix("models/"),"content":{"parts":[{"text":text}]}}
            options={}
            if s.input_type_field: options[s.input_type_field]=s.input_type_values.get(kind,kind)
            if s.dimensions_field and dimensions>0: options[s.dimensions_field]=dimensions
            if options: request["embedContentConfig"]=options
            requests.append(request)
        return {**deepcopy(s.extra_body),"requests":requests}
    if config.embedding_api != "openai-embeddings": raise ProviderError("MODEL_PROTOCOL_UNSUPPORTED")
    body = {"model": config.model, "input": list(texts)}
    if s.send_encoding_format:
        body["encoding_format"] = "float"
    if s.dimensions_field and dimensions > 0:
        body[s.dimensions_field] = dimensions
    if s.input_type_field:
        body[s.input_type_field] = s.input_type_values.get(kind, kind)
    if s.truncate_field:
        body[s.truncate_field] = (s.truncate_value == "true") if s.truncate_value in ("true", "false") else s.truncate_value
    if s.accepts_truncate_prompt_tokens and s.truncate_prompt_tokens > 0:
        body["truncate_prompt_tokens"] = s.truncate_prompt_tokens
    for key, value in s.extra_body.items():
        body.setdefault(key, deepcopy(value))
    return body


def build_rerank_body(config, query, documents):
    s = config.reranking
    if config.rerank_api=="nim-rerank":
        body={"model":config.model,"query":{"text":query},"passages":[{"text":text} for text in documents]}
        if s.truncate: body["truncate"]=s.truncate
        return {**deepcopy(s.extra_body),**body}
    if config.rerank_api=="dashscope-rerank":
        return {**deepcopy(s.extra_body),"model":config.model,"input":{"query":query,"documents":list(documents)},
                "parameters":{"return_documents":s.send_return_documents,"top_n":len(documents)}}
    if config.rerank_api=="tencent-lkeap":
        return {"Model":config.model.strip() or "lke-reranker-base","Query":query,"Docs":list(documents)}
    if config.rerank_api=="volcengine-knowledge":
        instruction=config.extra_config.get("instruction","").strip() or "Whether the document answers the query or matches the content retrieval intent"
        return {"datas":[{"query":query,"content":text} for text in documents],
                "rerank_model":config.model.strip() or "doubao-seed-rerank","rerank_instruction":instruction}
    if config.rerank_api != "cohere-rerank": raise ProviderError("MODEL_PROTOCOL_UNSUPPORTED")
    body = {"model": config.model, "query": query, "documents": list(documents)}
    if s.send_top_n and documents:
        body["top_n"] = len(documents)
    if s.send_return_documents:
        body["return_documents"] = True
    if s.accepts_truncate_prompt_tokens and s.truncate_prompt_tokens > 0:
        body["truncate_prompt_tokens"] = s.truncate_prompt_tokens
    for key, value in s.extra_body.items():
        body.setdefault(key, deepcopy(value))
    return body


def split_batches(items, *, max_items=0, max_item_chars=0, max_total_chars=0, fixed_chars=0):
    """api/batch.go: reject indivisible oversize input; never silently truncate."""
    if max_total_chars > 0 and fixed_chars >= max_total_chars:
        raise ProviderError("MODEL_QUERY_LIMIT_EXCEEDED")
    for item in items:
        if not isinstance(item, str):
            raise ProviderError("MODEL_TEXT_INPUT_INVALID")
        if max_item_chars > 0 and len(item) > max_item_chars:
            raise ProviderError("MODEL_INPUT_LIMIT_EXCEEDED")
        if max_total_chars > 0 and fixed_chars + len(item) > max_total_chars:
            raise ProviderError("MODEL_INPUT_LIMIT_EXCEEDED")
    out, start, total = [], 0, fixed_chars
    for i, item in enumerate(items):
        if i > start and ((max_items > 0 and i-start == max_items) or
                          (max_total_chars > 0 and total+len(item) > max_total_chars)):
            out.append((start, items[start:i]))
            start, total = i, fixed_chars
        total += len(item)
    if items:
        out.append((start, items[start:]))
    return out
