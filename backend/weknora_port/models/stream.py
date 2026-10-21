"""Pinned SSEReader + StreamAssembler semantics, without raw-content logging."""
from __future__ import annotations

import json
from dataclasses import replace
from .types import FunctionCall, ToolCall, StreamResponse, ProviderError


async def sse_events(response):
    """Each data line is an event, including data: without its optional space.

    Upstream does not combine multiline SSE fields. Scanner's 1MiB line bound
    is retained. Clean EOF is normal; socket/JSON errors are failed streams.
    """
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        while b"\n" in buffer:
            index = buffer.index(10)
            line = bytes(buffer[:index])
            del buffer[:index+1]
            if len(line) >= 1 << 20:
                raise ProviderError("PROVIDER_SSE_LINE_TOO_LONG", sent=True)
            event = _line_event(line.rstrip(b"\r"))
            if event is not None:
                yield event
                if event[0]:
                    return
        if len(buffer) >= 1 << 20:
            raise ProviderError("PROVIDER_SSE_LINE_TOO_LONG", sent=True)
    if buffer:
        event = _line_event(bytes(buffer).rstrip(b"\r"))
        if event is not None:
            yield event


def _line_event(line):
    if not line.startswith(b"data:"):
        return None
    data = line[5:]
    if data.startswith(b" "):
        data = data[1:]
    if data.strip() == b"[DONE]":
        return True, b""
    return False, data


def decode_tool_calls(raws, settings):
    if not isinstance(raws, list):
        raise ProviderError("PROVIDER_TOOL_CALL_INVALID", sent=True)
    calls, deltas = [], []
    for i, raw in enumerate(raws):
        if not isinstance(raw, dict) or not isinstance(raw.get("function", {}), dict):
            raise ProviderError("PROVIDER_TOOL_CALL_INVALID", sent=True)
        fn = raw.get("function", {})
        index = raw.get("index", i)
        strings = (raw.get("id", ""), raw.get("type", ""), fn.get("name", ""), fn.get("arguments", ""))
        if type(index) is not int or any(not isinstance(value, str) for value in strings):
            raise ProviderError("PROVIDER_TOOL_CALL_INVALID", sent=True)
        identity, kind, name, arguments = strings
        metadata = {key: raw[key] for key in settings.tool_call_extra_fields if raw.get(key) is not None}
        call = ToolCall(identity, kind or "function", FunctionCall(name, arguments), metadata)
        calls.append(call)
        deltas.append((index, call))
    return tuple(calls), deltas


class JSONFieldExtractor:
    """Incremental thinking-tool text extraction, never a semantic validator."""

    def __init__(self, field):
        self.field, self.buffer, self.start, self.emitted, self.done = field, "", None, 0, False

    def feed(self, fragment):
        if self.done:
            return ""
        self.buffer += fragment
        if self.start is None:
            index = self.buffer.find(json.dumps(self.field))
            if index < 0:
                return ""
            pos = index + len(json.dumps(self.field))
            while pos < len(self.buffer) and self.buffer[pos] in ": \t\r\n":
                pos += 1
            if pos == len(self.buffer) or self.buffer[pos] != '"':
                return ""
            self.start = pos+1
        value, end = self.buffer[self.start:], self.emitted
        while end < len(value):
            if value[end] == '"':
                self.done = True
                break
            if value[end] == "\\":
                size = 6 if end+1 < len(value) and value[end+1] == "u" else 2
                if end+size > len(value):
                    break
                end += size
            else:
                end += 1
        raw = value[self.emitted:end]
        self.emitted = end
        try:
            return json.loads('"' + raw + '"') if raw else ""
        except (json.JSONDecodeError, UnicodeError):
            return ""  # only a thinking progress hint; raw tool args are intact


class StreamAssembler:
    def __init__(self):
        self.active = False
        self.calls = {}
        self.last_names, self.notified, self.extractors = {}, set(), {}
        self.usage = None
        self.last_finish_reason = ""
        self.reasoning_details = []

    def ordered_calls(self):
        return tuple(self.calls[key] for key in sorted(self.calls))

    def finish_thinking(self):
        if not self.active:
            return []
        self.active = False
        return [StreamResponse("thinking", done=True)]

    def process_tools(self, deltas):
        events = []
        for index, tc in deltas:
            entry = self.calls.get(index, ToolCall())
            name = entry.function.name
            if tc.function.name and name != tc.function.name:
                name += tc.function.name
            entry = ToolCall(tc.id or entry.id, tc.type or entry.type or "function",
                FunctionCall(name, entry.function.arguments + tc.function.arguments),
                tc.provider_metadata or entry.provider_metadata)
            self.calls[index] = entry
            if (name and name == self.last_names.get(index) and tc.function.arguments and
                    index not in self.notified and entry.id):
                events.append(StreamResponse("tool_call", data={"tool_name": name, "tool_call_id": entry.id}))
                self.notified.add(index)
            self.last_names[index] = name
            if name == "thinking" and tc.function.arguments:
                extractor = self.extractors.setdefault(index, JSONFieldExtractor("thought"))
                text = extractor.feed(tc.function.arguments)
                if text:
                    events.append(StreamResponse("thinking", text,
                        data={"source": "thinking_tool", "tool_call_id": entry.id}))
        return events

    def process(self, *, reasoning="", content="", tools=(), finish_reason=""):
        events = self.process_tools(tools)
        done = bool(finish_reason)
        if done:
            self.last_finish_reason = finish_reason
        if reasoning:
            self.active = True
            events.append(StreamResponse("thinking", reasoning))
        if content:
            events.extend(self.finish_thinking())
            events.append(StreamResponse("answer", content, done, self.ordered_calls(), finish_reason=finish_reason))
        if done and self.calls:
            events.append(StreamResponse("answer", done=True, tool_calls=self.ordered_calls(), finish_reason=finish_reason))
        if done:
            events.extend(self.finish_thinking())
        if done and not content and not self.calls:
            events.append(StreamResponse("answer", done=True, finish_reason=finish_reason))
        return events

    def end(self):
        data = {"provider_answer_done": bool(self.last_finish_reason)}
        if self.reasoning_details:data["reasoning_metadata"]={"reasoning_details":self.reasoning_details}
        return [*self.finish_thinking(), StreamResponse("answer", done=True,
            tool_calls=self.ordered_calls(), usage=self.usage,
            finish_reason=self.last_finish_reason, data=data, is_final=True)]

    def fail(self, error):
        return [*self.finish_thinking(), StreamResponse("error", error.code, True,
            self.ordered_calls(), self.usage, "incomplete",
            {"error_code": error.code, "error_class": type(error).__name__, "status_code": error.status_code}, True)]
