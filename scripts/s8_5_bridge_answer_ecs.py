"""One new Cheap answer over receipt-bound, current-original bridge replay.

Import is inert. This is CACHED_REAL_OUTPUT_REPLAY_CURRENT_ORIGINAL_PROJECTION,
not fresh retrieval. Each fresh stage permits one generation; B requires settled
B evidence and a recorded review of the earlier A answer. Cached usage is never
added to the new PG/execution ledger. No failed operation is retried.
"""
from __future__ import annotations
import argparse
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid

from scripts import s8_4_bridge_ecs as historical
from scripts import s8_5_bridge_ecs as bridge
from scripts import s8_5_conversation_ecs as conversation

require, canonical, sha = historical.require, historical.canonical, historical.sha
read, write_new, safe_write = historical.read, historical.write_new, historical.safe_write
ROOT_PREFIX = '/opt/rag-s8/state/s8-5-bridge-answer-'
EXECUTION_ROOT = historical.EXECUTION_ROOT
LABEL = 'CACHED_REAL_OUTPUT_REPLAY_CURRENT_ORIGINAL_PROJECTION'
ADDON_ENVELOPE = {'max_calls':56,'max_tokens':200000,'max_repair_conversation_calls':28,
    'max_repair_conversation_tokens':140000,'max_answer_calls':2,'max_answer_tokens':20000}
ENVELOPE = {'max_probes': 2, 'max_calls': 2, 'max_tokens': 20000,
    'per_probe_max_calls': 1, 'per_probe_max_tokens': 10000, 'cheap_max_output_tokens': 1024,
    'provider': 'siliconflow', 'model': 'Qwen/Qwen3.5-4B', 'router_mode': 'CHEAP_ONLY',
    'rewrite_calls': 0, 'embedding_calls': 0, 'search_calls': 0, 'extraction_calls': 0,
    'rerank_calls': 0, 'expensive_calls': 0, 'retries': 0, 'history_rounds': 0, 'memory_enabled': False,
    'evidence_mode': LABEL, 'case_order': ['frost-A', 'frost-B']}


def validate_owner(owner, raw):
    require(isinstance(owner, dict) and owner.get('schema') == 's85-bridge-answer-authorization/v1'
        and owner.get('approved') is True and isinstance(owner.get('owner_message'), str) and owner['owner_message'].strip()
        and isinstance(owner.get('approval_source'), str) and owner['approval_source'].strip(), 'S85G_NEW_OWNER_REQUIRED')
    require(owner.get('envelope') == ENVELOPE and owner.get('session_id') == bridge.SESSION_ID
        and owner.get('session_envelope') == bridge.SESSION_ENVELOPE, 'S85G_EXACT_ENVELOPE_REQUIRED')
    require(owner.get('continuation_addon_envelope') == ADDON_ENVELOPE
        and isinstance(owner.get('prior_conversation_receipts'),list)
        and len(owner['prior_conversation_receipts']) == 1, 'S85G_PRIOR_CONVERSATION_AND_ADDON_REQUIRED')
    stage = owner.get('stage_id')
    require(isinstance(stage,str) and re.fullmatch(r'[0-9]{8}-[0-9a-f]{32}',stage)
        and owner.get('grant_id') == 's85g-'+stage, 'S85G_FRESH_STAGE_REQUIRED')
    case = owner.get('case_id')
    require(case in ('frost-A','frost-B') and type(owner.get('probe_index')) is int
        and owner['probe_index'] == (1 if case == 'frost-A' else 2), 'S85G_EXACT_PROBE_INDEX_REQUIRED')
    require(owner.get('source_inputs_sha256') == bridge.FROZEN_SHA256, 'S85G_FROZEN_INPUT_REQUIRED')
    bridge.validate_inputs(raw, case)
    require(isinstance(owner.get('prior_receipts'),list) and isinstance(owner.get('source_stage_id'),str)
        and re.fullmatch(r'[0-9]{8}-[0-9a-f]{32}',owner['source_stage_id']), 'S85G_PRIOR_BRIDGE_REQUIRED')
    prior = owner.get('prior_answer_receipts')
    require(isinstance(prior,list) and len(prior) == owner['probe_index']-1, 'S85G_PRIOR_ANSWER_REQUIRED')
    if case == 'frost-B':
        review = owner.get('a_answer_review', {})
        require(review.get('institution_event_boundary_checked') is True
            and isinstance(review.get('final_sha256'),str) and re.fullmatch('[0-9a-f]{64}',review['final_sha256']),
            'S85G_A_ANSWER_REVIEW_REQUIRED')
    return 's85g-'+stage


def validate_root(root, stage):
    path = PurePosixPath(str(root))
    require(str(root) == ROOT_PREFIX+stage and path.parent == PurePosixPath('/opt/rag-s8/state')
        and '\\' not in str(root) and '..' not in path.parts and root.resolve() == root,
        'S85G_PRIVATE_ROOT_REQUIRED')


