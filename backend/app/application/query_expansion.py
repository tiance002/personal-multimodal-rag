"""One deterministic lexical recovery stage; proxy signals are not true recall.

Variants project only the validated retrieval expression. No history/memory,
synonym entities, LLM dispatch or embedding calls are introduced here.
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from backend.app.domain.text_normalization import normalize_query
from backend.app.application.query_router import QueryRouter

STOP = frozenset('what how why when where which the a an is are of for in on to and or 的 是 在 了 和 与 什么 如何 怎么 为什么 多少 请问'.split())
PREFIX = re.compile(r'^(?:请告诉我|我想知道|请问|什么是|如何|怎么|为什么)')

@dataclass(frozen=True)
class ExpansionQuery:
    text: str
    source: str

def risk_signals(question, chunks, keyword_hits, understanding=None, *, keyword_requested=True):
    from backend.app.application.quality import QualityGate
    signals=[]
    if not chunks: signals.append('EMPTY_CANDIDATES')
    if keyword_requested and not keyword_hits: signals.append('KEYWORD_CHANNEL_MISSING')
    terms=[t for t in normalize_query(question).terms if len(t)>1 and t not in STOP]
    body='\n'.join(c.content.lower() for c in chunks)
    absent=[t for t in terms if t not in body]
    if absent: signals.append('KEY_TERMS_UNCOVERED')
    targets=QueryRouter().plan(question).targets
    if any(not QualityGate.supporting_chunks(chunks,t) for t in targets): signals.append('EVIDENCE_TARGET_UNCOVERED')
    task_missing=[]
    if understanding is not None:
        for task in understanding.tasks:
            # Only source-validated C tasks, never inferred hidden requirements.
            if understanding.q0[task.start:task.end]!=task.text: continue
            task_terms=[t for t in normalize_query(task.text).terms if len(t)>1 and t not in STOP]
            if task_terms and any(t not in body for t in task_terms): task_missing.append(task.text)
    if task_missing: signals.append('EXPLICIT_TASK_UNCOVERED')
    if len(chunks)<2: signals.append('FEW_EVIDENCE_UNITS')
    return dict(reasons=signals,missing_term_count=len(absent),missing_target_count=sum(not QualityGate.supporting_chunks(chunks,t) for t in targets),missing_task_count=len(task_missing),
                keyword_channel_state='NOT_SELECTED' if not keyword_requested else 'REQUESTED_NONEMPTY' if keyword_hits else 'REQUESTED_EMPTY')

def variants(question, understanding=None):
    base=normalize_query(question)
    # All hard anchors (Latin identifiers, numbers/dates) remain in each plan.
    explicit_identifiers={t.lower() for t in re.findall(r'\b[A-Z][A-Za-z0-9_-]*\b',question)}
    anchors=[t for t in base.terms if re.search(r'[A-Za-z0-9]',t)
             and (t not in STOP or t in explicit_identifiers or re.search(r'\d',t))]
    cleaned=PREFIX.sub('',question.strip())
    proposals=[ExpansionQuery(cleaned,'FIXED_QUESTION_PREFIX_REMOVAL')]
    phrases=re.findall(r'["“「]([^"”」]+)["”」]',question)
    proposals += [ExpansionQuery(p,'QUOTED_ORIGINAL_PHRASE') for p in phrases]
    if understanding is not None:
        proposals += [ExpansionQuery(t.text,'VALIDATED_TASK_SOURCE_SPAN') for t in understanding.tasks
                      if understanding.q0[t.start:t.end]==t.text]
    proposals += [ExpansionQuery(p,'ORIGINAL_DELIMITED_SEGMENT') for p in re.split(r'[,，;；。！？!?]+',question) if len(p.strip())>3]
    out=[];seen={base.terms}
    for p in proposals:
        tokens=list(dict.fromkeys([*anchors,*(t for t in normalize_query(p.text).terms if t not in STOP)]))
        if not tokens or tuple(tokens) in seen: continue
        # Source substring projection plus immutable anchors is allowed, never a
        # generated entity or a reinterpretation of q0. Constraints still q0.
        seen.add(tuple(tokens));out.append((p,base.model_copy(update={'terms':tuple(tokens)})))
        if len(out)==2:break
    return out

def fingerprint(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()
