"""Reproducible S8.5 source/argument audit using repository mock fixtures.

No hosted provider, database, ECS or final answer generation is invoked.
Frozen S8.4 bytes are read only; all S8.5 output is a separate new artifact.
"""
from __future__ import annotations
import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/weknora_port'))
FROZEN = ROOT / 'tests/weknora_port/fixtures/s8_4_bridge/inputs.json'
FROZEN_SHA = 'e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


async def build_report():
    from test_s8_5_conversation import build_multiturn_trace
    from test_bridge_pipeline import build_frozen_bridge_trace
    from test_bridge_rescue import Models, Repository
    from scripts.s8_4_bridge_ecs import build_state
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from backend.weknora_port.retrieval.bridge_rescue import rescue_bridge, CONTRACT_VERSION
    from backend.weknora_port.retrieval.context import into_chat_message
    from backend.weknora_port.retrieval.types import SearchResult, context_results
    from backend.weknora_port.generation.context import Evidence, prepare_generation
    from backend.weknora_port.generation.types import GenerationRequest

    before = FROZEN.read_bytes()
    assert sha(before) == FROZEN_SHA
    cases = json.loads(before)['cases']
    results = []
    for saved in cases:
        if saved['case_id'].startswith('frost-'):
            state, ctx, models, repo, first, second = await build_frozen_bridge_trace(saved)
        else:
            state = build_state(saved, {'cheap':{'model_id':'mock-cheap'}, 'rerank':{'model_id':'mock-bge'}})
            ctx = CallContext(saved['case_id'], TrustedScope('owner', tuple(saved['knowledge_base_ids'])))
            models = Models('{"status":"NO_FIRST_HOP"}')
            repo = Repository([SearchResult(**r) for r in saved['source_rows']])
            normal = deepcopy((state.search_results, state.rerank_results, state.merge_results))
            await rescue_bridge(state, ctx, repo, models)
            assert normal == (state.search_results, state.rerank_results, state.merge_results)
            first = second = None
        finalized = await into_chat_message(state, ctx)
        evidence = tuple(Evidence.from_result(row) for row in context_results(state))
        prepared = prepare_generation(GenerationRequest(saved['case_id'], 'offline-session',
            'offline-answer', saved['query'], finalized, evidence=evidence))
        details = state.rescue_evidence[0].metadata['bridge_rescue'] if state.rescue_evidence else {}
        decisions = [item.get('decision') for item in state.bridge_trace]
        item = {'case_id':saved['case_id'], 'execution':'DETERMINISTIC_MOCK',
            'q0':state.query, 'rewrite_query':state.rewrite_query,
            'first_hop_relation_verified':'FIRST_HOP_VERIFIED' in decisions,
            'identity_verified':details.get('identity_proof',{}).get('verified',False),
            'final_context_identity_supported':details.get('identity_proof',{}).get('final_context_identity_supported',False),
            'q1':details.get('q1'), 'initial_search_candidate_count':len(state.search_results),
            'second_hop_source_in_pool':bool(second and any(r and r.id == second.id for r in state.search_results)),
            'second_rerank_called':bool(models.rerank_calls),
            'second_rerank_passed':bool(state.rescue_evidence),
            'second_hop_location_found':'SECOND_HOP_LOCATION_FOUND' in decisions,
            'entity_bridge_verified':details.get('entity_bridge_verified',False),
            'final_answer_supported':details.get('final_answer_supported',False),
            'generated_answer_verified':None, 'generation_status':'NOT_RUN',
            'mock_cheap_calls':len(models.chat_calls), 'mock_bge_calls':len(models.rerank_calls),
            'context_result_ids':list(finalized.result_ids), 'context_sha256':sha(finalized.rendered_contexts.encode()),
            'generation_prompt_sha256':prepared.prompt_sha256,
            'supplement_source_ids':[r.id for r in state.rescue_evidence],
            'first_hop':({'source_chunk_id':first.id, 'quote_start':details['quote_start'],
                'quote_end':details['quote_end'], 'quote':details['first_hop_quote'],
                'source_version':first.metadata['source_version']} if first else None),
            'identity_proof':details.get('identity_proof'), 'second_hop':details.get('location_fact'),
            'bridge_trace':state.bridge_trace}
        if first:
            assert item['q1'] == 'Where is The Frost Place Advanced Seminar located?'
            assert item['first_hop_relation_verified'] and item['identity_verified']
            assert item['second_hop_source_in_pool'] and item['second_rerank_passed']
            assert second.id in finalized.result_ids
            assert details['relation_status'] == 'INSTITUTION_LOCATION_ONLY_EVENT_VENUE_UNVERIFIED'
            assert not item['entity_bridge_verified'] and not item['final_answer_supported']
            assert 'supports="institution_location_only"' in finalized.rendered_contexts
            assert any('seminar_venue="unverified"' in message.content for message in prepared.messages)
        else:
            assert not item['mock_cheap_calls'] and not item['mock_bge_calls'] and not state.rescue_evidence
        results.append(item)
    assert before == FROZEN.read_bytes()
    return {'schema':'s85-offline-audit/v1', 'status':'OFFLINE_CONTRACTS_PASS_FROST_FINAL_UNSUPPORTED',
        'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'extraction_contract':CONTRACT_VERSION, 'frozen_s84_input_sha256':FROZEN_SHA,
        'real_provider_calls':0, 'real_actual_tokens':0, 'synthetic_usage_omitted':True,
        'database_calls':0, 'ecs_operations':0, 'multi_turn':await build_multiturn_trace(),
        'multi_hop':results, 'scope_validation':'Mock repository invariants; no live permission assertion',
        'answer_accuracy':'NOT_MEASURED; no final model answer generated',
        'stage5':'NOT_REQUIRED_FOR_FROST; second hop exists in original pool; no new Search implemented'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(build_report())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({'status':result['status'], 'turns':len(result['multi_turn']),
                      'cases':len(result['multi_hop']), 'output':str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
