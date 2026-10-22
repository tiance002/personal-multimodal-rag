"""Session selection and clarification contracts with scripted SQL/ASGI only."""
import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from backend.weknora_port.http_api import create_app
from backend.weknora_port.models.types import ProviderError
from backend.weknora_port.runtime import ApplicationRuntime
from backend.weknora_port.service import OrdinaryTurnRequest, TurnConfiguration
from backend.weknora_port.storage.history import LocalPrincipal
from backend.weknora_port.storage.turns import PostgresTurnRepository
from test_pipeline import state
from test_service import REQ, runtime as ordinary_runtime
from test_turn_storage import Cursor, DB, Leases, PRINCIPAL


class ScopeDB(DB):
    def __init__(self):
        super().__init__()
        self.session = dict(id='session', title='Scope', knowledge_base_scope=['kb'], document_scope=[])
        self.resources = {'doc-a': 'kb', 'doc-b': 'kb', 'foreign': 'other-kb'}
        self.turn = None
        self.catalog = [dict(id='doc-a', knowledge_base_id='kb', title='HJ 1134—2020', file_name='a.pdf'),
                        dict(id='doc-b', knowledge_base_id='kb', title='HJ 348—2007', file_name='b.pdf')]

    async def execute(self, sql, params=()):
        if sql.startswith('SELECT * FROM sessions'):
            self.calls.append((sql, tuple(params)))
            return Cursor([] if self.deleted or params[0] != self.session['id'] else [deepcopy(self.session)])
        if sql.startswith('SELECT id FROM knowledges'):
            self.calls.append((sql, tuple(params)))
            return Cursor([{'id': item} for item in params[1] if self.resources.get(item) in params[2]])
        if sql.startswith('SELECT id,knowledge_base_id,title,file_name'):
            self.calls.append((sql, tuple(params)))
            docs = params[3]
            return Cursor([row for row in self.catalog if row['knowledge_base_id'] in params[1]
                           and (params[2] or row['id'] in docs)])
        if sql.startswith('UPDATE sessions SET document_scope'):
            self.calls.append((sql, tuple(params)))
            self.session['document_scope'] = json.loads(params[0])
            return Cursor([])
        if sql.startswith('SELECT * FROM port_turns'):
            self.calls.append((sql, tuple(params)))
            return Cursor([deepcopy(self.turn)] if self.turn else [])
        if sql.startswith('SELECT id,role FROM messages'):
            return Cursor([{'id': self.turn['user_message_id'], 'role': 'user'},
                           {'id': self.turn['assistant_message_id'], 'role': 'assistant'}])
        if sql.startswith('INSERT INTO port_turns'):
            names = ('tenant_id', 'subject_id', 'session_id', 'client_request_id', 'run_id',
                     'user_message_id', 'assistant_message_id', 'q0', 'payload_sha256',
                     'knowledge_base_scope', 'document_scope', 'request_configuration')
            self.turn = dict(zip(names, params))
            for name in names[-3:]: self.turn[name] = json.loads(self.turn[name])
        return await super().execute(sql, params)


def application():
    db = ScopeDB()
    app = ApplicationRuntime.__new__(ApplicationRuntime)
    app.principal = PRINCIPAL
    app.leases = Leases(db)
    app.turns = PostgresTurnRepository(app.leases, PRINCIPAL)
    return app, db


