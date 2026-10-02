from __future__ import annotations

import re
from collections.abc import Sequence

from backend.app.application.query_router import EvidencePlan, EvidenceTarget
from backend.app.application.quality import QualityGate
from backend.app.domain.models import EvidenceSnapshot
from backend.app.application.structured_evidence import row_facts, claim_amount


_REFERENCE = re.compile(r"\[(E\d+)\]")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_PLAN_AMOUNT = re.compile(
    r"(?:^|[，,;；。:\s-])(?P<subject>[A-Za-z0-9\u4e00-\u9fff]{1,12})\s*方案\s*"
    r"(?:的)?\s*(?P<attribute>[\u4e00-\u9fff]{1,12})\s*(?P<number>\d+(?:\.\d+)?)"
)
_REFUSAL = ("资料不足", "无法回答", "没有足够证据")


class AnswerValidator:
    """Check cited exact facts before an answer becomes a successful run."""

    def validate(self, answer: str, snapshots: Sequence[EvidenceSnapshot], plan: EvidencePlan) -> str | None:
        by_label = {snapshot.label: snapshot for snapshot in snapshots}
        referenced = set(_REFERENCE.findall(answer))
        if not referenced and not any(phrase in answer for phrase in _REFUSAL):
            return "UNSUPPORTED_ANSWER"
        if referenced - by_label.keys():
            return "INVALID_CITATION"
        for clause in re.split(r"[。！？；\n]", answer):
            labels = _REFERENCE.findall(clause)
            for claim in _PLAN_AMOUNT.finditer(_REFERENCE.sub("", clause)):
                target = EvidenceTarget(claim.group("subject"), claim.group("attribute").rstrip("为是"))
                number = claim.group("number")
                if not any(
                    (source := by_label.get(label)) is not None
                    and (fact := QualityGate.supporting_fact(source.quote, target)) is not None
                    and number in _NUMBER.findall(fact)
                    for label in labels
                ):
                    return "UNSUPPORTED_ANSWER"
        if not plan.targets:
            return None

        clauses = re.split(r"[。！？；\n]", answer)
        for target in plan.targets:
            table_sources={s.label:row_facts(s.quote,s.locator,target,s.quote_sha256) for s in snapshots}
            table_sources={label:facts for label,facts in table_sources.items() if facts}
            if table_sources:
                signatures={(f.value,f.unit) for facts in table_sources.values() for f in facts}
                if len(signatures)!=1:return "UNSUPPORTED_ANSWER"
                claims=[clause for clause in clauses if target.attribute in clause and re.search(r"\d",clause)]
                if not claims:return "UNSUPPORTED_ANSWER"
                for claim in claims:
                    amount=claim_amount(_REFERENCE.sub("",claim),target)
                    labels=_REFERENCE.findall(claim)
                    if amount is None or not any(amount in {(f.value,f.unit) for f in table_sources.get(label,())} for label in labels):
                        return "UNSUPPORTED_ANSWER"
                continue
            claims = [clause for clause in clauses if QualityGate.supporting_fact(clause, target)]
            if not claims:
                return "UNSUPPORTED_ANSWER"
            supported = False
            for claim in claims:
                labels = _REFERENCE.findall(claim)
                claim_text = _REFERENCE.sub("", claim)
                numbers = set(_NUMBER.findall(claim_text))
                if not numbers:
                    continue
                for label in labels:
                    source = by_label.get(label)
                    if source is None:
                        continue
                    if source.locator.get("kind")=="table" or target.period is not None and "\t" in source.quote:
                        continue
                    source_fact = QualityGate.supporting_fact(source.quote, target)
                    if source_fact and numbers <= set(_NUMBER.findall(source_fact)):
                        supported = True
                        break
                if supported:
                    break
            if not supported:
                return "UNSUPPORTED_ANSWER"
        return None
