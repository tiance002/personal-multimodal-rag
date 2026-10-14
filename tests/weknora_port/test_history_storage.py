"""Scripted SQL boundary checks, not actual PostgreSQL integration."""
import asyncio
import pytest

from backend.weknora_port.models.types import TrustedScope
from backend.weknora_port.storage.history import LocalPrincipal, PostgresHistoryRepository
from backend.weknora_port.storage.postgres import IsolationPolicy, PostgresRetriever
from backend.weknora_port.streaming.replay import FinalCommit
from tests.weknora_port.test_storage import FakeConnection, FakeCursor


class HistoryConnection(FakeConnection):
    def __init__(self, message=None, owner=True):
        super().__init__()
        self.owner, self.message = owner, message

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        if 'FROM sessions' in query:
            return FakeCursor([{'id': 'session'}] if self.owner else [])
        if query.startswith('SELECT content,knowledge_references'):
            return FakeCursor([self.message] if self.message is not None else [])
        return FakeCursor([])


def adapter(connection, verified=True):
    guard = PostgresRetriever(connection, IsolationPolicy('new', frozenset({'old'})))
    guard._verified = verified
    return PostgresHistoryRepository(guard, LocalPrincipal(10000, 'local'))


def test_identity_owner_fail_before_message_write():
    async def run():
        conn = HistoryConnection(owner=False)
        store = adapter(conn, verified=False)
        with pytest.raises(PermissionError):
            await store.create_turn('session', 'run', 'q', 'a', 'question', TrustedScope('local', ('kb',)))
        assert conn.calls == []
        store.repository._verified = True
        with pytest.raises(LookupError):
            await store.commit_final(FinalCommit('session', 'a', 'run', 'answer', []))
        assert not any(query.startswith('UPDATE') for query, _ in conn.calls)
        with pytest.raises(PermissionError):
            await store.validate_session('session', LocalPrincipal(10000, 'other'))
    asyncio.run(run())


def test_final_answer_references_and_usage_are_one_transaction_and_statement():
    async def run():
        conn = HistoryConnection({'is_completed': False})
        store = adapter(conn)
        await store.commit_final(FinalCommit('session', 'a', 'run', 'answer', [{'chunk_id':'real'}], {'total':4}))
        updates = [(sql,args) for sql,args in conn.calls if sql.startswith('UPDATE')]
        assert len(updates) == 1
        sql,args = updates[0]
        assert 'knowledge_references=' in sql and 'usage=' in sql and 'is_completed=true' in sql
        assert args[-2:] == (10000, 'local')
        assert any('FOR UPDATE' in sql and 'request_id=%s' in sql for sql,_ in conn.calls)
    asyncio.run(run())


def test_completed_answer_cannot_be_overwritten_and_same_commit_is_idempotent():
    async def run():
        conn = HistoryConnection({'is_completed':True, 'content':'answer',
                                  'knowledge_references':[{'chunk_id':'real'}], 'usage':None})
        store = adapter(conn)
        await store.commit_final(FinalCommit('session','a','run','answer',({'chunk_id':'real'},)))
        with pytest.raises(ValueError, match='FINAL_COMMIT_CONFLICT'):
            await store.commit_final(FinalCommit('session','a','run','changed',[]))
        assert not any(sql.startswith('UPDATE') for sql,_ in conn.calls)
        assert conn.rollback
    asyncio.run(run())


def test_question_and_empty_assistant_created_together_with_scope_snapshot():
    async def run():
        conn = HistoryConnection()
        result = await adapter(conn).create_turn('session','run','q','a','question',TrustedScope('local',('kb',),('doc',)))
        assert result.message_id == 'a'
        inserts = [args for sql,args in conn.calls if sql.startswith('INSERT INTO messages')]
        assert len(inserts) == 2
        assert inserts[0][5:7] == ('user', 'question')
        assert inserts[1][5:7] == ('assistant', '')
        assert inserts[0][-2:] == ('["kb"]','["doc"]')
    asyncio.run(run())
