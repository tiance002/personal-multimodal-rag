"""Current original identity projection for already-selected sources.

No source windows, additional recall, source-unit limits or relevance claims.
The existing Merge/TopK stages alone select the reading context.
"""
from copy import deepcopy
from .search import validate_results, RetrievalScopeError

MODE = 'bounded_source_v1'  # Persisted compatibility value; provenance only.
PROJECTED_KINDS = ('text', 'parent_text', 'image_ocr', 'image_caption')


def _domain(row):
    return (row.knowledge_base_id, row.knowledge_id, row.metadata.get('source_version'))


def _validate_source(row, domain, kinds=('text',)):
    if (not domain[2] or _domain(row) != domain or not row.id or
            row.chunk_metadata.get('source_version') != domain[2] or not row.is_enabled or
            row.chunk_type not in kinds or row.content_revision or row.content_rewritten):
        raise RetrievalScopeError()
    if (type(row.start_at) is not int or type(row.end_at) is not int or
            row.start_at < 0 or row.end_at < row.start_at or
            row.chunk_type in ('text','parent_text') and row.end_at - row.start_at != len(row.content)):
        raise PermissionError('SOURCE_LOCATOR_MISMATCH')


def _selected_ids(hit):
    return tuple(dict.fromkeys(i for i in (hit.id, *hit.sub_chunk_ids,
        hit.parent_chunk_id if hit.content_rewritten else '') if i))


async def _materialize_selected(hit, source, state, context, repository, *, cache=None):
    """Project recorded Merge members as individually readable original quotes."""
    ids = _selected_ids(hit)
    loaded = dict(cache) if cache is not None else dict(
        await repository.get_chunks(ids, context, current=True))
    if source is not None:
        loaded[source.id] = source
    restored, omitted = [], []
    for id in ids:
        row = loaded.get(id)
        if row is None:
            omitted.append(id)
            continue
        _validate_source(row, _domain(hit), PROJECTED_KINDS)
        if not row.content or row.content not in hit.content:
            omitted.append(id)
            continue
        raw = deepcopy(row)
        if row.id == hit.id:
            raw.score = hit.score
            for key in ('base_score', 'model_score'):
                if key in hit.metadata:
                    raw.metadata[key] = hit.metadata[key]
        raw.metadata.update(source_selection='MATERIALIZED_SELECTED_SOURCE',
                            source_anchor_id=hit.id, source_anchor_score=hit.score)
        restored.append(raw)
    state.evidence_trace.append({'stage':'source_projection', 'anchor':hit.id,
        'decision':'REMATERIALIZED' if restored else 'INSUFFICIENT_SOURCE',
        'reason':'EXACT_PERSISTED_QUOTES' if restored else 'SOURCE_NOT_CURRENT_OR_UNVERIFIABLE',
        'source_ids':[r.id for r in restored], 'omitted_ids':omitted})
    return restored


async def project_source_evidence(state, context, repository):
    """One scoped batch for admitted IDs and their recorded Merge members only."""
    if not state.needs_retrieval or state.source_evidence_mode == 'off':
        return
    if state.source_evidence_mode != MODE:
        raise ValueError('SOURCE_EVIDENCE_MODE_INVALID')
    selected = [r for r in state.merge_results if r is not None]
    candidates = [r for r in selected if r.chunk_type in PROJECTED_KINDS]
    ids = tuple(dict.fromkeys(id for r in candidates for id in _selected_ids(r)))
    loaded = dict(await repository.get_chunks(ids, context, current=True)) if ids else {}
    if not set(loaded) <= set(ids) or any(key != row.id for key,row in loaded.items()):
        raise RetrievalScopeError()
    validate_results(list(loaded.values()), state.targets, context)
    output, seen = [], set()
    for hit in selected:
        if hit.chunk_type in ('summary','table_summary'):
            state.evidence_trace.append({'stage':'source_projection', 'anchor':hit.id,
                'decision':'INSUFFICIENT_SOURCE', 'reason':'SUMMARY_LOCATOR_ONLY', 'source_ids':[]})
            continue
        if hit.chunk_type in PROJECTED_KINDS:
            if not hit.is_enabled or hit.chunk_metadata.get('source_version') != _domain(hit)[2]:
                raise RetrievalScopeError()
            rows = await _materialize_selected(hit, loaded.get(hit.id), state, context, repository, cache=loaded)
        else:
            rows = [hit]
        for row in rows:
            if row.id not in seen:
                output.append(row); seen.add(row.id)
    state.merge_results = output
