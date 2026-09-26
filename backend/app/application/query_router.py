from __future__ import annotations

import re
from dataclasses import dataclass


_PARALLEL = re.compile(
    r"^\s*(?P<left>[\w-]{1,30}?)\s*(?:与|和|、|及)\s*"
    r"(?P<right>[\w-]{1,30}?)\s*(?:两种方案|两个方案|两者)?\s*"
    r"各自(?:的)?(?P<attribute>[^，。？！?]{1,30})",
    re.UNICODE,
)
_COMPARISON = re.compile(
    r"^\s*(?P<left>[\w-]{1,30}?)\s*(?:与|和|、|及)\s*"
    r"(?P<right>[\w-]{1,30}?)\s*(?:两种方案|两个方案|两者)?\s*的?"
    r"(?P<attribute>[\w-]{1,30}?)差(?:额|异|距)",
    re.UNICODE,
)
_SINGLE = re.compile(
    r"^\s*(?P<subject>[\w-]{1,30}?)\s*的?\s*(?P<attribute>[\w-]{1,30}?)"
    r"(?:是多少|为多少|多少|是什么)[？?]?\s*$",
    re.UNICODE,
)
_RELATION = re.compile(
    r"^\s*(?P<left>[\w-]{1,30}?)\s*(?:与|和|、|及)\s*"
    r"(?P<right>[\w-]{1,30}?)\s*(?:有|之间有)?什么关系",
    re.UNICODE,
)
_NUMERIC_ATTRIBUTE = re.compile(r"成本|费用|价格|金额|预算|数量|时长|时间|日期|收入|支出|费率")


@dataclass(frozen=True)
class EvidenceTarget:
    subject: str
    attribute: str

    @property
    def search_query(self) -> str:
        return f"{self.subject} {self.attribute}"


@dataclass(frozen=True)
class EvidencePlan:
    original_query: str
    targets: tuple[EvidenceTarget, ...] = ()
    kind: str = "opaque"
    relation_subjects: tuple[str, str] | None = None


class QueryRouter:
    """Only split an unambiguous parallel fact request; preserve q0 otherwise."""

    def plan(self, question: str) -> EvidencePlan:
        if "关系" in question:
            relation = _RELATION.search(question)
            if relation is not None:
                return EvidencePlan(
                    question, kind="relation",
                    relation_subjects=(relation.group("left").strip(), relation.group("right").strip()),
                )
            return EvidencePlan(question)
        comparison = _COMPARISON.search(question)
        if comparison is not None:
            left, right = comparison.group("left").strip(), comparison.group("right").strip()
            attribute = comparison.group("attribute").strip()
            if left and right and left != right and attribute and _NUMERIC_ATTRIBUTE.search(attribute):
                return EvidencePlan(
                    question,
                    (EvidenceTarget(left, attribute), EvidenceTarget(right, attribute)),
                    "comparison",
                )
        match = _PARALLEL.search(question)
        if match is None:
            single = _SINGLE.search(question)
            if single is not None and ("多少" in question or _NUMERIC_ATTRIBUTE.search(single.group("attribute"))):
                return EvidencePlan(
                    question,
                    (EvidenceTarget(single.group("subject").strip(), single.group("attribute").strip()),),
                    "single",
                )
            return EvidencePlan(question)
        attribute = re.sub(r"^(?:准确|具体|实际|分别)", "", match.group("attribute").strip())
        attribute = re.sub(r"(?:是多少|为多少|是什么|多少|？|\?)$", "", attribute).strip()
        left, right = match.group("left").strip(), match.group("right").strip()
        if not attribute or not left or not right or left == right or not ("多少" in question or _NUMERIC_ATTRIBUTE.search(attribute)):
            return EvidencePlan(question)
        return EvidencePlan(question, (EvidenceTarget(left, attribute), EvidenceTarget(right, attribute)), "parallel")
