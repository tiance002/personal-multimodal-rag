"""Exact terminal-period recovery; frozen real outputs, no new Provider sends."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.weknora_port.conversation.types import MemoryScopeError
from backend.weknora_port.retrieval.query import understand_query
from backend.weknora_port.retrieval.strategy import plan_query
from test_s862_query_alignment import Q0, CTX, compact_output, current

REPLAY = json.loads((Path(__file__).parent / 'fixtures/s862r3_qu_replay.json').read_text(encoding='utf8'))
CODE = 'EXACT_ASCII_TERMINAL_PERIOD_RESTORED'


def run(state, output, finish='stop', **response_fields):
    calls = []
    class Model:
        async def chat(self, messages, options, context):
            calls.append((messages, options, context))
            return SimpleNamespace(content=output, finish_reason=finish, **response_fields)
    class Registry:
        async def chat(self, model_id, context):
            assert model_id == 'independent-cheap'
            return Model()
    asyncio.run(understand_query(state, CTX, Registry()))
    assert len(calls) == 1
    return calls


@pytest.mark.parametrize('sample', REPLAY['samples'], ids=lambda row: row['case'])
def test_settled_real_output_replay_recovers_original_and_rule_compare(sample):
    state = current(sample['q0'])
    calls = run(state, sample['raw_model_output'], sample['finish_reason'])
    plan = plan_query(state.query, state.rewrite_query, state.query_type)
    assert state.query == state.rewrite_query == sample['q0']
    assert state.query_type == 'SIMPLE_COMPARE' and state.rewrite_fallback_reason == ''
    assert plan.strategy == 'RULE_COMPARE'
    assert len(plan.subqueries) == (3 if sample['case'] == 'three-object' else 2)
    assert all(sub.query_text.startswith('What is known about ') for sub in plan.subqueries)
    trace = state.query_trace[-1]
    assert trace['decision'] == 'RECOVERED' and trace['reason'] == CODE
    assert trace['raw_response_sha256'] == hashlib.sha256(sample['raw_model_output'].encode('utf8')).hexdigest()
    assert trace['raw_response_hash_scope'] == 'chat_response.content_utf8'
    assert trace['rewrite_sha256'] == trace['q0_sha256']
    assert 'raw_model_output' not in trace and 'rewrite_query' not in trace
    assert calls[0][1].thinking is False and calls[0][1].max_completion_tokens == 256


@pytest.mark.parametrize('q0,candidate', [
    (Q0, Q0[:-1] + ' '), (Q0, ' ' + Q0[:-1]), (Q0, Q0[:-1] + '\n'),
    (Q0, Q0[:-1].replace('Compare', 'compare')), (Q0, Q0[:-1].replace(' and ', '  and ')),
    (Q0, Q0[:-1].replace('persistence', 'latency')), (Q0, Q0[:-2]),
    (Q0 + '.', Q0), (Q0 + '..', Q0 + '.'), (Q0[:-1] + '….', Q0[:-1] + '…'),
    (Q0[:-1] + '?', Q0[:-1]), (Q0[:-1] + '。', Q0[:-1]),
    ('Compare A and B in terms of latency after 2020, not safety.',
     'Compare A and B in terms of latency after 2021, not safety'),
    ('Compare A and B in terms of latency, not safety.',
     'Compare A and B in terms of latency, safety'),
])
def test_no_other_text_or_punctuation_difference_uses_exception(q0, candidate):
    state = current(q0)
    run(state, compact_output(candidate))
    assert state.rewrite_query == q0 and state.query_type == 'UNKNOWN'
    assert state.rewrite_fallback_reason
    assert state.query_trace[-1]['decision'] == 'FALLBACK'
    assert 'raw_response_sha256' not in state.query_trace[-1]


@pytest.mark.parametrize('finish', ['length', 'tool_calls', 'content_filter', '', None, 'unknown', 'end_turn', 'completed'])
def test_exception_requires_known_complete_stop(finish):
    state = current()
    run(state, compact_output(Q0[:-1]), finish)
    assert state.rewrite_query == Q0 and state.query_type == 'UNKNOWN'
    assert state.rewrite_fallback_reason and state.query_trace[-1]['decision'] == 'FALLBACK'


@pytest.mark.parametrize('fields', [{'tool_calls': ('tool',)}, {'truncated': True}])
def test_actual_tools_or_truncation_cannot_unlock_recovery(fields):
    state = current()
    run(state, compact_output(Q0[:-1]), **fields)
    assert state.query_type == 'UNKNOWN' and state.rewrite_fallback_reason


@pytest.mark.parametrize('mutation', ['prefix', 'fence', 'truncated', 'list', 'null', 'missing', 'alias', 'extra', 'duplicate', 'invalid-type', 'invalid-string', 'unicode'])
def test_only_unambiguous_complete_four_field_json_can_recover(mutation):
    data = {'rewrite_query': Q0[:-1], 'intent': 'kb_search', 'image_description': '', 'query_type': 'SIMPLE_COMPARE'}
    raw = json.dumps(data)
    if mutation == 'prefix': raw = 'Here is JSON: ' + raw
    elif mutation == 'fence': raw = '```json\n' + raw + '\n```'
    elif mutation == 'truncated': raw = raw[:-1]
    elif mutation == 'list': raw = '[' + raw + ']'
    elif mutation == 'null': raw = 'null'
    elif mutation == 'missing': del data['image_description']; raw = json.dumps(data)
    elif mutation == 'alias': data['rewritten_query'] = data.pop('rewrite_query'); raw = json.dumps(data)
    elif mutation == 'extra': data['extra'] = ''; raw = json.dumps(data)
    elif mutation == 'duplicate': raw = raw[:-1] + ',"rewrite_query":' + json.dumps(Q0[:-1]) + '}'
    elif mutation == 'invalid-type': data['query_type'] = 'FREE_AGENT'; raw = json.dumps(data)
    elif mutation == 'invalid-string': data['image_description'] = 2; raw = json.dumps(data)
    elif mutation == 'unicode': raw = raw.replace('kb_search', '\ud800')
    state = current()
    run(state, raw)
    assert state.rewrite_query == Q0 and state.query_type == 'UNKNOWN'
    assert state.rewrite_fallback_reason and state.query_trace[-1]['decision'] == 'FALLBACK'


def test_existing_ambiguity_still_clears_type_after_exact_match():
    q0 = 'What is it used for.'
    state = current(q0, history=[SimpleNamespace(query='Compare a Leyden jar and Franklin bells.', answer='Two entities.')])
    run(state, compact_output(q0[:-1], 'DIRECT'))
    assert state.rewrite_query == q0 and state.query_type == 'UNKNOWN'
    assert state.rewrite_fallback_reason == 'AMBIGUOUS_REFERENCE'
    assert state.query_trace[-1]['decision'] == 'FALLBACK'


@pytest.mark.parametrize('error', [PermissionError('scope denied'), MemoryScopeError(), TimeoutError('history timeout')])
def test_history_authority_failure_propagates_without_call(error):
    async def loader(state, context): raise error
    class Registry:
        async def chat(self, *args): raise AssertionError('no model lookup allowed')
    with pytest.raises(type(error)):
        asyncio.run(understand_query(current(), CTX, Registry(), history_loader=loader))


@pytest.mark.parametrize('mode,fields', [('off', {}), ('query_structure_v1', {'graph_enabled': True})])
def test_legacy_or_inapplicable_mode_is_unchanged(mode, fields):
    state = current(mode=mode, **fields)
    calls = run(state, compact_output(Q0[:-1]))
    assert state.query_type == 'UNKNOWN' and state.rewrite_fallback_reason
    assert calls[0][1].max_completion_tokens == 150
    assert state.query_trace[-1]['decision'] == 'FALLBACK'
    assert 'raw_response_sha256' not in state.query_trace[-1]


def test_recovery_trace_does_not_leak_to_next_understand_call():
    state = current()
    run(state, compact_output(Q0[:-1]))
    assert state.query_trace[-1]['decision'] == 'RECOVERED'
    run(state, compact_output('changed question'))
    assert len(state.query_trace) == 1
    assert state.query_trace[-1]['decision'] == 'FALLBACK'
    assert 'raw_response_sha256' not in state.query_trace[-1]
