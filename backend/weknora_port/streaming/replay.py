"""Replay and final persistence ordering contracts (ContinueStream semantic port)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from .events import StreamEvent, coalesce_replay_events
from .manager import StreamStore


class ReplayIdentity(Protocol):
    async def validate_session(self, session_id: str, principal: Any) -> None: ...
    async def validate_assistant_message(self, session_id: str, message_id: str) -> "AssistantRun": ...


@dataclass(frozen=True)
class AssistantRun:
    request_id: str
    message_id: str


@dataclass(frozen=True)
class FinalCommit:
    """Storage callback input for the root-owned atomic answer/reference write."""
    session_id: str
    message_id: str
    request_id: str
    answer: str
    references: Any
    usage: dict[str, Any] | None = None


class FinalCommitter(Protocol):
    async def commit_final(self, commit: FinalCommit) -> None:
        """Persist final answer and references atomically; raise on any failure."""


async def finalize_turn(store: StreamStore, committer: FinalCommitter, commit: FinalCommit,
                        *, complete_data: dict[str, Any] | None = None) -> None:
    """Persist before publishing terminal complete; no completion on failure."""
    await committer.commit_final(commit)
    await store.append_event(commit.session_id, commit.message_id,
        StreamEvent(commit.request_id, "complete", data=complete_data or {}, usage=commit.usage))


@dataclass
class ReplayService:
    store: StreamStore
    identity: ReplayIdentity
    poll_seconds: float = 0.1

    async def replay(self, session_id: str, message_id: str, principal: Any) -> AsyncIterator[StreamEvent]:
        """Replay offset zero then poll same run until complete or caller disconnects.

        This does not invoke a model and does not start/retry generation. Caller
        cancellation ends only this iterator. HTTP route must pass request context.
        """
        await self.identity.validate_session(session_id, principal)
        run = await self.identity.validate_assistant_message(session_id, message_id)
        if run.message_id != message_id:
            raise ValueError("ASSISTANT_MESSAGE_ID_MISMATCH")
        events, offset = await self.store.get_events(session_id, message_id, 0)
        if not events:
            raise LookupError("STREAM_EVENTS_NOT_FOUND")
        replay = coalesce_replay_events(events)
        for event in replay:
            yield event
        if any(event.type == "complete" for event in events):
            return
        while True:
            await asyncio.sleep(self.poll_seconds)
            next_events, next_offset = await self.store.get_events(session_id, message_id, offset)
            for event in next_events:
                yield event
                if event.type == "complete":
                    return
            offset = next_offset
