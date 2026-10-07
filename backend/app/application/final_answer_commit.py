"""One deterministic SUCCESS contract, independent of execution/fallback path.

This is not semantic entailment or general factual verification. Existing
heuristics remain upstream. Source excerpts are an explicit, exact-quote type,
not an escape hatch for arbitrary generated text.
"""
from collections.abc import Sequence
import hashlib
import json
import re
from typing import Literal

from backend.app.application.answer_hardening import ANY_MARKER, MARKER
from backend.app.application.answer_validation import (
    AnswerValidator, _ANSWER_FACT_SEPARATORS, _REFERENCE, _has_numeric_fact,
    _numeric_safe_split,
)
from backend.app.application.quality import QualityGate
from backend.app.application.query_router import EvidencePlan
from backend.app.application.structured_evidence import claim_amount, row_facts
from backend.app.domain.models import EvidenceSnapshot

AnswerType = Literal["generated", "fallback", "partial", "source_excerpt"]
EXCERPT_PREFIXES = (
    "基于检索到的证据：\n",
    "涉及外发与隐私配置，以下仅提供资料原文；实际运行配置需单独核查：\n",
)


class FinalAnswerCommitCheck:
    def check(self, answer: str, snapshots: Sequence[EvidenceSnapshot], plan: EvidencePlan,
              *, answer_type: AnswerType = "generated", finish_reason: str | None = None,
              citations: Sequence[str] | None = None) -> str | None:
        if finish_reason == "length":
            return "MODEL_OUTPUT_TRUNCATED"
        if not isinstance(answer, str) or not answer.strip():
            return "MODEL_EMPTY"
        if answer_type not in {"generated", "fallback", "partial", "source_excerpt"}:
            return "INVALID_ANSWER_TYPE"
        by_label = {}
        identities = set()
        for source in snapshots:
            if (not isinstance(source, EvidenceSnapshot)
                    or not isinstance(source.label, str) or not re.fullmatch(r"E[1-9]\d*", source.label)
                    or source.label in by_label
                    or not isinstance(source.version_id, str) or not source.version_id
                    or not isinstance(source.chunk_id, str) or not source.chunk_id
                    or not isinstance(source.quote, str) or not source.quote.strip() or not isinstance(source.locator, dict)
                    or hashlib.sha256(source.quote.encode("utf-8")).hexdigest() != source.quote_sha256):
                return "INVALID_EVIDENCE_SNAPSHOT"
            identity = (source.version_id, source.chunk_id)
            if identity in identities:
                return "INVALID_EVIDENCE_SNAPSHOT"
            try:
                json.dumps(source.locator, allow_nan=False)
            except (TypeError, ValueError):
                return "INVALID_EVIDENCE_SNAPSHOT"
            identities.add(identity)
            by_label[source.label] = source
        labels = tuple(dict.fromkeys(MARKER.findall(answer)))
        if (set(labels) - by_label.keys()
                or any(m[1] not in by_label for m in ANY_MARKER.finditer(answer))):
            return "INVALID_CITATION"
        if citations is not None and (len(set(citations)) != len(citations) or set(citations) != set(labels)):
            return "INVALID_CITATION"
        if answer_type == "source_excerpt":
            rendered = "\n\n".join(f"[{s.label}] {s.quote}" for s in snapshots)
            if not snapshots or not any(answer == prefix + rendered for prefix in EXCERPT_PREFIXES):
                return "INVALID_SOURCE_EXCERPT"
            # Do not present model-derived numbers as original source facts,
            # even in the model-free evidence-only path.
            if any(QualityGate.is_model_caption(s.locator) and _has_numeric_fact(s.quote) for s in snapshots):
                return "UNSUPPORTED_ANSWER"
            return None

        caption_cited = any(QualityGate.is_model_caption(by_label[label].locator) for label in labels)
        clauses = (_numeric_safe_split(answer, _ANSWER_FACT_SEPARATORS) if caption_cited
                   else _ANSWER_FACT_SEPARATORS.split(answer))
        if caption_cited:
            for clause in clauses:
                if (_has_numeric_fact(_REFERENCE.sub("", clause))
                        and not AnswerValidator._independent_numeric_claims(clause, by_label, plan.targets)):
                    return "UNSUPPORTED_ANSWER"
        for target in plan.targets:
            if target.period is None:
                continue  # No strong structured witness contract for this target.
            structured = [s for s in snapshots if not QualityGate.is_model_caption(s.locator)
                          and (s.locator.get("kind") == "table" or "\t" in s.quote)]
            if not structured:
                continue
            claims = [c for c in clauses if target.attribute in c and re.search(r"\d", _REFERENCE.sub("", c))
                      and not any(other != target and claim_amount(_REFERENCE.sub("", c), other) is not None
                                  for other in plan.targets)]
            if not claims:
                # Explicit partial answers may leave targets unanswered. No
                # missing target licenses an unsupported numeric assertion.
                if answer_type == "partial":
                    continue
                return "UNSUPPORTED_ANSWER"
            facts = {s.label: row_facts(s.quote, s.locator, target, s.quote_sha256,
                                      version_id=s.version_id) for s in structured}
            signatures = {(f.value, f.unit) for rows in facts.values() for f in rows}
            if len(signatures) != 1:
                return "UNSUPPORTED_ANSWER"
            for clause in claims:
                amount = claim_amount(_REFERENCE.sub("", clause), target)
                if amount is None or not any(amount in {(f.value, f.unit) for f in facts.get(label, ())}
                                             for label in MARKER.findall(clause)):
                    return "UNSUPPORTED_ANSWER"
        return None
