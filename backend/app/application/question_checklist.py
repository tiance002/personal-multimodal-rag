"""Optional literal prompt checklist; no retrieval, inference or answer grading."""
from __future__ import annotations

import re


MAX_QUESTION_CHARACTERS = 4096
MAX_CHECKLIST_CHARACTERS = 512

# Question marks inside titles, quotations or parenthetical text do not make
# that text independent requests. Unmatched delimiters are ambiguous too.
_ENCLOSURES = frozenset('\"\'`“”‘’「」『』《》〈〉<>（）()【】[]{}〔〕〖〗')
_OUTER_REQUEST = re.compile(
    r'(?:请勿|勿|不要|别|禁止|不必|不用|无需|不需要|不得|不准|不应)'
    r'[^？?\n]*(?:回答|作答|解答|答复)'
    r'|(?:不是|并非)[^？?\n]*(?:让|要|要求|请|在问)[^？?\n]*(?:回答|作答|解答|问)'
    r'|翻译|题面|问句|(?:只|仅)[^？?\n]*(?:分析|讨论|复述|改写|评价)'
    r'|\b(?:do\s+not|never)\s+(?:answer|respond)\b'
    r'|\b(?:only|just)\s+(?:translate|analy[sz]e|paraphrase|discuss)\b',
    re.I,
)
_SHARED_CONDITION = re.compile(
    r'如果|假如|假设|倘若|只有|仅有|仅在|只在|仅当|只当|除非|若'
    r'|当[^？?\n]*时|在[^？?\n]*(?:情况下|前提下)'
    r'|\b(?:if|when|unless|provided|assuming|during|after|before)\b',
    re.I,
)


def explicit_question_clauses(question: str) -> tuple[str, ...]:
    """Conservatively preserve complete, explicitly punctuated subquestions.

    Enclosures, outer instructions and conditional inputs are left intact for
    the existing planner. Never strip or distribute a shared qualification;
    these conservative guards are not a general natural-language parser.
    """
    if not question or len(question) > MAX_QUESTION_CHARACTERS:
        return ()
    if any(mark in _ENCLOSURES for mark in question):
        return ()
    # Colons/semicolons may introduce a scope for all following questions.
    # Ordinary negative interrogatives remain verbatim, rather than rejecting
    # every occurrence of a negative word such as 没/未/没有/不是/not.
    if any(mark in question for mark in ':：;；') or _OUTER_REQUEST.search(question) or _SHARED_CONDITION.search(question):
        return ()
    if re.search(r'https?://|上一轮问题背景（不是证据）：|\n只回答当前问题：', question, re.I):
        return ()
    clauses = tuple(m.group().strip() for m in re.finditer(r'[^？?]+[？?]', question))
    if not 2 <= len(clauses) <= 6:
        return ()
    # Require the entire question to be covered, with no trailing imperative,
    # adjacent empty question marks or silently omitted punctuation.
    if re.sub(r'\s+', '', ''.join(clauses)) != re.sub(r'\s+', '', question):
        return ()
    if any(not clause[:-1].strip(' ，,。；;：:！!') for clause in clauses):
        return ()
    return clauses


def explicit_question_checklist(question: str, legacy_intent_count: int) -> str:
    if legacy_intent_count != 1:
        return ''
    clauses = explicit_question_clauses(question)
    if not clauses:
        return ''
    checklist = (
        '\nExplicit question clauses (verbatim user requests; answer each from '
        'evidence with citations, or mark unsupported parts unknown):\n'
        + '\n'.join(f'{index}. {clause}' for index, clause in enumerate(clauses, 1))
    )
    # Character bound only: this is not a tokenizer or a model-window check.
    return checklist if len(checklist) <= MAX_CHECKLIST_CHARACTERS else ''
