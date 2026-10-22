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
    quality: dict[str, Any] | None = None


def validate_final_quality(value):
    """A content-free observation; never a semantic-support or routing verdict."""
    if (not isinstance(value, dict) or set(value) != {'citation_quality', 'generation_issues'}
            or not isinstance(value['citation_quality'], dict)):
        raise ValueError('FINAL_QUALITY_INVALID')
    citation = value['citation_quality']
    fields = {'status', 'valid_handle_count', 'invalid_handle_count', 'usable_evidence_count',
              'assessment_complete', 'semantic_support'}
    if (set(citation) != fields or citation['status'] not in
            {'VALID', 'INVALID', 'MISSING', 'EVIDENCE_INSUFFICIENT', 'NOT_REQUIRED', 'NOT_ASSESSED'}
            or citation['semantic_support'] != 'NOT_ASSESSED'
            or type(citation['assessment_complete']) is not bool
            or any(type(citation[k]) is not int or citation[k] < 0 for k in
                   ('valid_handle_count', 'invalid_handle_count', 'usable_evidence_count'))
            or not isinstance(value['generation_issues'], list)
            or any(not isinstance(x,str) or x not in {'EMPTY', 'TRUNCATED', 'INVALID_HANDLES', 'MISSING_CITATIONS'}
                   for x in value['generation_issues'])):
        raise ValueError('FINAL_QUALITY_INVALID')
    return value


def project_final_quality(row):
    """Project only a new, exact-run record; historical absence stays unknown."""
    if (row.get('role') != 'assistant' or row.get('is_completed') is not True
            or not isinstance(row.get('request_id'),str) or not row['request_id']
            or row.get('turn_status') in ('error', 'cancelled')):
        return row
    steps = row.get('agent_steps')
    if not isinstance(steps, list):
        return row
    matched = [step for step in steps if isinstance(step, dict)
               and step.get('type') == 'generation_quality'
               and step.get('request_id') == row.get('request_id')]
    if len(matched) != 1:
        return row
    try:
        quality = validate_final_quality({k:v for k,v in matched[0].items()
                                          if k not in ('type', 'request_id')})
    except ValueError:
        return row
    return {**row, **quality}


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
