"""S8.6 opt-in QU v2 and saved-session contract; all models/SQL are mocks."""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import fields
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.weknora_port.conversation_configuration import resolve_saved_conversation
from backend.weknora_port.http_api import SessionCreate
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.query import understand_query
from backend.weknora_port.runtime import ApplicationRuntime, ConversationSettings
from test_pipeline import CTX, Model, Registry, state

MODE = 'query_structure_v1'
Q0 = 'Compare PostgreSQL and Redis in terms of persistence and concurrency.'


def current(**kw):
    result = state(query=Q0, enable_rewrite=True, chat_model_id='cheap', **kw)
    # Dynamic assignment also makes the pre-implementation RED exercise QU,
    # rather than stopping at a dataclass constructor's unexpected keyword.
    result.retrieval_strategy_mode = MODE
    result.source_evidence_mode = 'bounded_source_v1'
    return result


def run_qu(result, output, finish='stop'):
    class Stub(Model):
        async def chat(self, messages, options, context):
            self.calls.append((messages, options, context))
            return SimpleNamespace(content=output, finish_reason=finish)
    model = Stub([])
    registry = Registry(model)
    asyncio.run(understand_query(result, CTX, registry))
    return model, registry


@pytest.mark.parametrize('prompt', [None, 'Custom legacy task: output exactly three fields. {"rewrite_query":"x","intent":"kb_search","image_description":""}'])
def test_v2_one_non_thinking_call_has_no_legacy_three_field_contract(prompt):
    result = current()
    if prompt is not None:
        result.rewrite_system_prompt = prompt
        result.rewrite_user_prompt = 'Legacy template demands three fields. {{query}}'
    model, registry = run_qu(result, json.dumps({'rewrite_query': Q0, 'intent': 'kb_search', 'image_description': '', 'query_type': 'SIMPLE_COMPARE'}))
    assert len(model.calls) == 1 and registry.lookups == ['cheap']
    messages, options, context = model.calls[0]
    assert options.thinking is False and options.max_completion_tokens == 256
    assert context.request_id == CTX.request_id + '/query_rewrite'
    system = messages[0].content
    assert 'exactly four fields' in system and 'query_type' in system
    assert 'three fields' not in system and 'THREE tasks' not in system
    assert all('query_type' in json.loads(value) for value in __import__('re').findall(r'\{[^{}]*\}', system))
    assert 'latest independent authorized USER question' in system
    assert result.query == result.rewrite_query == Q0
    assert result.query_type == 'SIMPLE_COMPARE'


def test_off_preserves_configured_legacy_prompt_and_output_budget():
    result = state(query=Q0, enable_rewrite=True, chat_model_id='cheap', rewrite_system_prompt='custom system', rewrite_user_prompt='custom {{query}}')
    model, _ = run_qu(result, json.dumps({'rewrite_query': Q0, 'intent': 'kb_search', 'image_description': '', 'query_type': 'DEPENDENT'}))
    messages, options, _ = model.calls[0]
    assert messages[0].content.startswith('custom system')
    assert 'exactly three fields' in messages[0].content
    assert 'exactly four fields' not in messages[0].content
    assert options.max_completion_tokens == 150 and options.thinking is False
    assert getattr(result, 'query_type', 'UNKNOWN') == 'UNKNOWN'


@pytest.mark.parametrize('kind', ['DIRECT', 'SIMPLE_COMPARE', 'JOINT', 'PARALLEL', 'DEPENDENT', 'MIXED', 'UNKNOWN'])
def test_v2_closed_type_values_accepted_only_with_valid_rewrite(kind):
    result = current()
    run_qu(result, json.dumps({'rewrite_query': Q0, 'intent': 'kb_search', 'image_description': '', 'query_type': kind}))
    assert result.rewrite_query == Q0 and not result.rewrite_fallback_reason
    assert result.query_type == kind


@pytest.mark.parametrize('kind', [None, 3, {}, 'dependent', 'AGENT', ' DIRECT '])
def test_invalid_type_keeps_valid_rewrite_and_becomes_unknown(kind):
    result = current()
    run_qu(result, json.dumps({'rewrite_query': Q0, 'intent': 'kb_search', 'image_description': '', 'query_type': kind}))
    assert result.rewrite_query == Q0 and not result.rewrite_fallback_reason
    assert result.query_type == 'UNKNOWN'


