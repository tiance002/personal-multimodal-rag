"""SIMULATED generation fixtures: check prompt contracts, not answer quality."""
import pytest

from backend.app.application.answer_hardening import detect_intents
from backend.app.application.question_checklist import explicit_question_clauses, explicit_question_checklist
from backend.app.application.quick_chain import QuickSettings
from backend.app.domain.scope import Scope
from backend.tests.test_quick_chain_quality import _chain, RecordingAnswerModel


@pytest.mark.parametrize('question,expected', [
    ('表中数值分别是多少？最高的是哪个？', ('表中数值分别是多少？', '最高的是哪个？')),
    ('本机地址与容器地址不同吗？原生服务会自动读配置吗？', ('本机地址与容器地址不同吗？', '原生服务会自动读配置吗？')),
    (' Is caching enabled? Is backup enabled? ', ('Is caching enabled?', 'Is backup enabled?')),
    ('关系是什么？', ()),
    ('缓存和备份的关系是什么？', ()),
    ('分别列出缓存、备份与日志。', ()),
    ('如何备份？请给出完整循环。', ()),
    ('请解释“备份吗？缓存吗？”', ()),
    ('`enabled?`是否开启？怎么配置？', ()),
    ('访问https://example.invalid/?q=x？是否有效？', ()),
    ('什么？？如何操作？', ()),
    ('？？', ()),
    ('？问题甲？问题乙？', ()),
    ('上一轮问题背景（不是证据）：甲是什么？乙是什么？\n只回答当前问题：它是什么？', ()),
    ('问题？' * 7, ()),
    ('甲' * 4096 + '？乙？', ()),
])
def test_literal_clause_bounds_and_ambiguity(question, expected):
    assert explicit_question_clauses(question) == expected


def test_existing_multi_intent_and_sleep_question_are_unchanged():
    question = '这份笔记如何说明睡眠与记忆的关系？还建议睡多久？'
    plan = detect_intents(question)
    assert len(plan.intents) == 2
    assert explicit_question_checklist(question, len(plan.intents)) == ''


class BudgetRecorder(RecordingAnswerModel):
    def __init__(self):
        super().__init__('question 缓存用于减少重复读取 [E1]。')
        self.limits = []

    def answer_with_budget(self, prompt, timeout_seconds, max_tokens):
        self.limits.append(max_tokens)
        return self.answer(prompt, timeout_seconds)


def invoke_trial(settings, question='question 缓存有效吗？备份已开启吗？'):
    model = BudgetRecorder()
    chain = _chain(['question 缓存用于减少重复读取。'], model)
    result = chain.invoke(question, Scope.from_ids(['kb']), settings=settings)
    assert result.error_code is None
    assert result.citations == ('E1',)
    assert result.trace.model_calls == 1
    assert len(model.prompts) == 1
    return model


def test_default_off_identical_explicit_false():
    default = invoke_trial(QuickSettings())
    disabled = invoke_trial(QuickSettings(explicit_question_checklist_enabled=False))
    assert default.prompts == disabled.prompts
    assert default.limits == disabled.limits
    assert 'Explicit question clauses' not in default.prompts[0]


def test_enabled_preserves_question_context_budget_and_one_call():
    disabled = invoke_trial(QuickSettings())
    enabled = invoke_trial(QuickSettings(explicit_question_checklist_enabled=True))
    before, after = disabled.prompts[0], enabled.prompts[0]
    assert enabled.limits == disabled.limits == [512]
    assert after.split('\nEvidence:\n', 1)[1] == before.split('\nEvidence:\n', 1)[1]
    assert '\nQuestion: question 缓存有效吗？备份已开启吗？' in after
    assert '1. question 缓存有效吗？\n2. 备份已开启吗？' in after
    appendix = explicit_question_checklist('question 缓存有效吗？备份已开启吗？', 1)
    assert after.replace(appendix, '', 1) == before


def test_enabled_does_not_authorize_invalid_citation():
    model = RecordingAnswerModel('question 缓存用于减少重复读取 [E9]。')
    chain = _chain(['question 缓存用于减少重复读取。'], model)
    result = chain.invoke('question 缓存有效吗？备份已开启吗？', Scope.from_ids(['kb']),
                          settings=QuickSettings(explicit_question_checklist_enabled=True))
    assert result.error_code == 'INVALID_CITATION'
    assert result.citations == ()
    assert len(model.prompts) == 1


