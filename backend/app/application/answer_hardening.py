"""Deterministic post-generation controls. No model, retrieval or gold access."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.app.application.run_metrics import current_metrics

VERSION = 'v1.1-hardening-2'
MARKER = re.compile(r'\[(E\d+)\]')
ANY_MARKER = re.compile(r'\[(E(?:\d+|#)(?:\s*[-,、]\s*E?\d+)*)\]')
QUESTION = re.compile(r'什么|如何|怎么|哪些|多少|多久|是否|为何|为什么|怎样|何时|\b(?:what|how|why|which|when)\b', re.I)


def normalize_marker_spacing(answer: str) -> str:
    return re.sub(r'\[\s*(E\d+)\s*\]', r'[\1]', answer)


@dataclass(frozen=True)
class IntentPlan:
    intents: tuple[str, ...]
    route_reason: str

    def checklist(self) -> str:
        if len(self.intents) < 2:
            return ''
        return '\nQuestion intents (answer each briefly; explicitly mark missing evidence; check all before finishing):\n' + '\n'.join(f'{i}. {q}' for i, q in enumerate(self.intents, 1))


def detect_intents(question: str) -> IntentPlan:
    if question.startswith('上一轮问题背景（不是证据）：') and '\n只回答当前问题：' in question:
        current = question.split('\n只回答当前问题：', 1)[1]
        current_plan = detect_intents(current)
        if len(current_plan.intents) == 1:
            return IntentPlan((current,), 'FOLLOW_UP_CURRENT_QUESTION')
        return current_plan
    comma_parts = re.split(r'[，,]', question)
    if sum(bool(QUESTION.search(p)) for p in comma_parts) >= 2:
        intents, prefix = [], []
        for part in comma_parts:
            if QUESTION.search(part):
                intents.append('，'.join([*prefix, part]).strip(' 。？?'))
                prefix = []
            else:
                prefix.append(part)
        if prefix:
            intents[-1] += '，' + '，'.join(prefix)
        return IntentPlan(tuple(intents), 'MULTIPLE_INTERROGATIVE_CLAUSES')
    clauses = tuple(s.strip(' ，,。；;？?') for s in re.split(r'[？?；;]|(?:同时|另外|并且)(?=.{0,60}(?:什么|如何|怎么|哪些|是否))', question) if s.strip(' ，,。；;？?'))
    if len(clauses) >= 2 and all(QUESTION.search(s) for s in clauses):
        return IntentPlan(clauses, 'MULTIPLE_INTERROGATIVE_CLAUSES')
    explicit = re.fullmatch(r'\s*分别(?:解释|说明|介绍)\s*(.{1,120})[。？?]?\s*', question)
    if explicit:
        subjects = tuple(x.strip(' 。？?') for x in re.split(r'、|以及|和|与', explicit[1]))
        if 2 <= len(subjects) <= 6 and all(subjects):
            return IntentPlan(tuple(f'解释{x}' for x in subjects), 'EXPLICIT_SEPARATELY')
    return IntentPlan((question,), 'SINGLE_OR_AMBIGUOUS')


@dataclass(frozen=True)
class GenerationBudget:
    complexity: str
    max_tokens: int
    max_calls: int = 1


def generation_budget(intents: IntentPlan, context_chars: int) -> GenerationBudget:
    # 512 is the existing single-fact cap. Larger answers get a finite increase;
    # context size alone cannot route every question to the largest budget.
    if len(intents.intents) >= 3 or (len(intents.intents) >= 2 and context_chars > 4000):
        return GenerationBudget('COMPLEX', 1280)
    if len(intents.intents) >= 2 or (context_chars > 2000 and (len(intents.intents[0]) > 70 or re.search(r'哪些|分别|\b(?:list|compare)\b', intents.intents[0], re.I))):
        return GenerationBudget('NORMAL', 896)
    return GenerationBudget('SIMPLE', 512)


def topic_anchors(intent: str) -> list[str]:
    text = re.sub(r'^(?:请|分别|解释|说明|介绍|根据资料|资料中|文档中|资料所说的|文档所说的)\s*', '', intent)
    text = re.split(r'什么|如何|怎么|哪些|多少|是否|为何|为什么|怎样|有何', text)[0]
    text = re.sub(r'(?:是|有|的|又|具体|有什么作用|有什么用途)$', '', text).strip(' ，,。？?：:')
    tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_./-]{1,}|[\u4e00-\u9fff]{2,}', text)
    # Only exact anchors: no guessed synonyms or case-specific topic lists.
    return [t for t in tokens if len(t) <= 40]


def intent_coverage(answer: str, plan: IntentPlan) -> list[str]:
    return ['MENTIONED' if (anchors := topic_anchors(q)) and any(a.casefold() in answer.casefold() for a in anchors) else 'MISSING_OR_UNKNOWN' for q in plan.intents]


def citation_occurrences(answer: str, snapshots: Any, chunks: Any) -> list[dict[str, Any]]:
    by_label = {s.label: s for s in snapshots}
    by_identity = {(c.version_id, c.chunk_id): c for c in chunks}
    rows = []
    for index, match in enumerate(ANY_MARKER.finditer(answer), 1):
        source = by_label.get(match[1])
        prefix = answer[:match.start()].rstrip()
        search_end = len(prefix) - 1 if prefix.endswith(('。', '！', '？', ';', '；')) else len(prefix)
        begin = max((answer.rfind(p, 0, search_end) for p in ('。', '！', '？', ';', '；', '\n')), default=-1) + 1
        claim = MARKER.sub('', answer[begin:match.start()]).strip().strip(' -*#：:，,')
        if rows and not answer[rows[-1]["marker_span"][1]:match.start()].strip():
            # Adjacent markers share the already-resolved preceding claim,
            # including its sentence boundary when multiple sentences share a line.
            begin = rows[-1]["answer_span"][0]
            claim = MARKER.sub('', answer[begin:match.start()]).strip().strip(' -*#：:，,')
        span_end = match.end()
        tail = answer[match.end():]
        if source and not claim and tail.lstrip().startswith(source.quote):
            span_end += len(tail) - len(tail.lstrip()) + len(source.quote)
            claim = source.quote
        chunk = by_identity.get((source.version_id, source.chunk_id)) if source else None
        # Exact source quotation proves only textual support. Semantic support is
        # still an offline Judge decision; lexical overlap never counts as PASS.
        relevance = 'SOURCE_TEXT_MATCH' if source and claim and claim in source.quote else 'CITATION_RELEVANCE_UNKNOWN'
        rows.append({'citation_occurrence': index, 'display_label': match[1],
                     'chunk_id': source.chunk_id if source else None,
                     'document_id': chunk.document_id if chunk else None,
                     'version_id': source.version_id if source else None,
                     'locator': {k: v for k, v in source.locator.items() if k in ('kind', 'start', 'end', 'page', 'bbox', 'asset_id')} if source else None,
                     'answer_span': [begin, span_end], 'marker_span': [match.start(), match.end()],
                     'relevance': relevance if source else 'CITATION_UNSUPPORTED'})
    return rows


def evidence_fallback(question: str, snapshots: Any, plan: Any) -> str:
    """Extract only directly anchored declarative sentences, never raw context.

    Opaque questions, missing topics and nonoperational 'how' snippets refuse.
    This is a narrow eligibility rule, not a claim of semantic sufficiency.
    """
    from backend.app.application.quality import QualityGate
    if plan.evidence_plan.targets:
        lines = []
        for target in plan.evidence_plan.targets:
            found = next(((s, f) for s in snapshots if (f := QualityGate.supporting_fact(s.quote, target))), None)
            if not found:
                return ''
            s, fact = found
            lines.append(f'{fact} [{s.label}]')
        return '根据资料原文，可确认以下内容：\n' + '\n'.join(lines)
    if re.search(r'多少|几(?:个|次|天|小时|秒)|\bhow (?:many|much)\b', question, re.I):
        return ''
    lines = []
    for intent in detect_intents(question).intents:
        anchors = topic_anchors(intent)
        if not anchors:
            return ''
        found = None
        for source in snapshots:
            for sentence in re.split(r'(?<=[。！？])|\n', source.quote):
                sentence = sentence.strip()
                if not 8 <= len(sentence) <= 420 or '?' in sentence or '？' in sentence:
                    continue
                if not any(a.casefold() in sentence.casefold() for a in anchors):
                    continue
                if not re.search(r'是|用于|通过|可以|需要|应当|必须|执行|使用|支持|包含|提供|采用|设置|调用|保存|返回|写下|回顾|重复|\b(?:is|are|use|must|can)\b', sentence, re.I):
                    continue
                if re.search(r'如何|怎么|怎样', intent) and not re.search(r'通过|执行|使用|设置|调用|保存|步骤|写下|回顾|重复|\buse\b', sentence, re.I):
                    continue
                found = f'{sentence} [{source.label}]'
                break
            if found:
                break
        if not found:
            return ''
        if found not in lines:
            lines.append(found)
    return '根据资料原文，可确认以下内容（原文摘录）：\n' + '\n'.join(lines)


def obvious_contradiction(answer: str, snapshots: Any) -> bool:
    for match in re.finditer(r'(?:没有提及|没有提到|未提及|不存在)\s*([^。！？\n\[，,]{2,40})', answer):
        topic = match[1].strip(' “”—：:')
        positive = re.split(r'[。！？\n]', answer[match.end():])
        for clause in positive:
            if topic in clause and not re.search(r'没有|不存在|未提及', clause):
                for label in MARKER.findall(clause):
                    if any(s.label == label and topic in s.quote for s in snapshots):
                        return True
    return False


@dataclass(frozen=True)
class FinalizedAnswer:
    answer: str
    error: str | None
    rejection: str | None
    fallback: str | None
    coverage: list[str]
    occurrences: list[dict[str, Any]]


class HardeningPolicy:
    def __init__(self, audit: Any = None) -> None:
        self.audit = audit

    def check_candidate(self, candidate: str, snapshots: Any, plan: Any, error: str | None) -> str | None:
        """Side-effect-free original-candidate check before audit/fallback/commit."""
        from backend.app.application.final_answer_commit import FinalAnswerCommitCheck
        if not isinstance(candidate, str) or not candidate.strip():
            return error or 'MODEL_EMPTY'
        labels = {s.label for s in snapshots}
        if set(MARKER.findall(candidate)) - labels or any(m[1] not in labels for m in ANY_MARKER.finditer(candidate)):
            return 'INVALID_CITATION'
        if not error and obvious_contradiction(candidate, snapshots):
            return 'SELF_CONTRADICTION'
        return error or FinalAnswerCommitCheck().check(candidate, snapshots, plan.evidence_plan)

    def observe_evidence_answer(self, answer: str, snapshots: Any, chunks: Any, plan: Any) -> None:
        if (metrics := current_metrics()) is not None:
            intents = detect_intents(plan.question)
            metrics.hardening.update({'version': VERSION, 'evidence_only': True,
                'route_reason': intents.route_reason, 'detected_intent_count': len(intents.intents),
                'intent_coverage': intent_coverage(answer, intents),
                'coverage_basis': 'exact_topic_presence_not_semantic_verdict',
                'citation_occurrences': citation_occurrences(answer, snapshots, chunks), 'continuation_calls': 0})

    def finalize(self, run_id: str, candidate: str, snapshots: Any, chunks: Any, plan: Any, error: str | None) -> FinalizedAnswer:
        from backend.app.application.final_answer_commit import FinalAnswerCommitCheck
        intent_plan = detect_intents(plan.question)
        coverage = intent_coverage(candidate, intent_plan)
        labels = {s.label for s in snapshots}
        if set(MARKER.findall(candidate)) - labels:
            error = 'INVALID_CITATION'
        if any(m[1] not in labels for m in ANY_MARKER.finditer(candidate)):
            error = 'INVALID_CITATION'
        if not error and obvious_contradiction(candidate, snapshots):
            error = 'SELF_CONTRADICTION'
        rejection = error
        audit_status = 'NOT_REQUIRED'
        candidate_rows = citation_occurrences(candidate, snapshots, chunks)
        if error:
            record = {'schema_version': VERSION, 'run_id': run_id,
                      'request_identity': hashlib.sha256(plan.question.encode()).hexdigest(),
                      'validator_result': 'FAIL', 'validator_reason': error, 'validator_rule_version': VERSION,
                      'candidate_present': bool(candidate), 'candidate_hash': hashlib.sha256(candidate.encode()).hexdigest(),
                      'candidate_local_audit_payload': {'text': candidate},
                      'supporting_context_chunk_ids': [s.chunk_id for s in snapshots],
                      'citation_candidate_mapping': candidate_rows,
                      'timestamp': datetime.now(timezone.utc).isoformat()}
            try:
                audit_status = 'SAVED_LOCAL' if self.audit and self.audit.save(record) else 'AUDIT_UNAVAILABLE'
            except (OSError, ValueError):
                audit_status = 'AUDIT_WRITE_FAILED'
            answer = (evidence_fallback(plan.question, snapshots, plan)
                      if audit_status == 'SAVED_LOCAL' and error != 'MODEL_OUTPUT_TRUNCATED' else '')
            fallback = 'EVIDENCE_ONLY_FALLBACK' if answer else None
            # Never release the rejected candidate. No inference or new call.
            if answer:
                error = FinalAnswerCommitCheck().check(answer, snapshots, plan.evidence_plan, answer_type='fallback')
                if error:
                    answer = ''
            # No fallback (including a truncated generation) retains its error.
        else:
            answer, fallback = candidate, None
            error = FinalAnswerCommitCheck().check(answer, snapshots, plan.evidence_plan)
            if error:
                answer = ''
        rows = citation_occurrences(answer, snapshots, chunks)
        if (metrics := current_metrics()) is not None:
            metrics.hardening.update({'version': VERSION, 'route_reason': intent_plan.route_reason,
                                     'detected_intent_count': len(intent_plan.intents),
                                     'intent_coverage': coverage, 'coverage_basis': 'exact_topic_presence_not_semantic_verdict',
                                     'validator_rejection': rejection, 'audit_status': audit_status,
                                     'fallback': fallback, 'citation_occurrences': rows,
                                     'candidate_citation_relevance_counts': {k: sum(r['relevance'] == k for r in candidate_rows) for k in ('SOURCE_TEXT_MATCH', 'CITATION_RELEVANCE_UNKNOWN', 'CITATION_UNSUPPORTED')},
                                     'continuation_calls': 0})
        return FinalizedAnswer(answer, error, rejection, fallback, coverage, rows)
