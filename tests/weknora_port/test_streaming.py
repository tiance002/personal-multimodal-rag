"""Fixed-source stream persistence/replay contract tests; fake Redis only."""
import asyncio
import json

import pytest

from backend.weknora_port.streaming import (
    AssistantRun, FinalCommit, InMemoryStreamStore, RedisListStreamStore,
    ReplayService, StreamEvent, coalesce_replay_events, finalize_turn, to_sse,
)


def test_wire_response_uses_message_event_and_request_id_not_segment_id():
    event = StreamEvent("segment-1", "answer", "hello", data={"event_id": "segment-1"})
    frame = to_sse(event, "request-9").decode()
    assert frame.startswith("event: message\ndata: ")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload["id"] == "request-9"
    assert payload["response_type"] == "answer"
    assert payload["data"]["event_id"] == "segment-1"


def test_replay_coalesces_only_adjacent_unfinished_same_segment_without_mutating():
    a = StreamEvent("seg", "answer", "a", data={"x": 1})
    b = StreamEvent("seg", "answer", "b", timestamp="t2", data={"y": 2})
    done = StreamEvent("seg", "answer", "!", done=True)
    other = StreamEvent("other", "answer", "z")
    thinking = StreamEvent("seg", "thinking", "t")
    merged = coalesce_replay_events([a, b, done, other, thinking])
    assert [(e.type, e.event_id, e.content, e.done) for e in merged] == [
        ("answer", "seg", "ab", False), ("answer", "seg", "!", True),
        ("answer", "other", "z", False), ("thinking", "seg", "t", False)]
    assert merged[0].timestamp == "t2" and merged[0].data == {"x": 1, "y": 2}
    assert a.content == "a" and a.data == {"x": 1}


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.expiries = {}

    async def rpush(self, key, value):
        self.data.setdefault(key, []).append(value)

    async def expire(self, key, ttl):
        self.expiries[key] = ttl

    async def lrange(self, key, start, end):
        rows = self.data.get(key, [])
        return rows[start:] if end == -1 else rows[start:end+1]


def test_redis_list_offset_counts_malformed_raw_rows_and_refreshes_ttl():
    async def run():
        redis = FakeRedis()
        store = RedisListStreamStore(redis)
        await store.append_event("s", "m", StreamEvent("e1", "answer", "a"))
        key = "stream:events:s:m"
        redis.data[key].insert(1, "not-json")
        await store.append_event("s", "m", StreamEvent("e2", "complete"))
        first, offset = await store.get_events("s", "m", 0)
        assert [e.event_id for e in first] == ["e1", "e2"]
        assert offset == 3
        assert redis.expiries[key] == 86400
        tail, next_offset = await store.get_events("s", "m", offset)
        assert tail == [] and next_offset == offset
    asyncio.run(run())


class Identity:
    async def validate_session(self, session_id, principal):
        assert session_id == "session" and principal == "owner"

    async def validate_assistant_message(self, session_id, message_id):
        assert message_id == "assistant-message"
        return AssistantRun("request", message_id)


def test_get_replay_starts_at_zero_emits_complete_and_does_not_generate():
    async def run():
        store = InMemoryStreamStore()
        await store.append_event("session", "assistant-message", StreamEvent("seg", "answer", "part"))
        await store.append_event("session", "assistant-message", StreamEvent("seg", "answer", "", done=True))
        await store.append_event("session", "assistant-message", StreamEvent("request", "complete"))
        replay = ReplayService(store, Identity())
        events = [event async for event in replay.replay("session", "assistant-message", "owner")]
        assert [(e.type, e.content, e.done) for e in events] == [
            ("answer", "part", False), ("answer", "", True), ("complete", "", False)]
        assert not hasattr(replay, "generate")
    asyncio.run(run())


def test_reconnect_continues_same_run_and_disconnect_only_stops_reader():
    async def run():
        store = InMemoryStreamStore()
        await store.append_event("session", "assistant-message", StreamEvent("seg", "answer", "a"))
        replay = ReplayService(store, Identity(), poll_seconds=0.001)
        iterator = replay.replay("session", "assistant-message", "owner")
        first = await anext(iterator)
        assert first.content == "a"
        await iterator.aclose()  # request disconnect; no generation/cancellation side effect
        await store.append_event("session", "assistant-message", StreamEvent("seg", "answer", "b", done=True))
        await store.append_event("session", "assistant-message", StreamEvent("request", "complete"))
        resumed = [event async for event in replay.replay("session", "assistant-message", "owner")]
        assert [e.content for e in resumed if e.type == "answer"] == ["a", "b"]
    asyncio.run(run())


def test_final_persistence_precedes_complete_and_failure_never_appends_terminal():
    class Committer:
        def __init__(self, *, fail=False):
            self.calls = []
            self.fail = fail

        async def commit_final(self, commit):
            self.calls.append(commit)
            if self.fail:
                raise RuntimeError("atomic storage failed")

    async def run():
        store = InMemoryStreamStore()
        commit = FinalCommit("s", "m", "req", "answer", [{"chunk_id": "c"}], {"total_tokens": 3})
        committer = Committer()
        await finalize_turn(store, committer, commit, complete_data={"final_content": "answer"})
        rows, _ = await store.get_events("s", "m", 0)
        assert [event.type for event in rows] == ["complete"]
        assert committer.calls == [commit]

        failed_store = InMemoryStreamStore()
        with pytest.raises(RuntimeError, match="atomic storage failed"):
            await finalize_turn(failed_store, Committer(fail=True), commit)
        rows, _ = await failed_store.get_events("s", "m", 0)
        assert rows == []
    asyncio.run(run())
