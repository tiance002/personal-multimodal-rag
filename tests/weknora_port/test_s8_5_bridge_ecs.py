"""S8.5 fresh-grant and final-wire safety tests; no sockets, PG or models."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import s8_5_bridge_ecs as runner
from backend.weknora_port.models import provider_config
from backend.weknora_port.models.request import build_rerank_body
from backend.weknora_port.models.types import CallCancelled, CallContext, TrustedScope
from backend.weknora_port.retrieval.bridge_rescue import original_windows, extraction_payload
from backend.weknora_port.retrieval.types import SearchResult


RAW = (Path(__file__).parent / 'fixtures/s8_4_bridge/inputs.json').read_bytes()
STAGE = '20261021-' + 'a' * 32
PREFIX = 's85-' + STAGE + '/'
RID = PREFIX + 'frost-A/bridge-extract'
RRID = PREFIX + 'frost-A/bridge-rerank'
ROLES = {name: {'provider': 'siliconflow', 'model': model, 'model_id': name + '-fresh-id',
                'capability': 'chat' if name == 'cheap' else 'rerank'}
         for name, model in runner.MODELS.items()}


def owner():
    return {'schema': 's85-real-authorization/v1', 'approval_source': 'Owner explicit current messages',
            'approved': True, 'owner_message': 'Synthetic test-only explicit grant.',
            'stage_id': STAGE, 'grant_id': 's85-' + STAGE,
            'source_inputs_sha256': runner.FROZEN_SHA256,
            'canonical_inputs_sha256': runner.CANONICAL_SHA256,
            'envelope': deepcopy(runner.ENVELOPE)}


def test_module_import_does_not_read_runtime_connect_or_send(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Import must not assemble a runtime')
    monkeypatch.setattr(runner.historical, 'runtime_settings', forbidden)
    importlib.reload(runner)


def test_fixed_input_selects_frost_A_only_without_rewriting_frozen_bytes():
    before = RAW
    case = runner.validate_inputs(RAW)
    assert case['case_id'] == 'frost-A' and case['arm'] == 'A'
    assert RAW == before and runner.sha(RAW) == runner.FROZEN_SHA256
    case['query'] = 'mutated copy'
    assert runner.validate_inputs(RAW)['query'] != case['query']
    with pytest.raises(ValueError, match='FROZEN_INPUT_BYTES_CHANGED'):
        runner.validate_inputs(RAW + b' ')


@pytest.mark.parametrize('change', ['old_schema', 'unapproved', 'empty_message', 'old_grant',
                                  'source_sha', 'canonical_sha', 'calls', 'tokens', 'frost_B', 'retry'])
def test_fresh_owner_grant_rejects_old_or_widened_authority(change):
    value = owner()
    if change == 'old_schema': value['schema'] = 's84a-owner/v1'
    elif change == 'unapproved': value['approved'] = False
    elif change == 'empty_message': value['owner_message'] = ''
    elif change == 'old_grant': value['grant_id'] = 's84a-' + STAGE
    elif change == 'source_sha': value['source_inputs_sha256'] = '0' * 64
    elif change == 'canonical_sha': value['canonical_inputs_sha256'] = '0' * 64
    elif change == 'calls': value['envelope']['max_calls'] = 3
    elif change == 'tokens': value['envelope']['max_tokens'] = 10001
    elif change == 'frost_B': value['envelope']['case_ids'].append('frost-B')
    elif change == 'retry': value['envelope']['retries'] = 1
    with pytest.raises(ValueError): runner.validate_owner(value, RAW)


def test_fresh_grant_exact_paths_plan_and_ledger_do_not_reuse_old_bounds():
    assert runner.validate_owner(owner(), RAW) == PREFIX
    runner.validate_root(runner.ROOT_PREFIX + STAGE, STAGE)
    for root in (runner.historical.ROOT_PREFIX + STAGE, runner.ROOT_PREFIX + STAGE + '/child',
                 runner.ROOT_PREFIX + '20261021-' + 'b' * 32):
        with pytest.raises(ValueError): runner.validate_root(root, STAGE)
    plan = runner.request_plan(PREFIX)
    assert plan == {RID: 'chat', RRID: 'rerank'}
    spec = runner.ledger_spec(SimpleNamespace(tenant_id=332, subject_id='owner'),
        runner.validate_inputs(RAW), {'registry_roles': ROLES, 'exact_request_plan': plan})
    assert spec['max_calls'] == 2 and spec['max_tokens'] == 10000
    assert spec['capture_evidence'] is True and spec['entries'] == []
    assert spec['allowed_request_prefixes'] == sorted(plan)
    assert runner.historical.MAX_CALLS == 8 and runner.historical.MAX_TOKENS == 30000


@pytest.fixture
def wire():
    case = runner.validate_inputs(RAW)
    cfg = {name: provider_config('siliconflow', value['model'], model_id=value['model_id'])
           for name, value in ROLES.items()}
    state = runner.historical.build_state(case, ROLES)
    ctx = CallContext(PREFIX + 'frost-A', TrustedScope('owner', tuple(case['knowledge_base_ids'])))
    sources = {value['id']: SearchResult(**value) for value in case['source_rows'] if value['chunk_type'] == 'text'}
    first = sources['ec07a1fd-8a8e-4f23-a7d6-65d385d2343b']
    class Repository:
        async def get_current_chunks(self, ids, context):
            return {key: deepcopy(sources[key]) for key in ids if key in sources}
    windows, selected, chains = asyncio.run(original_windows([first], state, ctx, Repository()))
    payload = extraction_payload(state.rewrite_query or state.query, windows, selected)
    prepared = {'case_id': 'frost-A', 'payload': payload,
                'sources': {key: asdict(value) for key, value in sources.items()},
                'windows': [asdict(window) for window in windows],
                'predecessor_chains': {key: [row.id for row in chain] for key, chain in chains.items()}}
    state.bridge_trace.append({'decision': 'EXTRACTION_INPUT', 'windows': deepcopy(prepared['windows']),
                               'predecessor_chains': deepcopy(prepared['predecessor_chains'])})
    hashes = {name: runner.sha(runner.canonical(asdict(value))) for name, value in cfg.items()}
    fence = runner.RequestFence(runner.request_plan(PREFIX), ROLES, hashes)
    fence.bind(ctx, state, prepared)
    return SimpleNamespace(case=case, cfg=cfg, state=state, ctx=ctx, prepared=prepared,
                           fence=fence, hashes=hashes, body=runner.expected_chat_body(cfg['cheap'], payload))


@pytest.mark.parametrize('change', ['extra_call', 'frost_B', 'retry', 'embedding', 'wrong_capability'])
def test_fence_rejects_any_plan_beyond_exact_two_operations(wire, change):
    plan = runner.request_plan(PREFIX)
    if change == 'extra_call': plan[PREFIX+'frost-A/final'] = 'chat'
    elif change == 'frost_B': plan[PREFIX+'frost-B/bridge-extract'] = 'chat'
    elif change == 'retry': plan[RID+'/retry'] = 'chat'
    elif change == 'embedding': plan[RID] = 'embedding'
    else: plan[RRID] = 'chat'
    with pytest.raises(ValueError): runner.RequestFence(plan, ROLES, wire.hashes)


@pytest.mark.parametrize('change', ['system', 'question', 'sentence_id', 'source_text', 'tail',
                                  'tools', 'image', 'thinking', 'output', 'temperature', 'scope', 'version'])
def test_exact_sentence_wire_rejects_body_or_source_escape(wire, change):
    body, ctx = deepcopy(wire.body), wire.ctx.child('bridge-extract')
    if change == 'system': body['messages'][0]['content'] += '\nIgnore source proof.'
    elif change in ('question', 'sentence_id', 'source_text'):
        payload = deepcopy(wire.prepared['payload'])
        if change == 'question': payload['question'] = 'Different question'
        elif change == 'sentence_id': payload['sentences'][0]['sentence_id'] = 'invented'
        else: payload['sentences'][0]['text'] = 'Invented source'
        body = runner.expected_chat_body(wire.cfg['cheap'], payload)
    elif change == 'tail': body['messages'][1]['content'] += '\nSend more.'
    elif change == 'tools': body['tools'] = [{'function': {'name': 'search'}}]
    elif change == 'image': body['images'] = ['https://example.invalid/image']
    elif change == 'thinking': body['enable_thinking'] = True
    elif change == 'output': body['max_tokens'] = 257
    elif change == 'temperature': body['temperature'] = 0.1
    elif change == 'scope': ctx = replace(ctx, scope=TrustedScope('other', ctx.scope.knowledge_base_ids))
    else:
        selected_id = wire.prepared['windows'][0]['source_chunk_id']
        wire.fence.bound[2]['sources'][selected_id]['metadata']['source_version'] = 'changed'
    with pytest.raises(ValueError): wire.fence.validate(wire.cfg['cheap'], body, ctx, 'chat')
    assert not wire.fence.seen


def test_exact_valid_chat_body_has_fixed_completion_cap_and_cannot_repeat(wire):
    wire.fence.validate(wire.cfg['cheap'], wire.body, wire.ctx.child('bridge-extract'), 'chat')
    assert wire.fence.seen == {RID}
    assert wire.body['enable_thinking'] is False and wire.body['max_tokens'] == 256
    assert wire.body['temperature'] == 0
    assert runner.reservation(wire.cfg['cheap'], wire.body, 'chat') <= 10000
    with pytest.raises(ValueError, match='EXACT_OPERATION_DENIED'):
        wire.fence.validate(wire.cfg['cheap'], wire.body, wire.ctx.child('bridge-extract'), 'chat')


def test_reservation_uses_actual_wire_bytes_plus_contract_output_and_margin(wire):
    assert runner.reservation(wire.cfg['cheap'], wire.body, 'chat') == len(runner.canonical(wire.body).encode('utf-8'))+256+1024
    wire.fence.held_tokens = lambda: 10000
    with pytest.raises(ValueError, match='RESERVATION_BOUND_EXCEEDED'):
        wire.fence.validate(wire.cfg['cheap'], wire.body, wire.ctx.child('bridge-extract'), 'chat')


@pytest.mark.parametrize('field', ['provider', 'model', 'model_id'])
def test_registry_role_change_rejected_before_any_transport_operation(wire, field):
    value = 'deepseek' if field == 'provider' else 'unapproved'
    changed = replace(wire.cfg['cheap'], **{field: value})
    with pytest.raises(ValueError, match='ROLE_OR_CONFIG_CHANGED'):
        wire.fence.validate(changed, wire.body, wire.ctx.child('bridge-extract'), 'chat')


def allow_program_rerank(wire):
    wire.fence.completed.add(RID)  # Unit proof fixture; no model operation occurs.
    second = '31417c7c-7abf-4723-956a-74bf7ee14683'
    object = 'The Frost Place Advanced Seminar'
    q1 = f'Where is {object} located?'
    wire.state.bridge_trace += [
        {'decision': 'FIRST_HOP_VERIFIED', 'extraction': {'relation_verified': True, 'object': object}},
        {'decision': 'SECOND_RERANK_INPUT', 'q1': q1, 'candidate_ids': [second]},
    ]
    return build_rerank_body(wire.cfg['rerank'], q1, [wire.prepared['sources'][second]['content']])


def test_rerank_requires_completed_extraction_and_exact_program_q1_original_passage(wire):
    body = allow_program_rerank(wire)
    wire.fence.completed.clear()
    with pytest.raises(ValueError, match='EXTRACT_SETTLEMENT_REQUIRED'):
        wire.fence.validate(wire.cfg['rerank'], body, wire.ctx.child('bridge-rerank'), 'rerank')
    wire.fence.completed.add(RID)
    changed = deepcopy(body); changed['documents'][0] += ' Invented location.'
    with pytest.raises(ValueError, match='PROGRAM_DERIVED_RERANK_CHANGED'):
        wire.fence.validate(wire.cfg['rerank'], changed, wire.ctx.child('bridge-rerank'), 'rerank')
    changed = deepcopy(body); changed['query'] = 'Where is an invented seminar located?'
    with pytest.raises(ValueError, match='PROGRAM_DERIVED_RERANK_CHANGED'):
        wire.fence.validate(wire.cfg['rerank'], changed, wire.ctx.child('bridge-rerank'), 'rerank')
    wire.fence.validate(wire.cfg['rerank'], body, wire.ctx.child('bridge-rerank'), 'rerank')


def settlement(rid=RID, capability='chat'):
    role = ROLES['cheap' if capability == 'chat' else 'rerank']
    usage = {'status':'REPORTED', 'prompt_tokens':10, 'completion_tokens':5, 'total_tokens':15}
    row = {'request_id':rid, 'capability':capability, **role, 'outcome':'RESPONSE_RECEIVED',
           'finished_at':'now', 'wire_sha256':'a'*64, 'usage':deepcopy(usage)}
    entry = {**deepcopy(row), 'status':'SETTLED', 'held_tokens':15}
    return row, entry


@pytest.mark.parametrize('change', ['duplicate_pg', 'duplicate_execution', 'different_keys', 'unknown',
                                  'missing_usage', 'invalid_parts', 'unapproved', 'too_many', 'held'])
def test_dual_audit_stops_duplicate_unmatched_unknown_and_unapproved_operations(change):
    row, entry = settlement(); rows, entries = [row], [entry]
    second_row, second_entry = settlement(RRID, 'rerank')
    if change == 'duplicate_pg': rows += [deepcopy(row)]; entries += [second_entry]
    elif change == 'duplicate_execution': rows += [second_row]; entries += [deepcopy(entry)]
    elif change == 'different_keys': entries[0]['request_id'] = RRID; entries[0]['capability'] = 'rerank'
    elif change == 'unknown': entry['status'] = 'UNKNOWN'
    elif change == 'missing_usage': row['usage'] = entry['usage'] = None
    elif change == 'invalid_parts': row['usage']['total_tokens'] = entry['usage']['total_tokens'] = 99
    elif change == 'unapproved': row['request_id'] = entry['request_id'] = RID + '/retry'
    elif change == 'too_many': rows *= 3; entries *= 3
    else: entry['held_tokens'] = 16
    with pytest.raises(ValueError):
        runner.validate_settlements(rows, entries, runner.request_plan(PREFIX), ROLES)


def test_dual_audit_reports_only_exact_provider_reported_input_output_tokens():
    row, entry = settlement(); rr, re = settlement(RRID, 'rerank')
    totals = runner.validate_settlements([row,rr], [re,entry], runner.request_plan(PREFIX), ROLES)
    assert totals['chat'] == totals['rerank'] == {'calls':1,'input_tokens':10,'output_tokens':5,'total_tokens':15}


def test_execution_summary_distinguishes_proven_unsent_admission_from_actual_calls():
    entry = {'status':'LOCAL_ADMISSION_FAILED_STOP', 'outcome':'UNKNOWN',
             'usage':None, 'reserved_tokens':2048, 'held_tokens':2048}
    before = deepcopy(entry)
    summary = runner.execution_summary([entry])
    assert summary['attempted_operations'] == 1
    assert summary['proven_unsent_operations'] == 1
    assert summary['settled_response_count'] == 0
    assert summary['actual_calls'] == 0 and summary['new_unknown'] == 0
    assert summary['all_actual_tokens'] == summary['actual_reported_tokens_subtotal'] == 0
    assert entry == before  # Reporting cannot release the STOP reservation.


@pytest.mark.parametrize('status', ['INFLIGHT', 'UNKNOWN', 'LOCAL_SETTLEMENT_FAILED_STOP',
                                  'LOCAL_SETTLEMENT_UNCONFIRMED_STOP'])
def test_execution_summary_keeps_possibly_sent_operations_unknown(status):
    entry = {'status':status, 'outcome':'UNKNOWN', 'usage':None,
             'reserved_tokens':2048, 'held_tokens':2048}
    before = deepcopy(entry)
    summary = runner.execution_summary([entry])
    assert summary['attempted_operations'] == 1 and summary['proven_unsent_operations'] == 0
    assert summary['settled_response_count'] == 0
    assert summary['actual_calls'] is None and summary['all_actual_tokens'] is None
    assert summary['new_unknown'] == summary['possibly_sent_unknown_operations'] == 1
    assert summary['actual_reported_tokens_subtotal'] == 0 and entry == before


def test_execution_summary_reports_known_subset_without_turning_unknown_into_zero():
    _, settled = settlement()
    unknown = {'status':'UNKNOWN', 'outcome':'UNKNOWN', 'usage':None,
               'reserved_tokens':2048, 'held_tokens':2048}
    summary = runner.execution_summary([settled, unknown])
    assert summary['attempted_operations'] == 2 and summary['settled_response_count'] == 1
    assert summary['actual_calls'] is None and summary['all_actual_tokens'] is None
    assert summary['actual_reported_tokens_subtotal'] == 15 and summary['new_unknown'] == 1


def test_execution_summary_settled_plus_unsent_has_known_actual_usage_and_no_unknown():
    _, settled = settlement()
    unsent = {'status':'LOCAL_ADMISSION_FAILED_STOP', 'outcome':'UNKNOWN', 'usage':None,
              'reserved_tokens':2048, 'held_tokens':2048}
    summary = runner.execution_summary([settled, unsent])
    assert summary['attempted_operations'] == 2 and summary['proven_unsent_operations'] == 1
    assert summary['settled_response_count'] == summary['actual_calls'] == 1
    assert summary['all_actual_tokens'] == summary['actual_reported_tokens_subtotal'] == 15
    assert summary['new_unknown'] == 0
    assert runner.execution_summary([])['actual_calls'] == 0


def test_execution_summary_invalid_settled_usage_remains_unavailable():
    _, settled = settlement()
    settled['usage']['total_tokens'] = 100
    summary = runner.execution_summary([settled])
    assert summary['settled_response_count'] == 0 and summary['new_unknown'] == 1
    assert summary['actual_calls'] is None and summary['all_actual_tokens'] is None


def test_installed_fence_preserves_existing_gate_and_stops_after_audit_failure(wire):
    calls=[]; gate=object()
    class Transport:
        @asynccontextmanager
        async def open(self,*args,**kwargs):
            calls.append('transport'); yield object()
    transport=Transport(); transport.gate=gate
    async def audit(): calls.append('audit'); raise PermissionError('S85_USAGE_UNKNOWN_STOP')
    wire.fence.audit=audit
    model=wire.fence.install(SimpleNamespace(transport=transport))
    async def scenario():
        with pytest.raises(PermissionError, match='USAGE_UNKNOWN_STOP'):
            async with model.transport.open(wire.cfg['cheap'],'/chat/completions',wire.body,
                    wire.ctx.child('bridge-extract'),capability='chat'): pass
        with pytest.raises(PermissionError, match='EXACT_OPERATION_DENIED'):
            async with model.transport.open(wire.cfg['cheap'],'/chat/completions',wire.body,
                    wire.ctx.child('bridge-extract'),capability='chat'): pass
    asyncio.run(scenario())
    assert calls==['transport','audit'] and model.transport.gate is gate
    assert wire.fence.stopped and not wire.fence.completed


def test_installed_fence_preserves_typed_cancellation_when_audit_also_fails(wire):
    class Transport:
        @asynccontextmanager
        async def open(self,*args,**kwargs):
            raise CallCancelled(sent=True)
            yield
    async def audit(): raise PermissionError('S85_USAGE_UNKNOWN_STOP')
    wire.fence.audit=audit
    model=wire.fence.install(SimpleNamespace(transport=Transport()))
    async def scenario():
        with pytest.raises(CallCancelled):
            async with model.transport.open(wire.cfg['cheap'],'/chat/completions',wire.body,
                    wire.ctx.child('bridge-extract'),capability='chat'): pass
    asyncio.run(scenario())


def test_stage_receipts_exclude_headers_and_have_stable_hashes(tmp_path):
    evidence=tmp_path/'evidence'; evidence.mkdir()
    raw=evidence/'op.request.json'
    raw.write_text(json.dumps({'headers_saved':False,'request_id':RID}),encoding='utf-8')
    manifest=runner.receipt_manifest(tmp_path)
    assert manifest==[{'path':str(raw),'bytes':raw.stat().st_size,'sha256':runner.sha(raw.read_bytes())}]
    raw.write_text(json.dumps({'headers_saved':True}),encoding='utf-8')
    with pytest.raises(ValueError, match='HEADERS_FORBIDDEN'): runner.receipt_manifest(tmp_path)


def test_exclusive_writes_refuse_to_replace_a_prior_stage_claim(tmp_path):
    claim=tmp_path/'run.claim'
    runner.write_new(claim,{'grant_id':'s85-'+STAGE})
    with pytest.raises(FileExistsError): runner.write_new(claim,{'grant_id':'other'})
    assert runner.read(claim)=={'grant_id':'s85-'+STAGE}


def session_owner(case_id='frost-A', *, index=1, references=(), stage=None):
    value=owner()
    value.update(schema='s85-real-authorization/v2',
        approval_source='Latest owner continuing authorization, preserved verbatim below',
        authorization_basis='current-owner-continuation', session_id=runner.SESSION_ID,
        session_envelope=deepcopy(runner.SESSION_ENVELOPE), case_id=case_id,
        attempt_index=index, prior_receipts=deepcopy(list(references)),
        continuation_reason='Fresh bounded diagnostic after reviewed implementation change.')
    if stage is not None:
        value['stage_id']=stage; value['grant_id']='s85-'+stage
    value['envelope']=runner.attempt_envelope(value)
    return value


def prior_reference(stage=STAGE):
    return {'stage_id':stage, **{name+'_sha256':'0'*64 for name in ('final','closure','preflight','authorization')}}


def successful_session_a():
    previous=owner()
    previous.update(session_id=runner.SESSION_ID, session_envelope=deepcopy(runner.SESSION_ENVELOPE),
                    case_id='frost-A', attempt_index=1, prior_receipts=[])
    row,entry=settlement(); rr,re=settlement(RRID,'rerank')
    entries=[entry,re]
    summary=runner.execution_summary(entries)
    trace=[{'decision':'FIRST_HOP_VERIFIED','extraction':{'relation_verified':True},
            'identity_proof':{'verified':True}}, {'decision':'BRIDGE_QUERY_VERIFIED'},
           {'decision':'SECOND_HOP_LOCATION_FOUND'}, {'decision':'ENTITY_BRIDGE_UNVERIFIED'},
           {'decision':'CONTEXT_ADMITTED'}, {'decision':'FINAL_ANSWER_UNVERIFIED'}]
    final={'status':'S85_REAL_DIAGNOSTIC_COMPLETE','ledger_status':'CLOSED',
           'case':{'case_id':'frost-A','trace':trace,'final_answer_supported':False},
           'envelope':deepcopy(runner.ENVELOPE), 'execution_entries':entries,
           'final_answer_generation_calls':0,'embedding_calls':0, **summary}
    closure={'status':'CLOSED','historical_authorizations_reused':False, **summary}
    preflight={'source_inputs_sha256':runner.FROZEN_SHA256,
               'owner_authorization_sha256':runner.sha(runner.canonical(previous)),
               'implementation_sha256':{'backend/weknora_port/retrieval/bridge_rescue.py':'a'*64},
               'current_case':{'payload':{'contract_version':runner.CONTRACT}}}
    return {'stage_id':STAGE,'authorization':previous,'final':final,'closure':closure,'preflight':preflight}


def test_v2_latest_owner_wording_and_partitioned_session_bounds_preserve_v1():
    value=session_owner()
    assert runner.validate_owner(value,RAW)==PREFIX
    assert runner.validate_owner(owner(),RAW)==PREFIX
    summary=runner.validate_continuation(value,[],{},RAW)
    assert summary['session_envelope']=={'max_calls':40,'max_tokens':200000,'max_bridge_attempts':6,'max_conversation_turns':7}
    assert summary['bridge_subbudget']=={'max_calls':12,'max_tokens':60000,'max_attempts':6}
    assert summary['prior_ledgers_modified'] is False
    assert runner.BRIDGE_SESSION_CALLS+28==40 and runner.BRIDGE_SESSION_TOKENS+140000==200000


@pytest.mark.parametrize('change',['session_id','session_cap','index','history_count','empty_reason','envelope','basis'])
def test_v2_rejects_session_budget_history_and_authorization_widening(change):
    value=session_owner()
    if change=='session_id': value['session_id']='historical-session'
    elif change=='session_cap': value['session_envelope']['max_tokens']=200001
    elif change=='index': value['attempt_index']=7
    elif change=='history_count': value['prior_receipts']=[prior_reference()]
    elif change=='empty_reason': value['continuation_reason']=''
    elif change=='envelope': value['envelope']['max_calls']=3
    else: value['authorization_basis']='historical-grant-reuse'
    with pytest.raises(ValueError): runner.validate_owner(value,RAW)


def test_v2_b_requires_prior_full_a_chain_but_not_false_event_venue_support():
    record=successful_session_a(); value=session_owner('frost-B',index=2,
        references=[prior_reference()],stage='20261021-'+'b'*32)
    assert runner.validate_inputs(RAW,'frost-B')['arm']=='B'
    summary=runner.validate_continuation(value,[record],{},RAW)
    assert summary['next_case_id']=='frost-B' and summary['prior_attempted_operations']==2
    assert record['final']['case']['final_answer_supported'] is False
    fence=runner.RequestFence(runner.request_plan('s85-'+value['stage_id']+'/','frost-B'),ROLES,{})
    assert fence.case_id=='frost-B'


@pytest.mark.parametrize('change',['no_a','missing_rerank','missing_identity','missing_context','generation'])
def test_v2_b_rejects_incomplete_or_out_of_contract_a(change):
    record=successful_session_a()
    if change=='no_a':
        value=session_owner('frost-B')
        with pytest.raises(ValueError,match='CONDITIONAL_B'): runner.validate_continuation(value,[],{},RAW)
        return
    if change=='missing_rerank':
        record['final']['execution_entries']=record['final']['execution_entries'][:1]
        summary=runner.execution_summary(record['final']['execution_entries'])
        record['final'].update(summary); record['closure'].update(summary)
    elif change=='missing_identity': record['final']['case']['trace'][0]['identity_proof']['verified']=False
    elif change=='missing_context': record['final']['case']['trace']=[item for item in record['final']['case']['trace'] if item['decision']!='CONTEXT_ADMITTED']
    else: record['final']['final_answer_generation_calls']=1
    value=session_owner('frost-B',index=2,references=[prior_reference()],stage='20261021-'+'b'*32)
    with pytest.raises(ValueError): runner.validate_continuation(value,[record],{},RAW)


def test_v2_same_case_requires_a_real_nonrunner_implementation_change():
    record=successful_session_a(); value=session_owner(index=2,references=[prior_reference()],stage='20261021-'+'b'*32)
    with pytest.raises(ValueError,match='IMPLEMENTATION_CHANGE'):
        runner.validate_continuation(value,[record],{'scripts/s8_5_bridge_ecs.py':'new-runner-only'},RAW)
    result=runner.validate_continuation(value,[record],{'backend/weknora_port/retrieval/bridge_rescue.py':'b'*64},RAW)
    assert result['next_attempt_index']==2 and result['prior_ledgers_modified'] is False


def test_v2_a_repair_after_known_cheap_only_pool_miss_needs_changed_implementation():
    record=successful_session_a()
    record['final']['execution_entries']=record['final']['execution_entries'][:1]
    record['final']['case']['trace']=[item for item in record['final']['case']['trace']
                                    if item['decision'] not in ('SECOND_HOP_LOCATION_FOUND','CONTEXT_ADMITTED')]
    record['final']['case']['trace'].append({'decision':'BRIDGE_EXTRACTION_UNVERIFIED',
                                            'reason':'SECOND_HOP_SOURCE_NOT_IN_POOL'})
    summary=runner.execution_summary(record['final']['execution_entries'])
    record['final'].update(summary); record['closure'].update(summary)
    before=deepcopy(record)
    value=session_owner(index=2,references=[prior_reference()],stage='20261021-'+'b'*32)
    result=runner.validate_continuation(value,[record],
        {'backend/weknora_port/retrieval/bridge_rescue.py':'fixed-prefix-matching'},RAW)
    assert result['next_case_id']=='frost-A' and result['prior_attempted_operations']==1
    assert result['prior_possibly_sent_unknown']==0 and record==before


def test_v2_unknown_prior_keeps_full_exposure_and_requires_diagnosis_for_fresh_repair():
    record=successful_session_a()
    entry=record['final']['execution_entries'][0]
    entry.update(status='UNKNOWN',outcome='UNKNOWN',usage=None,reserved_tokens=10000,held_tokens=10000)
    record['final']['execution_entries']=[entry]
    summary=runner.execution_summary([entry])
    record['final'].update(summary,status='S85_REAL_STOP',ledger_status='STOP',error='MODEL_PROVIDER_CONNECTION_FAILED')
    record['closure'].update(summary,status='STOP')
    before=deepcopy(record)
    value=session_owner(index=2,references=[prior_reference()],stage='20261021-'+'b'*32)
    implementation={'backend/weknora_port/retrieval/bridge_rescue.py':'b'*64}
    with pytest.raises(ValueError,match='STOP_DIAGNOSIS'): runner.validate_continuation(value,[record],implementation,RAW)
    value['prior_stop_diagnosis']='Affected request isolated; raw UNKNOWN and reservation retained, new implementation reviewed.'
    result=runner.validate_continuation(value,[record],implementation,RAW)
    assert result['prior_held_token_exposure']==10000 and result['prior_possibly_sent_unknown']==1
    assert record==before and record['closure']['status']=='STOP' and entry['held_tokens']==10000


def test_prior_receipt_loader_reads_only_pinned_files_and_rejects_mutated_hash(tmp_path,monkeypatch):
    prefix=str(tmp_path/'s8-5-bridge-')
    monkeypatch.setattr(runner,'ROOT_PREFIX',prefix)
    monkeypatch.setattr(runner,'validate_root',lambda root,stage: runner.require(str(root)==prefix+stage,'TEST_ROOT_CHANGED'))
    record=successful_session_a(); root=Path(prefix+STAGE)
    files={'final':'final.json','closure':'authorization-closure.json','preflight':'preflight.json',
           'authorization':'payload/owner-authorization.json'}
    reference={'stage_id':STAGE}
    for name,relative in files.items():
        path=root/relative; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(record[name]),encoding='utf-8')
        reference[name+'_sha256']=runner.sha(path.read_bytes())
    value=session_owner('frost-B',index=2,references=[reference],stage='20261021-'+'b'*32)
    assert runner.load_prior_receipts(value)==[record]
    (root/'final.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='HASH_CHANGED'): runner.load_prior_receipts(value)
