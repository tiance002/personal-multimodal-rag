"""Bounded conversation diagnostic contracts; no sockets, PostgreSQL or models."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import s8_5_conversation_ecs as runner
from backend.weknora_port.models import provider_config
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState

RAW = (Path(__file__).parent / 'fixtures/s8_4_bridge/inputs.json').read_bytes()
STAGE = '20261021-' + 'a' * 32
RUN = '12345678-1234-4234-8234-123456789abc'
ROLES = {name: {'provider': 'siliconflow', 'model': model, 'model_id': name + '-id',
                'capability': 'chat' if name == 'cheap' else name}
         for name, model in runner.MODELS.items()}


def owner():
    return {'schema': 's85-conversation-authorization/v1', 'approved': True,
            'approval_source': 'Owner explicit current continuation', 'owner_message': 'test-only grant',
            'stage_id': STAGE, 'grant_id': 's85c-' + STAGE,
            'session_id': runner.SESSION_ID, 'session_envelope': deepcopy(runner.SESSION_ENVELOPE),
            'envelope': deepcopy(runner.ENVELOPE), 'source_inputs_sha256': runner.FROZEN_SHA256}


def setup_fence(index=1):
    configs = {name: provider_config('siliconflow', value['model'], model_id=value['model_id'])
               for name, value in ROLES.items()}
    fence = runner.TurnFence(RUN, index, ROLES,
        {name: runner.sha(runner.canonical(asdict(config))) for name, config in configs.items()},
        held_tokens=lambda: 0)
    context = CallContext(RUN, TrustedScope('owner', (runner.KB_ID,), tuple(sorted(runner.DOCUMENT_IDS))), session_id='new-session')
    state = PipelineState(query=runner.TURNS[index-1]['q0'], rewrite_query='A validated query?')
    fence.bind(context, state)
    return fence, configs, context, state


def test_import_is_connection_free(monkeypatch):
    monkeypatch.setattr(runner.historical, 'runtime_settings', lambda *a, **k: pytest.fail('runtime at import'))
    importlib.reload(runner)


def test_fixed_public_sources_and_plan_are_independent_copies():
    sources, versions = runner.validate_inputs(RAW)
    assert set(versions) == set(runner.DOCUMENT_IDS)
    assert set(r['knowledge_id'] for r in sources.values()) == set(runner.DOCUMENT_IDS)
    assert len(runner.TURNS) == 7 and runner.TURNS[5]['needs_retrieval'] is False
    sources.clear()
    assert runner.validate_inputs(RAW)[0]
    with pytest.raises(ValueError, match='FROZEN_INPUT'):
        runner.validate_inputs(RAW + b' ')


@pytest.mark.parametrize('change', ['schema', 'approved', 'grant_id', 'stage_id', 'source_inputs_sha256',
                                  'session_id', 'session_envelope', 'envelope', 'owner_message'])
def test_exact_current_authorization_only(change):
    value = owner()
    value[change] = False if change == 'approved' else '' if change == 'owner_message' else 'changed'
    with pytest.raises(ValueError):
        runner.validate_owner(value, RAW)


def test_ledger_is_fresh_per_actual_run_and_keeps_backend_limits():
    value = runner.ledger_spec(SimpleNamespace(tenant_id=332, subject_id='owner'), RUN, ROLES)
    assert value['max_calls'] == 4 and value['max_tokens'] == 20000 and value['entries'] == []
    assert value['allowed_request_prefixes'] == [RUN + '/']
    assert value['capture_evidence'] is True
    assert runner.ENVELOPE['max_calls'] == 28 and runner.ENVELOPE['max_tokens'] == 140000
    with pytest.raises(ValueError):
        runner.request_kind('old-trial/bridge-extract', RUN)


def test_generation_output_contract_is_real_and_bounded():
    fence, cfg, _, _ = setup_fence()
    contract = runner.generation_contract(cfg['cheap'])
    assert contract['output_upper'] == 1024 and contract['thinking_mode'] == 'disabled'


def test_fallback_configuration_uses_same_real_1024_wire_bound_as_ordinary_generation():
    from backend.weknora_port.service import TurnConfiguration
    from backend.weknora_port.models.types import ChatOptions
    from backend.weknora_port.models.request import build_chat_body
    from backend.weknora_port.models.output_contract import resolve_output_contract
    from backend.weknora_port.models.types import Message
    _,configs,_,_=setup_fence()
    original=TurnConfiguration(PipelineState(),{'options':ChatOptions(max_completion_tokens=2048,thinking=False)},
        {'strategy':'model','response':'unchanged fallback','prompt':'source gap',
         'documents':lambda:'metadata only','options':ChatOptions(max_completion_tokens=2048,thinking=False)})
    bounded=runner.bounded_turn_configuration(original)
    for options in (bounded.generation_options['options'],bounded.fallback['options']):
        body=build_chat_body(configs['cheap'],(Message('user','no evidence; ask clarification'),),options,stream=True)
        contract=resolve_output_contract(configs['cheap'],body,capability='chat')
        assert contract.output_upper==1024 and contract.thinking_mode=='disabled'
    assert bounded.fallback['strategy']==original.fallback['strategy']=='model'
    assert bounded.fallback['documents'] is original.fallback['documents']
    assert original.fallback['options'].max_completion_tokens==2048


def test_real_service_searchnothing_fallback_keeps_diagnostic_completion_cap():
    from dataclasses import replace
    from test_service import runtime,REQ
    from backend.weknora_port.service import TurnConfiguration,MemoryBundle
    from backend.weknora_port.models.types import ChatOptions
    from backend.weknora_port.retrieval.search import SearchNothing
    from backend.weknora_port.generation import RouterPolicy
    async def scenario():
        config=runner.bounded_turn_configuration(TurnConfiguration(PipelineState(),
            {'policy':RouterPolicy(mode='CHEAP_ONLY'),'options':ChatOptions(max_completion_tokens=2048)},
            {'strategy':'model','response':'insufficient evidence','prompt':'No source evidence for {{query}}.',
             'documents':lambda:'metadata only','options':ChatOptions(max_completion_tokens=2048)}))
        service,repo,*_=runtime(configuration=lambda turn:config)
        class NoMemory:
            async def load(self,*args): return MemoryBundle()
        service.memory_loader=NoMemory()
        async def pipeline_factory(*args):
            async def no_results(*args): raise SearchNothing()
            return SimpleNamespace(run=no_results)
        service.pipeline_factory=pipeline_factory
        accepted=await repo.accept(replace(REQ,content=runner.TURNS[4]['q0']),'owner')
        accepted=replace(accepted,memory_context=replace(accepted.memory_context,agent_memory_allowed=False))
        prepared=await service.prepare_turn(accepted)
        assert prepared.request.is_fallback and not prepared.request.evidence
        assert prepared.request.options.max_completion_tokens==1024
        assert prepared.request.options.max_tokens==0 and prepared.request.options.thinking is False
        assert prepared.request.policy.mode=='CHEAP_ONLY'
    asyncio.run(scenario())


def repair_owner():
    value=owner()
    value.update(schema='s85-conversation-repair-authorization/v1',session_id=runner.REPAIR_SESSION_ID,
        session_envelope=deepcopy(runner.REPAIR_SESSION_ENVELOPE),
        prior_accounting=deepcopy(runner.REPAIR_PRIOR_ACCOUNTING),
        generation_only_allowance=deepcopy(runner.REPAIR_GENERATION_ALLOWANCE),
        prior_c2={'stage_id':'20261021-'+'b'*32,'final_sha256':'0'*64,'authorization_sha256':'1'*64})
    return value


def test_repair_authorization_preserves_old_grant_and_reserves_aggregate_exposure():
    assert runner.validate_owner(owner(),RAW)=='s85c-'+STAGE
    assert runner.validate_owner(repair_owner(),RAW)=='s85c-'+STAGE
    assert runner.REPAIR_PRIOR_ACCOUNTING['attempted_operations']+runner.ENVELOPE['max_calls']+2==56
    assert runner.REPAIR_PRIOR_ACCOUNTING['held_token_exposure']+runner.ENVELOPE['max_tokens']+20000==199479


@pytest.mark.parametrize('field',['session_id','session_envelope','prior_accounting','generation_only_allowance','prior_c2'])
def test_repair_authorization_rejects_unbound_or_widened_metadata(field):
    value=repair_owner();value[field]='invalid'
    with pytest.raises(ValueError): runner.validate_owner(value,RAW)


def test_repair_prior_receipts_are_hash_bound_and_read_only(tmp_path,monkeypatch):
    import json
    prefix=str(tmp_path/'s8-5-conversation-')
    monkeypatch.setattr(runner,'ROOT_PREFIX',prefix)
    monkeypatch.setattr(runner,'validate_root',lambda path,stage: runner.require(str(path)==prefix+stage,'TEST_PATH_CHANGED'))
    value=repair_owner();prior=value['prior_c2'];root=Path(prefix+prior['stage_id'])
    (root/'payload').mkdir(parents=True)
    original=owner();original['stage_id']=prior['stage_id'];original['grant_id']='s85c-'+prior['stage_id']
    final={'status':'S85C_STOP','error':'TURN_OWNER_MISMATCH','stage_prefix':'s85c-'+prior['stage_id'],
        'old_history_and_sources_unchanged':True,'attempted_operations':19,'actual_reported_tokens_subtotal':27517}
    (root/'final.json').write_text(json.dumps(final),encoding='utf-8')
    (root/'payload/owner-authorization.json').write_text(json.dumps(original),encoding='utf-8')
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    prior['final_sha256']=runner.sha((root/'final.json').read_bytes())
    prior['authorization_sha256']=runner.sha((root/'payload/owner-authorization.json').read_bytes())
    result=runner.repair_prior_receipt(value)
    assert result['verified_c2_reported_tokens']==27517 and result['maximum_aggregate_token_exposure']==199479
    assert result['prior_sessions_reused'] is result['prior_receipts_modified'] is False
    assert all(path.read_bytes()==raw for path,raw in before.items())
    value['prior_c2']['final_sha256']='f'*64
    with pytest.raises(ValueError,match='RECEIPT_CHANGED'): runner.repair_prior_receipt(value)


def test_oneoff_registry_cleanup_closes_unique_clients_without_masking_original_failure():
    from backend.weknora_port.models.types import ProviderError
    calls=[]
    class Closed:
        async def close(self): calls.append('closed')
    class Broken:
        async def close(self):
            calls.append('broken');raise ProviderError('PROVIDER_TRANSPORT_ERROR',sent=True)
    shared=Closed()
    app=SimpleNamespace(_runs={'one':{'registry':shared},'two':{'registry':shared},'three':{'registry':Broken()}})
    result=asyncio.run(runner.close_run_registries(app))
    assert calls==['closed','broken'] and result['unique_registries_closed_or_attempted']==2
    assert result['cleanup_failures']==[{'type':'ProviderError','code':'PROVIDER_TRANSPORT_ERROR','sent':True,'http_status':None}]


def test_fence_accepts_actual_effective_embedding_then_rerank_query():
    from backend.weknora_port.models.request import build_embedding_body, build_rerank_body
    fence, cfg, ctx, state = setup_fence()
    rid = RUN + '/query-embedding-' + runner.KB_ID + '-' + runner.sha(state.rewrite_query)[:16]
    fence.validate(cfg['embedding'], build_embedding_body(cfg['embedding'], [state.rewrite_query], 'query'),
                   ctx.child(rid[len(RUN)+1:]), 'embedding')
    body = build_rerank_body(cfg['rerank'], state.rewrite_query, ['original source'])
    fence.validate(cfg['rerank'], body, ctx.child('rerank'), 'rerank')
    assert [row['kind'] for row in fence.wires] == ['embedding', 'rerank']


@pytest.mark.parametrize('bad', ['old-q0', 'extra-origin', 'other-session', 'wrong-model', 'second-embedding',
                               'wrong-hash', 'batch', 'scope', 'unbound', 'output', 'retry'])
def test_actual_wire_fence_rejects_changed_or_extra_calls(bad):
    from backend.weknora_port.models.request import build_embedding_body
    from dataclasses import replace
    fence, cfg, ctx, state = setup_fence()
    rid = 'query-embedding-' + runner.KB_ID + '-' + runner.sha(state.rewrite_query)[:16]
    context, config = ctx.child(rid), cfg['embedding']
    body = build_embedding_body(config, [state.rewrite_query], 'query')
    if bad == 'old-q0': body['input'] = [state.query]
    if bad == 'extra-origin': context = replace(context, required_origin_kbs=('other',))
    if bad == 'other-session': context = replace(context, session_id='old-session')
    if bad == 'wrong-model': config = replace(config, model_id='other')
    if bad == 'second-embedding': fence.seen.add('embedding')
    if bad == 'wrong-hash': context = ctx.child('query-embedding-' + runner.KB_ID + '-0000000000000000')
    if bad == 'batch': context = ctx.child(rid + '/embedding-0')
    if bad == 'scope': context = replace(context, scope=TrustedScope('owner', (runner.KB_ID,), ()))
    if bad == 'unbound': fence.bound = None
    if bad == 'output': fence.held_tokens = lambda: 20000
    if bad == 'retry': context = ctx.child(rid + '/retry')
    with pytest.raises(ValueError): fence.validate(config, body, context, 'embedding')


def test_format_turn_fence_refuses_any_retrieval_send():
    fence, cfg, ctx, _ = setup_fence(6)
    with pytest.raises(ValueError, match='FORMAT_RETRIEVAL'):
        fence.validate(cfg['rerank'], {'query': 'x'}, ctx.child('rerank'), 'rerank')


def settlement(rid, cap, usage=10):
    role = ROLES['cheap' if cap == 'chat' else cap]
    shared = {'request_id': rid, 'capability': cap, **role, 'outcome': 'RESPONSE_RECEIVED',
              'wire_sha256': 'a' * 64, 'usage': {'status': 'REPORTED', 'prompt_tokens': usage,
                    'completion_tokens': 0, 'total_tokens': usage}}
    return {**shared, 'finished_at': 'now'}, {**shared, 'status': 'SETTLED', 'held_tokens': usage}


def test_dual_receipt_keys_and_exact_usage_not_just_counts():
    pg, local = settlement(RUN + '/query_rewrite', 'chat')
    result = runner.validate_settlements([pg], [local], RUN, ROLES)
    assert result['chat']['input_tokens'] == 10
    with pytest.raises(ValueError, match='DUPLICATE_PG'):
        runner.validate_settlements([pg, pg], [local, local], RUN, ROLES)
    with pytest.raises(ValueError, match='DUPLICATE_EXECUTION'):
        runner.validate_settlements([pg], [local, local], RUN, ROLES)
    other = deepcopy(pg); other['request_id'] = RUN + '/answer/1/cheap'
    with pytest.raises(ValueError, match='KEYS_MISMATCH'):
        runner.validate_settlements([other], [local], RUN, ROLES)
    other = deepcopy(pg); other['usage']['total_tokens'] = 11
    with pytest.raises(ValueError, match='USAGE'):
        runner.validate_settlements([other], [local], RUN, ROLES)


@pytest.mark.parametrize('status', ['UNKNOWN', 'RESERVED', 'LOCAL_ADMISSION_FAILED_STOP'])
def test_nonsettled_attempt_never_looks_successful(status):
    pg, local = settlement(RUN + '/query_rewrite', 'chat')
    local['status'] = status
    with pytest.raises(ValueError, match='UNKNOWN_OR_UNFINISHED'):
        runner.validate_settlements([pg], [local], RUN, ROLES)


def test_fallback_is_not_reported_as_coreference_success():
    state = PipelineState(query=runner.TURNS[1]['q0'], rewrite_query=runner.TURNS[1]['q0'],
                          rewrite_fallback_reason='CURRENT_QUESTION_CHANGED')
    report = runner.trace_state(state)
    assert report['coreference_resolved'] is False and report['rewrite_fallback_reason']
    state.rewrite_query = 'What did Jeanne Marie Beaumont direct from 2007–2010?'
    state.rewrite_fallback_reason = ''
    assert runner.trace_state(state)['coreference_resolved'] is True


def test_aggregate_stops_before_eighth_or_widened_ledger():
    closed = {'attempted_operations': 4, 'held_tokens_total': 20000,
              'possibly_sent_unknown_operations': 0}
    runner.check_aggregate([closed] * 6, next_turn=True)
    with pytest.raises(ValueError): runner.check_aggregate([closed] * 7, next_turn=True)
    with pytest.raises(ValueError): runner.check_aggregate([{**closed, 'possibly_sent_unknown_operations': 1}])


def test_fence_keeps_real_transport_and_stops_after_unconfirmed_audit():
    from backend.weknora_port.models.request import build_embedding_body
    fence, configs, context, state = setup_fence()
    calls = []

    @asynccontextmanager
    async def opened(*args, **kwargs):
        calls.append('original transport')
        yield SimpleNamespace()

    async def audit():
        raise ValueError('S85C_UNKNOWN_OR_UNFINISHED_STOP')

    fence.audit = audit
    transport = SimpleNamespace(open=opened, gate=object())
    original_gate = transport.gate
    model = fence.install(SimpleNamespace(transport=transport))
    assert model.transport.gate is original_gate
    rid = 'query-embedding-' + runner.KB_ID + '-' + runner.sha(state.rewrite_query)[:16]

    async def attempt():
        async with model.transport.open(configs['embedding'], '/embeddings',
            build_embedding_body(configs['embedding'], [state.rewrite_query], 'query'),
            context.child(rid), capability='embedding'):
            pass

    with pytest.raises(ValueError, match='UNKNOWN_OR_UNFINISHED'):
        asyncio.run(attempt())
    assert fence.stopped and calls == ['original transport'] and not fence.completed
    with pytest.raises(PermissionError, match='EXACT_OPERATION'):
        asyncio.run(attempt())
    assert calls == ['original transport']


def test_isolation_uses_real_history_scope_and_propagates_unknown_errors(monkeypatch):
    from backend.weknora_port.models.types import ProviderError
    from backend.weknora_port.storage.history import LocalPrincipal
    principal = LocalPrincipal(332, 'owner')
    seen = []

    async def snapshot(app): return {'provider': {'calls': 3}, 'data': {}}
    monkeypatch.setattr(runner.historical, 'history_snapshot', snapshot)

    async def messages(actor, session):
        if actor != principal: raise ProviderError('TRUSTED_PRINCIPAL_REQUIRED')
        return []

    async def history(memory, session, limit):
        seen.append((memory, session, limit))
        return () if session == 'empty' else (SimpleNamespace(request_id=RUN),)

    app = SimpleNamespace(list_messages=messages, history=SimpleNamespace(load=history))
    result = asyncio.run(runner.isolation_checks(app, principal, 'main', 'empty',
                         completed=SimpleNamespace(run_id=RUN)))
    assert result['wrong_subject_denied'] is True
    assert result['main_history_request_ids'] == [RUN]
    assert [row[1] for row in seen] == ['empty', 'main']
    assert all(row[0].principal_storage_id == 'owner' and not row[0].agent_memory_allowed for row in seen)

    async def broken(*args): raise RuntimeError('database unavailable')
    app.list_messages = broken
    with pytest.raises(RuntimeError, match='database unavailable'):
        asyncio.run(runner.isolation_checks(app, principal, 'main', 'empty'))


def test_isolation_zero_call_claim_requires_unchanged_provider_fingerprint(monkeypatch):
    from backend.weknora_port.models.types import ProviderError
    from backend.weknora_port.storage.history import LocalPrincipal
    principal = LocalPrincipal(332, 'owner')
    count = 0

    async def snapshot(app):
        nonlocal count
        count += 1
        return {'provider': {'calls': count}, 'data': {}}

    async def messages(actor, session):
        if actor != principal: raise ProviderError('TRUSTED_PRINCIPAL_REQUIRED')
        return []

    async def history(*args): return ()
    monkeypatch.setattr(runner.historical, 'history_snapshot', snapshot)
    app = SimpleNamespace(list_messages=messages, history=SimpleNamespace(load=history))
    with pytest.raises(ValueError, match='ISOLATION_READ_MUTATED_OR_SENT'):
        asyncio.run(runner.isolation_checks(app, principal, 'main', 'empty'))


def test_source_readback_checks_original_source_content_and_offsets(monkeypatch):
    import backend.weknora_port.runtime as runtime
    from backend.weknora_port.retrieval.types import SearchResult
    from backend.weknora_port.generation.context import Evidence
    actual = SearchResult('source-id', 'Exact original.', 0, knowledge_id=runner.DOCUMENT_IDS[0],
        knowledge_base_id=runner.KB_ID, start_at=20, end_at=35,
        metadata={'source_version': 'v1'}, chunk_metadata={'source_version': 'v1'})

    async def chunks(ids, context): return {'source-id': actual}

    monkeypatch.setattr(runtime, 'LeasedKnowledgeRepository', lambda *args: SimpleNamespace(get_current_chunks=chunks))
    evidence = Evidence.from_result(actual)
    from dataclasses import replace
    # Enriched presentation need not equal the raw original; source_content must.
    evidence = replace(evidence, content='A presentation header\nExact original.')
    request = SimpleNamespace(run_id=RUN, evidence=(evidence,))
    app = SimpleNamespace(_runs={RUN: {'registry': object()}})
    result = asyncio.run(runner.source_readback(app, request, None, {actual.knowledge_id: 'v1'}))
    assert result[0]['exact_text'] == 'Exact original.' and result[0]['start_at'] == 20
    request.evidence = (replace(evidence, source_content='Generated replacement.'),)
    with pytest.raises(ValueError, match='NOT_CURRENT_ORIGINAL'):
        asyncio.run(runner.source_readback(app, request, None, {actual.knowledge_id: 'v1'}))
    request.evidence = (replace(evidence, end_at=36),)
    with pytest.raises(ValueError, match='NOT_CURRENT_ORIGINAL'):
        asyncio.run(runner.source_readback(app, request, None, {actual.knowledge_id: 'v1'}))


def test_independent_rephrase_is_not_coreference_and_model_output_limit_is_checked():
    from dataclasses import replace
    state = PipelineState(query='An independent question?', rewrite_query='AN independent question?')
    assert runner.trace_state(state)['coreference_resolved'] is False
    _, configs, _, _ = setup_fence()
    with pytest.raises(ValueError, match='MODEL_OUTPUT_LIMIT'):
        runner.generation_contract(replace(configs['cheap'], max_output_tokens=512))


def test_citation_provenance_is_verified_without_claiming_answer_semantics():
    proof = {'chunk_id': 'chunk', 'document_id': runner.DOCUMENT_IDS[0],
             'start_at': 2, 'end_at': 10, 'exact_text': 'Original'}
    reference = {'id': 'chunk', 'knowledge_base_id': runner.KB_ID,
                 'knowledge_id': runner.DOCUMENT_IDS[0], 'start_at': 2, 'end_at': 10, 'content': 'Original'}
    result = runner.citation_readback(SimpleNamespace(references=[reference]), [proof])
    assert result['citations_present'] and result['prose_semantic_support'].startswith('REQUIRES_REVIEW')
    reference['content'] = 'Model generated'
    with pytest.raises(ValueError, match='CITATION_NOT_CURRENT_SOURCE'):
        runner.citation_readback(SimpleNamespace(references=[reference]), [proof])
    assert runner.citation_readback(SimpleNamespace(references=[]), [])['citations_present'] is False


@pytest.mark.parametrize('fail_at', [None, 2])
def test_seven_turn_runner_wiring_and_stop_preserve_fresh_ledgers_without_external_io(tmp_path, monkeypatch, fail_at):
    """Fake PG/service wiring only; it does not claim model/retrieval quality."""
    from datetime import datetime, timezone
    import uuid
    import backend.weknora_port.development as development
    import backend.weknora_port.execution as execution
    from backend.weknora_port.service import AcceptedTurn, TurnConfiguration
    from backend.weknora_port.models.types import TokenUsage
    from backend.weknora_port.conversation.types import MemoryContext, HistoryRound
    from backend.weknora_port.generation.types import GenerationRequest, GenerationResult
    from backend.weknora_port.generation.router import RouterPolicy
    from backend.weknora_port.retrieval.context import FinalizedContext
    from backend.weknora_port.runtime import ConversationSettings
    from backend.weknora_port.storage.history import LocalPrincipal
    from backend.weknora_port.storage.turns import TurnState
    from backend.weknora_port.streaming.events import StreamEvent

    root = tmp_path / 'stage'
    (root / 'payload').mkdir(parents=True)
    (root / 'payload/inputs.json').write_bytes(RAW)
    runner.write_new(root / 'payload/owner-authorization.json', owner())
    principal = LocalPrincipal(332, 'owner')
    configs = {name: provider_config('siliconflow', row['model'], model_id=row['model_id'])
               for name, row in ROLES.items()}
    before = {'provider': {'calls': 0, 'unfinished': 0}, 'data': {}}
    pre = {'before': before, 'registry_roles': ROLES, 'document_versions': {},
           'model_config_sha256': {name: runner.sha(runner.canonical(asdict(config))) for name, config in configs.items()}}
    runner.write_new(root / 'preflight.json', pre)
    sessions, admitted, completed, history = [], {}, [], []

    async def snapshot(*args): return deepcopy(before)
    async def prepared(*args, **kwargs): return deepcopy(pre)
    monkeypatch.setattr(runner, 'prepare', prepared)
    monkeypatch.setattr(runner, 'protected_snapshot', snapshot)
    monkeypatch.setattr(runner.historical, 'history_snapshot', snapshot)
    monkeypatch.setattr(runner, 'validate_root', lambda *a: None)
    monkeypatch.setattr(runner, 'runtime_settings', lambda *a: (object(), principal))
    monkeypatch.setattr(runner, 'EXECUTION_ROOT', str(tmp_path / 'executions'))
    monkeypatch.setattr(runner.bridge, 'receipt_manifest', lambda *a: [])
    monkeypatch.setattr(execution, 'LocalExecution', lambda path: SimpleNamespace(path=path))

    def close(path, *, success):
        value = runner.read(path)
        value['status'] = 'CLOSED' if success else 'STOP'
        runner.safe_write(path, value)
    monkeypatch.setattr(runner.historical, 'close_ledger', close)

    async def provider_rows(*args): return []
    monkeypatch.setattr(runner.historical, 'provider_rows', provider_rows)

    async def create_session(actor, request):
        assert actor == principal and set(request.document_scope) == set(runner.DOCUMENT_IDS)
        result = {'id': str(uuid.uuid4()), 'document_scope': request.document_scope,
                  'knowledge_base_scope': request.knowledge_base_scope}
        sessions.append(result['id'])
        return result

    async def accept(request, actor):
        assert actor == principal
        if request.request_id in admitted:
            from dataclasses import replace
            return replace(admitted[request.request_id], is_new=False, replay_only=True)
        from dataclasses import replace
        run = str(uuid.uuid4())
        turn = AcceptedTurn(run, request.session_id, str(uuid.uuid4()), str(uuid.uuid4()),
            CallContext(run, TrustedScope('owner', (runner.KB_ID,), tuple(sorted(runner.DOCUMENT_IDS))), session_id=request.session_id),
            MemoryContext(332, 'owner'), replace(request, request_id=run), True, False, request.request_id)
        admitted[request.request_id] = turn
        app._runs[run] = {'registry': object()}
        return turn

    async def status(session, message): return TurnState('completed')
    async def load_history(memory, session, limit):
        return tuple(history[-limit:]) if session == sessions[0] else ()
    async def list_messages(actor, session):
        from backend.weknora_port.models.types import ProviderError
        if actor != principal: raise ProviderError('TRUSTED_PRINCIPAL_REQUIRED')
        return [{'request_id': row.request_id} for row in history] if session == sessions[0] else []

    async def configuration(turn):
        state = PipelineState(enable_expansion=False, source_evidence_mode='bounded_source_v1')
        return TurnConfiguration(state, {'policy': RouterPolicy(mode='CHEAP_ONLY')})

    async def pipeline_factory(turn, progress):
        async def hybrid_search(*args): return []
        async def run_pipeline(state, context):
            state.rewrite_query = state.query
            state.history = list(history[-5:])
            state.needs_retrieval = state.query != runner.TURNS[5]['q0']
            state.intent = 'kb_search' if state.needs_retrieval else 'follow_up'
            if fail_at == len(completed)+1: raise RuntimeError('synthetic wiring stop')
            if state.needs_retrieval:
                from backend.weknora_port.retrieval.search import SearchParams, SearchTarget
                await pipeline.repository.hybrid_search((SearchTarget(runner.KB_ID, 'knowledge', tuple(runner.DOCUMENT_IDS)),),
                    SearchParams(state.rewrite_query), context)
            return FinalizedContext(state.query, '', ())
        pipeline = SimpleNamespace(repository=SimpleNamespace(hybrid_search=hybrid_search), run=run_pipeline)
        return pipeline

    async def generation_factory(turn, progress):
        async def generate(request, context):
            return GenerationResult('Insufficient evidence.', '', (), 'cheap', turn.run_id+'/answer/1/cheap', (),
                None, TokenUsage(0, 0, 0, 'REPORTED'), 'stop', False, False, 'a'*64, 'b'*64)
        return SimpleNamespace(run=generate)

    async def run_turn(turn):
        if turn.replay_only: return None
        config = await app.ordinary.configuration(turn)
        state = config.pipeline_state
        state.query = turn.request.content
        pipeline = await app.ordinary.pipeline_factory(turn, None)
        finalized = await pipeline.run(state, turn.context)
        coordinator = await app.ordinary.generation_factory(turn, None)
        request = GenerationRequest(turn.run_id, turn.session_id, turn.assistant_message_id, turn.request.content,
            finalized, **config.generation_options)
        result = await coordinator.run(request, turn.context)
        completed.append(turn.run_id)
        history.append(HistoryRound(turn.run_id, turn.request.content, result.answer,
            datetime.now(timezone.utc), knowledge_base_scope=(runner.KB_ID,), document_scope=tuple(runner.DOCUMENT_IDS)))
        return result

    async def replay(*args): yield StreamEvent('complete', 'complete', done=True)
    app = SimpleNamespace(principal=principal, conversation=ConversationSettings(), _runs={}, cloud_allowed=False,
        create_session=create_session, turns=SimpleNamespace(accept=accept, status=status),
        model_factory=lambda *args: pytest.fail('Fake path must not construct any model'),
        history=SimpleNamespace(load=load_history), list_messages=list_messages,
        ordinary=SimpleNamespace(configuration=configuration, pipeline_factory=pipeline_factory,
            generation_factory=generation_factory, run_turn=run_turn, replay=replay))

    @asynccontextmanager
    async def assemble(*args, **kwargs): yield app
    monkeypatch.setattr(development, 'assemble', assemble)
    result = asyncio.run(runner.run(root))
    assert result['new_sessions'] == sessions and len(sessions) == 2
    assert result['old_history_and_sources_unchanged'] is True and result['all_actual_tokens'] == 0
    assert result['attempted_operations'] == 0
    if fail_at is None:
        assert result['status'] == 'S85C_COMPLETED_DIAGNOSTIC' and len(completed) == 7
        assert all(record['replay']['new_provider_calls'] == 0 for record in result['turns'])
        assert all(runner.read(Path(record['ledger']))['status'] == 'CLOSED' for record in result['turns'])
    else:
        assert result['status'] == 'S85C_STOP' and len(completed) == fail_at-1
        assert runner.read(Path(result['turns'][-1]['ledger']))['status'] == 'STOP'
    # The original stage grant cannot be run twice, even after a stopped attempt.
    with pytest.raises(FileExistsError): asyncio.run(runner.run(root))
