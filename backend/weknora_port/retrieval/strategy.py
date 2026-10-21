"""Opt-in query structure plans; rules validate closed syntax, not query types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re


MODE = 'query_structure_v1'


class QueryType(str, Enum):
    DIRECT = 'DIRECT'
    SIMPLE_COMPARE = 'SIMPLE_COMPARE'
    JOINT = 'JOINT'
    PARALLEL = 'PARALLEL'
    DEPENDENT = 'DEPENDENT'
    MIXED = 'MIXED'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class SubQuery:
    id: str
    query_text: str
    covers: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    object_span: tuple[int, int] | None = field(default=None, kw_only=True)
    dimension_span: tuple[int, int] | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class QueryPlan:
    q0: str
    effective_query: str
    query_type: str
    strategy: str
    subqueries: tuple[SubQuery, ...]
    constraints: tuple[str, ...]
    reason: str


def validate_strategy_mode(mode, source_evidence_mode):
    if type(mode) is not str or mode not in ('off', MODE):
        raise ValueError('RETRIEVAL_STRATEGY_MODE_INVALID')
    if mode == MODE and source_evidence_mode != 'bounded_source_v1':
        raise ValueError('RETRIEVAL_STRATEGY_REQUIRES_BOUNDED_SOURCE')


def strategy_applicable(state):
    """Keep independent attachment, web and graph pathways intact."""
    attachment = str(getattr(state, 'attachment_prompt', '') or '').strip()
    return not (getattr(state, 'images', ()) or getattr(state, 'image_description', '') or
        attachment not in ('', '<no_document_attached />', '<no_document_attached/>') or
        getattr(state, 'quoted_context', '') or getattr(state, 'web_search_enabled', False) or
        getattr(state, 'graph_enabled', False))


# These expressions parse only the explicitly admitted SIMPLE_COMPARE grammar.
# They never assign a model query type or extract an entity from history.
_CN = re.compile(r'(?:比较|对比)(?P<a>[^和与的，。！？；：、（）()\[\]{}<>\n\r]+)(?:和|与)'
    r'(?P<b>[^和与的，。！？；：、（）()\[\]{}<>\n\r]+)的'
    r'(?P<dimension>[^的，。！？；：、（）()\[\]{}<>\n\r]+)[。？]?')
_EN = re.compile(r'Compare (?P<a>[A-Za-z][A-Za-z0-9 .+#_-]{0,79}?) and '
    r'(?P<b>[A-Za-z][A-Za-z0-9 .+#_-]{0,79}?) in terms of '
    r'(?P<dimension>[A-Za-z][A-Za-z0-9 /+#_-]*(?: [A-Za-z][A-Za-z0-9 /+#_-]*)*)[.?]?', re.I)
_UNSAFE_EN = re.compile(r"\b(?:not|no|non|neither|nor|never|without|except|excluding|or|if|when|unless|while|"
    r"before|after|during|since|until|between|from|to|only|document|source|file|report|"
    r"according|provided|where|whose|which|that|with|within|under|over|year|years|"
    r"today|yesterday|current|latest|previous|historical|past|future|of|for|"
    r"explain|describe|provide|include|output|format|table|list|write|answer|show)\b|['’]s\b", re.I)
_UNSAFE_CN = re.compile(r'不|无|非|未|除|仅|只|或者|或|如果|若|当|条件|期间|之前|之后|以来|'
    r'截至|从|至|到|年|月|日|今天|昨天|当前|最新|过去|历史|未来|文档|文件|来源|原文|依据|根据|报告|且|同时|并且|请|说明|解释|输出|表格|给出|展示|回答|要求|限制')


def try_rule_compare(effective_query):
    """Return exactly two queries and original spans, or decline the whole rule.

    The deliberately small grammar refuses dates, conditions and nested
    possessives rather than claim semantic equivalence for unparsed text.
    """
    if not isinstance(effective_query, str) or effective_query != effective_query.strip():
        return None
    if not effective_query or len(effective_query) > 400 or re.search(r'\d', effective_query):
        return None
    match = _CN.fullmatch(effective_query)
    language = 'cn'
    if match:
        if _UNSAFE_CN.search(effective_query) or _UNSAFE_EN.search(effective_query):
            return None
    else:
        language = 'en'
        match = _EN.fullmatch(effective_query)
        if not match or _UNSAFE_EN.search(effective_query.replace(' in terms of ', ' ', 1)):
            return None
    a, b, dimension = (match[group].strip() for group in ('a', 'b', 'dimension'))
    if not a or not b or not dimension or a.casefold() == b.casefold():
        return None
    if language == 'en' and any(re.search(r'\b(?:and|in|terms)\b', x, re.I) for x in (a, b)):
        return None
    # Whitespace within an entity is allowed, but no unexplained syntactic tail.
    if any(len(value) > limit for value, limit in ((a, 80), (b, 80), (dimension, 160))):
        return None
    dstart, dend = match.span('dimension')
    draw = effective_query[dstart:dend]
    dimension_span = (dstart + len(draw) - len(draw.lstrip()),
                      dend - len(draw) + len(draw.rstrip()))
    rows = []
    for number, name, group in ((1, a, 'a'), (2, b, 'b')):
        question = name + '的' + dimension + '是什么？' if language == 'cn' else 'What are ' + name + ' ' + dimension + '?'
        start, end = match.span(group)
        raw = effective_query[start:end]
        start += len(raw) - len(raw.lstrip())
        end -= len(raw) - len(raw.rstrip())
        rows.append(SubQuery('sq-' + str(number), question, (name, dimension),
            object_span=(start, end), dimension_span=dimension_span))
    return tuple(rows)


def plan_query(q0, effective_query, query_type):
    try:
        kind = QueryType(query_type).value
    except (TypeError, ValueError):
        kind = QueryType.UNKNOWN.value
    if kind == QueryType.SIMPLE_COMPARE.value:
        rows = try_rule_compare(effective_query)
        if rows:
            return QueryPlan(q0, effective_query, kind, 'RULE_COMPARE', rows, (), 'CLOSED_COMPARE_RULE')
        reason = 'RULE_NOT_APPLICABLE'
    elif kind in (QueryType.PARALLEL.value, QueryType.MIXED.value):
        reason = 'MODEL_PLAN_NOT_ENABLED'
    elif kind == QueryType.DEPENDENT.value:
        reason = 'DEPENDENT_FIRST_ROUND'
    else:
        reason = 'SINGLE_QUERY'
    return QueryPlan(q0, effective_query, kind, 'PLAIN',
        (SubQuery('sq-1', effective_query, (effective_query,)),), (), reason)
