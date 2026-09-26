from __future__ import annotations

from collections.abc import Sequence

from backend.app.application.citations import CitationService
from backend.app.application.retrieval import RetrievalItem


class ContextBuilder:
    def __init__(self, max_chars: int = 8000) -> None:
        self.max_chars = max_chars

    def build(self, run_id: str, items: Sequence[RetrievalItem], citations: CitationService) -> tuple[str, list[str]]:
        pieces: list[str] = []
        labels: list[str] = []
        for item in items:
            detail = citations.freeze(run_id, item.chunk)
            labels.append(detail.label)
            pieces.append(f"[{detail.label}] {detail.quote}")
        return "\n\n".join(pieces)[: self.max_chars], labels
