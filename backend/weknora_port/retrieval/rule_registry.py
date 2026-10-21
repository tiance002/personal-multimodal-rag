"""Versioned, closed query grammars; no retrieval, model call or type promotion.

Only the declarations in RULES are enabled.  A match proves syntactic slots,
not facts, evidence coverage or general natural-language equivalence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Callable


@dataclass(frozen=True)
class RuleMatch:
    status: str = 'NOT_APPLICABLE'
    reject_reason: str = 'SYNTAX_NOT_REGISTERED'
    objects: tuple[str, ...] = ()
    dimension: str = ''
    object_spans: tuple[tuple[int, int], ...] = ()
    dimension_span: tuple[int, int] | None = None
    full_match: str = ''
    rule_id: str = ''
    rule_version: str = ''
    language: str = ''
    query_type: str = ''

    @property
    def matched(self) -> bool:
        return self.status == 'MATCHED'

    def render_queries(self) -> tuple[str, ...]:
        return render_queries(self)


@dataclass(frozen=True)
class _Slots:
    objects: tuple[str, ...]
    dimension: str
    object_spans: tuple[tuple[int, int], ...]
    dimension_span: tuple[int, int]
    language: str


@dataclass(frozen=True)
class RuleDeclaration:
    rule_id: str
    rule_version: str
    query_type: str
    languages: tuple[str, ...]
    syntax: tuple[str, ...]
    minimum_objects: int
    maximum_objects: int
    required_attributes: tuple[str, ...]
    rejections: tuple[str, ...]
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]
    parser: Callable[[str], _Slots | None]
    templates: tuple[tuple[str, str], ...]


# Delimiters belong to grammar, never inferred from an arbitrary object name.
# Chinese two-object lists admit 和/与; three-object lists require two 顿号.
_CN_SLOT = r'[^的，。！？；：、（）()\[\]{}<>\n\r]+'
_CN_OBJECTS = r'[^的，。！？；：（）()\[\]{}<>\n\r]+'
_CN_COMPARE = (
    re.compile(r'(?:比较|对比)(?P<objects>' + _CN_OBJECTS + r')在(?P<dimension>' + _CN_SLOT + r')方面的区别[。？]?'),
    re.compile(r'(?P<objects>' + _CN_OBJECTS + r')在(?P<dimension>' + _CN_SLOT + r')方面有何区别[。？]?'),
    re.compile(r'(?:比较|对比)(?P<objects>' + _CN_OBJECTS + r')的(?P<dimension>' + _CN_SLOT + r')[。？]?'),
)
_CN_PARALLEL = (
    re.compile(r'分别(?:介绍|说明|查询)(?P<objects>' + _CN_OBJECTS + r')的(?P<dimension>' + _CN_SLOT + r')[。？]?'),
    re.compile(r'(?P<objects>' + _CN_OBJECTS + r')各自的(?P<dimension>' + _CN_SLOT + r')是什么[。？]?'),
)
_EN_OBJECT = r'[A-Za-z][A-Za-z0-9 .+#_/-]{0,79}?'
_EN_DIMENSION = r'[A-Za-z][A-Za-z0-9 /+#_-]{0,159}?'
_EN_COMPARE = (
    re.compile(r'Compare (?P<a>' + _EN_OBJECT + r'), (?P<b>' + _EN_OBJECT + r'), and (?P<c>' + _EN_OBJECT + r') in terms of (?P<dimension>' + _EN_DIMENSION + r')[.?]?', re.I),
    re.compile(r'Compare (?P<a>' + _EN_OBJECT + r') and (?P<b>' + _EN_OBJECT + r') in terms of (?P<dimension>' + _EN_DIMENSION + r')[.?]?', re.I),
    re.compile(r'Compare (?P<a>' + _EN_OBJECT + r') with (?P<b>' + _EN_OBJECT + r') on (?P<dimension>' + _EN_DIMENSION + r')[.?]?', re.I),
)

# These are rejection masks for closed slots, not a semantic type classifier.
_UNSAFE_EN = re.compile(
    r"\b(?:not|no|non|neither|nor|never|without|except|excluding|or|if|when|unless|while|"
    r"before|after|during|since|until|between|from|to|only|document|documents|source|sources|file|report|"
    r"according|provided|where|whose|which|that|in|on|at|with|within|under|over|year|years|month|date|"
    r"today|yesterday|current|latest|previous|historical|past|future|last|next|of|for|"
    r"explain|describe|provide|include|output|format|table|list|write|answer|show|"
    r"authorized|authorised|permission|permissions|user|tenant|scope|"
    r"joint|together|combined|relationship|relationships|correlation|interaction|interactions|compatibility|interoperability|"
    r"sum|total|average|ratio|difference|differences|percent|percentage|minimum|maximum|more|less|fewer)\b|['’]s\b", re.I)
_UNSAFE_CN = re.compile(
    r'不|无|非|未|除|仅|只|或者|或|如果|若|当|条件|期间|之前|之后|以来|截至|从|至|到|年|月|日|'
    r'今天|昨天|今年|去年|当前|最新|最近|过去|历史|未来|年份|年度|月份|日期|年前|年后|年内|年间|年初|年末|'
    r'文档|文件|来源|原文|依据|根据|报告|且|同时|并且|请|说明|解释|输出|表格|给出|展示|回答|要求|限制|'
    r'权限|授权|用户|租户|知识库|范围|协作|合作|共同|一起|兼容|相互|关系|相关性|总和|总计|合计|总成本|差额|比值|比例|平均|百分|至少|至多|大于|小于|多于|少于|超过|'
    r'比较|对比|分别|介绍|查询|各自|是什么|方面|差|总|多少')
_OBJECT_CONNECTOR = re.compile(r'\b(?:and|with|in|on|terms)\b', re.I)
_OBJECT_CONTROL = re.compile(r'所|主持|担任|负责|任职|创立|建立|参加|举办|举行|位于|属于|'
    r'\b(?:hosted|directed|founded|managed|led|located|served|belongs|owned)\b', re.I)
_WHOLE_OBJECT_PRONOUN = re.compile(r'(?:我|我们|你|你们|他|他们|她|她们|它|它们|这|这个|那个|这些|那些|前者|后者|其|'
    r'I|we|you|he|she|it|they|them|this|that|these|those|former|latter)', re.I)
_IDENTIFIER_WITH_DIGITS = re.compile(r'(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*(?![A-Za-z0-9])')


def _trimmed_span(text: str, span: tuple[int, int]) -> tuple[int, int]:
    start, end = span
    raw = text[start:end]
    return start + len(raw) - len(raw.lstrip()), end - len(raw) + len(raw.rstrip())


def _cn_slots(query: str, patterns: tuple[re.Pattern, ...]) -> _Slots | None:
    for pattern in patterns:
        match = pattern.fullmatch(query)
        if not match:
            continue
        raw = match['objects']
        if '、' in raw:
            # Mixed lists and two-item 顿号 lists are deliberately unsupported.
            if re.search(r'[和与]', raw) or raw.count('、') != 2:
                continue
            separators = tuple(re.finditer('、', raw))
        else:
            separators = tuple(re.finditer('[和与]', raw))
            if len(separators) != 1:
                continue
        spans = []
        base, cursor = match.start('objects'), 0
        for delimiter in (*separators, None):
            end = delimiter.start() if delimiter is not None else len(raw)
            spans.append(_trimmed_span(query, (base + cursor, base + end)))
            cursor = delimiter.end() if delimiter is not None else len(raw)
        dimension_span = _trimmed_span(query, match.span('dimension'))
        return _Slots(tuple(query[start:end] for start, end in spans),
            query[slice(*dimension_span)], tuple(spans), dimension_span, 'cn')
    return None


def _parse_compare(query: str) -> _Slots | None:
    chinese = _cn_slots(query, _CN_COMPARE)
    if chinese is not None:
        return chinese
    for pattern in _EN_COMPARE:
        match = pattern.fullmatch(query)
        if match:
            spans = tuple(_trimmed_span(query, match.span(name))
                for name in ('a', 'b', 'c') if name in match.groupdict())
            dimension_span = _trimmed_span(query, match.span('dimension'))
            return _Slots(tuple(query[start:end] for start, end in spans),
                query[slice(*dimension_span)], spans, dimension_span, 'en')
    return None


def _parse_parallel(query: str) -> _Slots | None:
    return _cn_slots(query, _CN_PARALLEL)


_REJECTIONS = ('ambiguous object connectors', 'nested attributes or relative clauses', 'unbound object pronouns', 'time constraints',
    'negation or alternatives', 'source or permission restrictions', 'conditions or extra requests',
    'quantitative or JOINT relations')
_TEMPLATES = (('cn', '{object}的{dimension}是什么？'),
              ('en', 'What is known about {object} regarding {dimension}?'))

RULES = (
    RuleDeclaration('COMPARE_SHARED', '1', 'SIMPLE_COMPARE', ('cn', 'en'),
        ('比较A和B的X', 'A与B在X方面有何区别', '比较A和B在X方面的区别', '比较A、B、C的X',
         'Compare A and B in terms of X', 'Compare A with B on X', 'Compare A, B, and C in terms of X'),
        2, 3, ('objects', 'shared_dimension', 'complete_character_spans'), _REJECTIONS,
        ('比较PostgreSQL和Redis的持久化与并发能力', 'Compare A with B on latency',
         'Compare A, B, and C in terms of latency'),
        ('比较A和B和C的性能', '比较A和B所主持会议的举办地点', '比较它和B的价格',
         'Compare A and B in terms of latency in 2024'),
        _parse_compare, _TEMPLATES),
    RuleDeclaration('PARALLEL_ATTRIBUTE', '1', 'SIMPLE_PARALLEL', ('cn',),
        ('分别介绍/说明/查询A和B的X', '分别介绍/说明/查询A、B、C的X', 'A和B各自的X是什么'),
        2, 3, ('objects', 'shared_dimension', 'complete_character_spans'), _REJECTIONS,
        ('分别介绍PostgreSQL和Redis的一致性', '分别查询A、B、C的性能', 'A和B各自的性能是什么'),
        ('分别介绍A和B和C的性能', '分别介绍A和它的价格', '分别查询A和B的性能及其文档依据'),
        _parse_parallel, _TEMPLATES[:1]),
)


def _slot_rejection(slots: _Slots, declaration: RuleDeclaration) -> str:
    if not declaration.minimum_objects <= len(slots.objects) <= declaration.maximum_objects or any(not value or len(value) > 80 for value in slots.objects):
        return 'OBJECT_COUNT_OR_LENGTH_INVALID'
    if len({value.casefold() for value in slots.objects}) != len(slots.objects):
        return 'DUPLICATE_OBJECT'
    if not slots.dimension or len(slots.dimension) > 160:
        return 'DIMENSION_INVALID'
    for value in (*slots.objects, slots.dimension):
        if _UNSAFE_EN.search(value) or _UNSAFE_CN.search(value):
            return 'PROTECTED_OR_COMPLEX_CONSTRAINT'
    if any(_OBJECT_CONNECTOR.search(value) or re.search(r'[和与、在]', value) for value in slots.objects):
        return 'AMBIGUOUS_OBJECT_CONNECTOR'
    if any(_WHOLE_OBJECT_PRONOUN.fullmatch(value) for value in slots.objects):
        return 'SUBJECT_IDENTITY_UNVERIFIED'
    if any(_OBJECT_CONTROL.search(value) for value in slots.objects):
        return 'NESTED_OBJECT_RELATION'
    if re.search(r'\d', slots.dimension):
        return 'QUANTITATIVE_OR_TIME_CONSTRAINT'
    for value in slots.objects:
        # Model names may contain numbers; standalone dates/quantities may not.
        identifiers = tuple(_IDENTIFIER_WITH_DIGITS.finditer(value))
        if any(not any(m.start() <= digit.start() < m.end() for m in identifiers)
               for digit in re.finditer(r'\d', value)):
            return 'QUANTITATIVE_OR_TIME_CONSTRAINT'
    return ''


def parse_rule(query, query_type=None) -> RuleMatch:
    """Parse enabled full-query syntax; explicit advice selects one family.

    query_type=None is diagnostic only.  Callers must not use that convenience
    to replace the model's type with a rule classification.
    """
    kind = getattr(query_type, 'value', query_type)
    declarations = RULES if kind is None else tuple(rule for rule in RULES if rule.query_type == kind)
    if not declarations:
        return RuleMatch(reject_reason='QUERY_TYPE_NOT_REGISTERED')
    explicit = declarations[0] if kind is not None else None
    def decline(reason, declaration=explicit):
        return RuleMatch(reject_reason=reason,
            rule_id=declaration.rule_id if declaration else '',
            rule_version=declaration.rule_version if declaration else '',
            query_type=declaration.query_type if declaration else '')
    if not isinstance(query, str) or not query or len(query) > 400 or query != query.strip():
        return decline('QUERY_INVALID')
    for declaration in declarations:
        slots = declaration.parser(query)
        if slots is None:
            continue
        rejection = _slot_rejection(slots, declaration)
        if rejection:
            return decline(rejection, declaration)
        return RuleMatch('MATCHED', '', slots.objects, slots.dimension, slots.object_spans,
            slots.dimension_span, query, declaration.rule_id, declaration.rule_version,
            slots.language, declaration.query_type)
    return decline('SYNTAX_NOT_REGISTERED')


def render_queries(match: RuleMatch) -> tuple[str, ...]:
    """Render exact source slots through the matched declaration's template."""
    if not isinstance(match, RuleMatch) or not match.matched:
        return ()
    declaration = next((rule for rule in RULES if rule.rule_id == match.rule_id
        and rule.rule_version == match.rule_version and rule.query_type == match.query_type), None)
    if declaration is None:
        return ()
    # A manually fabricated match cannot bypass the public parser's guards.
    if parse_rule(match.full_match, match.query_type) != match:
        return ()
    template = dict(declaration.templates).get(match.language)
    if template is None:
        return ()
    return tuple(template.format(object=name, dimension=match.dimension) for name in match.objects)


def render_classification_guidance() -> str:
    """Describe only enabled registry families using the same declarations."""
    lines = ['Closed rule families (query_type is advice; never force unsupported text into a family):']
    for rule in RULES:
        lines.append(rule.rule_id + '/v' + rule.rule_version + ' => ' + rule.query_type
            + '; syntax: ' + '; '.join(rule.syntax)
            + '; ' + str(rule.minimum_objects) + '-' + str(rule.maximum_objects)
            + ' distinct objects, one complete shared dimension, one query per object.')
        lines.append('Registered examples: ' + ' | '.join(rule.positive_examples))
        lines.append('Rejected examples: ' + ' | '.join(rule.negative_examples))
        lines.append('Reject: ' + '; '.join(rule.rejections) + '.')
        lines.append(json.dumps({'rewrite_query': rule.positive_examples[0], 'intent': 'kb_search',
            'image_description': '', 'query_type': rule.query_type}, ensure_ascii=False, separators=(',', ':')))
    lines.append('Do not reinterpret JOINT, DEPENDENT, MIXED or legacy PARALLEL as an enabled rule. '
        'Unsupported or ambiguous syntax remains UNKNOWN or its appropriate existing type; preserve the full question.')
    return '\n'.join(lines)
