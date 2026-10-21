"""Bounded M4 saved-receipt replay. No Provider or transport is constructed.

Evaluation quotes are checked only after production selection/rendering. This
does not measure a new model response or substitute for a normal API test.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import fields
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResult, RerankResponse
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.rerank import rerank, enriched_passage
from backend.weknora_port.retrieval.merge import merge as candidate_merge
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.search import SearchTarget, SearchNothing
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.source_evidence import MODE, expand_source_evidence
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.storage.knowledge import ScopedKnowledgeRepository
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest

BASE = '9350f2282659571d14931e420d79e896901033f3'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def states(row):
    return {s['stage']: s['state'] for s in (row.get('stage_capture') or {}).get('stages', [])
            if s.get('status') == 'completed' and 'state' in s}


def normalize(value):
    return ''.join(html.unescape(value).split())


def frozen_input(row):
    stage = states(row)
    call = next(c for c in row['provider_calls'] if c['capability'] == 'rerank')
    match = call['wire_capture_matches'][0]
    request = load(match['file'])
    response = load(match['response_file'])
    names = {f.name for f in fields(SearchResult)}
    rows = [SearchResult(**{k: v for k, v in r.items() if k in names})
            for r in stage['retrieval']['search_results'] if r]
    passages = [enriched_passage(r) for r in rows if enriched_passage(r).strip()]
    assert passages == request['body']['documents'], 'FROZEN_RERANK_INPUT_NOT_REPRODUCIBLE'
    assert request['body'] == match['request_body'], 'WIRE_BODY_DISAGREES'
    assert request['body']['query'] == stage['understand']['rewrite_query']
    assert all(0 <= r['index'] < len(passages) for r in response['results'])
    assert len({r['index'] for r in response['results']}) == len(response['results'])
    return rows, request, response, match


def baseline_merge():
    source = subprocess.check_output(['git', 'show', BASE + ':backend/weknora_port/retrieval/merge.py'], cwd=ROOT)
    namespace = {'__package__': 'backend.weknora_port.retrieval', '__name__': 'm4_saved_baseline_merge'}
    exec(compile(source, '<git-baseline-merge>', 'exec'), namespace)
    return namespace['merge']


async def arm(row, data, conn, merge_fn, mode, quotes):
    rows, request, response, match = data
    scope = request['trusted_scope']
    context = CallContext('OFFLINE_M4_S1', TrustedScope(scope['owner_id'],
        tuple(scope['knowledge_base_ids']), tuple(scope['document_ids'])))
    async def tenant(owner):
        assert owner == scope['owner_id']
        found = await (await conn.execute('SELECT DISTINCT tenant_id FROM knowledge_bases WHERE id=ANY(%s)',
                                        (list(scope['knowledge_base_ids']),))).fetchall()
        assert found and {r['tenant_id'] for r in found} == {332}
        return 332
    retriever = PostgresRetriever(conn, IsolationPolicy('7696088403504697382', frozenset({'OLD_BUSINESS_DENIED'})))
    await retriever.verify_identity()
    repo = ScopedKnowledgeRepository(retriever, tenant)
    class SavedModel:
        async def rerank(self, query, passages, ctx):
            assert query == request['body']['query'] and passages == request['body']['documents']
            return RerankResponse(tuple(RerankResult(r['index'], r['relevance_score']) for r in response['results']))
    class Registry:
        async def rerank(self, *args):
            return SavedModel()
    state = PipelineState(query=row['question'], rewrite_query=request['body']['query'],
        targets=tuple(SearchTarget(kb) for kb in scope['knowledge_base_ids']),
        search_results=deepcopy(rows), rerank_model_id='SAVED_RESPONSE_ONLY', source_evidence_mode=mode)
    segmenter = default_segmenter()
    try:
        await rerank(state, context, Registry(), segmenter)
        await merge_fn(state, context, repo, segmenter)
        filter_top_k(state)
        before = [r.id for r in state.merge_results]
        if mode != 'off':
            await expand_source_evidence(state, context, repo)
        final = await into_chat_message(state)
        prepared = prepare_generation(GenerationRequest('offline', 'offline', 'offline', row['question'],
            final, tuple(Evidence.from_result(r) for r in state.merge_results)))
        message = html.unescape(prepared.messages[-1].content)
        text = normalize(message)
        return {'status': 'OFFLINE_REPLAY', 'ids_before_source_expansion': before,
            'final_ids': list(final.result_ids), 'context_chars': len(final.rendered_contexts),
            'generation_chars': len(message), 'context_sha256': hashlib.sha256(message.encode()).hexdigest(),
            'quotes': [{'id': q['id'], 'present': normalize(q['text']) in text, 'fact_scope': q['fact_scope']}
                       for q in quotes],
            'rows': [{'id': r.id, 'kind': r.chunk_type, 'chars': len(r.content), 'score': r.score,
                      'document_id': r.knowledge_id, 'kb_id': r.knowledge_base_id,
                      'version': r.metadata.get('source_version'),
                      'selection': r.metadata.get('source_selection')} for r in state.merge_results],
            'trace': state.evidence_trace, 'actual_provider_delivery': 'NOT_RUN'}
    except SearchNothing:
        return {'status': 'SEARCH_NOTHING', 'final_ids': [], 'context_chars': 0, 'generation_chars': 0,
            'quotes': [{'id': q['id'], 'present': False, 'fact_scope': q['fact_scope']} for q in quotes],
            'rows': [], 'trace': state.evidence_trace, 'actual_provider_delivery': 'NOT_RUN'}


async def main(args):
    import psycopg
    from psycopg.rows import dict_row
    from scripts.local_runtime import environment, load_config
    plan = load(args.plan)
    assert 0 < len(plan['cases']) <= 16, 'BOUNDED_REPLAY_CASE_COUNT'
    raw = {r['case_id']: r for r in [json.loads(x) for x in Path(args.raw).read_text(encoding='utf-8').splitlines() if x.strip()]}
    env = environment(load_config(args.config))
    old_merge = baseline_merge()
    conn = await psycopg.AsyncConnection.connect(env['WEKNORA_PORT_DATABASE_URL'], autocommit=True, row_factory=dict_row)
    output = []
    try:
        await conn.execute('BEGIN TRANSACTION READ ONLY')
        database = await (await conn.execute('SELECT current_database() AS name')).fetchone()
        assert database['name'] == 'weknora_python_port_20261013'
        for case in plan['cases']:
            row = raw[case['case_id']]
            assert not row.get('unknown_call_count'), 'UNKNOWN_CASE_NOT_A_NEW_TEST'
            data = frozen_input(row)
            for d in row['expected_documents']:
                live = await (await conn.execute("SELECT tenant_id,knowledge_base_id,file_hash,metadata->>'source_version' AS version FROM knowledges WHERE id=%s AND deleted_at IS NULL", (d['id'],))).fetchone()
                assert live and live['tenant_id'] == 332 and live['knowledge_base_id'] == d['knowledge_base_id']
                assert live['file_hash'] == d['file_hash'] and live['version'] == d['source_version'], 'SOURCE_SNAPSHOT_CHANGED'
            arms = {}
            for name, merge_fn, mode in [('baseline_off', old_merge, 'off'),
                                          ('existing_bounded', old_merge, MODE),
                                          ('repaired_bounded', candidate_merge, MODE)]:
                try:
                    arms[name] = await arm(row, data, conn, merge_fn, mode, case.get('quotes', []))
                except Exception as exc:
                    # Preserve failed arm; it is not a PASS and does not stop independent cases.
                    arms[name] = {'status': 'REPLAY_ERROR', 'error': type(exc).__name__ + ':' + str(exc)}
            historical_ids = (row.get('stage_capture') or {}).get('finalized_context', {}).get('result_ids', [])
            baseline_match = arms['baseline_off'].get('final_ids') == historical_ids
            output.append({'case_id': row['case_id'], 'question': row['question'], 'scope': case.get('scope'),
                'historical_final_ids': historical_ids, 'baseline_ids_match': baseline_match,
                'request': {'path': data[3]['file'], 'sha256': digest(data[3]['file'])},
                'response': {'path': data[3]['response_file'], 'sha256': digest(data[3]['response_file'])},
                'inputs_changed': False, 'arms': arms})
        await conn.execute('ROLLBACK')
    finally:
        await conn.close()
    result = {'schema': 'm4-s1-saved-receipt-replay/v1', 'source_git_base': BASE, 'provider_sends': 0,
        'database_writes': 0, 'source_files': {str(args.raw): digest(args.raw), str(args.plan): digest(args.plan)},
        'cases': output, 'actual_answer_quality_after_repair': 'NOT_RUN',
        'noise_measurement': 'Added original text is source-valid but relevance remains unjudged; known negatives are explicit evaluation quotes.'}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'cases': len(output), 'baseline_matches': sum(r['baseline_ids_match'] for r in output),
        'arm_errors': sum(a['status'] == 'REPLAY_ERROR' for r in output for a in r['arms'].values()),
        'provider_sends': 0, 'output': str(args.out)}))
    return 0 if output and all(r['baseline_ids_match'] and all(
        a['status'] != 'REPLAY_ERROR' for a in r['arms'].values()) for r in output) else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('raw', 'plan', 'config', 'out'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    raise SystemExit(asyncio.run(main(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None))
