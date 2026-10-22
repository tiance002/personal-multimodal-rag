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


def _domain(row):
    return (row.knowledge_base_id, row.knowledge_id, row.metadata.get('source_version'))


def _validate_source(row, domain, kinds=('text',)):
    if (not domain[2] or _domain(row) != domain or not row.id or
            row.chunk_metadata.get('source_version') != domain[2] or not row.is_enabled or
            row.chunk_type not in kinds or row.content_revision or row.content_rewritten):
        raise RetrievalScopeError()
    if (type(row.start_at) is not int or type(row.end_at) is not int or
            row.start_at < 0 or row.end_at - row.start_at != len(row.content)):
        raise PermissionError('SOURCE_UNIT_LOCATOR_MISMATCH')


def _selected_ids(hit):
    return tuple(dict.fromkeys(i for i in (hit.id, *hit.sub_chunk_ids,
        hit.parent_chunk_id if hit.content_rewritten else '') if i))


async def _materialize_selected(hit, source, state, context, repository, *, cache=None):
    """Turn an admitted composite into bounded, individually readable quotes."""
    ids = _selected_ids(hit)
    if len(ids) > UNIT_CHUNKS:
        raise SourceEvidenceBudgetError('SOURCE_UNIT_BUDGET_EXCEEDED')
    extra = tuple(i for i in ids if i != source.id)
    loaded = ({i: cache[i] for i in extra if i in cache} if cache is not None else
              dict(await repository.get_chunks(extra, context, current=True)) if extra else {})
    if set(loaded) != set(extra) or any(key != row.id for key, row in loaded.items()):
        raise ValueError('SOURCE_BODY_NOT_PRESERVED')
    loaded[source.id] = source
    rows = [loaded[i] for i in ids]
    validate_results(rows, state.targets, context)
    for row in rows:
        _validate_source(row, _domain(source), ('text', 'parent_text'))
    if sum(len(row.content) for row in rows) > UNIT_CHARS:
        raise SourceEvidenceBudgetError('SOURCE_UNIT_BUDGET_EXCEEDED')
    restored = []
    for row in rows:
        raw = deepcopy(row)
        if row.id == hit.id:
            raw.score = hit.score  # Same admitted ID, not a new model score.
            for key in ('base_score', 'model_score'):
                if key in hit.metadata:
                    raw.metadata[key] = hit.metadata[key]
        raw.metadata.update(source_selection='MATERIALIZED_SELECTED_SOURCE',
                            source_anchor_id=hit.id, source_anchor_score=hit.score)
        restored.append(raw)
    state.evidence_trace.append({'stage':'source_projection', 'anchor':hit.id,
        'decision':'REMATERIALIZED', 'reason':'EXACT_PERSISTED_QUOTES',
        'source_ids':[r.id for r in restored]})
    return restored


async def _project_selected(selected, state, context, repository, cache):
    """Mandatory identity check, independent of optional neighborhood coverage.

    Only admitted TopK entries and their bounded ancestry are read. Previously
    verified source-unit rows are reused within this invocation, never globally.
    """
    candidates = [r for r in selected if r.chunk_type in ('text', 'parent_text')]
    ids = tuple(dict.fromkeys(i for r in candidates if len(_selected_ids(r)) <= UNIT_CHUNKS
                             for i in _selected_ids(r) if i not in cache))
    if ids:
        loaded = dict(await repository.get_chunks(ids, context, current=True))
        if set(loaded) != set(ids) or any(key != row.id for key, row in loaded.items()):
            raise ValueError('SOURCE_BODY_NOT_PRESERVED')
        validate_results(list(loaded.values()), state.targets, context)
        cache.update(loaded)
    restored, seen = [], set()
    for hit in selected:
        if hit.chunk_type not in ('text', 'parent_text'):
            restored.append(hit)
            continue
        try:
            if len(_selected_ids(hit)) > UNIT_CHUNKS:
                raise SourceEvidenceBudgetError('SOURCE_UNIT_BUDGET_EXCEEDED')
            source = cache[hit.id]
            _validate_source(source, _domain(hit), ('text', 'parent_text'))
            if (hit.chunk_metadata.get('source_version') != _domain(source)[2] or not hit.is_enabled):
                raise RetrievalScopeError()
            if source.content not in hit.content:
                raise ValueError('SOURCE_BODY_NOT_PRESERVED')
            exact = (hit.chunk_type == source.chunk_type and hit.chunk_index == source.chunk_index and
                     hit.content == source.content and hit.start_at == source.start_at and
                     hit.end_at == source.end_at and not hit.content_rewritten and not hit.content_revision)
            # Ancestry alone is not proof that those independent quotes were delivered.
            rows = ([hit] if exact and not hit.sub_chunk_ids else
                    await _materialize_selected(hit, source, state, context, repository, cache=cache))
        except SourceEvidenceBudgetError as error:
            state.evidence_trace.append({'stage':'source_projection', 'anchor':hit.id,
                'decision':'INSUFFICIENT_SOURCE', 'reason':str(error),
                'omitted_ids':list(_selected_ids(hit)), 'source_ids':[]})
            continue
        for row in rows:
            if row.id not in seen:
                restored.append(row)
                seen.add(row.id)
    return restored


