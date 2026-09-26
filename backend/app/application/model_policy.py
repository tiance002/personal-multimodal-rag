from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.domain.text_normalization import NormalizedQuery, normalize_query


@dataclass(frozen=True)
class QueryGatewayResult:
    expansions: tuple[str, ...] = ()


class ModelPolicy:
    def build_query_plan(
        self,
        question: str,
        gateway: Any | None,
        *,
        enabled: bool,
        timeout_seconds: float,
    ) -> tuple[NormalizedQuery, str | None]:
        baseline = normalize_query(question)
        if not enabled or gateway is None:
            return baseline, None
        try:
            result = gateway.query_expand(question, timeout_seconds)
            if isinstance(result, dict):
                expansions = result.get("expansions", [])
            else:
                expansions = getattr(result, "expansions", [])
            if not isinstance(expansions, (list, tuple)) or not all(isinstance(item, str) for item in expansions):
                raise ValueError("invalid local query schema")
            expanded_terms = list(baseline.terms)
            for expansion in expansions:
                expanded_terms.extend(normalize_query(expansion).terms)
            return baseline.model_copy(update={"terms": tuple(dict.fromkeys(expanded_terms))}), None
        except Exception:
            return baseline, "LOCAL_QUERY_FALLBACK"