@pytest.mark.parametrize('question', [
    '《缓存开启吗？备份开启吗？》是什么意思？',
    '〈缓存开启吗？备份开启吗？〉是什么意思？',
    '（缓存开启吗？备份开启吗？）是什么意思？',
    '(Is caching enabled? Is backup enabled?) What does it mean?',
    '【缓存开启吗？备份开启吗？】是什么意思？',
    '[Is caching enabled? Is backup enabled?] What does it mean?',
    '请勿回答这些问题：缓存开启吗？备份开启吗？',
    '不要回答缓存开启吗？备份开启吗？',
    '不用回答缓存开启吗？备份开启吗？',
    '无需回答缓存开启吗？备份开启吗？',
    '不是让你回答缓存开启吗？备份开启吗？',
    '仅翻译缓存开启吗？备份开启吗？',
    '只分析题面缓存开启吗？备份开启吗？',
    'Do not answer: Is caching enabled? Is backup enabled?',
    'Only translate Is caching enabled? Is backup enabled?',
    '仅在缓存失效时回答：备份开启吗？日志开启吗？',
    '如果缓存失效，备份开启吗？日志开启吗？',
    '只有缓存失效才回答备份开启吗？日志开启吗？',
    '当缓存失效时备份开启吗？日志开启吗？',
    '在缓存失效的情况下备份开启吗？日志开启吗？',
    '除非缓存失效，备份开启吗？日志开启吗？',
    'If caching fails, is backup enabled? Is logging enabled?',
    'Only when caching fails, is backup enabled? Is logging enabled?',
    'Is backup enabled? If caching fails, is logging enabled?',
])
def test_outer_scope_or_enclosure_is_never_split(question):
    assert explicit_question_clauses(question) == ()
    assert explicit_question_checklist(question, 1) == ''


@pytest.mark.parametrize('question,expected', [
    ('缓存没开启吗？备份未启用吗？', ('缓存没开启吗？', '备份未启用吗？')),
    ('系统没有回答吗？备份不是已启用吗？', ('系统没有回答吗？', '备份不是已启用吗？')),
    ('Is caching not enabled? Is backup disabled?', ('Is caching not enabled?', 'Is backup disabled?')),
])
def test_ordinary_negative_questions_keep_literal_negation(question, expected):
    assert explicit_question_clauses(question) == expected
    appendix = explicit_question_checklist(question, 1)
    assert '\n'.join(f'{index}. {clause}' for index, clause in enumerate(expected, 1)) in appendix


# Test-only measurements captured by the offline receipt plugin. All lengths
# are Python string character counts; no tokenizer or real model is used.
PROMPT_CHARACTER_SAMPLES = []


@pytest.mark.parametrize('question', [
    'question 《缓存开启吗？备份开启吗？》是什么意思？',
    'question 请勿回答这些问题：缓存开启吗？备份开启吗？',
    'question 不要回答缓存开启吗？备份开启吗？',
    'question 仅翻译缓存开启吗？备份开启吗？',
    'question 仅在缓存失效时回答：备份开启吗？日志开启吗？',
    'question 如果缓存失效，备份开启吗？日志开启吗？',
])
def test_ambiguous_input_preserves_entire_original_prompt(question):
    disabled = invoke_trial(QuickSettings(), question)
    enabled = invoke_trial(QuickSettings(explicit_question_checklist_enabled=True), question)
    before, after = disabled.prompts[0], enabled.prompts[0]
    assert before == after
    assert f'\nQuestion: {question}' in after
    assert disabled.limits == enabled.limits
    PROMPT_CHARACTER_SAMPLES.append({'question': question, 'input_characters': len(question),
                                    'disabled_prompt_characters': len(before),
                                    'enabled_prompt_characters': len(after), 'extra_characters': 0})


@pytest.mark.parametrize('question,expected_clause_count', [
    ('甲' * 4093 + '？乙？', 2),  # Exactly 4096 characters.
    ('甲' * 4085 + '？乙？丙？丁？戊？己？', 6),  # Six clauses, 4096 characters.
    ('甲' * 4094 + '？乙？', 0),  # 4097 characters must add nothing.
])
def test_question_and_appendix_character_boundaries(question, expected_clause_count):
    assert len(explicit_question_clauses(question)) == expected_clause_count
    appendix = explicit_question_checklist(question, 1)
    # These long inputs retain their original case identities and clause
    # expectations, but their complete appendices exceed the new 512-char cap.
    assert appendix == ''


@pytest.mark.parametrize('question,expected_extra_characters', [
    ('question 缓存有效吗？备份已开启吗？', 159),
    ('question 缓存没开启吗？备份未启用吗？', 160),
    ('question ' + '甲' * 4076 + '？乙？丙？丁？戊？己？', 4249),
])
def test_added_prompt_characters_are_bounded_without_changing_evidence(question, expected_extra_characters):
    # Keep the legacy 4249 parametrization identity while updating its expected
    # behavior: an oversized appendix contributes zero characters in its entirety.
    if expected_extra_characters > 512:
        expected_extra_characters = 0
    disabled = invoke_trial(QuickSettings(), question)
    enabled = invoke_trial(QuickSettings(explicit_question_checklist_enabled=True), question)
    before, after = disabled.prompts[0], enabled.prompts[0]
    appendix = explicit_question_checklist(question, 1)
    assert len(after) - len(before) == expected_extra_characters
    assert len(appendix) == expected_extra_characters <= 512
    assert after.replace(appendix, '', 1) == before
    assert f'\nQuestion: {question}' in after
    assert enabled.limits == disabled.limits
    PROMPT_CHARACTER_SAMPLES.append({'question': question, 'input_characters': len(question),
                                    'disabled_prompt_characters': len(before),
                                    'enabled_prompt_characters': len(after),
                                    'extra_characters': len(after) - len(before)})


