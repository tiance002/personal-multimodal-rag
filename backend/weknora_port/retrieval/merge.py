"""Current-body merging, scoped parent resolution, FAQ and neighbor context."""
from __future__ import annotations
from .query import go_strip, go_fields
from copy import deepcopy
import json
import re
from .search import deduplicate, partial_overlap, source_domain, validate_results, RetrievalScopeError
from .query import simple_tokens, jaccard
from .rerank import image_infos
from ..models.types import CallCancelled, RunDeadlineExceeded
IMAGE = re.compile('!\\[([^\\]]*)\\]\\(([^)]+)\\)')

def contains(container, contained):
    return bool(container and contained and (container == contained or (len(contained) >= 12 and contained in container)))

def join_content(acc, next, separator='\n\n'):
    if not acc:
        return next
    if not next:
        return acc
    if contains(acc, next):
        return acc
    if contains(next, acc):
        return next
    for n in range(min(len(acc), len(next), 400), 11, -1):
        if acc[-n:] == next[:n]:
            return acc + next[n:]
    return acc + separator + next

def append_overlap(acc, next, overlap):
    if overlap <= 0:
        return acc + next
    if overlap <= min(len(acc), len(next)) and acc[-overlap:] == next[:overlap]:
        return acc + next[overlap:]
    for n in range(min(len(acc), len(next), max(overlap * 3, 400)), 11, -1):
        pos = next.find(acc[-n:])
        if 0 <= pos <= max(overlap * 2, 320):
            return acc + next[pos + n:]
    return acc + next

def trusted(r):
    return r.content_revision == 0 and (not r.content_rewritten) and (r.end_at > r.start_at) and (len(r.content) == r.end_at - r.start_at)

def merge_images(target, source):
    out = image_infos(target.image_info)
    seen = {i.get('url', '') for i in out}
    for i in image_infos(source.image_info):
        if i.get('url', '') not in seen:
            out.append(i)
            seen.add(i.get('url', ''))
    if out:
        target.image_info = json.dumps(out, ensure_ascii=False, separators=(',', ':'))

def sequential(results, *, preserve_sources=False):
    groups = {}
    for r in results:
        key = (source_domain(r), r.chunk_type) if preserve_sources else (r.knowledge_id, r.chunk_type)
        groups.setdefault(key, []).append(r)
    output = []
    for key, chunks in groups.items():
        chunks.sort(key=lambda r: (r.chunk_index, r.id))
        last = None
        index = -1
        for cur in chunks:
            mode = 'separate'
            if last:
                if trusted(last) and trusted(cur) and (cur.start_at >= last.start_at):
                    if cur.start_at <= last.end_at:
                        mode = 'extend' if cur.end_at > last.end_at else 'subsume' if contains(last.content, cur.content) else 'join'
                elif contains(last.content, cur.content) or contains(cur.content, last.content) or cur.chunk_index == index + 1:
                    mode = 'join'
            if mode == 'separate':
                last = deepcopy(cur)
                output.append(last)
                index = cur.chunk_index
                continue
            if mode == 'extend':
                last.content = append_overlap(last.content, cur.content, last.end_at - cur.start_at)
                last.end_at = cur.end_at
            elif mode == 'join':
                last.content = join_content(last.content, cur.content)
            if cur.id not in last.sub_chunk_ids:
                last.sub_chunk_ids.append(cur.id)
            merge_images(last, cur)
            last.score = max(last.score, cur.score)
            index = max(index, cur.chunk_index)
    return sorted(output, key=lambda r: r.score, reverse=True)

async def _chunks(repository, ids, state, context):
    ids = tuple(dict.fromkeys((i for i in ids if i)))
    if not ids:
        return {}
    try:
        chunks = await repository.get_chunks(ids, context)
        if not isinstance(chunks, dict):
            chunks = {r.id: r for r in chunks}
        if not set(chunks) <= set(ids):
            raise RetrievalScopeError()
        validate_results(chunks.values(), state.targets, context)
        return chunks
    except (RetrievalScopeError, PermissionError, CallCancelled, RunDeadlineExceeded):
        raise
    except Exception:
        return {}

