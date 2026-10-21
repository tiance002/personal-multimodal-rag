"""Generation-only bridge probe contracts; no external IO or real calls."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import s8_5_bridge_answer_ecs as runner
from backend.weknora_port.models import provider_config
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.types import SearchResult

STAGE = '20261021-' + 'a'*32
SOURCE = '20261021-' + 'b'*32
RUN = '12345678-1234-4234-8234-123456789abc'
RAW = (Path(__file__).parent/'fixtures/s8_4_bridge/inputs.json').read_bytes()


def owner():
    return {'schema': 's85-bridge-answer-authorization/v1', 'approved': True,
        'approval_source': 'Owner current continuation', 'owner_message': 'Synthetic test-only authorization',
        'stage_id': STAGE, 'grant_id': 's85g-'+STAGE, 'case_id': 'frost-A', 'probe_index': 1,
        'session_id': runner.bridge.SESSION_ID, 'session_envelope': deepcopy(runner.bridge.SESSION_ENVELOPE),
        'continuation_addon_envelope':deepcopy(runner.ADDON_ENVELOPE),
        'prior_conversation_receipts':[{'stage_id':'20261021-'+'c'*32,'final_sha256':'0'*64,'authorization_sha256':'1'*64}],
        'envelope': deepcopy(runner.ENVELOPE), 'source_inputs_sha256': runner.bridge.FROZEN_SHA256,
        'source_stage_id': SOURCE, 'prior_receipts': [], 'prior_answer_receipts': []}


def test_import_does_not_connect(monkeypatch):
    monkeypatch.setattr(runner.conversation, 'runtime_settings', lambda *a: pytest.fail('No import-time runtime'))
    importlib.reload(runner)


@pytest.mark.parametrize('change', ['schema', 'grant_id', 'approved', 'envelope', 'source_inputs_sha256', 'probe_index'])
def test_fresh_exact_generation_only_grant(change):
    value = owner()
    value[change] = 'wrong'
    with pytest.raises(ValueError): runner.validate_owner(value, RAW)


def test_B_requires_A_review_and_fresh_settled_B_chain():
    value = owner(); value.update(case_id='frost-B', probe_index=2)
    with pytest.raises(ValueError, match='PRIOR_ANSWER'):
        runner.validate_owner(value, RAW)
    source = {'stage_id': SOURCE, 'final': {'status': 'S85_REAL_STOP', 'new_unknown': 1}}
    with pytest.raises(ValueError): runner.validate_source(source, 'frost-B')
    source['final'].update(status='S85_REAL_DIAGNOSTIC_COMPLETE', ledger_status='CLOSED', new_unknown=0,
        execution_entries=[], case={'case_id': 'frost-B', 'trace': [], 'finalized_context': {}})
    with pytest.raises(ValueError): runner.validate_source(source, 'frost-B')


def test_each_probe_ledger_allows_only_one_cheap_generation():
    role = {'provider': 'siliconflow', 'model': 'Qwen/Qwen3.5-4B', 'model_id': 'cheap', 'capability': 'chat'}
    ledger = runner.ledger_spec(SimpleNamespace(tenant_id=332, subject_id='owner'), RUN, role)
    assert ledger['max_calls'] == 1 and ledger['max_tokens'] == 10000
    assert ledger['allowed_models'] == [role]
    assert ledger['allowed_request_prefixes'] == [RUN+'/answer/1/cheap']
    assert runner.ENVELOPE['max_probes'] == 2 and runner.ENVELOPE['max_calls'] == 2


def test_normal_projection_never_adds_same_document_anchor_or_generated_descriptions():
    raw = SearchResult('selected', 'She directed the Seminar.', .8, knowledge_id='doc', knowledge_base_id='kb',
        start_at=0, end_at=25, metadata={'source_version':'v'}, chunk_metadata={'source_version':'v'},
        knowledge_description='Generated institution description')
    summary = replace(raw, id='generated-summary', chunk_type='summary', content='Invented biography')
    state = PipelineState(merge_results=[summary, deepcopy(raw)])
    seen = []

    async def originals(ids, context):
        seen.extend(ids)
        assert ids == ('selected',)
        return {'selected': deepcopy(raw)}

    class Repo:
        get_current_chunks = staticmethod(originals)
        async def get_source_unit(self, *args): pytest.fail('No source-window expansion')
        async def get_chunks(self, *args): pytest.fail('No unrelated identity anchor')

    projected = asyncio.run(runner.project_selected(state, None, Repo()))
    assert seen == ['selected'] and [row.id for row in state.merge_results] == ['selected']
    assert state.merge_results[0].knowledge_description == ''
    assert projected['dropped_generated_ids'] == ['generated-summary']
    assert projected['added_unselected_ids'] == []


def test_projection_fails_on_quote_disappearance_and_pointer_ancestry_budget():
    raw = SearchResult('selected', 'Original text.', 1, knowledge_id='doc', knowledge_base_id='kb',
        start_at=2, end_at=16, metadata={'source_version':'v'}, chunk_metadata={'source_version':'v'})
    state = PipelineState(merge_results=[replace(raw, content='Untraceable text')])
    async def originals(ids, context): return {'selected': raw}
    repo = SimpleNamespace(get_current_chunks=originals)
    with pytest.raises(ValueError, match='SELECTED_QUOTE'):
        asyncio.run(runner.project_selected(state, None, repo))


def test_cached_response_is_not_counted_as_new_usage_and_requires_exact_wire():
    cfg = provider_config('siliconflow', 'Qwen/Qwen3.5-4B', model_id='cheap')
    body = {'model':cfg.model, 'messages':[{'role':'user','content':'original'}], 'max_tokens':256,
            'enable_thinking':False}
    env = {'model':cfg.model, 'choices':[{'message':{'content':'{}'}, 'finish_reason':'stop'}],
           'usage':{'prompt_tokens':100,'completion_tokens':2,'total_tokens':102}}
    cached = runner.CachedOutputs({'chat': {'config':cfg, 'request': {'body':body}, 'raw':env}}, 'case')
    async def run():
        model = await cached.chat('cheap', None)
        response = await model.chat_wire(body)
        assert response.usage.total_tokens == 102
        with pytest.raises(ValueError, match='CACHED_WIRE'):
            await model.chat_wire({**body,'max_tokens':257})
    asyncio.run(run())
    assert cached.new_provider_calls == 0 and cached.new_usage_added == 0


def test_unknown_exposure_remains_held_in_generation_budget():
    records = [{'final': {'execution_entries':[{'request_id':'old/unknown','capability':'chat','status':'UNKNOWN','held_tokens':6365,'reserved_tokens':6365}]}}]
    result = runner.budget_summary(records, [])
    assert result['bridge_prior_attempts'] == 1 and result['bridge_prior_held_tokens'] == 6365
    with pytest.raises(ValueError): runner.budget_summary(records*12, [])


def test_addon_accounts_C2_and_reserves_repair_plus_both_answer_probes():
    def entry(rid,tokens,status='SETTLED'):
        return {'request_id':rid,'capability':'chat','status':status,'held_tokens':tokens,'reserved_tokens':tokens,
            'outcome':'RESPONSE_RECEIVED','usage':{'status':'REPORTED','prompt_tokens':tokens,'completion_tokens':0,'total_tokens':tokens}}
    bridge_entries = [entry('bridge/'+str(i),1) for i in range(6)]+[entry('bridge/unknown',6365,'UNKNOWN')]
    bridge_entries[0]['held_tokens'] = bridge_entries[0]['reserved_tokens'] = 5592
    bridge_entries[0]['usage']['prompt_tokens'] = bridge_entries[0]['usage']['total_tokens'] = 5592
    c2_entries = [entry('c2/'+str(i),1) for i in range(19)]
    c2_entries[0]['held_tokens'] = c2_entries[0]['reserved_tokens'] = 27499
    c2_entries[0]['usage']['prompt_tokens'] = c2_entries[0]['usage']['total_tokens'] = 27499
    result = runner.budget_summary([{'final':{'execution_entries':bridge_entries}}],[],[{'execution_entries':c2_entries}])
    assert result['aggregate_attempts_upper']==56 and result['aggregate_token_exposure_upper']==199479
    assert result['prior_unknown_operations']==1
    with pytest.raises(ValueError,match='DUPLICATE_PRIOR'):
        runner.budget_summary([{'final':{'execution_entries':bridge_entries}}],[],[{'execution_entries':bridge_entries}])


def test_generation_fence_preserves_original_q0_and_denies_all_other_operations():
    from backend.weknora_port.models.request import build_chat_body
    from backend.weknora_port.models.types import ChatOptions, Message
    cfg = provider_config('siliconflow', 'Qwen/Qwen3.5-4B', model_id='cheap')
    body = build_chat_body(cfg, [Message('user','Original question and qualified originals')],
        ChatOptions(max_completion_tokens=1024, thinking=False), stream=True)
    ctx = CallContext(RUN, TrustedScope('owner',('kb',),('doc',)), session_id='session')
    fence = runner.GenerationFence(ctx, cfg, body)
    for suffix in ('query_rewrite','rerank','answer/1/expensive','answer/2/cheap','bridge-extract'):
        with pytest.raises(ValueError): fence.validate(cfg, body, ctx.child(suffix), 'chat')
    fence.validate(cfg, body, ctx.child('answer/1/cheap'), 'chat')
    with pytest.raises(ValueError): fence.validate(cfg, body, ctx.child('answer/1/cheap'), 'chat')


def test_generation_fence_denies_altered_context_scope_or_output_cap():
    from backend.weknora_port.models.request import build_chat_body
    from backend.weknora_port.models.types import ChatOptions, Message
    cfg = provider_config('siliconflow', 'Qwen/Qwen3.5-4B', model_id='cheap')
    body = build_chat_body(cfg, [Message('user','Original')], ChatOptions(max_completion_tokens=1024, thinking=False), stream=True)
    ctx = CallContext(RUN, TrustedScope('owner',('kb',),('doc',)), session_id='session')
    for changed in ({**body,'max_tokens':1025}, {**body,'messages':[]}):
        with pytest.raises(ValueError): runner.GenerationFence(ctx,cfg,body).validate(cfg,changed,ctx.child('answer/1/cheap'),'chat')
    with pytest.raises(ValueError):
        runner.GenerationFence(ctx,cfg,body).validate(cfg,body,replace(ctx.child('answer/1/cheap'),required_origin_kbs=('other',)),'chat')


def test_answer_settlement_has_exact_one_key_and_actual_usage_only():
    role = {'provider':'siliconflow','model':'Qwen/Qwen3.5-4B','model_id':'cheap','capability':'chat'}
    pg = {**role,'request_id':RUN+'/answer/1/cheap','wire_sha256':'a'*64,'outcome':'RESPONSE_RECEIVED',
        'finished_at':'now','usage':{'status':'REPORTED','prompt_tokens':100,'completion_tokens':10,'total_tokens':110}}
    entry = {**deepcopy(pg),'status':'SETTLED','held_tokens':110}
    assert runner.validate_answer_settlement([pg],[entry],RUN,role)['total_tokens'] == 110
    for changed in ({**entry,'status':'UNKNOWN'}, {**entry,'held_tokens':111}, {**entry,'model_id':'other'}):
        with pytest.raises(ValueError): runner.validate_answer_settlement([pg],[changed],RUN,role)
    with pytest.raises(ValueError,match='DUPLICATE_PG'):
        runner.validate_answer_settlement([pg,pg],[entry],RUN,role)


@pytest.mark.parametrize('case_id',['frost-A','frost-B'])
def test_frozen_selected_source_replay_and_projection_keep_event_unverified(monkeypatch,case_id):
    """Offline contract replay; literal candidate equals saved A2/B3 output."""
    import backend.weknora_port.runtime as runtime
    from backend.weknora_port.retrieval.bridge_rescue import original_windows,extraction_payload
    from backend.weknora_port.models.request import build_rerank_body
    roles = {'cheap':{'provider':'siliconflow','model':'Qwen/Qwen3.5-4B','model_id':'cheap','capability':'chat'},
             'rerank':{'provider':'siliconflow','model':'BAAI/bge-reranker-v2-m3','model_id':'rerank','capability':'rerank'}}
    configs = {cap:provider_config('siliconflow',roles[name]['model'],model_id=name)
               for cap,name in (('chat','cheap'),('rerank','rerank'))}
    case = runner.bridge.validate_inputs(RAW,case_id)
    sources = {r['id']:SearchResult(**r) for r in case['source_rows'] if r['chunk_type']=='text'}
    case['document_ids'] = sorted({r.knowledge_id for r in sources.values()})
    context = CallContext(RUN,TrustedScope('owner',tuple(case['knowledge_base_ids']),tuple(case['document_ids'])))
    state = runner.historical.build_state(deepcopy(case),roles)
    class Repository:
        async def get_current_chunks(self,ids,context): return {key:deepcopy(sources[key]) for key in ids if key in sources}
        async def get_chunks(self,ids,context): return {key:deepcopy(sources[key]) for key in ids if key in sources}
        async def get_source_unit(self,*args): pytest.fail('Generation probe may not expand a window')
    repository = Repository()
    windows,selected,chains = asyncio.run(original_windows([sources['ec07a1fd-8a8e-4f23-a7d6-65d385d2343b']],state,context,repository))
    payload = extraction_payload(case['rewrite_query'] or case['query'],windows,selected)
    candidate = {'status':'FIRST_HOP_CANDIDATE','sentence_id':'s-c9a81754f7673886cbb035bf',
        'subject':'She','subject_resolution':'UNVERIFIED','predicate':'served as the Director of',
        'object':'The Frost Place Advanced Seminar','time':'2007–2010','target_attribute':'location',
        'bridge_search_phrases':['The Frost Place Advanced Seminar']}
    chat_env = {'model':configs['chat'].model,'choices':[{'message':{'content':json.dumps(candidate,ensure_ascii=False)},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':1210,'completion_tokens':132,'total_tokens':1342}}
    location = sources['31417c7c-7abf-4723-956a-74bf7ee14683']
    cached = {'chat':{'config':configs['chat'],'request':{'body':runner.bridge.expected_chat_body(configs['chat'],payload)},'raw':chat_env},
        'rerank':{'config':configs['rerank'],'request':{'body':build_rerank_body(configs['rerank'],
            'Where is The Frost Place Advanced Seminar located?',[location.content])},
            'raw':{'results':[{'index':0,'relevance_score':.989133894443512}],
                   'meta':{'tokens':{'input_tokens':47,'output_tokens':0,'image_tokens':0}}}}}
    record = {'stage_id':SOURCE,'preflight':{'registry_roles':roles,'subject_sha256':runner.sha('owner'),
        'current_case':{'sources':{key:asdict(row) for key,row in sources.items()}}},
        'final':{'usage_by_capability':{'historical_only':True}}}
    monkeypatch.setattr(runtime,'LeasedKnowledgeRepository',lambda *a:repository)
    monkeypatch.setattr(runner,'load_cached',lambda *a:deepcopy(cached))
    import backend.weknora_port.retrieval.bridge_rescue as bridge_rescue
    rescue = bridge_rescue.rescue_bridge
    captured = {}
    async def observe(state,*args):
        captured['state'] = state
        return await rescue(state,*args)
    monkeypatch.setattr(bridge_rescue,'rescue_bridge',observe)
    try:
        new_state,finalized,rows,replay = asyncio.run(runner.replay_context(
            SimpleNamespace(principal=SimpleNamespace(subject_id='owner')),None,record,case,configs,context))
    except ValueError:
        raise AssertionError(captured.get('state').bridge_trace) from None
    assert replay['cached_usage_added_to_new_ledger'] == 0
    assert all(row.chunk_type=='text' and not row.knowledge_description for row in rows)
    assert 'generated_summary' not in finalized.rendered_contexts and '# Summary' not in finalized.rendered_contexts
    assert 'seminar_venue="unverified"' in finalized.rendered_contexts
    assert replay['projection']['added_unselected_ids'] == []
    if case_id == 'frost-B':
        assert 'abc3a6aa-5453-4f0a-b7b6-5270cc96067f' not in replay['projection']['projected_ids']
        first = next(item for item in replay['trace'] if item.get('decision')=='FIRST_HOP_VERIFIED')
        assert first['identity_proof']['final_context_identity_supported'] is False


def test_raw_receipt_files_are_complete_hash_bound_and_usage_reconciled(tmp_path,monkeypatch):
    cheap = provider_config('siliconflow','Qwen/Qwen3.5-4B',model_id='cheap')
    rerank = provider_config('siliconflow','BAAI/bge-reranker-v2-m3',model_id='rerank')
    roles = {name:{'provider':cfg.provider,'model':cfg.model,'model_id':cfg.model_id,'capability':cap}
             for name,cap,cfg in (('cheap','chat',cheap),('rerank','rerank',rerank))}
    monkeypatch.setattr(runner,'EXECUTION_ROOT',str(tmp_path))
    folder = tmp_path/('s85-'+SOURCE)/'evidence'; folder.mkdir(parents=True)
    stem = '1'*32; rid = 's85-'+SOURCE+'/frost-A/bridge-extract'
    body = {'model':cheap.model,'messages':[{'role':'user','content':'original'}],'max_tokens':256,'enable_thinking':False}
    raw = {'model':cheap.model,'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
           'usage':{'prompt_tokens':100,'completion_tokens':2,'total_tokens':102}}
    pg = {**roles['cheap'],'request_id':rid,'wire_sha256':runner.sha(runner.canonical(body)),
        'outcome':'RESPONSE_RECEIVED','finished_at':'now','usage':{'status':'REPORTED','prompt_tokens':100,'completion_tokens':2,'total_tokens':102}}
    entry = {**deepcopy(pg),'status':'SETTLED','held_tokens':102}
    payloads = {'.request.json':{**roles['cheap'],'request_id':rid,'headers_saved':False,'body':body,
        'scope':{'owner_id':'owner','knowledge_base_ids':['kb'],'document_ids':[]},'required_origin_kbs':[]},
        '.response.body':raw,'.response.json':{'complete':True,'status_code':200},'.timing.json':{}}
    refs = []
    for suffix,value in payloads.items():
        path = folder/(stem+suffix); runner.write_new(path,value)
        refs.append({'path':str(path),'bytes':path.stat().st_size,'sha256':runner.sha(path.read_bytes())})
    record = {'stage_id':SOURCE,'preflight':{'registry_roles':roles,'subject_sha256':runner.sha('owner'),
        'current_case':{'sources':{'source':{'knowledge_base_id':'kb','knowledge_id':'doc'}}}},
        'final':{'case':{'case_id':'frost-A'},'receipt_files':refs,'provider_rows':[pg],'execution_entries':[entry]}}
    result = runner.load_cached(record,{'chat':cheap,'rerank':rerank})
    assert set(result)=={'chat'} and result['chat']['raw']==raw
    (folder/(stem+'.response.body')).write_bytes(b'{}')
    with pytest.raises(ValueError,match='RAW_RECEIPT_HASH_CHANGED'):
        runner.load_cached(record,{'chat':cheap,'rerank':rerank})
