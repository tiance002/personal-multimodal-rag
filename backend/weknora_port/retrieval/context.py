"""Final answer-ready context rendering, before any P5 role selection."""
from __future__ import annotations
from .query import go_strip, go_fields
from .types import context_results, bridge_context_attributes
from datetime import datetime, timedelta
import html
import re
from dataclasses import dataclass
from .rerank import image_infos
from .merge import IMAGE
HTML_IMAGE = re.compile('(?i)<img\\b([^>]*?)\\ssrc\\s*=\\s*[\'\\"]([^\'\\"]+)[\'\\"]([^>]*)>')

@dataclass(frozen=True)
class FinalizedContext:
    user_content: str
    rendered_contexts: str
    result_ids: tuple[str, ...]
    system_prompt_override: str = ''
    images: tuple[str, ...] = ()
    context_char_limit: int = 0

def render_placeholders(template, values, now=None):
    now = now or datetime.now()
    values = dict(values)
    for key, value in {'current_time': now.strftime('%Y-%m-%d'), 'current_week': now.strftime('%A'), 'yesterday': (now - timedelta(days=1)).strftime('%Y-%m-%d')}.items():
        values.setdefault(key, value)
    for key, value in values.items():
        template = template.replace('{{' + key + '}}', value)
    return template

def validate_input(text):
    if any((ord(c) < 32 and c not in '\t\n\r' for c in text)):
        raise ValueError('TEMPLATE_INVALID_QUERY')
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('TEMPLATE_INVALID_QUERY') from None
    patterns = [f'<{tag}[^>]*>.*?</{tag}>' for tag in ('script', 'iframe', 'object', 'embed', 'form', 'button')]
    patterns.extend(('<embed[^>]*>', '<input[^>]*>', 'javascript:', 'vbscript:', 'on(?:load|error|click|mouseover|focus|blur)\\s*='))
    if any((re.search(p, text, re.I) for p in patterns)):
        raise ValueError('TEMPLATE_INVALID_QUERY')
    return go_strip(text)

def html_escape(text):
    return html.escape(text, quote=True).replace('&#x27;', '&#39;').replace('&quot;', '&#34;')

def document_header(results):
    seen = set()
    docs = []
    for r in results:
        if not r.knowledge_id or r.knowledge_id in seen:
            continue
        seen.add(r.knowledge_id)
        title = r.knowledge_title or r.knowledge_filename
        if not title:
            continue
        s = '<document>\n<title>' + html_escape(title) + '</title>\n'
        for tag, value in (('description', r.knowledge_description), ('metadata', r.knowledge_custom_metadata)):
            if value:
                s += f'<{tag}>{html_escape(value)}</{tag}>\n'
        docs.append(s + '</document>\n')
    return '<documents>\n' + ''.join(docs) + '</documents>' if docs else ''

def enriched_chat_passage(r):
    images = {}
    for i in image_infos(r.image_info):
        for key in (i.get('url'), i.get('original_url')):
            if key:
                images[key] = i
    injections = []
    matches = list(IMAGE.finditer(r.content))
    for regex, urlgroup, trim in ((IMAGE, 2, False), (HTML_IMAGE, 2, True)):
        for m in regex.finditer(r.content):
            key = go_strip(m[urlgroup]) if trim else m[urlgroup]
            img = images.get(key)
            if not img:
                continue
            lines = []
            for field, label in (('caption', 'Image caption'), ('ocr_text', 'Image text (OCR)')):
                value = go_strip(str(img.get(field, '')))
                if value:
                    lines.append(f'**{label}:** {value}')
            if lines:
                injections.append((m.end(), '\n\n> ' + '\n\n'.join(lines).replace('\n', '\n> ')))
    content = r.content
    for pos, text in sorted(injections, reverse=True):
        content = content[:pos] + text + content[pos:]
    return content

