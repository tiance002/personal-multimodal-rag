"""Runtime literal-rewrite instructions and preserved C2 rejection evidence."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest

from backend.weknora_port.retrieval.query import (
    _REWRITE_GUARD_PROMPT, understand_query, validate_rewrite,
)
from test_pipeline import CTX, Model, Registry, state


EN_Q0 = 'What did she direct from 2011–2014?'
EN_REWRITE = 'What did Ada Rowan direct from 2011–2014?'
CN_Q0 = '它在2020年之后有什么缺点？不要讨论多标签。'
CN_REWRITE = '朴素贝叶斯分类在2020年之后有什么缺点？不要讨论多标签。'


def history(question):
    return SimpleNamespace(query=question, answer='Dialogue context only, never source evidence.')


def prompt_examples():
    return [json.loads(match[0]) for match in re.finditer(r'\{[^{}]*\}', _REWRITE_GUARD_PROMPT)]


def test_runtime_task_one_explicitly_overrides_broad_legacy_paraphrase_examples():
    assert 'Runtime Task 1 OVERRIDE' in _REWRITE_GUARD_PROMPT
    assert re.search(r'overrides.*earlier.*(?:instructions|examples)', _REWRITE_GUARD_PROMPT, re.I)
    assert 'copy q0 byte-for-byte' in _REWRITE_GUARD_PROMPT
    assert 'at most ONE' in _REWRITE_GUARD_PROMPT
    assert 'latest independent authorized USER question' in _REWRITE_GUARD_PROMPT
    assert 'Never take an antecedent only from an assistant answer' in _REWRITE_GUARD_PROMPT


def test_prompt_preserves_input_language_and_all_literal_constraints():
    for item in ('language', 'punctuation', 'range glyphs', 'numbers', 'negations', 'restrictions'):
        assert item in _REWRITE_GUARD_PROMPT
    assert 'Do not translate' in _REWRITE_GUARD_PROMPT
    assert 'keyword query' in _REWRITE_GUARD_PROMPT
    assert 'rewrite grammar' in _REWRITE_GUARD_PROMPT
    assert 'If ambiguous, keep q0 unchanged' in _REWRITE_GUARD_PROMPT
    assert 'Do not cross a newer independent topic' in _REWRITE_GUARD_PROMPT


def test_format_examples_use_exact_three_field_json_and_preserve_en_cn_questions():
    examples = prompt_examples()
    expected = {'rewrite_query', 'intent', 'image_description'}
    assert examples and all(set(example) == expected for example in examples)
    assert any(example['rewrite_query'] == EN_REWRITE for example in examples)
    assert any(example['rewrite_query'] == CN_REWRITE for example in examples)
    assert 'Format-only examples, not source facts' in _REWRITE_GUARD_PROMPT
    assert 'exactly three fields' in _REWRITE_GUARD_PROMPT
    assert 'No Markdown fences' in _REWRITE_GUARD_PROMPT


@pytest.mark.parametrize('q0,candidate,previous', [
    (EN_Q0, EN_REWRITE, 'Who is Ada Rowan?'),
    (CN_Q0, CN_REWRITE, '什么是朴素贝叶斯分类？'),
])
def test_format_examples_satisfy_unchanged_literal_validator(q0, candidate, previous):
    current = state(query=q0, history=[history(previous)])
    assert validate_rewrite(current, candidate) == ''


@pytest.mark.parametrize('q0,candidate,previous,reason', [
    ('What did she direct from 2007–2010?',
     'Jeanne Marie Beaumont从2007年到2010年导演了什么？',
     'Who is Jeanne Marie Beaumont?', 'EXPLICIT_NUMBER_OR_TIME_CHANGED'),
    ('Which poetry collections has she written?',
     'Jeanne Marie Beaumont poetry collections',
     'Who is Jeanne Marie Beaumont?', 'CURRENT_QUESTION_CHANGED'),
    ('Compare a Leyden jar and Franklin bells.',
     'Compare a Leyden jar and Franklin bells',
     'Who is Jeanne Marie Beaumont?', 'REWRITE_NOT_LITERAL_REFERENCE_SUBSTITUTION'),
])
def test_actual_c2_bad_rewrites_remain_rejected_and_use_q0(q0, candidate, previous, reason):
    current = state(query=q0, enable_rewrite=True, chat_model_id='cheap',
                    history=[history(previous)])
    assert validate_rewrite(current, candidate) == reason
    output = json.dumps({'rewrite_query':candidate, 'intent':'kb_search', 'image_description':''})
    model = Model([output])
    asyncio.run(understand_query(current, CTX, Registry(model)))
    assert current.query == current.rewrite_query == q0
    assert current.rewrite_fallback_reason == reason
    messages, options, context = model.calls[0]
    assert messages[0].content.endswith(_REWRITE_GUARD_PROMPT)
    assert options.max_completion_tokens == 150 and options.thinking is False
    assert context.request_id == CTX.request_id + '/query_rewrite'


def test_ambiguous_reference_never_selects_a_single_model_suggestion():
    q0 = 'What is it used for?'
    current = state(query=q0, history=[history('Compare the Rowan method and the Cedar method.')])
    assert validate_rewrite(current, 'What is the Rowan method used for?') == 'AMBIGUOUS_REFERENCE'
    assert validate_rewrite(current, q0) == 'AMBIGUOUS_REFERENCE'


def test_independent_q0_keeps_terminal_punctuation_and_original_language():
    q0 = 'Compare the Rowan method and the Cedar method.'
    current = state(query=q0, history=[history('Who is Ada Rowan?')])
    assert validate_rewrite(current, q0) == ''
    assert validate_rewrite(current, q0[:-1])
    assert validate_rewrite(current, '比较Rowan方法和Cedar方法。')
