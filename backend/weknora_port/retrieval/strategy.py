"""Opt-in query structure plans; rules validate closed syntax, not query types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


MODE = 'query_structure_v1'


class QueryType(str, Enum):
    DIRECT = 'DIRECT'
    SIMPLE_COMPARE = 'SIMPLE_COMPARE'
    SIMPLE_PARALLEL = 'SIMPLE_PARALLEL'
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
    rule_id: str = field(default='', kw_only=True)
    rule_version: str = field(default='', kw_only=True)
    rule_status: str = field(default='NOT_EVALUATED', kw_only=True)
    reject_reason: str = field(default='', kw_only=True)
    rule_full_match: bool = field(default=False, kw_only=True)


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


def _subqueries(match):
    if not match.matched:
        return None
    queries = match.render_queries()
    if not 2 <= len(match.objects) <= 3 or len(queries) != len(match.objects):
        raise ValueError('RULE_RESULT_CONTRACT_INVALID')
    return tuple(SubQuery('sq-' + str(i), query, (name, match.dimension),
        object_span=span, dimension_span=match.dimension_span)
        for i, (name, span, query) in enumerate(zip(match.objects, match.object_spans, queries), 1))


def try_rule_compare(effective_query):
    """Compatibility wrapper over the versioned closed comparison registry."""
    from .rule_registry import parse_rule
    return _subqueries(parse_rule(effective_query, QueryType.SIMPLE_COMPARE.value))


def plan_query(q0, effective_query, query_type):
    try:
        kind = QueryType(query_type).value
        reject_reason = 'QUERY_TYPE_NOT_RULE_ELIGIBLE'
    except (TypeError, ValueError):
        kind = QueryType.UNKNOWN.value
        reject_reason = 'INVALID_QUERY_TYPE'
    metadata = {'reject_reason': reject_reason}
    if kind in (QueryType.SIMPLE_COMPARE.value, QueryType.SIMPLE_PARALLEL.value):
        from .rule_registry import parse_rule
        match = parse_rule(effective_query, kind)
        metadata = dict(rule_id=match.rule_id, rule_version=match.rule_version,
            rule_status=match.status, reject_reason=match.reject_reason,
            rule_full_match=bool(match.full_match))
        rows = _subqueries(match)
        if rows:
            strategy = 'RULE_COMPARE' if kind == QueryType.SIMPLE_COMPARE.value else 'RULE_PARALLEL'
            reason = 'CLOSED_COMPARE_RULE' if kind == QueryType.SIMPLE_COMPARE.value else 'CLOSED_PARALLEL_RULE'
            return QueryPlan(q0, effective_query, kind, strategy, rows, (), reason, **metadata)
        reason = 'RULE_NOT_APPLICABLE'
    elif kind in (QueryType.PARALLEL.value, QueryType.MIXED.value):
        reason = 'MODEL_PLAN_NOT_ENABLED'
    elif kind == QueryType.DEPENDENT.value:
        reason = 'DEPENDENT_FIRST_ROUND'
    else:
        reason = 'SINGLE_QUERY'
    # Other model labels are never locally promoted. Potential rule matches
    # for those labels are measured only by the separate offline evaluation.
    return QueryPlan(q0, effective_query, kind, 'PLAIN',
        (SubQuery('sq-1', effective_query, (effective_query,)),), (), reason, **metadata)


def plan_rule_trace(plan):
    """Finite private planning metadata; no user text in the strategy trace."""
    import hashlib
    return {'stage': 'strategy_rule', 'rule_id': plan.rule_id,
        'rule_version': plan.rule_version, 'rule_status': plan.rule_status,
        'reject_reason': plan.reject_reason,
        'subquery_count': len(plan.subqueries) if plan.rule_status == 'MATCHED' else 0,
        'retrieval_units_planned': len(plan.subqueries), 'full_match': plan.rule_full_match,
        'object_spans': [list(sub.object_span) for sub in plan.subqueries if sub.object_span is not None],
        'dimension_span': next((list(sub.dimension_span) for sub in plan.subqueries
                                if sub.dimension_span is not None), None),
        'effective_query_sha256': hashlib.sha256(plan.effective_query.encode('utf8')).hexdigest(),
        'q0_sha256': hashlib.sha256(plan.q0.encode('utf8')).hexdigest()}