def validate_source(record, case_id):
    final = record['final']
    require(final.get('status') == 'S85_REAL_DIAGNOSTIC_COMPLETE' and final.get('ledger_status') == 'CLOSED'
        and final.get('new_unknown') == 0 and final.get('old_history_roles_models_source_counts_preserved') is True,
        'S85G_SETTLED_SOURCE_REQUIRED')
    case = final.get('case', {})
    require(case.get('case_id') == case_id and case.get('first_hop_verified') is True
        and isinstance(case.get('finalized_context'),dict) and case['finalized_context'].get('result_ids')
        and any(item.get('decision') == 'FIRST_HOP_VERIFIED' and item.get('extraction',{}).get('relation_verified') is True
            and item.get('identity_proof',{}).get('verified') is True for item in case.get('trace',[])),
        'S85G_VERIFIED_SOURCE_CHAIN_REQUIRED')
    entries = final.get('execution_entries')
    require(isinstance(entries,list) and entries and len(entries) <= 2
        and all(row.get('status') == 'SETTLED' and row.get('outcome') == 'RESPONSE_RECEIVED' for row in entries),
        'S85G_SETTLED_SOURCE_REQUIRED')


def budget_summary(bridge_records, answer_records, conversation_records=()):
    groups = [bridge.execution_summary(record['final']['execution_entries']) for record in bridge_records]
    old = [bridge.execution_summary(record['final']['execution_entries']) for record in answer_records]
    conversations = [bridge.execution_summary(record['execution_entries']) for record in conversation_records]
    seen = set()
    for record in [*bridge_records,*answer_records,*conversation_records]:
        entries = record['execution_entries'] if 'execution_entries' in record else record['final']['execution_entries']
        for entry in entries:
            key = entry['request_id'],entry['capability']
            require(key not in seen,'S85G_DUPLICATE_PRIOR_OPERATION')
            seen.add(key)
    calls = sum(row['attempted_operations'] for row in groups+old)
    held = sum(row['held_tokens_total'] for row in groups+old)
    remaining = 2-len(old)
    require(remaining > 0 and calls+remaining <= 12 and held+10000*remaining <= 60000,
            'S85G_BRIDGE_OR_GLOBAL_BOUND_EXCEEDED')
    conversation_calls = sum(row['attempted_operations'] for row in conversations)
    conversation_held = sum(row['held_tokens_total'] for row in conversations)
    # Old 40-call authorizations remain immutable. The separately approved addon
    # accounts C2 and reserves the whole fresh repair plus remaining answer probes.
    require(calls+conversation_calls+remaining+28 <= ADDON_ENVELOPE['max_calls']
        and held+conversation_held+10000*remaining+140000 <= ADDON_ENVELOPE['max_tokens'], 'S85G_GLOBAL_BOUND_EXCEEDED')
    return {'bridge_prior_attempts': calls, 'bridge_prior_held_tokens': held,
        'prior_conversation_attempts':conversation_calls,'prior_conversation_held_tokens':conversation_held,
        'aggregate_attempts_upper':calls+conversation_calls+remaining+28,
        'aggregate_token_exposure_upper':held+conversation_held+10000*remaining+140000,
        'remaining_answer_calls_reserved':remaining,'remaining_answer_tokens_reserved':10000*remaining,
        'prior_unknown_operations': sum(row['possibly_sent_unknown_operations'] for row in groups+old+conversations),
        'next_probe_calls_reserved':1, 'next_probe_tokens_reserved':10000,
        'conversation_envelope_reserved':{'max_calls':28,'max_tokens':140000}}


def load_conversation_records(owner,raw):
    records = []
    for ref in owner['prior_conversation_receipts']:
        require(isinstance(ref,dict) and set(ref) == {'stage_id','final_sha256','authorization_sha256'},
                'S85G_PRIOR_CONVERSATION_REFERENCE_INVALID')
        stage = ref['stage_id']
        require(isinstance(stage,str) and re.fullmatch(r'[0-9]{8}-[0-9a-f]{32}',stage), 'S85G_PRIOR_CONVERSATION_REFERENCE_INVALID')
        root = Path(conversation.ROOT_PREFIX+stage)
        require(root.resolve() == root,'S85G_PRIOR_CONVERSATION_REDIRECT_DENIED')
        record = {'stage_id':stage}
        for key,relative in (('final','final.json'),('authorization','payload/owner-authorization.json')):
            path = root/relative
            require(path.is_file() and not path.is_symlink() and root in path.resolve().parents
                and sha(path.read_bytes()) == ref[key+'_sha256'],'S85G_PRIOR_CONVERSATION_HASH_CHANGED')
            record[key] = read(path)
        conversation.validate_owner(record['authorization'],raw)
        final = record['final']
        require(final.get('stage_prefix') == 's85c-'+stage and final.get('status') in ('S85C_STOP','S85C_COMPLETED_DIAGNOSTIC')
            and final.get('old_history_and_sources_unchanged') is True and final.get('envelope') == record['authorization']['envelope'],
            'S85G_PRIOR_CONVERSATION_CONTRACT_CHANGED')
        entries = []
        for turn in final['turns']:
            roles = {('cheap' if row['capability']=='chat' else row['capability']):row for row in turn['execution']['allowed_models']}
            conversation.validate_settlements(turn['provider_rows'],turn['execution']['entries'],turn['request_id'],roles)
            entries.extend(turn['execution']['entries'])
        summary = bridge.execution_summary(entries)
        require(summary['attempted_operations'] == final.get('attempted_operations')
            and summary['actual_reported_tokens_subtotal'] == final.get('actual_reported_tokens_subtotal')
            and summary['all_actual_tokens'] == final.get('all_actual_tokens')
            and summary['possibly_sent_unknown_operations'] == final.get('possibly_sent_unknown_operations'),
            'S85G_PRIOR_CONVERSATION_ACCOUNTING_CHANGED')
        record['execution_entries'] = entries
        records.append(record)
    return records


