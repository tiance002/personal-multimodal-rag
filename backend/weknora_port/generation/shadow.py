"""Versioned pre-generation observations and an opt-in, non-dispatching candidate.

No Gold, answer, case identifier, or post-generation signals are accepted.
Unknown semantics stay unknown; counts are not evidence sufficiency.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

SNAPSHOT_VERSION = 'pre-generation-input/v1'
SHADOW_VERSION = 'shadow-structure/v1'
QUERY_TYPES = frozenset(('DIRECT', 'SIMPLE_COMPARE', 'SIMPLE_PARALLEL', 'JOINT',
                         'PARALLEL', 'DEPENDENT', 'MIXED', 'UNKNOWN'))


def snapshot_from_state(state, evidence):
    """Only server-produced, validated state. Does not classify a query again."""
    trace = getattr(state, 'query_trace', ())
    kind = getattr(state, 'query_type', 'UNKNOWN')
    observed = (getattr(state, 'retrieval_strategy_mode', 'off') == 'query_structure_v1'
                and kind in QUERY_TYPES and any(row.get('contract') == 'qu_v2'
                and row.get('query_type') == kind for row in trace))
    plan = getattr(state, 'strategy_plan', None)
    spans = []
    if plan and plan.rule_status == 'MATCHED' and plan.rule_full_match:
        spans = [list(s.object_span) for s in plan.subqueries if s.object_span is not None]
    rows = tuple(evidence)
    identities = {(e.knowledge_base_id, e.knowledge_id,
                   str((e.metadata or {}).get('source_version', ''))) for e in rows}
    identity_known = bool(rows) and all(all(key) for key in identities)
    # The present guard proves a bounded reference ambiguity, not general
    # unambiguity. A negative result must not become a universal False.
    from ..retrieval.query import requires_ambiguity_clarification
    ambiguity = True if requires_ambiguity_clarification(state) else None
    stages = getattr(state, 'strategy_trace', ())
    branches = []
    for row in stages:
        stage = row.get('stage')
        if stage in ('strategy_round', 'strategy_bridge', 'strategy_rule'):
            branches.append(stage)
    return {
        'version': SNAPSHOT_VERSION, 'timing': 'BEFORE_FINAL_GENERATION',
        'query_type': kind if observed else None,
        'query_type_status': 'OBSERVED_QU_V2' if observed else 'NOT_RECORDED',
        'explicit_object_count': len(spans) if spans else None,
        'object_spans': spans,
        'rule_status': plan.rule_status if plan else 'NOT_EVALUATED',
        'evidence_chunk_count': len(rows),
        'distinct_document_versions': len(identities) if identity_known else None,
        'source_count_unit': 'unique(KB,document,source_version)',
        'bounded_reference_ambiguity': ambiguity,
        'general_ambiguity': None,
        'evidence_sufficiency': None,
        'retrieval_branches': sorted(set(branches)),
        'retrieval_units_used': getattr(state, 'strategy_units_used', 0),
    }


def enrich_snapshot(snapshot, prepared, features):
    """Copy an allowlist; never echo arbitrary metadata, prompts or identities."""
    keys = ('version', 'timing', 'query_type', 'query_type_status',
            'explicit_object_count', 'object_spans', 'rule_status',
            'evidence_chunk_count', 'distinct_document_versions', 'source_count_unit',
            'bounded_reference_ambiguity', 'general_ambiguity', 'evidence_sufficiency',
            'retrieval_branches', 'retrieval_units_used')
    result = {k: deepcopy(snapshot[k]) for k in keys if k in snapshot}
    result.update(version=SNAPSHOT_VERSION, timing='BEFORE_FINAL_GENERATION',
                  prompt_utf8_bytes=len(prepared.full_prompt.encode('utf-8')),
                  prompt_sha256=hashlib.sha256(prepared.full_prompt.encode('utf-8')).hexdigest(),
                  estimated_input_tokens=features.estimated_input_tokens,
                  token_estimator=features.estimator,
                  explicit_condition_count=features.condition_count,
                  condition_status=features.statuses[1])
    return result


def shadow_decide(snapshot, *, baseline_role, mode='off'):
    """Candidate only: never chooses the role dispatched by the coordinator.

The narrow rule nominates a validated multi-object comparison/parallel plan
with at least two document versions. This is a structural signal, never a
claim that the evidence is sufficient or that Expensive improves this answer.
"""
    if mode not in ('off', 'shadow_structure_v1'):
        raise ValueError('SHADOW_ROUTING_MODE_INVALID')
    result = {'version': SHADOW_VERSION, 'mode': mode,
              'baseline_role': baseline_role, 'candidate_role': baseline_role,
              'dispatch_changed': False, 'evidence_sufficiency': 'NOT_ASSESSED'}
    if mode == 'off':
        return {**result, 'status': 'DISABLED', 'reason': 'SHADOW_DISABLED'}
    if snapshot.get('bounded_reference_ambiguity') is True:
        return {**result, 'status': 'ABSTAIN', 'reason': 'REFERENCE_AMBIGUOUS'}
    kind = snapshot.get('query_type')
    if (snapshot.get('query_type_status') != 'OBSERVED_QU_V2' or kind not in QUERY_TYPES
            or kind == 'UNKNOWN'):
        return {**result, 'status': 'NOT_EVALUABLE', 'reason': 'QUERY_TYPE_UNAVAILABLE'}
    if kind not in ('SIMPLE_COMPARE', 'SIMPLE_PARALLEL'):
        return {**result, 'status': 'NO_MATCH', 'reason': 'OUTSIDE_NARROW_RULE'}
    count, documents = snapshot.get('explicit_object_count'), snapshot.get('distinct_document_versions')
    if snapshot.get('rule_status') != 'MATCHED' or type(count) is not int or not 2 <= count <= 3:
        return {**result, 'status': 'NOT_EVALUABLE', 'reason': 'VALIDATED_OBJECTS_UNAVAILABLE'}
    if type(documents) is not int or documents < 0:
        return {**result, 'status': 'NOT_EVALUABLE', 'reason': 'SOURCE_IDENTITY_COUNT_UNAVAILABLE'}
    if documents < 2:
        return {**result, 'status': 'NO_MATCH', 'reason': 'SINGLE_SOURCE_VERSION'}
    return {**result, 'status': 'CANDIDATE', 'reason': 'MULTI_OBJECT_MULTI_DOCUMENT',
            'candidate_role': 'expensive'}
