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


_FACT_SEPARATORS = re.compile(r"[，,；;、]")
_SOURCE_FACT_SEPARATORS = re.compile(r"[。！？!?，,；;、\n]")
_ANSWER_FACT_SEPARATORS = re.compile(r"[。！？；\n]")
_NUMBER_ATOM = re.compile(
    r"[+\-\u2212\uff0b\uff0d]?\s*(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:[eE][+\-\u2212\uff0b\uff0d]?\d+)?"
)
_NUMBER_EDGE_HINTS = frozenset(".+-eE\u2212\uff0b\uff0d")

_LOCAL_IDENTIFIER = re.compile(
    r"(?:图|表)\s*\d+(?:[.-]\d+)*(?!\d|[.,，-][+-]?\d)"
    r"|(?:型号|模型编号|设备编号)\s*[A-Za-z][A-Za-z0-9_.-]*"
)
_IDENTIFIER_ADJACENT_UNIT = re.compile(r"(?:元|%|％|厘米|毫米|米|摄氏度|度|公斤|千克|秒|分钟|小时)")


def _has_numeric_fact(text: str) -> bool:
    """Ignore only explicit local IDs, never the remaining statement."""
    def identifier(match: re.Match[str]) -> str:
        # An adjacent measure makes this ambiguous; keep its digits guarded.
        if _IDENTIFIER_ADJACENT_UNIT.match(text[match.end():].lstrip()):
            return match.group()
        return ""
    return _NUMBER.search(_LOCAL_IDENTIFIER.sub(identifier, text)) is not None


def _numeric_safe_split(text: str, separators: re.Pattern[str]) -> list[str]:
    """Preserve ambiguous number/separator/number groups on both evidence sides.

    Do not normalize thousands, signed endpoints, or comma ranges. A separator
    between numeric neighbors (including spaces/signs) stays inside the whole
    statement, which must have literal original support or be rejected.
    """
    fragments = []
    start = 0
    atom_ends = {atom.end() for atom in _NUMBER_ATOM.finditer(text)}
    for divider in separators.finditer(text):
        if divider.group() in ",，;；、。！？!?\n":
            left = divider.start() - 1
            while left >= 0 and text[left].isspace():
                left -= 1
            right = divider.end()
            while right < len(text) and text[right].isspace():
                right += 1
            # Both edges use the same atom grammar, including .digits and
            # signed .digits. Numeric-looking incomplete edges stay whole too;
            # they cannot authorize cross-fact splitting or numeric conversion.
            left_number = left + 1 in atom_ends
            right_number = right < len(text) and _NUMBER_ATOM.match(text, right) is not None
            left_ambiguous = left >= 0 and (text[left] in _NUMBER_EDGE_HINTS or not text[left].isalnum())
            right_ambiguous = right < len(text) and (text[right] in _NUMBER_EDGE_HINTS or not text[right].isalnum())
            if (left_number or left_ambiguous) and (right_number or right_ambiguous):
                continue
        fragments.append(text[start:divider.start()])
        start = divider.end()
    fragments.append(text[start:])
    return fragments


class AnswerValidator:
    """Check cited exact facts before an answer becomes a successful run."""

    def validate(self, answer: str, snapshots: Sequence[EvidenceSnapshot], plan: EvidencePlan) -> str | None:
        by_label = {snapshot.label: snapshot for snapshot in snapshots}
        referenced = set(_REFERENCE.findall(answer))
        if not referenced and not any(phrase in answer for phrase in _REFUSAL):
            return "UNSUPPORTED_ANSWER"
        if referenced - by_label.keys():
            return "INVALID_CITATION"
        caption_cited = any(QualityGate.is_model_caption(by_label[label].locator) for label in referenced)
        # Conservative numeric atom boundaries protect caption-derived claims.
        # Pure native citations retain the original sentence scope, including
        # a numbered sentence immediately after a closing citation bracket.
        clauses = (_numeric_safe_split(answer, _ANSWER_FACT_SEPARATORS) if caption_cited
                   else _ANSWER_FACT_SEPARATORS.split(answer))
        for clause in clauses:
            labels = _REFERENCE.findall(clause)
            # Numeric occurrence is not same-fact support. Check each explicitly
            # separated claim against its citations; opaque claims need a full
            # literal original statement, not pooled numbers from another fact.
            if _has_numeric_fact(_REFERENCE.sub("", clause)) and caption_cited:
                if not self._independent_numeric_claims(clause, by_label, plan.targets):
                    return "UNSUPPORTED_ANSWER"
            for claim in _PLAN_AMOUNT.finditer(_REFERENCE.sub("", clause)):
                target = EvidenceTarget(claim.group("subject"), claim.group("attribute").rstrip("为是"))
                number = claim.group("number")
                if not any(
                    (source := by_label.get(label)) is not None
                    and not QualityGate.is_model_caption(source.locator)
                    and (fact := QualityGate.supporting_fact(source.quote, target)) is not None
                    and number in _NUMBER.findall(fact)
                    for label in labels
                ):
                    return "UNSUPPORTED_ANSWER"
        if not plan.targets:
            return None

        for target in plan.targets:
            table_sources={s.label:row_facts(s.quote,s.locator,target,s.quote_sha256,version_id=s.version_id)
                           for s in snapshots if not QualityGate.is_model_caption(s.locator)}
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
                    if source is None or QualityGate.is_model_caption(source.locator):
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

    @classmethod
    def _independent_numeric_claims(cls, clause: str, sources: dict[str, EvidenceSnapshot],
                                    targets: Sequence[EvidenceTarget]) -> bool:
        fragments = _numeric_safe_split(clause, _FACT_SEPARATORS)
        local_labels = [_REFERENCE.findall(fragment) for fragment in fragments]
        # A sole trailing citation group applies to the whole explicit list.
        # Once earlier facts have their own labels, none may borrow later ones.
        shared_labels = local_labels[-1] if not any(local_labels[:-1]) else None
        for fragment, labels in zip(fragments, local_labels):
            claim = _REFERENCE.sub("", fragment).strip().rstrip("。!?！？")
            if not _has_numeric_fact(claim):
                continue  # Keep caption's qualitative use.
            labels = shared_labels if shared_labels is not None else labels
            if not any(cls._original_supports_claim(claim, sources[label], targets) for label in labels):
                return False
        return True

    @staticmethod
    def _original_supports_claim(claim: str, source: EvidenceSnapshot,
                                 targets: Sequence[EvidenceTarget]) -> bool:
        if (QualityGate.is_model_caption(source.locator)
                or source.locator.get("parse_status") != "complete"):
            return False
        if source.locator.get("kind") == "table" or "\t" in source.quote:
            # Keep native row/unit validation; never treat table rendering as
            # prose. Opaque plans cannot guess a semantic target from a table.
            for target in targets:
                amount = claim_amount(claim, target)
                if amount is not None and any((fact.value, fact.unit) == amount
                    for fact in row_facts(source.quote, source.locator, target,
                        source.quote_sha256, version_id=source.version_id)):
                    return True
            return False
        # This deliberately narrow opaque support is exact whole-statement
        # agreement. No substring match, unit conversion, paraphrase, or number
        # pooling can establish a different subject/attribute/month/unit fact.
        return any(claim == statement.strip().rstrip("。!?！？")
                   for statement in _numeric_safe_split(source.quote, _SOURCE_FACT_SEPARATORS))
