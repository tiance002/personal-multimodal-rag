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


async def replay(case):
    search_rows = [result(r) for r in case['source_search_results']]
    scope = case['scope']
    context = CallContext('offline-replay', TrustedScope(scope['owner_id'],
        tuple(scope['knowledge_base_ids']), tuple(scope['document_ids'])))
    targets = tuple(SearchTarget(kb, target_type='knowledge' if scope['document_ids'] else 'knowledge_base',
        knowledge_ids=tuple(scope['document_ids'])) for kb in scope['knowledge_base_ids'])
    state = PipelineState(query=case['q0'], rewrite_query=case['q0'], targets=targets,
        source_evidence_mode='bounded_source_v1', rerank_model_id='frozen-bge',
        rerank_threshold=.3, rerank_top_k=30, enable_expansion=False, rerank_context_enabled=True)
    state._strategy_serial = True
    reads, calls = [], []
    originals = {r.id:deepcopy(r) for r in search_rows}
    for row in originals.values(): row.score = 0

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
    decisions = [t for t in state.evidence_trace if t.get('reason') in (
        'RERANK_ACCEPTED','RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK','SEARCH_EMPTY','RERANK_MODEL_ERROR')]
    return {'case_id':case['case_id'], 'case_key':case['case_key'], 'category':case['category'],
        'historical_run_uuid':case['actual_run_uuid_from_saved_pg'],
        'historical_counts':case['counts'], 'q0':case['q0'], 'scope':scope,
        'status':'OFFLINE_ORIGINAL_DELIVERY_PASS', 'source_unit_calls':0,
        'rerank_boundary_calls':calls, 'new_provider_calls':0,
        'rerank_selected_ids':[r.id for r in state.rerank_results],
        'selected_sources':[{'id':r.id,'knowledge_id':r.knowledge_id,'knowledge_base_id':r.knowledge_base_id,
            'source_version':r.metadata.get('source_version'),'start':r.start_at,'end':r.end_at,
            'characters':len(r.content),'content_sha256':hashlib.sha256(r.content.encode()).hexdigest()}
            for r in state.merge_results],
        'decisions':decisions,'evidence_trace':state.evidence_trace,
        'current_prepared_messages_sha256':prepared.prompt_sha256,
        'model_answer_quality':'NOT_RUN', 'semantic_support':'NOT_CHECKED',
        'cat_vs_dog_exact_text_in_pool':any('cat vs dog' in r.content.lower() for r in search_rows)
            if case['case_key']=='mml_0434' else None,
        'cat_vs_dog_exact_text_in_final':any('cat vs dog' in r.content.lower() for r in state.merge_results)
            if case['case_key']=='mml_0434' else None,
        'source_read_calls':reads}


async def replay_all():
    fixture = json.loads(FIXTURE.read_bytes())
    return [await replay(case) for case in fixture['cases']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = asyncio.run(replay_all())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'schema':'s8.6.4-offline-retrieval-replay/v1',
        'status':'OFFLINE_PASS_REAL_NOT_RUN','provider_calls':0,'cases':cases},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'cases':len(cases),'provider_calls':0,'delivered_counts':{
        c['case_key']:len(c['selected_sources']) for c in cases}},ensure_ascii=False))


if __name__ == '__main__': main()