def load_answer_records(owner, raw):
    records = []
    for ref in owner['prior_answer_receipts']:
        require(isinstance(ref,dict) and set(ref) == {'stage_id','final_sha256','authorization_sha256'}, 'S85G_PRIOR_ANSWER_REFERENCE_INVALID')
        stage = ref['stage_id']
        require(isinstance(stage,str) and re.fullmatch(r'[0-9]{8}-[0-9a-f]{32}',stage), 'S85G_PRIOR_ANSWER_REFERENCE_INVALID')
        root = Path(ROOT_PREFIX+stage)
        require(root.resolve() == root, 'S85G_PRIOR_ANSWER_REDIRECT_DENIED')
        record = {'stage_id':stage}
        for key, relative in (('final','final.json'),('authorization','payload/owner-authorization.json')):
            path = root/relative
            require(path.is_file() and not path.is_symlink() and root in path.resolve().parents
                and sha(path.read_bytes()) == ref[key+'_sha256'], 'S85G_PRIOR_ANSWER_HASH_CHANGED')
            record[key] = read(path)
        validate_owner(record['authorization'],raw)
        final = record['final']
        require(record['authorization']['case_id'] == 'frost-A' and final.get('status') == 'S85G_COMPLETE'
            and final.get('ledger_status') == 'CLOSED' and final.get('new_unknown') == 0
            and final.get('old_history_and_sources_unchanged') is True
            and owner['a_answer_review']['final_sha256'] == ref['final_sha256'], 'S85G_COMPLETED_A_ANSWER_REQUIRED')
        records.append(record)
    return records


def load_bridge_records(owner, raw):
    records = bridge.load_prior_receipts(owner)
    for index, record in enumerate(records):
        auth, final, pre, closure = (record[key] for key in ('authorization','final','preflight','closure'))
        prefix = bridge.validate_owner(auth,raw)
        require(auth.get('attempt_index') == index+1 and auth.get('prior_receipts') == owner['prior_receipts'][:index]
            and auth.get('session_id') == bridge.SESSION_ID and pre.get('owner_authorization_sha256') == sha(canonical(auth))
            and pre.get('source_inputs_sha256') == bridge.FROZEN_SHA256, 'S85G_PRIOR_BRIDGE_CHAIN_CHANGED')
        summary = bridge.execution_summary(final['execution_entries'])
        require(all(final.get(key) == value and closure.get(key) == value for key,value in summary.items())
            and final.get('ledger_status') == closure.get('status') and final['ledger_status'] in ('CLOSED','STOP'),
            'S85G_PRIOR_BRIDGE_ACCOUNTING_CHANGED')
        keys = [(row['request_id'],row['capability']) for row in final['execution_entries']]
        plan = bridge.request_plan(prefix,bridge.attempt_case(auth))
        require(len(set(keys)) == len(keys) and all(plan.get(rid) == cap for rid,cap in keys), 'S85G_PRIOR_OPERATION_CHANGED')
    return records


def load_cached(record, configs):
    """Read only final-hash-bound files in the original fixed execution directory."""
    final, stage = record['final'], record['stage_id']
    base = Path(EXECUTION_ROOT)/('s85-'+stage)/'evidence'
    require(base.resolve() == base, 'S85G_SOURCE_RECEIPT_REDIRECT_DENIED')
    paths = {}
    for ref in final.get('receipt_files',[]):
        path = Path(ref['path'])
        require(path.parent == base and not path.is_symlink() and path.is_file()
            and re.fullmatch(r'[0-9a-f]{32}\.(request\.json|response\.body|response\.json|timing\.json)',path.name)
            and path.stat().st_size == ref['bytes'] and sha(path.read_bytes()) == ref['sha256'], 'S85G_RAW_RECEIPT_HASH_CHANGED')
        require(path.name not in paths, 'S85G_DUPLICATE_RECEIPT')
        paths[path.name] = path
    cached = {}
    bridge.validate_settlements(final['provider_rows'],final['execution_entries'],
        bridge.request_plan('s85-'+stage+'/',final['case']['case_id']),record['preflight']['registry_roles'])
    for path in paths.values():
        if not path.name.endswith('.request.json'): continue
        request = read(path); cap = request['capability']
        scope = request.get('scope',{})
        originals = record['preflight']['current_case']['sources'].values()
        kbs,docs = {row['knowledge_base_id'] for row in originals},{row['knowledge_id'] for row in originals}
        require(sha(scope.get('owner_id','')) == record['preflight']['subject_sha256']
            and set(scope.get('knowledge_base_ids',[])) == kbs
            and (not scope.get('document_ids') or docs.issubset(scope['document_ids']))
            and not request.get('required_origin_kbs'), 'S85G_RAW_SCOPE_BINDING_CHANGED')
        stem = path.name.removesuffix('.request.json')
        require(all(stem+suffix in paths for suffix in ('.response.body','.response.json','.timing.json')),
                'S85G_COMPLETE_RAW_RECEIPT_REQUIRED')
        response = read(paths[stem+'.response.json'])
        require(response.get('complete') is True and response.get('status_code') == 200 and request.get('headers_saved') is False,
                'S85G_RAW_RESPONSE_NOT_COMPLETE')
        entry = next((row for row in final['execution_entries'] if row['request_id'] == request['request_id'] and row['capability'] == cap),None)
        require(entry is not None and cap in ('chat','rerank') and cap not in cached
            and sha(canonical(request['body'])) == entry['wire_sha256']
            and all(request[name] == entry[name] == getattr(configs[cap],name) for name in ('provider','model','model_id')),
            'S85G_RAW_REQUEST_BINDING_CHANGED')
        raw = read(paths[stem+'.response.body'])
        from backend.weknora_port.models.provider import assert_response_identity, parse_chat_response
        from backend.weknora_port.models.usage import normalize_rerank_usage
        assert_response_identity(raw,configs[cap])
        usage = parse_chat_response(raw,configs[cap]).usage if cap == 'chat' else normalize_rerank_usage(raw,configs[cap].provider)
        require(usage.status == 'REPORTED' and all(getattr(usage,name) == entry['usage'][name]
            for name in ('prompt_tokens','completion_tokens','total_tokens')), 'S85G_RAW_USAGE_BINDING_CHANGED')
        cached[cap] = {'config':configs[cap], 'request':request, 'raw':raw}
    require(len(cached) == len(final['execution_entries']) and 'chat' in cached, 'S85G_RAW_OPERATION_SET_CHANGED')
    return cached