@pytest.mark.parametrize('appendix_characters', [511, 512, 513])
@pytest.mark.parametrize('unicode_text', ['甲', '🙂', 'e\u0301'])
def test_final_appendix_cap_preserves_unicode_and_original_prompt(appendix_characters, unicode_text):
    # Independently counted: 130 header characters, 6 numbering characters,
    # 1 separator newline, and 12 non-padding question characters = 149.
    padding_characters = appendix_characters - 149
    padding = (unicode_text * padding_characters)[:padding_characters]
    first_clause = 'question ' + padding + '？'
    question = first_clause + '乙？'
    complete_appendix = (
        '\nExplicit question clauses (verbatim user requests; answer each from '
        'evidence with citations, or mark unsupported parts unknown):\n'
        + '1. ' + first_clause + '\n2. 乙？'
    )
    assert len(complete_appendix) == appendix_characters
    assert explicit_question_clauses(question) == (first_clause, '乙？')
    expected_appendix = complete_appendix if appendix_characters <= 512 else ''
    assert explicit_question_checklist(question, 1) == expected_appendix

    default = invoke_trial(QuickSettings(), question)
    disabled = invoke_trial(QuickSettings(explicit_question_checklist_enabled=False), question)
    enabled = invoke_trial(QuickSettings(explicit_question_checklist_enabled=True), question)
    before, after = disabled.prompts[0], enabled.prompts[0]
    assert default.prompts == disabled.prompts
    assert default.limits == disabled.limits == enabled.limits == [512]
    assert 'Explicit question clauses' not in before
    assert f'\nQuestion: {question}' in before
    assert f'\nQuestion: {question}' in after
    assert after.split('\nEvidence:\n', 1)[1] == before.split('\nEvidence:\n', 1)[1]
    assert after.replace(expected_appendix, '', 1) == before
    assert len(after) - len(before) == len(expected_appendix)
    if not expected_appendix:
        assert after == before
        assert 'Explicit question clauses' not in after
    PROMPT_CHARACTER_SAMPLES.append({'question': question, 'input_characters': len(question),
                                    'candidate_appendix_characters': appendix_characters,
                                    'unicode_text': unicode_text,
                                    'disabled_prompt_characters': len(before),
                                    'enabled_prompt_characters': len(after),
                                    'extra_characters': len(after) - len(before)})


@pytest.mark.parametrize('appendix_characters', [511, 512, 513])
def test_six_clause_appendix_cap_includes_all_numbering_and_separators(appendix_characters):
    # 130 header + 18 numbering + 5 separator + 20 non-padding question chars.
    first_clause = 'question ' + '甲' * (appendix_characters - 173) + '？'
    clauses = (first_clause, '乙？', '丙？', '丁？', '戊？', '己？')
    question = ''.join(clauses)
    complete_appendix = (
        '\nExplicit question clauses (verbatim user requests; answer each from '
        'evidence with citations, or mark unsupported parts unknown):\n'
        + '1. ' + first_clause + '\n2. 乙？\n3. 丙？\n4. 丁？\n5. 戊？\n6. 己？'
    )
    assert len(complete_appendix) == appendix_characters
    assert explicit_question_clauses(question) == clauses
    expected_appendix = complete_appendix if appendix_characters <= 512 else ''
    assert explicit_question_checklist(question, 1) == expected_appendix
    disabled = invoke_trial(QuickSettings(), question)
    enabled = invoke_trial(QuickSettings(explicit_question_checklist_enabled=True), question)
    before, after = disabled.prompts[0], enabled.prompts[0]
    assert f'\nQuestion: {question}' in after
    assert after.replace(expected_appendix, '', 1) == before
    assert enabled.limits == disabled.limits == [512]
    assert len(after) - len(before) == len(expected_appendix)
    PROMPT_CHARACTER_SAMPLES.append({'question': question, 'input_characters': len(question),
                                    'candidate_appendix_characters': appendix_characters,
                                    'clause_count': 6,
                                    'disabled_prompt_characters': len(before),
                                    'enabled_prompt_characters': len(after),
                                    'extra_characters': len(after) - len(before)})
