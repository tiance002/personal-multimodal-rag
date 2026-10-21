"""Local Ollama chat adapter (source=='local').

Semantic port of Tencent/WeKnora@3e8b0bfc:
  internal/models/chat/chat.go      NewChat dispatch, ConfigFromModel (L100/L121)
  internal/models/chat/ollama.go    NewOllamaChat/buildChatRequest/convertMessages/Chat/ChatStream
  internal/models/utils/ollama/ollama.go  OllamaService.Chat/EnsureModelAvailable

Boundaries that differ from upstream on purpose:
  * Upstream's callback-based ChatStream becomes an async iterator of decoded
    NDJSON objects; the connection lives in the injected local service, not in
    this module (no ollama/httpx base URL comes from the row's BaseURL/APIKey).
  * Ollama streaming is NDJSON (one JSON object per line), never SSE. It is
    decoded by ``ndjson_events`` below, not by ``stream.sse_events``.
  * Local calls retain the shared endpoint, scope, send and settlement gates.
    They never fall back to a remote connection and fail loudly ("MODEL_LOCAL_SERVICE_UNAVAILABLE") when no local
    service is wired.
  * Thinking content is used only as a Content fallback (upstream
    responseContent == "" && Thinking != ""), never promoted to real evidence.
"""
from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy
from dataclasses import replace
from typing import AsyncIterator, Mapping, Protocol, Sequence

from .request import neutralize
from .types import (ChatOptions, ChatResponse, FunctionCall, Message, ModelConfig,
                    ProviderError, StreamResponse, ToolCall, TokenUsage)


class LocalChatService(Protocol):
    """Injected local connection seam (one adapter = upstream OllamaService).

    Chat bytes are sent through the injected ProviderModel transport (one
    controlled egress boundary), so this seam only supplies the connection
    base URL and the availability check; it never buffers a chat response.
    """

    base_url: str
    allow_pull: bool
    async def ensure_model_available(self, model_name: str, **controlled) -> None: ...


def local_connection_config(config: ModelConfig, base_url: str, *, allow_pull=False) -> ModelConfig:
    """The only local chat connection source: service, not row inline secrets.

    api='ollama' renders /api/chat; base URL comes from the injected service;
    the row's endpoint URL/query and any inline credential are dropped.
    """
    return replace(config, api="ollama", base_url=base_url.rstrip("/"),
                   endpoint_url="", query={}, requires_auth=False, auth_style="none",
                   custom_headers={},extra_config={"local_allow_pull":"true"} if allow_pull else {})


# ---------------------------------------------------------------- request wire

def _tool_calls_out(tool_calls) -> list[dict]:
    """Port tool calls back to Ollama's ``{function:{index,name,arguments}}``."""
    out = []
    for tc in tool_calls:
        arguments: dict = {}
        raw = neutralize(tc.function.arguments)
        if raw:
            parsed = _try_json(raw)
            if isinstance(parsed, dict):
                arguments = parsed
        out.append({"function": {"index": _atoi(tc.id), "name": tc.function.name,
                                 "arguments": arguments}})
    return out


def _atoi(text) -> int:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        return 0


def _try_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def convert_ollama_messages(config: ModelConfig, messages: Sequence[Message],
                            image_resolver=None) -> list[dict]:
    resolve = image_resolver or (lambda url: url)
    out = []
    for msg in messages:
        content = neutralize(msg.content) if msg.content else ""
        entry: dict = {"role": msg.role, "content": content}
        calls = _tool_calls_out(msg.tool_calls)
        if calls:
            entry["tool_calls"] = calls
        if msg.role == "tool" and msg.name:
            entry["tool_name"] = msg.name
        images = list(msg.images)
        if msg.multi_content:
            images = list(msg.images)
            texts = []
            for part in msg.multi_content:
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
                elif part.get("type") == "image_url" and part.get("image_url"):
                    url = (part["image_url"] or {}).get("url")
                    if url:
                        images.append(url)
            if entry["content"] == "":
                entry["content"] = neutralize("".join(texts))
        if images and msg.role == "user":
            payload = []
            for url in images:
                data = _image_bytes(resolve(url))
                if data is not None:
                    payload.append(base64.b64encode(data).decode("ascii"))
            if payload:
                entry["images"] = payload
        out.append(entry)
    return out


def _image_bytes(url):
    """Inline data: URIs only. No network fetch ever happens on this path."""
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    head, _, body = url.partition(",")
    if ";base64" not in head:
        return None
    try:
        return base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        return None