def scoped_case(record,raw,case_id):
    case = bridge.validate_inputs(raw,case_id)
    sources = record['preflight']['current_case']['sources']
    require(sources and all(row['knowledge_base_id'] in case['knowledge_base_ids'] for row in sources.values()),
            'S85G_SOURCE_SCOPE_CHANGED')
    # Frozen retrieval was KB-scoped. Generation is narrowed to its actual saved
    # original documents; no broad KB history or search is introduced here.
    case['document_ids'] = sorted({row['knowledge_id'] for row in sources.values()})
    return case


class CachedOutputs:
    """Explicit offline adapter over unmodified, settled real raw responses."""
    new_provider_calls = 0
    new_usage_added = 0
    def __init__(self,cached,case_id):
        self.cached,self.case_id,self.used = cached,case_id,set()
        self.prompt_comparison = {}
    async def chat(self,mid,context):
        require(mid == self.cached['chat']['config'].model_id, 'S85G_CACHED_MODEL_CHANGED')
        return self
    async def chat_wire(self,body):
        from backend.weknora_port.models.provider import parse_chat_response
        saved = self.cached['chat']
        require(body == saved['request']['body'], 'S85G_CACHED_WIRE_CHANGED')
        self.used.add('chat')
        return parse_chat_response(deepcopy(saved['raw']),saved['config'])
    async def rerank(self,*args):
        if len(args) == 2:
            mid,context = args
            require('rerank' in self.cached and mid == self.cached['rerank']['config'].model_id, 'S85G_UNRECORDED_CACHED_RERANK')
            return _CachedRerank(self)
        raise ValueError('S85G_CACHED_RERANK_CONTRACT')


class _CachedChat:
    def __init__(self,cache): self.cache = cache
    async def chat(self,messages,options,context):
        from backend.weknora_port.models.request import build_chat_body
        cache = self.cache; saved = cache.cached['chat']
        current = build_chat_body(saved['config'],messages,options)
        expected = deepcopy(saved['request']['body'])
        # Current prompt instructions may have been repaired. Only the unchanged
        # original user/sentence payload is replayed, never claimed newly sent.
        cache.prompt_comparison = {'saved_system_sha256':sha(canonical(expected['messages'][0])),
            'current_system_sha256':sha(canonical(current['messages'][0])),
            'new_prompt_sent':False,'sentence_payload_must_match':True}
        current['messages'][0] = expected['messages'][0]
        require('chat' not in cache.used and current == expected, 'S85G_CACHED_SENTENCE_INPUT_CHANGED')
        return await cache.chat_wire(expected)


class _CachedRerank:
    def __init__(self,cache): self.cache,self.config = cache,cache.cached['rerank']['config']
    async def rerank(self,q1,documents,context):
        from backend.weknora_port.models.request import build_rerank_body
        from backend.weknora_port.models.usage import normalize_rerank_usage
        from backend.weknora_port.models.types import RerankResponse,RerankResult
        cache = self.cache; saved = cache.cached['rerank']
        require('rerank' not in cache.used and build_rerank_body(self.config,q1,documents) == saved['request']['body'],
                'S85G_CACHED_RERANK_WIRE_CHANGED')
        cache.used.add('rerank')
        rows = saved['raw'].get('results')
        require(isinstance(rows,list), 'S85G_CACHED_RERANK_INVALID')
        return RerankResponse(tuple(RerankResult(row['index'],row.get('relevance_score',row.get('score')), '') for row in rows),
            normalize_rerank_usage(saved['raw'],self.config.provider), self.config.reranking.score_scale)


class ReplayRegistry:
    def __init__(self,cache): self.cache = cache
    async def chat(self,mid,context):
        await self.cache.chat(mid,context)
        return _CachedChat(self.cache)
    async def rerank(self,mid,context): return await self.cache.rerank(mid,context)


