"""Scoped parallel recall; semantic port of chat_pipeline/search.go."""
from __future__ import annotations
from .query import go_strip, go_fields
import asyncio
from dataclasses import dataclass, replace
from typing import Protocol, Sequence, Mapping
from ..models.types import CallContext
from ..models.cloud_errors import raise_if_send_denied
from .types import SearchResult

@dataclass(frozen=True)
class SearchTarget:
    knowledge_base_id: str
    target_type: str = 'knowledge_base'
    knowledge_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()
    scope_tag_ids: tuple[str, ...] = ()
    disable_recall_thresholds: bool = False
    embedding_model_key: str = ''
    keyword_index_enabled: bool = True
    vector_index_enabled: bool = True

@dataclass(frozen=True)
class SearchParams:
    query_text: str
    match_count: int = 30
    vector_threshold: float = 0.2
    keyword_threshold: float = 0.3
    disable_vector: bool = False
    disable_keywords: bool = False
    skip_context_enrichment: bool = False

class RetrievalRepository(Protocol):

    async def hybrid_search(self, targets: Sequence[SearchTarget], params: SearchParams, context: CallContext, query_embedding: Sequence[float] | None=None) -> Sequence[SearchResult]:
        ...

    async def get_chunks(self, ids: Sequence[str], context: CallContext) -> Mapping[str, SearchResult]:
        ...

    async def get_current_chunks(self, ids: Sequence[str], context: CallContext) -> Mapping[str, SearchResult]:
        ...

    async def get_chunk_children(self, parent_ids: Sequence[str], context: CallContext) -> Sequence[SearchResult]:
        ...

    async def get_faq_answers(self, ids: Sequence[str], context: CallContext) -> Mapping[str, object]:
        ...

    async def get_source_unit(self, anchor: SearchResult, context: CallContext) -> Sequence[SearchResult]:
        ...

class SearchError(Exception):
    pass

class SearchNothing(SearchError):

    def __init__(self):
        super().__init__('SEARCH_NOTHING')

class RetrievalScopeError(SearchError):

    def __init__(self):
        super().__init__('RETRIEVAL_SCOPE_REJECTED')

def validate_targets(targets, context):
    allowed = set(context.scope.knowledge_base_ids)
    documents = set(context.scope.document_ids)
    if not context.scope.owner_id or not allowed:
        raise RetrievalScopeError()
    for t in targets:
        if t.knowledge_base_id not in allowed:
            raise RetrievalScopeError()
        if documents and (t.target_type != 'knowledge' or not t.knowledge_ids or (not set(t.knowledge_ids) <= documents)):
            raise RetrievalScopeError()

def validate_results(results, targets, context):
    by_kb = {}
    for t in targets:
        by_kb.setdefault(t.knowledge_base_id, []).append(t)
    for r in results:
        if r.knowledge_base_id not in by_kb:
            raise RetrievalScopeError()
        if not any((t.target_type != 'knowledge' or r.knowledge_id in t.knowledge_ids for t in by_kb[r.knowledge_base_id])):
            raise RetrievalScopeError()
        if context.scope.document_ids and r.knowledge_id not in context.scope.document_ids:
            raise RetrievalScopeError()
    return list(results)

def content_signature(text):
    from .query import go_lower
    return ' '.join(go_fields(go_lower(text)))

def source_domain(result):
    """Keep both persisted version breadcrumbs; disagreement is never equivalent."""
    version = result.metadata.get('source_version')
    chunk_version = result.chunk_metadata.get('source_version')
    verified = bool(result.knowledge_base_id and result.knowledge_id and
                    version and version == chunk_version)
    return (result.knowledge_base_id, result.knowledge_id, version, chunk_version,
            None if verified else result.id)


def deduplicate(results, *, preserve_sources=False):
    ids, texts, out = (set(), set(), [])
    domains = {}
    for r in results:
        sig = r.content if preserve_sources else content_signature(r.content)
        if preserve_sources:
            domain = source_domain(r)
            if r.id in domains and domains[r.id] != domain:
                raise RetrievalScopeError()
            domains[r.id] = domain
        identity = r.id
        text_key = (domain, r.chunk_type, sig) if preserve_sources else sig
        if identity in ids or (sig and text_key in texts):
            continue
        ids.add(identity)
        if sig:
            texts.add(text_key)
        out.append(r)
    return out

def partial_overlap(results, segmenter=None, threshold=0.85, *, preserve_sources=False):
    if preserve_sources:
        # A bag of tokens cannot prove two source claims equivalent. Even a
        # substring may add a date, amount or negation. Only exact raw
        # bodies within the same immutable source/origin domain may collapse.
        return deduplicate(results, preserve_sources=True)
    from .query import simple_tokens
    removed = set()
    normalized = [content_signature(r.content) for r in results]
    tokens = [simple_tokens(r.content, segmenter) for r in results]
    for i in range(len(results)):
        if i in removed:
            continue
        for j in range(i + 1, len(results)):
            if j in removed:
                continue
            a, b = (i, j) if len(normalized[i].encode()) <= len(normalized[j].encode()) else (j, i)
            if not normalized[a]:
                continue
            denominator = min(len(tokens[a]), len(tokens[b]))
            overlap = normalized[a] in normalized[b] or (denominator and len(tokens[a] & tokens[b]) / denominator >= threshold)
            if overlap:
                victim = b if results[a].score > results[b].score else a
                removed.add(victim)
    return [r for i, r in enumerate(results) if i not in removed]

