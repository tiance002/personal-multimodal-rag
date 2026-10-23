"""Threshold retry, top-1 fallback, composite relevance and MMR."""
from __future__ import annotations
from .query import go_strip, go_fields
import json
import math
import re
from .query import simple_tokens, jaccard
from .search import SearchNothing
from ..models.types import CallCancelled, RunDeadlineExceeded
from ..models.cloud_errors import raise_if_send_denied


class InvalidRerankResponse(ValueError):
    """Shared safety check, separate from the opt-in Top1 policy experiment."""


def _validate_raw_results(raw, candidate_count):
    seen = set()
    for result in raw:
        index, score = getattr(result, 'index', None), getattr(result, 'score', None)
        try:
            finite = type(score) in (int, float) and math.isfinite(score)
        except (OverflowError, ValueError):
            finite = False
        if type(index) is not int or not 0 <= index < candidate_count or index in seen or not finite:
            raise InvalidRerankResponse('RERANK_RESPONSE_INVALID')
        seen.add(index)
    return raw

def clean_passage(text):
    substitutions = [('(?s)```[^\\r\\n]*\\r?\\n(.*?)\\r?\\n?```', '\\1'), ('(?s)\\$\\$(.*?)\\$\\$', '\\1'), ('</?[a-zA-Z][^>]*>', ''), ('\\[(!\\[[^\\]]*\\]\\([^)]*\\))\\]\\([^)]*\\)', '\\1'), ('!\\[[^\\]]*\\]\\((?:[^()]|\\([^()]*\\))*\\)', ''), ('\\[([^\\]]*)\\]\\((?:[^()]|\\([^()]*\\))*\\)', '\\1'), ('https?://[^\\s)\\]>]+', ''), ('(?m)^[ \\t]*\\|[ \\t:|-]+\\|[ \\t]*$', '')]
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)
    text = re.sub('(?m)^[ \\t]*\\|(.+?)\\|[ \\t]*$', lambda m: ', '.join((go_strip(c) for c in m[1].split('|') if go_strip(c))), text)
    for pattern in ('(?m)^#{1,6}\\s+', '(?m)^>\\s?', '(?m)^[\\t ]*(?:[-*+]|\\d+\\.)\\s+'):
        text = re.sub(pattern, '', text)
    for pattern in ('\\*\\*\\*(.*?)\\*\\*\\*', '\\*\\*(.*?)\\*\\*', '\\*(.*?)\\*'):
        text = re.sub(pattern, '\\1', text)
    return go_strip(re.sub('\\n{3,}', '\n\n', text))

def filter_image_derivative_status(info):
    """Exclude incomplete derivatives from evidence; retain status-free legacy.

    Ingestion preserves partial text for document inspection. Neither scoring
    passages nor answer context may promote that text back into valid evidence.
    """
    for text_key, status_key in (('caption', 'caption_status'), ('ocr_text', 'ocr_status')):
        if status_key not in info:
            continue
        status = info[status_key]
        if isinstance(status, dict) and 'status' not in status:
            continue  # Older records may contain an empty status object.
        if not isinstance(status, dict) or status.get('status') != 'completed':
            info[text_key] = ''
    return info


def image_infos(raw):
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            return []
        output = []
        for value in values:
            if value is None:
                continue
            if not isinstance(value, dict):
                return []
            info = dict(value)
            for key in ('url', 'original_url', 'caption', 'ocr_text'):
                if info.get(key) is None:
                    info[key] = ''
                if not isinstance(info[key], str):
                    return []
            for key in ('start_pos', 'end_pos'):
                if info.get(key) is None:
                    info[key] = 0
                if type(info[key]) is not int:
                    return []
            output.append(filter_image_derivative_status(info))
        return output
    except (ValueError, TypeError):
        return []