async def project_selected(state,context,repository):
    """Project selected originals/ancestry only; never load a source window."""
    from backend.weknora_port.retrieval.source_evidence import _materialize_selected,_validate_source,_domain
    selected = [row for row in state.merge_results if row.chunk_type not in ('summary','table_summary')]
    dropped = [row.id for row in state.merge_results if row not in selected]
    allowed = {value for row in selected for value in (row.id,*row.sub_chunk_ids,
        row.parent_chunk_id if row.content_rewritten else '') if value}
    loaded = dict(await repository.get_current_chunks(tuple(dict.fromkeys(row.id for row in selected)),context))
    require(set(loaded) == {row.id for row in selected}, 'S85G_SELECTED_ORIGINAL_MISSING')
    projected = []
    for hit in selected:
        source = loaded[hit.id]
        _validate_source(source,_domain(hit),('text','parent_text'))
        if hit.content == source.content and (hit.start_at,hit.end_at) == (source.start_at,source.end_at) and not hit.content_rewritten:
            rows = [deepcopy(source)]
        else:
            # At least the selected surviving quote must remain in its own
            # original/declared ancestry. Same-document existence is insufficient.
            if not hit.sub_chunk_ids and not hit.content_rewritten:
                require(hit.content in source.content or source.content in hit.content, 'S85G_SELECTED_QUOTE_NOT_PRESERVED')
            rows = await _materialize_selected(hit,source,state,context,repository)
            require(any(hit.content in row.content or row.content in hit.content for row in rows), 'S85G_SELECTED_QUOTE_NOT_PRESERVED')
        for row in rows:
            require(row.id in allowed,'S85G_UNSELECTED_SOURCE_ADDITION')
            row.knowledge_description = row.knowledge_custom_metadata = row.context_header = ''
            if row.id not in {r.id for r in projected}: projected.append(row)
    state.merge_results = projected
    state.source_evidence_mode = 'bounded_source_v1'
    return {'normal_selected_ids':[row.id for row in selected], 'allowed_source_ids':sorted(allowed),
        'projected_ids':[row.id for row in projected], 'dropped_generated_ids':dropped, 'added_unselected_ids':[]}


async def replay_context(app,registry,record,case,configs,context):
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge
    from backend.weknora_port.retrieval.context import into_chat_message
    from backend.weknora_port.retrieval.types import context_results
    repo = LeasedKnowledgeRepository(app,registry)
    require(record['preflight'].get('subject_sha256') == sha(app.principal.subject_id), 'S85G_SOURCE_OWNER_CHANGED')
    source = record['preflight']['current_case']['sources']
    loaded = dict(await repo.get_current_chunks(tuple(source),context))
    require(set(loaded) == set(source),'S85G_CURRENT_SOURCE_SCOPE_OR_VERSION_DENIED')
    for key in source: historical.verify_source_pair(source[key],loaded[key])
    state = historical.build_state(deepcopy(case),record['preflight']['registry_roles'])
    cache = CachedOutputs(load_cached(record,configs),case['case_id'])
    await rescue_bridge(state,context,repo,ReplayRegistry(cache))
    require(cache.used == set(cache.cached) and any(row.get('decision') == 'FIRST_HOP_VERIFIED' for row in state.bridge_trace)
        and not any(row.get('reason') == 'VALIDATION_OR_OPTIONAL_MODEL_FAILURE' for row in state.bridge_trace),
        'S85G_CURRENT_VALIDATOR_REPLAY_FAILED')
    projection = await project_selected(state,context,repo)
    first = next(row['extraction'] for row in state.bridge_trace if row.get('decision') == 'FIRST_HOP_VERIFIED')
    require(any(row.id == first['source_chunk_id'] and first['quote'] in row.content for row in state.merge_results),
            'S85G_FIRST_HOP_QUOTE_DROPPED_BY_PROJECTION')
    for row in state.rescue_evidence:
        row.knowledge_description = row.knowledge_custom_metadata = row.context_header = ''
    finalized = await into_chat_message(state,context)
    rows = tuple(context_results(state))
    require(rows and all(row.chunk_type in ('text','parent_text') for row in rows), 'S85G_ORIGINAL_CONTEXT_REQUIRED')
    require('final_answer_supported="false"' in finalized.rendered_contexts
        and 'seminar_venue="unverified"' in finalized.rendered_contexts, 'S85G_INSTITUTION_BOUNDARY_QUALIFICATION_REQUIRED')
    return state,finalized,rows,{'label':LABEL,'source_stage_id':record['stage_id'],
        'cached_operations_replayed':sorted(cache.used),'cached_usage_added_to_new_ledger':0,
        'cached_prompt_binding':cache.prompt_comparison,
        'cached_usage_provenance':record['final']['usage_by_capability'],'projection':projection,
        'trace':state.bridge_trace,'finalized_context':asdict(finalized)}


def ledger_spec(principal,run_id,role):
    conversation.request_kind(run_id+'/answer/1/cheap',run_id)
    return {'status':'OPEN','scope':'S8_5_BRIDGE_ANSWER_NEW_OWNER','tenant_id':principal.tenant_id,
        'subject_id':principal.subject_id,'allowed_kbs':[conversation.KB_ID],'allowed_models':[role],
        'allowed_request_prefixes':[run_id+'/answer/1/cheap'],'max_calls':1,'max_tokens':10000,
        'capture_evidence':True,'entries':[]}


