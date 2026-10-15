"""Unified ProviderModel: protocol configuration varies, transport does not."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import replace
from collections.abc import Mapping, Sequence
from .types import (Message, ChatOptions, ChatResponse, TokenUsage, ProviderError,
                    EmbeddingResponse, RerankResponse, RerankResult, CallCancelled, RunDeadlineExceeded)
from .request import build_chat_body, build_embedding_body, build_rerank_body, split_batches, strip_images
from .transport import call_lifetime, DEFAULT_CHAT_TIMEOUT, DEFAULT_STREAM_TIMEOUT, env_duration
from .usage import normalize_usage, accumulate_usage
from .stream import StreamAssembler, sse_events, decode_tool_calls
from .native_request import build_native_body
from .native_response import parse_native_response, NativeStreamState, native_usage
from .local_ollama import (build_ollama_body, parse_ollama_response, OllamaStreamState,
                           local_error_event, ndjson_events, local_connection_config)


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


def normalize_rerank_response(response,documents):
    """Source rerank/protocol.go:Rerank, normalizeScore service adapter.

    Protocol clients return raw scores. This one boundary fills omitted text,
    rejects invalid indices and stably sorts once after logit conversion.
    """
    results=[]
    for item in response.results:
        if type(item.index) is not int or not 0<=item.index<len(documents):
            raise ProviderError("PROVIDER_RERANK_INVALID",sent=True)
        score=item.score
        if response.score_scale=="logit":
            if score>=0: score=1/(1+math.exp(-score))
            else:
                exp=math.exp(score);score=exp/(1+exp)
        results.append(replace(item,score=score,text=item.text or documents[item.index]))
    results.sort(key=lambda item:item.score,reverse=True)
    return RerankResponse(tuple(results),response.usage,"probability")


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
    def __init__(self, config, transport, *, local_service=None, image_resolver=None):
        self.config, self.transport, self.image_resolver = config, transport, image_resolver
        # source=='local' routes to an injected Ollama connection instead of the
        # row's inline BaseURL/APIKey; a missing service fails loudly at call
        # time and is never silently rewritten into a remote send.
        self.local_service = local_service

    def _options(self, options, context):
        if context.session_id and (options is None or not options.prompt_cache_key):
            options = replace(options or ChatOptions(), prompt_cache_key=context.session_id)
        return options

    def build_request_body(self, messages, options=None, *, stream=False):
        if self.config.source=='local':
            return build_ollama_body(self.config, messages, options, stream=stream,
                                     image_resolver=self.image_resolver)
        if self.config.provider=="weknoracloud" and self.config.model_type=="VLLM":
            # Specialized source VLM wire; ordinary cloud chat flattens images.
            from .request import convert_messages
            config=replace(self.config,chat=replace(self.config.chat,supports_multi_content=True))
            converted=convert_messages(config,messages,self.image_resolver)
            for message in converted:
                if isinstance(message.get("content"),list):
                    for part in message["content"]:
                        if part.get("image_url"): part["image_url"].pop("detail",None)
            body={"model":self.config.model,"messages":converted,"stream":False,
                  "max_tokens":options.completion_budget() if options and options.completion_budget()>0 else 5000,
                  "temperature":options.temperature if options and options.temperature>0 else float(.1)}
        else:
            builder=build_chat_body if self.config.api == "openai-completions" else build_native_body
            body = builder(self.config, messages, options, stream=stream, image_resolver=self.image_resolver)
        budget = body.get("max_tokens", body.get("max_completion_tokens", body.get("max_output_tokens", (body.get("generationConfig") or {}).get("maxOutputTokens",0))))
        if type(budget) is not int or budget < 0:
            raise ProviderError("MODEL_OUTPUT_BUDGET_INVALID")
        if self.config.max_output_tokens > 0 and budget > self.config.max_output_tokens:
            raise ProviderError("MODEL_OUTPUT_LIMIT_EXCEEDED")
        return body

    def _affinity(self, options, context):
        return ((options.prompt_cache_key if options and options.prompt_cache_key else context.session_id)[:64]
                if (self.config.chat.prompt_cache_key if self.config.api=="openai-completions" else
                    self.config.protocol_settings.get(self.config.api,{}).get("prompt_cache_key")) else "")

    async def chat(self, messages: Sequence[Message], options: ChatOptions | None, context, *, _capability="chat"):
        if self.config.source=='local':
            return await self._local_chat(messages, options, context, _capability=_capability)
        options = self._options(options, context)
        async with call_lifetime(context, DEFAULT_CHAT_TIMEOUT) as lifetime:
            for retry in ((False,) if _capability == "vision" else (False, True)):
                body = self.build_request_body(strip_images(messages) if retry else messages, options)
                call_context = context.child("image-fallback") if retry else context
                try:
                    async with self.transport.open(self.config, "/chat/completions", body, call_context,
                            capability=_capability, affinity=self._affinity(options, context), lifetime=lifetime) as handle:
                        if self.config.api=="anthropic-messages" and "text/event-stream" in handle.response.headers.get("content-type","").lower():
                            assembler=StreamAssembler();state=NativeStreamState(self.config,assembler)
                            content,reasoning=[],[]
                            async for done,raw in sse_events(handle.response):
                                if done: break
                                if raw:
                                    for event in state.process(decode_json(raw)):
                                        if event.response_type=="answer": content.append(event.content)
                                        elif event.response_type=="thinking": reasoning.append(event.content)
                                    if state.complete: break
                            state.finish()
                            response=ChatResponse("".join(content),"".join(reasoning),assembler.last_finish_reason,
                                assembler.ordered_calls(),assembler.usage or normalize_usage(None,True),state.metadata,
                                reasoning_signature=state.metadata.get("reasoning_signature",""))
                        else:
                            env = decode_json(await handle.response.aread())
                            # Retain real counters even when response identity fails.
                            handle.usage=(normalize_usage(env.get("usage"),self.config.chat.prompt_cache_accounting)
                                if self.config.api=="openai-completions" else native_usage(self.config,
                                    env.get("usageMetadata") if self.config.api=="google-generative-ai" else env.get("usage")))
                            assert_response_identity(env,self.config)
                            response=parse_chat_response(env, self.config) if self.config.api=="openai-completions" else parse_native_response(env,self.config)
                        handle.observed(response.usage)
                        return response
                except ProviderError as error:
                    if _capability != "vision" and not retry and getattr(error, "multimodal_unsupported", False):
                        continue
                    raise

    async def vision(self, messages, options, context):
        if not self.config.supports_vision:
            raise ProviderError("MODEL_VISION_UNSUPPORTED")
        deadline=asyncio.get_running_loop().time()+env_duration("VLM_HTTP_TIMEOUT_SECONDS",180)
        if context.deadline is None or context.deadline>deadline:
            context=replace(context,deadline=deadline,deadline_kind="model")
        if options is None:
            import struct
            temp=float(struct.unpack("f",struct.pack("f",.1))[0])
            try: temp=float(self.config.extra_config.get("temperature",temp))
            except ValueError: pass
            options=ChatOptions(max_tokens=5000,temperature=temp)
        return await self.chat(messages, options, context, _capability="vision")

    async def chat_stream(self, messages, options, context):
        if self.config.source=='local':
            async for event in self._local_stream(messages, options, context):
                yield event
            return
        if self.config.api != "openai-completions":
            async for event in self._native_stream(messages,options,context):
                yield event
            return
        options = self._options(options, context)
        assembler = StreamAssembler()
        model_reported = None
        def identity_event(event):
            return replace(event, data={**(event.data or {}), "model_requested": self.config.model, "model_reported": model_reported})
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
                                if isinstance(env.get("model"), str) and env["model"]:
                                    model_reported = env["model"]
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
                            yield identity_event(event)
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
                yield identity_event(event)

    async def _local_ensure(self, service, effective, context, lifetime):
        try:
            if context.cancelled is not None and context.cancelled.is_set():raise CallCancelled()
            if context.deadline is not None and context.deadline<=asyncio.get_running_loop().time():
                from .types import RunDeadlineExceeded
                if context.deadline_kind=="run":raise RunDeadlineExceeded()
                raise ProviderError("MODEL_ADMISSION_TIMEOUT")
            await service.ensure_model_available(self.config.model,transport=self.transport,
                config=effective,context=context,lifetime=lifetime)
        except CallCancelled:
            raise
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("MODEL_LOCAL_SERVICE_UNAVAILABLE") from None

    async def _local_chat(self, messages, options, context, *, _capability="chat"):
        service = self.local_service
        if service is None:
            raise ProviderError("MODEL_LOCAL_SERVICE_UNAVAILABLE")
        effective = local_connection_config(self.config, service.base_url,allow_pull=service.allow_pull)
        async with call_lifetime(context, DEFAULT_CHAT_TIMEOUT) as lifetime:
            await self._local_ensure(service,effective,context,lifetime)
            lifetime.phase="admission"
            body = build_ollama_body(self.config, messages, options, stream=False,
                                     image_resolver=self.image_resolver)
            async with self.transport.open(effective, "/api/chat", body, context,
                    capability=_capability, stream=False, lifetime=lifetime) as handle:
                env = decode_json(await handle.response.aread())
                assert_response_identity(env,self.config)
                response = parse_ollama_response(env, self.config)
                handle.observed(response.usage)
                return response

    async def _local_stream(self, messages, options, context):
        reported=None
        def identity(event):
            return replace(event, data={**(event.data or {}),
                "model_requested": self.config.model, "model_reported": reported})
        service = self.local_service
        if service is None:
            yield identity(local_error_event(ProviderError("MODEL_LOCAL_SERVICE_UNAVAILABLE")))
            return
        effective = local_connection_config(self.config, service.base_url,allow_pull=service.allow_pull)
        state = OllamaStreamState(self.config)
        try:
            async with call_lifetime(context, DEFAULT_STREAM_TIMEOUT) as lifetime:
                await self._local_ensure(service,effective,context,lifetime)
                lifetime.phase="admission"
                body = build_ollama_body(self.config, messages, options, stream=True,
                                         image_resolver=self.image_resolver)
                async with self.transport.open(effective, "/api/chat", body, context,
                        capability="chat", stream=True, lifetime=lifetime) as handle:
                    usage = None
                    completed=False
                    async for env in ndjson_events(handle.response):
                        assert_response_identity(env,self.config)
                        if env.get("model") is not None:reported=env["model"]
                        for event in state.feed(env):
                            if event.is_final:
                                completed=True
                                usage=event.usage
                                handle.observed(usage or TokenUsage(cache_status="unsupported"))
                            yield identity(event)
                        if completed:break
                    if not completed:raise ProviderError("PROVIDER_STREAM_INCOMPLETE",sent=True)
        except (CallCancelled,RunDeadlineExceeded):
            raise
        except ProviderError as error:
            for event in state.finish_thinking():
                yield identity(event)
            yield identity(local_error_event(error))
        except Exception:
            for event in state.finish_thinking():
                yield identity(event)
            yield identity(local_error_event(ProviderError("MODEL_LOCAL_CHAT_FAILED")))

    async def _native_stream(self,messages,options,context):
        options=self._options(options,context)
        assembler=StreamAssembler();state=NativeStreamState(self.config,assembler)
        reported=None
        def identity(event):
            data={**(event.data or {}),"model_requested":self.config.model,"model_reported":reported}
            if event.is_final and state.metadata: data["reasoning_metadata"]=state.metadata
            return replace(event,data=data)
        try:
            async with call_lifetime(context,DEFAULT_STREAM_TIMEOUT) as lifetime:
                for retry in (False,True):
                    body=self.build_request_body(strip_images(messages) if retry else messages,options,stream=True)
                    ctx=context.child("image-fallback") if retry else context
                    try:
                        async with self.transport.open(self.config,"",body,ctx,capability="chat",stream=True,
                                affinity=self._affinity(options,context),lifetime=lifetime) as handle:
                            async for done,raw in sse_events(handle.response):
                                if done: break
                                if not raw: continue
                                env=decode_json(raw)
                                events=state.process(env)
                                handle.usage=assembler.usage or normalize_usage(None)
                                assert_response_identity(env,self.config)
                                if env.get("model"): reported=env["model"]
                                for event in events: yield event
                                if state.complete: break
                            for event in state.finish(): yield event
                            handle.observed(assembler.usage or normalize_usage(None))
                        for event in assembler.end(): yield identity(event)
                        return
                    except ProviderError as error:
                        if not retry and getattr(error,"multimodal_unsupported",False): continue
                        raise
        except CallCancelled:
            raise
        except ProviderError as error:
            for event in assembler.fail(error): yield identity(event)

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
                    if self.config.embedding_api == "dashscope-embeddings":
                        if env.get("code"): raise ProviderError("PROVIDER_API_ERROR",sent=True)
                        rows=(env.get("output") or {}).get("embeddings")
                    elif self.config.embedding_api == "ark-embeddings":
                        if len(batch)!=1: raise ProviderError("MODEL_EMBEDDING_BATCH_INVALID")
                        rows=[{"index":0,"embedding":(env.get("data") or {}).get("embedding")}]
                    elif self.config.embedding_api == "google-embeddings":
                        raw=env.get("embeddings")
                        rows=[{"index":i,"embedding":r.get("values")} for i,r in enumerate(raw)] if isinstance(raw,list) and all(isinstance(r,dict) for r in raw) else None
                    else:
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
                    protocol=self.config.rerank_api
                    if protocol=="dashscope-rerank":
                        if env.get("code"): raise ProviderError("PROVIDER_API_ERROR",sent=True)
                        rows=(env.get("output") or {}).get("results",[])
                    elif protocol=="nim-rerank":
                        raw=env.get("rankings",[])
                        rows=[{"index":r.get("index",0),"relevance_score":r.get("logit",0),"document":batch[r.get("index",0)] if type(r.get("index",0)) is int and 0<=r.get("index",0)<len(batch) else ""} for r in raw] if isinstance(raw,list) and all(isinstance(r,dict) for r in raw) else None
                    elif protocol in ("tencent-lkeap","volcengine-knowledge"):
                        data=env.get("Response") if protocol=="tencent-lkeap" else env.get("data")
                        if not isinstance(data,dict) or data.get("Error") or env.get("code",0): raise ProviderError("PROVIDER_API_ERROR",sent=True)
                        scores=data.get("ScoreList") if protocol=="tencent-lkeap" else data.get("scores")
                        if not isinstance(scores,list) or len(scores)!=len(batch): raise ProviderError("PROVIDER_RERANK_INVALID",sent=True)
                        rows=[{"index":i,"relevance_score":score or 0,"document":batch[i]} for i,score in enumerate(scores)]
                    else:
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