async def search(state, context, repository, *, embedding_resolver=None, web_search=None, entity_search=None, segmenter=None):
    from .query import expand_queries
    from ..models.types import CallCancelled, RunDeadlineExceeded
    if not state.needs_retrieval:
        return
    targets = tuple((t for t in state.targets if t.knowledge_base_id and (not (t.target_type == 'knowledge' and (not t.knowledge_ids)))))
    if targets:
        validate_targets(targets, context)
    if not targets and (not state.web_search_enabled):
        return
    errors, results = ([], [])

    async def recall(batch, params, embedding=None):
        try:
            found = await repository.hybrid_search(batch, params, context, embedding)
            return validate_results(found, batch, context)
        except (RetrievalScopeError, PermissionError, CallCancelled, RunDeadlineExceeded):
            raise
        except Exception as exc:
            raise_if_send_denied(exc)
            errors.append(exc)
            return []

    async def group(key, batch):
        embedding, vector_disabled = (None, False)
        if key and embedding_resolver:
            try:
                embedding = await embedding_resolver(key, state.rewrite_query, context)
            except (CallCancelled, RunDeadlineExceeded):
                raise
            except Exception as exc:
                raise_if_send_denied(exc)
                errors.append(exc)
                vector_disabled = True
                batch = [t for t in batch if t.keyword_index_enabled]
        buckets = {}
        for t in batch:
            full = t.target_type == 'knowledge_base' and (not t.tag_ids) and (not t.scope_tag_ids)
            k = ('full',) if full else (t,)
            buckets.setdefault(k, []).append(t)
        calls = []
        for bucket in buckets.values():
            disabled = bucket[0].disable_recall_thresholds if not (bucket[0].target_type == 'knowledge_base' and (not bucket[0].tag_ids) and (not bucket[0].scope_tag_ids)) else False
            params = SearchParams(state.rewrite_query, state.embedding_top_k, 0 if disabled else state.vector_threshold, 0 if disabled else state.keyword_threshold, vector_disabled, False, True)
            calls.append(recall(bucket, params, embedding))
        return [r for group_result in await asyncio.gather(*calls) for r in group_result]
    grouped = {}
    for t in targets:
        grouped.setdefault(t.embedding_model_key, []).append(t)
    calls = [group(key, batch) for key, batch in grouped.items()]
    if state.web_search_enabled:
        if web_search is None:
            raise SearchError('WEB_SEARCH_UNIMPLEMENTED')

        async def web():
            try:
                return list(await web_search(state, context))
            except (CallCancelled, RunDeadlineExceeded):
                raise
            except Exception:
                return []
        calls.append(web())
    entity_task = None
    if state.entities:
        if entity_search is None:
            raise SearchError('ENTITY_SEARCH_UNIMPLEMENTED')

        async def entities():
            from copy import deepcopy
            try:
                return validate_results(await entity_search(deepcopy(state), context), targets, context)
            except (RetrievalScopeError, PermissionError, CallCancelled, RunDeadlineExceeded):
                raise
            except Exception:
                return []
        entity_task = asyncio.create_task(entities())
    try:
        for batch in await asyncio.gather(*calls):
            results.extend(batch)
        chunk_failed = bool(errors and (not results))
        if not chunk_failed and state.enable_expansion and (len(results) < max(1, state.embedding_top_k)):
            variants = expand_queries(state.rewrite_query, state.query, segmenter)
            semaphore = asyncio.Semaphore(max(1, min(16, len(variants) * len(targets))))

            async def expand(q, t):
                async with semaphore:
                    disabled = t.disable_recall_thresholds
                    params = SearchParams(q, max(state.embedding_top_k * 2, state.rerank_top_k * 2), 0 if disabled else state.vector_threshold, 0 if disabled else state.keyword_threshold * 0.8, False, False, True)
                    return await recall((t,), params)
            for batch in await asyncio.gather(*(expand(q, t) for q in variants for t in targets)):
                results.extend(batch)
        if entity_task:
            try:
                results.extend(await entity_task)
            except BaseException:
                entity_task.cancel()
                raise
        state.search_results = deduplicate(results, preserve_sources=
            getattr(state, 'source_evidence_mode', 'off') == 'bounded_source_v1')
        if not state.search_results:
            if chunk_failed:
                raise SearchError('SEARCH_FAILED') from None
            raise SearchNothing()
    finally:
        if entity_task and (not entity_task.done()):
            entity_task.cancel()
            await asyncio.gather(entity_task, return_exceptions=True)