def test_valid_legacy_three_field_response_keeps_rewrite_but_type_unknown():
    result = current()
    run_qu(result, json.dumps({'rewrite_query': Q0, 'intent': 'kb_search', 'image_description': ''}))
    assert result.rewrite_query == Q0 and not result.rewrite_fallback_reason
    assert result.query_type == 'UNKNOWN'


@pytest.mark.parametrize('output,finish', [
    ('not JSON', 'stop'), ('{}', 'stop'),
    (json.dumps({'rewrite_query': Q0[:-1], 'query_type': 'SIMPLE_COMPARE'}), 'stop'),
    (json.dumps({'rewrite_query': Q0, 'query_type': 'SIMPLE_COMPARE'}), 'length'),
    ('preface ' + json.dumps({'rewrite_query': Q0, 'query_type': 'SIMPLE_COMPARE'}), 'stop'),
])
def test_invalid_truncated_or_changed_rewrite_falls_back_with_unknown_type(output, finish):
    result = current()
    model, _ = run_qu(result, output, finish)
    assert len(model.calls) == 1
    assert result.query == result.rewrite_query == Q0 and result.rewrite_fallback_reason
    assert result.query_type == 'UNKNOWN'


def test_literal_history_substitution_preserves_original_question_numbers_and_negation():
    q0 = 'What did she direct from 2007–2010? Do not include other years.'
    result = current()
    result.query = q0
    result.history = [SimpleNamespace(query='Who is Jeanne Marie Beaumont?', answer='Prior generated prose is not source evidence.')]
    rewritten = q0.replace('she', 'Jeanne Marie Beaumont')
    run_qu(result, json.dumps({'rewrite_query': rewritten, 'intent': 'follow_up', 'query_type': 'DEPENDENT'}))
    assert result.query == q0 and result.rewrite_query == rewritten
    assert result.query_type == 'DEPENDENT' and result.intent == 'kb_search'
    result = current(); result.query = q0
    result.history = [SimpleNamespace(query='Who is Jeanne Marie Beaumont?', answer='irrelevant')]
    run_qu(result, json.dumps({'rewrite_query': rewritten.replace('2010', '2011'), 'query_type': 'DEPENDENT'}))
    assert result.rewrite_query == q0 and result.query_type == 'UNKNOWN'


def test_v2_type_cannot_bypass_comparison_ambiguity_or_transform_protection():
    history = [SimpleNamespace(query='Compare a Leyden jar and Franklin bells.', answer='Both are electrical devices.')]
    result = current(); result.query = 'What is it used for?'; result.history = list(history)
    run_qu(result, json.dumps({'rewrite_query': 'What are Franklin bells used for?', 'intent': 'follow_up', 'query_type': 'DIRECT'}))
    assert result.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'
    assert result.rewrite_query == result.query and result.query_type == 'UNKNOWN' and result.intent == 'kb_search'
    history.append(SimpleNamespace(query='What is it used for?', answer='Unsupported old guess about bells.'))
    result = current(); result.query = 'Format the previous answer as a table.'; result.history = history
    run_qu(result, json.dumps({'rewrite_query': result.query, 'intent': 'follow_up', 'query_type': 'DIRECT'}))
    from backend.weknora_port.retrieval.query import requires_ambiguity_clarification
    assert requires_ambiguity_clarification(result)


@pytest.mark.parametrize('unsupported', [{'images': ('image',)}, {'attachment_prompt': 'document body'}, {'quoted_context': 'quoted attachment'}, {'web_search_enabled': True}, {'graph_enabled': True}])
def test_unsupported_combinations_keep_legacy_qu(unsupported):
    result = current(**unsupported)
    model, _ = run_qu(result, json.dumps({'rewrite_query': Q0, 'intent': 'kb_search', 'query_type': 'SIMPLE_COMPARE'}))
    messages, options, _ = model.calls[0]
    assert 'exactly three fields' in messages[0].content
    assert 'exactly four fields' not in messages[0].content
    assert options.max_completion_tokens == 150
    assert result.query_type == 'UNKNOWN'


