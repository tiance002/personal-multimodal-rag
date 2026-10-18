"""Threshold retry, top-1 fallback, composite relevance and MMR."""
from __future__ import annotations
from .query import go_strip, go_fields
import json
import re
from .query import simple_tokens, jaccard
from .search import SearchNothing
from ..models.types import CallCancelled, RunDeadlineExceeded
from ..models.cloud_errors import raise_if_send_denied

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
            output.append(info)
        return output
    except (ValueError, TypeError):
        return []

def enriched_passage(result):
    base = clean_passage(result.content)
    extra = []
    for img in image_infos(result.image_info):
        extra.extend((img[k] for k in ('caption', 'ocr_text') if isinstance(img.get(k), str) and img[k]))
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
    if not state.needs_retrieval or not state.search_results or (not state.rerank_model_id):
        return
    try:
        model = await registry.rerank(state.rerank_model_id, context)
    except (CallCancelled, RunDeadlineExceeded):
        raise
    except Exception as exc:
        raise_if_send_denied(exc)
        raise RuntimeError('GET_RERANK_MODEL_FAILED') from None
    candidates = []
    passages = []
    for r in state.search_results:
        if r is None:
            continue
        passage = enriched_passage(r)
        if go_strip(passage):
            candidates.append(r)
            passages.append(passage)
    if not passages:
        return

    async def invoke():
        response = await model.rerank(state.rewrite_query, passages, context.child('rerank'))
        return list(response.results if hasattr(response, 'results') else response)

    def filtered(raw, threshold):
        selected = [r for r in raw if r.index < len(candidates) and r.score >= threshold]
        floor = 0 if any((t.disable_recall_thresholds for t in state.targets)) else 0.15
        if not selected and raw and (raw[0].score >= floor):
            selected = [raw[0]]
        return selected
    try:
        raw = await invoke()
    except (CallCancelled, RunDeadlineExceeded):
        raise
    except Exception as exc:
        raise_if_send_denied(exc)
        state.search_results = candidates
        return
    selected = filtered(raw, state.rerank_threshold)
    if not selected and state.rerank_threshold > 0.3:
        try:
            selected = filtered(await invoke(), max(state.rerank_threshold * 0.7, 0.3))
        except (CallCancelled, RunDeadlineExceeded):
            raise
        except Exception as exc:
            raise_if_send_denied(exc)
            state.search_results = candidates
            return
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
    if not state.rerank_results:
        raise SearchNothing()
