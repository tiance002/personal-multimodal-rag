from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict


_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_TOKEN = re.compile(r"[\w]+|>=|<=|==|!=|[><=]", re.UNICODE)


class NormalizedQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    q0: str
    normalized: str
    terms: tuple[str, ...]


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().lower()


def _cjk_terms(token: str) -> Iterator[str]:
    """Yield a CJK token and its overlapping bigrams.

    A two-character run is its own only bigram, so it is yielded once instead of
    twice; otherwise every two-character term would be double counted and any
    term-frequency ranking would be skewed.
    """
    yield token
    if len(token) > 2:
        for index in range(len(token) - 1):
            yield token[index:index + 2]


def _terms(text: str) -> Iterator[str]:
    for match in _TOKEN.finditer(_normalize(text)):
        token = match.group(0)
        if _CJK_RUN.fullmatch(token):
            yield from _cjk_terms(token)
        else:
            yield token


def _add_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def normalize_query(text: str) -> NormalizedQuery:
    terms: list[str] = []
    for term in _terms(text):
        _add_unique(terms, term)
    return NormalizedQuery(q0=text, normalized=_normalize(text), terms=tuple(terms))


def term_frequencies(text: str) -> dict[str, int]:
    """Occurrence counts per token, including overlapping CJK bigrams.

    `normalize_query` deliberately de-duplicates terms because a query only needs
    to know *whether* a term is present. Persisted term statistics need real
    counts instead, otherwise every stored frequency would be 1 and any
    term-frequency ranking would degenerate into a presence flag.
    """
    counts: Counter[str] = Counter(term for term in _terms(text) if term)
    return dict(counts)
