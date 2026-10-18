"""Opt-in bounded source units; retrieval representations are not source facts.

This is context expansion, not a claim that every nearby sentence is relevant.
No model scores are changed, and no evaluation targets are available here.
"""
from copy import deepcopy
from .search import validate_results, RetrievalScopeError

MODE = 'bounded_source_v1'
UNIT_CHARS = 2048
UNIT_CHUNKS = 6
ADDED_CHARS = 4096
CONTEXT_CHARS = 16000
MAX_GROUPS = 3


class SourceEvidenceBudgetError(ValueError):
    pass


async def expand_source_evidence(state, context, repository):
    if not state.needs_retrieval or state.source_evidence_mode == 'off':
        return
    if state.source_evidence_mode != MODE:
        raise ValueError('SOURCE_EVIDENCE_MODE_INVALID')
    # Retain every already-selected result; expansion may never evict a hit.
    selected = list(state.merge_results)
    seen = {r.id for r in selected}
    # A sub-chunk ID records ancestry, not proof that its entire quote survived
    # parent replacement, overlap handling or historical slicing.
    domains, added = {}, 0
    for anchor in selected:
        if anchor.chunk_type not in ('text', 'summary'):
            continue
        version = anchor.metadata.get('source_version')
        domain = (anchor.knowledge_base_id, anchor.knowledge_id, version)
        if not version or domain not in domains and len(domains) >= MAX_GROUPS:
            continue
        domains.setdefault(domain, []).append(anchor)
    anchors = [anchor for group in domains.values() for anchor in
               sorted(group, key=lambda r: (r.chunk_type != 'summary', -r.chunk_index, r.id))[:3]]
    expanded_domains = set()
    for anchor in anchors:
        version = anchor.metadata['source_version']
        domain = (anchor.knowledge_base_id, anchor.knowledge_id, version)
        if domain in expanded_domains:
            continue
        rows = list(await repository.get_source_unit(anchor, context))
        validate_results(rows, state.targets, context)
        for row in rows:
            if ((row.knowledge_base_id, row.knowledge_id, row.metadata.get('source_version')) != domain or
                    row.chunk_metadata.get('source_version') != version or not row.is_enabled or
                    row.chunk_type != 'text' or row.content_revision or row.content_rewritten):
                raise RetrievalScopeError()
        for row in rows:
            if row.id in seen and not any(row.content in r.content for r in selected if r.id == row.id):
                raise ValueError('SOURCE_BODY_NOT_PRESERVED')
        new = [r for r in rows if r.id not in seen and r.content]
        reason = 'SUMMARY_SHORT_SOURCE' if anchor.chunk_type == 'summary' else 'PRECEDING_SOURCE_WINDOW'
        count = sum(len(r.content) for r in new)
        if len(rows) > UNIT_CHUNKS or sum(len(r.content) for r in rows) > UNIT_CHARS:
            raise SourceEvidenceBudgetError('SOURCE_UNIT_BUDGET_EXCEEDED')
        if added + count > ADDED_CHARS:
            state.evidence_trace.append({'stage': 'source_unit', 'anchor': anchor.id,
                'decision': 'BUDGET', 'reason': reason, 'omitted_ids': [r.id for r in new]})
            continue
        for row in new:
            r = deepcopy(row)
            r.metadata.update(source_selection=reason, source_anchor_id=anchor.id,
                              source_anchor_score=anchor.score)
            # Loaded row retains score=0; never copy the Summary's model score.
            selected.append(r)
            seen.add(r.id)
        added += count
        if new:
            expanded_domains.add(domain)
        state.evidence_trace.append({'stage': 'source_unit', 'anchor': anchor.id,
            'decision': 'EXPANDED' if new else 'UNCHANGED', 'reason': reason,
            'added_ids': [r.id for r in new], 'added_chars': count})
    state.merge_results = selected