async def into_chat_message(state, context=None, *, persist_sink=None, now=None, record_bridge=True):
    query = validate_input(state.query)

    def supplements(content):
        if state.image_description and (not state.chat_model_supports_vision):
            content += '\n\n[用户上传图片内容]\n' + state.image_description
        if state.quoted_context:
            content += '\n\n' + state.quoted_context
        return content + state.attachment_prompt
    if not state.needs_retrieval:
        if go_strip(state.rewrite_query):
            try:
                query = validate_input(state.rewrite_query)
            except ValueError:
                pass
        query = supplements(query)
        rendered = render_placeholders(state.context_template, {'query': query, 'contexts': '', 'language': state.language}, now) if state.context_template else query
        state.rendered_contexts = ''
        state.user_content = rendered
    else:
        results = context_results(state)
        faq = [r for r in results if r.chunk_type == 'faq'] if state.faq_priority_enabled else []
        docs = [r for r in results if r.chunk_type != 'faq'] if faq else results
        header = document_header(faq + docs if faq else results)
        contexts = header + '\n' if header else ''
        if faq:
            high = any((r.score >= state.faq_direct_answer_threshold for r in faq))
            contexts += '<source type="faq" priority="high">\n'
            for i, r in enumerate(faq):
                exact = ' match="exact"' if high and i == 0 else ''
                contexts += f'<context id="FAQ-{i + 1}"{exact}{bridge_context_attributes(r)}>{enriched_chat_passage(r)}</context>\n'
            contexts += '</source>\n'
            if docs:
                contexts += '<source type="document" priority="supplementary">\n'
                contexts += ''.join((f'<context id="DOC-{i + 1}"{bridge_context_attributes(r)}>{enriched_chat_passage(r)}</context>\n' for i, r in enumerate(docs)))
                contexts += '</source>'
        else:
            contexts += '\n'.join((f'<context id="{i + 1}"{bridge_context_attributes(r)}>{enriched_chat_passage(r)}</context>' for i, r in enumerate(results)))
        state.rendered_contexts = contexts
        state.user_content = supplements(render_placeholders(state.context_template, {'query': query, 'contexts': contexts, 'language': state.language}, now))
        if persist_sink and state.user_message_id and state.user_content and (state.user_content != state.query):
            try:
                await persist_sink(state, context)
            except Exception:
                pass
    limit = 0
    if getattr(state, 'source_evidence_mode', 'off') != 'off':
        from .source_evidence import CONTEXT_CHARS, SourceEvidenceBudgetError
        limit = CONTEXT_CHARS
        if len(state.rendered_contexts) > limit:
            raise SourceEvidenceBudgetError('SOURCE_CONTEXT_BUDGET_EXCEEDED')
    if getattr(state, 'graph_context_char_limit', 0):
        from .source_evidence import SourceEvidenceBudgetError
        limit = state.graph_context_char_limit
        if len(state.rendered_contexts) > limit:
            raise SourceEvidenceBudgetError('GRAPH_CONTEXT_BUDGET_EXCEEDED')
    if getattr(state, 'rescue_evidence', ()):
        from .source_evidence import SourceEvidenceBudgetError
        bridge_limit = state.bridge_context_char_limit
        if len(state.rendered_contexts) > bridge_limit:
            raise SourceEvidenceBudgetError('BRIDGE_CONTEXT_BUDGET_EXCEEDED')
        limit = min(limit or bridge_limit, bridge_limit)
    result = FinalizedContext(state.user_content, state.rendered_contexts, tuple((r.id for r in context_results(state))), state.system_prompt_override, tuple(state.images), limit)
    normal_hints = [r for r in context_results(state) if r is not None and
                    (r.metadata or {}).get('bridge_normal_qualification')]
    if record_bridge and (getattr(state, 'rescue_evidence', ()) or normal_hints):
        if getattr(state, 'rescue_evidence', ()):
            state.bridge_trace.append({'stage':'bridge_context', 'decision':'CONTEXT_ADMITTED',
                'source_ids':[r.id for r in state.rescue_evidence], 'result_ids':list(result.result_ids)})
        if normal_hints:
            state.bridge_trace.append({'stage':'bridge_context',
                'decision':'NORMAL_LOCATION_HINT_CONTEXT_ADMITTED',
                'source_ids':[r.metadata['bridge_normal_qualification']['source_chunk_id'] for r in normal_hints],
                'result_ids':list(result.result_ids)})
        supported = not normal_hints and all(
            r.metadata['bridge_rescue'].get('final_answer_supported') is True
            for r in getattr(state, 'rescue_evidence', ()))
        state.bridge_trace.append({'stage':'bridge_context',
            'decision':'FINAL_ANSWER_SUPPORTED' if supported else 'FINAL_ANSWER_UNVERIFIED',
            'evidence_chain_supported':supported, 'generated_answer_verified':False})
    state._finalized_context = result
    return result