def prune_images(content, raw):
    allowed = {v for img in image_infos(raw) for v in (img.get('url'), img.get('original_url')) if v}
    return go_strip(re.sub('\\n{3,}', '\n\n', IMAGE.sub(lambda m: m[0] if m[2] in allowed else '', content)))

class _ImageInfoObject(list):
    """JSON object pairs kept in order, including repeated/case-equivalent keys."""


def _reject_image_json_constant(value):
    raise ValueError('INVALID_IMAGE_JSON_CONSTANT')


def _parent_image_infos(raw):
    """Decode fixed Go ImageInfo fields for aggregation only (64-bit Go int).

    encoding/json applies exact/folded tags in input order, ignores null for
    primitives, and reports any typed-field error for the entire child array.
    Child passthrough and the shared rerank/context decoder remain unchanged.
    """
    try:
        values = json.loads(raw, object_pairs_hook=_ImageInfoObject,
                            parse_constant=_reject_image_json_constant)
        if type(values) is not list:
            return []
        output = []
        for value in values:
            if value is None:
                continue
            if not isinstance(value, _ImageInfoObject):
                return []
            info = dict(url='', original_url='', start_pos=0, end_pos=0,
                        caption='', ocr_text='')
            for key, item in value:
                # Go simple-fold equivalence for these ASCII tags includes long
                # s, but never expands ligatures as Unicode full casefold does.
                key = ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z'
                              else 's' if c == '\u017f' else c for c in key)
                if key not in info or item is None:
                    continue
                if key in ('start_pos', 'end_pos'):
                    if type(item) is not int or not -(1 << 63) <= item < (1 << 63):
                        return []
                elif not isinstance(item, str):
                    return []
                info[key] = item
            output.append(info)
        return output
    except (ValueError, TypeError):
        return []


async def collect_image_info(ids, state, context, repository, *, source_chunks=None):
    """Source two-level enabled-child aggregation. Order of URLs is unspecified upstream."""
    ids = tuple(dict.fromkeys((i for i in ids if i)))
    if not ids:
        return {}
    if not hasattr(repository, 'get_chunk_children'):
        raise RuntimeError('IMAGE_CHILD_COLLECTION_UNIMPLEMENTED')

    # New parent aggregation must stay anchored to the actual source document
    # and immutable version, even if several documents are allowed by the query.
    # Legacy callers without source rows retain the existing scoped collector.
    sources = dict(source_chunks or {})

    def domain(row):
        return (row.knowledge_base_id, row.knowledge_id,
                row.chunk_metadata.get('source_version'))

    async def children(parent_ids):
        try:
            rows = list(await repository.get_chunk_children(parent_ids, context))
            if any((r.parent_chunk_id not in parent_ids for r in rows)):
                raise RetrievalScopeError()
            rows = validate_results(rows, state.targets, context)
            if source_chunks is not None:
                for row in rows:
                    if (not getattr(row, 'is_enabled', True) or
                            row.chunk_type not in ('text', 'image_ocr', 'image_caption')):
                        continue
                    parent = sources.get(row.parent_chunk_id)
                    if parent is None or domain(row) != domain(parent):
                        raise RetrievalScopeError()
            return rows
        except (RetrievalScopeError, PermissionError, CallCancelled, RunDeadlineExceeded):
            raise
        except Exception:
            return []
    aggs = {}

    def add(target, child):
        for info in _parent_image_infos(child.image_info):
            key = info.get('url') or info.get('original_url')
            if not key:
                continue
            agg = aggs.setdefault(target, {})
            if key not in agg:
                agg[key] = {field: info[field] for field in
                            ('url', 'original_url', 'start_pos', 'end_pos', 'caption', 'ocr_text')}
            else:
                for field in ('ocr_text', 'caption'):
                    if info.get(field):
                        agg[key][field] = info[field]
    text_to_parent = {}
    for child in await children(ids):
        if not getattr(child, 'is_enabled', True):
            continue
        if child.chunk_type in ('image_ocr', 'image_caption'):
            add(child.parent_chunk_id, child)
        elif child.chunk_type == 'text':
            text_to_parent[child.id] = child.parent_chunk_id
            sources[child.id] = child
    if text_to_parent:
        for child in await children(tuple(text_to_parent)):
            if getattr(child, 'is_enabled', True) and child.chunk_type in ('image_ocr', 'image_caption'):
                add(text_to_parent[child.parent_chunk_id], child)
    return {id: json.dumps(list(agg.values()), ensure_ascii=False, separators=(',', ':')) for id, agg in aggs.items()}

