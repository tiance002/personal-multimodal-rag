"""P30 bounded offline checks; no providers, SQL, fabricated old vectors or Gold.

P29 final Search snapshots exercise dedup/TopK, not reconstruction of missing
pre-fusion lists. Weighted RRF uses an explicitly synthetic ordered fixture.
The repository fixture tests gather ordering, not historical per-query recall.
"""
from __future__ import annotations
import asyncio
import argparse
import copy
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.fusion import Hit, fuse_or_deduplicate
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.query import expand_queries, default_segmenter
from backend.weknora_port.retrieval.search import deduplicate, search, SearchTarget
from backend.weknora_port.retrieval.types import SearchResult

OUT = Path('E:/WK/run/p30-20261017')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rows(values):
    fields = SearchResult.__dataclass_fields__
    return [SearchResult(**{k: v for k, v in r.items() if k in fields}) for r in values]


def signature(values):
    return [(r.id, r.score, r.knowledge_base_id, r.knowledge_id, r.content) for r in values]


async def completion_check(seed):
    # Delays change only completion timing; query order and each reply are fixed.
    class Repository:
        async def hybrid_search(self, targets, params, context, embedding=None):
            tag = targets[0].tag_ids[0]
            delay = (seed * 7 + ord(tag)) % 5
            await asyncio.sleep(delay / 1000)
            return [SearchResult(tag, 'unique content '+tag, .5,
                                 knowledge_id='doc', knowledge_base_id='kb')]
    st = PipelineState(query='fixed input', rewrite_query='fixed input',
        targets=tuple(SearchTarget('kb', tag_ids=(x,)) for x in ('a', 'b', 'c')),
        enable_expansion=False)
    await search(st, CallContext('offline', TrustedScope('fixture', ('kb',))), Repository())
    return [r.id for r in st.search_results]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, default=OUT)
    parser.add_argument('--metrics', type=Path, default=ROOT/'docs/evaluation/evidence/p29-rerank-real-ab-20261017/metrics.json')
    parser.add_argument('--output', type=Path, default=OUT/'offline-determinism.json')
    args = parser.parse_args()
    frozen = json.loads((args.evidence_dir/'frozen-search.json').read_text(encoding='utf-8'))
    metrics = json.loads(args.metrics.read_text(encoding='utf-8'))
    checks = []
    for case in frozen['cases']:
        source = rows(case['rows'])
        expected = None
        for _ in range(100):
            values = deduplicate(copy.deepcopy(source))
            state = PipelineState(merge_results=values, rerank_top_k=30)
            filter_top_k(state)
            value = digest(signature(state.merge_results))
            if expected is None:
                expected = value
            assert value == expected, case['case_id']
        # Equal-score TopK uses its existing complete secondary comparator.
        tied = copy.deepcopy(source)
        for r in tied:
            r.score = .5
        outputs = []
        for ordered in (tied, list(reversed(tied))):
            state = PipelineState(merge_results=ordered, rerank_top_k=10)
            filter_top_k(state)
            outputs.append([r.id for r in state.merge_results])
        assert outputs[0] == outputs[1]
        checks.append({'case': case['case_id'], 'kind': 'captured_final_search_dedup_topk',
            'repetitions': 100, 'stable': True, 'output_sha256': expected,
            'same_score_topk_secondary_order_stable': True,
            'not_pre_fusion_or_historical_vector_replay': True})

    # Synthetic ordered vector/keyword rankings: no connection to old vectors.
    vectors = [Hit('a', .6, 'first'), Hit('b', .5), Hit('a', .9, 'highest')]
    keywords = [Hit('c', 100), Hit('a', 90, 'keyword')]
    seen = set()
    for _ in range(100):
        result = fuse_or_deduplicate(copy.deepcopy(vectors), copy.deepcopy(keywords))
        seen.add(digest([asdict(r) for r in result]))
        by_id = {r.chunk_id: r for r in result}
        assert by_id['a'].content == 'highest'
        assert abs(by_id['a'].score-(.7/61+.3/62)) < 1e-15
    assert len(seen) == 1
    checks.append({'kind': 'synthetic_ordered_weighted_rrf', 'repetitions': 100,
                   'stable': True, 'output_sha256': next(iter(seen)),
                   'historical_component_rankings_available': False})

    orders = [asyncio.run(completion_check(seed)) for seed in range(20)]
    assert all(order == ['a', 'b', 'c'] for order in orders)
    checks.append({'kind': 'synthetic_repository_completion_timing', 'repetitions': 20,
                   'stable': True, 'actual_search_gather_order': ['a', 'b', 'c']})

    expansions = []
    for case in metrics['cases'][:6]:
        q = case['rewrite']['initial_embedding_query']
        if isinstance(q, list):
            q = q[0]
        actual = case['rewrite']['actual_embedding_queries']
        flat = [v['input'][0] for v in actual]
        generated = expand_queries(q, case['q0'], default_segmenter())
        if len(flat) > 1:
            assert set(generated) == set(flat[1:]), case['case_id']
        expansions.append({'case': case['case_id'], 'q0': case['q0'], 'rewrite': q,
            'generated_in_source_order': generated, 'actual_embedding_order': flat,
            'expansion_was_executed': len(flat) > 1,
            'generated_set_matches_actual_expansions': set(generated) == set(flat[1:])
                if len(flat) > 1 else None,
            'note': 'Observed provider send/completion order need not equal gather result order.'})

    result = {'status': 'PASS_OFFLINE_DETERMINISM_NOT_HISTORICAL_CAUSAL_PROOF',
        'cloud_calls': 0, 'database_calls': 0, 'checks': checks, 'expansions': expansions,
        'historical_embedding_vectors': 'NOT_CAPTURED',
        'historical_prefusion_vector_keyword_lists': 'NOT_CAPTURED'}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'checks': len(checks),
                      'case_repetitions': 600, 'rrf_repetitions': 100,
                      'completion_schedules': 20, 'cloud_calls': 0}))


if __name__ == '__main__':
    main()