def enriched_passage(result, *, deduplicate_images=False):
    base = clean_passage(result.content)
    extra = []
    seen = {go_strip(result.content)} if deduplicate_images else set()
    for img in image_infos(result.image_info):
        for key in ('caption', 'ocr_text'):
            value = img.get(key)
            if not isinstance(value, str) or not value:
                continue
            if deduplicate_images:
                exact = go_strip(value)
                if not exact or exact in seen:
                    continue
                seen.add(exact)
            extra.append(value)
    metadata = result.chunk_metadata
    if isinstance(metadata, (bytes, str)):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    generated = metadata.get('generated_questions') or []
    questions = [q.get('question', '') for q in generated if isinstance(q, dict) and isinstance(q.get('question', ''), str)] if isinstance(generated, list) else []
    if questions:
        extra.append('; '.join(questions))
    return base + ('\n\n' if base and extra else '') + '\n'.join(extra)


def model_passage(result, *, passage=None):
    """v0.8.3 ModelPassage: trusted title/breadcrumb plus the enriched body.

    The indexed matched_content may contain generated/expanded text; it is not
    substituted for source content. These prefixes do not alter source quotes.
    """
    passage = enriched_passage(result, deduplicate_images=True) if passage is None else passage
    if not go_strip(passage) or result.chunk_type == 'faq':
        return passage
    prefixes = [go_strip(result.knowledge_title), go_strip(result.context_header)]
    return '\n\n'.join([p for p in prefixes if p] + [passage])

def mmr(candidates, passages, k, segmenter=None):
    tokens = [simple_tokens(text, segmenter) for text in passages]
    chosen = []
    remaining = set(range(len(candidates)))
    while remaining and len(chosen) < k:
        best, best_score = (-1, -1.0)
        for i in sorted(remaining):
            redundancy = max((jaccard(tokens[i], tokens[j]) for j in chosen), default=0.0)
            score = 0.7 * candidates[i].score - 0.3 * redundancy
            if score > best_score:
                best, best_score = (i, score)
        chosen.append(best)
        remaining.remove(best)
    return [candidates[i] for i in chosen]

