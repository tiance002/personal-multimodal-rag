"""Wire and stored event DTOs adapted from WeKnora stream/interfaces + helpers."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

_DELTAS = frozenset(("answer", "thinking", "reflection"))


@dataclass(frozen=True)
class StreamEvent:
    event_id: str
    type: str
    content: str = ""
    done: bool = False
    timestamp: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    usage: Mapping[str, Any] | None = None

    def with_timestamp(self) -> "StreamEvent":
        if self.timestamp:
            return self
        return StreamEvent(self.event_id, self.type, self.content, self.done,
                           datetime.now(timezone.utc).isoformat(), dict(self.data), self.usage)

    def as_dict(self) -> dict[str, Any]:
        event = self.with_timestamp()
        result = {"id": event.event_id, "type": event.type, "content": event.content,
                  "done": event.done, "timestamp": event.timestamp}
        if event.data:
            result["data"] = dict(event.data)
        if event.usage is not None:
            result["usage"] = dict(event.usage)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StreamEvent":
        return cls(str(data.get("id", "")), str(data.get("type", "")),
                   str(data.get("content", "")), bool(data.get("done", False)),
                   str(data.get("timestamp", "")), data.get("data") or {}, data.get("usage"))


@dataclass(frozen=True)
class StreamResponse:
    id: str
    response_type: str
    content: str = ""
    done: bool = False
    data: Mapping[str, Any] = field(default_factory=dict)
    usage: Mapping[str, Any] | None = None
    knowledge_references: Any = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "response_type": self.response_type,
                                  "content": self.content, "done": self.done}
        if self.data:
            result["data"] = dict(self.data)
        if self.usage is not None:
            result["usage"] = dict(self.usage)
        if self.knowledge_references is not None:
            result["knowledge_references"] = self.knowledge_references
        return result


def response_for(event: StreamEvent, request_id: str) -> StreamResponse:
    refs = event.data.get("references") if event.type == "references" else None
    return StreamResponse(request_id, event.type, event.content, event.done, event.data,
                          event.usage, refs)


def to_sse(event: StreamEvent, request_id: str) -> bytes:
    """Encode the fixed wire frame: `event: message` with JSON request ID."""
    payload = json.dumps(response_for(event, request_id).as_dict(), ensure_ascii=False,
                         separators=(",", ":"))
    return f"event: message\ndata: {payload}\n\n".encode("utf-8")


def coalesce_replay_events(events: list[StreamEvent]) -> list[StreamEvent]:
    """Merge adjacent unfinished deltas of one event type and event_id."""
    if len(events) < 2:
        return list(events)
    out: list[StreamEvent] = []
    for event in events:
        if (out and event.type in _DELTAS and not event.done and
                out[-1].type == event.type and out[-1].event_id == event.event_id and not out[-1].done):
            previous = out[-1]
            data = dict(previous.data)
            data.update(event.data)
            out[-1] = StreamEvent(previous.event_id, previous.type, previous.content + event.content,
                                  False, event.timestamp, data, event.usage or previous.usage)
        else:
            out.append(event)
    return out
