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
