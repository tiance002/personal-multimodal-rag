from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, ConfigDict


_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_TOKEN = re.compile(r"[\w]+|>=|<=|==|!=|[><=]", re.UNICODE)


class NormalizedQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    q0: str
    normalized: str
    terms: tuple[str, ...]


def _add_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def normalize_query(text: str) -> NormalizedQuery:
    normalized = unicodedata.normalize("NFKC", text).strip().lower()
    terms: list[str] = []
    for match in _TOKEN.finditer(normalized):
        token = match.group(0)
        _add_unique(terms, token)
        if _CJK_RUN.fullmatch(token):
            for index in range(len(token) - 1):
                _add_unique(terms, token[index:index + 2])
    return NormalizedQuery(q0=text, normalized=normalized, terms=tuple(terms))
