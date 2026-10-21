"""Offline task ownership/collection checks; in-memory durable admission, no I/O."""
import asyncio
import gc
import weakref
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.weknora_port.conversation.types import MemoryContext
from backend.weknora_port.generation import GenerationReplay
from backend.weknora_port.models.types import CallContext, ProviderError, TrustedScope
from backend.weknora_port.service import (
    AcceptedTurn, OrdinaryTurnRequest, PreparedOrdinaryTurn, RuntimeService,
)
from backend.weknora_port.streaming.events import StreamEvent


class Repository:
    def __init__(self):
        self.turns = {}
        self.failures = []
        self.commits = []
        self.fail_commit = False

    async def accept(self, request, principal):
        assert principal == 'owner'
        prior = self.turns.get(request.request_id)
        if prior:
            if prior.request != request:
                raise ProviderError('TURN_PAYLOAD_CONFLICT')
            return replace(prior, is_new=False, replay_only=True)
        turn = AcceptedTurn(
            request.request_id, request.session_id, 'user-' + request.request_id,
            'assistant-' + request.request_id,
            CallContext(request.request_id, TrustedScope('owner', ('kb',)),
                        cancelled=asyncio.Event(), session_id=request.session_id),
            MemoryContext(1, 'owner'), request, True, False,
        )
        self.turns[request.request_id] = turn
        return turn

    async def validate_run(self, session, message, principal):
        assert principal == 'owner'
        turn = next(t for t in self.turns.values()
                    if (t.session_id, t.assistant_message_id) == (session, message))
        return replace(turn, is_new=False, replay_only=True)

    async def commit_failure(self, turn, code):
        if self.fail_commit:
            raise RuntimeError('private failure detail')
        self.failures.append(code)
        return True

    async def commit_cancelled(self, turn, answer, thinking, references):
        self.failures.append('MODEL_CALL_CANCELLED')
        return True


class Store:
    def __init__(self):
        self.events = {}

    async def append_event(self, session, message, event):
        self.events.setdefault((session, message), []).append(event)

    async def get_events(self, session, message, offset):
        rows = self.events.get((session, message), [])
        return rows[offset:], len(rows)


class Harness:
    def __init__(self, *, outcome=None, blocked=False):
        self.repository = Repository()
        self.store = Store()
        self.outcome = outcome
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.executions = 0
        self.watch_refs = []
        self.watches_closed = 0
        self.service = RuntimeService(
            self.repository, self.store, None, None, configuration=None,
            pipeline_factory=None, generation_factory=lambda *args: self,
        )
        self.service.prepare_turn = self.prepare
        self.service._watch_stop = self.watch

    async def prepare(self, turn, progress):
        self.started.set()
        await asyncio.sleep(0)
        await self.release.wait()
        return PreparedOrdinaryTurn(turn, turn.context)

    async def watch(self, turn):
        self.watch_refs.append(weakref.ref(asyncio.current_task()))
        try:
            await asyncio.Event().wait()
        finally:
            self.watches_closed += 1

    async def run(self, turn, context):
        self.executions += 1
        if isinstance(self.outcome, GenerationReplay):
            raise GenerationReplay()
        if isinstance(self.outcome, ProviderError):
            raise ProviderError(self.outcome.code)
        self.repository.commits.append(turn.run_id)
        await self.store.append_event(turn.session_id, turn.assistant_message_id,
                                     StreamEvent(turn.run_id, 'answer', 'answer'))
        await self.store.append_event(turn.session_id, turn.assistant_message_id,
                                     StreamEvent(turn.run_id, 'complete'))
        return 'answer'


def request(number='run'):
    return OrdinaryTurnRequest('session', 'question', str(number),
                               knowledge_base_scope=('kb',))


async def settled():
    # Let task callbacks and gather bookkeeping release their temporary references.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    gc.collect()


def test_consecutive_completed_tasks_and_watchers_are_collectible():
    async def scenario():
        harness = Harness()
        service = harness.service
        refs = []
        for number in range(12):
            turn = await service.accept_turn(request(number), 'owner')
            refs.append(weakref.ref(service._tasks[service._key(turn)]))
            await service.drain_background()
        await settled()
        assert not service._tasks
        assert not service._active
        assert harness.watches_closed == 12
        assert all(ref() is None for ref in refs + harness.watch_refs)
        assert len(harness.repository.commits) == 12
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', [None, ProviderError('MODEL_EGRESS_DENIED'), GenerationReplay()])
def test_active_claim_and_watcher_released_on_each_terminal_path(outcome):
    async def scenario():
        harness = Harness(outcome=outcome)
        await harness.service.accept_turn(request(), 'owner')
        await harness.service.drain_background()
        await settled()
        assert not harness.service._tasks and not harness.service._active
        assert harness.watches_closed == 1
        assert all(ref() is None for ref in harness.watch_refs)
        before = harness.executions
        replay = await harness.service.accept_turn(request(), 'owner')
        assert replay.replay_only
        assert await harness.service.run_turn(replay) is None
        assert harness.executions == before == 1
    asyncio.run(scenario())


