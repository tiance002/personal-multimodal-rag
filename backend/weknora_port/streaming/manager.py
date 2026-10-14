"""Append-only stream storage protocol and Redis-list adapter.

Adapted from internal/stream/redis_manager.go (WeKnora MIT). No Redis is
created or contacted on import; callers inject an async Redis-compatible client.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from .events import StreamEvent


class StreamStore(Protocol):
    async def append_event(self, session_id: str, message_id: str, event: StreamEvent) -> None: ...
    async def get_events(self, session_id: str, message_id: str, from_offset: int) -> tuple[list[StreamEvent], int]: ...


class RedisListClient(Protocol):
    async def rpush(self, key: str, value: str) -> Any: ...
    async def expire(self, key: str, seconds: int) -> Any: ...
    async def lrange(self, key: str, start: int, end: int) -> list[str | bytes]: ...


@dataclass
class RedisListStreamStore:
    client: RedisListClient
    prefix: str = "stream:events"
    ttl_seconds: int = 86400
    # Application may inject its live-run lease adapter. The source manager
    # refreshes this lease on every append and read, including empty polls.
    touch_live_run: Callable[[str, int], Awaitable[None]] | None = None

    def _key(self, session_id: str, message_id: str) -> str:
        return f"{self.prefix}:{session_id}:{message_id}"

    async def _touch_live_run(self, session_id: str) -> None:
        if self.touch_live_run is not None and session_id:
            try:
                await self.touch_live_run(session_id, self.ttl_seconds)
            except Exception:
                # Fixed source deliberately ignores lease-refresh errors.
                return

    async def append_event(self, session_id: str, message_id: str, event: StreamEvent) -> None:
        key = self._key(session_id, message_id)
        value = json.dumps(event.with_timestamp().as_dict(), ensure_ascii=False, separators=(",", ":"))
        await self.client.rpush(key, value)
        await self.client.expire(key, self.ttl_seconds)
        await self._touch_live_run(session_id)

    async def get_events(self, session_id: str, message_id: str, from_offset: int) -> tuple[list[StreamEvent], int]:
        if from_offset < 0:
            raise ValueError("STREAM_OFFSET_INVALID")
        rows = await self.client.lrange(self._key(session_id, message_id), from_offset, -1)
        await self._touch_live_run(session_id)
        events: list[StreamEvent] = []
        for row in rows:
            try:
                events.append(StreamEvent.from_dict(json.loads(row)))
            except (ValueError, TypeError, UnicodeDecodeError):
                # Offsets count raw Redis rows, even if a malformed row is skipped.
                continue
        return events, from_offset + len(rows)


@dataclass
class InMemoryStreamStore:
    """Deterministic protocol fake for tests; not evidence of real Redis behavior."""
    rows: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    async def append_event(self, session_id: str, message_id: str, event: StreamEvent) -> None:
        key = (session_id, message_id)
        value = json.dumps(event.with_timestamp().as_dict(), ensure_ascii=False, separators=(",", ":"))
        self.rows.setdefault(key, []).append(value)

    async def get_events(self, session_id: str, message_id: str, from_offset: int) -> tuple[list[StreamEvent], int]:
        if from_offset < 0:
            raise ValueError("STREAM_OFFSET_INVALID")
        rows = self.rows.get((session_id, message_id), [])[from_offset:]
        return [StreamEvent.from_dict(json.loads(row)) for row in rows], from_offset + len(rows)