def build_ollama_body(config: ModelConfig, messages: Sequence[Message],
                      options: ChatOptions | None, *, stream: bool = False,
                      image_resolver=None) -> dict:
    body = {"model": config.model,
            "messages": convert_ollama_messages(config, messages, image_resolver),
            "stream": bool(stream)}
    if options is not None:
        wire: dict = {"temperature": options.temperature}
        if options.top_p > 0:
            wire["top_p"] = options.top_p
        budget = options.completion_budget()
        if budget > 0:
            wire["num_predict"] = budget
        if wire:
            body["options"] = wire
        level, requested = options.reasoning()
        if requested:
            body["think"] = level not in ("", "off")
        if options.format:
            parsed = _try_json(options.format)
            body["format"] = parsed if parsed is not None else options.format
        if options.tools:
            tools = []
            for tool in options.tools:
                function = {"name": tool.name, "description": tool.description}
                if tool.parameters:
                    function["parameters"] = deepcopy(tool.parameters)
                tools.append({"type": tool.type or "function", "function": function})
            body["tools"] = tools
    return body


# --------------------------------------------------------------- response wire

def _response_tool_calls(raws):
    if not isinstance(raws, list):
        raise ProviderError("PROVIDER_TOOL_CALL_INVALID", sent=True)
    calls = []
    for raw in raws:
        if not isinstance(raw, dict) or not isinstance(raw.get("function", {}), dict):
            raise ProviderError("PROVIDER_TOOL_CALL_INVALID", sent=True)
        function = raw.get("function", {})
        index = function.get("index", 0)
        if type(index) is not int:
            index = 0
        name = function.get("name")
        if name is None:
            name = ""
        if not isinstance(name, str):
            raise ProviderError("PROVIDER_TOOL_CALL_INVALID", sent=True)
        arguments = function.get("arguments")
        if arguments is None:
            arguments = {}
        arguments = json.dumps(arguments, separators=(",", ":"), sort_keys=True)
        calls.append((index, ToolCall(str(index), "function", FunctionCall(name, arguments))))
    return calls


def _message_identity(env):
    if not isinstance(env, Mapping):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    message = env.get("message") or {}
    if not isinstance(message, Mapping):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    return message


def parse_ollama_response(env, config: ModelConfig) -> ChatResponse:
    """Non-stream /api/chat body -> ChatResponse (upstream Chat callback)."""
    _reject_vendor_error(env)
    message = _message_identity(env)
    content = _text(message.get("content"))
    if content == "":
        content = _text(message.get("thinking"))
    calls = tuple(call for _, call in _response_tool_calls(message.get("tool_calls") or []))
    return ChatResponse(content, "", "", calls, _usage(env), {}, env.get("model") or "")


def _text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    return value


def _count(value) -> int:
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    return value


def _reject_vendor_error(env) -> None:
    """Ollama may answer HTTP 200 with a top-level ``error`` (non-stream and,
    as an NDJSON line, in-stream). Surface a fixed project code only: the
    vendor text can echo a key or the private prompt."""
    error = env.get("error") if isinstance(env, Mapping) else None
    if (isinstance(error, Mapping) and error) or (isinstance(error, str) and error.strip()):
        raise ProviderError("PROVIDER_API_ERROR", sent=True)


def _usage(env) -> TokenUsage:
    """Ollama eval counters, without fabricating a settlement.

    Both ``prompt_eval_count`` and ``eval_count`` present (including an explicit
    zero) -> REPORTED with the sum. Neither present -> UNKNOWN. Only one
    present -> UNKNOWN with the missing counter left absent, never padded to 0.
    """
    prompt_present = env.get("prompt_eval_count") is not None
    completion_present = env.get("eval_count") is not None
    if prompt_present and completion_present:
        prompt, completion = _count(env["prompt_eval_count"]), _count(env["eval_count"])
        return TokenUsage(prompt, completion, prompt + completion, "REPORTED",
                          0, 0, 0, 0, False, "unsupported")
    if not prompt_present and not completion_present:
        return TokenUsage(cache_status="unsupported")
    return TokenUsage(_count(env["prompt_eval_count"]) if prompt_present else None,
                      _count(env["eval_count"]) if completion_present else None,
                      None, "UNKNOWN", 0, 0, 0, 0, False, "unsupported")


