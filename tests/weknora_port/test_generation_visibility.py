"""Offline output status projection; no model, database or network calls."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.weknora_port.runtime import ApplicationRuntime
from tests.weknora_port.test_generation import CTX, event, request, setup


@pytest.mark.parametrize(('text', 'finish', 'issues'), [
    ('unfinished answer', 'length', ['TRUNCATED']),
    ('', 'stop', ['EMPTY']),
    ('complete answer', 'stop', ['MISSING_CITATIONS']),
    ('EOF source semantics retained', '', ['MISSING_CITATIONS']),
])
def test_complete_exposes_only_final_attempt_issues_without_changing_persistence(text, finish, issues):
    co, cheap, expensive, store, commit, life = setup(
        [event('discarded cheap draft', done=True, finish='length', final=True)],
        [event(text, done=True, finish=finish, final=True)])
    result = asyncio.run(co.run(request(), CTX))
    complete = store.events[-1]
    assert complete.type == 'complete'
    assert complete.data['generation_issues'] == issues
    assert complete.data['finish_reason'] == finish
    assert complete.data['truncated'] == ('TRUNCATED' in issues)
    assert complete.data['role'] == 'expensive'
    assert result.answer == commit.calls[0].answer == text
    assert len(cheap.calls) == len(expensive.calls) == len(commit.calls) == 1
    assert all('discarded cheap draft' not in e.content for e in store.events)
    assert all(record.cost_status == 'UNKNOWN' for record, _ in life.finished)


def test_history_projection_uses_exact_completed_run_and_selected_attempt_identity():
    calls = []
    principal = SimpleNamespace(tenant_id=332, subject_id='subject')
    rows = [{'id': 'assistant', 'generation_issues': ['TRUNCATED'], 'finish_reason': 'length'}]

    class Connection:
        async def execute(self, sql, params):
            calls.append((sql, params))
            class Cursor:
                async def fetchall(self):
                    return rows
            return Cursor()

    @asynccontextmanager
    async def lease():
        yield SimpleNamespace(connection=Connection())

    async def session(guard, session_id, who):
        assert session_id == 'session' and who is principal

    runtime = ApplicationRuntime.__new__(ApplicationRuntime)
    runtime._owner = lambda who: None
    runtime.leases = SimpleNamespace(lease=lease)
    runtime.turns = SimpleNamespace(_session=session)
    assert asyncio.run(runtime.list_messages(principal, 'session')) == rows
    sql, params = calls[0]
    assert params == (332, 'subject', 'session')
    for predicate in (
        't.tenant_id=m.tenant_id', 't.subject_id=m.subject_id', 't.session_id=m.session_id',
        't.run_id=m.request_id', 't.assistant_message_id=m.id', "t.status='completed'",
        'r.run_id=t.run_id', 'r.tenant_id=t.tenant_id', 'r.subject_id=t.subject_id',
        'r.session_id=t.session_id', 'r.message_id=t.assistant_message_id',
        'a.run_id=r.run_id', 'a.tenant_id=r.tenant_id', 'a.subject_id=r.subject_id',
        "m.role='assistant'", 'm.is_completed=true', "m.turn_status NOT IN ('error','cancelled')",
        "a.status IN ('COMPLETED','TRUNCATED')", 'ORDER BY a.ordinal DESC LIMIT 1',
        'm.tenant_id=%s AND m.subject_id=%s AND m.session_id=%s',
    ):
        assert predicate in sql
    assert sql.lstrip().startswith('SELECT')