def test_patch_select_switch_clear_and_strict_current_resource_authorization():
    async def run():
        runtime, db = application()
        app = create_app(runtime, resolve_principal=lambda request: PRINCIPAL, manage_background_workers=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Origin': 'http://test'}) as client:
            for docs in (['doc-a'], ['doc-b'], []):
                response = await client.patch('/api/v1/sessions/session/document-scope', json={'document_scope': docs})
                assert response.status_code == 200, response.text
                assert response.json()['data']['document_scope'] == docs == db.session['document_scope']
                assert response.json()['data']['knowledge_base_scope'] == ['kb']
            before = deepcopy(db.session)
            foreign = await client.patch('/api/v1/sessions/session/document-scope', json={'document_scope': ['foreign']})
            assert foreign.status_code == 403 and db.session == before
            forged = await client.patch('/api/v1/sessions/session/document-scope',
                                       json={'document_scope': ['doc-a'], 'knowledge_base_scope': ['other-kb']})
            assert forged.status_code == 422
            wrong = await client.patch('/api/v1/sessions/foreign-session/document-scope', json={'document_scope': []})
            assert wrong.status_code != 200
            no_origin = await client.patch('/api/v1/sessions/session/document-scope', json={'document_scope': []},
                                          headers={'Origin': 'http://untrusted'})
            assert no_origin.status_code == 403
        assert all('tenant_id=%s AND subject_id=%s' in sql for sql, _ in db.calls if sql.startswith('UPDATE sessions'))
    asyncio.run(run())


@pytest.mark.parametrize('original_docs,new_docs', [([], ['doc-a']), (['doc-a'], ['doc-b']), (['doc-a'], [])])
@pytest.mark.parametrize('status', ['pending', 'completed'])
def test_selection_changes_only_future_turns_and_old_post_get_retain_snapshot(original_docs, new_docs, status):
    async def run():
        app, db = application()
        db.session['document_scope'] = original_docs
        request = OrdinaryTurnRequest('session', 'Original question', 'client')
        accepted = await app.turns.accept(request, PRINCIPAL)
        db.turn['status'] = status
        snapshot = deepcopy(db.turn)
        await app.update_session_document_scope(PRINCIPAL, 'session', new_docs)
        resumed = await app.turns.validate_run('session', accepted.assistant_message_id, PRINCIPAL)
        replay = await app.turns.accept(request, PRINCIPAL)
        assert replay.replay_only and resumed.replay_only
        assert resumed.context.scope == accepted.context.scope == replay.context.scope
        assert db.turn == snapshot and list(accepted.context.scope.document_ids) == original_docs
        for changed in (replace(request, content='Changed'), replace(request, accept_language='en-US'),
                        replace(request, document_scope=('foreign',))):
            with pytest.raises(ProviderError, match='PAYLOAD_CONFLICT'):
                await app.turns.accept(changed, PRINCIPAL)
        db.turn = None
        future = await app.turns.accept(replace(request, request_id='next'), PRINCIPAL)
        assert list(future.context.scope.document_ids) == new_docs
    asyncio.run(run())


def test_stop_after_document_switch_keeps_original_accepted_run():
    async def run():
        app, db = application()
        db.session['document_scope'] = ['doc-a']
        accepted = await app.turns.accept(OrdinaryTurnRequest('session', 'q', 'client'), PRINCIPAL)
        await app.update_session_document_scope(PRINCIPAL, 'session', ['doc-b'])
        service, _, store, *_ = ordinary_runtime()
        service.repository = app.turns
        result = await service.request_stop('session', accepted.assistant_message_id, PRINCIPAL)
        assert result['success'] is True
        assert db.turn['document_scope'] == ['doc-a']
    asyncio.run(run())


def test_foreign_principal_deleted_resources_and_kb_revocation_still_deny_old_runs():
    async def run():
        app, db = application()
        db.session['document_scope'] = ['doc-a']
        accepted = await app.turns.accept(OrdinaryTurnRequest('session', 'q', 'client'), PRINCIPAL)
        for stranger in (LocalPrincipal(PRINCIPAL.tenant_id, 'stranger'), LocalPrincipal(999, PRINCIPAL.subject_id)):
            before = len(db.calls)
            with pytest.raises(ProviderError, match='OWNER_MISMATCH'):
                await app.turns.update_document_scope('session', ['doc-b'], stranger)
            with pytest.raises(ProviderError, match='OWNER_MISMATCH'):
                await app.turns.validate_run('session', accepted.assistant_message_id, stranger)
            assert len(db.calls) == before
        del db.resources['doc-a']
        with pytest.raises(ProviderError, match='DOCUMENT_SCOPE_DENIED'):
            await app.turns.validate_run('session', accepted.assistant_message_id, PRINCIPAL)
        db.resources['doc-a'] = 'kb'
        db.session['knowledge_base_scope'] = []
        with pytest.raises(ProviderError, match='SESSION_SCOPE_CHANGED'):
            await app.turns.validate_run('session', accepted.assistant_message_id, PRINCIPAL)
    asyncio.run(run())


@pytest.mark.parametrize('question', ['这份技术规范的实施日期是什么？', '这个PPT介绍了什么？', '上述报告的结论是什么？',
                                      'What is the effective date of this specification?'])
def test_real_authorized_title_clarification_precedes_history_memory_and_retrieval(question):
    async def run():
        app, db = application()
        service, repo, retrieval, coordinator, cheap, expensive, *_ = ordinary_runtime()
        s = state(language='English' if question.startswith('What') else 'Chinese')
        service.configuration = lambda turn: TurnConfiguration(s)
        service.document_clarifier = app._document_scope_clarification
        async def forbidden(*args, **kwargs): raise AssertionError('Clarification must precede model-bound preparation')
        service.history_service = SimpleNamespace(load=forbidden)
        service.memory_loader = SimpleNamespace(load=forbidden)
        accepted = await repo.accept(replace(REQ, content=question), 'owner')
        prepared = await service.prepare_turn(accepted)
        assert prepared.request.fixed_fallback
        assert 'HJ 1134—2020' in prepared.request.fixed_fallback and 'HJ 348—2007' in prepared.request.fixed_fallback
        assert 'doc-a' in prepared.request.fixed_fallback and 'doc-b' in prepared.request.fixed_fallback
        assert not prepared.request.evidence and not prepared.request.history and not prepared.request.memory_prompt
        result = await coordinator.run(prepared.request, prepared.context)
        assert result.references == () and not cheap.calls and not expensive.calls and not retrieval.calls
        queries = [sql for sql, _ in db.calls if sql.startswith('SELECT id,knowledge_base_id,title,file_name')]
        assert queries and "enable_status='enabled'" in queries[0] and 'deleted_at IS NULL' in queries[0]
    asyncio.run(run())


@pytest.mark.parametrize('question,docs', [('What is a classifier?', ()), ('这些技术规范有哪些差异？', ('doc-a', 'doc-b')),
                                         ('Compare this document and that document.', ('doc-a', 'doc-b')),
                                         ('这份规范的实施日期是什么？', ('doc-a',))])
def test_normal_questions_and_explicit_scopes_are_not_forced_to_choose(question, docs):
    async def run():
        app, db = application()
        turn = SimpleNamespace(session_id='session', request=SimpleNamespace(content=question),
                               context=SimpleNamespace(scope=SimpleNamespace(knowledge_base_ids=('kb',), document_ids=docs)))
        assert await app._document_scope_clarification(turn, 'Chinese') is None
    asyncio.run(run())


def test_clarification_catalog_never_uses_other_kbs_or_expands_explicit_document_set():
    async def run():
        app, db = application()
        db.catalog.append(dict(id='foreign', knowledge_base_id='other-kb', title='SECRET', file_name='secret.pdf'))
        turn = SimpleNamespace(session_id='session', request=SimpleNamespace(content='这份文档写了什么？'),
                               context=SimpleNamespace(scope=SimpleNamespace(knowledge_base_ids=('kb',), document_ids=())))
        answer = await app._document_scope_clarification(turn, 'Chinese')
        assert 'SECRET' not in answer and 'HJ 1134—2020' in answer
        turn.context.scope.document_ids = ('doc-a', 'doc-b')
        assert 'SECRET' not in await app._document_scope_clarification(turn, 'Chinese')
    asyncio.run(run())


def test_runtime_search_targets_keep_the_accepted_selection_after_session_changes(monkeypatch):
    from backend.weknora_port.http_api import SessionCreate
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from test_s86_qu_configuration import assembled_runtime
    app, saved, _ = assembled_runtime(monkeypatch)
    asyncio.run(app.create_session(app.principal,SessionCreate(knowledge_base_scope=['kb'],document_scope=['doc-a'])))
    turn=SimpleNamespace(session_id=saved['id'],run_id='accepted-run',
        context=CallContext('accepted-run',TrustedScope('synthetic-subject',('kb',),('doc-a',))),
        request=SimpleNamespace(summary_model_id='',reasoning_effort='off',accept_language='zh-CN',knowledge_base_scope=('kb',)))
    saved['document_scope']=['doc-b']
    config=asyncio.run(app._configuration(turn))
    assert config.pipeline_state.targets[0].target_type=='knowledge'
    assert config.pipeline_state.targets[0].knowledge_ids==('doc-a',)


@pytest.mark.parametrize('literal', ['HJ 1134—2020', 'A.PDF'])
def test_unique_exact_real_title_or_filename_exempts_clarification_without_saving_selection(literal):
    async def run():
        app, db=application()
        turn=SimpleNamespace(session_id='session',request=SimpleNamespace(content=f'这份规范 {literal} 的实施日期？'),
            context=SimpleNamespace(scope=SimpleNamespace(knowledge_base_ids=('kb',),document_ids=())))
        assert await app._document_scope_clarification(turn,'Chinese') is None
        assert db.session['document_scope']==[]
        assert not any(sql.startswith('UPDATE') for sql,_ in db.calls)
    asyncio.run(run())


@pytest.mark.parametrize('query', ['这份规范 HJ 1134—2020 和 HJ 348—2007 的实施日期？',
                                  '这份规范 HJ 1134-2020 的实施日期？'])
def test_two_literal_matches_or_nonliteral_name_still_require_selection(query):
    async def run():
        app, _=application()
        turn=SimpleNamespace(session_id='session',request=SimpleNamespace(content=query),
            context=SimpleNamespace(scope=SimpleNamespace(knowledge_base_ids=('kb',),document_ids=())))
        assert await app._document_scope_clarification(turn,'Chinese') is not None
    asyncio.run(run())
