"""Bounded serial retrieval units and immutable-source aggregation.

No query understanding, intermediate generation, bridge extraction, database
writes, or recursive Pipeline.run calls are performed in this module.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..models.types import CallCancelled, RunDeadlineExceeded, ProviderError
from .search import SearchNothing, RetrievalScopeError, validate_results
from .types import ContextBudgetError
from .strategy import QueryPlan

MAX_RETRIEVAL_UNITS = 3


@dataclass(frozen=True)
class RoundResult:
    state: Any
    status: str = 'OK'
    reason: str = ''

    @property
    def search_results(self): return self.state.search_results

    @property
    def rerank_results(self): return self.state.rerank_results

    @property
    def merge_results(self): return self.state.merge_results


@dataclass(frozen=True)
class StrategyResult:
    plan: QueryPlan
    rounds: tuple[RoundResult, ...]
    selected: tuple[Any, ...]
    coverage: dict
    trace: tuple[dict, ...]
    units_used: int
    queries_seen: frozenset[str]


def _check_context(context):
    if context.cancelled is not None and context.cancelled.is_set():
        raise CallCancelled()
    if context.deadline is not None and context.deadline <= asyncio.get_running_loop().time():
        raise RunDeadlineExceeded()


def _query_key(text):
    return ' '.join(text.split()).casefold()


def isolated_state(state, query):
    """Copy state data while keeping root question/history/attachments untouched."""
    sub = deepcopy(state)
    for name, value in vars(sub).items():
        if isinstance(value, list) and (name.endswith('_results') or name.endswith('_trace') or
                name in ('rescue_evidence', 'bridge_identity_evidence', 'strategy_rounds')):
            setattr(sub, name, [])
    for name in ('bridge_normal_qualifications', 'strategy_coverage', 'strategy_rerank_scores'):
        if hasattr(sub, name): setattr(sub, name, {})
    if hasattr(sub, 'strategy_rerank_accepted_ids'): sub.strategy_rerank_accepted_ids = set()
    sub.query = sub.rewrite_query = query
    sub.enable_expansion = False
    sub._strategy_serial = True
    sub.needs_retrieval = True
    sub.bridge_rescue_mode = 'off'
    sub.rendered_contexts = sub.user_content = ''
    if hasattr(sub, '_finalized_context'): del sub._finalized_context
    return sub


def _fingerprint(row):
    version = (row.metadata or {}).get('source_version')
    chunk_version = (row.chunk_metadata or {}).get('source_version')
    derived = row.chunk_type in ('image_ocr', 'image_caption')
    if (not row.id or not row.knowledge_base_id or not row.knowledge_id or not version or
            chunk_version != version or not row.is_enabled or row.content_rewritten or
            row.content_revision or row.chunk_type not in ('text', 'parent_text', 'faq', 'image_ocr', 'image_caption') or
            not row.content or type(row.start_at) is not int or type(row.end_at) is not int or
            row.start_at < 0 or row.end_at < row.start_at or
            (not derived and row.end_at - row.start_at != len(row.content)) or
            (derived and row.metadata.get('source_selection') != 'MATERIALIZED_SELECTED_SOURCE')):
        raise RetrievalScopeError()
    fingerprint = (row.knowledge_base_id, row.knowledge_id, version, chunk_version,
            row.chunk_type, row.start_at, row.end_at, row.content)
    # Persisted image derivatives have asset locators, not original text spans.
    # Projection already reread them with current=True; keep their type and
    # provenance distinct, including when the same ID appears across rounds.
    if derived:
        fingerprint += (row.parent_chunk_id, row.image_info, deepcopy(row.chunk_metadata))
    return fingerprint


def _coverage(plan):
    return {sub.id: {'requirement': list(sub.covers), 'candidate_ids': [], 'selected_ids': [],
        'delivered_ids': [], 'source_validity': 'NONE', 'semantic_support': 'NOT_CHECKED',
        'delivery': 'NOT_STARTED', 'reason': ''} for sub in plan.subqueries}


async def execute_plan(state, context, plan, retrieve_once):
    """Run at most three total units serially; unsafe exceptions propagate."""
    coverage, rounds, trace, queues = _coverage(plan), [], [], []
    units = getattr(state, 'strategy_units_used', len(getattr(state, 'strategy_rounds', ())))
    # Existing callers created before this field may only retain round records.
    units = max(units, len(getattr(state, 'strategy_rounds', ())))
    seen_queries = set(getattr(state, 'strategy_queries_seen', ()))
    ids = [sub.id for sub in plan.subqueries]
    if len(ids) != len(set(ids)) or any(sub.depends_on for sub in plan.subqueries):
        raise ValueError('STRATEGY_PLAN_INVALID')
    fingerprints = {}
    for subquery in plan.subqueries:
        _check_context(context)
        entry = coverage[subquery.id]
        if units >= MAX_RETRIEVAL_UNITS:
            entry.update(delivery='BUDGET_OMITTED', reason='RETRIEVAL_UNIT_LIMIT')
            continue
        key = _query_key(subquery.query_text)
        if not key or key in seen_queries:
            entry.update(delivery='NOT_STARTED', reason='DUPLICATE_QUERY')
            continue
        sub = isolated_state(state, subquery.query_text)
        units += 1
        seen_queries.add(key)
        child = context.child('strategy/' + subquery.id)
        try:
            result = await retrieve_once(sub, child)
        except SearchNothing:
            result = RoundResult(sub, 'EMPTY', 'SEARCH_NOTHING')
        except ProviderError as error:
            if error.code == 'LOCAL_EXECUTION_LIMIT_DENIED' and not error.sent:
                entry.update(delivery='BUDGET_OMITTED', reason=error.code)
                trace.append({'stage':'strategy_round', 'subquery_id':subquery.id,
                    'status':'BUDGET_OMITTED', 'reason':error.code})
                break
            raise
        if not isinstance(result, RoundResult) or result.status not in ('OK', 'EMPTY', 'ERROR', 'BUDGET_OMITTED'):
            raise ValueError('STRATEGY_ROUND_CONTRACT_INVALID')
        rounds.append(result)
        trace.append({'stage':'strategy_round', 'subquery_id':subquery.id,
            'status':result.status, 'reason':result.reason, 'request_id':child.request_id})
        if result.status == 'ERROR':
            entry.update(delivery='ERROR', reason=result.reason or 'RETRIEVAL_ERROR')
            break
        if result.status == 'BUDGET_OMITTED':
            entry.update(delivery='BUDGET_OMITTED', reason=result.reason or 'SOURCE_CONTEXT_BUDGET')
            continue
        rows = [row for row in result.merge_results if row is not None] if result.status == 'OK' else []
        validate_results(rows, state.targets, context)
        for row in rows:
            fingerprint = _fingerprint(row)
            if row.id in fingerprints and fingerprints[row.id] != fingerprint:
                raise RetrievalScopeError()
            fingerprints[row.id] = fingerprint
        entry.update(candidate_ids=[row.id for row in rows],
            source_validity='VALIDATED' if rows else 'NONE',
            delivery='PENDING' if rows else 'NO_EVIDENCE', reason=result.reason)
        queues.append((subquery.id, list(rows)))
    # Do not compare independent Rerank scores or apply a second q0 threshold.
    selected, by_id = [], {}
    top_k = max(0, state.rerank_top_k)
    while any(queue for _, queue in queues):
        for sub_id, queue in queues:
            if not queue: continue
            row = queue.pop(0)
            if row.id in by_id:
                target = by_id[row.id]
                labels = target.metadata['strategy_subquery_ids']
                if sub_id not in labels: labels.append(sub_id)
                continue
            if top_k > 0 and len(selected) >= top_k:
                continue
            copied = deepcopy(row)
            copied.metadata['strategy_subquery_ids'] = [sub_id]
            by_id[row.id] = copied
            selected.append(copied)
    for sub_id, entry in coverage.items():
        chosen = [row.id for row in selected if sub_id in row.metadata['strategy_subquery_ids']]
        entry['selected_ids'] = chosen
        if entry['candidate_ids'] and not chosen:
            entry.update(delivery='BUDGET_OMITTED', reason='FINAL_SELECTION_BUDGET')
    return StrategyResult(plan, tuple(rounds), tuple(selected), coverage, tuple(trace),
        units, frozenset(seen_queries))


def apply_strategy_result(state, result):
    """Project only retrieval results; root q0/rewrite/P5 inputs remain intact."""
    state.strategy_plan = result.plan
    state.strategy_rounds.extend(result.rounds)
    state.strategy_trace.extend(result.trace)
    state.strategy_coverage.update(deepcopy(result.coverage))
    state.strategy_units_used = result.units_used
    state.strategy_queries_seen = set(result.queries_seen)
    state.merge_results = list(result.selected)
    state.search_results = [deepcopy(row) for unit in result.rounds for row in unit.search_results if row is not None]
    state.rerank_results = [deepcopy(row) for unit in result.rounds for row in unit.rerank_results if row is not None]
    for unit in result.rounds:
        for name in ('evidence_trace', 'graph_trace'):
            getattr(state, name).extend(deepcopy(getattr(unit.state, name, ())))
        if hasattr(state, 'strategy_rerank_scores'):
            state.strategy_rerank_scores.update(getattr(unit.state, 'strategy_rerank_scores', {}))
        if hasattr(state, 'strategy_rerank_accepted_ids'):
            state.strategy_rerank_accepted_ids.update(getattr(unit.state, 'strategy_rerank_accepted_ids', ()))


def _selection_coverage(state):
    for sub_id, entry in state.strategy_coverage.items():
        entry['selected_ids'] = [row.id for row in state.merge_results
            if sub_id in row.metadata.get('strategy_subquery_ids', ())]
        entry['delivered_ids'] = []
        if entry['candidate_ids']:
            entry['delivery'] = 'PENDING' if entry['selected_ids'] else 'BUDGET_OMITTED'
            if not entry['selected_ids']: entry['reason'] = 'FINAL_CONTEXT_BUDGET'


async def fit_context_budget(state, context, *, prepare_preview=None):
    """Check both actual encoders; trim tail only and record omitted branches.

    prepare_preview is an offline encoding callback, never a generation call.
    Only actual GenerationCoordinator preparation may record DELIVERED later.
    """
    from .context import into_chat_message
    if prepare_preview is None:
        from ..generation.context import Evidence, prepare_generation
        from ..generation.types import GenerationRequest
        from .types import context_results

        def prepare_preview(preview, finalized):
            # Encoding only. The real coordinator owns the delivery observer.
            return prepare_generation(GenerationRequest('strategy-preview', 'strategy-preview',
                'strategy-preview', q0=preview.query, finalized=finalized,
                evidence=tuple(Evidence.from_result(row) for row in context_results(preview)),
                language=preview.language, citations_enabled=True))
    while True:
        _check_context(context)
        preview = deepcopy(state)
        try:
            finalized = await into_chat_message(preview, context, record_bridge=False)
            if prepare_preview is not None:
                prepared = prepare_preview(preview, finalized)
                if hasattr(prepared, '__await__'): await prepared
            break
        except ContextBudgetError:
            if not state.merge_results: raise
            removed = state.merge_results.pop()
            state.strategy_trace.append({'stage':'strategy_context', 'decision':'BUDGET_OMITTED',
                'source_id':removed.id, 'reason':'FINAL_CONTEXT_BUDGET'})
    _selection_coverage(state)


def record_delivered_coverage(state, prepared):
    """Confirm original IDs AND exact raw bodies in actual current messages.

    Source validity and model-independent semantic support remain separate.
    A citable parent ID, search hit or historical answer alone proves nothing.
    """
    from ..generation.context import compact_known_context, escape_text
    snapshot = prepared.registry.snapshot()
    references = {ref[0]: (handle, ref) for handle, ref in snapshot.chunks if handle in snapshot.citable}
    # Context may occur in the system template or the final current user turn;
    # exclude all prior user/assistant dialogue messages from delivery proof.
    current = '\n'.join(message.content for i, message in enumerate(prepared.messages)
        if i == 0 or i == len(prepared.messages) - 1)
    delivered = []
    from .types import context_results
    for row in context_results(state):
        matched = references.get(row.id)
        if not matched or matched[1][1:3] != (row.knowledge_id, row.knowledge_base_id): continue
        registry = prepared.registry.clone_for_attempt()
        exact = registry.encode_resource_text(compact_known_context(escape_text(row.content), registry))
        if '<content>' + exact + '</content>' in current:
            delivered.append(row)
    for sub_id, entry in state.strategy_coverage.items():
        entry['delivered_ids'] = [row.id for row in delivered
            if sub_id in row.metadata.get('strategy_subquery_ids', ())]
        if entry['candidate_ids']:
            entry['delivery'] = 'DELIVERED' if entry['delivered_ids'] else 'BUDGET_OMITTED'
            if not entry['delivered_ids']: entry['reason'] = 'FINAL_CONTEXT_NOT_DELIVERED'
    assessment = getattr(state, '_strategy_bridge_assessment', None)
    if assessment is not None:
        from dataclasses import replace
        from .bridge_strategy import BridgeStatus
        required = set(assessment.required_source_ids)
        actual = {row.id for row in delivered}
        complete_delivery = bool(required and required <= actual and assessment.context_admitted)
        supported = bool(complete_delivery and assessment.status == BridgeStatus.COMPLETE
                         and assessment.source_chain_supported)
        state._strategy_bridge_assessment = replace(assessment,
            delivered=complete_delivery, final_answer_supported=supported)
        state.strategy_trace.append({'stage':'strategy_bridge',
            'decision':'ACTUAL_MESSAGES_DELIVERED' if complete_delivery else 'FINAL_CONTEXT_NOT_DELIVERED',
            'required_source_ids':sorted(required),'delivered_ids':sorted(required & actual),
            'source_validity':assessment.source_validity,'semantic_support':assessment.semantic_support,
            'final_answer_supported':supported,'prompt_sha256':prepared.prompt_sha256})
    state.strategy_trace.append({'stage':'strategy_context', 'decision':'ACTUAL_MESSAGES_PREPARED',
        'delivered_ids':[row.id for row in delivered], 'semantic_support':'NOT_CHECKED',
        'prompt_sha256':prepared.prompt_sha256})