def validate_answer_settlement(rows,entries,run_id,role):
    expected = (run_id+'/answer/1/cheap','chat')
    indexed = []
    for label,values in (('PG',rows),('EXECUTION',entries)):
        keys = [(row['request_id'],row['capability']) for row in values]
        require(len(keys) == len(set(keys)), 'S85G_DUPLICATE_'+label+'_OPERATION')
        require(keys == [expected], 'S85G_EXACT_NEW_CALL_COUNT_REQUIRED')
        indexed.append(values[0])
    pg,entry = indexed
    require(entry.get('status') == 'SETTLED' and pg.get('outcome') == entry.get('outcome') == 'RESPONSE_RECEIVED'
        and pg.get('finished_at') and pg.get('wire_sha256') == entry.get('wire_sha256')
        and all(pg.get(name) == entry.get(name) == role[name] for name in ('provider','model','model_id')),
        'S85G_UNKNOWN_OR_IDENTITY_CHANGED_STOP')
    usage,other = pg.get('usage'),entry.get('usage')
    require(isinstance(usage,dict) and isinstance(other,dict) and usage.get('status') == other.get('status') == 'REPORTED'
        and all(type(usage.get(name)) is int and usage[name] >= 0 and usage[name] == other.get(name)
            for name in ('prompt_tokens','completion_tokens','total_tokens'))
        and usage['prompt_tokens']+usage['completion_tokens'] == usage['total_tokens'] == entry.get('held_tokens')
        and usage['total_tokens'] <= 10000, 'S85G_USAGE_MISMATCH_OR_UNKNOWN_STOP')
    return deepcopy(usage)


class GenerationFence:
    def __init__(self,context,config,body,audit=None):
        self.context,self.config,self.body,self.audit = context,config,deepcopy(body),audit
        self.used = self.stopped = False
    def validate(self,config,body,context,capability):
        require(not self.used and not self.stopped and capability == 'chat'
            and context.request_id == self.context.request_id+'/answer/1/cheap'
            and context.session_id == self.context.session_id and context.scope == self.context.scope
            and not context.required_origin_kbs and config == self.config and body == self.body,
            'S85G_EXACT_GENERATION_WIRE_REQUIRED')
        from backend.weknora_port.models.output_contract import resolve_output_contract
        contract = resolve_output_contract(config,body,capability='chat')
        require(contract.output_upper == 1024 and contract.thinking_mode == 'disabled'
            and bridge.reservation(config,body,'chat') <= 10000, 'S85G_GENERATION_BOUND_REQUIRED')
        self.used = True
    def install(self,model):
        original,fence = model.transport.open,self
        @asynccontextmanager
        async def opened(config,path,body,context,**kwargs):
            try:
                fence.validate(config,body,context,kwargs.get('capability'))
                async with original(config,path,body,context,**kwargs) as handle: yield handle
                if fence.audit is not None: await fence.audit()
            except BaseException:
                fence.stopped = True
                raise
        model.transport.open = opened
        return model


async def prepare(root,*,write=True):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.storage.configuration import PostgresModelRoleStore
    from backend.weknora_port.models.types import CallContext,TrustedScope,ChatOptions
    from backend.weknora_port.generation.context import Evidence,prepare_generation,DEFAULT_SYSTEM_PROMPT
    from backend.weknora_port.generation.types import GenerationRequest
    from backend.weknora_port.generation.router import RouterPolicy
    from backend.weknora_port.models.request import build_chat_body
    from backend.weknora_port.conversation_configuration import resolved_chat_options
    raw = (root/'payload/inputs.json').read_bytes(); owner = read(root/'payload/owner-authorization.json')
    prefix = validate_owner(owner,raw); validate_root(root,owner['stage_id'])
    records = load_bridge_records(owner,raw); answers = load_answer_records(owner,raw)
    conversations = load_conversation_records(owner,raw)
    require(owner['stage_id'] not in {record['stage_id'] for record in [*records,*answers,*conversations]},
            'S85G_PRIOR_STAGE_ID_REUSED')
    budget = budget_summary(records,answers,conversations)
    require(not budget['prior_unknown_operations'] or owner.get('unknown_holds_acknowledged') is True,
            'S85G_UNKNOWN_HOLDS_ACKNOWLEDGEMENT_REQUIRED')
    matches = [record for record in records if record['stage_id'] == owner['source_stage_id']]
    require(len(matches) == 1,'S85G_SOURCE_RECEIPT_REQUIRED'); record = matches[0]
    validate_source(record,owner['case_id']); case = scoped_case(record,raw,owner['case_id'])
    settings,principal = conversation.runtime_settings(owner['stage_id'])
    async with assemble(settings,principal=principal) as app:
        before = await conversation.protected_snapshot(app)
        require(before['provider']['unfinished'] == 0,'S85G_UNFINISHED_PROVIDER_STOP')
        async with app.leases.lease() as guard:
            revision,formal = await PostgresModelRoleStore(guard,principal,app.catalogue,cipher=app.cipher).read()
        cheap = next(row for row in formal if row.role == 'cheap')
        require(cheap.provider == 'siliconflow' and cheap.model == ENVELOPE['model'],'S85G_FORMAL_CHEAP_REQUIRED')
        mid = app.models._identity(cheap)[0]
        config = app.catalogue.resolve(await app.models.resolve(mid),mid)
        conversation.generation_contract(config)
        role = {'provider':config.provider,'model':config.model,'model_id':mid,'capability':'chat'}
        configs = {}
        for cap,name in (('chat','cheap'),('rerank','rerank')):
            old = record['preflight']['registry_roles'][name]
            configs[cap] = app.catalogue.resolve(await app.models.resolve(old['model_id']),old['model_id'])
            require(sha(canonical(asdict(configs[cap]))) == record['preflight']['model_config_sha256'][name],
                    'S85G_CACHED_MODEL_CONFIG_CHANGED')
        context = CallContext(str(uuid.UUID(int=0,version=4)),TrustedScope(principal.subject_id,
            tuple(case['knowledge_base_ids']),tuple(sorted(case['document_ids']))),session_id=str(uuid.UUID(int=1,version=4)))
        registry = app.registry_for_context()
        try:
            state,finalized,rows,replay = await replay_context(app,registry,record,case,configs,context)
        finally: await registry.close()
        rendered = datetime.now()
        system_prompt = app.conversation.summary.get('prompt',DEFAULT_SYSTEM_PROMPT)
        options = replace(resolved_chat_options(app.conversation.summary,'off'),max_tokens=0,
            max_completion_tokens=1024,thinking=False,prompt_cache_key=context.session_id)
        request = GenerationRequest(context.request_id,context.session_id,str(uuid.UUID(int=2,version=4)),case['query'],finalized,
            evidence=tuple(Evidence.from_result(row) for row in rows),history=(),memory_prompt='',language='English',
            citations_enabled=True,policy=RouterPolicy(mode='CHEAP_ONLY'),rendered_at=rendered,
            options=options,system_prompt=system_prompt)
        body = build_chat_body(config,prepare_generation(request).messages,request.options,stream=True)
        reserve = bridge.reservation(config,body,'chat')
        require(reserve <= 10000,'S85G_ACTUAL_GENERATION_WIRE_TOO_LARGE')
        require(await conversation.protected_snapshot(app) == before,'S85G_ZERO_SEND_MUTATION')
    result = {'status':'S85G_READY_ZERO_SEND','stage_prefix':prefix,'case_id':case['case_id'],
        'owner_authorization_sha256':sha(canonical(owner)),'source_inputs_sha256':sha(raw),
        'implementation_sha256':{**conversation.implementation_hashes(),
            'scripts/s8_5_bridge_answer_ecs.py':sha(Path(__file__).read_bytes())},
        'envelope':ENVELOPE,'budget':budget,'before':before,'registry_role':role,'document_ids':case['document_ids'],
        'model_config_sha256':sha(canonical(asdict(config))),'role_revision':revision,
        'replay':replay,'source_results':[asdict(row) for row in rows],
        'document_versions':{row.knowledge_id:row.metadata['source_version'] for row in rows},
        'generation_options':asdict(request.options),'system_prompt':system_prompt,'rendered_at':rendered.isoformat(),
        'placeholder_wire':body,'reservation_tokens':reserve,'new_provider_calls':0,'new_actual_tokens':0}
    # UUIDs have fixed wire lengths; keep preparation deterministic for run equality.
    result['rendered_at'] = rendered.strftime('%Y-%m-%dT00:00:00')
    if write: write_new(root/'preflight.json',result)
    return result


