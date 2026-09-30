"""Deterministic retrieval-mode selection; never calls an LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalRoute:
    mode: str
    reason: tuple[str, ...]


class RetrievalRouter:
    MODES = frozenset({"adaptive", "vector", "keyword", "hybrid"})
    _quoted = re.compile(r'["“「『][^"”」』]+["”」』]')
    _identifier = re.compile(r"\b[A-Za-z]\w*_[A-Za-z0-9_]\w*\b|\b[A-Z]?[a-z]+(?:[A-Z][a-z0-9]*)+\b")
    _filename = re.compile(r"\b[\w-]+\.(?:md|py|ts|tsx|json|yaml|yml|pdf|docx|txt|csv)\b", re.I)
    _code = re.compile(r"\b[A-Z]{2,}(?:[-_.][A-Za-z0-9]+)+\b")
    _acronym = re.compile(r"\b[A-Z]{2,}\b")
    _lookup = re.compile(r"查找|查询|搜索|定位|在哪|定义|编号|版本|型号|多少|参数|API|标题")

    def __init__(self, mode: str = "adaptive") -> None:
        if mode not in self.MODES:
            raise ValueError("unsupported retrieval mode")
        self.mode = mode

    def route(self, question: str) -> RetrievalRoute:
        if self.mode != "adaptive":
            return RetrievalRoute(self.mode, ("EXPLICIT_RETRIEVAL_MODE",))
        checks = ((self._quoted, "EXACT_PHRASE"), (self._identifier, "IDENTIFIER"),
                  (self._filename, "FILENAME"), (self._code, "CODE_TOKEN"))
        reasons = [reason for pattern, reason in checks if pattern.search(question)]
        if self._lookup.search(question):
            if self._acronym.search(question):
                reasons.append("ACRONYM_LOOKUP")
            if re.search(r"\d", question):
                reasons.append("NUMERIC_LOOKUP")
        if reasons:
            return RetrievalRoute("hybrid", tuple(reasons))
        return RetrievalRoute("vector", ("SEMANTIC_VECTOR_DEFAULT",))