async def rerank(state, context, registry, segmenter=None):
    floorless = getattr(state, 'rerank_top1_floorless', False)
    if type(floorless) is not bool:
        raise ValueError('RERANK_TOP1_SETTING_INVALID')
    if floorless and getattr(state, 'graph_enabled', False):
        raise ValueError('GRAPH_TOP1_EXPERIMENT_CONFLICT')
    if not state.needs_retrieval:
        return
    if not any(r is not None for r in state.search_results):
        state.evidence_trace.append({'stage': 'rerank', 'reason': 'SEARCH_EMPTY'})
        raise SearchNothing()
    if not state.rerank_model_id:
        return
    try:
        model = await registry.rerank(state.rerank_model_id, context)
    except (CallCancelled, RunDeadlineExceeded):
        raise
    except Exception as exc:
        state.evidence_trace.append({'stage': 'rerank', 'reason': 'RERANK_MODEL_ERROR',
            'operation': 'model_lookup'})
        raise_if_send_denied(exc)
        if getattr(state, '_strategy_serial', False) and (isinstance(exc, PermissionError) or getattr(exc, 'sent', False)):
            raise
        raise RuntimeError('GET_RERANK_MODEL_FAILED') from None
    candidates = []
    passages = []
    model_passages = []
    with_context = getattr(state, 'rerank_context_enabled', False)
    for r in state.search_results:
        if r is None:
            continue
        passage = enriched_passage(r, deduplicate_images=with_context)
        if go_strip(passage):
            candidates.append(r)
            passages.append(passage)
            model_passages.append(model_passage(r, passage=passage) if with_context else passage)
    if not passages:
        return

    invoke_count = 0
    async def invoke():
        nonlocal invoke_count
        invoke_count += 1
        # Provider still enforces document/request limits and stable batch indices.
        suffix = 'rerank' if invoke_count == 1 or not getattr(state, '_strategy_serial', False) else 'rerank/threshold-' + str(invoke_count)
        response = await model.rerank(state.rewrite_query, model_passages, context.child(suffix))
        try:
            raw = list(response.results if hasattr(response, 'results') else response)
        except TypeError:
            raise InvalidRerankResponse('RERANK_RESPONSE_INVALID') from None
        _validate_raw_results(raw, len(candidates))
        state.strategy_rerank_scores = {candidates[r.index].id:r.score for r in raw}
        state.evidence_trace.append({'stage': 'rerank_raw', 'input_ids': [r.id for r in candidates],
            'response': [{'index': r.index, 'score': r.score} for r in raw]})
        return raw

    def filtered(raw, threshold):
        selected = [r for r in raw if r.index < len(candidates) and r.score >= threshold]
        if floorless:
            if not selected and raw:
                selected = [max(raw, key=lambda r: r.score)]
            return selected
        floor = 0 if any((t.disable_recall_thresholds for t in state.targets)) else 0.15
        if not selected and raw and (raw[0].score >= floor):
            selected = [raw[0]]
        return selected
    try:
        raw = await invoke()
    except (CallCancelled, RunDeadlineExceeded):
        raise
    except InvalidRerankResponse:
        raise
    except Exception as exc:
        state.evidence_trace.append({'stage': 'rerank', 'reason': 'RERANK_MODEL_ERROR',
            'operation': 'rerank'})
        raise_if_send_denied(exc)
        if getattr(state, '_strategy_serial', False):
            if isinstance(exc, PermissionError) or getattr(exc, 'sent', False):
                raise
            from .search import SearchError
            raise SearchError('RERANK_FAILED') from None
        state.search_results = candidates
        return
    selected = filtered(raw, state.rerank_threshold)
    if not selected and state.rerank_threshold > 0.3:
        try:
            selected = filtered(await invoke(), max(state.rerank_threshold * 0.7, 0.3))
        except (CallCancelled, RunDeadlineExceeded):
            raise
        except InvalidRerankResponse:
            raise
        except Exception as exc:
            state.evidence_trace.append({'stage': 'rerank', 'reason': 'RERANK_MODEL_ERROR',
                'operation': 'threshold_retry'})
            raise_if_send_denied(exc)
            if getattr(state, '_strategy_serial', False):
                if isinstance(exc, PermissionError) or getattr(exc, 'sent', False):
                    raise
                from .search import SearchError
                raise SearchError('RERANK_FAILED') from None
            state.search_results = candidates
            return
    state.strategy_rerank_accepted_ids = {candidates[r.index].id for r in selected}
    accepted = []
    texts = []
    from copy import deepcopy
    for rr in selected:
        if rr.index < 0 or rr.index >= len(candidates):
            continue
        r = deepcopy(candidates[rr.index])
        base = r.score
        source = 0.95 if r.knowledge_source.lower() == 'web_search' else 1.0
        r.score = min(1.0, max(0.0, 0.6 * rr.score + 0.3 * base + 0.1 * source))
        if not isinstance(r.metadata, dict):
            r.metadata = {}
        r.metadata.update(base_score=f'{base:.4f}', model_score=f'{rr.score:.4f}')
        if state.faq_priority_enabled and r.chunk_type == 'faq' and (state.faq_score_boost > 1):
            r.metadata.update(faq_boosted='true', faq_original_score=f'{r.score:.4f}')
            r.score = min(1.0, r.score * state.faq_score_boost)
        accepted.append(r)
        texts.append(passages[rr.index])
    state.rerank_results = mmr(accepted, texts, min(len(accepted), max(1, state.rerank_top_k)), segmenter)
    state.evidence_trace.append({'stage': 'rerank_filter_mmr', 'threshold': state.rerank_threshold,
        'reason': 'RERANK_ACCEPTED' if state.rerank_results else 'RERANK_ALL_BELOW_THRESHOLD_SEARCH_FALLBACK',
        'accepted_ids': [r.id for r in accepted], 'mmr_ids': [r.id for r in state.rerank_results],
        'search_scores': [{'id': r.id, 'score': r.score} for r in state.search_results if r is not None]})
    # An empty rerank pool is distinct from empty Search. Merge's existing
    # fallback sorts the original Search results, preserving their scores.