def test_cancellation_releases_task_and_claim_and_preserves_cancelled_terminal():
    async def scenario():
        harness = Harness(blocked=True)
        turn = await harness.service.accept_turn(request(), 'owner')
        await harness.started.wait()
        await asyncio.sleep(0)
        harness.service._tasks[harness.service._key(turn)].cancel()
        await harness.service.drain_background()
        await settled()
        assert not harness.service._tasks and not harness.service._active
        assert harness.watches_closed == 1 and harness.executions == 0
        assert harness.repository.failures == ['MODEL_CALL_CANCELLED']
        events, _ = await harness.store.get_events(turn.session_id, turn.assistant_message_id, 0)
        assert events[-1].type == 'complete' and events[-1].data['status'] == 'cancelled'
    asyncio.run(scenario())


def test_same_request_competition_and_replay_after_collection_never_resend():
    async def scenario():
        harness = Harness(blocked=True)
        service = harness.service
        turns = await asyncio.gather(*(service.accept_turn(request(), 'owner') for _ in range(20)))
        assert sum(turn.is_new for turn in turns) == 1
        assert len(service._tasks) == 1
        harness.release.set()
        await service.drain_background()
        await settled()
        assert not service._tasks and not service._active
        replay = await service.accept_turn(request(), 'owner')
        events = [event async for event in service.replay(replay, 'owner')]
        assert [event.type for event in events] == ['agent_query', 'answer', 'complete']
        assert harness.executions == 1 and harness.repository.commits == ['run']
        replacement = Harness()
        replacement.service.repository = harness.repository
        replacement.service.store = harness.store
        restarted = await replacement.service.accept_turn(request(), 'owner')
        assert restarted.replay_only and not replacement.service._tasks
        assert [event async for event in replacement.service.replay(restarted, 'owner')] == events
        with pytest.raises(ProviderError, match='TURN_PAYLOAD_CONFLICT'):
            await service.accept_turn(replace(request(), content='changed'), 'owner')
    asyncio.run(scenario())


def test_stale_completion_callback_does_not_remove_newer_task():
    async def scenario():
        harness = Harness(blocked=True)
        service = harness.service
        turn = await service.accept_turn(request(), 'owner')
        key = service._key(turn)
        old_task = service._tasks[key]
        await harness.started.wait()
        newer_task = asyncio.create_task(asyncio.Event().wait())
        service._tasks[key] = newer_task
        try:
            harness.release.set()
            await old_task
            await settled()
            assert service._tasks[key] is newer_task
            assert not service._active
        finally:
            newer_task.cancel()
            await asyncio.gather(newer_task, return_exceptions=True)
            service._tasks.pop(key, None)
    asyncio.run(scenario())


def test_stale_run_finalizer_does_not_remove_newer_active_owner():
    async def scenario():
        harness = Harness(blocked=True)
        service = harness.service
        turn = await service.accept_turn(request(), 'owner')
        await harness.started.wait()
        key = service._key(turn)
        replacement_owner = asyncio.current_task()
        service._active[key] = replacement_owner
        harness.release.set()
        await service.drain_background()
        assert service._active[key] is replacement_owner
        assert harness.watches_closed == 1
        service._active.pop(key)
    asyncio.run(scenario())


def test_background_errors_are_bounded_safe_codes_without_task_retention():
    async def scenario():
        harness = Harness(outcome=ProviderError('MODEL_EGRESS_DENIED'))
        harness.repository.fail_commit = True
        service = harness.service
        refs = []
        for number in range(140):
            turn = await service.accept_turn(request(number), 'owner')
            refs.append(weakref.ref(service._tasks[service._key(turn)]))
            await service.drain_background()
        await settled()
        assert not service._tasks and not service._active
        assert len(service.background_errors) <= 128
        assert ('owner', 'session', '139') in service.background_errors
        assert ('owner', 'session', '0') not in service.background_errors
        assert set(service.background_errors.values()) == {'ORDINARY_CHAIN_FAILED'}
        assert 'private' not in str(service.background_errors)
        assert all(ref() is None for ref in refs + harness.watch_refs)
    asyncio.run(scenario())


def test_unknown_attempt_remains_unknown_and_replay_never_resends_after_cleanup():
    # Use the established coordinator's offline provider/lifecycle fixtures.
    from test_generation import event
    from test_service import REQ, runtime

    async def scenario():
        service, repository, _, coordinator, cheap, expensive, store, commit, _ = runtime()
        cheap.outputs = [event('private draft'), ProviderError('PROVIDER_HTTP_ERROR', sent=True)]
        turn = await service.accept_turn(REQ, 'owner')
        task_ref = weakref.ref(service._tasks[service._key(turn)])
        await service.drain_background()
        # The provider fixture retains its raised exception/traceback in outputs.
        # Drop that test-owned reference before checking service task collection.
        cheap.outputs.clear()
        await settled()
        assert task_ref() is None and not service._active and not service._tasks
        unknown = coordinator.lifecycle.finished[0][0]
        assert unknown.cost_status == 'UNKNOWN'
        repeated = await service.accept_turn(REQ, 'owner')
        assert repeated.replay_only and await service.run_turn(repeated) is None
        events = [item async for item in service.replay(repeated, 'owner')]
        assert events == store.events and events[-1].type == 'complete'
        assert len(cheap.calls) == 1 and not expensive.calls and not commit.calls
        assert coordinator.lifecycle.finished[0][0] is unknown
        assert unknown.cost_status == 'UNKNOWN'
    asyncio.run(scenario())