@pytest.mark.parametrize('mode,source,error', [('bad', 'bounded_source_v1', 'RETRIEVAL_STRATEGY_MODE_INVALID'), (MODE, 'off', 'RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE')])
def test_standalone_qu_rejects_invalid_config_before_registry_lookup(mode, source, error):
    result = current(); result.retrieval_strategy_mode = mode; result.source_evidence_mode = source
    registry = Registry(Model([]))
    with pytest.raises(ValueError, match=error):
        asyncio.run(understand_query(result, CTX, registry))
    assert registry.lookups == []


def test_strategy_settings_keyword_only_and_safe_saved_overlay():
    assert next(value for value in fields(ConversationSettings) if value.name == 'retrieval_strategy_mode').kw_only
    assert ConversationSettings().retrieval_strategy_mode == 'off'
    source = ConversationSettings()
    assert resolve_saved_conversation(source, {})['retrieval_strategy_mode'] == 'off'
    saved = {'context_config': {'conversation': {'retrieval_strategy_mode': MODE, 'source_evidence_mode': 'bounded_source_v1', 'budget': 99999, 'scope': ['other'], 'query_type': 'DIRECT'}}}
    effective = resolve_saved_conversation(source, saved)
    assert effective['retrieval_strategy_mode'] == MODE
    assert not {'scope', 'budget', 'query_type'} & set(effective)
    assert source.retrieval_strategy_mode == 'off'
    with pytest.raises(ValueError, match='RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE'):
        ConversationSettings(retrieval_strategy_mode=MODE)
    with pytest.raises(ValueError, match='RETRIEVAL_STRATEGY_MODE_INVALID'):
        resolve_saved_conversation(source, {'context_config': {'conversation': {'retrieval_strategy_mode': 'bad'}}})
    with pytest.raises(ValueError, match='RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE'):
        resolve_saved_conversation(source, {'context_config': {'conversation': {'retrieval_strategy_mode': MODE}}})


@pytest.mark.parametrize('extra', [{'retrieval_strategy_mode': 'bad'}, {'retrieval_strategy_mode': True}, {'retrieval_strategy_mode': 1}, {'retrieval_strategy_mode': MODE, 'budget': 24000}, {'retrieval_strategy_mode': MODE, 'context_config': {}}])
def test_public_session_schema_rejects_unapproved_fields(extra):
    with pytest.raises(ValidationError):
        SessionCreate(knowledge_base_scope=['kb'], **extra)


def assembled_runtime(monkeypatch, startup=None):
    import re
    import backend.weknora_port.runtime as module
    from backend.weknora_port.generation import RouterPolicy
    stored, calls = {}, []
    class NoSendRegistry:
        def __init__(self, *args, **kwargs): pass
        async def chat(self, *args):
            calls.append('registry.chat')
            return SimpleNamespace(config=SimpleNamespace(supports_vision=False))
    monkeypatch.setattr(module, 'NeutralModelRegistry', NoSendRegistry)
    @asynccontextmanager
    async def transaction(): yield
    async def execute(sql, params):
        calls.append('sql.insert')
        names = re.search(r'INSERT INTO sessions\((.*?)\) VALUES', sql).group(1).split(',')
        stored.update(zip(names, params))
        for name in ('context_config', 'summary_parameters', 'knowledge_base_scope', 'document_scope'):
            stored[name] = json.loads(stored[name])
    @asynccontextmanager
    async def lease():
        calls.append('lease')
        yield SimpleNamespace(connection=SimpleNamespace(transaction=transaction, execute=execute))
    async def resources(*args): calls.append('resources')
    async def session(*args): return stored
    async def snapshot(*args):
        return SimpleNamespace(model_id=lambda role: 'cheap-id', model_ids=(('cheap', 'cheap-id'), ('rerank', 'rerank-id')))
    async def rows(*args): return [{'id': 'kb', 'summary_model_id': 'cheap-id'}]
    async def load(*args): return ({'id': 'cheap-id', 'type': 'KnowledgeQA', 'source': 'remote'}, None, None)
    runtime = object.__new__(ApplicationRuntime)
    runtime.principal = SimpleNamespace(tenant_id=332, subject_id='synthetic-subject')
    runtime.leases = SimpleNamespace(lease=lease); runtime.turns = SimpleNamespace(_resources=resources, _session=session)
    runtime._snapshot = snapshot; runtime._rows = rows; runtime._runs = {}
    runtime.models = SimpleNamespace(_load=load); runtime.catalogue = None
    runtime.conversation = startup or ConversationSettings()
    runtime.policy = RouterPolicy(mode='DYNAMIC'); runtime.graph_enabled = False
    return runtime, stored, calls