async def resolve_parents(results, state, context, repository):
    parents = await _chunks(repository, [r.parent_chunk_id for r in results], state, context)
    if not parents:
        return results
    parents.update(await _chunks(repository, [r.parent_chunk_id for r in parents.values()], state, context))
    child_ids = [r.id if r.chunk_type == 'text' else r.parent_chunk_id for r in results if r.parent_chunk_id and (r.chunk_type in ('image_ocr', 'image_caption') or (r.chunk_type == 'text' and r.parent_chunk_id in parents and (parents[r.parent_chunk_id].chunk_type == 'parent_text')))]
    scoped = await collect_image_info(child_ids, state, context, repository,
        source_chunks={**parents, **{r.id: r for r in results}})
    for r in results:
        parent = parents.get(r.parent_chunk_id)
        if not parent or not parent.content:
            continue
        if r.chunk_type == 'text' and parent.chunk_type == 'parent_text':
            r.image_info = scoped.get(r.id, '')
            r.content = join_content(prune_images(parent.content, r.image_info), r.content)
        elif r.chunk_type in ('image_ocr', 'image_caption') and parent.chunk_type == 'text':
            recognized = r.content
            hit = r.image_info
            source = parents.get(parent.parent_chunk_id)
            if source is None or source.chunk_type != 'parent_text' or (not source.content):
                source = parent
            r.chunk_index = parent.chunk_index
            r.image_info = scoped.get(parent.id, '')
            if not r.image_info and hit:
                urls = {m[2] for m in IMAGE.finditer(parent.content)}
                from .context import HTML_IMAGE
                urls.update((go_strip(m[2]) for m in HTML_IMAGE.finditer(parent.content)))
                infos = [i for i in image_infos(hit) if i.get('url') in urls or i.get('original_url') in urls]
                r.image_info = json.dumps(infos, ensure_ascii=False) if infos else ''
            r.content = join_content(prune_images(source.content, r.image_info), prune_images(parent.content, r.image_info))
            r.content = join_content(r.content, recognized)
            infos = image_infos(r.image_info)
            field = 'ocr_text' if r.chunk_type == 'image_ocr' else 'caption'
            for i in infos:
                if str(i.get(field, '')) == recognized:
                    i[field] = ''
            if infos:
                r.image_info = json.dumps(infos, ensure_ascii=False)
        else:
            continue
        r.content_rewritten = True
        if r.id not in r.sub_chunk_ids:
            r.sub_chunk_ids.append(r.id)
    return results

def inject_history(results, state, segmenter=None):
    seen = {r.id for r in results}
    tokens = simple_tokens(state.rewrite_query or state.query, segmenter)
    count = 0
    for h in reversed(state.history):
        refs = getattr(h, 'knowledge_references', ())
        if not refs:
            continue
        for r in refs:
            if isinstance(r, dict):
                from .types import SearchResult
                from dataclasses import fields
                values = {k: v for k, v in r.items() if k in {f.name for f in fields(SearchResult)}}
                values['sub_chunk_ids'] = r.get('sub_chunk_id', values.get('sub_chunk_ids', []))
                if not all((k in values for k in ('id', 'content', 'score'))):
                    continue
                r = SearchResult(**values)
            if not hasattr(r, 'id') or r.id in seen:
                continue
            similarity = jaccard(tokens, simple_tokens(r.content, segmenter))
            if similarity < 0.15:
                continue
            r = deepcopy(r)
            r.score *= 0.6
            r.match_type = 'history'
            r.metadata = {} if not isinstance(r.metadata, dict) else r.metadata
            r.metadata['history_similarity'] = f'{similarity:.4f}'.rstrip('0').rstrip('.')
            results.append(r)
            seen.add(r.id)
            count += 1
            if count == 3:
                break
        break
    return results

