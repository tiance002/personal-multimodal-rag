"""Optional current document identity quotes after ordinary evidence selection."""
from copy import deepcopy
from dataclasses import replace
import hashlib

from ..models.transport import call_lifetime
from ..models.types import CallCancelled, RunDeadlineExceeded
from .search import RetrievalScopeError, validate_results, validate_targets
from .source_evidence import MODE, _domain, _validate_source
from .types import ContextBudgetError, context_results


async def _fits_context(state, context):
    """Preview both existing encoders without changing selection or generating."""
    from .context import into_chat_message
    from ..generation.context import Evidence, prepare_generation
    from ..generation.types import GenerationRequest
    preview = state
    finalized = await into_chat_message(preview, context, record_bridge=False)
    prepare_generation(GenerationRequest('source-context-preview', 'source-context-preview',
        'source-context-preview', preview.query, finalized,
        evidence=tuple(Evidence.from_result(row) for row in context_results(preview)),
        language=preview.language, citations_enabled=True))


async def project_document_context(state, context, repository):
    """Append one exact <=400-character original head per selected document.

    The opt-in scoring-context experiment also exposes its document identity
    data to the answer. Each quote has its own original ID and locator; it is
    never attached to another chunk's citation or treated as support for that
    chunk's facts. Existing selected sources and scores remain unchanged.
    """
    if (not state.needs_retrieval or not getattr(state, 'rerank_context_enabled', False)
            or state.source_evidence_mode != MODE):
        return
    selected = [row for row in state.merge_results if row is not None]
    validate_targets(state.targets, context)
    validate_results(selected, state.targets, context)
    anchors = {}
    for row in selected:
        if row.chunk_type not in ('text', 'parent_text'):
            continue
        _validate_source(row, _domain(row), ('text', 'parent_text'))
        if row.knowledge_id in anchors and _domain(anchors[row.knowledge_id]) != _domain(row):
            raise RetrievalScopeError()
        anchors.setdefault(row.knowledge_id, row)
    documents = tuple(anchors)
    if not documents:
        return

    def trace(decision, reason, **details):
        state.evidence_trace.append({'stage':'source_document_context',
            'decision':decision, 'reason':reason, **details})

    reader = getattr(repository, 'get_first_source_chunks', None)
    if len(documents) > 30 or not callable(reader):
        trace('OMITTED', 'SOURCE_DOCUMENT_LIMIT' if len(documents)>30 else 'SOURCE_READER_UNAVAILABLE')
        return
    try:
        async with call_lifetime(replace(context, deadline_kind='run'), 10):
            heads = await reader(documents, context)
    except (PermissionError, RetrievalScopeError, CallCancelled, RunDeadlineExceeded):
        raise
    except Exception:
        trace('OMITTED', 'SOURCE_READBACK_UNAVAILABLE')
        return
    if (not isinstance(heads, dict) or not set(heads)<=set(documents)
            or any(doc != row.knowledge_id for doc,row in heads.items())
            or len({row.id for row in heads.values()}) != len(heads)):
        raise RetrievalScopeError()
    validate_results(list(heads.values()), state.targets, context)
    selected_by_id = {row.id:row for row in context_results(state)}
    for document in documents:
        head = heads.get(document)
        if head is None:
            trace('OMITTED', 'CURRENT_HEAD_UNAVAILABLE', document_id=document)
            continue
        anchor = anchors[document]
        try:
            _validate_source(head, _domain(anchor))
            if head.chunk_index != 0 or not head.content:
                raise ValueError('SOURCE_HEAD_INVALID')
        except (RetrievalScopeError, PermissionError, ValueError):
            trace('OMITTED', 'HEAD_IDENTITY_UNVERIFIABLE', document_id=document)
            continue
        if head.id in selected_by_id:
            existing = selected_by_id[head.id]
            if (_domain(existing) != _domain(head) or existing.start_at != head.start_at
                    or existing.end_at > head.end_at
                    or existing.content != head.content[:len(existing.content)]):
                raise RetrievalScopeError()
            trace('ALREADY_SELECTED', 'EXACT_CURRENT_HEAD_PRESENT', source_id=head.id,
                document_id=document)
            continue
        quote = deepcopy(head)
        quote.content = head.content[:400]
        quote.end_at = quote.start_at + len(quote.content)
        proof = {'source_chunk_id':quote.id, 'document_id':document,
            'source_version':_domain(quote)[2], 'start':quote.start_at, 'end':quote.end_at,
            'quote_sha256':hashlib.sha256(quote.content.encode('utf-8')).hexdigest()}
        quote.metadata['source_document_context'] = proof
        # Preserve the selected document's display fields without inventing a title.
        for field in ('knowledge_title', 'knowledge_filename', 'knowledge_source'):
            setattr(quote, field, getattr(anchor, field))
        preview = deepcopy(state)
        preview.merge_results.append(quote)
        try:
            await _fits_context(preview, context)
        except ContextBudgetError:
            trace('OMITTED', 'FINAL_CONTEXT_BUDGET', **proof)
            continue
        state.merge_results.append(quote)
        selected_by_id[quote.id] = quote
        trace('ADMITTED', 'EXACT_CURRENT_DOCUMENT_IDENTITY', **proof)
