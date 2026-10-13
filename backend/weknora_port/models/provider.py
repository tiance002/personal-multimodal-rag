"""Unified ProviderModel: protocol configuration varies, transport does not."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace
from collections.abc import Mapping, Sequence
from .types import (Message, ChatOptions, ChatResponse, TokenUsage, ProviderError,
                    EmbeddingResponse, RerankResponse, RerankResult, CallCancelled)
from .request import build_chat_body, build_embedding_body, build_rerank_body, split_batches, strip_images
from .transport import call_lifetime, DEFAULT_CHAT_TIMEOUT, DEFAULT_STREAM_TIMEOUT
from .usage import normalize_usage, accumulate_usage
from .stream import StreamAssembler, sse_events, decode_tool_calls


def decode_json(raw):
    try:
        env = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(env, dict):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True) from None
    if isinstance(env.get("error"), dict) and env["error"].get("message"):
        raise ProviderError("PROVIDER_API_ERROR", sent=True)
    return env


def assert_response_identity(env, config):
    # Go's rawEnvelope does not decode model and ignores mismatches. The
    # approved identity safety wrapper adds this single objective comparison.
    if "model" in env and env["model"] is not None and env["model"] != config.model:
        raise ProviderError("PROVIDER_MODEL_IDENTITY_MISMATCH", sent=True)


def _string(obj, field):
    value = obj.get(field, "")
    if value is None:
        return ""  # Go json.Unmarshal null into string retains zero value
    if not isinstance(value, str):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    return value


def _reasoning(message, settings):
    for field in settings.reasoning_fields:
        if field in ("reasoning_content", "reasoning", "reasoning_text"):
            text = _string(message, field)
            if text:
                return text
    return ""


def split_think_tags(content):
    trimmed = content.strip()
    if not trimmed.startswith("<think>"):
        return content, ""
    index = trimmed.rfind("</think>")
    if index < 0:
        return "", trimmed[len("<think>"):].strip()
    return trimmed[index+len("</think>"):].strip(), trimmed[len("<think>"):index].strip()


def parse_chat_response(env, config):
    assert_response_identity(env, config)
    choices = env.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError("PROVIDER_NO_RESPONSE", sent=True)
    choice = choices[0]
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    content, inline = split_think_tags(_string(message, "content"))
    calls = decode_tool_calls(message["tool_calls"], config.chat)[0] if message.get("tool_calls") else ()
    metadata = {"reasoning_details": message["reasoning_details"]} if "reasoning_details" in message else {}
    return ChatResponse(content, _reasoning(message, config.chat) or inline, _string(choice, "finish_reason"),
        calls, normalize_usage(env.get("usage"), config.chat.prompt_cache_accounting), metadata, env.get("model") or "")


class ProviderModel:
    def __init__(self, config, transport, *, image_resolver=None):
        self.config, self.transport, self.image_resolver = config, transport, image_resolver

    def _options(self, options, context):
        if context.session_id and (options is None or not options.prompt_cache_key):
            options = replace(options or ChatOptions(), prompt_cache_key=context.session_id)
        return options

    def build_request_body(self, messages, options=None, *, stream=False):
        body = build_chat_body(self.config, messages, options, stream=stream, image_resolver=self.image_resolver)
        budget = body.get("max_tokens", body.get("max_completion_tokens", 0))
        if type(budget) is not int or budget < 0:
            raise ProviderError("MODEL_OUTPUT_BUDGET_INVALID")
        if self.config.max_output_tokens > 0 and budget > self.config.max_output_tokens:
            raise ProviderError("MODEL_OUTPUT_LIMIT_EXCEEDED")
        return body

    def _affinity(self, options, context):
        return ((options.prompt_cache_key if options and options.prompt_cache_key else context.session_id)[:64]
                if self.config.chat.prompt_cache_key else "")

    async def chat(self, messages: Sequence[Message], options: ChatOptions | None, context, *, _capability="chat"):
        options = self._options(options, context)
        async with call_lifetime(context, DEFAULT_CHAT_TIMEOUT) as lifetime:
            for retry in (False, True):
                body = self.build_request_body(strip_images(messages) if retry else messages, options)
                call_context = context.child("image-fallback") if retry else context
                try:
                    async with self.transport.open(self.config, "/chat/completions", body, call_context,
                            capability=_capability, affinity=self._affinity(options, context), lifetime=lifetime) as handle:
                        env = decode_json(await handle.response.aread())
                        usage = normalize_usage(env.get("usage"), self.config.chat.prompt_cache_accounting)
                        handle.observed(usage)
                        return parse_chat_response(env, self.config)
                except ProviderError as error:
                    if not retry and getattr(error, "multimodal_unsupported", False):
                        continue
                    raise

    async def vision(self, messages, options, context):
        if not self.config.supports_vision:
            raise ProviderError("MODEL_VISION_UNSUPPORTED")
        return await self.chat(messages, options, context, _capability="vision")

    async def chat_stream(self, messages, options, context):
        options = self._options(options, context)
        assembler = StreamAssembler()
        try:
            async with call_lifetime(context, DEFAULT_STREAM_TIMEOUT) as lifetime:
                for retry in (False, True):
                    body = self.build_request_body(strip_images(messages) if retry else messages, options, stream=True)
                    call_context = context.child("image-fallback") if retry else context
                    try:
                        async with self.transport.open(self.config, "/chat/completions", body, call_context,
                                capability="chat", stream=True, affinity=self._affinity(options, context), lifetime=lifetime) as handle:
                            async for done, raw in sse_events(handle.response):
                                if done:
                                    break
                                if not raw:
                                    continue
                                env = decode_json(raw)
                                # Usage can arrive even on a mismatched/error answer.
                                # Retain counters without treating the answer as valid.
                                if env.get("usage") is not None:
                                    assembler.usage = normalize_usage(env["usage"], self.config.chat.prompt_cache_accounting)
                                    handle.usage = assembler.usage
                                assert_response_identity(env, self.config)
                                choices = env.get("choices", [])
                                if not isinstance(choices, list):
                                    raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
                                if not choices:
                                    continue
                                choice = choices[0]
                                if not isinstance(choice, dict) or not isinstance(choice.get("delta", {}), dict):
                                    raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
                                delta = choice.get("delta", {})
                                details = delta.get("reasoning_details")
                                if isinstance(details, list):
                                    assembler.reasoning_details.extend(details)
                                tools = decode_tool_calls(delta["tool_calls"], self.config.chat)[1] if delta.get("tool_calls") else ()
                                for event in assembler.process(reasoning=_reasoning(delta, self.config.chat),
                                        content=_string(delta, "content"), tools=tools, finish_reason=_string(choice, "finish_reason")):
                                    yield event
                            handle.observed(assembler.usage or normalize_usage(None, self.config.chat.prompt_cache_accounting))
                        # Emit final only after durable gate finish has succeeded.
                        for event in assembler.end():
                            yield event
                        return
                    except ProviderError as error:
                        if not retry and getattr(error, "multimodal_unsupported", False):
                            continue
                        raise
        except CallCancelled:
            # Cancellation is not a provider failure or a signal to upgrade.
            raise
        except ProviderError as error:
            for event in assembler.fail(error):
                yield event

    async def embed(self, texts, context, *, kind="document"):
        s = self.config.embedding
        batches = split_batches(tuple(texts), max_items=s.max_batch_size, max_item_chars=s.max_input_chars)
        vectors, usages = [], []
        for ordinal, (_, batch) in enumerate(batches):
            ctx = context.child(f"embedding-{ordinal}") if len(batches) > 1 else context
            async with call_lifetime(ctx, s.request_timeout) as lifetime:
                body = build_embedding_body(self.config, batch, kind)
                async with self.transport.open(self.config, s.path or "/embeddings", body, ctx, capability="embedding", lifetime=lifetime) as handle:
                    env = decode_json(await handle.response.aread())
                    usage = normalize_usage(env.get("usage"))
                    handle.observed(usage)
                    assert_response_identity(env, self.config)
                    rows = env.get("data")
                    if not isinstance(rows, list):
                        raise ProviderError("PROVIDER_EMBEDDING_INVALID", sent=True)
                    out, seen = [None]*len(batch), set()
                    for i, row in enumerate(rows):
                        if not isinstance(row, dict):
                            raise ProviderError("PROVIDER_EMBEDDING_INVALID", sent=True)
                        index = row.get("index", i)
                        if index is None:
                            index = i
                        vector = row.get("embedding")
                        if (type(index) is not int or not 0 <= index < len(batch) or index in seen or
                                not isinstance(vector, list) or not vector or
                                any(type(n) not in (int, float) or not math.isfinite(n) for n in vector)):
                            raise ProviderError("PROVIDER_EMBEDDING_INVALID", sent=True)
                        out[index], seen = tuple(float(n) for n in vector), seen | {index}
                    if any(vector is None for vector in out):
                        raise ProviderError("PROVIDER_EMBEDDING_MISSING", sent=True)
                    vectors.extend(out)
                    usages.append(usage)
        return EmbeddingResponse(tuple(vectors), accumulate_usage(usages))

    async def rerank(self, query, documents, context):
        s = self.config.reranking
        if s.max_query_chars > 0 and len(query) > s.max_query_chars:
            raise ProviderError("MODEL_QUERY_LIMIT_EXCEEDED")
        batches = split_batches(tuple(documents), max_items=s.max_documents, max_item_chars=s.max_document_chars,
                                max_total_chars=s.max_request_chars, fixed_chars=len(query))
        results, usages = [], []
        for ordinal, (start, batch) in enumerate(batches):
            ctx = context.child(f"rerank-{ordinal}") if len(batches) > 1 else context
            async with call_lifetime(ctx, s.request_timeout) as lifetime:
                body = build_rerank_body(self.config, query, batch)
                async with self.transport.open(self.config, s.path or "/rerank", body, ctx, capability="rerank", lifetime=lifetime) as handle:
                    env = decode_json(await handle.response.aread())
                    usage = normalize_usage(env.get("usage"))
                    handle.observed(usage)
                    assert_response_identity(env, self.config)
                    rows = env.get("results", [])
                    if not isinstance(rows, list):
                        raise ProviderError("PROVIDER_RERANK_INVALID", sent=True)
                    for row in rows:
                        if not isinstance(row, dict):
                            raise ProviderError("PROVIDER_RERANK_INVALID", sent=True)
                        index = row.get("index", 0)
                        score = row.get("relevance_score")
                        if score is None:
                            score = row.get("score", 0) or 0
                        text = row.get("document") or ""
                        if isinstance(text, dict):
                            text = text.get("text", "")
                        if (type(index) is not int or not 0 <= index < len(batch) or
                                type(score) not in (int, float) or not math.isfinite(score) or not isinstance(text, str)):
                            raise ProviderError("PROVIDER_RERANK_INVALID", sent=True)
                        results.append(RerankResult(start+index, float(score), text))
                    usages.append(usage)
        return RerankResponse(tuple(results), accumulate_usage(usages), s.score_scale)