async def populate_faq(results, state, context, repository):
    ids = [r.id for r in results if r.chunk_type == 'faq']
    if not ids:
        return
    available = await _chunks(repository, ids, state, context)
    if not available:
        return
    try:
        faqs = await repository.get_faq_answers(tuple(available), context)
    except (RetrievalScopeError, PermissionError, CallCancelled, RunDeadlineExceeded):
        raise
    except Exception:
        return
    if not set(faqs) <= set(available):
        raise RetrievalScopeError()
    for r in results:
        f = faqs.get(r.id)
        if f is None:
            continue
        question = f.get('standard_question', f.get('question', '')) if isinstance(f, dict) else f.question
        answers = f.get('answers', []) if isinstance(f, dict) else f.answers
        content = 'Q: ' + go_strip(question) + '\n' if go_strip(question) else ''
        valid = [go_strip(str(a)) for a in answers if go_strip(str(a))]
        if valid:
            content += 'Answer:\n' + ''.join(('- ' + a + '\n' for a in valid))
        r.content = go_strip(content)

async def expand_neighbors(results, state, context, repository):
    eligible = [r for r in results if r.chunk_type == 'text' and len(r.content) < 350]
    cache = await _chunks(repository, [r.id for r in eligible], state, context)
    for r in eligible:
        base = cache.get(r.id)
        if base is None or not base.content or base.chunk_type != 'text':
            continue
        before = after = ''
        prev = base.pre_chunk_id
        next = base.next_chunk_id
        seen = {base.id}
        added = []
        while prev or next:
            request = [i for i in (prev, next) if i and i not in cache and (i not in seen)]
            cache.update(await _chunks(repository, request, state, context))
            for side in ('prev', 'next'):
                cursor = prev if side == 'prev' else next
                c = cache.get(cursor)
                if cursor in seen or c is None or c.knowledge_id != r.knowledge_id:
                    if side == 'prev':
                        prev = ''
                    else:
                        next = ''
                    continue
                seen.add(cursor)
                # A neighbor is optional context. Never sacrifice the selected
                # source body or split a formula/table merely to fill a window.
                candidate_before = join_content(c.content, before) if side == 'prev' else before
                candidate_after = join_content(after, c.content) if side == 'next' else after
                if len(join_content(join_content(candidate_before, base.content), candidate_after)) > 850:
                    if side == 'prev':
                        prev = ''
                    else:
                        next = ''
                    continue
                added.append(cursor)
                if side == 'prev':
                    before = join_content(c.content, before)
                    prev = c.pre_chunk_id
                else:
                    after = join_content(after, c.content)
                    next = c.next_chunk_id
                if len(join_content(join_content(before, base.content), after)) >= 350 and len(added) > 2:
                    break
            merged = join_content(join_content(before, base.content), after)
            if len(merged) >= 350:
                break
        if base.content:
            r.content = join_content(join_content(before, base.content), after)
            r.content_rewritten = True
            r.sub_chunk_ids.extend((i for i in added if i not in r.sub_chunk_ids))


def _deduplicate_origins(results, segmenter=None, *, overlap=False):
    """A generated Summary cannot replace a source body, even with identical text."""
    results = deduplicate(results, preserve_sources=True)
    return partial_overlap(results, segmenter, preserve_sources=True) if overlap else results


async def merge(state, context, repository, segmenter=None):
    if not state.needs_retrieval:
        return
    results = deepcopy(state.rerank_results or sorted(state.search_results, key=lambda r: r.score, reverse=True))
    preserve_sources = getattr(state, 'source_evidence_mode', 'off') == 'bounded_source_v1'
    if preserve_sources:
        # Validate before content/identity dedup can hide an invalid candidate.
        validate_results([r for r in results if r.match_type != 'web_search'], state.targets, context)
    dedup = _deduplicate_origins if preserve_sources else deduplicate
    # Old answers/citations aid reference understanding, never replace current
    # Search and Rerank with stored source bodies or stale versions.
    results = dedup(results)
    validate_results([r for r in results if r.match_type != 'web_search'], state.targets, context)
    if repository:
        results = await resolve_parents(results, state, context, repository)
    results = sequential(results, preserve_sources=preserve_sources)
    if repository:
        await populate_faq(results, state, context, repository)
        await expand_neighbors(results, state, context, repository)
    state.merge_results = (_deduplicate_origins(sequential(results, preserve_sources=True), segmenter, overlap=True)
                           if preserve_sources else partial_overlap(deduplicate(sequential(results)), segmenter))