@pytest.mark.parametrize('setting,source', [(None, None), ('off', None), (MODE, 'bounded_source_v1')])
def test_public_session_to_saved_conversation_to_actual_pipeline_state(monkeypatch, setting, source):
    runtime, stored, calls = assembled_runtime(monkeypatch)
    body = {'knowledge_base_scope': ['kb', 'kb'], 'document_scope': ['doc', 'doc']}
    if setting is not None: body['retrieval_strategy_mode'] = setting
    if source is not None: body['source_evidence_mode'] = source
    result = asyncio.run(runtime.create_session(runtime.principal, SessionCreate(**body)))
    expected = {key: value for key, value in [('retrieval_strategy_mode', setting), ('source_evidence_mode', source)] if value is not None}
    assert stored['context_config'] == ({'conversation': expected} if expected else {})
    turn = SimpleNamespace(session_id=result['id'], run_id='run',
        context=CallContext('run', TrustedScope('synthetic-subject', ('kb',), ('doc',))),
        request=SimpleNamespace(summary_model_id='', reasoning_effort='off', accept_language='zh-CN', knowledge_base_scope=('kb',)))
    cfg = asyncio.run(runtime._configuration(turn))
    assert cfg.pipeline_state.retrieval_strategy_mode == (setting or 'off')
    assert cfg.pipeline_state.source_evidence_mode == (source or 'off')
    assert cfg.pipeline_state.bridge_cheap_model_id == ('cheap-id' if setting == MODE else '')
    assert cfg.pipeline_state.bridge_rescue_mode == 'off'
    assert cfg.pipeline_state.targets[0].knowledge_base_id == 'kb'


def test_candidate_public_session_requires_explicit_bounded_profile_before_lease(monkeypatch):
    runtime, stored, calls = assembled_runtime(monkeypatch, ConversationSettings(source_evidence_mode='bounded_source_v1'))
    request = SessionCreate(knowledge_base_scope=['kb'], retrieval_strategy_mode=MODE)
    with pytest.raises(ValueError, match='RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE'):
        asyncio.run(runtime.create_session(runtime.principal, request))
    assert not calls and not stored



@pytest.mark.parametrize('extra,status,code', [
    ({'retrieval_strategy_mode': MODE, 'source_evidence_mode': 'bounded_source_v1'}, 200, None),
    ({'retrieval_strategy_mode': MODE}, 409, 'RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE'),
    ({'retrieval_strategy_mode': 'bad', 'source_evidence_mode': 'bounded_source_v1'}, 422, 'REQUEST_INVALID'),
])
def test_actual_http_candidate_configuration_errors_precede_lease_and_send(monkeypatch, extra, status, code):
    import httpx
    from backend.weknora_port.http_api import create_app
    runtime, stored, calls = assembled_runtime(monkeypatch)
    app = create_app(runtime, resolve_principal=lambda request: runtime.principal, manage_background_workers=False)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Origin': 'http://test'}) as client:
            return await client.post('/api/v1/sessions', json={'knowledge_base_scope': ['kb'], **extra})
    result = asyncio.run(exercise())
    assert result.status_code == status
    if code:
        assert result.json()['error']['code'] == code
        assert calls == [] and stored == {}
    else:
        assert stored['context_config']['conversation']['retrieval_strategy_mode'] == MODE
        assert 'registry.chat' not in calls
