"""Zero-send boundary replay of saved Search + BGE responses through real Merge."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import fields
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResult
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.generation.context import Evidence, prepare_generation, escape_text
from backend.weknora_port.generation.types import GenerationRequest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/s8_6_4/retrieval_replays.json'
RESULT_FIELDS = {f.name for f in fields(SearchResult)}


def result(raw):
    return SearchResult(**{k:deepcopy(v) for k,v in raw.items() if k in RESULT_FIELDS})


def _readback_rows(snapshot, scope, anchor):
    """Validate and adapt a saved current-source readback; never Search candidates."""
    if not isinstance(snapshot, dict) or snapshot.get('status') != 'CURRENT_SOURCE_READBACK_VERIFIED':
        raise ValueError('CURRENT_SOURCE_READBACK_STATUS_INVALID')
    doc = snapshot.get('document') or {}
    kb = snapshot.get('knowledge_base') or {}
    expected_ids = {anchor.id, anchor.pre_chunk_id, anchor.next_chunk_id}
    requested = snapshot.get('requested_chunk_ids')
    raw_rows = snapshot.get('chunks')
    if (snapshot.get('provider_calls') != 0 or snapshot.get('database_writes') != 0 or
            snapshot.get('transaction_read_only') != 'on' or snapshot.get('transaction_end') != 'ROLLBACK' or
            snapshot.get('tenant_id') != doc.get('tenant_id') or
            snapshot.get('owner_sha256') != hashlib.sha256(scope['owner_id'].encode()).hexdigest() or
            doc.get('knowledge_base_id') not in scope['knowledge_base_ids'] or
            doc.get('id') != anchor.knowledge_id or
            anchor.knowledge_base_id != doc.get('knowledge_base_id') or
            anchor.metadata.get('source_version') != doc.get('source_version') or
            kb.get('id') != doc.get('knowledge_base_id') or
            kb.get('tenant_id') != doc.get('tenant_id') or doc.get('deleted_at') is not None or
            doc.get('enable_status') != 'enabled' or doc.get('parse_status') != 'completed' or
            not doc.get('source_version') or requested is None or set(requested) != expected_ids or
            len(requested) != len(expected_ids) or not isinstance(raw_rows, list)):
        raise ValueError('CURRENT_SOURCE_READBACK_SCOPE_OR_VERSION_INVALID')
    rows = {}
    for raw in raw_rows:
        id = raw.get('id')
        if id in rows or id not in expected_ids:
            raise ValueError('CURRENT_SOURCE_READBACK_DUPLICATE_OR_UNEXPECTED_ID')
        if (raw.get('tenant_id') != doc.get('tenant_id') or
                raw.get('knowledge_base_id') != doc.get('knowledge_base_id') or
                raw.get('knowledge_id') != doc.get('id') or raw.get('deleted_at') is not None or
                raw.get('is_enabled') is not True or raw.get('index_status') != 'ready' or
                raw.get('chunk_type') != 'text' or raw.get('content_revision') != 0 or
                raw.get('metadata', {}).get('source_version') != doc['source_version'] or
                type(raw.get('start_at')) is not int or type(raw.get('end_at')) is not int or
                raw['end_at'] - raw['start_at'] != len(raw.get('content', ''))):
            raise ValueError('CURRENT_SOURCE_READBACK_ROW_SCOPE_OR_VERSION_INVALID')
        rows[id] = result({
            'id': id, 'content': raw['content'], 'score': 0,
            'knowledge_id': raw['knowledge_id'], 'knowledge_base_id': raw['knowledge_base_id'],
            'chunk_type': raw['chunk_type'], 'chunk_index': raw['chunk_index'],
            'start_at': raw['start_at'], 'end_at': raw['end_at'],
            'parent_chunk_id': raw.get('parent_chunk_id') or '',
            'pre_chunk_id': raw.get('pre_chunk_id') or '', 'next_chunk_id': raw.get('next_chunk_id') or '',
            'metadata': {'source_version': doc['source_version']},
            'chunk_metadata': deepcopy(raw['metadata']), 'content_revision': raw['content_revision'],
            'is_enabled': raw['is_enabled'],
        })
    if set(rows) != expected_ids:
        raise ValueError('CURRENT_SOURCE_READBACK_ROWS_INCOMPLETE')
    if (rows[anchor.id].pre_chunk_id != anchor.pre_chunk_id or
            rows[anchor.id].next_chunk_id != anchor.next_chunk_id or
            rows[anchor.pre_chunk_id].next_chunk_id != anchor.id or
            rows[anchor.next_chunk_id].pre_chunk_id != anchor.id):
        raise ValueError('CURRENT_SOURCE_READBACK_NEIGHBOR_LINK_INVALID')
    return rows


async def replay(case, *, readback=None, rerank_top1_floorless=False):
    search_rows = [result(r) for r in case['source_search_results']]
    scope = case['scope']
    context = CallContext('offline-replay', TrustedScope(scope['owner_id'],
        tuple(scope['knowledge_base_ids']), tuple(scope['document_ids'])))
    targets = tuple(SearchTarget(kb, target_type='knowledge' if scope['document_ids'] else 'knowledge_base',
        knowledge_ids=tuple(scope['document_ids'])) for kb in scope['knowledge_base_ids'])
    state = PipelineState(query=case['q0'], rewrite_query=case['q0'], targets=targets,
        source_evidence_mode='bounded_source_v1', rerank_model_id='frozen-bge',
        rerank_threshold=.3, rerank_top_k=30, enable_expansion=False, rerank_context_enabled=True,
        rerank_top1_floorless=rerank_top1_floorless)
    state._strategy_serial = True
    reads, calls = [], []
    originals = {r.id:deepcopy(r) for r in search_rows}
    for row in originals.values(): row.score = 0
    anchor = next((r for r in search_rows if r.id == '10ff171b-a054-4cbc-9ab4-4c52b8c3022f'), None)
    readback_map = _readback_rows(readback, scope, anchor) if readback is not None else {}
    originals.update({id:deepcopy(row) for id,row in readback_map.items()})

    class Repository:
        async def get_chunks(self, ids, ctx, *, current=False):
            assert ctx.scope == context.scope and current
            reads.append(list(ids))
            return {id:deepcopy(originals[id]) for id in ids if id in originals}

        async def get_chunk_children(self, ids, ctx): return []

        async def get_faq_answers(self, ids, ctx): return {}

    class Model:
        async def rerank(self, query, documents, ctx):
            assert query == case['q0']
            raw = case['raw_rerank_trace']
            assert [r.id for r in state.search_results] == raw['input_ids']
            assert len(documents) == len(raw['input_ids'])
            calls.append({'query':query, 'request_id':ctx.request_id,
                'documents_sha256':hashlib.sha256(json.dumps(documents,ensure_ascii=False).encode()).hexdigest()})
            return SimpleNamespace(results=[RerankResult(r['index'],r['score']) for r in raw['results']])

    class Registry:
        async def rerank(self, id, ctx): return Model()

    async def restore_search(actual, ctx, repository, **kwargs):
        # Frozen post-Hybrid Search boundary, never a new embedding/SQL call.
        actual.search_results = deepcopy(search_rows)

    with patch('backend.weknora_port.retrieval.pipeline.search', restore_search):
        round_result = await RetrievalPipeline(Repository(), Registry()).retrieve_once(state, context)
    assert round_result.status == 'OK' and state.merge_results
    finalized = await into_chat_message(state, context)
    prepared = prepare_generation(GenerationRequest('offline','offline','offline',state.query,finalized,
        evidence=tuple(Evidence.from_result(r) for r in state.merge_results), citations_enabled=True))
    message = prepared.messages[-1].content
    for row in state.merge_results:
        source = originals[row.id]
        assert (row.content,row.start_at,row.end_at) == (source.content,source.start_at,source.end_at)
        assert escape_text(source.content) in message
    registry_chunks = {handle:ref for handle,ref in prepared.registry.snapshot().chunks}
    source_ids = {r.id for r in state.merge_results}
    prepared_sources = []
    for handle, ref in registry_chunks.items():
        if ref[0] not in source_ids:
            continue
        row = originals[ref[0]]
        assert f'id=\"{handle}\"' in message
        assert escape_text(row.content) in message
        prepared_sources.append({'handle':handle,'id':row.id,'knowledge_id':row.knowledge_id,
            'knowledge_base_id':row.knowledge_base_id,'chunk_index':row.chunk_index,
            'start_at':row.start_at,'end_at':row.end_at,
            'source_version':row.metadata.get('source_version'),
            'content_sha256':hashlib.sha256(row.content.encode()).hexdigest()})
    decisions = [t for t in state.evidence_trace if t.get('reason') in (
        'RERANK_ACCEPTED','RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK','SEARCH_EMPTY','RERANK_MODEL_ERROR')]
    raw_trace = next((t for t in state.evidence_trace if t.get('stage') == 'rerank_raw'), {})
    return {'case_id':case['case_id'], 'case_key':case['case_key'], 'category':case['category'],
        'historical_run_uuid':case['actual_run_uuid_from_saved_pg'],
        'historical_counts':case['counts'], 'q0':case['q0'], 'scope':scope,
        'status':'OFFLINE_ORIGINAL_DELIVERY_PASS', 'source_unit_calls':0,
        'rerank_boundary_calls':calls, 'new_provider_calls':0,
        'rerank_selected_ids':[r.id for r in state.rerank_results],
        'rerank_selected_scores':{r.id:r.score for r in state.rerank_results},
        'rerank_top1_floorless':rerank_top1_floorless, 'rerank_threshold':state.rerank_threshold,
        'frozen_search_candidate':next(({ 'id':r.id,'score':r.score,'input_index':i }
            for i,r in enumerate(search_rows) if r.id == '10ff171b-a054-4cbc-9ab4-4c52b8c3022f'), None),
        'rerank_raw_trace':raw_trace,
        'selected_sources':[{'id':r.id,'score':r.score,'knowledge_id':r.knowledge_id,'knowledge_base_id':r.knowledge_base_id,
            'source_version':r.metadata.get('source_version'),'start':r.start_at,'end':r.end_at,
            'characters':len(r.content),'content_sha256':hashlib.sha256(r.content.encode()).hexdigest()}
            for r in state.merge_results],
        'decisions':decisions,'evidence_trace':state.evidence_trace,
        'current_prepared_messages_sha256':prepared.prompt_sha256,
        'prepared_message_length':len(message), 'prepared_source_identity_map':prepared_sources,
        'model_answer_quality':'NOT_RUN', 'semantic_support':'NOT_CHECKED',
        'cat_vs_dog_exact_text_in_pool':any('cat vs dog' in r.content.lower() for r in search_rows)
            if case['case_key']=='mml_0434' else None,
        'cat_vs_dog_exact_text_in_final':"Kaggle's Cat vs Dog dataset (25k dog/cat pictures)" in message
            if case['case_key']=='mml_0434' else None,
        'anchor_exact_neighbor_text_in_final':"Kaggle's Cat vs Dog dataset (25k dog/cat pictures)\n\n(picture by Dato)" in message
            if case['case_key']=='mml_0434' else None,
        'source_read_calls':reads}


async def replay_all():
    fixture = json.loads(FIXTURE.read_bytes())
    return [await replay(case) for case in fixture['cases']]


async def _replay_selected(cases, readback, floorless):
    return [await replay(case, readback=readback, rerank_top1_floorless=floorless) for case in cases]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case-key')
    parser.add_argument('--current-source-readback', type=Path)
    parser.add_argument('--rerank-top1-floorless', action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args()
    fixture = json.loads(FIXTURE.read_bytes())
    cases = fixture['cases']
    if args.case_key:
        cases = [c for c in cases if c['case_key'] == args.case_key]
        if len(cases) != 1:
            parser.error('case-key must select exactly one fixture case')
    readback = json.loads(args.current_source_readback.read_bytes()) if args.current_source_readback else None
    cases = asyncio.run(_replay_selected(cases, readback, args.rerank_top1_floorless))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'schema':'s8.6.4-offline-retrieval-replay/v1',
        'status':'OFFLINE_PASS_REAL_NOT_RUN','provider_calls':0,'cases':cases},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'cases':len(cases),'provider_calls':0,'delivered_counts':{
        c['case_key']:len(c['selected_sources']) for c in cases}},ensure_ascii=False))


if __name__ == '__main__': main()