async def expand_source_evidence(state, context, repository):
    if not state.needs_retrieval or state.source_evidence_mode == 'off':
        return
    if state.source_evidence_mode != MODE:
        raise ValueError('SOURCE_EVIDENCE_MODE_INVALID')
    # Retain every admitted original. Summaries remain locator candidates in
    # Search/Rerank/Merge, but are projected out of this source-only handoff.
    selected = list(state.merge_results)
    seen = {r.id for r in selected}
    # A sub-chunk ID records ancestry, not proof that its entire quote survived
    # parent replacement, overlap handling or historical slicing.
    domains, added, source_cache = {}, 0, {}
    for anchor in selected:
        if anchor.chunk_type not in ('text', 'summary'):
            continue
        version = anchor.metadata.get('source_version')
        domain = _domain(anchor)
        if not version or domain not in domains and len(domains) >= MAX_GROUPS:
            continue
        domains.setdefault(domain, []).append(anchor)
    anchors = [anchor for group in domains.values() for anchor in
               sorted(group, key=lambda r: (r.chunk_type != 'summary', -r.chunk_index, r.id))[:3]]
    expanded_domains = set()
    for anchor in anchors:
        version = anchor.metadata['source_version']
        domain = _domain(anchor)
        if domain in expanded_domains:
            continue
        if anchor.chunk_type == 'summary' and any(
                row.chunk_type == 'text' and row.content and _domain(row) == domain and
                row.chunk_metadata.get('source_version') == version
                for row in selected):
            # The admitted original is already independently identified. Use
            # its bounded window instead of filling the rest of a Summary's
            # document, which adds noise without validating the paraphrase.
            state.evidence_trace.append({'stage': 'source_unit', 'anchor': anchor.id,
                'decision': 'UNCHANGED', 'reason': 'SUMMARY_SOURCE_ALREADY_SELECTED',
                'added_ids': [], 'added_chars': 0})
            continue
        rows = list(await repository.get_source_unit(anchor, context))
        validate_results(rows, state.targets, context)
        for row in rows:
            _validate_source(row, domain)
            source_cache[row.id] = row
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

    # Scope-local source projection, never a global Summary/index exclusion.
    # Even a correctly linked Summary is generated text: adding its source does
    # not verify every summary claim (e.g. mistranslation or reversed causality).
    # Keep the independently identified originals, not the conflicting paraphrase.
    originals = [r for r in selected if r.chunk_type not in ('summary', 'table_summary')]
    originals = await _project_selected(originals, state, context, repository, source_cache)
    for hint in selected:
        if hint.chunk_type not in ('summary', 'table_summary'):
            continue
        source_ids = [r.id for r in originals if r.chunk_type == 'text' and
            _domain(r) == _domain(hint) and r.metadata.get('source_version') and
            r.chunk_metadata.get('source_version') == r.metadata['source_version']]
        state.evidence_trace.append({'stage': 'source_projection', 'anchor': hint.id,
            'decision': 'SOURCE_AVAILABLE' if source_ids else 'INSUFFICIENT_SOURCE',
            'reason': 'SUMMARY_LOCATOR_ONLY', 'source_ids': source_ids})
    state.merge_results = originals