class OllamaStreamState:
    """Pinned upstream ChatStream callback as an incremental mapper."""

    def __init__(self, config: ModelConfig):
        self.config = config
        self.thinking_active = False
        self.calls: dict[int, ToolCall] = {}

    def finish_thinking(self):
        if not self.thinking_active:
            return []
        self.thinking_active = False
        return [StreamResponse("thinking", done=True)]

    def feed(self, env) -> list[StreamResponse]:
        _reject_vendor_error(env)
        if "done" in env and type(env["done"]) is not bool:raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
        message = _message_identity(env)
        events: list[StreamResponse] = []
        thinking = _text(message.get("thinking"))
        content = _text(message.get("content"))
        if thinking:
            self.thinking_active = True
            events.append(StreamResponse("thinking", thinking))
        if content:
            events.extend(self.finish_thinking())
            events.append(StreamResponse("answer", content))
        raws = message.get("tool_calls") or []
        calls = _response_tool_calls(raws)
        if calls:
            for index, call in calls:
                self.calls[index] = call
            events.append(StreamResponse("tool_call", tool_calls=tuple(call for _, call in calls)))
            for raw in raws:
                function = raw.get("function") if isinstance(raw, dict) else None
                if isinstance(function, Mapping) and function.get("name") == "thinking":
                    arguments = function.get("arguments")
                    thought = arguments.get("thought") if isinstance(arguments, Mapping) else None
                    if isinstance(thought, str) and thought:
                        events.append(StreamResponse("thinking", thought,
                            data={"source": "thinking_tool", "tool_call_id": str(function.get("index", 0))}))
        if env.get("done"):
            events.append(StreamResponse("answer", done=True, usage=_usage(env),
                tool_calls=tuple(self.calls[key] for key in sorted(self.calls)), is_final=True))
        return events


# --------------------------------------------------------------- NDJSON decode

async def ndjson_events(response) -> AsyncIterator[dict]:
    """Ollama NDJSON transport: one JSON object per line, not an SSE frame."""
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        while b"\n" in buffer:
            index = buffer.index(10)
            line = bytes(buffer[:index])
            del buffer[:index + 1]
            event = _ndjson_object(line.rstrip(b"\r"))
            if event is not None:
                yield event
        if len(buffer) >= 1 << 20:
            raise ProviderError("PROVIDER_NDJSON_LINE_TOO_LONG", sent=True)
    if buffer:
        event = _ndjson_object(bytes(buffer).rstrip(b"\r"))
        if event is not None:
            yield event


def _ndjson_object(line):
    line = line.strip()
    if not line:
        return None
    if len(line) >= 1 << 20:
        raise ProviderError("PROVIDER_NDJSON_LINE_TOO_LONG", sent=True)
    try:
        value = json.loads(line,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True) from None
    if not isinstance(value, dict):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    _reject_vendor_error(value)
    return value


# ------------------------------------------------------------ default service

class HTTPLocalService:
    """Global connection metadata, with checks on the same gated transport.

    Pull requires an explicit server authorization; service startup is never automatic.
    Endpoint admission uses the application's existing exact endpoint set and
    SafeHTTPTransport policy; this service never creates a second HTTP client.
    """
    def __init__(self, base_url: str, *, allow_pull: bool, **legacy):
        if legacy:raise ProviderError("MODEL_LOCAL_CONTROL_GATE_REQUIRED")
        if type(allow_pull) is not bool:raise ProviderError("MODEL_LOCAL_PULL_POLICY_REQUIRED")
        self.base_url=base_url.rstrip("/")
        self.allow_pull=allow_pull

    async def aclose(self):pass

    async def ensure_model_available(self, model_name, *, transport, config, context, lifetime):
        async with transport.open(config,"/api/tags",{},context.child("local-availability"),
                capability="local_model_check",method="GET",lifetime=lifetime) as handle:
            listed=_ndjson_object(await handle.response.aread())
            if not isinstance(listed,dict) or not isinstance(listed.get("models"),list):
                raise ProviderError("PROVIDER_RESPONSE_INVALID",sent=True)
            names={row.get("name") for row in listed["models"] if isinstance(row,Mapping)}
            wanted=model_name if ":" in model_name else model_name+":latest"
            available=wanted in names or model_name in names
            if not available and not self.allow_pull:raise ProviderError("MODEL_LOCAL_MODEL_UNAVAILABLE",sent=True)
            handle.observed(TokenUsage(cache_status="unsupported"))
        if available:return
        lifetime.phase="admission"
        async with transport.open(config,"/api/pull",{"name":model_name,"stream":True},
                context.child("local-pull"),capability="local_model_pull",stream=True,lifetime=lifetime) as handle:
            completed=False
            async for event in ndjson_events(handle.response):
                if event.get("status")=="success":completed=True
            if not completed:raise ProviderError("MODEL_LOCAL_MODEL_UNAVAILABLE",sent=True)
            handle.observed(TokenUsage(cache_status="unsupported"))



def local_error_event(error: ProviderError) -> StreamResponse:
    return StreamResponse("error", error.code, True, (), None, "incomplete",
                          {"error_code": error.code, "error_class": type(error).__name__}, True)


__all__ = ["LocalChatService", "build_ollama_body", "convert_ollama_messages",
           "parse_ollama_response", "OllamaStreamState", "ndjson_events",
           "HTTPLocalService", "local_error_event", "local_connection_config"]
