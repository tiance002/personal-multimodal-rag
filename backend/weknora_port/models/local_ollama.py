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
  * No cloud egress gate applies: a local model never falls back to a remote
    transport and fails loudly ("MODEL_LOCAL_SERVICE_UNAVAILABLE") when no local
    service is wired.
  * Thinking content is used only as a Content fallback (upstream
    responseContent == "" && Thinking != ""), never promoted to real evidence.
"""
from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence

from .request import neutralize
from .types import (ChatOptions, ChatResponse, FunctionCall, Message, ModelConfig,
                    ProviderError, StreamResponse, ToolCall, TokenUsage)


class LocalChatService(Protocol):
    """Injected local connection seam (one adapter = upstream OllamaService)."""

    async def ensure_model_available(self, model_name: str) -> None: ...
    def chat(self, model_name: str, request: Mapping[str, Any]) -> AsyncIterator[Mapping[str, Any]]: ...


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
    message = _message_identity(env)
    content = _text(message.get("content"))
    if content == "":
        content = _text(message.get("thinking"))
    calls = tuple(call for _, call in _response_tool_calls(message.get("tool_calls") or []))
    prompt = completion = 0
    if _count(env.get("eval_count")) > 0:
        prompt, completion = _count(env.get("prompt_eval_count")), _count(env.get("eval_count"))
    return ChatResponse(content, "", "", calls,
        TokenUsage(prompt, completion, prompt + completion, "REPORTED", 0, 0, 0, 0, False, "unsupported"),
        {}, "")


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


def _usage(env):
    prompt, completion = _count(env.get("prompt_eval_count")), _count(env.get("eval_count"))
    if prompt <= 0 and completion <= 0:
        return None
    return TokenUsage(prompt, completion, prompt + completion, "REPORTED", 0, 0, 0, 0, False, "unsupported")


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
        value = json.loads(line)
    except (ValueError, TypeError, UnicodeError):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True) from None
    if not isinstance(value, dict):
        raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
    return value


# ------------------------------------------------------------ default service

class HTTPLocalService:
    """Minimal OllamaService equivalent over an injectable httpx transport.

    Model name is the only per-row input; the connection (base URL / client)
    comes from this service, matching upstream's global OllamaService.
    """

    def __init__(self, base_url: str = "http://localhost:11434", *, transport=None,
                 client=None, timeout=300.0):
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        if client is None:
            import httpx  # local only; never used for the remote cloud path
            client = httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False)
        self.client = client

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def ensure_model_available(self, model_name: str) -> None:
        try:
            response = await self.client.get(self.base_url + "/api/tags")
        except Exception as error:  # noqa: BLE001 - connection failure is service-unavailable
            if isinstance(error, ProviderError):
                raise
            raise ProviderError("MODEL_LOCAL_SERVICE_UNAVAILABLE") from None
        if response.status_code != 200:
            raise ProviderError("MODEL_LOCAL_SERVICE_UNAVAILABLE")
        try:
            listed = json.loads(response.content)
        except (ValueError, TypeError):
            raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True) from None
        names = {row.get("name") for row in (listed.get("models") or []) if isinstance(row, Mapping)}
        wanted = model_name if ":" in model_name else model_name + ":latest"
        if wanted in names or model_name in names:
            return
        try:
            response = await self.client.post(self.base_url + "/api/pull",
                                              json={"name": model_name, "stream": True})
        except Exception as error:  # noqa: BLE001
            if isinstance(error, ProviderError):
                raise
            raise ProviderError("MODEL_LOCAL_SERVICE_UNAVAILABLE") from None
        if response.status_code != 200:
            raise ProviderError("MODEL_LOCAL_MODEL_UNAVAILABLE")
        async for _ in ndjson_events(response):
            pass

    async def chat(self, model_name: str, request: Mapping[str, Any]) -> AsyncIterator[dict]:
        try:
            response = await self.client.post(self.base_url + "/api/chat", json=request)
        except Exception as error:  # noqa: BLE001
            if isinstance(error, ProviderError):
                raise
            raise ProviderError("MODEL_LOCAL_SERVICE_UNAVAILABLE") from None
        if response.status_code != 200:
            raise ProviderError("MODEL_LOCAL_CHAT_FAILED")
        if request.get("stream"):
            async for item in ndjson_events(response):
                yield item
        else:
            try:
                item = json.loads(await response.aread())
            except (ValueError, TypeError):
                raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True) from None
            if not isinstance(item, dict):
                raise ProviderError("PROVIDER_RESPONSE_INVALID", sent=True)
            yield item


def local_error_event(error: ProviderError) -> StreamResponse:
    return StreamResponse("error", error.code, True, (), None, "incomplete",
                          {"error_code": error.code, "error_class": type(error).__name__}, True)


__all__ = ["LocalChatService", "build_ollama_body", "convert_ollama_messages",
           "parse_ollama_response", "OllamaStreamState", "ndjson_events",
           "HTTPLocalService", "local_error_event"]
