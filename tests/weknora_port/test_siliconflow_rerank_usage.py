"""Offline replay of immutable P13/P14 HTTP responses; no provider requests."""
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from backend.weknora_port.models import provider_config
from backend.weknora_port.models.usage import normalize_rerank_usage
from tests.weknora_port.test_provider_transport import setup, context

ROOT = Path(__file__).parent / 'fixtures/p16_rerank_usage'
RECORDS = json.loads((ROOT / 'manifest.json').read_text())


@pytest.mark.parametrize('record', RECORDS, ids=lambda r: r['file'])
def test_real_response_offline_replay_reaches_gate_without_changing_scores(record):
    raw = (ROOT / record['file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record['sha256']
    payload = json.loads(raw)

    async def run():
        cfg = provider_config('siliconflow', 'BAAI/bge-reranker-v2-m3')
        model, transport, gate = setup(lambda _: httpx.Response(200, content=raw), config=cfg)
        try:
            out = await model.rerank('FROZEN_RESPONSE_REPLAY', ['source'] * 31, context())
            assert [(r.index, r.score) for r in out.results] == [
                (r['index'], r['relevance_score']) for r in payload['results']]
            assert out.usage.status == 'REPORTED'
            expected = record['input_tokens'] + record['output_tokens']
            assert out.usage.total_tokens == expected
            finished = gate.finishes[0][2]
            facts = finished.provider_usage_facts
            assert finished.total_tokens == expected
            assert facts.accounting() == (expected, expected)
            assert facts.get('input').path == 'meta.tokens.input_tokens'
            assert not facts.get('total').present  # sum is derived, not supplier total
            assert facts.derived_total == expected
            assert 'billed_units' not in facts.numeric_raw
            assert len(gate.calls) == len(gate.finishes) == 1
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_billing_units_never_supply_or_override_tokens():
    billed = {'input_tokens': 999, 'output_tokens': 999, 'total_tokens': 999,
              'search_units': 1000, 'classifications': 500}
    assert normalize_rerank_usage({'meta': {'billed_units': billed}}, 'siliconflow').status == 'UNKNOWN'
    env = {'meta': {'tokens': {'input_tokens': 10, 'output_tokens': 0, 'image_tokens': 0},
                    'billed_units': billed}}
    u = normalize_rerank_usage(env, 'siliconflow')
    assert u.total_tokens == 10 and u.provider_usage_facts.accounting() == (10, 10)
    assert normalize_rerank_usage(env, 'other-provider').status == 'UNKNOWN'
    env['usage'] = {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}
    assert normalize_rerank_usage(env, 'siliconflow').total_tokens == 3


@pytest.mark.parametrize('tokens', [
    {}, {'input_tokens': 10}, {'input_tokens': 10, 'output_tokens': 0},
    {'input_tokens': 10, 'output_tokens': 0, 'image_tokens': 2},
    {'input_tokens': True, 'output_tokens': 0, 'image_tokens': 0},
    {'input_tokens': -1, 'output_tokens': 0, 'image_tokens': 0},
    {'input_tokens': '10', 'output_tokens': 0, 'image_tokens': 0},
    {'input_tokens': 1.5, 'output_tokens': 0, 'image_tokens': 0},
    {'input_tokens': 10, 'output_tokens': None, 'image_tokens': 0},
    {'input_tokens': 10, 'output_tokens': 0, 'image_tokens': 0, 'total_tokens': 9},
    None, 'invalid',
])
def test_partial_invalid_or_ambiguous_image_counts_stay_unknown(tokens):
    u = normalize_rerank_usage({'meta': {'tokens': tokens, 'billed_units': {'total_tokens': 999}}}, 'siliconflow')
    assert u.status == 'UNKNOWN' and u.provider_usage_facts.accounting()[0] is None


def test_explicit_zero_is_reported_and_total_is_checked():
    u = normalize_rerank_usage({'meta': {'tokens': {
        'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'image_tokens': 0}}}, 'siliconflow')
    assert u.status == 'REPORTED' and u.total_tokens == 0
    assert u.provider_usage_facts.accounting() == (0, 0)


def test_later_meta_tokens_cannot_erase_previously_observed_lower_bound():
    from backend.weknora_port.models.transport import ResponseHandle
    from backend.weknora_port.models.types import TokenUsage
    handle = ResponseHandle(None, TokenUsage())
    handle.capture({'prompt_tokens': 150})
    handle.observed(normalize_rerank_usage({'meta': {'tokens': {
        'input_tokens': 10, 'output_tokens': 0, 'image_tokens': 0}}}, 'siliconflow'))
    assert handle.usage.provider_usage_facts.accounting() == (None, 150)


def test_mixed_batch_protocols_keep_individual_accounting_and_sum_once():
    async def run():
        cfg = provider_config('siliconflow', 'BAAI/bge-reranker-v2-m3')
        cfg = replace(cfg, reranking=replace(cfg.reranking, max_documents=1))
        bodies = iter([
            {'meta': {'tokens': {'input_tokens': 7, 'output_tokens': 0, 'image_tokens': 0}}},
            {'usage': {'prompt_tokens': 3, 'completion_tokens': 0, 'total_tokens': 3}},
        ])
        model, transport, gate = setup(lambda _: httpx.Response(200, json={
            **next(bodies), 'results': [{'index': 0, 'relevance_score': .5}]}), config=cfg)
        try:
            out = await model.rerank('q', ['one', 'two'], context())
            assert out.usage.total_tokens == 10 and out.usage.status == 'REPORTED'
            assert [u.provider_usage_facts.accounting()[0] for _, _, u in gate.finishes] == [7, 3]
        finally:
            await transport.aclose()
    asyncio.run(run())