async def run(root):
    from backend.weknora_port.development import assemble
    from backend.weknora_port.execution import LocalExecution
    from backend.weknora_port.http_api import SessionCreate
    from backend.weknora_port.service import OrdinaryTurnRequest
    from backend.weknora_port.models.types import ChatOptions
    from backend.weknora_port.generation.context import Evidence,prepare_generation
    from backend.weknora_port.generation.types import GenerationRequest
    from backend.weknora_port.models.request import build_chat_body
    raw = (root/'payload/inputs.json').read_bytes(); owner = read(root/'payload/owner-authorization.json')
    prefix = validate_owner(owner,raw); validate_root(root,owner['stage_id'])
    pre = read(root/'preflight.json')
    require(pre == await prepare(root,write=False),'S85G_PREFLIGHT_CHANGED')
    write_new(root/'run.claim',{'stage_prefix':prefix,'envelope':ENVELOPE})
    folder = Path(EXECUTION_ROOT)/prefix
    require(not folder.exists(),'S85G_EXECUTION_REUSE_DENIED'); folder.mkdir(mode=0o700,parents=True)
    ledger = folder/'execution.json'; sessions,runs = [],[]
    settings,principal = conversation.runtime_settings(owner['stage_id'])
    final = {'status':'S85G_STOP','case_id':owner['case_id'],'envelope':ENVELOPE,'label':LABEL,
        'cached_usage_added_to_new_ledger':0,'generated_answer_semantic_support':'REQUIRES_ORIGINAL_AND_ANSWER_REVIEW'}
    async with assemble(settings,principal=principal) as app:
        try:
            require(await conversation.protected_snapshot(app) == pre['before'],'S85G_HISTORY_CHANGED')
            case = bridge.validate_inputs(raw,owner['case_id']); case['document_ids'] = pre['document_ids']
            created = await app.create_session(principal,SessionCreate(title=prefix,
                knowledge_base_scope=case['knowledge_base_ids'],document_scope=case['document_ids'],source_evidence_mode='bounded_source_v1'))
            sessions.append(created['id']); write_new(root/'new-session.json',created)
            accepted = await app.turns.accept(OrdinaryTurnRequest(created['id'],case['query'],str(uuid.uuid4()),
                knowledge_base_scope=tuple(case['knowledge_base_ids']),document_scope=tuple(case['document_ids']),
                summary_model_id=pre['registry_role']['model_id'],reasoning_effort='off',accept_language='en'),principal)
            require(accepted.is_new and not accepted.replay_only,'S85G_NEW_ADMISSION_REQUIRED')
            accepted = replace(accepted,memory_context=replace(accepted.memory_context,agent_memory_allowed=False))
            runs.append(accepted.run_id); final['admission'] = {'run_id':accepted.run_id,'session_id':accepted.session_id,
                'user_message_id':accepted.user_message_id,'assistant_message_id':accepted.assistant_message_id,'q0':accepted.request.content}
            write_new(ledger,ledger_spec(principal,accepted.run_id,pre['registry_role']))
            os.environ['WEKNORA_PORT_EXECUTION_ROOT'] = EXECUTION_ROOT; app.execution = LocalExecution(ledger)
            original_factory = app.model_factory; holder = {}
            async def factory(config,key,gate):
                model = original_factory(config,key,gate); model = await model if inspect.isawaitable(model) else model
                return holder['fence'].install(model)
            # Build the exact request before creating any model in _configuration.
            from backend.weknora_port.retrieval.context import FinalizedContext
            from backend.weknora_port.retrieval.types import SearchResult
            rows = tuple(SearchResult(**row) for row in pre['source_results'])
            options = ChatOptions(**pre['generation_options']); options = replace(options,prompt_cache_key=accepted.session_id)
            request = GenerationRequest(accepted.run_id,accepted.session_id,accepted.assistant_message_id,case['query'],
                FinalizedContext(**pre['replay']['finalized_context']),evidence=tuple(Evidence.from_result(row) for row in rows),
                history=(),memory_prompt='',language='English',citations_enabled=True,policy=app.policy,
                options=options,system_prompt=pre['system_prompt'],rendered_at=datetime.fromisoformat(pre['rendered_at']))
            loaded = await app.models.resolve(pre['registry_role']['model_id']); config = app.catalogue.resolve(loaded,pre['registry_role']['model_id'])
            require(sha(canonical(asdict(config))) == pre['model_config_sha256'],'S85G_MODEL_CONFIG_CHANGED')
            body = build_chat_body(config,prepare_generation(request).messages,request.options,stream=True)
            require(bridge.reservation(config,body,'chat') <= 10000,'S85G_ACTUAL_GENERATION_WIRE_TOO_LARGE')
            write_new(root/'actual-wire-preflight.json',{'body':body,'wire_sha256':sha(canonical(body)),
                'reservation_tokens':bridge.reservation(config,body,'chat'),'admission':final['admission']})
            async def audit():
                entries = read(ledger)['entries']; pg = await historical.provider_rows(app,principal,accepted.run_id+'/')
                validate_answer_settlement(pg,entries,accepted.run_id,pre['registry_role'])
                require(await conversation.protected_snapshot(app,sessions,runs) == pre['before'],'S85G_OLD_HISTORY_OR_SOURCE_MUTATION')
            holder['fence'] = GenerationFence(accepted.context,config,body,audit)
            app.model_factory = factory
            ordinary_config = await app._configuration(accepted)
            require(ordinary_config.generation_options['system_prompt'] == request.system_prompt
                and ordinary_config.generation_options['policy'].mode == 'CHEAP_ONLY', 'S85G_ORDINARY_GENERATION_CONFIG_CHANGED')
            final['source_readback'] = await conversation.source_readback(app,request,accepted.context,pre['document_versions'])
            async def progress(*args,**kwargs): return None
            coordinator = await app._generation(accepted,progress)
            app.cloud_allowed = True
            result = await coordinator.run(request,accepted.context)
            app.cloud_allowed = False
            await audit()
            final['result'] = asdict(result); final['citation_readback'] = conversation.citation_readback(result,final['source_readback'])
            final['status'] = 'S85G_COMPLETE'
        except BaseException as exc:
            final['error'] = historical.safe_error(exc)
            if runs:
                try: await app.ordinary.publish_error(accepted,exc)
                except BaseException as failure: final['failure_commit_error'] = historical.safe_error(failure)
        finally:
            app.cloud_allowed = False
            if ledger.exists():
                historical.close_ledger(ledger,success=final['status']=='S85G_COMPLETE')
                final['execution_entries'] = read(ledger)['entries']; final['ledger_status'] = read(ledger)['status']
                final['receipt_files'] = bridge.receipt_manifest(folder)
                final.update(bridge.execution_summary(final['execution_entries']))
            else: final['execution_entries'],final['ledger_status'] = [],'NOT_CREATED'
            final.update(bridge.execution_summary(final['execution_entries']))
            final['provider_rows'] = await historical.provider_rows(app,principal,runs[0]+'/') if runs else []
            final['after'] = await conversation.protected_snapshot(app,sessions,runs)
            final['old_history_and_sources_unchanged'] = final['after'] == pre['before']
            if not final['old_history_and_sources_unchanged']: final['status'],final['error'] = 'S85G_STOP','S85G_OLD_HISTORY_OR_SOURCE_MUTATION'
            for value in app._runs.values():
                try: await value['registry'].close()
                except BaseException as exc: final['registry_close_error'] = historical.safe_error(exc)
            app._runs.clear()
            safe_write(root/'final.json',final)
            safe_write(root/'authorization-closure.json',{'status':final['ledger_status'],
                'historical_authorizations_reused':False,'unused_operation_allowance_revoked':True,
                'new_execution_summary':bridge.execution_summary(final['execution_entries']),
                'cached_usage_added_to_new_ledger':0})
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','run')); parser.add_argument('--root',required=True,type=Path)
    args = parser.parse_args(); result = asyncio.run(prepare(args.root) if args.action == 'prepare' else run(args.root))
    print(canonical({'status':result['status'],'error':result.get('error'),'all_actual_tokens':result.get('all_actual_tokens')}))
    if result['status'] == 'S85G_STOP': raise SystemExit(2)


if __name__ == '__main__': main()
